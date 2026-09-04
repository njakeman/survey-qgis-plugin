"""Self-contained single-file HTML export - the option for Google My Maps (which
never renders photos bundled inside a KMZ, only externally-hosted image URLs - see
core/kml.py's module docstring) or anyone without Google Earth at all. One .html
file: Leaflet.js + OpenStreetMap tiles loaded from a CDN over the *recipient's* own
internet connection at view time (this module itself makes no network calls, same as
every other part of core/), with every photo/audio file embedded directly as a
base64 data: URI - no separate files/ folder, nothing else to keep alongside it.

Zero qgis/PyQt5 imports (tests/test_core_has_no_qgis_imports.py). Photo downscaling
is shared with core/kml.py via photo_optimize.py - see that module for why Pillow is
this project's one dependency, and note its import stays lazy.

Scope (v1): same as core/kml.py - plain observation export only (no revisit/
then-vs-now), no custom or heading-rotated icons (Leaflet's default marker only).
"""
from __future__ import annotations  # `X | None` unions must stay lazy on Python 3.9

import base64
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as _html_escape

from .media import MediaRef, resolve_media
from .model import Geometry, GeometryType, Observation, SurveyExport
from .photo_optimize import DEFAULT_PHOTO_OPTIMIZATION, PhotoOptimization, optimize_photo_bytes

LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
_TILE_ATTRIBUTION = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
)

_NAME_TRUNCATE_LEN = 60  # matches core/kml.py's placemark-name convention

_AUDIO_MIME_BY_EXT = {
    ".webm": "audio/webm",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}


def _audio_mime(filename: str) -> str:
    return _AUDIO_MIME_BY_EXT.get(Path(filename).suffix.lower(), "application/octet-stream")


def _feature_name(obs: Observation) -> str:
    """note (truncated) -> os_grid_ref -> obs_id - same fallback chain and truncate-
    before-escape ordering as core/kml.py's _placemark_name (note's "no note"
    sentinel is '', not None - a truthiness check, not `is not None`).
    """
    if obs.note:
        text = obs.note.strip()
        if len(text) > _NAME_TRUNCATE_LEN:
            text = text[: _NAME_TRUNCATE_LEN - 1].rstrip() + "…"
        return text
    if obs.os_grid_ref:
        return obs.os_grid_ref
    return obs.obs_id


def _to_latlng(position: tuple[float, float]) -> list[float]:
    # Leaflet (like most JS mapping libraries) takes [lat, lng] - the OPPOSITE of our
    # (lon, lat) GeoJSON/RFC 7946 internal representation and of core/kml.py's KML
    # output (which wants (lon, lat) and must NOT swap). This is the one place in
    # this module that must swap - do not "fix" it to match kml.py's order.
    lon, lat = position
    return [lat, lon]


def geometry_to_leaflet(geometry: Geometry) -> dict:
    """Public (not underscore-prefixed) so the coordinate-order swap gets a direct
    unit test independent of a full document build - see core/kml.py's
    geometry_to_kml for the equivalent, non-swapping KML version.
    """
    if geometry.type is GeometryType.POINT:
        return {"type": "point", "coords": _to_latlng(geometry.coordinates)}
    if geometry.type is GeometryType.LINE_STRING:
        return {"type": "line", "coords": [_to_latlng(p) for p in geometry.coordinates]}
    if geometry.type is GeometryType.POLYGON:
        return {
            "type": "polygon",
            "coords": [[_to_latlng(p) for p in ring] for ring in geometry.coordinates],
        }
    raise AssertionError(f"unreachable geometry type {geometry.type}")  # pragma: no cover


def build_data_uris(zf: zipfile.ZipFile, refs: list, *, photo_optimization) -> dict[str, str]:
    """Map each unique zip_entry -> a `data:<mime>;base64,...` URI. Dedupes by
    zip_entry (mirrors core/kml.py's build_media_manifest) so a file referenced more
    than once is only read/encoded/optimized once. Photos are always embedded as
    image/jpeg: optimize_photo_bytes() always re-encodes to JPEG when it succeeds,
    and the handoff format only ever puts .jpg files under photos/ in the first
    place, so there's no real case where a photo is anything else.
    """
    uris: dict[str, str] = {}
    for ref in refs:
        if ref.zip_entry in uris:
            continue
        data = zf.read(ref.zip_entry)
        if ref.kind == "photo":
            mime = "image/jpeg"
            if photo_optimization is not None:
                data = optimize_photo_bytes(data, photo_optimization)
        else:
            mime = _audio_mime(ref.filename)
        encoded = base64.b64encode(data).decode("ascii")
        uris[ref.zip_entry] = f"data:{mime};base64,{encoded}"
    return uris


