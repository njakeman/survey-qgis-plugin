"""Builds synthetic export zips for cases sample.zip cannot cover: survey_revisit
(all 4 station states), trace_gaps, position_source='map', .m4a audio with
audio_duration_ms, an old export missing the 4 newer keys, and a self-intersecting
polygon (handoff §8 - must import, not reject).

Each build_* function returns raw zip bytes; tests write them to tmp_path when a
real Path is needed (e.g. to open with zipfile.ZipFile).
"""
import io
import json
import zipfile

_BASE_SESSION = {
    "id": "01ABCDEFGHJKMNPQRSTVWXYZ01",
    "name": "Synthetic fixture",
    "started_at": "2026-01-15T09:00:00.000Z",
    "ended_at": "2026-01-15T10:30:00.000Z",
}

# Every documented property, so a test only needs to override what it cares about.
_FULL_PROPS_TEMPLATE = {
    "obs_id": "01FIXTUREOBS00000000000001",
    "recorded_at": "2026-01-15T09:05:00.000Z",
    "fix_at": "2026-01-15T09:04:58.000Z",
    "lat": 51.5,
    "lon": -0.1,
    "gps_accuracy_m": 5.0,
    "altitude_m": 20.0,
    "altitude_accuracy_m": 3.0,
    "heading_deg": 90.0,
    "heading_accuracy_deg": 8.0,
    "note": "",
    "photo": None,
    "audio": None,
    "audio_duration_ms": None,
    "feature_layer": None,
    "feature_id": None,
    "feature_label": None,
    "os_grid_ref": None,
    "position_source": "gps",
    "trace_length_m": None,
    "trace_gaps": None,
    "ref_obs_id": None,
    "ref_photo": None,
    "session_name": "Synthetic fixture",
    "app_version": "0.1.0",
}


def _feature(geometry: dict, **prop_overrides) -> dict:
    props = dict(_FULL_PROPS_TEMPLATE)
    props.update(prop_overrides)
    return {"type": "Feature", "geometry": geometry, "properties": props}


def _point(lon: float, lat: float) -> dict:
    return {"type": "Point", "coordinates": [lon, lat]}


