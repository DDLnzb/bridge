"""#720 - the CORS policy of the bridge.

`allow_origins=["*"]` together with `allow_credentials=True` made Starlette
reflect **every** origin - the origin of the request was not checked against a
list, it was adopted. The only thing that still protected the web UI was the
`SameSite=Strict` attribute of its session cookie.

The bridge needs no CORS at all: the *arr apps are not browsers, and the web UI
is served from the same origin as the API. The default is therefore an empty
allowlist; `BRIDGE_CORS_ORIGINS` is the explicit way to open it for a
deployment that operates the UI from another origin.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app.config import ENV_SOURCES, config
from app.main import app


@pytest.fixture(autouse=True)
def restore_config():
    """Resets the global config after the test."""
    snapshot = {name: getattr(config, name) for name in ENV_SOURCES}
    yield
    for name, value in snapshot.items():
        setattr(config, name, value)


@pytest.fixture
def app_with_allowlist(monkeypatch):
    """`app.main` with a CORS allowlist.

    The allowlist is read when the module builds the app - the module has to be
    reloaded after the environment changed.
    """
    main_module = importlib.import_module("app.main")

    monkeypatch.setenv("BRIDGE_CORS_ORIGINS", "https://ui.example")
    config.reload()
    reloaded = importlib.reload(main_module)
    try:
        yield reloaded.app
    finally:
        # The environment of the monkeypatch is undone explicitly: the config
        # must not keep the allowlist of the test.
        monkeypatch.undo()
        config.reload()
        importlib.reload(main_module)


def test_foreign_origin_gets_no_cors_header():
    """A foreign origin must not be reflected."""
    client = TestClient(app)

    antwort = client.get("/health", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in antwort.headers
    assert "access-control-allow-credentials" not in antwort.headers


def test_foreign_origin_is_refused_on_a_preflight():
    """The preflight of a foreign origin does not succeed either."""
    client = TestClient(app)

    antwort = client.options(
        "/api",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert antwort.status_code == 400
    assert "access-control-allow-origin" not in antwort.headers


def test_configured_origin_is_allowed(app_with_allowlist):
    """An origin from the allowlist receives the header."""
    client = TestClient(app_with_allowlist)

    antwort = client.get("/health", headers={"Origin": "https://ui.example"})

    assert antwort.headers.get("access-control-allow-origin") == "https://ui.example"
    # Credentials are never allowed - the bridge authenticates with the key.
    assert "access-control-allow-credentials" not in antwort.headers


def test_other_origins_stay_refused_with_an_allowlist(app_with_allowlist):
    """The allowlist is a list, not a wildcard."""
    client = TestClient(app_with_allowlist)

    antwort = client.get("/health", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in antwort.headers


def test_without_an_origin_there_are_no_cors_headers():
    """A request without an Origin gets no CORS headers at all."""
    client = TestClient(app)

    antwort = client.get("/health")

    assert "access-control-allow-origin" not in antwort.headers
