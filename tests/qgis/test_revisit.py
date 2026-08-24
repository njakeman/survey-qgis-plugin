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
