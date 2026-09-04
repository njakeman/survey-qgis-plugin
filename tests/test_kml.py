"""Pure-core tests for core/kml.py - no QGIS. Every generated KML is parsed back with
xml.etree.ElementTree to prove well-formedness, not just eyeballed.
"""
import io
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from field_survey_import.core import kml, reader
from field_survey_import.core.errors import MediaJoinError
from field_survey_import.core.media import MediaRef
from field_survey_import.core.model import (
    Geometry,
    GeometryType,
    Observation,
    SurveyExport,
    SurveySession,
)
from tests.fixtures import build_zips

NS = {"k": kml.KML_NAMESPACE}
_DUMMY_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


@contextmanager
def _open(zip_bytes: bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        yield zf


def _dummy_obs(*, obs_id="OBS1", photo=None, note="", os_grid_ref=None, audio=None):
    from field_survey_import.core.model import PhotoRef

    photos = (PhotoRef(photo=photo, ref_photo=None),) if photo is not None else ()
    return Observation(
        geometry=Geometry(type=GeometryType.POINT, coordinates=(0.0, 0.0)),
        obs_id=obs_id,
        recorded_at=_DUMMY_TIME,
        fix_at=_DUMMY_TIME,
        lat=0.0,
        lon=0.0,
        gps_accuracy_m=1.0,
        altitude_m=None,
        altitude_accuracy_m=None,
        heading_deg=None,
        heading_accuracy_deg=None,
        note=note,
        photo=photo,
        photos=photos,
        audio=audio,
        audio_duration_ms=None,
        feature_layer=None,
        feature_id=None,
        feature_label=None,
        os_grid_ref=os_grid_ref,
        position_source="gps",
        trace_length_m=None,
        trace_gaps=None,
        ref_obs_id=None,
        ref_photo=None,
        session_name="s",
        app_version="0.1.0",
    )


def _dummy_export(observations, *, name="Dummy session"):
    session = SurveySession(id="SESSION1", name=name, started_at=_DUMMY_TIME, ended_at=None)
    return SurveyExport(session=session, revisit=None, observations=tuple(observations))


def test_point_only_export_placemark_count_and_coordinates():
    with _open(build_zips.build_multi_photo_zip()) as zf:
        export = reader.read_export(zf)
        kml_xml, _manifest = kml.build_kml_document(export, zf)

    root = ET.fromstring(kml_xml)
    folders = root.findall(".//k:Folder", NS)
    assert len(folders) == 1
    assert folders[0].find("k:name", NS).text == "Points"

    placemarks = root.findall(".//k:Placemark", NS)
    assert len(placemarks) == 3
    coords = placemarks[0].find(".//k:Point/k:coordinates", NS).text
    assert coords == "-0.140000,50.830000"  # (lon, lat), 6dp - a swapped order fails here


def test_multi_photo_gallery_img_count_matches_photos_in_order():
    with _open(build_zips.build_multi_photo_zip()) as zf:
        export = reader.read_export(zf)
        kml_xml, _manifest = kml.build_kml_document(export, zf)

    root = ET.fromstring(kml_xml)
    placemarks = root.findall(".//k:Folder[k:name='Points']/k:Placemark", NS)
    img_counts = [p.find("k:description", NS).text.count("<img") for p in placemarks]
    assert img_counts == [2, 4, 1]


def test_zero_photo_observation_renders_no_img_tag():
    with _open(build_zips.build_map_point_zip()) as zf:
        export = reader.read_export(zf)
        kml_xml, _manifest = kml.build_kml_document(export, zf)

    root = ET.fromstring(kml_xml)
    description = root.find(".//k:Placemark/k:description", NS).text
    assert "<img" not in description


def test_linestring_geometry_serializes_in_order_no_swap():
    with _open(build_zips.build_trace_gaps_zip()) as zf:
        export = reader.read_export(zf)
        kml_xml, _manifest = kml.build_kml_document(export, zf)

    root = ET.fromstring(kml_xml)
    line_obs = next(o for o in export.observations if o.geometry.type is GeometryType.LINE_STRING)
    coords_text = root.find(".//k:Folder[k:name='Paths']//k:LineString/k:coordinates", NS).text
    pairs = coords_text.split(" ")
    assert len(pairs) == len(line_obs.geometry.coordinates)
    for pair, (lon, lat) in zip(pairs, line_obs.geometry.coordinates):
        assert pair == f"{lon:.6f},{lat:.6f}"


def test_polygon_single_ring_has_only_outer_boundary():
    with _open(build_zips.build_trace_gaps_zip()) as zf:
        export = reader.read_export(zf)
        kml_xml, _manifest = kml.build_kml_document(export, zf)

    root = ET.fromstring(kml_xml)
    polygon = root.find(".//k:Folder[k:name='Boundaries']//k:Polygon", NS)
    assert polygon.find("k:outerBoundaryIs", NS) is not None
    assert polygon.find("k:innerBoundaryIs", NS) is None


def test_polygon_with_multiple_rings_keeps_all_of_them():
    outer_ring = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0))
    inner_ring = ((0.2, 0.2), (0.4, 0.2), (0.4, 0.4), (0.2, 0.2))
    geometry = Geometry(type=GeometryType.POLYGON, coordinates=(outer_ring, inner_ring))

    polygon_xml = kml.geometry_to_kml(geometry)
    root = ET.fromstring(f'<root xmlns="{kml.KML_NAMESPACE}">{polygon_xml}</root>')
    assert len(root.findall(".//k:outerBoundaryIs", NS)) == 1
    assert len(root.findall(".//k:innerBoundaryIs", NS)) == 1


