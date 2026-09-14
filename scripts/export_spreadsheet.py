"""Turns a Field Survey zip export into a shareable bundle for people who just want
the photos and a spreadsheet: one zip holding <name>.xlsx + <name>.csv (a curated
subset of the observation properties - note, lat, lon, os_grid_ref, photo,
heading_deg, a 16-point compass `direction`, and recorded_date/recorded_time in
local time), every photo under photos/ (original bytes, untouched), and any extra
files passed via --include (typically the HTML map from scripts/export_html.py).
Standalone script, deliberately NOT a QGIS plugin action - runs under the plain
.venv (needs openpyxl and tzdata - `pip install -r requirements.txt`).

    .venv\\Scripts\\python.exe scripts\\export_spreadsheet.py <zip_path> [-o out.zip]
        [--name cissbury_survey_2026_09_04] [--include map.html ...] [--timezone Europe/London]
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # so field_survey_import resolves uninstalled,
                                     # matching scripts/export_html.py's pattern

from field_survey_import.core import reader, spreadsheet  # noqa: E402
from field_survey_import.core.errors import MediaJoinError, SurveyFormatError  # noqa: E402

_BYTES_PER_MB = 1024 * 1024


def _default_output_path(zip_path: Path) -> Path:
    return zip_path.with_name(f"{zip_path.stem}-spreadsheet.zip")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a Field Survey zip export into a zip of spreadsheet "
        "(.xlsx + .csv) plus the original photos, optionally with extra files bundled in."
    )
    parser.add_argument("zip_path", type=Path, help="Path to the Field Survey export zip")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output .zip path (default: <zip stem>-spreadsheet.zip next to the input zip)",
    )
    parser.add_argument(
        "--name", default=None,
        help="Basename for the .xlsx/.csv inside the bundle (default: the output zip's stem)",
    )
    parser.add_argument(
        "--include", type=Path, action="append", default=[], metavar="PATH",
        help="Extra file to add to the bundle at its basename (repeatable), e.g. the "
        ".html map from scripts/export_html.py",
    )
    parser.add_argument(
        "--timezone", default=spreadsheet.DEFAULT_TIMEZONE,
        help="IANA timezone for recorded_date/recorded_time (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    out_path = args.output if args.output is not None else _default_output_path(args.zip_path)
    name = args.name if args.name is not None else out_path.stem

    for path in args.include:
        if not path.is_file():
            print(f"error: --include {path}: no such file", file=sys.stderr)
            return 1

    try:
        with zipfile.ZipFile(args.zip_path) as zf:
            export = reader.read_export(zf)
            summary = spreadsheet.write_bundle(
                export, zf, out_path, name=name, tz=args.timezone, include=args.include,
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
    except ImportError as exc:
        print(
            f"error: {exc} - install the script's dependencies with "
            "`.venv\\Scripts\\python.exe -m pip install -r requirements.txt`",
            file=sys.stderr,
        )
        return 1
    except ZoneInfoNotFoundError as exc:
        print(
            f"error: unknown timezone {exc} - check the --timezone name, and on Windows "
            "make sure the `tzdata` package is installed (`pip install -r requirements.txt`)",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    size_mb = summary.output_size_bytes / _BYTES_PER_MB
    print(f"Wrote {out_path} ({size_mb:.1f} MB)")
    print(f"  {summary.session_name!r}: {summary.row_count} rows in {name}.xlsx / {name}.csv")
    print(f"  {summary.photo_count} photos under photos/")
    if summary.included_files:
        print(f"  also included: {', '.join(summary.included_files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