def _zip_bytes(doc: dict, *, media: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("session.geojson", json.dumps(doc, sort_keys=True, indent=2) + "\n")
        for name, content in (media or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def build_revisit_zip() -> bytes:
    """A revisit session exercising all four station states (handoff §7), including
    a not_visited station - which has NO feature anywhere in this zip.
    """
    features = [
        _feature(
            _point(-0.1, 51.5),
            obs_id="01REVISITOBS0000000000001",
            note="Re-photographed the gatepost",
            photo="01REVISITPHOTO000000001.jpg",
            ref_obs_id="01REFOBS000000000000001",
            ref_photo="01REFPHOTO0000000000001.jpg",
        ),
        _feature(
            _point(-0.11, 51.51),
            obs_id="01REVISITOBS0000000000002",
            note="Bull in field, could not approach",
            ref_obs_id="01REFOBS000000000000002",
            ref_photo="01REFPHOTO0000000000002.jpg",
        ),
    ]
    doc = {
        "type": "FeatureCollection",
        "survey_session": {**_BASE_SESSION, "id": "01REVISITSESSION000000001"},
        "survey_revisit": {
            "reference_file": "spring-baseline-2026-04-12.zip",
            "reference_hash": "deadbeef" * 8,
            "reference_session_id": "01REFSESSION0000000000001",
            "reference_session_name": "Spring baseline",
            "reference_started_at": "2026-04-12T09:00:00.000Z",
            "stations": [
                {"ref_obs_id": "01REFOBS000000000000001", "state": "done", "reason": None},
                {
                    "ref_obs_id": "01REFOBS000000000000002",
                    "state": "no_access",
                    "reason": "bull in field",
                },
                {"ref_obs_id": "01REFOBS000000000000003", "state": "skipped", "reason": None},
                {"ref_obs_id": "01REFOBS000000000000004", "state": "not_visited", "reason": None},
            ],
        },
        "features": features,
    }
    media = {
        "photos/01REVISITPHOTO000000001.jpg": b"\xff\xd8\xff\xe0fake-jpeg-bytes",
    }
    return _zip_bytes(doc, media=media)


def build_revisit_reference_zip() -> bytes:
    """The baseline session build_revisit_zip() revisits: session id and the one
    station's obs_id match what build_revisit_zip()'s ref_obs_id/
    reference_session_id point at, so importing both into one GeoPackage lets
    the then-vs-now join (ref_obs_id <-> obs_id, handoff §7) resolve for real.
    """
    features = [
        _feature(
            _point(-0.1, 51.5),
            obs_id="01REFOBS000000000000001",
            note="Gatepost, spring baseline",
            photo="01REFPHOTO0000000000001.jpg",
        ),
    ]
    doc = {
        "type": "FeatureCollection",
        "survey_session": {
            "id": "01REFSESSION0000000000001",
            "name": "Spring baseline",
            "started_at": "2026-04-12T09:00:00.000Z",
            "ended_at": "2026-04-12T10:00:00.000Z",
        },
        "features": features,
    }
    media = {"photos/01REFPHOTO0000000000001.jpg": b"\xff\xd8\xff\xe0fake-reference-jpeg"}
    return _zip_bytes(doc, media=media)


def build_trace_gaps_zip() -> bytes:
    """A path and a boundary, each with trace_gaps. Path: 6 coords, gaps [2, 4]
    (segments coord1->2 and coord3->4 are inferred). Boundary: gap on the closing
    segment, to exercise the edge case explicitly.
    """
    line_coords = [
        [-0.10, 51.50], [-0.101, 51.501], [-0.102, 51.502],
        [-0.103, 51.503], [-0.104, 51.504], [-0.105, 51.505],
    ]
    ring = [
        [-0.20, 51.60], [-0.201, 51.601], [-0.202, 51.600], [-0.20, 51.599], [-0.20, 51.60],
    ]
    features = [
        _feature(
            {"type": "LineString", "coordinates": line_coords},
            obs_id="01TRACEGAPLINE00000000001",
            position_source="trace",
            heading_deg=None,
            heading_accuracy_deg=None,
            altitude_m=None,
            altitude_accuracy_m=None,
            trace_length_m=42.0,
            trace_gaps=[2, 4],
            note="Track",
        ),
        _feature(
            {"type": "Polygon", "coordinates": [ring]},
            obs_id="01TRACEGAPPOLY00000000001",
            position_source="trace",
            heading_deg=None,
            heading_accuracy_deg=None,
            altitude_m=None,
            altitude_accuracy_m=None,
            trace_length_m=88.0,
            trace_gaps=[4],  # the closing segment (coord[3] -> coord[0], 1-based index 4)
            note="Boundary",
        ),
    ]
    doc = {
        "type": "FeatureCollection",
        "survey_session": {**_BASE_SESSION, "id": "01TRACEGAPSESSION00000001"},
        "features": features,
    }
    return _zip_bytes(doc)


def build_map_point_zip() -> bytes:
    """position_source='map': altitude fields null (eyeballed, not measured)."""
    features = [
        _feature(
            _point(-0.3, 51.7),
            obs_id="01MAPPOINTOBS0000000000001",
            position_source="map",
            altitude_m=None,
            altitude_accuracy_m=None,
            gps_accuracy_m=25.0,  # map precision at pick zoom, not GPS accuracy
            note="Marked from the map - far side of the valley",
        ),
    ]
    doc = {
        "type": "FeatureCollection",
        "survey_session": {**_BASE_SESSION, "id": "01MAPPOINTSESSION0000001"},
        "features": features,
    }
    return _zip_bytes(doc)


def build_audio_variants_zip() -> bytes:
    """.m4a audio with audio_duration_ms set - the untested-by-sample container."""
    features = [
        _feature(
            _point(-0.4, 51.8),
            obs_id="01AUDIOM4AOBS0000000000001",
            audio="01AUDIOM4AFILE00000000001.m4a",
            audio_duration_ms=8400,
            note="Voice note about the hedge condition",
        ),
    ]
    doc = {
        "type": "FeatureCollection",
        "survey_session": {**_BASE_SESSION, "id": "01AUDIOM4ASESSION000001"},
        "features": features,
    }
    media = {"audio/01AUDIOM4AFILE00000000001.m4a": b"fake-m4a-bytes"}
    return _zip_bytes(doc, media=media)


def build_old_export_zip() -> bytes:
    """audio_duration_ms, trace_gaps, ref_obs_id, ref_photo, feature_layer,
    feature_id, feature_label ABSENT entirely (not null) - an export from before
    those keys existed (handoff §3: 'old exports ... may lack the newer keys').
    """
    props = dict(_FULL_PROPS_TEMPLATE)
    props["obs_id"] = "01OLDEXPORTOBS00000000001"
    for key in (
        "audio_duration_ms", "trace_gaps", "ref_obs_id", "ref_photo",
        "feature_layer", "feature_id", "feature_label",
    ):
        del props[key]

    doc = {
        "type": "FeatureCollection",
        "survey_session": {**_BASE_SESSION, "id": "01OLDEXPORTSESSION000001"},
        "features": [{"type": "Feature", "geometry": _point(-0.5, 51.9), "properties": props}],
    }
    return _zip_bytes(doc)


def build_self_intersecting_polygon_zip() -> bytes:
    """A figure-eight ring - handoff §8: 'valid data the app deliberately saves
    with a warning - import it, don't reject it.'
    """
    # A simple bowtie/figure-eight: (0,0) -> (1,1) -> (1,0) -> (0,1) -> (0,0)
    ring = [[0.0, 51.0], [0.001, 51.001], [0.001, 51.0], [0.0, 51.001], [0.0, 51.0]]
    features = [
        _feature(
            {"type": "Polygon", "coordinates": [ring]},
            obs_id="01SELFINTERSECTOBS00000001",
            position_source="trace",
            heading_deg=None,
            heading_accuracy_deg=None,
            altitude_m=None,
            altitude_accuracy_m=None,
            trace_length_m=15.0,
            note="Figure-eight walk",
        ),
    ]
    doc = {
        "type": "FeatureCollection",
        "survey_session": {**_BASE_SESSION, "id": "01SELFINTERSECTSESSION01"},
        "features": features,
    }
    return _zip_bytes(doc)
