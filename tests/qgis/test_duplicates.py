"""Milestone 3 verification (duplicate detection): identical hash -> already
imported; same session_id different hash -> Replace/Add alongside both behave;
no orphan tables/media afterwards.
"""
import io
import json
import zipfile

from osgeo import ogr
from qgis.core import QgsVectorLayer

from field_survey_import.core import reader
from field_survey_import.qgis import duplicates, import_flow, writer


def _mutate_zip(src_zip_path, tmp_path, note_suffix="-edited"):
    """A 'changed' re-export: same session_id, different content hash, one extra
    feature so counts visibly differ.
    """
    with zipfile.ZipFile(src_zip_path) as zf:
        doc = json.loads(zf.read("session.geojson"))
        members = {n: zf.read(n) for n in zf.namelist() if n != "session.geojson"}

    doc["features"][0]["properties"]["note"] += note_suffix

    out_bytes = io.BytesIO()
    with zipfile.ZipFile(out_bytes, "w") as out:
        out.writestr("session.geojson", json.dumps(doc, sort_keys=True, indent=2) + "\n")
        for name, content in members.items():
            out.writestr(name, content)

    out_path = tmp_path / "mutated.zip"
    out_path.write_bytes(out_bytes.getvalue())
    return out_path


def test_already_imported_detected_by_identical_hash(sample_zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    from field_survey_import.core import identity

    result = import_flow.import_zip(sample_zip_path, gpkg_path)
    content_hash = identity.content_hash(sample_zip_path)

    check = duplicates.check_duplicate(gpkg_path, result.session_id, content_hash)
    assert check.status is duplicates.DuplicateStatus.ALREADY_IMPORTED
    assert len(check.existing_rows) == 1


def test_conflict_detected_on_changed_reexport(sample_zip_path, tmp_path):
    from field_survey_import.core import identity

    gpkg_path = tmp_path / "survey.gpkg"
    result = import_flow.import_zip(sample_zip_path, gpkg_path)

    mutated = _mutate_zip(sample_zip_path, tmp_path)
    mutated_hash = identity.content_hash(mutated)

    check = duplicates.check_duplicate(gpkg_path, result.session_id, mutated_hash)
    assert check.status is duplicates.DuplicateStatus.CONFLICT
    assert len(check.existing_rows) == 1


def test_replace_leaves_exactly_one_import(sample_zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    first = import_flow.import_zip(sample_zip_path, gpkg_path)
    mutated = _mutate_zip(sample_zip_path, tmp_path)

    second = duplicates.replace_import(mutated, gpkg_path, first.session_id)

    assert second.slug == first.slug  # deterministic - Replace reuses the same names
    rows = writer.query_sessions_by_session_id(gpkg_path, first.session_id)
    assert len(rows) == 1
    assert rows[0]["import_id"] == second.import_id

    ds = ogr.Open(str(gpkg_path))
    layer_names = {ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())}
    ds = None
    # exactly the 3 geometry layers + 3 side tables, no orphans from the first import
    side_tables = {"fs_sessions", "fs_revisits", "fs_revisit_stations", "fs_photos"}
    expected = {wl.table_name for wl in second.layers} | side_tables
    assert layer_names == expected

    # old media dir contents replaced (same dir name, since slug is unchanged)
    assert first.media_dir == second.media_dir
    photos = list((second.media_dir / "photos").glob("*.jpg"))
    assert len(photos) == 5


def test_add_alongside_keeps_both_imports(sample_zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    first = import_flow.import_zip(sample_zip_path, gpkg_path)
    mutated = _mutate_zip(sample_zip_path, tmp_path)

    with zipfile.ZipFile(mutated) as zf:
        export = reader.read_export(zf)

    second = duplicates.add_alongside_import(mutated, gpkg_path, export.session)

    assert second.slug != first.slug
    rows = writer.query_sessions_by_session_id(gpkg_path, first.session_id)
    assert len(rows) == 2
    import_ids = {r["import_id"] for r in rows}
    assert import_ids == {first.import_id, second.import_id}

    ds = ogr.Open(str(gpkg_path))
    layer_names = {ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())}
    ds = None
    for layer in first.layers + second.layers:
        assert layer.table_name in layer_names

    # both layer sets still independently loadable
    for result in (first, second):
        points_table = next(
            wl.table_name for wl in result.layers if wl.geometry_type.value == "Point"
        )
        qlayer = QgsVectorLayer(f"{gpkg_path}|layername={points_table}", "pts", "ogr")
        assert qlayer.isValid()
        assert qlayer.featureCount() == 8

    assert first.media_dir != second.media_dir
    assert first.media_dir.exists()
    assert second.media_dir.exists()
