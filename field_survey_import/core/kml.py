"""Pure-Python KML/KMZ builder for sharing a Field Survey export with non-specialist
GIS tools (Google Earth / Google Maps) - see scripts/export_kml.py for the CLI entry
point. Zero qgis/PyQt5 imports (tests/test_core_has_no_qgis_imports.py). The KML/XML
side is still zero-dependency - hand-assembled string templates rather than
xml.etree.ElementTree, matching the existing precedent of qgis/forms.py's expression
strings: ElementTree can't emit CDATA sections, and pulling in lxml/simplekml would be
an unnecessary dependency for a feature that doesn't need one. Photo downscaling is
this project's one real third-party dependency, Pillow - see photo_optimize.py
(shared with core/html_map.py's self-contained-HTML export) for why, and note its
import is lazy so importing this module at all never requires Pillow installed, only
actually optimizing a photo does.

Scope (v1): plain observation export only. Revisit / then-vs-now photo comparison
(qgis/revisit.py's ref_obs_id<->obs_id resolution, handoff §7) is QGIS-side and out of
scope - a revisit session's observations export exactly like any other's, no
then-vs-now logic. No custom or heading-rotated icons either - Google Earth's built-in
default pin only, so nothing needs bundling or a network fetch (a possible future
enhancement, not built here).

Known, documented limitation of the output itself: Google My Maps' importer does not
render images embedded in a KMZ's balloon HTML at all, regardless of file size or
structure - only externally-hosted image URLs. Google Earth (desktop/web/mobile)
renders embedded KMZ images fine - this module optimises for that. If My Maps
specifically is the target, see core/html_map.py instead (a self-contained HTML file
with photos embedded as data URIs - works in any browser, not just Earth).
"""
from __future__ import annotations  # `X | None` unions must stay lazy on Python 3.9

import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from .media import MediaRef, resolve_media
from .model import Geometry, GeometryType, Observation, SurveyExport
from .photo_optimize import (
    DEFAULT_PHOTO_OPTIMIZATION,
    PhotoOptimization,
)
from .photo_optimize import (
    optimize_photo_bytes as _optimize_photo_bytes,
)

KML_NAMESPACE = "http://www.opengis.net/kml/2.2"

_GEOM_FOLDER_TITLES: dict[GeometryType, str] = {
    GeometryType.POINT: "Points",
    GeometryType.LINE_STRING: "Paths",
    GeometryType.POLYGON: "Boundaries",
}

_NAME_TRUNCATE_LEN = 60
_COORD_PRECISION = 6  # ~11cm at the equator - a display choice, not a precision claim


def _escape_text(text: str) -> str:
    """Escape &, < and > for both non-CDATA XML text (<name>, <Document><name>) and
    text embedded inside a CDATA-wrapped HTML description. Inside CDATA the XML parser
    doesn't strictly require this (only the literal ']]>' sequence is actually illegal
    there) - it's done anyway for two independent reasons: (1) HTML hygiene, so a note
    like "<b>shout</b>" or "AT&T" can't be misinterpreted as markup/an entity by the
    balloon's HTML renderer, and (2) escaping '>' also turns any literal ']]>' in user
    text into ']]&gt;', which no longer matches the CDATA close sequence - so this one
    helper does double duty as the CDATA-injection guard. Do not remove escaping from
    CDATA-embedded text on the theory that "CDATA doesn't need it" - that reasoning is
    correct for the XML layer only and reopens the ']]>' truncation bug.
    """
    return _xml_escape(text)


