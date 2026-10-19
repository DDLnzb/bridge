"""Gemeinsame Fixtures der Bridge-Tests.

Wichtig für alle Tests hier: die `app/`-Module importieren absolut als
`from app.…`, der Teststart erfolgt also nur aus der `bridge/`-Wurzel.
`STATE_PATH` wird beim Modulimport aus der Config gebildet und danach nicht
mehr neu gelesen — für Tests mit eigenem State-Pfad muss `app.config` und
`app.sabnzbd_api` nach dem Setzen der Env-Variable neu importiert werden.
Das erledigt `fresh_import`.
"""

import importlib
import sys

import pytest


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """State-Datei im tmp-Verzeichnis. Muss VOR dem Modulimport gesetzt werden."""
    path = tmp_path / "bridge_state.json"
    monkeypatch.setenv("DDLNZB_STATE_FILE", str(path))
    return path


@pytest.fixture
def fresh_import(state_file):
    """Liefert eine Funktion, die `app.sabnzbd_api` frisch importiert.

    Bewusst poppt sie vor jedem Import: ein Rückstand aus einem vorherigen Test
    würde sonst einen anderen `STATE_PATH` mitschleppen.
    """

    def _import():
        for module in ("app.sabnzbd_api", "app.config"):
            sys.modules.pop(module, None)
        return importlib.import_module("app.sabnzbd_api")

    return _import
