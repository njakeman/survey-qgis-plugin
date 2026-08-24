"""Shared fixtures for the QGIS-dependent test suite (run via
scripts/run-qgis-tests.ps1, under the QGIS-bundled interpreter).
"""
import sys
from pathlib import Path

import pytest
from qgis.core import QgsApplication

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SAMPLE_ZIP = REPO_ROOT / "sample.zip"

_QGIS_PREFIX = r"C:\Program Files\QGIS 3.44.8\apps\qgis-ltr"

_qgs_app = None


@pytest.fixture(scope="session", autouse=True)
def qgis_app():
    """Initialise a single headless QgsApplication for the whole test session."""
    global _qgs_app
    if _qgs_app is None:
        QgsApplication.setPrefixPath(_QGIS_PREFIX, True)
        _qgs_app = QgsApplication([], False)
        _qgs_app.initQgis()
        yield _qgs_app
        _qgs_app.exitQgis()
    else:  # pragma: no cover - session-scoped, shouldn't recurse
        yield _qgs_app


class FakeIface:
    """Minimal stand-in for QgisInterface, enough to exercise
    FieldSurveyPlugin.initGui()/unload() without a running QGIS GUI session.
    """

    def __init__(self):
        from qgis.PyQt.QtWidgets import QMainWindow

        self._main_window = QMainWindow()
        self._toolbars = []
        self._menu_actions = []

    def addToolBar(self, name):
        from qgis.PyQt.QtWidgets import QToolBar

        bar = QToolBar(name)
        self._toolbars.append(bar)
        return bar

    def mainWindow(self):
        return self._main_window

    def addPluginToMenu(self, menu, action):
        self._menu_actions.append((menu, action))

    def removePluginMenu(self, menu, action):
        self._menu_actions = [(m, a) for (m, a) in self._menu_actions if a is not action]

    def removeToolBarIcon(self, action):
        pass


@pytest.fixture
def fake_iface():
    return FakeIface()
