"""Contract between bridge configuration, compose service and README.

Two findings that both behave silently (#555, #671):

- The bridge service in `docker-compose.prod.yml` started with `API_URL`
  instead of `DDLNZB_API_URL` and did not receive its credentials. The process
  started, but rejected every SABnzbd call ("Bridge API key not configured")
  and never reached the API.
- `bridge/README.md` documented the config contract of the `src/` stub
  (`JD_EMAIL`, `DDLNZB_BRIDGE_TOKEN`). Anyone who followed the quick start
  started a bridge without access.

The contract is therefore pinned at the source: **what `app/config.py` reads
must be present in `bridge/.env`, set as a variable in the compose service and
named in the README.** Everything else is decoration that will eventually rot
against the code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = REPO_ROOT / "bridge"
COMPOSE_FILE = REPO_ROOT / "docker-compose.prod.yml"
CONFIG_FILE = BRIDGE_DIR / "app" / "config.py"
README_FILE = BRIDGE_DIR / "README.md"
ENV_FILE = BRIDGE_DIR / ".env"

#: This test checks the deployment contract: the compose file of the
#: production stack and the versioned `.env`. Neither of them exists here on
#: purpose — this repository holds the code in the repo root, there is no
#: compose file and no versioned credentials. The checks therefore only run
#: where their subject is.
#:
#: The guard is attached to exactly the files the tests read. A check on the
#: directory alone misleads: this repository itself is called "bridge", so
#: `BRIDGE_DIR` points to the repo root and the directory check would stay
#: green incorrectly.
if not (COMPOSE_FILE.is_file() and ENV_FILE.is_file()):
    pytest.skip(
        "Deployment contract (compose file, versioned .env) - not present in this repository",
        allow_module_level=True,
    )

#: Variables of the `src/` stub. The bridge starts `app/` — no running process
#: reads these names. Anyone who finds them in the README follows the wrong
#: contract (#671).
SRC_VARIABLEN = ("DDLNZB_BRIDGE_TOKEN", "JD_EMAIL", "JD_PASSWORD", "JD_DEVICE")

#: Invented download paths. There is only `DOWNLOAD_DIR` plus the hard-wired
#: `CATEGORY_MAPPING` (`bridge/app/config.py`).
ERFUNDENE_PFAD_VARIABLEN = ("DOWNLOAD_PATH_MOVIES", "DOWNLOAD_PATH_TV", "DOWNLOAD_PATH_DOCS")

#: `os.getenv("NAME", "")` — an empty default makes the variable mandatory.
_GETENV_PFLICHT = re.compile(r'os\.getenv\(\s*"([A-Z0-9_]+)"\s*,\s*""\s*\)')
_GETENV_OHNE_DEFAULT = re.compile(r'os\.getenv\(\s*"([A-Z0-9_]+)"\s*\)')


def _service_block(text: str, name: str) -> str:
    """The block of a compose service as text.

    Same pattern as in `backend/tests/worker/test_vpn_config_mount.py`:
    a service starts with two spaces and the name and ends at the next
    service.
    """
    zeilen = text.splitlines()
    for index, zeile in enumerate(zeilen):
        if not re.match(rf"^  {re.escape(name)}:\s*$", zeile):
            continue

        block = [zeile]
        for folgezeile in zeilen[index + 1 :]:
            if re.match(r"^  [A-Za-z0-9_-]+:\s*$", folgezeile):
                break
            block.append(folgezeile)
        return "\n".join(block)

    raise AssertionError(f"No service {name!r} found in {COMPOSE_FILE.name}")


def _gelesene_variablen() -> set[str]:
    """All variables that `app/config.py` reads from the environment."""
    text = CONFIG_FILE.read_text()
    return set(_GETENV_PFLICHT.findall(text)) | set(_GETENV_OHNE_DEFAULT.findall(text))


def _pflicht_variablen() -> set[str]:
    """The read variables without a usable default — without them the bridge
    starts functionally empty."""
    return _gelesene_variablen()


def _env_werte() -> dict[str, str]:
    """The set variables from `bridge/.env`."""
    werte: dict[str, str] = {}
    for zeile in ENV_FILE.read_text().splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, _, wert = zeile.partition("=")
        werte[name.strip()] = wert.strip()
    return werte


def test_compose_setzt_die_api_url_mit_ddl_anzb_vorzeichen() -> None:
    """The code reads `DDLNZB_API_URL` — the compose must set it too.

    With `API_URL` the variable stays empty, the bridge falls back to
    `http://localhost:8000` and does not reach the API (#555).
    """
    block = _service_block(COMPOSE_FILE.read_text(), "bridge")

    assert "DDLNZB_API_URL=" in block, (
        "The bridge service does not set DDLNZB_API_URL.\n"
        f"Block:\n{block}\n"
        "Without the variable the bridge falls back to http://localhost:8000."
    )

    falsche = [
        zeile
        for zeile in block.splitlines()
        if re.match(r"^\s*-\s*API_URL=", zeile) or re.match(r"^\s*API_URL=", zeile)
    ]
    assert not falsche, (
        "The bridge service sets the API URL under the wrong name "
        f"`API_URL`: {falsche}.\n"
        "`app/config.py` reads exclusively `DDLNZB_API_URL`."
    )


def test_compose_zeigt_auf_den_api_service_im_compose_netz() -> None:
    """`localhost` points from inside the container at the container itself."""
    block = _service_block(COMPOSE_FILE.read_text(), "bridge")

    treffer = re.search(r"DDLNZB_API_URL=(\S+)", block)
    assert treffer, "The bridge service does not set DDLNZB_API_URL."

    wert = treffer.group(1)
    assert "localhost" not in wert and "127.0.0.1" not in wert, (
        f"DDLNZB_API_URL points at {wert!r}. From inside the container that is "
        "the container itself - the bridge does not reach the API that way.\n"
        "In the compose network the API service is called `api`."
    )


def test_compose_gibt_dem_bridge_seine_zugangsdaten() -> None:
    """Without credentials the bridge starts, but rejects every call."""
    block = _service_block(COMPOSE_FILE.read_text(), "bridge")

    liest_env_file = bool(re.search(r"env_file:", block))
    environment = re.search(r"environment:\n((?:\s{6}-.*\n)+)", block)
    gesetzte = (
        set(re.findall(r"^\s*-\s*([A-Z0-9_]+)=", environment.group(1), re.M))
        if environment
        else set()
    )

    pflicht = {"DDLNZB_API_KEY", "MYJD_EMAIL", "MYJD_PASSWORD", "MYJD_DEVICE_NAME"}
    aus_environment = pflicht <= gesetzte

    assert aus_environment or liest_env_file, (
        "The bridge service does not receive its credentials.\n"
        f"Block:\n{block}\n"
        "What is expected is either an `env_file:` (bridge/.env maintains exactly "
        "the variables from app/config.py) or a complete `environment:` "
        "set with DDLNZB_API_KEY, MYJD_EMAIL, MYJD_PASSWORD and "
        "MYJD_DEVICE_NAME."
    )


def test_der_api_key_stammt_aus_der_env_file_und_nicht_aus_environment() -> None:
    """`environment` wins over `env_file` - an empty entry empties the key.

    The bridge service set `DDLNZB_API_KEY=${DDLNZB_API_KEY:-}` next to
    `env_file: ./bridge/.env`. Compose resolves that entry to an empty string
    and thereby overwrites the key from the file as soon as the variable is
    not set in the `.env` of the host (#716). The bridge then starts without a
    key and rejects every SABnzbd call.
    """
    block = _service_block(COMPOSE_FILE.read_text(), "bridge")

    environment = re.search(r"environment:\n((?:\s{6}-.*\n)+)", block)
    gesetzte = (
        set(re.findall(r"^\s*-\s*([A-Z0-9_]+)=", environment.group(1), re.M))
        if environment
        else set()
    )

    assert "DDLNZB_API_KEY" not in gesetzte, (
        "The bridge service sets DDLNZB_API_KEY in the `environment` section.\n"
        f"Block:\n{block}\n"
        "In Compose `environment` wins over `env_file`: an empty entry "
        "overwrites the key from bridge/.env with an empty string (#716)."
    )

    assert "./bridge/.env" in block, (
        "The bridge service does not read bridge/.env any more. Without the "
        "file the credentials are missing (#555)."
    )

    assert _env_werte().get("DDLNZB_API_KEY"), (
        "bridge/.env has no DDLNZB_API_KEY. With the `environment` entry gone "
        "the file is the only source of the key."
    )


def test_bridge_env_datei_setzt_jede_pflichtvariable() -> None:
    """`bridge/.env` is the source for the local start — it must be
    complete."""
    assert ENV_FILE.exists(), f"{ENV_FILE} is missing. The file is versioned."

    werte = _env_werte()
    fehlend = sorted(name for name in _pflicht_variablen() if not werte.get(name))

    assert not fehlend, (
        f"bridge/.env has no non-empty values for: {fehlend}.\n"
        "app/config.py reads these variables without a usable default - without "
        "them the bridge starts functionally empty (API key missing, JDownloader "
        "does not connect)."
    )


def test_readme_nennt_jede_pflichtvariable() -> None:
    """The README must name the contract that the code reads."""
    readme = README_FILE.read_text()

    fehlend = sorted(name for name in _pflicht_variablen() if name not in readme)

    assert not fehlend, (
        f"bridge/README.md does not name these variables read by app/config.py: "
        f"{fehlend}.\n"
        "Anyone who follows the quick start otherwise starts a bridge without access."
    )


def _enthaelt_variable(text: str, name: str) -> bool:
    """Whether `text` names the variable as a standalone token.

    The word boundary matters: `MYJD_EMAIL` contains `JD_EMAIL` as a
    substring. Without the boundary a correct README would be reported as
    wrong.
    """
    return re.search(rf"(?<![A-Z0-9_]){re.escape(name)}(?![A-Z0-9_])", text) is not None


def test_readme_nennt_keine_src_variablen() -> None:
    """`src/` is not started — its config contract is dead (#671)."""
    readme = README_FILE.read_text()

    treffer = [name for name in SRC_VARIABLEN if _enthaelt_variable(readme, name)]
    assert not treffer, (
        f"bridge/README.md names the src/ variables {treffer}.\n"
        "The started path is app/ and reads MYJD_* and DDLNZB_API_KEY."
    )

    erfunden = [name for name in ERFUNDENE_PFAD_VARIABLEN if _enthaelt_variable(readme, name)]
    assert not erfunden, (
        f"bridge/README.md names the non-existent path variables {erfunden}.\n"
        "There is only DOWNLOAD_DIR and the hard-wired CATEGORY_MAPPING."
    )


def test_readme_nennt_den_sabnzbd_port_6789() -> None:
    """The container listens on 6789 by hard wiring (Dockerfile CMD)."""
    readme = README_FILE.read_text()

    assert "6789" in readme, (
        "bridge/README.md does not name the SABnzbd port 6789.\n"
        "The image starts uvicorn via CMD with --host 0.0.0.0 --port 6789 - "
        "BRIDGE_PORT only applies to local runs."
    )
