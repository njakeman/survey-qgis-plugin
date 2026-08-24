"""Then-vs-now photo resolution for revisit sessions (handoff §7). The join is
`ref_obs_id` (this session) <-> `obs_id` (the reference session), resolved via
`fs_revisits.reference_session_id`. Plain import must never depend on the
reference session being present - every failure path here returns a plain
English reason rather than raising, so the caller can just say what's missing.

Scoped to the same GeoPackage as the revisit session: the common case is both
sessions living in one project gpkg (the "Add to existing GeoPackage" default
destination). Cross-gpkg lookup is not attempted.
"""
from dataclasses import dataclass
from pathlib import Path

from osgeo import ogr

from . import writer


@dataclass(frozen=True)
class ReferencePhoto:
    path: Path | None
    reference_session_name: str | None
    not_found_reason: str | None


def resolve_reference_photo(
    gpkg_path: Path, reference_session_id: str | None, ref_obs_id: str
) -> ReferencePhoto:
    if reference_session_id is None:
        return ReferencePhoto(None, None, "no reference_session_id recorded for this revisit")

    rows = writer.query_sessions_by_session_id(gpkg_path, reference_session_id)
    if not rows:
        return ReferencePhoto(
            None, None, "the reference session hasn't been imported into this GeoPackage"
        )

    row = rows[0]  # most recent import of the reference session
    points_table = row.get("points_layer")
    session_name = row.get("session_name")
    if not points_table:
        return ReferencePhoto(None, session_name, "the reference session has no points layer")

    ds = ogr.Open(str(gpkg_path))
    if ds is None:
        return ReferencePhoto(None, session_name, "could not open the GeoPackage")
    try:
        layer = ds.GetLayerByName(points_table)
        if layer is None:
            return ReferencePhoto(None, session_name, "the reference points layer is missing")

        escaped = ref_obs_id.replace("'", "''")
        layer.SetAttributeFilter(f"obs_id = '{escaped}'")
        feature = next(iter(layer), None)
        if feature is None:
            return ReferencePhoto(
                None, session_name, "no matching observation found in the reference session"
            )

        photo_path = feature.GetField("photo_path")
        if not photo_path:
            return ReferencePhoto(None, session_name, "that reference observation has no photo")

        return ReferencePhoto(gpkg_path.parent / photo_path, session_name, None)
    finally:
        ds = None  # noqa: F841
