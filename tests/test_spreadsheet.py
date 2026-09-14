"""core/spreadsheet.py - the curated-columns spreadsheet + photos bundle export."""
import csv
import datetime as dt
import io
import json
import zipfile

import pytest

from field_survey_import.core import reader, spreadsheet
from field_survey_import.core.errors import MediaJoinError
from tests.fixtures import build_zips

EXPECTED_COLUMNS = (
    "note", "lat", "lon", "os_grid_ref", "photo", "heading_deg", "direction",
    "recorded_date", "recorded_time",
)


def _export_from_bytes(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return reader.read_export(zf)


def _single_obs_zip(**props) -> bytes:
    doc = {
        "type": "FeatureCollection",
        "survey_session": {**build_zips._BASE_SESSION, "id": "01SPREADSHEETSESSION00001"},
        "features": [build_zips._feature(build_zips._point(-0.387, 50.8596), **props)],
    }
    return build_zips._zip_bytes(doc)


# --- compass ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "heading, expected",
    [
        (0.0, "n"), (11.24, "n"), (11.25, "nne"), (45.0, "ne"), (90.0, "e"),
        (132.25, "se"), (180.0, "s"), (270.0, "w"), (348.74, "nnw"), (348.75, "n"),
        (349.83, "n"), (359.9, "n"), (360.0, "n"),
    ],
)
def test_compass_direction_16_point_lowercase(heading, expected):
    assert spreadsheet.compass_direction(heading) == expected


def test_compass_direction_none_is_empty_string():
    assert spreadsheet.compass_direction(None) == ""


# --- rows -----------------------------------------------------------------------

def test_columns_are_the_curated_subset_in_order():
    assert spreadsheet.COLUMNS == EXPECTED_COLUMNS


def test_build_rows_converts_recorded_at_to_uk_local_time_in_summer():
    export = _export_from_bytes(_single_obs_zip(
        recorded_at="2026-09-04T09:08:32.569Z", heading_deg=132.246, lat=50.8596, lon=-0.387,
        note="Heavily vegetated", os_grid_ref="TQ 13621 07911",
        photo="p1.jpg", photos=[{"photo": "p1.jpg", "ref_photo": None}],
    ))

    rows = spreadsheet.build_rows(export, tz="Europe/London")

    assert len(rows) == 1
    row = rows[0]
    assert tuple(row) == EXPECTED_COLUMNS
    assert row["note"] == "Heavily vegetated"
    assert row["lat"] == pytest.approx(50.8596)
    assert row["lon"] == pytest.approx(-0.387)
    assert row["os_grid_ref"] == "TQ 13621 07911"
    assert row["photo"] == "p1.jpg"
    assert row["heading_deg"] == pytest.approx(132.246)
    assert row["direction"] == "se"
    assert row["recorded_date"] == dt.date(2026, 9, 4)
    assert row["recorded_time"] == dt.time(10, 8, 32)  # BST = UTC+1


def test_build_rows_winter_date_stays_on_utc():
    export = _export_from_bytes(_single_obs_zip(recorded_at="2026-01-15T09:05:00.000Z"))

    row = spreadsheet.build_rows(export, tz="Europe/London")[0]

    assert row["recorded_time"] == dt.time(9, 5, 0)


def test_build_rows_utc_timezone_is_passthrough():
    export = _export_from_bytes(_single_obs_zip(recorded_at="2026-09-04T23:30:00.000Z"))

    row = spreadsheet.build_rows(export, tz="UTC")[0]

    assert row["recorded_date"] == dt.date(2026, 9, 4)
    assert row["recorded_time"] == dt.time(23, 30, 0)


def test_build_rows_null_optional_fields_become_empty_strings():
    export = _export_from_bytes(_single_obs_zip(heading_deg=None, os_grid_ref=None, photo=None))

    row = spreadsheet.build_rows(export, tz="UTC")[0]

    assert row["heading_deg"] == ""
    assert row["direction"] == ""
    assert row["os_grid_ref"] == ""
    assert row["photo"] == ""


def test_build_rows_multi_photo_cell_lists_every_photo_in_order():
    export = _export_from_bytes(build_zips.build_multi_photo_zip())

    rows = spreadsheet.build_rows(export, tz="UTC")

    assert rows[0]["photo"] == "01MULTIPHOTOFILE000000001.jpg; 01MULTIPHOTOFILE000000002.jpg"
    assert rows[2]["photo"] == "01MULTIPHOTOFILE000000007.jpg"


# --- csv / xlsx -------------------------------------------------------------------

