"""`jdownloader.reconfigure()` must name the current reason for a failure (#681).

The web UI shows the text of `jdownloader.last_error` instead of a generic
hint. For that to work the error must be **current** - and it must be set in
*every* failure branch of `reconfigure()`. A branch that returns `False`
without touching `last_error` leaves the older error of a previous attempt in
the status, and the user reads a reason that no longer applies.

What is checked is behaviour: `last_error` after the call.
"""

import pytest

from app.config import config
from app.jdownloader import MyJDownloaderAPI


@pytest.fixture
def jd(monkeypatch):
    """A JDownloader client with a stubbed `myjdapi` underneath."""
    client = MyJDownloaderAPI(email="a@b.invalid", password="geheim", device_name="UnraidDocker")
    monkeypatch.setattr(client, "_client", _StubClient())
    return client


class _StubClient:
    """The `myjdapi` client - the parts `reconfigure()` touches."""

    def __init__(self):
        self.login_fehler: Exception | None = None
        self.gerät_fehler: Exception | None = None
        self.gerät = None

    def set_app_key(self, key):  # pragma: no cover - not relevant
        return None

    def disconnect(self):  # pragma: no cover - not relevant
        return None

    def connect(self, email, password):
        if self.login_fehler:
            raise self.login_fehler
        return None

    def update_devices(self):  # pragma: no cover - not relevant
        return None

    def get_device(self, name):
        if self.gerät_fehler:
            raise self.gerät_fehler
        return self.gerät


async def test_ohne_zugangsdaten_nennt_der_fehler_die_ursache(jd, monkeypatch):
    """Without credentials the error says so - not "login failed"."""
    monkeypatch.setattr(config, "MYJD_EMAIL", "")
    monkeypatch.setattr(config, "MYJD_PASSWORD", "")

    assert await jd.reconfigure() is False
    assert jd.last_error == "JDownloader credentials are not configured"


async def test_der_alte_fehler_weicht_dem_aktuellen(jd, monkeypatch):
    """A failed reconnect names the **current** reason - not the older one.

    The error of an earlier attempt stood in the status for hours before
    (#681). The reconnect therefore clears the state first and sets the
    current reason afterwards.
    """
    monkeypatch.setattr(config, "MYJD_EMAIL", "")
    monkeypatch.setattr(config, "MYJD_PASSWORD", "")
    jd.last_error = "MyJD login failed: TYPE: AUTH_FAILED"

    assert await jd.reconfigure() is False
    assert jd.last_error == "JDownloader credentials are not configured"
    assert "AUTH_FAILED" not in jd.last_error


async def test_ein_fehlschlagener_login_bleibt_stehen(jd, monkeypatch):
    """A failed login carries the reason of JDownloader."""
    monkeypatch.setattr(config, "MYJD_EMAIL", "a@b.invalid")
    monkeypatch.setattr(config, "MYJD_PASSWORD", "falsch")
    jd._client.login_fehler = RuntimeError("TYPE: AUTH_FAILED")

    assert await jd.reconfigure() is False
    assert jd.last_error is not None
    assert "AUTH_FAILED" in jd.last_error


async def test_ein_fehlendes_geraet_wird_benannt(jd, monkeypatch):
    """The connection works, the device does not exist - that is the reason."""
    monkeypatch.setattr(config, "MYJD_EMAIL", "a@b.invalid")
    monkeypatch.setattr(config, "MYJD_PASSWORD", "geheim")
    monkeypatch.setattr(config, "MYJD_DEVICE_NAME", "UnraidDocker")
    jd._client.gerät_fehler = RuntimeError("no such device")

    assert await jd.reconfigure() is False
    assert jd.last_error == "device 'UnraidDocker' not found"


async def test_ein_erfolgreicher_neuaufbau_raeumt_den_fehler(jd, monkeypatch):
    """A successful reconnect clears the error of the earlier attempt."""
    monkeypatch.setattr(config, "MYJD_EMAIL", "a@b.invalid")
    monkeypatch.setattr(config, "MYJD_PASSWORD", "geheim")
    monkeypatch.setattr(config, "MYJD_DEVICE_NAME", "UnraidDocker")
    jd._client.gerät = type("Geraet", (), {"device_id": "d1", "name": "UnraidDocker"})()
    jd.last_error = "alter Fehler"

    assert await jd.reconfigure() is True
    assert jd.last_error is None
