"""Milestone verification (handoff §7): import a reference session + its
revisit into one GeoPackage, confirm station-state styling, the ref_obs_id <->
obs_id join resolving a real photo, and that plain import of the revisit ALONE
(no reference present) degrades gracefully rather than depending on it.
"""
import zipfile
from pathlib import Path

from qgis.core import QgsVectorLayer

from field_survey_import.qgis import import_flow, revisit
from tests.fixtures import build_zips


def _write_zip(zip_bytes: bytes, tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(zip_bytes)
    return path


def test_revisit_import_populates_side_tables(tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    revisit_zip = _write_zip(build_zips.build_revisit_zip(), tmp_path, "revisit.zip")

    result = import_flow.import_zip(revisit_zip, gpkg_path)
    assert result.is_revisit is True

    from osgeo import ogr

    ds = ogr.Open(str(gpkg_path))
    stations_lyr = ds.GetLayerByName("fs_revisit_stations")
    states = sorted(f.GetField("state") for f in stations_lyr)
    assert states == ["done", "no_access", "not_visited", "skipped"]
    ds = None


def test_then_vs_now_resolves_a_real_photo_when_reference_is_present(tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    reference_zip = _write_zip(build_zips.build_revisit_reference_zip(), tmp_path, "reference.zip")
    revisit_zip = _write_zip(build_zips.build_revisit_zip(), tmp_path, "revisit.zip")

    import_flow.import_zip(reference_zip, gpkg_path)
    import_flow.import_zip(revisit_zip, gpkg_path)

    ref = revisit.resolve_reference_photo(
        gpkg_path, "01REFSESSION0000000000001", "01REFOBS000000000000001"
    )
    assert ref.not_found_reason is None
    assert ref.reference_session_name == "Spring baseline"
    assert ref.path is not None
    assert ref.path.exists()


def test_then_vs_now_degrades_gracefully_without_reference(tmp_path):
    # Plain import must not depend on the reference being present (§7's hard
    # requirement) - importing the revisit ALONE must still fully succeed, and
    # comparison must explain what's missing rather than erroring.
    gpkg_path = tmp_path / "survey.gpkg"
    revisit_zip = _write_zip(build_zips.build_revisit_zip(), tmp_path, "revisit.zip")

    result = import_flow.import_zip(revisit_zip, gpkg_path)
    assert result.is_revisit is True
    counts = {layer.geometry_type.value: layer.feature_count for layer in result.layers}
    assert counts["Point"] == 2  # the import itself is unaffected

    ref = revisit.resolve_reference_photo(
        gpkg_path, "01REFSESSION0000000000001", "01REFOBS000000000000001"
    )
    assert ref.path is None
    assert "hasn't been imported" in ref.not_found_reason


def test_station_with_no_reason_and_reference_photo_never_resolvable_here(tmp_path):
    # ref_photo names a file inside the REFERENCE zip, which this zip never
    # contains (handoff §7) - the revisit zip's own media must not claim it.
    gpkg_path = tmp_path / "survey.gpkg"
    revisit_zip = _write_zip(build_zips.build_revisit_zip(), tmp_path, "revisit.zip")
    with zipfile.ZipFile(revisit_zip) as zf:
        names = zf.namelist()
    assert "photos/01REFPHOTO0000000000001.jpg" not in names

    import_flow.import_zip(revisit_zip, gpkg_path)  # must succeed regardless


def test_per_photo_ref_photo_resolves_distinct_reference_photos(tmp_path):
    # build_multi_photo_revisit_zip(): 4 photos on one observation - two pair
    # with specific reference photos by filename, one names a reference photo
    # that doesn't exist, and one has a null ref_photo (falls back to the
    # ref_obs_id<->obs_id join, consuming the reference's remaining unpaired
    # photo - proving exact matches are never stolen by the fallback).
    gpkg_path = tmp_path / "survey.gpkg"
    reference_zip = _write_zip(
        build_zips.build_multi_photo_reference_zip(), tmp_path, "reference.zip"
    )
    revisit_zip = _write_zip(build_zips.build_multi_photo_revisit_zip(), tmp_path, "revisit.zip")
    import_flow.import_zip(reference_zip, gpkg_path)
    import_flow.import_zip(revisit_zip, gpkg_path)

    now_photos = (
        ("n0.jpg", "01MPREFPHOTO000000000001.jpg"),
        ("n1.jpg", "01MPREFPHOTO000000000002.jpg"),
        ("n2.jpg", "01MPREFPHOTO000000009999.jpg"),  # doesn't exist in the reference
        ("n3.jpg", None),  # falls back to the obs-level join
    )
    comparison = revisit.resolve_reference_photos(
        gpkg_path,
        reference_session_id="01MPREFSESSION0000000001",
        ref_obs_id="01MPREFOBS0000000000001",
        now_photos=now_photos,
    )
    assert comparison.not_found_reason is None
    pairs = comparison.pairs
    assert pairs[0].then_filename == "01MPREFPHOTO000000000001.jpg"
    assert pairs[0].then_path.exists()
    assert pairs[1].then_filename == "01MPREFPHOTO000000000002.jpg"
    assert pairs[1].then_path.exists()
    assert pairs[2].then_path is None
    assert "doesn't contain a photo named" in pairs[2].not_found_reason
    # the null-ref_photo photo must NOT be paired with an already-exact-matched
    # reference photo - it gets the reference's third, still-unconsumed one.
    assert pairs[3].then_filename == "01MPREFPHOTO000000000003.jpg"
    assert pairs[3].then_path.exists()


def test_per_photo_ref_photo_degrades_gracefully_without_reference(tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    revisit_zip = _write_zip(build_zips.build_multi_photo_revisit_zip(), tmp_path, "revisit.zip")
    import_flow.import_zip(revisit_zip, gpkg_path)

    now_photos = (("n0.jpg", "01MPREFPHOTO000000000001.jpg"), ("n1.jpg", None))
    comparison = revisit.resolve_reference_photos(
        gpkg_path,
        reference_session_id="01MPREFSESSION0000000001",
        ref_obs_id="01MPREFOBS0000000000001",
        now_photos=now_photos,
    )
    assert "hasn't been imported" in comparison.not_found_reason
    assert len(comparison.pairs) == 2  # the "now" side still renders
    assert all(p.then_path is None for p in comparison.pairs)


def test_per_photo_ref_photo_falls_back_when_reference_has_no_fs_photos_rows(tmp_path):
    # Simulates a reference session imported before fs_photos existed.
    from osgeo import ogr

    gpkg_path = tmp_path / "survey.gpkg"
    reference_zip = _write_zip(build_zips.build_revisit_reference_zip(), tmp_path, "reference.zip")
    revisit_zip = _write_zip(build_zips.build_revisit_zip(), tmp_path, "revisit.zip")
    import_flow.import_zip(reference_zip, gpkg_path)
    import_flow.import_zip(revisit_zip, gpkg_path)

    ds = ogr.Open(str(gpkg_path), update=1)
    ds.ExecuteSQL("DELETE FROM fs_photos WHERE session_id = '01REFSESSION0000000000001'")
    ds = None

    now_photos = (("n0.jpg", "01REFPHOTO0000000000001.jpg"),)
    comparison = revisit.resolve_reference_photos(
        gpkg_path,
        reference_session_id="01REFSESSION0000000000001",
        ref_obs_id="01REFOBS000000000000001",
        now_photos=now_photos,
    )
    assert comparison.not_found_reason is None
    assert comparison.pairs[0].then_path.exists()


def test_revisit_points_layer_gets_compare_action_when_loaded(tmp_path):
    # Exercises the same layer-loading path import_dialog.py uses, without a
    # QDialog event loop - directly calling the pieces it wires together.
    from field_survey_import.qgis import actions, styling

    gpkg_path = tmp_path / "survey.gpkg"
    revisit_zip = _write_zip(build_zips.build_revisit_zip(), tmp_path, "revisit.zip")
    result = import_flow.import_zip(revisit_zip, gpkg_path)

    points_table = next(wl.table_name for wl in result.layers if wl.geometry_type.value == "Point")
    layer = QgsVectorLayer(f"{gpkg_path}|layername={points_table}", "pts", "ogr")
    styling.apply_style(layer, result.layers[0].geometry_type, revisit=True)
    actions.register_audio_action(layer)
    actions.register_compare_action(layer)

    action_names = {a.name() for a in layer.actions().actions()}
    assert actions.COMPARE_ACTION_NAME in action_names
