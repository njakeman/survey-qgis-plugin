"""Session/zip identity: content hashing for duplicate detection (§4), and the
slug used to name layers and the media directory (§4: "must not collide" across
many sessions imported into one project over time).
"""
import hashlib
import re
from collections.abc import Collection
from datetime import datetime
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def content_hash(path: Path) -> str:
    """sha256 of the whole zip file. Re-exports of an unchanged session produce a
    byte-identical zip (handoff §3: session.geojson is serialised canonically), so
    this is a meaningful identity check for "has this exact export already been
    imported" (§4).
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    lowered = text.strip().lower()
    slug = _SLUG_STRIP_RE.sub("-", lowered).strip("-")
    return slug


def session_slug(name: str, started_at: datetime, session_id: str) -> str:
    """Layer/media-dir base name. Can't trust `name` alone: sample.zip proves it can
    diverge from started_at's date (name="2026-08-12", started_at date=2026-08-13)
    and it can in principle be empty or pure punctuation. Falls back to a slice of
    the session id when the name sanitises away to nothing, so the slug is never
    just a bare date shared by every session recorded that day.
    """
    date_part = started_at.date().isoformat()
    name_part = slugify(name) or session_id[-8:].lower()
    return f"{name_part}-{date_part}"


def unique_slug(base: str, taken: Collection[str]) -> str:
    """base, then base-2, base-3, ... for the first name not already in `taken`
    (handoff §4: layer naming and the media directory scheme must not collide)."""
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"
