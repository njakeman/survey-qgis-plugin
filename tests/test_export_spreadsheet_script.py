"""CLI-level tests for scripts/export_spreadsheet.py - calls main() directly (a plain
function, no subprocess needed).
"""
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import export_spreadsheet  # noqa: E402

from tests.fixtures import build_zips  # noqa: E402

pytest.importorskip("openpyxl")


def test_success_writes_bundle_and_prints_summary(tmp_path, capsys):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_multi_photo_zip())

    exit_code = export_spreadsheet.main([str(zip_path)])

    assert exit_code == 0
    out_path = tmp_path / "export-spreadsheet.zip"
    assert out_path.exists()
    with zipfile.ZipFile(out_path) as bundle:
        names = bundle.namelist()
    assert "export-spreadsheet.xlsx" in names
    assert "export-spreadsheet.csv" in names
    assert sum(n.startswith("photos/") for n in names) == 7

    captured = capsys.readouterr()
    assert str(out_path) in captured.out
    assert "3 rows" in captured.out
    assert "7 photos" in captured.out


def test_output_name_and_include_flags(tmp_path, capsys):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_map_point_zip())
    html = tmp_path / "map.html"
    html.write_text("<!doctype html>", encoding="utf-8")
    out_path = tmp_path / "bundle.zip"

    exit_code = export_spreadsheet.main([
        str(zip_path), "-o", str(out_path), "--name", "my_survey", "--include", str(html),
    ])

    assert exit_code == 0
    with zipfile.ZipFile(out_path) as bundle:
        names = set(bundle.namelist())
    assert {"my_survey.xlsx", "my_survey.csv", "map.html"} <= names
    assert "also included: map.html" in capsys.readouterr().out


def test_timezone_flag_is_applied(tmp_path):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_map_point_zip())  # recorded_at 09:05:00Z

    exit_code = export_spreadsheet.main([str(zip_path), "--timezone", "Asia/Tokyo"])

    assert exit_code == 0
    with zipfile.ZipFile(tmp_path / "export-spreadsheet.zip") as bundle:
        csv_text = bundle.read("export-spreadsheet.csv").decode("utf-8-sig")
    assert ",18:05:00" in csv_text


def test_unknown_timezone_exits_nonzero(tmp_path, capsys):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_map_point_zip())

    exit_code = export_spreadsheet.main([str(zip_path), "--timezone", "Not/AZone"])

    assert exit_code == 1
    assert "unknown timezone" in capsys.readouterr().err


def test_missing_include_file_exits_nonzero(tmp_path, capsys):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_map_point_zip())

    exit_code = export_spreadsheet.main([str(zip_path), "--include", str(tmp_path / "nope")])

    assert exit_code == 1
    assert "no such file" in capsys.readouterr().err


def test_non_zip_file_exits_nonzero(tmp_path, capsys):
    bogus = tmp_path / "not-a-zip.zip"
    bogus.write_bytes(b"not a zip file")

    exit_code = export_spreadsheet.main([str(bogus)])

    assert exit_code == 1
    assert "not a valid zip file" in capsys.readouterr().err
