"""Exercises everything sample.zip cannot: survey_revisit (all 4 station states),
trace_gaps, position_source='map', .m4a audio, an old export with absent (not null)
keys, and a self-intersecting polygon (handoff §8 - must import, not reject).
"""
import io
import zipfile

import pytest

from field_survey_import.core import media, reader
from field_survey_import.core.errors import SurveyFormatError
from field_survey_import.core.model import GeometryType
from tests.fixtures import build_zips


def _open(zip_bytes: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(zip_bytes))


def test_revisit_all_four_station_states():
    with _open(build_zips.build_revisit_zip()) as zf:
        export = reader.read_export(zf)

    assert export.revisit is not None
    assert export.revisit.reference_session_id == "01REFSESSION0000000000001"
    states = {s.ref_obs_id: s for s in export.revisit.stations}
    assert states["01REFOBS000000000000001"].state == "done"
    assert states["01REFOBS000000000000002"].state == "no_access"
    assert states["01REFOBS000000000000002"].reason == "bull in field"
    assert states["01REFOBS000000000000003"].state == "skipped"
    assert states["01REFOBS000000000000004"].state == "not_visited"
    # not_visited has no corresponding feature in this zip at all (handoff §7).
    obs_ids_with_features = {o.ref_obs_id for o in export.observations}
    assert "01REFOBS000000000000004" not in obs_ids_with_features


def test_revisit_ref_photo_points_into_reference_zip_not_this_one():
    zip_bytes = build_zips.build_revisit_zip()
    with _open(zip_bytes) as zf:
        export = reader.read_export(zf)
        obs = next(o for o in export.observations if o.obs_id == "01REVISITOBS0000000000001")
        assert obs.ref_photo == "01REFPHOTO0000000000001.jpg"
        # That filename is NOT resolvable in this zip - it lives in the reference zip.
        assert "photos/01REFPHOTO0000000000001.jpg" not in zf.namelist()


def test_trace_gaps_parsed_as_int_tuples():
    with _open(build_zips.build_trace_gaps_zip()) as zf:
        export = reader.read_export(zf)

    line = next(o for o in export.observations if o.geometry.type is GeometryType.LINE_STRING)
    assert line.trace_gaps == (2, 4)

    polygon = next(o for o in export.observations if o.geometry.type is GeometryType.POLYGON)
    assert polygon.trace_gaps == (4,)  # the closing segment


def test_map_position_source_has_null_altitude():
    with _open(build_zips.build_map_point_zip()) as zf:
        export = reader.read_export(zf)

    obs = export.observations[0]
    assert obs.position_source == "map"
    assert obs.altitude_m is None
    assert obs.altitude_accuracy_m is None
    assert obs.gps_accuracy_m == 25.0  # map precision, not GPS accuracy


def test_m4a_audio_with_duration():
    zip_bytes = build_zips.build_audio_variants_zip()
    with _open(zip_bytes) as zf:
        export = reader.read_export(zf)
        obs = export.observations[0]
        assert obs.audio == "01AUDIOM4AFILE00000000001.m4a"
        assert obs.audio_duration_ms == 8400

        _, audio_ref = media.resolve_media(obs, zf)
        assert audio_ref is not None
        assert audio_ref.zip_entry == "audio/01AUDIOM4AFILE00000000001.m4a"


def test_old_export_missing_keys_become_none_not_errors():
    with _open(build_zips.build_old_export_zip()) as zf:
        export = reader.read_export(zf)

    obs = export.observations[0]
    assert obs.audio_duration_ms is None
    assert obs.trace_gaps is None
    assert obs.ref_obs_id is None
    assert obs.ref_photo is None
    assert obs.feature_layer is None
    assert obs.feature_id is None
    assert obs.feature_label is None


def test_self_intersecting_polygon_imports_without_error():
    with _open(build_zips.build_self_intersecting_polygon_zip()) as zf:
        export = reader.read_export(zf)

    assert len(export.observations) == 1
    obs = export.observations[0]
    assert obs.geometry.type is GeometryType.POLYGON
    ring = obs.geometry.coordinates[0]
    assert ring[0] == ring[-1]
    assert len(ring) == 5


def test_feature_layer_and_feature_id_must_be_both_or_neither():
    # A deliberately malformed feature: feature_layer set, feature_id null.
    doc_bytes = build_zips.build_map_point_zip()
    with _open(doc_bytes) as zf:
        raw = zf.read("session.geojson")
    import json

    doc = json.loads(raw)
    doc["features"][0]["properties"]["feature_layer"] = "some-layer"
    # feature_id stays None -> violates the "both or neither" rule (handoff §3).
    broken = json.dumps(doc).encode()

    with pytest.raises(SurveyFormatError, match="feature_layer/feature_id"):
        reader.parse_session_geojson(broken)
