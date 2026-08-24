"""Exercises ImportDialog's logic (zip parsing / preview / destination / accept)
without a modal event loop - constructs the dialog, drives its widgets and private
methods directly, matching how the real dialog's OK button would behave.
"""
from field_survey_import.core.model import GeometryType
from field_survey_import.ui.import_dialog import ImportDialog


def test_choosing_a_zip_populates_preview_and_suggests_new_gpkg_path(sample_zip_path, fake_iface):
    dialog = ImportDialog(fake_iface.mainWindow())
    dialog._on_zip_changed(str(sample_zip_path))

    assert dialog._export is not None
    assert dialog._export.session.id == "01KZXJP1ZEPS7PYV04HRH6MKBH"
    assert "Points: 8" in dialog.preview_label.text()
    assert "Paths: 2" in dialog.preview_label.text()
    assert "Boundaries: 1" in dialog.preview_label.text()
    assert dialog.dest_new_widget.filePath()  # auto-suggested


def test_choosing_an_invalid_path_shows_error_not_crash(fake_iface, tmp_path):
    bogus = tmp_path / "not-a-zip.zip"
    bogus.write_bytes(b"not a zip file")
    dialog = ImportDialog(fake_iface.mainWindow())
    dialog._on_zip_changed(str(bogus))
    assert dialog._export is None
    assert "Could not read" in dialog.preview_label.text()


def test_accept_with_new_gpkg_destination_imports_and_loads_layers(
    sample_zip_path, fake_iface, tmp_path
):
    from qgis.core import QgsProject

    dialog = ImportDialog(fake_iface.mainWindow())
    dialog._on_zip_changed(str(sample_zip_path))
    gpkg_path = tmp_path / "survey.gpkg"
    dialog.dest_new_radio.setChecked(True)
    dialog.dest_new_widget.setFilePath(str(gpkg_path))

    project = QgsProject.instance()
    project.clear()
    try:
        dialog._on_accept()
        assert dialog.result is not None
        assert gpkg_path.exists()

        layer_count = sum(
            1
            for layer in project.mapLayers().values()
            if layer.customProperty("field_survey/import_id") == dialog.result.import_id
        )
        assert layer_count == 3  # Points/Paths/Boundaries all have features in sample.zip

        points_layer = next(
            layer
            for layer in project.mapLayers().values()
            if layer.customProperty("field_survey/kind") == GeometryType.POINT.value
        )
        assert points_layer.featureCount() == 8
    finally:
        project.clear()
