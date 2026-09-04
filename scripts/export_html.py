"""Turns a Field Survey zip export into a single self-contained HTML map - the
option for Google My Maps (which never renders photos bundled inside a KMZ, only
externally-hosted image URLs - see scripts/export_kml.py) or anyone without Google
Earth. Standalone script, deliberately NOT a QGIS plugin action - runs under the
plain .venv (needs Pillow for photo downscaling - `pip install -r requirements.txt`).

    .venv\\Scripts\\python.exe scripts\\export_html.py <zip_path> [-o output.html]

The finished .html opens in any browser, no install needed - just double-click it or
open it as a file:// URL. It still loads its map tiles (OpenStreetMap) and the
Leaflet library from a CDN over the *viewer's* own internet connection when opened;
every photo and audio file is embedded directly in the file itself (base64 data
URIs), so nothing else needs to be shared alongside it.

Photos are downscaled/recompressed by default before embedding - see
core/html_map.py / core/photo_optimize.py. Use --no-optimize-photos to embed
originals unchanged, or --max-photo-dimension/--photo-quality to tune the trade-off.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # so field_survey_import resolves uninstalled,
                                     # matching scripts/build_styles.py's pattern

from field_survey_import.core import html_map, reader  # noqa: E402
from field_survey_import.core.errors import MediaJoinError, SurveyFormatError  # noqa: E402
from field_survey_import.core.model import GeometryType  # noqa: E402
from field_survey_import.core.photo_optimize import PhotoOptimization  # noqa: E402

_BYTES_PER_MB = 1024 * 1024


def _default_output_path(zip_path: Path) -> Path:
    return zip_path.with_suffix(".html")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a Field Survey zip export into a shareable, "
        "self-contained HTML map (works in any browser, including Google My Maps "
        "users who can't see photos embedded in a KMZ)."
    )
    parser.add_argument("zip_path", type=Path, help="Path to the Field Survey export zip")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output .html path (default: <zip stem>.html next to the input zip)",
    )
    parser.add_argument(
        "--no-optimize-photos", action="store_true",
        help="Embed photos at their original resolution/quality (default: downscale "
        "to fit --max-photo-dimension at --photo-quality - see module docstring)",
    )
    parser.add_argument(
        "--max-photo-dimension", type=int,
        default=html_map.DEFAULT_PHOTO_OPTIMIZATION.max_dimension,
        help="Longest edge, in pixels, to downscale embedded photos to "
        "(default: %(default)s; larger originals are never upscaled)",
    )
    parser.add_argument(
        "--photo-quality", type=int, default=html_map.DEFAULT_PHOTO_OPTIMIZATION.quality,
        help="JPEG quality (1-95) for re-encoded photos (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    out_path = args.output if args.output is not None else _default_output_path(args.zip_path)
    photo_optimization = (
        None
        if args.no_optimize_photos
        else PhotoOptimization(max_dimension=args.max_photo_dimension, quality=args.photo_quality)
    )

    try:
        with zipfile.ZipFile(args.zip_path) as zf:
            export = reader.read_export(zf)
            summary = html_map.write_html(
                export, zf, out_path, photo_optimization=photo_optimization
            )
    # MediaJoinError is a SurveyFormatError subclass - it MUST be caught first, or
    # this branch is unreachable and every media problem reports as a generic format
    # error instead of naming the missing file.
    except MediaJoinError as exc:
        print(f"error: referenced media missing from the zip: {exc}", file=sys.stderr)
        return 1
    except SurveyFormatError as exc:
        print(f"error: not a valid Field Survey export: {exc}", file=sys.stderr)
        return 1
    except zipfile.BadZipFile as exc:
        print(f"error: not a valid zip file: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    counts = summary.observation_counts
    size_mb = summary.output_size_bytes / _BYTES_PER_MB
    print(f"Wrote {out_path} ({size_mb:.1f} MB)")
    print(
        f"  {summary.session_name!r}: "
        f"{counts.get(GeometryType.POINT, 0)} points, "
        f"{counts.get(GeometryType.LINE_STRING, 0)} paths, "
        f"{counts.get(GeometryType.POLYGON, 0)} boundaries"
    )
    print(f"  {summary.photo_count} photos, {summary.audio_count} audio files embedded")
    print("  Open it directly in a browser - map tiles need the viewer's own internet")
    print("  connection, but every photo/audio file is already embedded in the file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
