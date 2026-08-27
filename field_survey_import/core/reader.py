"""zip -> SurveyExport. The only place session.geojson gets parsed.

Handoff gotchas enforced here (see errors.py for the exceptions raised):
- §8: zips use data descriptors (client-zip) - `zipfile` handles this via the central
  directory; we only ever call zf.read()/zf.namelist(), never touch local headers.
- §3/§8: a property key can be MISSING (old export) or explicitly null - both are
  tolerated and normalised to None for nullable fields.
- The mixed int/float trap (altitude_accuracy_m, heading_accuracy_deg): every DOUBLE
  field is coerced through float(), so an integral JSON value never produces a
  different Python type than a fractional one.
- §8: polygons are accepted as-is, including self-intersecting rings - no geometry
  validity checking happens here at all.
- geometry type (not `position_source`) is what a caller should split layers on;
  this module doesn't enforce a relationship between the two, only that
  `position_source` is one of the three known values.
"""
from __future__ import annotations  # `X | None` annotations must stay lazy on Python 3.9

import json
import re
import zipfile
from datetime import datetime

from .errors import SurveyFormatError
from .model import (
    Geometry,
    GeometryType,
    Observation,
    PhotoRef,
    RevisitStation,
    SurveyExport,
    SurveyRevisit,
    SurveySession,
)
from .schema import SURVEY_FIELD_NAMES, VALID_POSITION_SOURCES

SESSION_GEOJSON_ENTRY = "session.geojson"

_GEOM_TYPE_BY_NAME = {t.value: t for t in GeometryType}

# App emits e.g. "2026-08-24T10:05:41.000Z" - UTC, fractional seconds of any length.
# datetime.fromisoformat() only accepts a bare 'Z' from Python 3.11 (target floor is
# 3.9) and is picky about fractional-second digit counts, so this is hand-rolled
# rather than relying on stdlib parsing to accept the app's exact format.
_ISO_UTC_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})(?P<frac>\.\d+)?Z$"
)


def _parse_datetime(value: str, *, context: str) -> datetime:
    match = _ISO_UTC_RE.match(value)
    if not match:
        raise SurveyFormatError(f"{context}: not a recognised UTC ISO-8601 timestamp: {value!r}")
    frac = match.group("frac") or ""
    if frac:
        digits = (frac[1:] + "000000")[:6]  # pad/truncate to microseconds
        frac = f".{digits}"
    return datetime.fromisoformat(f"{match.group('date')}T{match.group('time')}{frac}+00:00")


def read_export(zf: zipfile.ZipFile) -> SurveyExport:
    """Parse session.geojson from an already-open zip. The caller owns the ZipFile so
    it can be shared with media.py - opening the same zip twice would be wasteful and
    (worse) a second read could theoretically observe different bytes.
    """
    try:
        raw = zf.read(SESSION_GEOJSON_ENTRY)
    except KeyError as exc:
        raise SurveyFormatError(f"zip has no {SESSION_GEOJSON_ENTRY!r} entry") from exc
    return parse_session_geojson(raw)


def parse_session_geojson(raw: bytes) -> SurveyExport:
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SurveyFormatError(f"session.geojson is not valid JSON: {exc}") from exc

    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        raise SurveyFormatError("session.geojson is not a GeoJSON FeatureCollection")

    session = _parse_session(doc)
    revisit = _parse_revisit(doc.get("survey_revisit"))

    features = doc.get("features")
    if not isinstance(features, list):
        raise SurveyFormatError("session.geojson 'features' is missing or not a list")

    observations = tuple(
        _parse_observation(f, index=i) for i, f in enumerate(features)
    )

    return SurveyExport(session=session, revisit=revisit, observations=observations)


def _parse_session(doc: dict) -> SurveySession:
    raw = doc.get("survey_session")
    if not isinstance(raw, dict):
        raise SurveyFormatError("session.geojson is missing the survey_session foreign member")

    try:
        session_id = raw["id"]
        name = raw["name"]
        started_at_raw = raw["started_at"]
    except KeyError as exc:
        raise SurveyFormatError(f"survey_session is missing required key {exc}") from exc

    ended_at_raw = raw.get("ended_at")

    return SurveySession(
        id=session_id,
        name=name,
        started_at=_parse_datetime(started_at_raw, context="survey_session.started_at"),
        ended_at=(
            _parse_datetime(ended_at_raw, context="survey_session.ended_at")
            if ended_at_raw is not None
            else None
        ),
    )


