"""Authentifizierung der Bridge-Routen außer `/api`.

Deckt #389 ab: `/`, `/status` und `/heartbeat/reconnect` waren frei
erreichbar. `/health` bleibt bewusst offen — Load-Balancer und der
Docker-HEALTHCHECK müssen ihn ohne Zugangsdaten erreichen.
"""

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


@pytest.mark.parametrize("pfad", ["/", "/status"])
def test_diagnoserouten_ohne_key_sind_gesperrt(client, pfad):
    assert client.get(pfad).status_code == 401
    assert client.get(pfad, headers={"X-API-Key": ""}).status_code == 401
    assert client.get(pfad, headers={"X-API-Key": "falscher-key"}).status_code == 401


@pytest.mark.parametrize("pfad", ["/", "/status"])
def test_diagnoserouten_mit_key_sind_erreichbar(client, pfad):
    antwort = client.get(pfad, headers={"X-API-Key": KEY})

    assert antwort.status_code == 200
    assert antwort.json()["name"] == "DDLnzb Bridge"


def test_status_liefert_keine_daten_ohne_key(client):
    """Die Aufklärung ist der eigentliche Schaden - sie darf nicht durchsickern."""
    antwort = client.get("/status", headers={"X-API-Key": "falscher-key"})

    assert antwort.status_code == 401
    assert "device_id" not in antwort.text
    assert "heartbeat" not in antwort.text


def test_heartbeat_reconnect_ist_gesperrt(client):
    antwort = client.post("/heartbeat/reconnect")

    assert antwort.status_code == 401
    assert antwort.json()["detail"] == "Invalid API key"


def test_heartbeat_reconnect_mit_falschem_key_ist_gesperrt(client):
    antwort = client.post("/heartbeat/reconnect", headers={"X-API-Key": "falscher-key"})

    assert antwort.status_code == 401


def test_health_bleibt_offen(client):
    antwort = client.get("/health")

    assert antwort.status_code == 200
    assert antwort.json() == {"status": "healthy"}


@pytest.mark.parametrize("pfad", ["/", "/status"])
def test_ohne_konfigurierten_key_ist_alles_gesperrt(client_ohne_key, pfad):
    """Fehlende Konfiguration darf nicht zu offenem Zugang führen."""
    assert client_ohne_key.get(pfad).status_code == 401
    assert client_ohne_key.get(pfad, headers={"X-API-Key": ""}).status_code == 401


def test_apikey_query_param_wird_akzeptiert(client):
    """Maschinenclients können keinen Header setzen - wie in sabnzbd_api.py."""
    assert client.get("/status", params={"apikey": KEY}).status_code == 200
    assert client.get("/status", params={"apikey": "falsch"}).status_code == 401
