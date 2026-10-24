"""Web UI of the bridge: auth, initial setup, saving, secrets.

Covers:
- the initial setup via the setup token (including rejection without token)
- the login with the bridge key and the session cookie
- saving the configuration and its immediate effect
- the protection of secrets (not mirrored back, empty = unchanged)
- the protection of the writing endpoints without a valid session
"""

import pytest
from fastapi.testclient import TestClient

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
    """Resets the global config after the test.

    `config` is a module global; `config.reload()` in the routes would
    otherwise carry the values over into the next test.
    """
    snapshot = {name: getattr(config, name) for name in ENV_SOURCES}
    yield
    for name, value in snapshot.items():
        setattr(config, name, value)


@pytest.fixture
def client(monkeypatch):
    """Test client with a configured key."""
    monkeypatch.setenv("DDLNZB_API_KEY", KEY)
    config.reload()
    return TestClient(app)


@pytest.fixture
def client_ohne_key(monkeypatch):
    """Test client without a configured key - the initial setup is active."""
    monkeypatch.delenv("DDLNZB_API_KEY", raising=False)
    config.reload()
    return TestClient(app)


def _login(client) -> None:
    """Logs in and keeps the session cookie."""
    antwort = client.post("/ui/login", data={"key": KEY}, follow_redirects=False)
    assert antwort.status_code == 303


# -------------------------------------------------------------- Start page


def test_startseite_zeigt_setup_wenn_kein_key(client_ohne_key):
    antwort = client_ohne_key.get("/ui")

    assert antwort.status_code == 200
    assert "Setup" in antwort.text
    assert "setup_token" in antwort.text


def test_startseite_zeigt_login_wenn_key_gesetzt_aber_keine_session(client):
    antwort = client.get("/ui")

    assert antwort.status_code == 200
    assert "Login" in antwort.text
    assert "Set up the bridge" not in antwort.text


def test_startseite_zeigt_dashboard_mit_session(client):
    _login(client)

    antwort = client.get("/ui")

    assert antwort.status_code == 200
    assert "Set up the bridge" in antwort.text
    assert "JDownloader" in antwort.text


# ---------------------------------------------------------- Initial setup


def test_setup_ohne_token_wird_abgelehnt(client_ohne_key):
    antwort = client_ohne_key.post(
        "/ui/setup",
        data={"setup_token": "wrong", "api_key": KEY},
        follow_redirects=False,
    )

    assert antwort.status_code == 401
    assert "Invalid setup token" in antwort.text


def test_setup_mit_token_speichert_key_und_meldet_an(client_ohne_key, settings_file, monkeypatch):
    monkeypatch.setattr(webui, "_SETUP_TOKEN", TOKEN)

    antwort = client_ohne_key.post(
        "/ui/setup",
        data={"setup_token": TOKEN, "api_key": KEY, "jd_device": "Seedbox"},
        follow_redirects=False,
    )

    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/ui"
    assert config.DDLNZB_API_KEY == KEY
    assert settings_file.exists()
    assert KEY in settings_file.read_text(encoding="utf-8")
    # The token is used up and must not be reusable.
    assert webui._SETUP_TOKEN is None


def test_setup_ohne_api_key_wird_abgelehnt(client_ohne_key, monkeypatch):
    monkeypatch.setattr(webui, "_SETUP_TOKEN", TOKEN)

    antwort = client_ohne_key.post(
        "/ui/setup",
        data={"setup_token": TOKEN, "api_key": ""},
        follow_redirects=False,
    )

    assert antwort.status_code == 400
    assert config.DDLNZB_API_KEY == ""


def test_setup_nach_konfiguration_nicht_mehr_moeglich(client):
    antwort = client.post(
        "/ui/setup",
        data={"setup_token": TOKEN, "api_key": "other-key"},
        follow_redirects=False,
    )

    assert antwort.status_code == 303
    assert config.DDLNZB_API_KEY == KEY


# ------------------------------------------------------------------- Login


def test_login_mit_falschem_key_wird_abgelehnt(client):
    antwort = client.post("/ui/login", data={"key": "wrong"}, follow_redirects=False)

    assert antwort.status_code == 401
    assert "Invalid API key" in antwort.text


def test_login_setzt_session_cookie(client):
    antwort = client.post("/ui/login", data={"key": KEY}, follow_redirects=False)

    assert antwort.status_code == 303
    assert "ddlnzb_ui" in antwort.cookies
    # The cookie must not contain the key itself.
    assert KEY not in antwort.cookies["ddlnzb_ui"]


def test_logout_beendet_die_session(client):
    _login(client)

    antwort = client.post("/ui/logout", follow_redirects=False)

    assert antwort.status_code == 303
    assert "Set up the bridge" not in client.get("/ui").text


# ---------------------------------------------------------------- Writing


def test_speichern_ohne_session_wird_abgelehnt(client):
    antwort = client.post("/ui/settings", data={"download_dir": "/downloads"})

    assert antwort.status_code == 401


def test_speichern_mit_session_speichert_und_wirkt(client, settings_file):
    _login(client)

    antwort = client.post(
        "/ui/settings",
        data={"download_dir": "/downloads", "extract_archives": "on", "delete_archives": ""},
        follow_redirects=False,
    )

    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/ui?flash=saved"
    assert config.DOWNLOAD_DIR == "/downloads"
    assert config.EXTRACT_ARCHIVES is True
    # An unchecked checkbox is a value, not an empty field.
    assert config.DELETE_ARCHIVES is False
    assert "EXTRACT_ARCHIVES" in settings_file.read_text(encoding="utf-8")


def test_leeres_secret_feld_behaelt_den_gespeicherten_wert(client, settings_file):
    _login(client)
    client.post("/ui/settings", data={"api_key": "new-key"}, follow_redirects=False)

    antwort = client.post(
        "/ui/settings", data={"api_key": "", "download_dir": "/dl"}, follow_redirects=False
    )

    assert antwort.status_code == 303
    assert config.DDLNZB_API_KEY == "new-key"


def test_reset_loescht_die_ueberschreibungen(client, settings_file):
    _login(client)
    client.post("/ui/settings", data={"download_dir": "/downloads"}, follow_redirects=False)

    antwort = client.post("/ui/reset", follow_redirects=False)

    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/ui?flash=reset"
    assert config.DOWNLOAD_DIR == "./downloads"
    assert not settings_file.exists()


# ----------------------------------------------------------------- Secrets


def test_geheimnisse_werden_nicht_zurueckgespiegelt(client):
    _login(client)
    client.post(
        "/ui/settings",
        data={"api_key": "top-secret-key", "myjd_password": "top-secret-password"},
        follow_redirects=False,
    )

    antwort = client.get("/ui")

    assert "top-secret-key" not in antwort.text
    assert "top-secret-password" not in antwort.text
    assert "unchanged" in antwort.text


def test_dashboard_zeigt_die_api_url_nicht_als_eingabefeld(client):
    """The bridge brings the API URL with it - it is not a field in the form."""
    _login(client)

    antwort = client.get("/ui")

    assert 'name="api_url"' not in antwort.text
    assert config.DDLNZB_API_URL in antwort.text


def test_verbindungstest_ist_ohne_session_gesperrt(client):
    antwort = client.post("/ui/test")

    assert antwort.status_code == 401