def _parse_revisit(raw: object) -> SurveyRevisit | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SurveyFormatError("survey_revisit is present but not an object")

    stations_raw = raw.get("stations")
    if not isinstance(stations_raw, list):
        raise SurveyFormatError("survey_revisit.stations is missing or not a list")

    stations = tuple(_parse_station(s, index=i) for i, s in enumerate(stations_raw))

    try:
        return SurveyRevisit(
            reference_file=raw["reference_file"],
            reference_hash=raw["reference_hash"],
            reference_session_id=raw.get("reference_session_id"),
            reference_session_name=raw["reference_session_name"],
            reference_started_at=_parse_datetime(
                raw["reference_started_at"], context="survey_revisit.reference_started_at"
            ),
            stations=stations,
        )
    except KeyError as exc:
        raise SurveyFormatError(f"survey_revisit is missing required key {exc}") from exc


def _parse_station(raw: dict, *, index: int) -> RevisitStation:
    try:
        ref_obs_id = raw["ref_obs_id"]
        state = raw["state"]
    except KeyError as exc:
        raise SurveyFormatError(f"survey_revisit.stations[{index}] is missing key {exc}") from exc
    return RevisitStation(ref_obs_id=ref_obs_id, state=state, reason=raw.get("reason"))


_MISSING = object()


def _get(props: dict, key: str):
    """Distinguish an absent key from an explicit null. Both are tolerated (handoff
    §3: old exports lack newer keys entirely) but callers that need to *know* which
    happened (e.g. a future stricter-validation mode) can compare against _MISSING.
    """
    return props.get(key, _MISSING)


def _as_double(value: object, *, field_name: str, obs_id: str) -> float | None:
    if value is None or value is _MISSING:
        return None
    if isinstance(value, bool):  # bool is an int subclass - reject before the number check
        raise SurveyFormatError(f"{obs_id}.{field_name}: expected a number, got bool")
    if isinstance(value, (int, float)):
        return float(value)
    raise SurveyFormatError(f"{obs_id}.{field_name}: expected a number, got {type(value).__name__}")


