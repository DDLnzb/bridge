"""Constant-time comparison of the bridge API key - over bytes.

`secrets.compare_digest` refuses `str` arguments that contain non-ASCII
characters: it raises a `TypeError`. A client sending such a key therefore
got an HTTP 500 instead of a 401 - the exception happened inside the
comparison, before any decision could be made (#1050). The parameter is
unauthenticated, so the 500 was reachable from the outside without a key.

The comparison lives in `app/api_key.py`, the single place that knows how
the key is compared. Both call sites use it: the SABnzbd dispatch
(`app/sabnzbd_api.py`) and the web UI dependency (`app/main.py`).
"""

import pytest
from fastapi.testclient import TestClient

from app.config import config
from app.main import app

KEY = "test-bridge-key"

#: A key with a character outside ASCII. `secrets.compare_digest` refuses
#: such a `str` pair with a `TypeError`.
#: The character is written as an escape so that the file stays pure ASCII -
#: the test only needs *a* non-ASCII character, not a specific one.
NON_ASCII_KEY = "key-mit-umlaut-\u00e4"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "DDLNZB_API_KEY", KEY)
    return TestClient(app)


def test_nicht_ascii_key_wird_im_dispatch_abgewiesen(client):
    """The SABnzbd dispatch answers with the error, not with a crash."""
    antwort = client.get("/api", params={"mode": "auth", "apikey": NON_ASCII_KEY})

    assert antwort.status_code == 200
    assert antwort.json()["status"] is False


def test_nicht_ascii_key_wird_im_ui_pfad_abgewiesen(client):
    """The web UI dependency rejects the same key with a 401."""
    antwort = client.get("/status", params={"apikey": NON_ASCII_KEY})

    assert antwort.status_code == 401


def test_der_richtige_key_wird_weiterhin_angenommen(client):
    """Counter-check: the comparison did not become stricter than before."""
    antwort = client.get("/api", params={"mode": "auth", "apikey": KEY})

    assert antwort.json() == {"auth": "ok"}


def test_ein_falscher_ascii_key_wird_weiterhin_abgewiesen(client):
    """A wrong ASCII key stays a rejection - the byte comparison did not
    turn every key into a match."""
    antwort = client.get("/api", params={"mode": "auth", "apikey": "wrong-key"})

    assert antwort.json()["status"] is False
