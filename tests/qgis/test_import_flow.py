"""Milestone 2 verification: headless import of sample.zip -> GeoPackage. 3 layers,
5 photos + 2 audio on disk, one fs_sessions row.
"""
from osgeo import ogr
from qgis.core import QgsVectorLayer

from field_survey_import.qgis import import_flow, writer


def test_import_sample_zip_end_to_end(sample_zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    result = import_flow.import_zip(sample_zip_path, gpkg_path)

    assert gpkg_path.exists()
    assert result.session_id == "01KZXJP1ZEPS7PYV04HRH6MKBH"
    assert result.is_revisit is False

    counts = {layer.geometry_type.value: layer.feature_count for layer in result.layers}
    assert counts["Point"] == 8
    assert counts["LineString"] == 2
    assert counts["Polygon"] == 1

    # media on disk
    photos = list((result.media_dir / "photos").glob("*.jpg"))
    audio = list((result.media_dir / "audio").glob("*.webm"))
    assert len(photos) == 5
    assert len(audio) == 2
    for f in photos + audio:
        assert f.stat().st_size > 0

    # layers load and have the expected field set / types
    points_layer_name = next(
        wl.table_name for wl in result.layers if wl.geometry_type.value == "Point"
    )
    qlayer = QgsVectorLayer(f"{gpkg_path}|layername={points_layer_name}", "pts", "ogr")
    assert qlayer.isValid()
    assert qlayer.featureCount() == 8
    field_names = {f.name() for f in qlayer.fields()}
    assert "heading_deg" in field_names
    assert "session_id" in field_names
    assert "photo_path" in field_names

    heading_field = qlayer.fields().field("heading_deg")
    assert heading_field.isNumeric()
    alt_acc_field = qlayer.fields().field("altitude_accuracy_m")
    assert alt_acc_field.typeName() in ("Real", "double")  # must be Double, never Integer

    # a feature with a photo has a resolvable relative path
    feature_with_photo = next(f for f in qlayer.getFeatures() if f["photo_path"])
    resolved = gpkg_path.parent / feature_with_photo["photo_path"]
    assert resolved.exists()

    # fs_sessions has exactly one row for this import
    ds = ogr.Open(str(gpkg_path))
    sessions_lyr = ds.GetLayerByName("fs_sessions")
    assert sessions_lyr.GetFeatureCount() == 1
    row = next(iter(sessions_lyr))
    assert row["session_id"] == "01KZXJP1ZEPS7PYV04HRH6MKBH"
    assert row["point_count"] == 8
    assert row["path_count"] == 2
    assert row["boundary_count"] == 1
    ds = None


def test_query_sessions_by_session_id(sample_zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    result = import_flow.import_zip(sample_zip_path, gpkg_path)

    rows = writer.query_sessions_by_session_id(gpkg_path, result.session_id)
    assert len(rows) == 1
    assert rows[0]["import_id"] == result.import_id
    assert rows[0]["source_zip_sha256"] is not None

    assert writer.query_sessions_by_session_id(gpkg_path, "NONEXISTENT") == []
