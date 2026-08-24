"""Every shipped .qml loads cleanly onto a layer with the real field set, and
styling.apply_style() actually uses them (not silently falling through to the
in-code fallback) - catches qml/QGIS-version drift.
"""
from qgis.core import QgsVectorLayer

from field_survey_import.core.model import GeometryType
from field_survey_import.core.schema import ALL_FIELDS
from field_survey_import.qgis import styling
from field_survey_import.qgis.fields import build_qgs_fields

_WKB_URI = {
    GeometryType.POINT: "Point",
    GeometryType.LINE_STRING: "LineString",
    GeometryType.POLYGON: "Polygon",
}


def _template_layer(kind: GeometryType) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{_WKB_URI[kind]}?crs=EPSG:4326", "t", "memory")
    layer.dataProvider().addAttributes(build_qgs_fields(ALL_FIELDS))
    layer.updateFields()
    return layer


def test_all_three_qml_files_exist_and_apply_from_disk():
    for kind in (GeometryType.POINT, GeometryType.LINE_STRING, GeometryType.POLYGON):
        layer = _template_layer(kind)
        used_qml = styling.apply_style(layer, kind)
        assert used_qml, f"{kind} fell back to the in-code renderer - .qml missing/failed to load"
        assert layer.renderer() is not None


def test_points_qml_has_the_expected_rule_count():
    layer = _template_layer(GeometryType.POINT)
    styling.apply_style(layer, GeometryType.POINT)
    renderer = layer.renderer()
    assert len(renderer.rootRule().children()) == 4


def test_paths_and_boundaries_qml_have_the_gap_fast_path_split():
    for kind in (GeometryType.LINE_STRING, GeometryType.POLYGON):
        layer = _template_layer(kind)
        styling.apply_style(layer, kind)
        renderer = layer.renderer()
        assert len(renderer.rootRule().children()) == 2