def test_special_characters_do_not_corrupt_xml():
    with _open(build_zips.build_kml_special_chars_zip()) as zf:
        export = reader.read_export(zf)
        kml_xml, _manifest = kml.build_kml_document(export, zf)

    root = ET.fromstring(kml_xml)  # must not raise
    assert root.find("k:Document/k:name", NS).text == "Tricky & <name>"

    description = root.find(".//k:Placemark/k:description", NS).text
    assert "&amp;" in description
    assert "]]&gt;" in description
    assert "]]>" not in description  # the CDATA-terminator guard actually worked


@pytest.mark.parametrize(
    ("note", "os_grid_ref", "expected_name"),
    [
        ("", "TQ 123 456", "TQ 123 456"),
        ("", None, "01FALLBACKOBS0000000001"),
        ("A real note", "TQ 123 456", "A real note"),
    ],
)
def test_placemark_name_fallback_chain(note, os_grid_ref, expected_name):
    obs = _dummy_obs(obs_id="01FALLBACKOBS0000000001", note=note, os_grid_ref=os_grid_ref)
    assert kml._placemark_name(obs) == expected_name


def test_media_manifest_dedupes_by_zip_entry():
    refs = [
        MediaRef(kind="photo", filename="a.jpg", zip_entry="photos/a.jpg"),
        MediaRef(kind="photo", filename="a.jpg", zip_entry="photos/a.jpg"),
    ]
    manifest = kml.build_media_manifest(refs)
    assert manifest == {"photos/a.jpg": "files/a.jpg"}


def test_media_manifest_disambiguates_filename_collision_across_kinds():
    refs = [
        MediaRef(kind="photo", filename="x.jpg", zip_entry="photos/x.jpg"),
        MediaRef(kind="audio", filename="x.jpg", zip_entry="audio/x.jpg"),
    ]
    manifest = kml.build_media_manifest(refs)
    assert len(set(manifest.values())) == 2
    assert manifest["photos/x.jpg"] == "files/x.jpg"
    assert manifest["audio/x.jpg"] == "files/audio/x.jpg"


def test_write_kmz_raises_media_join_error_for_missing_photo(tmp_path):
    export = _dummy_export([_dummy_obs(photo="does-not-exist.jpg")])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass  # empty zip - the referenced photo genuinely isn't in it
    with zipfile.ZipFile(buf) as zf:
        with pytest.raises(MediaJoinError):
            kml.write_kmz(export, zf, tmp_path / "out.kmz")


def test_kmz_round_trip_end_to_end(tmp_path):
    zip_bytes = build_zips.build_multi_photo_zip()
    out_path = tmp_path / "out.kmz"
    with _open(zip_bytes) as zf:
        export = reader.read_export(zf)
        summary = kml.write_kmz(export, zf, out_path)

    with zipfile.ZipFile(out_path) as out:
        names = set(out.namelist())
        assert "doc.kml" in names
        file_entries = {n for n in names if n != "doc.kml"}
        assert len(file_entries) == 7  # 2 + 4 + 1 unique photos, no audio
        assert all(n.startswith("files/") for n in file_entries)

        root = ET.fromstring(out.read("doc.kml"))
        assert root.tag == f"{{{kml.KML_NAMESPACE}}}kml"

    assert summary.photo_count == 7
    assert summary.audio_count == 0
    assert summary.observation_counts[GeometryType.POINT] == 3
