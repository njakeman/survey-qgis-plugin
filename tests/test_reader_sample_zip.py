"""Verifies the reader against the real fixture, sample.zip (11 features: 8 Point /
2 LineString / 1 Polygon; see CLAUDE.md / the plan for the full analysis this pins).
"""
from field_survey_import.core import reader
from field_survey_import.core.model import GeometryType


def _export(sample_zip):
    return reader.read_export(sample_zip)


def test_session_metadata(sample_zip):
    export = _export(sample_zip)
    s = export.session
    assert s.id == "01KZXJP1ZEPS7PYV04HRH6MKBH"
    assert s.name == "2026-08-12"
    assert s.started_at.isoformat() == "2026-08-13T12:48:49.135000+00:00"
    assert s.ended_at is None  # verified: null even though the export post-dates the session


def test_no_revisit_in_sample(sample_zip):
    export = _export(sample_zip)
    assert export.revisit is None


def test_feature_count_and_geometry_split(sample_zip):
    export = _export(sample_zip)
    assert len(export.observations) == 11
    kinds = [o.geometry.type for o in export.observations]
    assert kinds.count(GeometryType.POINT) == 8
    assert kinds.count(GeometryType.LINE_STRING) == 2
    assert kinds.count(GeometryType.POLYGON) == 1


def test_position_source_matches_geometry(sample_zip):
    export = _export(sample_zip)
    for obs in export.observations:
        if obs.geometry.type is GeometryType.POINT:
            assert obs.position_source == "gps"
        else:
            assert obs.position_source == "trace"


def test_mixed_int_float_fields_are_always_float(sample_zip):
    # heading_accuracy_deg arrives as a bare JSON int (10) on some features and a
    # float on others - both must come out as Python float, never a mix.
    export = _export(sample_zip)
    seen_int_valued = False
    for obs in export.observations:
        if obs.heading_accuracy_deg is not None:
            assert isinstance(obs.heading_accuracy_deg, float)
            if obs.heading_accuracy_deg == 10.0:
                seen_int_valued = True
        if obs.altitude_accuracy_m is not None:
            assert isinstance(obs.altitude_accuracy_m, float)
    assert seen_int_valued, "fixture no longer exercises the mixed int/float case"


def test_note_empty_string_not_none(sample_zip):
    export = _export(sample_zip)
    notes = [obs.note for obs in export.observations]
    assert "" in notes
    assert all(n is not None for n in notes)


def test_documented_but_absent_keys_are_none(sample_zip):
    # audio_duration_ms, trace_gaps, ref_obs_id, ref_photo never appear in the raw
    # JSON at all (confirmed absent, not just null) - the reader must tolerate that.
    export = _export(sample_zip)
    for obs in export.observations:
        assert obs.audio_duration_ms is None
        assert obs.trace_gaps is None
        assert obs.ref_obs_id is None
        assert obs.ref_photo is None


def test_heading_deg_present_only_on_points(sample_zip):
    export = _export(sample_zip)
    for obs in export.observations:
        has_heading = obs.heading_deg is not None
        assert has_heading == (obs.geometry.type is GeometryType.POINT)


def test_point_geometry_matches_lat_lon(sample_zip):
    export = _export(sample_zip)
    for obs in export.observations:
        if obs.geometry.type is GeometryType.POINT:
            lon, lat = obs.geometry.coordinates
            assert lon == obs.lon
            assert lat == obs.lat


def test_polygon_is_single_ring_and_closed(sample_zip):
    export = _export(sample_zip)
    polygons = [o for o in export.observations if o.geometry.type is GeometryType.POLYGON]
    assert len(polygons) == 1
    rings = polygons[0].geometry.coordinates
    assert len(rings) == 1
    ring = rings[0]
    assert len(ring) == 30
    assert ring[0] == ring[-1]


def test_no_extra_undocumented_properties(sample_zip):
    export = _export(sample_zip)
    for obs in export.observations:
        assert obs.extra_properties == {}
