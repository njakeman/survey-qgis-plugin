"""Processing » Field Survey » Import survey zip. A thin wrapper over exactly the
same import_flow/duplicates code the dialog uses (handoff §9.2) - this is what
makes batch-importing many zips possible via QGIS's built-in "Run as batch
process" button, and gives a non-interactive conflict policy instead of a modal
dialog (decision: dialog primary, Processing algorithm for scripting/batch).
"""
import zipfile
from pathlib import Path

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
)

from ..core import identity, reader
from ..qgis import duplicates, import_flow

SKIP, REPLACE, ADD_ALONGSIDE, FAIL = range(4)
_ON_DUPLICATE_OPTIONS = ["Skip", "Replace existing", "Add alongside", "Fail"]


class ImportSurveyZipAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    DESTINATION = "DESTINATION"
    ON_DUPLICATE = "ON_DUPLICATE"

    OUTPUT_SESSION_NAME = "SESSION_NAME"
    OUTPUT_POINT_COUNT = "POINT_COUNT"
    OUTPUT_PATH_COUNT = "PATH_COUNT"
    OUTPUT_BOUNDARY_COUNT = "BOUNDARY_COUNT"
    OUTPUT_GPKG_PATH = "GPKG_PATH"
    OUTPUT_STATUS = "STATUS"

    def createInstance(self):
        return ImportSurveyZipAlgorithm()

    def name(self) -> str:
        return "import_survey_zip"

    def displayName(self) -> str:
        return "Import survey zip…"

    def group(self) -> str:
        return "Field Survey"

    def groupId(self) -> str:
        return "field_survey"

    def shortHelpString(self) -> str:
        return (
            "Imports a Field Survey app export zip into a GeoPackage as Points/Paths/"
            "Boundaries layers, exactly as the toolbar's Import dialog does. Use QGIS's "
            "'Run as batch process' to import many zips at once. ON_DUPLICATE controls "
            "what happens when the zip's session was already imported into DESTINATION."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                self.INPUT, "Field Survey export (.zip)", extension="zip"
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.DESTINATION,
                "Destination GeoPackage",
                behavior=QgsProcessingParameterFile.File,
                extension="gpkg",
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.ON_DUPLICATE,
                "If this session is already in the destination",
                options=_ON_DUPLICATE_OPTIONS,
                defaultValue=SKIP,
            )
        )
        self.addOutput(QgsProcessingOutputString(self.OUTPUT_SESSION_NAME, "Session name"))
        self.addOutput(QgsProcessingOutputNumber(self.OUTPUT_POINT_COUNT, "Points imported"))
        self.addOutput(QgsProcessingOutputNumber(self.OUTPUT_PATH_COUNT, "Paths imported"))
        self.addOutput(QgsProcessingOutputNumber(self.OUTPUT_BOUNDARY_COUNT, "Boundaries imported"))
        self.addOutput(QgsProcessingOutputString(self.OUTPUT_GPKG_PATH, "GeoPackage written to"))
        self.addOutput(QgsProcessingOutputString(self.OUTPUT_STATUS, "Status"))

    def processAlgorithm(self, parameters, context, feedback):
        zip_path = Path(self.parameterAsFile(parameters, self.INPUT, context))
        gpkg_path = Path(self.parameterAsFile(parameters, self.DESTINATION, context))
        on_duplicate = self.parameterAsEnum(parameters, self.ON_DUPLICATE, context)

        feedback.pushInfo(f"Reading {zip_path.name}…")
        content_hash = identity.content_hash(zip_path)

        with zipfile.ZipFile(zip_path) as zf:
            export = reader.read_export(zf)

        check = duplicates.check_duplicate(gpkg_path, export.session.id, content_hash)

        if check.status is duplicates.DuplicateStatus.ALREADY_IMPORTED:
            feedback.pushInfo("Already imported (identical content) - nothing to do.")
            return self._result(export.session.name, [], gpkg_path, "already_imported")

        if check.status is duplicates.DuplicateStatus.CONFLICT:
            if on_duplicate == SKIP:
                feedback.pushInfo(
                    f'"{export.session.name}" already imported with different content - skipping '
                    "(ON_DUPLICATE=Skip)."
                )
                return self._result(export.session.name, [], gpkg_path, "skipped_conflict")
            if on_duplicate == FAIL:
                raise QgsProcessingException(
                    f'"{export.session.name}" already imported with different content '
                    "(ON_DUPLICATE=Fail)."
                )
            if on_duplicate == REPLACE:
                feedback.pushInfo("Replacing the existing import…")
                result = duplicates.replace_import(zip_path, gpkg_path, export.session.id)
            else:  # ADD_ALONGSIDE
                feedback.pushInfo("Adding alongside the existing import…")
                result = duplicates.add_alongside_import(zip_path, gpkg_path, export.session)
        else:
            result = import_flow.import_zip(zip_path, gpkg_path)

        feedback.pushInfo(f'Imported "{result.session_name}" -> {gpkg_path}')
        return self._result(result.session_name, result.layers, gpkg_path, "imported")

    def _result(self, session_name, layers, gpkg_path, status):
        counts = {layer.geometry_type.value: layer.feature_count for layer in layers}
        return {
            self.OUTPUT_SESSION_NAME: session_name,
            self.OUTPUT_POINT_COUNT: counts.get("Point", 0),
            self.OUTPUT_PATH_COUNT: counts.get("LineString", 0),
            self.OUTPUT_BOUNDARY_COUNT: counts.get("Polygon", 0),
            self.OUTPUT_GPKG_PATH: str(gpkg_path),
            self.OUTPUT_STATUS: status,
        }