def test_write_csv_header_and_stringified_values(tmp_path):
    export = _export_from_bytes(_single_obs_zip(
        recorded_at="2026-09-04T09:08:32.569Z", heading_deg=132.246, note="Note, with comma",
    ))
    rows = spreadsheet.build_rows(export, tz="Europe/London")
    out = tmp_path / "s.csv"

    spreadsheet.write_csv(rows, out)

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM so Excel opens it as UTF-8
    with open(out, encoding="utf-8-sig", newline="") as fh:
        parsed = list(csv.DictReader(fh))
    assert list(parsed[0]) == list(EXPECTED_COLUMNS)
    assert parsed[0]["note"] == "Note, with comma"
    assert parsed[0]["recorded_date"] == "2026-09-04"
    assert parsed[0]["recorded_time"] == "10:08:32"
    assert parsed[0]["direction"] == "se"
    assert float(parsed[0]["heading_deg"]) == pytest.approx(132.246)


def test_write_xlsx_round_trips_typed_cells(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    export = _export_from_bytes(_single_obs_zip(
        recorded_at="2026-09-04T09:08:32.569Z", heading_deg=132.246, note="Heavily vegetated",
    ))
    rows = spreadsheet.build_rows(export, tz="Europe/London")
    out = tmp_path / "s.xlsx"

    spreadsheet.write_xlsx(rows, out)

    ws = openpyxl.load_workbook(out).active
    header = [c.value for c in ws[1]]
    assert header == list(EXPECTED_COLUMNS)
    data = {h: c for h, c in zip(header, ws[2])}
    assert data["note"].value == "Heavily vegetated"
    assert isinstance(data["lat"].value, float)
    assert data["direction"].value == "se"
    assert data["recorded_date"].value == dt.datetime(2026, 9, 4)  # Excel dates load as datetime
    assert data["recorded_date"].number_format == "yyyy-mm-dd"
    assert data["recorded_time"].value == dt.time(10, 8, 32)
    assert data["recorded_time"].number_format == "hh:mm:ss"


# --- bundle ---------------------------------------------------------------------

def test_write_bundle_contents(tmp_path):
    pytest.importorskip("openpyxl")
    src = tmp_path / "export.zip"
    src.write_bytes(build_zips.build_multi_photo_zip())
    extra = tmp_path / "sub" / "map.html"
    extra.parent.mkdir()
    extra.write_text("<!doctype html>", encoding="utf-8")
    out = tmp_path / "bundle.zip"

    with zipfile.ZipFile(src) as zf:
        export = reader.read_export(zf)
        summary = spreadsheet.write_bundle(
            export, zf, out, name="my_survey", tz="UTC", include=[extra],
        )
        source_photo = zf.read("photos/01MULTIPHOTOFILE000000001.jpg")

    with zipfile.ZipFile(out) as bundle:
        names = bundle.namelist()
        assert "my_survey.xlsx" in names
        assert "my_survey.csv" in names
        assert "map.html" in names
        photos = sorted(n for n in names if n.startswith("photos/"))
        assert photos == sorted(f"photos/01MULTIPHOTOFILE00000000{i}.jpg" for i in range(1, 8))
        assert all("\\" not in n for n in names)
        assert bundle.read("photos/01MULTIPHOTOFILE000000001.jpg") == source_photo
        assert bundle.read("map.html") == b"<!doctype html>"

    assert summary.session_name == "Synthetic fixture"
    assert summary.row_count == 3
    assert summary.photo_count == 7
    assert summary.included_files == ("map.html",)
    assert summary.output_size_bytes == out.stat().st_size


def test_write_bundle_include_colliding_with_spreadsheet_name_is_rejected(tmp_path):
    pytest.importorskip("openpyxl")
    src = tmp_path / "export.zip"
    src.write_bytes(build_zips.build_map_point_zip())
    clash = tmp_path / "my_survey.csv"
    clash.write_text("x")

    with zipfile.ZipFile(src) as zf:
        export = reader.read_export(zf)
        with pytest.raises(ValueError, match="my_survey.csv"):
            spreadsheet.write_bundle(
                export, zf, tmp_path / "b.zip", name="my_survey", tz="UTC", include=[clash],
            )


def test_write_bundle_missing_photo_raises_media_join_error(tmp_path):
    pytest.importorskip("openpyxl")
    doc = json.loads(
        zipfile.ZipFile(io.BytesIO(build_zips.build_multi_photo_zip())).read("session.geojson")
    )
    src = tmp_path / "export.zip"
    with zipfile.ZipFile(src, "w") as zf:  # geojson only - every photo missing
        zf.writestr("session.geojson", json.dumps(doc))

    with zipfile.ZipFile(src) as zf:
        export = reader.read_export(zf)
        with pytest.raises(MediaJoinError):
            spreadsheet.write_bundle(export, zf, tmp_path / "b.zip", name="s", tz="UTC")

