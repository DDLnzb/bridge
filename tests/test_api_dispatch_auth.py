"""Authentifizierung am SABnzbd-Dispatch `/api` — Issue #478.

`main.py` verglich den Schlüssel bereits zeitkonstant
(`secrets.compare_digest`), der Dispatch in `sabnzbd_api.py` dagegen mit `!=`.
Beide Prüfungen schützen denselben Schlüssel; eine zeitkonstante Prüfung, die an
einer Stelle fehlt, ist keine.

Der Verhaltenstest prüft deshalb nicht die Implementierung, sondern die
vertragliche Eigenschaft: dass `/api` ohne oder mit falschem Key **nie** Daten
liefert — und dass `mode=auth` mit dem richtigen Key freigibt.
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
    antwort = client.get("/api", params={**params, "apikey": "falscher-key"})

    assert antwort.status_code == 200
    assert antwort.json()["status"] is False


def test_fehlender_key_wird_abgewiesen(client):
    """Ein fehlender Parameter ist ein `None` - kein leerer String.

    Genau hier ist die Umstellung auf `compare_digest` wichtig: der Vergleich
    darf nicht schon an einem `None` scheitern, sonst wäre ein leerer Key eine
    Ausnahme statt einer Ablehnung.
    """
    antwort = client.get("/api", params={"mode": "auth"})

    assert antwort.json()["status"] is False


def test_leerer_key_wird_abgewiesen(client):
    antwort = client.get("/api", params={"mode": "auth", "apikey": ""})

    assert antwort.json()["status"] is False


def test_ohne_konfigurierten_key_ist_nichts_erreichbar(client_ohne_key):
    """Leere Konfiguration schließt komplett - sonst wäre der Bridge abschaltbar."""
    antwort = client_ohne_key.get("/api", params={"mode": "auth", "apikey": ""})

    assert antwort.json()["status"] is False


def test_richtiger_key_schaltet_mode_auth_frei(client):
    antwort = client.get("/api", params={"mode": "auth", "apikey": KEY})

    assert antwort.json() == {"auth": "ok"}


def test_richtiger_key_liefert_echte_daten(client):
    """Gegenprobe: die Sperre greift nicht pauschal - gültige Aufrufe kommen durch."""
    antwort = client.get("/api", params={"mode": "version", "apikey": KEY})

    assert antwort.json()["version"] == "4.0.0-ddlnzb"


def test_kurzer_key_steht_nicht_vor_langem(client):
    """Zeitkonstanter Vergleich: ein Präfix des richtigen Schlüssels genügt nicht."""
    antwort = client.get("/api", params={"mode": "auth", "apikey": KEY[:-1]})

    assert antwort.json()["status"] is False


def test_vergleich_laeuft_ueber_compare_digest(client, monkeypatch):
    """Der zeitkonstante Vergleich muss tatsächlich aufgerufen werden.

    Alle Tests oben unterscheiden `!=` und `compare_digest` **nicht** - beide
    liefern dieselbe Antwort, der Unterschied liegt nur in der Laufzeit. Ein
    reiner Verhaltenstest kann die Regression also nicht sehen und wäre grün,
    obwohl der Schlüssel wieder mit `!=` verglichen würde.

    Deshalb wird der Aufruf beobachtet statt das Ergebnis: die Funktion wird
    durch eine Attrappe ersetzt, die das echte Ergebnis durchreicht und sich
    dabei merkt, dass sie überhaupt beteiligt war.
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
