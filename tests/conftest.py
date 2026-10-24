"""Shared fixtures for the bridge tests.

Important for all tests here: the `app/` modules are imported absolutely as
`from app.…`, so the test run starts only from the `bridge/` root.
`STATE_PATH` is built from the config at module import time and is not read
again afterwards — for tests with their own state path, `app.config` and
`app.sabnzbd_api` must be re-imported after setting the env variable.
That is what `fresh_import` does.
"""

import importlib
import sys

import pytest


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """State file in the tmp directory. Must be set BEFORE the module import."""
    path = tmp_path / "bridge_state.json"
    monkeypatch.setenv("DDLNZB_STATE_FILE", str(path))
    return path


@pytest.fixture
def fresh_import(state_file):
    """Returns a function that imports `app.sabnzbd_api` freshly.

    It deliberately pops the modules before every import: a leftover from a
    previous test would otherwise drag a different `STATE_PATH` along.
    """

    def _import():
        for module in ("app.sabnzbd_api", "app.config"):
            sys.modules.pop(module, None)
        return importlib.import_module("app.sabnzbd_api")

    return _import
