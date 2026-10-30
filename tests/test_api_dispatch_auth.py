"""Authentication on the SABnzbd dispatch `/api`.

`main.py` already compared the key in constant time
(`secrets.compare_digest`), but the dispatch in `sabnzbd_api.py` used `!=`.
Both checks protect the same key; a constant-time check that is missing in
one place is no constant-time check.

The behaviour test therefore does not inspect the implementation but the
contractual property: that `/api` never returns data without a key or with a
wrong key **at all** — and that `mode=auth` releases with the right key.
"""

import secrets

import pytest
from fastapi.testclient import TestClient

from app.config import config
from app.main import app

KEY = "test-bridge-key"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "DDLNZB_API_KEY", KEY)
    return TestClient(app)


@pytest.fixture
def client_ohne_key(monkeypatch):
    monkeypatch.setattr(config, "DDLNZB_API_KEY", "")
    return TestClient(app)


@pytest.mark.parametrize(
    "params",
    [
        {"mode": "version"},
        {"mode": "auth"},
        {"mode": "queue", "output": "json"},
        {"mode": "ddlnzb_links", "release_id": "abc"},
    ],
)
def test_falscher_key_wird_ueberall_abgewiesen(client, params):
    antwort = client.get("/api", params={**params, "apikey": "wrong-key"})

    assert antwort.status_code == 200
    assert antwort.json()["status"] is False


def test_fehlender_key_wird_abgewiesen(client):
    """A missing parameter is a `None` — not an empty string.

    This is exactly where the switch to `compare_digest` matters: the
    comparison must not fail on a `None` already, otherwise an empty key
    would be an exception instead of a rejection.
    """
    antwort = client.get("/api", params={"mode": "auth"})

    assert antwort.json()["status"] is False


def test_leerer_key_wird_abgewiesen(client):
    antwort = client.get("/api", params={"mode": "auth", "apikey": ""})

    assert antwort.json()["status"] is False


def test_ohne_konfigurierten_key_ist_nichts_erreichbar(client_ohne_key):
    """An empty configuration locks everything down — otherwise the bridge could be switched off."""
    antwort = client_ohne_key.get("/api", params={"mode": "auth", "apikey": ""})

    assert antwort.json()["status"] is False


def test_richtiger_key_schaltet_mode_auth_frei(client):
    antwort = client.get("/api", params={"mode": "auth", "apikey": KEY})

    assert antwort.json() == {"auth": "ok"}


def test_richtiger_key_liefert_echte_daten(client):
    """Counter-check: the lock is not applied wholesale — valid calls get through."""
    antwort = client.get("/api", params={"mode": "version", "apikey": KEY})

    assert antwort.json()["version"] == "4.0.0-ddlnzb"


def test_kurzer_key_steht_nicht_vor_langem(client):
    """Constant-time comparison: a prefix of the correct key is not enough."""
    antwort = client.get("/api", params={"mode": "auth", "apikey": KEY[:-1]})

    assert antwort.json()["status"] is False


def test_der_download_token_ist_keine_berechtigung(client):
    """`addurl` is behind the key too - the token authorizes nothing.

    The download token of the NZB link is read **without** verifying its
    signature (see `app/AGENTS.md`). That is only harmless because the key
    is the boundary of this hop: the token names a release, the key
    authorizes the call. A mode that accepted a token without a key would
    turn the unsigned token into a bearer credential.
    """
    antwort = client.get(
        "/api",
        params={
            "mode": "addurl",
            "name": "https://ddlnzb.invalid/api/download?payload=abc.def",
            "nzbname": "release.nzb",
            "apikey": "wrong-key",
        },
    )

    assert antwort.json() == {"status": False, "error": "Invalid API key"}


def test_vergleich_laeuft_ueber_compare_digest(client, monkeypatch):
    """The constant-time comparison must actually be called.

    All the tests above do not distinguish `!=` from `compare_digest` — both
    return the same answer, the difference lies only in the runtime. A pure
    behaviour test therefore cannot see the regression and would stay green
    even though the key would be compared with `!=` again.

    That is why the call is observed instead of the result: the function is
    replaced by a decoy that passes the real result through and notes in the
    process that it was involved at all.
    """
    import app.sabnzbd_api as sabnzbd_api

    aufrufe = []
    original = secrets.compare_digest

    def spy(a, b):
        aufrufe.append((a, b))
        return original(a, b)

    monkeypatch.setattr(sabnzbd_api.secrets, "compare_digest", spy)

    antwort = client.get("/api", params={"mode": "auth", "apikey": KEY})

    assert antwort.json() == {"auth": "ok"}
    assert aufrufe == [(KEY, KEY)]
