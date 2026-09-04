"""Turns a Field Survey zip export into a KMZ shareable via Google Earth/Google Maps.
Standalone script, deliberately NOT a QGIS plugin action - runs under the plain
.venv (field_survey_import/core is zero-dependency stdlib Python).

    .venv\\Scripts\\python.exe scripts\\export_kml.py <zip_path> [-o output.kmz]

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
    args = parser.parse_args(argv)
    out_path = args.output if args.output is not None else _default_output_path(args.zip_path)

    try:
        with zipfile.ZipFile(args.zip_path) as zf:
            export = reader.read_export(zf)
            summary = kml.write_kmz(export, zf, out_path)
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
    print(f"Wrote {out_path}")
    print(
        f"  {summary.session_name!r}: "
        f"{counts.get(GeometryType.POINT, 0)} points, "
        f"{counts.get(GeometryType.LINE_STRING, 0)} paths, "
        f"{counts.get(GeometryType.POLYGON, 0)} boundaries"
    )
    print(f"  {summary.photo_count} photos, {summary.audio_count} audio files embedded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
