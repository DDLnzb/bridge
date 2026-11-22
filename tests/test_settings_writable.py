"""The data directory must be writable - otherwise the web UI cannot save.

The bridge keeps its configuration in `DDLNZB_DATA_DIR` (in the image
`/app/data`). On Unraid that path is a **bind mount** onto the host directory
`/mnt/user/appdata/DDLnzb` - and that host directory does not belong to the
user the container process runs as (uid 99 / gid 100). The write of the
settings file then fails with `PermissionError`.

The failure used to surface as an unhandled 500 with a traceback. Covered
here:

- `settings.save()` reports the failure as `SettingsNotWritableError`
- `settings.storage_writable()` probes the directory - really, not with
  `os.access` (that one answers "writable" for the root user)
- the setup route answers with a readable message instead of a traceback
- the startup warns about a data directory that cannot be written
"""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import settings as settings_store
from app import webui
from app.config import ENV_SOURCES, config
from app.main import app

KEY = "test-bridge-key"
TOKEN = "test-setup-token"


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    """Settings file in the tmp directory - the real file stays untouched."""
    path = tmp_path / "settings.json"
    monkeypatch.setenv("DDLNZB_SETTINGS_FILE", str(path))
    return path


@pytest.fixture(autouse=True)
def restore_config():
    """Resets the global config after the test."""
    snapshot = {name: getattr(config, name) for name in ENV_SOURCES}
    yield
    for name, value in snapshot.items():
        setattr(config, name, value)


@pytest.fixture
def client_ohne_key(monkeypatch):
    """Test client without a configured key - the initial setup is active."""
    monkeypatch.delenv("DDLNZB_API_KEY", raising=False)
    config.reload()
    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    """Test client with a configured key."""
    monkeypatch.setenv("DDLNZB_API_KEY", KEY)
    config.reload()
    return TestClient(app)


def _write_text_boom(self, *args, **kwargs):
    """Replaces `Path.write_text` with a permission error."""
    raise PermissionError(13, "Permission denied")


# ----------------------------------------------------------- settings.py


def test_save_meldet_fehlende_schreibrechte(monkeypatch):
    monkeypatch.setattr(Path, "write_text", _write_text_boom)

    with pytest.raises(settings_store.SettingsNotWritableError) as excinfo:
        settings_store.save({"DDLNZB_API_KEY": KEY})

    # The message must name the path and the identity that lacks the rights.
    assert "settings.json" in str(excinfo.value)
    assert "uid" in str(excinfo.value)


def test_save_meldet_fehlende_schreibrechte_als_oserror(monkeypatch):
    """The error stays an `OSError` - the existing handlers keep working."""
    monkeypatch.setattr(Path, "write_text", _write_text_boom)

    with pytest.raises(OSError):
        settings_store.save({"DDLNZB_API_KEY": KEY})


def test_storage_writable_prueft_das_verzeichnis(settings_file):
    assert settings_store.storage_writable() is True


def test_storage_writable_meldet_fehlende_schreibrechte(tmp_path, monkeypatch):
    """A directory that cannot be written is detected."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("DDLNZB_SETTINGS_FILE", str(blocker / "settings.json"))

    assert settings_store.storage_writable() is False


def test_storage_writable_hinterlaesst_keine_probe_datei(settings_file):
    """The probe removes its own file - the data directory stays clean.

    The data directory is the mount on the host; a file that stays there is
    a leftover of the check, not of the bridge.
    """
    assert settings_store.storage_writable() is True

    reste = [p.name for p in settings_file.parent.iterdir() if p.name.startswith(".ddlnzb")]
    assert reste == [], f"Die Schreibprobe hat Dateien hinterlassen: {reste}"


def test_storage_writable_benoennt_die_probe_eindeutig(settings_file, monkeypatch):
    """Two calls use two different probe files.

    The dashboard renders the state on every request; with a fixed probe name
    the first call would delete the file the second one is about to write.
    """
    namen: list[str] = []
    original = Path.write_text

    def _write_text(self, *args, **kwargs):
        namen.append(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _write_text)

    settings_store.storage_writable()
    settings_store.storage_writable()

    assert len(namen) == 2, f"Die Schreibprobe wurde {len(namen)}-mal aufgerufen"
    assert namen[0] != namen[1], f"Beide Proben nutzen denselben Namen: {namen[0]}"


def test_not_writable_hint_nennt_der_weg_und_die_identitaet(settings_file):
    hint = settings_store.not_writable_hint()

    assert str(settings_file.parent) in hint
    assert "uid" in hint
    assert "chown" in hint


# --------------------------------------------------------------- webui.py


def test_setup_antwortet_mit_lesbarer_meldung_statt_traceback(client_ohne_key, monkeypatch):
    monkeypatch.setattr(webui, "_SETUP_TOKEN", TOKEN)
    monkeypatch.setattr(Path, "write_text", _write_text_boom)

    antwort = client_ohne_key.post(
        "/ui/setup",
        data={"setup_token": TOKEN, "api_key": KEY},
        follow_redirects=False,
    )

    assert antwort.status_code == 500
    assert "Traceback" not in antwort.text
    assert "not writable" in antwort.text
    # The setup token survives - the operator can retry after the fix.
    assert webui._SETUP_TOKEN == TOKEN


def test_setup_meldet_die_schreibrechte_im_log(client_ohne_key, monkeypatch, caplog):
    monkeypatch.setattr(webui, "_SETUP_TOKEN", TOKEN)
    monkeypatch.setattr(Path, "write_text", _write_text_boom)

    with caplog.at_level(logging.ERROR, logger="ddlnzb.bridge.webui"):
        client_ohne_key.post(
            "/ui/setup",
            data={"setup_token": TOKEN, "api_key": KEY},
            follow_redirects=False,
        )

    assert any("not writable" in record.message for record in caplog.records)


def test_speichern_meldet_die_schreibrechte_im_dashboard(client, monkeypatch):
    """The dashboard shows that the settings file cannot be written."""
    client.post("/ui/login", data={"key": KEY}, follow_redirects=False)

    monkeypatch.setattr(Path, "write_text", _write_text_boom)

    antwort = client.get("/ui")

    assert antwort.status_code == 200
    assert "not writable" in antwort.text