def _popup_html(
    obs: Observation,
    photo_refs: tuple[MediaRef, ...],
    audio_ref: MediaRef | None,
    data_uris: dict[str, str],
) -> str:
    parts = [f"<b>{_html_escape(_feature_name(obs))}</b>"]
    if obs.note:
        parts.append(f"<p>{_html_escape(obs.note)}</p>")
    parts.append(f"<p><b>Recorded:</b> {_html_escape(obs.recorded_at.isoformat())}</p>")
    if obs.os_grid_ref:
        parts.append(f"<p><b>Grid ref:</b> {_html_escape(obs.os_grid_ref)}</p>")
    parts.append(f"<p><b>GPS accuracy:</b> ±{obs.gps_accuracy_m:.1f} m</p>")
    if obs.heading_deg is not None:
        parts.append(f"<p><b>Heading:</b> {obs.heading_deg:.0f}°</p>")
    if photo_refs:
        width = 280 if len(photo_refs) == 1 else 130
        imgs = "".join(
            f'<img src="{data_uris[ref.zip_entry]}" width="{width}" '
            'style="margin:2px;border-radius:4px;"/>'
            for ref in photo_refs
        )
        parts.append(f"<div>{imgs}</div>")
    if audio_ref is not None:
        parts.append(
            '<audio controls style="width:100%;margin-top:6px;">'
            f'<source src="{data_uris[audio_ref.zip_entry]}"></audio>'
        )
    return "".join(parts)


def build_html_document(
    export: SurveyExport,
    zf: zipfile.ZipFile,
    *,
    photo_optimization: PhotoOptimization | None = DEFAULT_PHOTO_OPTIMIZATION,
) -> str:
    """Returns the complete, self-contained HTML document as a string. Propagates
    MediaJoinError from resolve_media() unchanged, same as core/kml.py.
    """
    media_by_obs = {obs.obs_id: resolve_media(obs, zf) for obs in export.observations}

    all_refs: list[MediaRef] = []
    for photo_refs, audio_ref in media_by_obs.values():
        all_refs.extend(photo_refs)
        if audio_ref is not None:
            all_refs.append(audio_ref)
    data_uris = build_data_uris(zf, all_refs, photo_optimization=photo_optimization)

    features = []
    for obs in export.observations:
        photo_refs, audio_ref = media_by_obs[obs.obs_id]
        features.append(
            {
                "geom": geometry_to_leaflet(obs.geometry),
                "popupHtml": _popup_html(obs, photo_refs, audio_ref, data_uris),
            }
        )

    # Embedded inside an inline <script> block below - '</' -> '<\/' so a literal
    # '</script' inside a surveyor's note (the only field free-text enough to
    # contain it - base64 data URIs can never contain '<', so photos/audio are not
    # a risk here) can't prematurely close the script element. '\/' is a legal JSON
    # escape for a plain '/', so this can't corrupt the JSON itself.
    payload = json.dumps(features, ensure_ascii=False).replace("</", "<\\/")
    tile_url_js = json.dumps(_TILE_URL)
    tile_attribution_js = json.dumps(_TILE_ATTRIBUTION)
    title = _html_escape(export.session.name)

    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        f"<title>{title}</title>"
        f'<link rel="stylesheet" href="{LEAFLET_CSS}"/>'
        "<style>html,body,#map{height:100%;margin:0;}"
        "body{font-family:sans-serif;}</style>"
        "</head><body>"
        '<div id="map"></div>'
        f'<script src="{LEAFLET_JS}"></script>'
        "<script>"
        f"const FEATURES = {payload};"
        "const map = L.map('map');"
        f"L.tileLayer({tile_url_js}, "
        f"{{attribution: {tile_attribution_js}, maxZoom: 19}}).addTo(map);"
        "const layer = L.featureGroup();"
        "FEATURES.forEach(function (f) {"
        "  var shape;"
        "  if (f.geom.type === 'point') { shape = L.marker(f.geom.coords); }"
        "  else if (f.geom.type === 'line') { shape = L.polyline(f.geom.coords); }"
        "  else { shape = L.polygon(f.geom.coords); }"
        "  shape.bindPopup(f.popupHtml, {maxWidth: 320});"
        "  shape.addTo(layer);"
        "});"
        "layer.addTo(map);"
        "if (FEATURES.length) {"
        "  map.fitBounds(layer.getBounds(), {padding: [20, 20]});"
        "} else {"
        "  map.setView([0, 0], 2);"
        "}"
        "</script>"
        "</body></html>"
    )


@dataclass(frozen=True)
class HtmlSummary:
    session_name: str
    observation_counts: dict[GeometryType, int]
    photo_count: int
    audio_count: int
    output_size_bytes: int


def write_html(
    export: SurveyExport,
    zf: zipfile.ZipFile,
    out_path: Path,
    *,
    photo_optimization: PhotoOptimization | None = DEFAULT_PHOTO_OPTIMIZATION,
) -> HtmlSummary:
    html = build_html_document(export, zf, photo_optimization=photo_optimization)
    out_path.write_text(html, encoding="utf-8")

    counts: dict[GeometryType, int] = {t: 0 for t in GeometryType}
    photo_count = audio_count = 0
    for obs in export.observations:
        counts[obs.geometry.type] += 1
        photo_count += len(obs.photos)
        if obs.audio is not None:
            audio_count += 1

    return HtmlSummary(
        session_name=export.session.name,
        observation_counts=counts,
        photo_count=photo_count,
        audio_count=audio_count,
        output_size_bytes=out_path.stat().st_size,
    )
