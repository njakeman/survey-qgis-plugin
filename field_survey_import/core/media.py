"""Media resolution and extraction (handoff §2, §4, §8).

The join is always on the literal `photo`/`audio` property value - never
`obs_id + '.jpg'` (§2, §8): the photo id is not the observation id, and revisit
reference photos make the wrong join actively harmful. sample.zip's media basenames
happen to equal their owning obs_id; that's a coincidence this module must not
(and does not) rely on.
"""
from __future__ import annotations  # `X | None` annotations must stay lazy on Python 3.9

import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .errors import MediaJoinError
from .model import Observation

PHOTOS_DIR = "photos"
AUDIO_DIR = "audio"


@dataclass(frozen=True)
class MediaRef:
    kind: str  # "photo" | "audio"
    filename: str  # bare filename, e.g. "<photoId>.jpg" - the property's literal value
    zip_entry: str  # "photos/<filename>" - the actual zip member name


def resolve_media(obs: Observation, zf: zipfile.ZipFile) -> tuple[MediaRef | None, MediaRef | None]:
    """Returns (photo_ref, audio_ref) for one observation, or None for either that's
    absent. Raises MediaJoinError if a non-null reference names a file the zip
    doesn't contain - the format guarantees that can't happen (§2), so if it does,
    the zip is corrupt or truncated and the caller should not proceed silently.
    """
    names = set(zf.namelist())
    photo_ref = _resolve_one(obs.photo, PHOTOS_DIR, names, obs_id=obs.obs_id, kind="photo")
    audio_ref = _resolve_one(obs.audio, AUDIO_DIR, names, obs_id=obs.obs_id, kind="audio")
    return photo_ref, audio_ref


def _resolve_one(
    value: str | None, subdir: str, names: set[str], *, obs_id: str, kind: str
) -> MediaRef | None:
    if value is None:
        return None
    entry = f"{subdir}/{value}"
    if entry not in names:
        raise MediaJoinError(
            f"{obs_id}.{kind}={value!r} names {entry!r}, which is not in the zip "
            "(the export format guarantees this can't happen - the zip is likely "
            "corrupt or truncated)"
        )
    return MediaRef(kind=kind, filename=value, zip_entry=entry)


def extract_media(zf: zipfile.ZipFile, refs: set[MediaRef], dest: Path) -> dict[str, Path]:
    """Extract exactly the given media entries to dest/photos/ and dest/audio/.
    Returns {zip_entry: absolute Path written}. Refuses to extract anything outside
    dest as a defence-in-depth measure even though the zip is a trusted format (a
    corrupt/malicious zip entry name with '..' or an absolute path must never escape
    the media directory).
    """
    written: dict[str, Path] = {}
    for ref in refs:
        target = _safe_join(dest, ref.zip_entry)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(ref.zip_entry) as src, open(target, "wb") as out:
            out.write(src.read())
        written[ref.zip_entry] = target
    return written


def _safe_join(base: Path, zip_entry: str) -> Path:
    # Normalise to posix semantics first (zip entries always use '/'), reject
    # anything that isn't a plain relative path under photos/ or audio/.
    normalised = posixpath.normpath(zip_entry)
    if (
        normalised.startswith("../")
        or normalised == ".."
        or normalised.startswith("/")
        or ":" in normalised  # e.g. "C:/..." on Windows
        or normalised.split("/", 1)[0] not in (PHOTOS_DIR, AUDIO_DIR)
    ):
        raise MediaJoinError(f"unsafe zip entry name: {zip_entry!r}")
    target = (base / normalised).resolve()
    if base.resolve() not in target.parents:
        raise MediaJoinError(f"zip entry escapes the media directory: {zip_entry!r}")
    return target
