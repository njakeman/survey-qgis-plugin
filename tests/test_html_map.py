"""Pure-core tests for core/html_map.py - no QGIS. The embedded JSON payload is
parsed back out and validated with json.loads, not just eyeballed.
"""
import base64
import io
import json
import re
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from field_survey_import.core import html_map, reader
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

PIL_Image = pytest.importorskip("PIL.Image")

_DUMMY_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
_PAYLOAD_RE = re.compile(r"const FEATURES = (\[.*?\]);const map", re.S)


@contextmanager
def _open(zip_bytes: bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        yield zf


def _features_from(html: str) -> list:
    match = _PAYLOAD_RE.search(html)
    assert match, "could not find the embedded FEATURES payload in the generated HTML"
    return json.loads(match.group(1))


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


def _real_jpeg(size: tuple[int, int], *, quality: int = 95) -> bytes:
    img = PIL_Image.new("RGB", size, color=(90, 140, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def test_document_has_leaflet_and_doctype():
    with _open(build_zips.build_map_point_zip()) as zf:
        export = reader.read_export(zf)
        html = html_map.build_html_document(export, zf)

    assert html.startswith("<!doctype html>")
    assert "leaflet.css" in html
    assert "leaflet.js" in html


def test_point_coordinates_are_swapped_to_lat_lng():
    with _open(build_zips.build_multi_photo_zip()) as zf:
        export = reader.read_export(zf)
        html = html_map.build_html_document(export, zf)

    features = _features_from(html)
    assert len(features) == 3
    lon, lat = export.observations[0].geometry.coordinates
    assert features[0]["geom"] == {"type": "point", "coords": [lat, lon]}


def test_multi_photo_gallery_img_count_matches_photos_in_order():
    with _open(build_zips.build_multi_photo_zip()) as zf:
        export = reader.read_export(zf)
        html = html_map.build_html_document(export, zf)

    features = _features_from(html)
    img_counts = [f["popupHtml"].count("<img") for f in features]
    assert img_counts == [2, 4, 1]


def test_zero_photo_observation_renders_no_img_tag():
    with _open(build_zips.build_map_point_zip()) as zf:
        export = reader.read_export(zf)
        html = html_map.build_html_document(export, zf)

    features = _features_from(html)
    assert "<img" not in features[0]["popupHtml"]


def test_linestring_and_polygon_geometry_swap_every_coordinate():
    with _open(build_zips.build_trace_gaps_zip()) as zf:
        export = reader.read_export(zf)
        html = html_map.build_html_document(export, zf)

    features = _features_from(html)
    line_obs = next(o for o in export.observations if o.geometry.type is GeometryType.LINE_STRING)
    poly_obs = next(o for o in export.observations if o.geometry.type is GeometryType.POLYGON)

    line_feature = next(f for f in features if f["geom"]["type"] == "line")
    expected_line = [[lat, lon] for lon, lat in line_obs.geometry.coordinates]
    assert line_feature["geom"]["coords"] == expected_line

    poly_feature = next(f for f in features if f["geom"]["type"] == "polygon")
    expected_rings = [[[lat, lon] for lon, lat in ring] for ring in poly_obs.geometry.coordinates]
    assert poly_feature["geom"]["coords"] == expected_rings


def test_special_characters_do_not_break_json_or_script():
    with _open(build_zips.build_kml_special_chars_zip()) as zf:
        export = reader.read_export(zf)
        html = html_map.build_html_document(export, zf)

    features = _features_from(html)  # json.loads must not raise - the main proof
    assert "Tricky &amp; &lt;name&gt;" in html  # HTML-escaped in <title>
    assert "]]&gt;" in features[0]["popupHtml"]  # '>' is HTML-escaped even in a popup
    # The one real risk for inline JSON-in-<script> is a literal '</script' breaking
    # out of the script element early - it must have been neutralised in the raw file.
    raw_payload_text = _PAYLOAD_RE.search(html).group(1)
    assert "</script" not in raw_payload_text.lower()


def test_geometry_to_leaflet_swaps_coordinate_order():
    geometry = Geometry(type=GeometryType.POINT, coordinates=(-0.14, 50.83))
    assert html_map.geometry_to_leaflet(geometry) == {"type": "point", "coords": [50.83, -0.14]}


def test_build_data_uris_dedupes_by_zip_entry():
    refs = [
        MediaRef(kind="photo", filename="a.jpg", zip_entry="photos/a.jpg"),
        MediaRef(kind="photo", filename="a.jpg", zip_entry="photos/a.jpg"),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("photos/a.jpg", _real_jpeg((50, 50)))
    with zipfile.ZipFile(buf) as zf:
        uris = html_map.build_data_uris(zf, refs, photo_optimization=None)
    assert len(uris) == 1
    assert uris["photos/a.jpg"].startswith("data:image/jpeg;base64,")


def test_audio_mime_type_is_detected_from_extension():
    assert html_map._audio_mime("x.webm") == "audio/webm"
    assert html_map._audio_mime("x.m4a") == "audio/mp4"
    assert html_map._audio_mime("x.unknownext") == "application/octet-stream"


def test_write_html_downscales_photos_by_default(tmp_path):
    obs = _dummy_obs(photo="big.jpg")
    export = _dummy_export([obs])
    original = _real_jpeg((2000, 1500), quality=95)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("photos/big.jpg", original)
    out_path = tmp_path / "out.html"

    with zipfile.ZipFile(buf) as zf:
        summary = html_map.write_html(export, zf, out_path)

    html = out_path.read_text(encoding="utf-8")
    features = _features_from(html)
    b64 = features[0]["popupHtml"].split("base64,", 1)[1].split('"', 1)[0]
    embedded = base64.b64decode(b64)
    assert len(embedded) < len(original)
    with PIL_Image.open(io.BytesIO(embedded)) as img:
        assert max(img.size) <= html_map.DEFAULT_PHOTO_OPTIMIZATION.max_dimension
    assert summary.photo_count == 1
    assert summary.output_size_bytes == out_path.stat().st_size


def test_write_html_no_optimization_embeds_photo_unchanged(tmp_path):
    obs = _dummy_obs(photo="big.jpg")
    export = _dummy_export([obs])
    original = _real_jpeg((2000, 1500), quality=95)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("photos/big.jpg", original)
    out_path = tmp_path / "out.html"

    with zipfile.ZipFile(buf) as zf:
        html_map.write_html(export, zf, out_path, photo_optimization=None)

    html = out_path.read_text(encoding="utf-8")
    features = _features_from(html)
    b64 = features[0]["popupHtml"].split("base64,", 1)[1].split('"', 1)[0]
    assert base64.b64decode(b64) == original


def test_write_html_raises_media_join_error_for_missing_photo(tmp_path):
    export = _dummy_export([_dummy_obs(photo="does-not-exist.jpg")])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass  # empty zip - the referenced photo genuinely isn't in it
    with zipfile.ZipFile(buf) as zf:
        with pytest.raises(MediaJoinError):
            html_map.write_html(export, zf, tmp_path / "out.html")
