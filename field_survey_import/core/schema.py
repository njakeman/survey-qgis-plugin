"""The field schema for session.geojson features (handoff §3) plus the extra columns
the plugin itself adds. This is the single source of truth other layers build from:
GeoPackage field creation (qgis/writer.py) derives its QMetaType mapping from FieldType
here rather than re-declaring the property list, and reader.py validates parsed
properties against SURVEY_FIELDS so a missing/extra key is caught in one place.

Every property is declared explicitly rather than inferred from a sample file:
`altitude_accuracy_m` and `heading_accuracy_deg` serialise as bare integers when their
value happens to be whole (e.g. `10` not `10.0`), which would fool naive type inference
into declaring an Integer field that then rejects the float rows that follow.
"""
from __future__ import annotations  # `X | None` annotations must stay lazy on Python 3.9

from dataclasses import dataclass
from enum import Enum


class FieldType(Enum):
    STRING = "string"
    DOUBLE = "double"
    INT64 = "int64"
    DATETIME = "datetime"
    JSON = "json"  # no array/object type in GeoPackage - stored as a JSON-encoded string


@dataclass(frozen=True)
class FieldDef:
    name: str
    type: FieldType
    nullable: bool
    doc: str


# The 25 properties documented in handoff §3, in the order they're documented there.
SURVEY_FIELDS: tuple[FieldDef, ...] = (
    FieldDef("obs_id", FieldType.STRING, False,
             "Observation id (ULID). Stable across re-exports of the same session."),
    FieldDef("recorded_at", FieldType.DATETIME, False,
             "When the surveyor tapped Save."),
    FieldDef("fix_at", FieldType.DATETIME, False,
             "When the position was actually measured; deliberately distinct from "
             "recorded_at. For traces: the walk's start."),
    FieldDef("lat", FieldType.DOUBLE, False,
             "Representative point latitude (traces: distance-midpoint / area-weighted "
             "centroid - not derivable from the geometry, treat as opaque)."),
    FieldDef("lon", FieldType.DOUBLE, False,
             "Representative point longitude - see lat."),
    FieldDef("gps_accuracy_m", FieldType.DOUBLE, False,
             "Metres. Meaning depends on position_source: gps=measured accuracy, "
             "map=map precision at pick zoom, trace=worst vertex's fix accuracy."),
    FieldDef("altitude_m", FieldType.DOUBLE, True,
             "GPS altitude. Deliberately null for map-picked points."),
    FieldDef("altitude_accuracy_m", FieldType.DOUBLE, True, ""),
    FieldDef("heading_deg", FieldType.DOUBLE, True,
             "Compass heading at capture, clockwise from north (device-reported, not "
             "true-north normalised). Null = compass denied/unavailable - never fake "
             "a direction for these. Drives the arrow symbology."),
    FieldDef("heading_accuracy_deg", FieldType.DOUBLE, True, "Compass uncertainty."),
    FieldDef("note", FieldType.STRING, False,
             "Free text. Empty string ('') is the no-note sentinel, not null."),
    FieldDef("photo", FieldType.STRING, True,
             "Bare filename inside the zip's photos/ dir, e.g. '<photoId>.jpg'. Join "
             "on this literal value - never reconstruct as obs_id + '.jpg'. Legacy "
             "mirror of photos[0].photo (handoff addendum) - photos is authoritative."),
    FieldDef("photos", FieldType.JSON, True,
             "JSON array of {photo, ref_photo} objects, one per photo on this "
             "observation (handoff addendum - not in the original §3 table). "
             "Absent/null/[] on old exports; reader.py synthesises a single-entry "
             "tuple from the photo/ref_photo scalars in that case."),
    FieldDef("audio", FieldType.STRING, True,
             "Bare filename inside the zip's audio/ dir: '.webm' = Opus, '.m4a' = AAC."),
    FieldDef("audio_duration_ms", FieldType.INT64, True,
             "Measured at record time - usable without opening the file."),
    FieldDef("feature_layer", FieldType.STRING, True,
             "Reference-layer id if this observation was started from a tapped "
             "feature. Present iff feature_id is."),
    FieldDef("feature_id", FieldType.STRING, True,
             "Reference-feature id. Present iff feature_layer is."),
    FieldDef("feature_label", FieldType.STRING, True, "Human label for the tapped feature."),
    FieldDef("os_grid_ref", FieldType.STRING, True,
             "OSTN15-correct OS grid reference. Null outside Great Britain. Display "
             "convenience only - a restatement of lat/lon."),
    FieldDef("position_source", FieldType.STRING, False,
             "'gps' | 'map' | 'trace' - how the coordinates were obtained. "
             "Authoritative for gps_accuracy_m's meaning; geometry type (not this "
             "field) is authoritative for the Points/Paths/Boundaries layer split."),
    FieldDef("trace_length_m", FieldType.DOUBLE, True,
             "Walked length (path) or perimeter (boundary). Null on every Point row."),
    FieldDef("trace_gaps", FieldType.JSON, True,
             "JSON array of 1-based segment indices the app inferred rather than "
             "measured (index i => the segment from coordinate i-1 to i). "
             "null/absent = no gaps."),
    FieldDef("ref_obs_id", FieldType.STRING, True,
             "Revisit pairing: the reference-session observation this re-photographs."),
    FieldDef("ref_photo", FieldType.STRING, True,
             "That reference station's photo filename INSIDE THE REFERENCE ZIP - not "
             "this zip. Do not attempt to resolve it against this export's media."),
    FieldDef("session_name", FieldType.STRING, False,
             "Denormalised copy of survey_session.name, on every row."),
    FieldDef("app_version", FieldType.STRING, False,
             "App version that produced the export."),
)

# Columns the plugin adds on top of the documented schema (qgis/writer.py, §4/§7).
PLUGIN_FIELDS: tuple[FieldDef, ...] = (
    FieldDef("session_id", FieldType.STRING, False,
             "survey_session.id - joins a feature back to fs_sessions without relying "
             "on the denormalised, non-unique session_name."),
    FieldDef("photo_path", FieldType.STRING, True,
             "Path to the FIRST extracted photo, relative to the GeoPackage's "
             "directory - kept for the existing single-photo ExternalResource "
             "form widget. photo_paths is authoritative for the full set."),
    FieldDef("photo_paths", FieldType.JSON, True,
             "JSON array of every extracted photo's path, relative to the "
             "GeoPackage's directory, in the same order as the photos property."),
    FieldDef("photo_count", FieldType.INT64, False,
             "len(photo_paths). 0, never null, so it's directly filterable."),
    FieldDef("audio_path", FieldType.STRING, True,
             "Path to the extracted audio file, relative to the GeoPackage's directory."),
    FieldDef("revisit_state", FieldType.STRING, True,
             "Denormalised from fs_revisit_stations via ref_obs_id: "
             "'done' | 'skipped' | 'no_access' | 'not_visited'."),
    FieldDef("revisit_reason", FieldType.STRING, True,
             "Denormalised from fs_revisit_stations: free text, only ever set when "
             "revisit_state is 'no_access'."),
)

ALL_FIELDS: tuple[FieldDef, ...] = SURVEY_FIELDS + PLUGIN_FIELDS

SURVEY_FIELD_NAMES: frozenset[str] = frozenset(f.name for f in SURVEY_FIELDS)

VALID_POSITION_SOURCES = frozenset({"gps", "map", "trace"})
VALID_REVISIT_STATES = frozenset({"done", "skipped", "no_access", "not_visited"})
