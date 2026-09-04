"""CLI-level tests for scripts/export_kml.py - calls main() directly (a plain
function, no subprocess needed) to exercise argument parsing, error handling, and
the success path end-to-end.
"""
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import export_kml  # noqa: E402

from tests.fixtures import build_zips  # noqa: E402


def test_success_writes_kmz_and_prints_summary(tmp_path, capsys):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_multi_photo_zip())

    exit_code = export_kml.main([str(zip_path)])

    assert exit_code == 0
    out_path = zip_path.with_suffix(".kmz")
    assert out_path.exists()
    with zipfile.ZipFile(out_path) as zf:
        assert "doc.kml" in zf.namelist()

    captured = capsys.readouterr()
    assert str(out_path) in captured.out
    assert "3 points" in captured.out
    assert "7 photos" in captured.out


def test_custom_output_path(tmp_path):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_map_point_zip())
    out_path = tmp_path / "custom.kmz"

    exit_code = export_kml.main([str(zip_path), "-o", str(out_path)])

    assert exit_code == 0
    assert out_path.exists()


def test_non_zip_file_exits_nonzero(tmp_path, capsys):
    bogus = tmp_path / "not-a-zip.zip"
    bogus.write_bytes(b"not a zip file")

    exit_code = export_kml.main([str(bogus)])

    assert exit_code == 1
    assert "not a valid zip file" in capsys.readouterr().err


def test_missing_input_path_exits_nonzero(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.zip"

    exit_code = export_kml.main([str(missing)])

    assert exit_code == 1
    assert capsys.readouterr().err  # some error message was printed
