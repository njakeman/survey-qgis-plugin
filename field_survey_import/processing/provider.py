"""Processing provider registering the plugin's algorithms under
Processing » Field Survey. Phase 0 stub — algorithms are added in milestone 3.
"""
from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from ..plugin import ICON_PATH


class FieldSurveyProvider(QgsProcessingProvider):
    def id(self) -> str:  # noqa: A003
        return "field_survey"

    def name(self) -> str:
        return "Field Survey"

    def icon(self) -> QIcon:
        return QIcon(ICON_PATH)

    def loadAlgorithms(self) -> None:  # noqa: N802
        from .import_algorithm import ImportSurveyZipAlgorithm

        self.addAlgorithm(ImportSurveyZipAlgorithm())