def _format_kml_timestamp(dt: datetime) -> str:
    """recorded_at is always tz-aware UTC already (reader._parse_datetime normalises
    it) - re-normalise via astimezone() anyway rather than assuming, and truncate to
    whole seconds ('Z'-suffixed) - KML's dateTime allows fractional seconds, nothing
    here needs that precision.
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _placemark_name(obs: Observation) -> str:
    """note (truncated) -> os_grid_ref -> obs_id. Truncate the RAW text before
    escaping, never after - truncating post-escape risks slicing a multi-char entity
    like '&amp;' in half. note's "no note" sentinel is '' (schema.py), not None, so
    this is a truthiness check, not `is not None`.
    """
    if obs.note:
        text = obs.note.strip()
        if len(text) > _NAME_TRUNCATE_LEN:
            text = text[: _NAME_TRUNCATE_LEN - 1].rstrip() + "…"
        return _escape_text(text)
    if obs.os_grid_ref:
        return _escape_text(obs.os_grid_ref)
    return _escape_text(obs.obs_id)


def _coord(position: tuple[float, float]) -> str:
    # position is (lon, lat) - GeoJSON/RFC 7946 order, which is ALSO the order KML's
    # <coordinates> requires. Do not swap.
    lon, lat = position
    return f"{lon:.{_COORD_PRECISION}f},{lat:.{_COORD_PRECISION}f}"


def geometry_to_kml(geometry: Geometry) -> str:
    """Public (not underscore-prefixed) because the multi-ring Polygon case is worth a
    direct unit test independent of a full document build.
    """
    if geometry.type is GeometryType.POINT:
        return f"<Point><coordinates>{_coord(geometry.coordinates)}</coordinates></Point>"
    if geometry.type is GeometryType.LINE_STRING:
        coords = " ".join(_coord(p) for p in geometry.coordinates)
        return (
            "<LineString><tessellate>1</tessellate>"
            f"<coordinates>{coords}</coordinates></LineString>"
        )
    if geometry.type is GeometryType.POLYGON:
        rings = geometry.coordinates
        parts = [_ring_kml(rings[0], "outerBoundaryIs")]
        parts.extend(_ring_kml(ring, "innerBoundaryIs") for ring in rings[1:])
        return "<Polygon>" + "".join(parts) + "</Polygon>"
    raise AssertionError(f"unreachable geometry type {geometry.type}")  # pragma: no cover


def _ring_kml(ring: tuple[tuple[float, float], ...], tag: str) -> str:
    coords = " ".join(_coord(p) for p in ring)
    return f"<{tag}><LinearRing><coordinates>{coords}</coordinates></LinearRing></{tag}>"


def build_media_manifest(refs: Iterable[MediaRef]) -> dict[str, str]:
    """Map each unique zip_entry -> a collision-safe path under files/ inside the KMZ.
    Keyed by zip_entry (not filename): ULID filenames make a collision between two
    DIFFERENT files vanishingly unlikely, but if it ever happens, the second (and
    later) occurrence keeps its photos/audio subdirectory so nothing gets silently
    overwritten inside the KMZ.
    """
    manifest: dict[str, str] = {}
    claimed_by: dict[str, str] = {}  # kmz-relative path -> the zip_entry that claimed it
    for ref in refs:
        if ref.zip_entry in manifest:
            continue
        candidate = f"files/{ref.filename}"
        if candidate in claimed_by and claimed_by[candidate] != ref.zip_entry:
            candidate = f"files/{ref.kind}/{ref.filename}"
        manifest[ref.zip_entry] = candidate
        claimed_by[candidate] = ref.zip_entry
    return manifest


def _gallery_html(photo_refs: tuple[MediaRef, ...], manifest: dict[str, str]) -> str:
    if not photo_refs:
        return ""
    width = 400 if len(photo_refs) == 1 else 160
    imgs = "".join(
        f'<img src="{_escape_text(manifest[ref.zip_entry])}" width="{width}" '
        'style="margin:2px;"/>'
        for ref in photo_refs
    )
    return f"<div>{imgs}</div>"


def _audio_html(audio_ref: MediaRef | None, manifest: dict[str, str]) -> str:
    if audio_ref is None:
        return ""
    href = _escape_text(manifest[audio_ref.zip_entry])
    label = _escape_text(audio_ref.filename)
    return f'<p><a href="{href}">Download voice note ({label})</a></p>'


def _description_html(
    obs: Observation,
    photo_refs: tuple[MediaRef, ...],
    audio_ref: MediaRef | None,
    manifest: dict[str, str],
) -> str:
    parts: list[str] = []
    if obs.note:
        parts.append(f"<p>{_escape_text(obs.note)}</p>")
    parts.append(f"<p><b>Recorded:</b> {_escape_text(obs.recorded_at.isoformat())}</p>")
    if obs.os_grid_ref:
        parts.append(f"<p><b>Grid ref:</b> {_escape_text(obs.os_grid_ref)}</p>")
    parts.append(f"<p><b>GPS accuracy:</b> ±{obs.gps_accuracy_m:.1f} m</p>")
    if obs.heading_deg is not None:
        parts.append(f"<p><b>Heading:</b> {obs.heading_deg:.0f}°</p>")
    gallery = _gallery_html(photo_refs, manifest)
    if gallery:
        parts.append(gallery)
    audio_html = _audio_html(audio_ref, manifest)
    if audio_html:
        parts.append(audio_html)
    return "<![CDATA[" + "".join(parts) + "]]>"


def _placemark_kml(
    obs: Observation,
    photo_refs: tuple[MediaRef, ...],
    audio_ref: MediaRef | None,
    manifest: dict[str, str],
) -> str:
    return (
        "<Placemark>"
        f"<name>{_placemark_name(obs)}</name>"
        f"<description>{_description_html(obs, photo_refs, audio_ref, manifest)}</description>"
        f"<TimeStamp><when>{_format_kml_timestamp(obs.recorded_at)}</when></TimeStamp>"
        f"{geometry_to_kml(obs.geometry)}"
        "</Placemark>"
    )


def build_kml_document(
    export: SurveyExport, zf: zipfile.ZipFile
) -> tuple[str, dict[str, str], dict[str, MediaRef]]:
    """Returns (kml_xml_string, media_manifest, ref_by_entry). The manifest is
    returned rather than recomputed by write_kmz(), so callers/tests can assert on
    exactly which zip_entry maps to which KMZ-internal path without re-parsing the
    KML; ref_by_entry lets write_kmz() tell photos (worth downscaling) from audio
    (embedded as-is) without re-deriving that from the manifest's paths. Propagates
    MediaJoinError from resolve_media() unchanged - a referenced photo genuinely
    missing from the zip should stop the export, not silently drop it.
    """
    media_by_obs = {obs.obs_id: resolve_media(obs, zf) for obs in export.observations}

    all_refs: list[MediaRef] = []
    for photo_refs, audio_ref in media_by_obs.values():
        all_refs.extend(photo_refs)
        if audio_ref is not None:
            all_refs.append(audio_ref)
    manifest = build_media_manifest(all_refs)
    ref_by_entry = {ref.zip_entry: ref for ref in all_refs}

    by_type: dict[GeometryType, list[str]] = {t: [] for t in GeometryType}
    for obs in export.observations:
        photo_refs, audio_ref = media_by_obs[obs.obs_id]
        by_type[obs.geometry.type].append(_placemark_kml(obs, photo_refs, audio_ref, manifest))

    folders = "".join(
        f"<Folder><name>{title}</name>{''.join(by_type[gt])}</Folder>"
        for gt, title in _GEOM_FOLDER_TITLES.items()
        if by_type[gt]
    )

    kml_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<kml xmlns="{KML_NAMESPACE}">'
        f"<Document><name>{_escape_text(export.session.name)}</name>{folders}</Document>"
        "</kml>"
    )
    return kml_xml, manifest, ref_by_entry


@dataclass(frozen=True)
class KmzSummary:
    session_name: str
    observation_counts: dict[GeometryType, int]
    photo_count: int
    audio_count: int
    output_size_bytes: int


def write_kmz(
    export: SurveyExport,
    zf: zipfile.ZipFile,
    out_path: Path,
    *,
    photo_optimization: PhotoOptimization | None = DEFAULT_PHOTO_OPTIMIZATION,
) -> KmzSummary:
    """Build doc.kml and write it plus every referenced photo/audio file into a single
    .kmz at out_path, copying bytes straight out of the source zip
    (zf.read(ref.zip_entry)) - never extracting to a temp directory on disk. Photos
    (not audio) are downscaled/recompressed per `photo_optimization` unless it's
    None (embed originals unchanged).
    """
    kml_xml, manifest, ref_by_entry = build_kml_document(export, zf)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("doc.kml", kml_xml)
        for zip_entry, kmz_path in manifest.items():
            data = zf.read(zip_entry)
            ref = ref_by_entry[zip_entry]
            if photo_optimization is not None and ref.kind == "photo":
                data = _optimize_photo_bytes(data, photo_optimization)
            out.writestr(kmz_path, data)

    counts: dict[GeometryType, int] = {t: 0 for t in GeometryType}
    photo_count = audio_count = 0
    for obs in export.observations:
        counts[obs.geometry.type] += 1
        photo_count += len(obs.photos)
        if obs.audio is not None:
            audio_count += 1

    return KmzSummary(
        session_name=export.session.name,
        observation_counts=counts,
        photo_count=photo_count,
        audio_count=audio_count,
        output_size_bytes=out_path.stat().st_size,
    )
