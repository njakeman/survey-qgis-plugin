"""GeoPackage writing: three vector layers per session (Points/Paths/Boundaries,
handoff §4) plus aspatial side tables carrying session/revisit metadata that isn't
on the features (fs_sessions, fs_revisits, fs_revisit_stations).

Side tables rather than QgsLayerMetadata, because:
- duplicate detection needs to be a real query (`WHERE session_id = ?`), not
  string-matching XML;
- a not_visited revisit station has NO feature at all (handoff §7) - it can only
  live in a table, never on a layer;
- one GeoPackage holds many sessions over time (§4) - per-layer metadata would
  duplicate the same bookkeeping three times per session for no benefit.

`import_id` (not `session_id`) is each table's real identity column: `session_id`
is deliberately NOT unique, since "Add alongside" (a re-import of a changed
session) means two rows legitimately share one session_id with different
import_ids and different layer/table names.
"""
from dataclasses import dataclass
from pathlib import Path

from osgeo import ogr
from qgis.core import (
    QgsCoordinateTransformContext,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from ..core.model import GeometryType, Observation, SurveyExport
from .fields import build_qgs_fields, observation_to_feature

ogr.UseExceptions()

_WKB_URI = {
    GeometryType.POINT: "Point",
    GeometryType.LINE_STRING: "LineString",
    GeometryType.POLYGON: "Polygon",
}

# (layer suffix, display suffix) - the three layers every session produces (§4).
GEOMETRY_KINDS: tuple[GeometryType, ...] = (
    GeometryType.POINT,
    GeometryType.LINE_STRING,
    GeometryType.POLYGON,
)

LAYER_TABLE_SUFFIX = {
    GeometryType.POINT: "points",
    GeometryType.LINE_STRING: "paths",
    GeometryType.POLYGON: "boundaries",
}
LAYER_DISPLAY_SUFFIX = {
    GeometryType.POINT: "Points",
    GeometryType.LINE_STRING: "Paths",
    GeometryType.POLYGON: "Boundaries",
}


@dataclass(frozen=True)
class WrittenLayer:
    geometry_type: GeometryType
    table_name: str
    display_name: str
    feature_count: int


def write_geometry_layers(
    gpkg_path: Path,
    slug: str,
    session_display_name: str,
    export: SurveyExport,
    *,
    session_id: str,
    media_paths: dict[str, tuple[str | None, str | None]],
    revisit_lookup: dict[str, tuple[str, str | None]],
) -> list[WrittenLayer]:
    """Writes Points/Paths/Boundaries layers into gpkg_path (created if absent,
    appended to otherwise - see `_action_on_existing_file`). Always writes all
    three, even when a kind has zero features, so layer naming/structure never
    depends on what a particular session happened to record (§4).

    media_paths: obs_id -> (photo_path, audio_path), both relative to the
    GeoPackage's directory (§4 "prefer relative paths").
    revisit_lookup: ref_obs_id -> (state, reason), used to denormalise revisit
    station state onto the matching observation's row (obs.ref_obs_id).
    """
    by_kind: dict[GeometryType, list[Observation]] = {k: [] for k in GEOMETRY_KINDS}
    for obs in export.observations:
        by_kind[obs.geometry.type].append(obs)

    written = []
    gpkg_exists = gpkg_path.exists()
    for kind in GEOMETRY_KINDS:
        observations = by_kind[kind]
        table_name = f"{_table_prefix(slug)}_{LAYER_TABLE_SUFFIX[kind]}"
        display_name = f"{session_display_name} — {LAYER_DISPLAY_SUFFIX[kind]}"

        mem_layer = _build_memory_layer(kind, observations, session_id, media_paths, revisit_lookup)

        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = table_name
        opts.fileEncoding = "UTF-8"
        opts.actionOnExistingFile = (
            QgsVectorFileWriter.CreateOrOverwriteLayer
            if gpkg_exists
            else QgsVectorFileWriter.CreateOrOverwriteFile
        )
        err, msg, _new_file, _new_layer = QgsVectorFileWriter.writeAsVectorFormatV3(
            mem_layer, str(gpkg_path), QgsCoordinateTransformContext(), opts
        )
        if err != QgsVectorFileWriter.NoError:
            raise OSError(f"failed writing layer {table_name!r} to {gpkg_path}: {msg}")
        gpkg_exists = True  # every layer after the first must append, not recreate

        written.append(
            WrittenLayer(
                geometry_type=kind,
                table_name=table_name,
                display_name=display_name,
                feature_count=len(observations),
            )
        )
    return written


def _table_prefix(slug: str) -> str:
    # GeoPackage/SQLite table names: keep it to safe characters. slug is already
    # lowercase [a-z0-9-] (core.identity.slugify) - just swap hyphens for underscores.
    return "fs_" + slug.replace("-", "_")


def _build_memory_layer(
    kind: GeometryType,
    observations: list[Observation],
    session_id: str,
    media_paths: dict[str, tuple[str | None, str | None]],
    revisit_lookup: dict[str, tuple[str, str | None]],
) -> QgsVectorLayer:
    from ..core.schema import ALL_FIELDS

    uri = f"{_WKB_URI[kind]}?crs=EPSG:4326"
    layer = QgsVectorLayer(uri, "mem", "memory")
    fields = build_qgs_fields(ALL_FIELDS)
    layer.dataProvider().addAttributes(fields)
    layer.updateFields()

    features = []
    for obs in observations:
        photo_path, audio_path = media_paths.get(obs.obs_id, (None, None))
        revisit_state, revisit_reason = (
            revisit_lookup.get(obs.ref_obs_id, (None, None)) if obs.ref_obs_id else (None, None)
        )
        features.append(
            observation_to_feature(
                obs,
                layer.fields(),
                session_id=session_id,
                photo_path=photo_path,
                audio_path=audio_path,
                revisit_state=revisit_state,
                revisit_reason=revisit_reason,
            )
        )
    if features:
        layer.dataProvider().addFeatures(features)
    return layer


# --- Side tables (fs_sessions / fs_revisits / fs_revisit_stations), via OGR -------
# QgsVectorFileWriter has no first-class notion of an aspatial GeoPackage table;
# OGR's CreateLayer(geom_type=ogr.wkbNone) is the documented way to get one
# registered correctly in gpkg_contents as data_type='attributes'.

_SESSIONS_TABLE = "fs_sessions"
_REVISITS_TABLE = "fs_revisits"
_STATIONS_TABLE = "fs_revisit_stations"

_SESSIONS_FIELDS = (
    ("import_id", ogr.OFTString),
    ("session_id", ogr.OFTString),
    ("session_name", ogr.OFTString),
    ("started_at", ogr.OFTDateTime),
    ("ended_at", ogr.OFTDateTime),
    ("source_zip_name", ogr.OFTString),
    ("source_zip_sha256", ogr.OFTString),
    ("imported_at", ogr.OFTDateTime),
    ("app_version", ogr.OFTString),
    ("plugin_version", ogr.OFTString),
    ("slug", ogr.OFTString),
    ("media_dir", ogr.OFTString),
    ("points_layer", ogr.OFTString),
    ("paths_layer", ogr.OFTString),
    ("boundaries_layer", ogr.OFTString),
    ("point_count", ogr.OFTInteger),
    ("path_count", ogr.OFTInteger),
    ("boundary_count", ogr.OFTInteger),
    ("is_revisit", ogr.OFTInteger),
)

_REVISITS_FIELDS = (
    ("import_id", ogr.OFTString),
    ("session_id", ogr.OFTString),
    ("reference_file", ogr.OFTString),
    ("reference_hash", ogr.OFTString),
    ("reference_session_id", ogr.OFTString),
    ("reference_session_name", ogr.OFTString),
    ("reference_started_at", ogr.OFTDateTime),
)

_STATIONS_FIELDS = (
    ("import_id", ogr.OFTString),
    ("session_id", ogr.OFTString),
    ("ref_obs_id", ogr.OFTString),
    ("state", ogr.OFTString),
    ("reason", ogr.OFTString),
)


def ensure_side_tables(gpkg_path: Path) -> None:
    """Idempotently create the three side tables if this is a fresh GeoPackage (or
    an existing one from before the plugin touched it). Safe to call before every
    import.
    """
    mode = 1 if gpkg_path.exists() else 0
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(gpkg_path)) if mode == 0 else ogr.Open(
        str(gpkg_path), update=1
    )
    try:
        existing = {ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())}
        for name, field_defs in (
            (_SESSIONS_TABLE, _SESSIONS_FIELDS),
            (_REVISITS_TABLE, _REVISITS_FIELDS),
            (_STATIONS_TABLE, _STATIONS_FIELDS),
        ):
            if name in existing:
                continue
            layer = ds.CreateLayer(name, geom_type=ogr.wkbNone)
            for field_name, field_type in field_defs:
                layer.CreateField(ogr.FieldDefn(field_name, field_type))
    finally:
        ds = None  # noqa: F841 - closes/flushes the OGR datasource (no explicit .Close() in this GDAL binding)


