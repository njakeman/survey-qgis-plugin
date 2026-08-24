"""Parsed representation of a Field Survey export (handoff §3, §7). Plain dataclasses,
independent of any GeoJSON/GIS library, so field_survey_import.core stays free of
qgis/PyQt5 imports (see core/__init__.py and tests/test_core_has_no_qgis_imports.py).
"""
from __future__ import annotations  # `X | None` annotations must stay lazy on Python 3.9

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class GeometryType(Enum):
    POINT = "Point"
    LINE_STRING = "LineString"
    POLYGON = "Polygon"


# A GeoJSON position, [lon, lat] per RFC 7946 - never [lat, lon].
Position = tuple[float, float]


@dataclass(frozen=True)
class Geometry:
    """Coordinates keep their raw GeoJSON shape rather than being normalised, since the
    three geometry types are structurally different (a single position; a list of
    positions; a list of rings) and each is only ever consumed by code that already
    knows which type it has (reader.py checks type against coordinate nesting once, at
    parse time - see reader._parse_geometry).
    """

    type: GeometryType
    # Point: Position
    # LineString: tuple[Position, ...]
    # Polygon: tuple[tuple[Position, ...], ...]  (a single ring per handoff §3 - "always
    #   a single closed ring, first coordinate = last" - but stored as the standard
    #   GeoJSON ring-list shape rather than assumed-single, so a future export with a
    #   hole would surface as extra rings instead of silently losing them)
    coordinates: Any


@dataclass(frozen=True)
class Observation:
    """One GeoJSON Feature from session.geojson, geometry + all 25 documented
    properties (handoff §3) plus any properties this version of the plugin doesn't
    recognise (preserved in `extra_properties` rather than dropped).
    """

    geometry: Geometry

    obs_id: str
    recorded_at: datetime
    fix_at: datetime
    lat: float
    lon: float
    gps_accuracy_m: float
    altitude_m: float | None
    altitude_accuracy_m: float | None
    heading_deg: float | None
    heading_accuracy_deg: float | None
    note: str
    photo: str | None
    audio: str | None
    audio_duration_ms: int | None
    feature_layer: str | None
    feature_id: str | None
    feature_label: str | None
    os_grid_ref: str | None
    position_source: str
    trace_length_m: float | None
    trace_gaps: tuple[int, ...] | None
    ref_obs_id: str | None
    ref_photo: str | None
    session_name: str
    app_version: str

    extra_properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SurveySession:
    """The always-present `survey_session` foreign member (handoff §3)."""

    id: str
    name: str
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True)
class RevisitStation:
    ref_obs_id: str
    state: str  # one of schema.VALID_REVISIT_STATES
    reason: str | None


@dataclass(frozen=True)
class SurveyRevisit:
    """The `survey_revisit` foreign member, present only on revisit sessions (§7).
    `stations` lists EVERY reference station, including ones never visited this trip.
    """

    reference_file: str
    reference_hash: str
    reference_session_id: str | None
    reference_session_name: str
    reference_started_at: datetime
    stations: tuple[RevisitStation, ...]


@dataclass(frozen=True)
class SurveyExport:
    """The fully parsed contents of one session.geojson (not the zip - see
    reader.read_export for the zip-opening layer, which additionally carries the
    source path and content hash needed for duplicate detection).
    """

    session: SurveySession
    revisit: SurveyRevisit | None
    observations: tuple[Observation, ...]
