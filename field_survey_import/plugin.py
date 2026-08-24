"""QGIS plugin lifecycle: registers the toolbar/menu entry point and (later) the
Processing provider. Import wiring itself lives in field_survey_import.core /
field_survey_import.qgis — this module only handles QGIS registration plumbing.
"""
import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

PLUGIN_NAME = "Field Survey Import"
ICON_PATH = os.path.join(os.path.dirname(__file__), "resources", "icon.svg")


class FieldSurveyPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions: list[QAction] = []
        self.menu = "&Field Survey Import"
        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName("FieldSurveyImportToolbar")
        self._provider = None

    def initGui(self):  # noqa: N802 - QGIS-mandated name
        action = QAction(QIcon(ICON_PATH), "Import Field Survey zip…", self.iface.mainWindow())
        action.triggered.connect(self.run_import)
        self.toolbar.addAction(action)
        self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)

        from .processing.provider import FieldSurveyProvider

        self._provider = FieldSurveyProvider()
        from qgis.core import QgsApplication

        QgsApplication.processingRegistry().addProvider(self._provider)

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        self.actions.clear()
        del self.toolbar

        if self._provider is not None:
            from qgis.core import QgsApplication

            QgsApplication.processingRegistry().removeProvider(self._provider)
            self._provider = None

    def run_import(self):
        from qgis.core import Qgis

        from .ui.import_dialog import ImportDialog

        dialog = ImportDialog(self.iface.mainWindow(), iface=self.iface)
        if dialog.exec_() and dialog.result is not None:
            result = dialog.result
            counts = {layer.geometry_type.value: layer.feature_count for layer in result.layers}
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                f'Imported "{result.session_name}": {counts.get("Point", 0)} points, '
                f'{counts.get("LineString", 0)} paths, {counts.get("Polygon", 0)} boundaries.',
                level=Qgis.Success,
                duration=8,
            )
