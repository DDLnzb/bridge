"""The web UI reconnect must update the heartbeat state (#681).

Two paths reconnect JDownloader, and only one of them cared for the
heartbeat: `POST /heartbeat/reconnect` goes through `force_reconnect()`,
`POST /ui/reconnect` did not touch the state at all. A `last_error` from an
earlier attempt therefore stayed in `/status`, `healthy` stayed `False` - and
the heartbeat only cleans up in `_ping()`, which runs **only with active
downloads**.

What is checked is behaviour: the state after the request, not the source
text.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import ENV_SOURCES, config
from app.heartbeat import jd_heartbeat
from app.jdownloader import jdownloader
from app.main import app

KEY = "test-bridge-key"

ALTER_FEHLER = "MyJD login failed: TYPE: AUTH_FAILED"
NEUER_FEHLER = "MyJD login failed: TYPE: AUTH_FAILED (neuer Versuch)"


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


@pytest.fixture(autouse=True)
def jdownloader_state(monkeypatch):
    """Keeps the global JDownloader client clean after the test.

    `monkeypatch` restores the values it recorded - also when the test
    overwrites them afterwards.
    """
    monkeypatch.setattr(jdownloader, "device_id", None)
    monkeypatch.setattr(jdownloader, "last_error", None)
    return jdownloader


@pytest.fixture(autouse=True)
def heartbeat_state():
    """Puts the heartbeat into the state of an earlier failed attempt."""
    jd_heartbeat._consecutive_failures = 1
    jd_heartbeat._last_error = ALTER_FEHLER
    jd_heartbeat._last_success = None
    yield jd_heartbeat
    jd_heartbeat._consecutive_failures = 0
    jd_heartbeat._last_error = None
    jd_heartbeat._last_success = None


@pytest.fixture
def client(monkeypatch):
    """Test client with a configured key and a logged-in session."""
    monkeypatch.setenv("DDLNZB_API_KEY", KEY)
    config.reload()
    client = TestClient(app)
    antwort = client.post("/ui/login", data={"key": KEY}, follow_redirects=False)
    assert antwort.status_code == 303
    return client


def _heartbeat_status(client) -> dict:
    """The heartbeat block of `GET /status`."""
    antwort = client.get("/status", params={"apikey": KEY})
    assert antwort.status_code == 200
    return antwort.json()["heartbeat"]


def test_erfolgreicher_reconnect_raeumt_den_heartbeat(client, monkeypatch):
    """After a successful reconnect the stale error is gone."""
    monkeypatch.setattr(jdownloader, "device_id", "device-123")

    async def _reconfigure() -> bool:
        jdownloader.last_error = None
        return True

    monkeypatch.setattr(jdownloader, "reconfigure", _reconfigure)

    antwort = client.post("/ui/reconnect")

    assert antwort.status_code == 200
    assert antwort.json()["ok"] is True

    status = _heartbeat_status(client)
    assert status["last_error"] is None
    assert status["consecutive_failures"] == 0
    assert status["healthy"] is True


def test_fehlgeschlagener_reconnect_zeigt_den_aktuellen_fehler(client, monkeypatch):
    """A failed reconnect names the current reason - not the older one."""
    monkeypatch.setattr(jdownloader, "device_id", "device-123")

    async def _reconfigure() -> bool:
        jdownloader.last_error = NEUER_FEHLER
        return False

    monkeypatch.setattr(jdownloader, "reconfigure", _reconfigure)

    antwort = client.post("/ui/reconnect")

    assert antwort.status_code == 200
    assert antwort.json()["ok"] is False
    assert antwort.json()["detail"] == NEUER_FEHLER

    status = _heartbeat_status(client)
    assert status["last_error"] == NEUER_FEHLER


def test_der_fehler_geht_in_die_antwort_der_oberflaeche(client, monkeypatch):
    """The message distinguishes "wrong password" from "device not found"."""
    monkeypatch.setattr(jdownloader, "device_id", "device-123")

    async def _reconfigure() -> bool:
        jdownloader.last_error = "device 'UnraidDocker' not found"
        return False

    monkeypatch.setattr(jdownloader, "reconfigure", _reconfigure)

    detail = client.post("/ui/reconnect").json()["detail"]

    assert "not found" in detail


def test_ein_gescheiterter_reconnect_zaehlt_nicht_fuer_die_pings(client, monkeypatch):
    """The counter belongs to the pings - the automatic reconnect keeps working."""
    monkeypatch.setattr(jdownloader, "device_id", "device-123")

    async def _reconfigure() -> bool:
        jdownloader.last_error = NEUER_FEHLER
        return False

    monkeypatch.setattr(jdownloader, "reconfigure", _reconfigure)

    client.post("/ui/reconnect")

    assert _heartbeat_status(client)["consecutive_failures"] == 1
