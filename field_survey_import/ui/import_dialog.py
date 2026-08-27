"""The import dialog: pick a zip, choose a GeoPackage destination, review a live
preview of what will be imported, then import (handoff §4, §9).
"""
import zipfile
from collections import Counter
from pathlib import Path

from qgis.core import QgsProject, QgsVectorLayer
from qgis.gui import QgsFileWidget
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
)

from ..core import identity
from ..core.errors import SurveyFormatError
from ..core.model import GeometryType
from ..qgis import duplicates, import_flow
from .conflict_dialog import ADD_ALONGSIDE, CANCEL, REPLACE, ConflictDialog

_GEOM_LABELS = {
    GeometryType.POINT: "Points",
    GeometryType.LINE_STRING: "Paths",
    GeometryType.POLYGON: "Boundaries",
}


class ImportDialog(QDialog):
    def __init__(self, parent=None, iface=None):
        super().__init__(parent)
        self.iface = iface
        self.result: import_flow.ImportResult | None = None
        self.result_gpkg_path: Path | None = None

        self._export = None
        self._zip_path: Path | None = None
        self._content_hash: str | None = None

        self.setWindowTitle("Import Field Survey zip…")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Field Survey export (.zip):"))
        self.zip_widget = QgsFileWidget()
        self.zip_widget.setFilter("Field Survey export (*.zip)")
        self.zip_widget.fileChanged.connect(self._on_zip_changed)
        layout.addWidget(self.zip_widget)

        self.preview_label = QLabel("Choose a zip to see what will be imported.")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        dest_box = QGroupBox("Destination")
        dest_layout = QVBoxLayout(dest_box)

        self.dest_existing_radio = QRadioButton("Add to existing GeoPackage")
        self.dest_existing_radio.setChecked(True)
        self.dest_existing_widget = QgsFileWidget()
        self.dest_existing_widget.setStorageMode(QgsFileWidget.SaveFile)
        self.dest_existing_widget.setFilter("GeoPackage (*.gpkg)")

        self.dest_new_radio = QRadioButton("New GeoPackage for this session")
        self.dest_new_widget = QgsFileWidget()
        self.dest_new_widget.setStorageMode(QgsFileWidget.SaveFile)
        self.dest_new_widget.setFilter("GeoPackage (*.gpkg)")

        self.dest_group = QButtonGroup(self)
        self.dest_group.addButton(self.dest_existing_radio)
        self.dest_group.addButton(self.dest_new_radio)

        dest_layout.addWidget(self.dest_existing_radio)
        dest_layout.addWidget(self.dest_existing_widget)
        dest_layout.addWidget(self.dest_new_radio)
        dest_layout.addWidget(self.dest_new_widget)
        layout.addWidget(dest_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- zip parsing / preview -------------------------------------------------

    def _on_zip_changed(self, path: str):
        if not path:
            self._export = None
            self._zip_path = None
            self.preview_label.setText("Choose a zip to see what will be imported.")
            return

        zip_path = Path(path)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                from ..core import reader

                export = reader.read_export(zf)
        except (SurveyFormatError, OSError, zipfile.BadZipFile) as exc:
            self._export = None
            self._zip_path = None
            self.preview_label.setText(f"Could not read this zip: {exc}")
            return

        self._export = export
        self._zip_path = zip_path
        self._content_hash = identity.content_hash(zip_path)
        self.preview_label.setText(self._preview_text(export))

        slug = identity.session_slug(
            export.session.name, export.session.started_at, export.session.id
        )
        if not self.dest_new_widget.filePath():
            self.dest_new_widget.setFilePath(str(zip_path.parent / f"{slug}.gpkg"))

    @staticmethod
    def _preview_text(export) -> str:
        counts = Counter(o.geometry.type for o in export.observations)
        photo_count = sum(len(o.photos) for o in export.observations)
        audio_count = sum(1 for o in export.observations if o.audio)
        ended = export.session.ended_at.isoformat() if export.session.ended_at else "still open"
        lines = [
            f"Session: {export.session.name}",
            f"Started: {export.session.started_at.isoformat()}   Ended: {ended}",
            f"Points: {counts.get(GeometryType.POINT, 0)}   "
            f"Paths: {counts.get(GeometryType.LINE_STRING, 0)}   "
            f"Boundaries: {counts.get(GeometryType.POLYGON, 0)}",
            f"Photos: {photo_count}   Audio: {audio_count}",
        ]
        if export.revisit is not None:
            lines.append(f"Revisit of: {export.revisit.reference_session_name}")
        return "\n".join(lines)

    # -- destination / import ----------------------------------------------------

    def _current_destination(self) -> Path | None:
        use_existing = self.dest_existing_radio.isChecked()
        widget = self.dest_existing_widget if use_existing else self.dest_new_widget
        path = widget.filePath()
        return Path(path) if path else None

    def _on_accept(self):
        if self._export is None or self._zip_path is None:
            need_zip_msg = "Choose a valid Field Survey zip first."
            QMessageBox.warning(self, "Field Survey Import", need_zip_msg)
            return

        gpkg_path = self._current_destination()
        if gpkg_path is None:
            QMessageBox.warning(self, "Field Survey Import", "Choose a destination GeoPackage.")
            return

        check = duplicates.check_duplicate(gpkg_path, self._export.session.id, self._content_hash)

        if check.status is duplicates.DuplicateStatus.ALREADY_IMPORTED:
            already_imported_msg = "This exact export is already imported - nothing to do."
            QMessageBox.information(self, "Field Survey Import", already_imported_msg)
            return

        if check.status is duplicates.DuplicateStatus.CONFLICT:
            choice = ConflictDialog.ask(self, self._export.session.name, check.existing_rows)
            if choice == CANCEL:
                return
            if choice == REPLACE:
                result = duplicates.replace_import(
                    self._zip_path,
                    gpkg_path,
                    self._export.session.id,
                    on_before_delete=lambda rows: self._remove_loaded_layers(gpkg_path, rows),
                )
            elif choice == ADD_ALONGSIDE:
                result = duplicates.add_alongside_import(
                    self._zip_path, gpkg_path, self._export.session
                )
            else:
                return
        else:
            result = import_flow.import_zip(self._zip_path, gpkg_path)

        self._load_layers_into_project(result, gpkg_path)
        self.result = result
        self.result_gpkg_path = gpkg_path
        self.accept()

    # -- QgsProject wiring --------------------------------------------------------

    def _remove_loaded_layers(self, gpkg_path: Path, rows: list):
        project = QgsProject.instance()
        table_names = set()
        for row in rows:
            for key in ("points_layer", "paths_layer", "boundaries_layer"):
                if row.get(key):
                    table_names.add(row[key])

        to_remove = [
            layer.id()
            for layer in project.mapLayers().values()
            if layer.customProperty("field_survey/gpkg") == str(gpkg_path)
            and layer.customProperty("field_survey/table") in table_names
        ]
        if to_remove:
            project.removeMapLayers(to_remove)

    def _load_layers_into_project(self, result: import_flow.ImportResult, gpkg_path: Path):
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = root.insertGroup(0, result.session_name)

        for layer_info in result.layers:
            if layer_info.feature_count == 0:
                continue
            qlayer = QgsVectorLayer(
                f"{gpkg_path}|layername={layer_info.table_name}", layer_info.display_name, "ogr"
            )

            # Style BEFORE setting custom properties: loadNamedStyle() replaces the
            # layer's whole <customproperties> block with whatever the .qml carries
            # (empty, for our template-generated styles) - setting these first would
            # get silently wiped the moment the style loads.
            from ..qgis import actions, forms, styling

            styling.apply_style(qlayer, layer_info.geometry_type, revisit=result.is_revisit)
            forms.apply_map_tip(qlayer)
            forms.configure_form_widgets(qlayer, str(gpkg_path.parent))
            actions.register_audio_action(qlayer)
            if result.is_revisit and layer_info.geometry_type == GeometryType.POINT:
                actions.register_compare_action(qlayer)

            qlayer.setCustomProperty("field_survey/gpkg", str(gpkg_path))
            qlayer.setCustomProperty("field_survey/table", layer_info.table_name)
            qlayer.setCustomProperty("field_survey/import_id", result.import_id)
            qlayer.setCustomProperty("field_survey/kind", layer_info.geometry_type.value)
            project.addMapLayer(qlayer, False)
            group.addLayer(qlayer)
