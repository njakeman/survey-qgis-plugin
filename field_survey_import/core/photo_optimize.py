"""Photo downscaling/recompression shared by every export format that embeds
photos directly in its output file (core/kml.py's KMZ, core/html_map.py's
self-contained HTML). Zero qgis/PyQt5 imports; Pillow is imported lazily, inside
`optimize_photo_bytes`, so this module - and anything that imports it - stays
importable without Pillow installed. Only actually optimizing a photo needs it.
"""
from __future__ import annotations  # `X | None` unions must stay lazy on Python 3.9

import io
from dataclasses import dataclass


@dataclass(frozen=True)
class PhotoOptimization:
    """Downscale/recompress settings for embedded photos. Every current consumer's
    balloon/popup only ever displays a photo at 400px (single hero) or 160px
    (gallery thumbnail) wide - a phone's full-resolution original (often 1600px+ and
    several hundred KB) is wasted size with no visible benefit, and for core/kml.py
    is usually what pushes a multi-photo session's .kmz over Google My Maps' 5MB
    upload limit. Defaults leave generous headroom over even a 2x-retina render of
    the largest (400px) display size while cutting a typical 1600x1200 phone JPEG
    by roughly 4-5x.
    """

    max_dimension: int = 1024
    quality: int = 75


DEFAULT_PHOTO_OPTIMIZATION = PhotoOptimization()


def optimize_photo_bytes(data: bytes, opt: PhotoOptimization) -> bytes:
    """Downscale (never upscale - Image.thumbnail is a no-op on a smaller image) and
    re-encode a photo as JPEG. Imports Pillow lazily, inside this function, so this
    module stays importable without Pillow installed - only actually optimizing a
    photo needs it (both export scripts' --no-optimize-photos skips this entirely).

    ImageOps.exif_transpose() bakes in the EXIF Orientation tag as real pixel
    rotation before re-encoding: Image.save() below doesn't carry EXIF over from the
    source (Pillow only writes EXIF when explicitly told to), so without this step a
    photo taken in portrait could be re-saved sideways. Losing the rest of the EXIF
    block (camera make/model, GPS) is a deliberate side effect, not a bug - none of
    it is needed once the surveyor's own recorded lat/lon is already in the
    placemark/marker, and dropping it is a small privacy win for a file meant to be
    shared.

    Falls back to the original bytes, unchanged, if Pillow can't decode the image
    (corrupt file, or a format Pillow doesn't handle) or if re-encoding somehow
    produces something no smaller than the original - optimisation must never make
    the output worse, and one bad photo must never fail the whole export.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")  # drop alpha/CMYK/palette oddities for JPEG output
            img.thumbnail((opt.max_dimension, opt.max_dimension), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=opt.quality, optimize=True)
            optimized = out.getvalue()
    except Exception:
        return data
    return optimized if len(optimized) < len(data) else data
