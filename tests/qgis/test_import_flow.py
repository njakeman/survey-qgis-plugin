"""Milestone 2 verification: headless import of sample.zip -> GeoPackage. 3 layers,
5 photos + 2 audio on disk, one fs_sessions row.
"""
import json

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


def test_import_multi_photo_zip_writes_every_photo(multi_photo_zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    result = import_flow.import_zip(multi_photo_zip_path, gpkg_path)

    photos = list((result.media_dir / "photos").glob("*.jpg"))
    assert len(photos) == 7

    points_table = next(wl.table_name for wl in result.layers if wl.geometry_type.value == "Point")
    qlayer = QgsVectorLayer(f"{gpkg_path}|layername={points_table}", "pts", "ogr")
    assert qlayer.isValid()

    counts = sorted(f["photo_count"] for f in qlayer.getFeatures())
    assert counts == [1, 2, 4]

    for feature in qlayer.getFeatures():
        paths = json.loads(feature["photo_paths"])
        assert len(paths) == feature["photo_count"]
        assert feature["photo_path"] == paths[0]
        for rel in paths:
            assert (gpkg_path.parent / rel).exists()

    ds = ogr.Open(str(gpkg_path))
    photos_lyr = ds.GetLayerByName("fs_photos")
    rows = sorted(
        ({name: f.GetField(name) for name in ("obs_id", "seq", "photo", "layer_table")}
         for f in photos_lyr),
        key=lambda r: (r["obs_id"], r["seq"]),
    )
    ds = None
    assert len(rows) == 7
    assert all(r["layer_table"] == points_table for r in rows)
    # seq is 0-based and contiguous per obs_id, in export order
    by_obs = {}
    for r in rows:
        by_obs.setdefault(r["obs_id"], []).append(r["seq"])
    for seqs in by_obs.values():
        assert seqs == list(range(len(seqs)))


def test_replace_against_a_geopackage_written_before_fs_photos_existed(
    sample_zip_path, tmp_path
):
    # Simulates a v0.1.0 GeoPackage: import, then drop fs_photos entirely. Pins
    # the ogr.UseExceptions() DELETE-guard fix in writer.delete_session_rows_and_layers -
    # without it, Replace raises RuntimeError instead of proceeding.
    from field_survey_import.qgis import duplicates

    gpkg_path = tmp_path / "survey.gpkg"
    first = import_flow.import_zip(sample_zip_path, gpkg_path)

    ds = ogr.Open(str(gpkg_path), update=1)
    ds.ExecuteSQL("DROP TABLE fs_photos")
    ds = None

    second = duplicates.replace_import(sample_zip_path, gpkg_path, first.session_id)
    assert second.session_id == first.session_id
    rows = writer.query_sessions_by_session_id(gpkg_path, first.session_id)
    assert len(rows) == 1
