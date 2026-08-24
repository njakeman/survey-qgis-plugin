"""core.schema.FieldType -> QGIS QMetaType mapping, and Observation -> QgsFeature
conversion. Isolated from writer.py so the type mapping (the fix for the mixed
int/float trap - see core/schema.py) is easy to find and test in isolation.
"""
import json

from qgis.core import QgsFeature, QgsField, QgsFields, QgsGeometry, QgsPointXY
from qgis.PyQt.QtCore import QDate, QDateTime, QMetaType, Qt, QTime

from ..core.model import Geometry, GeometryType, Observation
from ..core.schema import FieldDef, FieldType

_FIELD_TYPE_MAP = {
    FieldType.STRING: QMetaType.Type.QString,
    FieldType.DOUBLE: QMetaType.Type.Double,
    FieldType.INT64: QMetaType.Type.LongLong,
    FieldType.DATETIME: QMetaType.Type.QDateTime,
    FieldType.JSON: QMetaType.Type.QString,  # GeoPackage has no array type (core/schema.py)
}


def qgs_field_type(field_type: FieldType) -> QMetaType.Type:
    return _FIELD_TYPE_MAP[field_type]


def build_qgs_fields(field_defs: tuple[FieldDef, ...]) -> QgsFields:
    fields = QgsFields()
    for fd in field_defs:
        fields.append(QgsField(fd.name, qgs_field_type(fd.type)))
    return fields


def to_qdatetime(dt) -> QDateTime:
    """Explicit QDate/QTime construction rather than string parsing, so this never
    depends on Qt's ISO-8601 parser accepting our exact string shape.
    """
    if dt is None:
        return QDateTime()
    qd = QDate(dt.year, dt.month, dt.day)
    qt = QTime(dt.hour, dt.minute, dt.second, dt.microsecond // 1000)
    qdt = QDateTime(qd, qt)
    qdt.setTimeSpec(Qt.UTC)
    return qdt


def build_geometry(geom: Geometry) -> QgsGeometry:
    if geom.type is GeometryType.POINT:
        lon, lat = geom.coordinates
        return QgsGeometry.fromPointXY(QgsPointXY(lon, lat))
    if geom.type is GeometryType.LINE_STRING:
        points = [QgsPointXY(lon, lat) for lon, lat in geom.coordinates]
        return QgsGeometry.fromPolylineXY(points)
    if geom.type is GeometryType.POLYGON:
        rings = [[QgsPointXY(lon, lat) for lon, lat in ring] for ring in geom.coordinates]
        return QgsGeometry.fromPolygonXY(rings)
    raise AssertionError(f"unreachable geometry type {geom.type}")  # pragma: no cover


def observation_to_feature(
    obs: Observation,
    fields: QgsFields,
    *,
    session_id: str,
    photo_path: str | None,
    audio_path: str | None,
    revisit_state: str | None,
    revisit_reason: str | None,
) -> QgsFeature:
    feature = QgsFeature(fields)
    feature.setGeometry(build_geometry(obs.geometry))

    values = {
        "obs_id": obs.obs_id,
        "recorded_at": to_qdatetime(obs.recorded_at),
        "fix_at": to_qdatetime(obs.fix_at),
        "lat": obs.lat,
        "lon": obs.lon,
        "gps_accuracy_m": obs.gps_accuracy_m,
        "altitude_m": obs.altitude_m,
        "altitude_accuracy_m": obs.altitude_accuracy_m,
        "heading_deg": obs.heading_deg,
        "heading_accuracy_deg": obs.heading_accuracy_deg,
        "note": obs.note,
        "photo": obs.photo,
        "audio": obs.audio,
        "audio_duration_ms": obs.audio_duration_ms,
        "feature_layer": obs.feature_layer,
        "feature_id": obs.feature_id,
        "feature_label": obs.feature_label,
        "os_grid_ref": obs.os_grid_ref,
        "position_source": obs.position_source,
        "trace_length_m": obs.trace_length_m,
        "trace_gaps": json.dumps(list(obs.trace_gaps)) if obs.trace_gaps is not None else None,
        "ref_obs_id": obs.ref_obs_id,
        "ref_photo": obs.ref_photo,
        "session_name": obs.session_name,
        "app_version": obs.app_version,
        "session_id": session_id,
        "photo_path": photo_path,
        "audio_path": audio_path,
        "revisit_state": revisit_state,
        "revisit_reason": revisit_reason,
    }
    for name, value in values.items():
        feature.setAttribute(name, value)
    return feature
