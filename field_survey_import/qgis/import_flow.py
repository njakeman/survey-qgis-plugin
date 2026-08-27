"""Top-level import orchestration: zip -> GeoPackage. This is the single place the
whole pipeline (parse -> resolve/extract media -> write layers -> write side
tables) is wired together, so the crude Phase-2 toolbar wiring, the eventual
import dialog, and the Processing algorithm all call exactly the same code path.
"""
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core import identity, media, reader
from . import writer

# Kept in sync with metadata.txt's version by hand for now; revisited when
# scripts/package.ps1 is written (it already parses metadata.txt for the same
# number, so a follow-up can have this read from there instead of duplicating it).
PLUGIN_VERSION = "0.1.0"


@dataclass(frozen=True)
class ImportResult:
    import_id: str
    slug: str
    gpkg_path: Path
    media_dir: Path
    layers: list
    session_id: str
    session_name: str
    is_revisit: bool


def import_zip(
    zip_path: Path, gpkg_path: Path, *, slug_override: str | None = None
) -> ImportResult:
    """slug_override lets 'Add alongside' (services/duplicates.py) force a
    collision-free slug instead of the deterministic default - a fresh/Replace
    import always uses the deterministic slug so Replace naturally overwrites the
    same layer/table names.
    """
    zip_path = Path(zip_path)
    gpkg_path = Path(gpkg_path)
    gpkg_path.parent.mkdir(parents=True, exist_ok=True)

    content_hash = identity.content_hash(zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        export = reader.read_export(zf)
        slug = slug_override or identity.session_slug(
            export.session.name, export.session.started_at, export.session.id
        )
        media_dir_name = f"{slug}-media"
        media_dir = gpkg_path.parent / media_dir_name

        refs = set()
        obs_media_refs = {}
        for obs in export.observations:
            photo_refs, audio_ref = media.resolve_media(obs, zf)
            refs.update(photo_refs)
            if audio_ref:
                refs.add(audio_ref)
            obs_media_refs[obs.obs_id] = (photo_refs, audio_ref)

        written_files = media.extract_media(zf, refs, media_dir) if refs else {}

    def _rel(ref):
        return _relative_to(written_files[ref.zip_entry], gpkg_path.parent) if ref else None

    media_paths = {}
    for obs_id, (photo_refs, audio_ref) in obs_media_refs.items():
        media_paths[obs_id] = ([_rel(r) for r in photo_refs], _rel(audio_ref))

    revisit_lookup = {}
    if export.revisit is not None:
        for station in export.revisit.stations:
            revisit_lookup[station.ref_obs_id] = (station.state, station.reason)

    import_id = uuid.uuid4().hex
    session_display_name = f"{export.session.name} ({export.session.started_at.date().isoformat()})"

    writer.ensure_side_tables(gpkg_path)
    layers = writer.write_geometry_layers(
        gpkg_path,
        slug,
        session_display_name,
        export,
        session_id=export.session.id,
        media_paths=media_paths,
        revisit_lookup=revisit_lookup,
    )
    writer.insert_session_row(
        gpkg_path,
        import_id=import_id,
        session_id=export.session.id,
        session_name=export.session.name,
        started_at=export.session.started_at,
        ended_at=export.session.ended_at,
        source_zip_name=zip_path.name,
        source_zip_sha256=content_hash,
        imported_at=datetime.now(timezone.utc),
        app_version=export.observations[0].app_version if export.observations else "",
        plugin_version=PLUGIN_VERSION,
        slug=slug,
        media_dir=media_dir_name,
        layers=layers,
        is_revisit=export.revisit is not None,
    )
    writer.insert_revisit_rows(
        gpkg_path, import_id=import_id, session_id=export.session.id, revisit=export.revisit
    )

    table_by_kind = {wl.geometry_type: wl.table_name for wl in layers}
    photo_rows = []
    for obs in export.observations:
        photo_paths, _audio_rel = media_paths.get(obs.obs_id, ([], None))
        for seq, entry in enumerate(obs.photos):
            photo_rows.append(
                writer.PhotoRow(
                    layer_table=table_by_kind[obs.geometry.type],
                    obs_id=obs.obs_id,
                    seq=seq,
                    photo=entry.photo,
                    ref_photo=entry.ref_photo,
                    photo_path=photo_paths[seq] if seq < len(photo_paths) else None,
                )
            )
    writer.insert_photo_rows(
        gpkg_path, import_id=import_id, session_id=export.session.id, rows=photo_rows
    )

    return ImportResult(
        import_id=import_id,
        slug=slug,
        gpkg_path=gpkg_path,
        media_dir=media_dir,
        layers=layers,
        session_id=export.session.id,
        session_name=export.session.name,
        is_revisit=export.revisit is not None,
    )


def _relative_to(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()
