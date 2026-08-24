"""Popup wiring (handoff §6): the map tip template evaluates cleanly against real
imported data (no stray unevaluated [% %] blocks - the case-syntax bug this
catches: QGIS's expression engine only supports searched-CASE, not switch-style
CASE x WHEN v THEN ...), and the form widgets apply without error.
"""
from qgis.core import (
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsProject,
    QgsVectorLayer,
)

from field_survey_import.qgis import forms, import_flow


def _load_points_layer(sample_zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    result = import_flow.import_zip(sample_zip_path, gpkg_path)
    table = next(wl.table_name for wl in result.layers if wl.geometry_type.value == "Point")
    layer = QgsVectorLayer(f"{gpkg_path}|layername={table}", "pts", "ogr")
    QgsProject.instance().addMapLayer(layer)
    return layer, gpkg_path


def test_map_tip_has_no_unevaluated_expression_blocks(sample_zip_path, tmp_path):
    layer, _gpkg = _load_points_layer(sample_zip_path, tmp_path)
    forms.apply_map_tip(layer)

    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))

    for feature in layer.getFeatures():
        ctx.setFeature(feature)
        rendered = QgsExpression.replaceExpressionText(layer.mapTipTemplate(), ctx)
        assert "[%" not in rendered, f"unevaluated expression block left in map tip: {rendered}"
        assert "%]" not in rendered

    QgsProject.instance().clear()


def test_map_tip_shows_photo_and_position_source_caveat_for_gps_feature(sample_zip_path, tmp_path):
    layer, gpkg_path = _load_points_layer(sample_zip_path, tmp_path)
    forms.apply_map_tip(layer)

    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    feature = next(f for f in layer.getFeatures() if f["photo_path"])
    ctx.setFeature(feature)

    rendered = QgsExpression.replaceExpressionText(layer.mapTipTemplate(), ctx)
    assert "<img src=\"file:///" in rendered
    assert "GPS accuracy" in rendered  # every media-bearing sample feature is position_source=gps
    resolved_src = rendered.split('src="')[1].split('"')[0].removeprefix("file:///")
    from pathlib import Path

    assert Path(resolved_src).exists()

    QgsProject.instance().clear()


def test_configure_form_widgets_applies_without_error(sample_zip_path, tmp_path):
    layer, gpkg_path = _load_points_layer(sample_zip_path, tmp_path)
    forms.configure_form_widgets(layer, str(gpkg_path.parent))

    photo_setup = layer.editorWidgetSetup(layer.fields().indexFromName("photo_path"))
    assert photo_setup.type() == "ExternalResource"
    assert photo_setup.config()["DefaultRoot"] == str(gpkg_path.parent)

    pos_setup = layer.editorWidgetSetup(layer.fields().indexFromName("position_source"))
    assert pos_setup.type() == "ValueMap"

    form_config = layer.editFormConfig()
    obs_id_idx = layer.fields().indexFromName("obs_id")
    assert form_config.readOnly(obs_id_idx) is True

    QgsProject.instance().clear()