def _as_int(value: object, *, field_name: str, obs_id: str) -> int | None:
    if value is None or value is _MISSING:
        return None
    if isinstance(value, bool):
        raise SurveyFormatError(f"{obs_id}.{field_name}: expected a number, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    raise SurveyFormatError(f"{obs_id}.{field_name}: expected a number, got {type(value).__name__}")


def _as_str(value: object, *, field_name: str, obs_id: str, nullable: bool) -> str | None:
    if value is None or value is _MISSING:
        if not nullable:
            raise SurveyFormatError(f"{obs_id}.{field_name}: required but missing/null")
        return None
    if isinstance(value, str):
        return value
    raise SurveyFormatError(f"{obs_id}.{field_name}: expected a string, got {type(value).__name__}")


def _as_datetime(value: object, *, field_name: str, obs_id: str) -> datetime:
    if value is None or value is _MISSING or not isinstance(value, str):
        raise SurveyFormatError(f"{obs_id}.{field_name}: required timestamp missing/invalid")
    return _parse_datetime(value, context=f"{obs_id}.{field_name}")


def _as_required_double(value: object, *, field_name: str, obs_id: str) -> float:
    result = _as_double(value, field_name=field_name, obs_id=obs_id)
    if result is None:
        raise SurveyFormatError(f"{obs_id}.{field_name}: required but missing/null")
    return result


def _as_required_str(value: object, *, field_name: str, obs_id: str) -> str:
    result = _as_str(value, field_name=field_name, obs_id=obs_id, nullable=False)
    assert result is not None  # nullable=False guarantees this; documents intent for readers
    return result


def _as_trace_gaps(value: object, *, obs_id: str) -> tuple[int, ...] | None:
    if value is None or value is _MISSING:
        return None
    if not isinstance(value, list):
        raise SurveyFormatError(f"{obs_id}.trace_gaps: expected a list, got {type(value).__name__}")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise SurveyFormatError(f"{obs_id}.trace_gaps: expected a list of ints, got {item!r}")
        if item < 1:
            raise SurveyFormatError(f"{obs_id}.trace_gaps: index {item} is not 1-based (>=1)")
        result.append(item)
    return tuple(result)


def _as_photo_entry(item: object, *, obs_id: str, index: int) -> PhotoRef:
    if isinstance(item, str):
        return PhotoRef(photo=item, ref_photo=None)
    if isinstance(item, dict):
        photo = item.get("photo")
        if not isinstance(photo, str):
            raise SurveyFormatError(
                f"{obs_id}.photos[{index}].photo: expected a string, got {type(photo).__name__}"
            )
        ref_photo = item.get("ref_photo")
        if ref_photo is not None and not isinstance(ref_photo, str):
            raise SurveyFormatError(
                f"{obs_id}.photos[{index}].ref_photo: expected a string or null, "
                f"got {type(ref_photo).__name__}"
            )
        return PhotoRef(photo=photo, ref_photo=ref_photo)
    raise SurveyFormatError(
        f"{obs_id}.photos[{index}]: expected a string or object, got {type(item).__name__}"
    )


def _as_photos(
    value: object, *, obs_id: str, photo: str | None, ref_photo: str | None
) -> tuple[PhotoRef, ...]:
    """The `photos` array (handoff addendum - not part of the original §3 schema).
    `photo`/`ref_photo` are this observation's already-parsed scalar properties,
    used both to synthesise a result when `photos` itself is absent/null/empty
    (every export before this format change, and any future one that reverts to
    single-photo), and to make sure a scalar `photo` is never lost even if some
    future export's array happened to omit it - see the handoff addendum.
    """
    if value is None or value is _MISSING or value == []:
        return (PhotoRef(photo=photo, ref_photo=ref_photo),) if photo is not None else ()

    if not isinstance(value, list):
        raise SurveyFormatError(f"{obs_id}.photos: expected a list, got {type(value).__name__}")

    entries = [_as_photo_entry(item, obs_id=obs_id, index=i) for i, item in enumerate(value)]

    seen = {entry.photo for entry in entries}
    if photo is not None and photo not in seen:
        entries.insert(0, PhotoRef(photo=photo, ref_photo=ref_photo))

    deduped: list[PhotoRef] = []
    dedup_seen: set[str] = set()
    for entry in entries:
        if entry.photo in dedup_seen:
            continue
        dedup_seen.add(entry.photo)
        deduped.append(entry)
    return tuple(deduped)


def _parse_geometry(raw: dict, *, obs_id: str) -> Geometry:
    if not isinstance(raw, dict):
        raise SurveyFormatError(f"{obs_id}: feature has no geometry object")
    type_name = raw.get("type")
    geom_type = _GEOM_TYPE_BY_NAME.get(type_name)
    if geom_type is None:
        raise SurveyFormatError(f"{obs_id}: unsupported geometry type {type_name!r}")

    coords = raw.get("coordinates")
    coords = _validate_and_freeze_coordinates(coords, geom_type, obs_id=obs_id)
    return Geometry(type=geom_type, coordinates=coords)


def _validate_and_freeze_coordinates(coords, geom_type: GeometryType, *, obs_id: str):
    def position(p) -> tuple[float, float]:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            raise SurveyFormatError(f"{obs_id}: malformed coordinate position {p!r}")
        lon, lat = p[0], p[1]
        if isinstance(lon, bool) or isinstance(lat, bool):
            raise SurveyFormatError(f"{obs_id}: malformed coordinate position {p!r}")
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            raise SurveyFormatError(f"{obs_id}: malformed coordinate position {p!r}")
        return (float(lon), float(lat))

    if geom_type is GeometryType.POINT:
        return position(coords)
    if geom_type is GeometryType.LINE_STRING:
        if not isinstance(coords, list):
            raise SurveyFormatError(f"{obs_id}: LineString coordinates must be a list")
        return tuple(position(p) for p in coords)
    if geom_type is GeometryType.POLYGON:
        if not isinstance(coords, list):
            raise SurveyFormatError(f"{obs_id}: Polygon coordinates must be a list of rings")
        # Deliberately not validated further: closure, ring count, self-intersection
        # are all left as-is per handoff §8 ("import it, don't reject it").
        return tuple(tuple(position(p) for p in ring) for ring in coords)
    raise AssertionError(f"unreachable geometry type {geom_type}")  # pragma: no cover


def _parse_observation(raw: dict, *, index: int) -> Observation:
    if not isinstance(raw, dict) or raw.get("type") != "Feature":
        raise SurveyFormatError(f"features[{index}] is not a GeoJSON Feature")

    props = raw.get("properties")
    if not isinstance(props, dict):
        raise SurveyFormatError(f"features[{index}] has no properties object")

    obs_id = props.get("obs_id")
    if not isinstance(obs_id, str):
        raise SurveyFormatError(f"features[{index}]: obs_id is missing or not a string")

    geometry = _parse_geometry(raw.get("geometry"), obs_id=obs_id)

    # Bound helpers, so every call site below only names the key - obs_id/props
    # threading is what made this function's previous version hard to read (and
    # blew past the line-length limit on nearly every field).
    def dbl(key: str) -> float | None:
        return _as_double(_get(props, key), field_name=key, obs_id=obs_id)

    def req_dbl(key: str) -> float:
        return _as_required_double(_get(props, key), field_name=key, obs_id=obs_id)

    def txt(key: str) -> str | None:
        return _as_str(_get(props, key), field_name=key, obs_id=obs_id, nullable=True)

    def req_txt(key: str) -> str:
        return _as_required_str(_get(props, key), field_name=key, obs_id=obs_id)

    def num(key: str) -> int | None:
        return _as_int(_get(props, key), field_name=key, obs_id=obs_id)

    def dt(key: str) -> datetime:
        return _as_datetime(_get(props, key), field_name=key, obs_id=obs_id)

    position_source = req_txt("position_source")
    if position_source not in VALID_POSITION_SOURCES:
        valid = sorted(VALID_POSITION_SOURCES)
        raise SurveyFormatError(f"{obs_id}.position_source: {position_source!r} not one of {valid}")

    feature_layer = txt("feature_layer")
    feature_id = txt("feature_id")
    if (feature_layer is None) != (feature_id is None):
        raise SurveyFormatError(
            f"{obs_id}: feature_layer/feature_id must both be present or both null "
            f"(got feature_layer={feature_layer!r}, feature_id={feature_id!r})"
        )

    extra_properties = {k: v for k, v in props.items() if k not in SURVEY_FIELD_NAMES}

    return Observation(
        geometry=geometry,
        obs_id=obs_id,
        recorded_at=dt("recorded_at"),
        fix_at=dt("fix_at"),
        lat=req_dbl("lat"),
        lon=req_dbl("lon"),
        gps_accuracy_m=req_dbl("gps_accuracy_m"),
        altitude_m=dbl("altitude_m"),
        altitude_accuracy_m=dbl("altitude_accuracy_m"),
        heading_deg=dbl("heading_deg"),
        heading_accuracy_deg=dbl("heading_accuracy_deg"),
        note=req_txt("note"),
        photo=(photo := txt("photo")),
        photos=_as_photos(
            _get(props, "photos"), obs_id=obs_id, photo=photo, ref_photo=txt("ref_photo")
        ),
        audio=txt("audio"),
        audio_duration_ms=num("audio_duration_ms"),
        feature_layer=feature_layer,
        feature_id=feature_id,
        feature_label=txt("feature_label"),
        os_grid_ref=txt("os_grid_ref"),
        position_source=position_source,
        trace_length_m=dbl("trace_length_m"),
        trace_gaps=_as_trace_gaps(_get(props, "trace_gaps"), obs_id=obs_id),
        ref_obs_id=txt("ref_obs_id"),
        ref_photo=txt("ref_photo"),
        session_name=req_txt("session_name"),
        app_version=req_txt("app_version"),
        extra_properties=extra_properties,
    )
