"""#715 - the settings file has to be applied when the bridge starts.

The configuration entered via the web UI was only read by the **write** routes
of that UI. After a restart of the container the bridge started with the
environment alone: the API key was empty, a setup token was written into the
log and every call of the *arr apps was answered with 401 - although the
settings file was mounted and the documentation promised the opposite.

The precedence `settings file > environment > default` must therefore apply at
startup just like it applies after a write.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import webui
from app.config import ENV_SOURCES, BridgeConfig, config
from app.main import app
from app.persistence import queue_persistence


@pytest.fixture(autouse=True)
def restore_config():
    """Resets the global config after the test.

    `config` is a module global - the reload in the lifespan would otherwise
    carry the values over into the next test.
    """
    snapshot = {name: getattr(config, name) for name in ENV_SOURCES}
    yield
    for name, value in snapshot.items():
        setattr(config, name, value)


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """Settings file in the tmp directory - the real file stays untouched."""
    path = tmp_path / "settings.json"
    monkeypatch.setenv("DDLNZB_SETTINGS_FILE", str(path))
    monkeypatch.delenv("DDLNZB_API_KEY", raising=False)
    # The health check in the lifespan must not reach the internet.
    monkeypatch.setenv("DDLNZB_API_URL", "http://127.0.0.1:9")
    # The queue file written on shutdown must not land in the working tree.
    monkeypatch.setattr(queue_persistence, "file_path", tmp_path / "queue.json")
    return path


def _write_settings(path, values):
    path.write_text(json.dumps({"version": 1, "values": values}), encoding="utf-8")


def test_settings_file_is_applied_at_startup(settings_file):
    """The key from the settings file is effective without any UI write."""
    _write_settings(settings_file, {"DDLNZB_API_KEY": "FROM-SETTINGS-FILE"})

    with TestClient(app):
        assert config.DDLNZB_API_KEY == "FROM-SETTINGS-FILE"


def test_no_setup_token_when_the_key_comes_from_the_settings_file(settings_file, monkeypatch):
    """A configured bridge does not print a setup token.

    The token is the documented way into the initial setup - it must not
    appear while a key is configured.
    """
    _write_settings(settings_file, {"DDLNZB_API_KEY": "FROM-SETTINGS-FILE"})

    calls = []
    monkeypatch.setattr(webui, "setup_token", lambda: calls.append("setup_token"))

    with TestClient(app):
        pass

    assert calls == []


def test_the_key_from_the_settings_file_is_accepted(settings_file):
    """The key is not only in the config - it also opens the protected routes."""
    _write_settings(settings_file, {"DDLNZB_API_KEY": "FROM-SETTINGS-FILE"})

    with TestClient(app) as client:
        antwort = client.get("/status", headers={"X-API-Key": "FROM-SETTINGS-FILE"})

    assert antwort.status_code == 200


def test_settings_file_wins_over_the_environment(settings_file, monkeypatch):
    """The precedence is `settings file > environment`."""
    monkeypatch.setenv("DDLNZB_API_KEY", "FROM-ENV")
    _write_settings(settings_file, {"DDLNZB_API_KEY": "FROM-SETTINGS-FILE"})

    with TestClient(app):
        assert config.DDLNZB_API_KEY == "FROM-SETTINGS-FILE"


def test_environment_applies_without_a_settings_file(settings_file, monkeypatch):
    """Without a file the environment applies - as before."""
    monkeypatch.setenv("DDLNZB_API_KEY", "FROM-ENV")

    with TestClient(app):
        assert config.DDLNZB_API_KEY == "FROM-ENV"


def test_a_fresh_config_instance_reads_the_settings_file(settings_file):
    """The file is applied when the config is created - not only on reload."""
    _write_settings(settings_file, {"DDLNZB_API_KEY": "FROM-SETTINGS-FILE"})

    frisch = BridgeConfig()

    assert frisch.DDLNZB_API_KEY == "FROM-SETTINGS-FILE"


def test_a_broken_settings_file_does_not_prevent_the_start(settings_file, monkeypatch):
    """A broken file is logged, not fatal - the bridge starts with the env."""
    settings_file.write_text("{kaputt", encoding="utf-8")
    monkeypatch.setenv("DDLNZB_API_KEY", "FROM-ENV")

    with TestClient(app):
        assert config.DDLNZB_API_KEY == "FROM-ENV"
