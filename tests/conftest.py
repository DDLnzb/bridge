"""Shared fixtures for the bridge tests.

Important for all tests here: the `app/` modules are imported absolutely as
`from app.…`, so the test run starts only from the `bridge/` root.

`STATE_PATH` is built from the config at module import time and is not read
again afterwards — the `state_file` fixture therefore patches the constant
itself. For tests that need a **new** module instance, `app.config` and
`app.sabnzbd_api` must be re-imported after setting the env variable.
That is what `fresh_import` does.
"""

import importlib
import sys

import pytest

from app.persistence import queue_persistence


@pytest.fixture(autouse=True)
def queue_file_in_tmp(tmp_path, monkeypatch):
    """The queue file must not land in the working tree.

    The bridge writes the queue file on **every** change of the queue (see
    `app/sabnzbd_api.py::_persist_state`). A test that persists a state
    therefore wrote its jobs into `bridge/data/queue.json` - a file a local
    bridge restores at the next start.

    The path is redirected here, for **every** test. `monkeypatch` undoes
    the change afterwards - a plain assignment on the singleton would stay
    and the following tests would write into a deleted directory.
    """
    monkeypatch.setattr(queue_persistence, "file_path", tmp_path / "queue.json")
    return tmp_path / "queue.json"


@pytest.fixture(autouse=True)
def state_file(tmp_path, monkeypatch):
    """The state file of every test lies in the tmp directory.

    The bridge writes the state file on every change of the queue or the
    history. A test that persists a state therefore wrote into the working
    tree - `bridge/data/state.json`, a file a local bridge restores at the
    next start.

    ⚠️ **`STATE_PATH` is patched directly.** It is a module constant of
    `app.sabnzbd_api`, built at import time — the environment variable
    reaches only the tests that re-import the module (`fresh_import`). A
    test that does not re-import would keep the path of the working tree.

    The environment variable is set as well: it is what `fresh_import` reads
    when it builds the module anew.
    """
    path = tmp_path / "bridge_state.json"
    monkeypatch.setenv("DDLNZB_STATE_FILE", str(path))

    import app.sabnzbd_api as sabnzbd_api

    monkeypatch.setattr(sabnzbd_api, "STATE_PATH", path)
    return path


@pytest.fixture
def fresh_import(state_file):
    """Returns a function that imports `app.sabnzbd_api` freshly.

    It deliberately pops the modules before every import: a leftover from a
    previous test would otherwise drag a different `STATE_PATH` along.

    ⚠️ **`app.download_handler` is popped as well.** It binds the names of
    `app.sabnzbd_api` **at import time** (`from app.sabnzbd_api import
    complete_download, …`). Without the reload it would keep calling the
    *old* module instance — with the `STATE_PATH` of the working tree. A
    test that persisted a state through the download handler therefore wrote
    into `bridge/data/state.json` although the module in `sys.modules` had
    the path of the tmp directory.
    """

    def _import():
        for module in ("app.sabnzbd_api", "app.download_handler", "app.config"):
            sys.modules.pop(module, None)
        return importlib.import_module("app.sabnzbd_api")

    return _import
