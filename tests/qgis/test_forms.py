"""Popup wiring (handoff §6): the map tip template evaluates cleanly against real
imported data (no stray unevaluated [% %] blocks - the case-syntax bug this
catches: QGIS's expression engine only supports searched-CASE, not switch-style
CASE x WHEN v THEN ...), and the form widgets apply without error.
"""
import re
from pathlib import Path

from qgis.core import (
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)

from field_survey_import.core.schema import ALL_FIELDS
from field_survey_import.qgis import forms, import_flow
from field_survey_import.qgis.fields import build_qgs_fields


def _load_points_layer(zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    result = import_flow.import_zip(zip_path, gpkg_path)
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


def test_map_tip_renders_one_img_per_photo_on_multi_photo_feature(multi_photo_zip_path, tmp_path):
    layer, gpkg_path = _load_points_layer(multi_photo_zip_path, tmp_path)
    forms.apply_map_tip(layer)

    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))

    four_photo_feature = next(f for f in layer.getFeatures() if f["photo_count"] == 4)
    ctx.setFeature(four_photo_feature)
    rendered = QgsExpression.replaceExpressionText(layer.mapTipTemplate(), ctx)
    assert "[%" not in rendered
    assert rendered.count('<img src="file:///') == 4
    assert "4 photos" in rendered
    for src in _srcs(rendered):
        assert Path(src).exists()

    QgsProject.instance().clear()


def test_map_tip_single_photo_has_no_count_header(multi_photo_zip_path, tmp_path):
    layer, _gpkg = _load_points_layer(multi_photo_zip_path, tmp_path)
    forms.apply_map_tip(layer)

    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    one_photo_feature = next(f for f in layer.getFeatures() if f["photo_count"] == 1)
    ctx.setFeature(one_photo_feature)
    rendered = QgsExpression.replaceExpressionText(layer.mapTipTemplate(), ctx)
    assert rendered.count('<img src="file:///') == 1
    assert "photos</div>" not in rendered

    QgsProject.instance().clear()


def test_map_tip_falls_back_when_photo_paths_column_is_absent(tmp_path):
    # Pins the eval-error-leaves-block-verbatim trap: a layer built from the
    # field set BEFORE photo_paths/photo_count/photos existed must still render
    # exactly the single photo_path image, with no stray [%. Written to a real
    # GeoPackage on disk (not a memory layer) - layer_property(@layer,'path'),
    # which the gallery expression depends on, only resolves for file-backed
    # layers, exactly like every layer the plugin actually loads.
    from qgis.core import (
        QgsCoordinateTransformContext,
        QgsVectorFileWriter,
    )

    old_fields = tuple(
        fd for fd in ALL_FIELDS if fd.name not in ("photos", "photo_paths", "photo_count")
    )
    mem_layer = QgsVectorLayer("Point?crs=EPSG:4326", "old-shape", "memory")
    mem_layer.dataProvider().addAttributes(build_qgs_fields(old_fields))
    mem_layer.updateFields()
    feat = QgsFeature(mem_layer.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(0.0, 0.0)))
    feat.setAttribute("photo_path", "a.jpg")
    mem_layer.dataProvider().addFeature(feat)

    (tmp_path / "a.jpg").write_bytes(b"fake-jpeg")
    gpkg_path = tmp_path / "old.gpkg"
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = "old_points"
    err, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
        mem_layer, str(gpkg_path), QgsCoordinateTransformContext(), opts
    )
    assert err == QgsVectorFileWriter.NoError

    layer = QgsVectorLayer(f"{gpkg_path}|layername=old_points", "old", "ogr")
    assert layer.isValid()

    forms.apply_map_tip(layer)
    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    ctx.setFeature(next(layer.getFeatures()))
    rendered = QgsExpression.replaceExpressionText(layer.mapTipTemplate(), ctx)
    assert "[%" not in rendered
    assert rendered.count("<img") == 1
    (src,) = _srcs(rendered)
    assert Path(src).exists()


def test_map_tip_renders_no_gallery_for_a_photoless_feature():
    from field_survey_import.core.schema import ALL_FIELDS

    layer = QgsVectorLayer("Point?crs=EPSG:4326", "no-photos", "memory")
    layer.dataProvider().addAttributes(build_qgs_fields(ALL_FIELDS))
    layer.updateFields()
    feat = QgsFeature(layer.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(0.0, 0.0)))
    layer.dataProvider().addFeature(feat)
    layer.updateExtents()

    forms.apply_map_tip(layer)
    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    ctx.setFeature(next(layer.getFeatures()))
    rendered = QgsExpression.replaceExpressionText(layer.mapTipTemplate(), ctx)
    assert "[%" not in rendered
    assert "<img" not in rendered
    assert "photos</div>" not in rendered


def _srcs(rendered: str) -> list:
    return re.findall(r'src="file:///([^"]+)"', rendered)
