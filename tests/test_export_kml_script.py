"""CLI-level tests for scripts/export_kml.py - calls main() directly (a plain
function, no subprocess needed) to exercise argument parsing, error handling, and
the success path end-to-end.
"""
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import export_kml  # noqa: E402

from tests.fixtures import build_zips  # noqa: E402

PIL_Image = pytest.importorskip("PIL.Image")


def _write_photo_zip(tmp_path: Path, *, photo_size=(2000, 1500), quality=95) -> Path:
    """A minimal real zip (not a fixture-builder call) with one point observation and
    a genuinely decodable JPEG - needed to exercise --no-optimize-photos/
    --max-photo-dimension/--photo-quality end-to-end, which the fake placeholder
    bytes other fixtures use (fast, but not real images) can't exercise. Filled with
    random noise, not a solid colour: a flat-colour JPEG compresses to almost nothing
    regardless of resolution/quality (near-zero entropy), which would make any
    size-threshold assertion meaningless - noise is a closer (worst-case) stand-in
    for a real photo's size behaviour.
    """
    w, h = photo_size
    img = PIL_Image.frombytes("RGB", photo_size, os.urandom(w * h * 3))
    photo_buf = io.BytesIO()
    img.save(photo_buf, format="JPEG", quality=quality)

    props = dict(build_zips._FULL_PROPS_TEMPLATE)
    props.update(obs_id="01PHOTOSIZEOBS000000001", photo="big.jpg", photos=[{"photo": "big.jpg"}])
    doc = {
        "type": "FeatureCollection",
        "survey_session": {**build_zips._BASE_SESSION, "id": "01PHOTOSIZESESSION00001"},
        "features": [
            {"type": "Feature", "geometry": build_zips._point(-0.1, 51.5), "properties": props}
        ],
    }
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("session.geojson", json.dumps(doc))
        zf.writestr("photos/big.jpg", photo_buf.getvalue())
    return zip_path


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


def test_photos_are_downscaled_by_default(tmp_path):
    zip_path = _write_photo_zip(tmp_path)

    exit_code = export_kml.main([str(zip_path)])

    assert exit_code == 0
    with zipfile.ZipFile(zip_path.with_suffix(".kmz")) as out:
        embedded = out.read("files/big.jpg")
    with PIL_Image.open(io.BytesIO(embedded)) as img:
        assert max(img.size) <= export_kml.kml.DEFAULT_PHOTO_OPTIMIZATION.max_dimension


def test_no_optimize_photos_flag_embeds_original(tmp_path):
    zip_path = _write_photo_zip(tmp_path)
    with zipfile.ZipFile(zip_path) as zf:
        original = zf.read("photos/big.jpg")

    exit_code = export_kml.main([str(zip_path), "--no-optimize-photos"])

    assert exit_code == 0
    with zipfile.ZipFile(zip_path.with_suffix(".kmz")) as out:
        assert out.read("files/big.jpg") == original


def test_max_photo_dimension_and_quality_flags_are_applied(tmp_path):
    zip_path = _write_photo_zip(tmp_path)

    exit_code = export_kml.main(
        [str(zip_path), "--max-photo-dimension", "300", "--photo-quality", "40"]
    )

    assert exit_code == 0
    with zipfile.ZipFile(zip_path.with_suffix(".kmz")) as out:
        embedded = out.read("files/big.jpg")
    with PIL_Image.open(io.BytesIO(embedded)) as img:
        assert max(img.size) <= 300


def test_warns_when_output_still_exceeds_threshold(tmp_path, capsys):
    zip_path = _write_photo_zip(tmp_path)

    exit_code = export_kml.main([str(zip_path), "--warn-over-mb", "0.001"])

    assert exit_code == 0  # still succeeds - this is advisory, not a failure
    assert "warning" in capsys.readouterr().err.lower()


def test_zero_warn_over_mb_disables_the_warning(tmp_path, capsys):
    zip_path = _write_photo_zip(tmp_path)

    exit_code = export_kml.main([str(zip_path), "--warn-over-mb", "0"])

    assert exit_code == 0
    assert capsys.readouterr().err == ""
