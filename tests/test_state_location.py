"""The state file lands in the mounted volume.

The bridge wrote its state to `/app/ddlnzb_bridge_state.json` - outside
the mounted volume (`bridge_data:/app/data`). A rollout
(`docker compose up -d --force-recreate`, the way Coolify restarts a
service) therefore took queue and history with it: the file lived in the
container's writable layer and was gone with the container (#1049).

The contract: **the state file path lies under `DDLNZB_DATA_DIR`.** The
path is absolute - a relative one would resolve against the working
directory of the process again, which is the container root.

The compose contract is checked separately in
`tests/test_deployment_contract.py` - it needs the compose file of the
production stack, which does not exist in the public repository.
"""

from pathlib import Path

from app.config import config


def test_der_default_der_state_datei_liegt_im_datenverzeichnis(monkeypatch):
    """Without configuration the state file sits next to the queue."""
    from app.config import BridgeConfig

    # The default - the shared fixture sets a state file of its own.
    monkeypatch.delenv("DDLNZB_STATE_FILE", raising=False)

    frisch = BridgeConfig()

    assert Path(frisch.STATE_FILE).is_absolute(), (
        f"The default state file path {frisch.STATE_FILE!r} is relative - it "
        "would resolve against the working directory of the process, which is "
        "the container root and not the mounted volume (#1049)."
    )
    assert Path(frisch.STATE_FILE).parent == Path(frisch.DATA_DIR)


def test_die_state_datei_liegt_unter_dem_datenverzeichnis(tmp_path, monkeypatch):
    """The configured path is resolved into the data directory."""
    monkeypatch.setenv("DDLNZB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DDLNZB_STATE_FILE", "")

    from app.config import BridgeConfig

    frisch = BridgeConfig()

    assert Path(frisch.STATE_FILE).is_absolute()
    assert Path(frisch.STATE_FILE).parent == tmp_path, (
        f"The state file {frisch.STATE_FILE!r} does not lie under the data "
        "directory - a rollout would take queue and history with it (#1049)."
    )


def test_ein_relativer_pfad_landet_im_datenverzeichnis(tmp_path, monkeypatch):
    """A relative path is resolved against the data directory.

    Not against the working directory of the process: that is the container
    root, whose writable layer is thrown away with every rollout.
    """
    monkeypatch.setenv("DDLNZB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DDLNZB_STATE_FILE", "state.json")

    from app.config import BridgeConfig

    frisch = BridgeConfig()

    assert Path(frisch.STATE_FILE) == tmp_path / "state.json"


def test_ein_absoluter_pfad_bleibt_absolut(tmp_path, monkeypatch):
    """An absolute path is taken as it is - that is the escape hatch."""
    ziel = tmp_path / "anderswo" / "state.json"
    monkeypatch.setenv("DDLNZB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DDLNZB_STATE_FILE", str(ziel))

    from app.config import BridgeConfig

    frisch = BridgeConfig()

    assert Path(frisch.STATE_FILE) == ziel


def test_die_queue_datei_liegt_unter_dem_datenverzeichnis():
    """The second persistence belongs there as well.

    The property that matters: the queue file lands in the **data
    directory** and nowhere else — not in the working directory of the
    process, whose writable layer is thrown away with every rollout (#1049).

    The default path is checked, not the instance of the shared fixture —
    the fixture redirects it into the tmp directory.
    """
    from app.persistence import DEFAULT_QUEUE_FILE

    pfad = Path(DEFAULT_QUEUE_FILE)

    assert pfad.is_absolute(), (
        f"The queue file path {pfad} is relative - it would resolve against "
        "the working directory of the process, which is the container root."
    )
    assert pfad.parent == Path(config.DATA_DIR), (
        f"The queue file is written to {pfad} - it does not belong in the data directory (#1049)."
    )
    assert pfad.parent != Path.cwd(), (
        "The queue file lands in the working directory of the process - a "
        "rollout would take it with it (#1049)."
    )


def test_der_pfad_ist_keine_datei_im_arbeitsverzeichnis():
    """The state file must not be written into the working directory."""
    assert Path(config.STATE_FILE).is_absolute(), (
        f"The state file path {config.STATE_FILE!r} is relative - it lands in "
        "the working directory of the process and not in the mounted volume "
        "(#1049)."
    )
    assert Path(config.STATE_FILE).parent == Path(config.DATA_DIR)
