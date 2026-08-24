"""Duplicate detection and resolution (handoff §4; decision: detect by
survey_session.id + zip content hash, offer Replace / Add alongside / Cancel).
"""
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..core import identity
from . import import_flow, writer


class DuplicateStatus(Enum):
    NONE = "none"  # no prior import of this session_id - proceed normally
    ALREADY_IMPORTED = "already_imported"  # identical content hash already present
    CONFLICT = "conflict"  # same session_id, different hash - user must choose


@dataclass(frozen=True)
class DuplicateCheck:
    status: DuplicateStatus
    existing_rows: list  # fs_sessions rows for this session_id, most-recent-first


def check_duplicate(gpkg_path: Path, session_id: str, content_hash: str) -> DuplicateCheck:
    rows = writer.query_sessions_by_session_id(gpkg_path, session_id)
    if not rows:
        return DuplicateCheck(DuplicateStatus.NONE, [])
    if any(r["source_zip_sha256"] == content_hash for r in rows):
        return DuplicateCheck(DuplicateStatus.ALREADY_IMPORTED, rows)
    return DuplicateCheck(DuplicateStatus.CONFLICT, rows)


def replace_import(
    zip_path: Path, gpkg_path: Path, session_id: str, *, on_before_delete=None
) -> import_flow.ImportResult:
    """Removes every existing import of `session_id` (layers, side-table rows, and
    its media directory) and imports the zip fresh, into the same deterministic
    slug/table names. `on_before_delete(rows)` is called first if given, so a UI
    layer can remove any loaded QgsMapLayers from the project before the
    underlying gpkg tables are dropped (open layers can lock the file on Windows).
    """
    rows = writer.query_sessions_by_session_id(gpkg_path, session_id)
    if on_before_delete is not None:
        on_before_delete(rows)

    media_dirs = {r["media_dir"] for r in rows if r.get("media_dir")}
    writer.delete_session_rows_and_layers(gpkg_path, session_id)
    for media_dir_name in media_dirs:
        media_dir = gpkg_path.parent / media_dir_name
        shutil.rmtree(media_dir, ignore_errors=True)

    return import_flow.import_zip(zip_path, gpkg_path)


def add_alongside_import(zip_path: Path, gpkg_path: Path, session) -> import_flow.ImportResult:
    """Imports the zip under a slug guaranteed not to collide with any session
    already in this GeoPackage (handoff §4: "must not collide"), leaving every
    prior import of this session_id untouched.
    """
    base_slug = identity.session_slug(session.name, session.started_at, session.id)
    taken = writer.list_all_slugs(gpkg_path)
    slug = identity.unique_slug(base_slug, taken)
    return import_flow.import_zip(zip_path, gpkg_path, slug_override=slug)
