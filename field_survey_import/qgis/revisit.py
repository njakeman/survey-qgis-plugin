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


@dataclass(frozen=True)
class PhotoPair:
    seq: int
    now_path: Path | None
    now_filename: str | None
    then_path: Path | None
    then_filename: str | None  # the ref_photo literal that was looked for
    not_found_reason: str | None  # per-pair; None iff then_path is set


@dataclass(frozen=True)
class ReferenceComparison:
    reference_session_name: str | None
    pairs: tuple[PhotoPair, ...]
    not_found_reason: str | None  # session-level failure; pairs still carry "now" photos


def resolve_reference_photos(
    gpkg_path: Path,
    *,
    reference_session_id: str | None,
    ref_obs_id: str,
    now_photos: tuple[tuple[str | None, str | None], ...],
) -> ReferenceComparison:
    """Per-photo then-vs-now (handoff addendum §7): each of this observation's
    photos with a non-null `ref_photo` is looked up directly by filename in the
    reference session's photos; a photo with a null `ref_photo` falls back to the
    ref_obs_id<->obs_id join (the whole of the old single-photo behaviour),
    consuming the reference observation's photos in order as it goes.

    now_photos: (photo_path relative to the GeoPackage's directory, ref_photo) for
    every photo on THIS observation, in seq order - both may be None (a photo
    that never got extracted, or one with no ref_photo).

    Every session-level failure message matches resolve_reference_photo()'s
    exactly (handoff §7: plain import must never depend on the reference being
    present) - on any of them, `pairs` still carries the "now" photos so the
    dialog has something to show.
    """

    def _now_pairs(reason: str) -> tuple[PhotoPair, ...]:
        return tuple(
            PhotoPair(
                seq=seq,
                now_path=gpkg_path.parent / rel_path if rel_path else None,
                now_filename=Path(rel_path).name if rel_path else None,
                then_path=None,
                then_filename=ref_photo,
                not_found_reason=reason,
            )
            for seq, (rel_path, ref_photo) in enumerate(now_photos)
        )

    if reference_session_id is None:
        reason = "no reference_session_id recorded for this revisit"
        return ReferenceComparison(None, _now_pairs(reason), reason)

    rows = writer.query_sessions_by_session_id(gpkg_path, reference_session_id)
    if not rows:
        reason = "the reference session hasn't been imported into this GeoPackage"
        return ReferenceComparison(None, _now_pairs(reason), reason)

    row = rows[0]  # most recent import of the reference session
    session_name = row.get("session_name")
    points_table = row.get("points_layer")
    if not points_table:
        reason = "the reference session has no points layer"
        return ReferenceComparison(session_name, _now_pairs(reason), reason)

    ds = ogr.Open(str(gpkg_path))
    if ds is None:
        reason = "could not open the GeoPackage"
        return ReferenceComparison(session_name, _now_pairs(reason), reason)
    try:
        ref_layer = ds.GetLayerByName(points_table)
        if ref_layer is None:
            reason = "the reference points layer is missing"
            return ReferenceComparison(session_name, _now_pairs(reason), reason)

        by_filename, by_obs = _build_reference_index(gpkg_path, ref_layer, row["import_id"])

        # Pass 1: resolve every filename-exact match first, so an obs-level
        # fallback (pass 2) can never steal a reference photo another photo on
        # this observation named explicitly.
        exact_matches: dict[int, dict] = {}
        consumed_filenames: set[str] = set()
        for seq, (_rel_path, ref_photo) in enumerate(now_photos):
            if not ref_photo:
                continue
            entry = by_filename.get(ref_photo)
            if entry is not None:
                exact_matches[seq] = entry
                consumed_filenames.add(entry["filename"])

        # Pass 2: build every pair, falling back to the obs-level join for a
        # null ref_photo and consuming reference photos in order as it goes
        # (consumed_filenames doubles as that per-obs consumption state).
        pairs = []
        for seq, (rel_path, ref_photo) in enumerate(now_photos):
            now_path = gpkg_path.parent / rel_path if rel_path else None
            now_filename = Path(rel_path).name if rel_path else None

            if ref_photo:
                entry = exact_matches.get(seq)
                if entry is None:
                    pairs.append(
                        PhotoPair(
                            seq=seq,
                            now_path=now_path,
                            now_filename=now_filename,
                            then_path=None,
                            then_filename=ref_photo,
                            not_found_reason=(
                                f"the reference session doesn't contain a photo "
                                f"named '{ref_photo}'"
                            ),
                        )
                    )
                else:
                    pairs.append(
                        PhotoPair(
                            seq=seq,
                            now_path=now_path,
                            now_filename=now_filename,
                            then_path=(
                                gpkg_path.parent / entry["rel_path"] if entry["rel_path"] else None
                            ),
                            then_filename=entry["filename"],
                            not_found_reason=None,
                        )
                    )
                continue

            obs_rows = [
                r for r in by_obs.get(ref_obs_id, []) if r["filename"] not in consumed_filenames
            ]
            if not obs_rows:
                reason = (
                    "no matching observation found in the reference session"
                    if ref_obs_id not in by_obs
                    else "no reference photo paired with this one"
                )
                pairs.append(
                    PhotoPair(
                        seq=seq,
                        now_path=now_path,
                        now_filename=now_filename,
                        then_path=None,
                        then_filename=None,
                        not_found_reason=reason,
                    )
                )
                continue

            entry = obs_rows[0]
            consumed_filenames.add(entry["filename"])
            pairs.append(
                PhotoPair(
                    seq=seq,
                    now_path=now_path,
                    now_filename=now_filename,
                    then_path=gpkg_path.parent / entry["rel_path"] if entry["rel_path"] else None,
                    then_filename=entry["filename"],
                    not_found_reason=None,
                )
            )

        return ReferenceComparison(session_name, tuple(pairs), None)
    finally:
        ds = None  # noqa: F841


def _build_reference_index(gpkg_path: Path, ref_layer, ref_import_id: str):
    """(by_filename, by_obs) for the reference session's photos: by_filename maps
    a bare photo filename -> {"filename", "rel_path"}; by_obs maps obs_id -> the
    same dicts in seq order.

    Prefers fs_photos (every photo, correct seq order). Falls back to scanning
    the reference points layer's own photo/photo_path columns directly when
    fs_photos has no rows for this import - a reference session imported before
    fs_photos existed (handoff addendum backward-compat note).
    """
    photo_rows = writer.query_photos_by_import_id(gpkg_path, ref_import_id)
    if photo_rows:
        by_filename = {}
        by_obs: dict[str, list] = {}
        for r in photo_rows:
            if not r["photo"]:
                continue
            entry = {"filename": r["photo"], "rel_path": r["photo_path"]}
            by_filename[r["photo"]] = entry
            by_obs.setdefault(r["obs_id"], []).append(entry)
        return by_filename, by_obs

    by_filename = {}
    by_obs = {}
    ref_layer.ResetReading()
    for feat in ref_layer:
        filename = feat.GetField("photo")
        if not filename:
            continue
        entry = {"filename": filename, "rel_path": feat.GetField("photo_path")}
        by_filename[filename] = entry
        by_obs.setdefault(feat.GetField("obs_id"), []).append(entry)
    return by_filename, by_obs