def _iso_or_none(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def insert_session_row(
    gpkg_path: Path,
    *,
    import_id: str,
    session_id: str,
    session_name: str,
    started_at,
    ended_at,
    source_zip_name: str,
    source_zip_sha256: str,
    imported_at,
    app_version: str,
    plugin_version: str,
    slug: str,
    media_dir: str,
    layers: list[WrittenLayer],
    is_revisit: bool,
) -> None:
    counts = {kind: 0 for kind in GEOMETRY_KINDS}
    names = {kind: None for kind in GEOMETRY_KINDS}
    for layer in layers:
        counts[layer.geometry_type] = layer.feature_count
        names[layer.geometry_type] = layer.table_name

    ds = ogr.Open(str(gpkg_path), update=1)
    try:
        lyr = ds.GetLayerByName(_SESSIONS_TABLE)
        feat = ogr.Feature(lyr.GetLayerDefn())
        feat["import_id"] = import_id
        feat["session_id"] = session_id
        feat["session_name"] = session_name
        feat["started_at"] = _iso_or_none(started_at)
        feat["ended_at"] = _iso_or_none(ended_at)
        feat["source_zip_name"] = source_zip_name
        feat["source_zip_sha256"] = source_zip_sha256
        feat["imported_at"] = _iso_or_none(imported_at)
        feat["app_version"] = app_version
        feat["plugin_version"] = plugin_version
        feat["slug"] = slug
        feat["media_dir"] = media_dir
        feat["points_layer"] = names[GeometryType.POINT]
        feat["paths_layer"] = names[GeometryType.LINE_STRING]
        feat["boundaries_layer"] = names[GeometryType.POLYGON]
        feat["point_count"] = counts[GeometryType.POINT]
        feat["path_count"] = counts[GeometryType.LINE_STRING]
        feat["boundary_count"] = counts[GeometryType.POLYGON]
        feat["is_revisit"] = 1 if is_revisit else 0
        lyr.CreateFeature(feat)
    finally:
        ds = None  # noqa: F841


def insert_revisit_rows(gpkg_path: Path, *, import_id: str, session_id: str, revisit) -> None:
    if revisit is None:
        return
    ds = ogr.Open(str(gpkg_path), update=1)
    try:
        rev_lyr = ds.GetLayerByName(_REVISITS_TABLE)
        feat = ogr.Feature(rev_lyr.GetLayerDefn())
        feat["import_id"] = import_id
        feat["session_id"] = session_id
        feat["reference_file"] = revisit.reference_file
        feat["reference_hash"] = revisit.reference_hash
        feat["reference_session_id"] = revisit.reference_session_id
        feat["reference_session_name"] = revisit.reference_session_name
        feat["reference_started_at"] = _iso_or_none(revisit.reference_started_at)
        rev_lyr.CreateFeature(feat)

        stations_lyr = ds.GetLayerByName(_STATIONS_TABLE)
        for station in revisit.stations:
            sfeat = ogr.Feature(stations_lyr.GetLayerDefn())
            sfeat["import_id"] = import_id
            sfeat["session_id"] = session_id
            sfeat["ref_obs_id"] = station.ref_obs_id
            sfeat["state"] = station.state
            sfeat["reason"] = station.reason
            stations_lyr.CreateFeature(sfeat)
    finally:
        ds = None  # noqa: F841


def list_all_slugs(gpkg_path: Path) -> set[str]:
    """Every slug already used in this GeoPackage, across all sessions - what
    identity.unique_slug() must avoid colliding with for 'Add alongside' (§4).
    """
    if not gpkg_path.exists():
        return set()
    ds = ogr.Open(str(gpkg_path))
    if ds is None:
        return set()
    try:
        lyr = ds.GetLayerByName(_SESSIONS_TABLE)
        if lyr is None:
            return set()
        return {feat["slug"] for feat in lyr if feat["slug"]}
    finally:
        ds = None  # noqa: F841


def _delete_layer_by_name(ds, name: str) -> None:
    for i in range(ds.GetLayerCount()):
        if ds.GetLayerByIndex(i).GetName() == name:
            ds.DeleteLayer(i)
            return


def delete_session_rows_and_layers(gpkg_path: Path, session_id: str) -> None:
    """Removes every import of `session_id`: its geometry layers, and its rows in
    all three side tables. Used by 'Replace' (duplicates.py) - the caller is
    responsible for removing any loaded QgsMapLayers from the project first
    (Windows locks open files) and for clearing the media directory.
    """
    rows = query_sessions_by_session_id(gpkg_path, session_id)
    if not rows:
        return
    ds = ogr.Open(str(gpkg_path), update=1)
    try:
        for row in rows:
            for key in ("points_layer", "paths_layer", "boundaries_layer"):
                name = row.get(key)
                if name:
                    _delete_layer_by_name(ds, name)
        escaped = session_id.replace("'", "''")
        ds.ExecuteSQL(f"DELETE FROM {_STATIONS_TABLE} WHERE session_id = '{escaped}'")
        ds.ExecuteSQL(f"DELETE FROM {_REVISITS_TABLE} WHERE session_id = '{escaped}'")
        ds.ExecuteSQL(f"DELETE FROM {_SESSIONS_TABLE} WHERE session_id = '{escaped}'")
    finally:
        ds = None  # noqa: F841


def query_sessions_by_session_id(gpkg_path: Path, session_id: str) -> list[dict]:
    """Duplicate detection support (§4): every fs_sessions row for this
    survey_session.id, most recent import first. Empty list if the GeoPackage or
    the side table doesn't exist yet.
    """
    if not gpkg_path.exists():
        return []
    ds = ogr.Open(str(gpkg_path))
    if ds is None:
        return []
    try:
        lyr = ds.GetLayerByName(_SESSIONS_TABLE)
        if lyr is None:
            return []
        escaped = session_id.replace("'", "''")
        lyr.SetAttributeFilter(f"session_id = '{escaped}'")
        field_names = [name for name, _ in _SESSIONS_FIELDS]
        rows = [{name: feat.GetField(name) for name in field_names} for feat in lyr]
        rows.sort(key=lambda r: r.get("imported_at") or "", reverse=True)
        return rows
    finally:
        ds = None  # noqa: F841
