"""CLI-level tests for scripts/export_html.py - calls main() directly (a plain
function, no subprocess needed).
"""
import base64
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import export_html  # noqa: E402

from tests.fixtures import build_zips  # noqa: E402

PIL_Image = pytest.importorskip("PIL.Image")

_PAYLOAD_RE = re.compile(r"const FEATURES = (\[.*?\]);const map", re.S)


def _write_photo_zip(tmp_path: Path, *, photo_size=(2000, 1500), quality=95) -> Path:
    """Same rationale as tests/test_export_kml_script.py's helper of the same name -
    a genuinely decodable, noise-filled (not solid colour) JPEG is needed to exercise
    real downscaling/size behaviour.
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


def _embedded_photo_bytes(html_path: Path) -> bytes:
    html = html_path.read_text(encoding="utf-8")
    features = json.loads(_PAYLOAD_RE.search(html).group(1))
    b64 = features[0]["popupHtml"].split("base64,", 1)[1].split('"', 1)[0]
    return base64.b64decode(b64)


def test_success_writes_html_and_prints_summary(tmp_path, capsys):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_multi_photo_zip())

    exit_code = export_html.main([str(zip_path)])

    assert exit_code == 0
    out_path = zip_path.with_suffix(".html")
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8").startswith("<!doctype html>")

    captured = capsys.readouterr()
    assert str(out_path) in captured.out
    assert "3 points" in captured.out
    assert "7 photos" in captured.out


def test_custom_output_path(tmp_path):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_map_point_zip())
    out_path = tmp_path / "custom.html"

    exit_code = export_html.main([str(zip_path), "-o", str(out_path)])

    assert exit_code == 0
    assert out_path.exists()


def test_basemap_flag_defaults_to_openfreemap_liberty(tmp_path):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_map_point_zip())

    exit_code = export_html.main([str(zip_path)])

    assert exit_code == 0
    html = zip_path.with_suffix(".html").read_text(encoding="utf-8")
    assert "styles/liberty" in html


def test_basemap_flag_esri_wires_through(tmp_path):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_map_point_zip())

    exit_code = export_html.main([str(zip_path), "--basemap", "esri"])

    assert exit_code == 0
    html = zip_path.with_suffix(".html").read_text(encoding="utf-8")
    assert "server.arcgisonline.com" in html
    assert "maplibre" not in html.lower()


def test_invalid_basemap_choice_exits_via_argparse(tmp_path, capsys):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(build_zips.build_map_point_zip())

    with pytest.raises(SystemExit) as exc_info:
        export_html.main([str(zip_path), "--basemap", "not-a-real-basemap"])

    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_non_zip_file_exits_nonzero(tmp_path, capsys):
    bogus = tmp_path / "not-a-zip.zip"
    bogus.write_bytes(b"not a zip file")

    exit_code = export_html.main([str(bogus)])

    assert exit_code == 1
    assert "not a valid zip file" in capsys.readouterr().err


def test_photos_are_downscaled_by_default(tmp_path):
    zip_path = _write_photo_zip(tmp_path)

    exit_code = export_html.main([str(zip_path)])

    assert exit_code == 0
    embedded = _embedded_photo_bytes(zip_path.with_suffix(".html"))
    with PIL_Image.open(io.BytesIO(embedded)) as img:
        assert max(img.size) <= export_html.html_map.DEFAULT_PHOTO_OPTIMIZATION.max_dimension


def test_no_optimize_photos_flag_embeds_original(tmp_path):
    zip_path = _write_photo_zip(tmp_path)
    with zipfile.ZipFile(zip_path) as zf:
        original = zf.read("photos/big.jpg")

    exit_code = export_html.main([str(zip_path), "--no-optimize-photos"])

    assert exit_code == 0
    assert _embedded_photo_bytes(zip_path.with_suffix(".html")) == original


def test_max_photo_dimension_and_quality_flags_are_applied(tmp_path):
    zip_path = _write_photo_zip(tmp_path)

    exit_code = export_html.main(
        [str(zip_path), "--max-photo-dimension", "300", "--photo-quality", "40"]
    )

    assert exit_code == 0
    embedded = _embedded_photo_bytes(zip_path.with_suffix(".html"))
    with PIL_Image.open(io.BytesIO(embedded)) as img:
        assert max(img.size) <= 300
