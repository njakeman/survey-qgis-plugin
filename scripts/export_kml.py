"""Turns a Field Survey zip export into a KMZ shareable via Google Earth/Google Maps.
Standalone script, deliberately NOT a QGIS plugin action - runs under the plain
.venv (field_survey_import/core is zero-dependency stdlib Python, except for the
photo downscaling below, which needs Pillow - `pip install -r requirements.txt`).

    .venv\\Scripts\\python.exe scripts\\export_kml.py <zip_path> [-o output.kmz]

Photos are downscaled/recompressed by default before embedding (kml.PhotoOptimization
- see its docstring): the balloon only ever displays a photo at 400px/160px wide, so a
phone's full-resolution original is wasted size and is usually what pushes a
multi-photo session's .kmz over Google My Maps' 5MB upload limit. Use
--no-optimize-photos to embed originals unchanged, or --max-photo-dimension/
--photo-quality to tune the trade-off.

Known limitation (documented, not solved here): Google My Maps' importer typically
does not render images embedded inside a KMZ's balloon HTML, only externally-hosted
image URLs. Google Earth (desktop/web/mobile) renders embedded KMZ images fine - this
script optimises for that.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # so field_survey_import resolves uninstalled,
                                     # matching scripts/build_styles.py's pattern

from field_survey_import.core import kml, reader  # noqa: E402
from field_survey_import.core.errors import MediaJoinError, SurveyFormatError  # noqa: E402
from field_survey_import.core.model import GeometryType  # noqa: E402

_BYTES_PER_MB = 1024 * 1024


def _default_output_path(zip_path: Path) -> Path:
    return zip_path.with_suffix(".kmz")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a Field Survey zip export into a shareable KMZ "
        "(Google Earth / Google Maps)."
    )
    parser.add_argument("zip_path", type=Path, help="Path to the Field Survey export zip")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output .kmz path (default: <zip stem>.kmz next to the input zip)",
    )
    parser.add_argument(
        "--no-optimize-photos", action="store_true",
        help="Embed photos at their original resolution/quality (default: downscale "
        "to fit --max-photo-dimension at --photo-quality - see module docstring)",
    )
    parser.add_argument(
        "--max-photo-dimension", type=int, default=kml.DEFAULT_PHOTO_OPTIMIZATION.max_dimension,
        help="Longest edge, in pixels, to downscale embedded photos to "
        "(default: %(default)s; larger originals are never upscaled)",
    )
    parser.add_argument(
        "--photo-quality", type=int, default=kml.DEFAULT_PHOTO_OPTIMIZATION.quality,
        help="JPEG quality (1-95) for re-encoded photos (default: %(default)s)",
    )
    parser.add_argument(
        "--warn-over-mb", type=float, default=5.0,
        help="Print a warning if the finished .kmz exceeds this size in MB - "
        "5MB is Google My Maps' upload limit (default: %(default)s; 0 disables)",
    )
    args = parser.parse_args(argv)
    out_path = args.output if args.output is not None else _default_output_path(args.zip_path)
    photo_optimization = (
        None
        if args.no_optimize_photos
        else kml.PhotoOptimization(
            max_dimension=args.max_photo_dimension, quality=args.photo_quality
        )
    )

    try:
        with zipfile.ZipFile(args.zip_path) as zf:
            export = reader.read_export(zf)
            summary = kml.write_kmz(
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
    if args.warn_over_mb and size_mb > args.warn_over_mb:
        print(
            f"warning: {size_mb:.1f} MB exceeds {args.warn_over_mb:g} MB (Google My "
            "Maps' upload limit) - try a lower --photo-quality or --max-photo-dimension",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
