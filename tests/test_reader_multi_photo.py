"""Verifies core/reader.py's handling of the `photos` array (handoff addendum) -
against the real fixture (multiple-photo-test-2026-08-25.zip), against sample.zip
(an old export with no `photos` key at all), and against every reader.py tolerance
rule the addendum documents.
"""
import io
import json
import zipfile

import pytest

from field_survey_import.core import reader
from field_survey_import.core.errors import SurveyFormatError
from field_survey_import.core.model import PhotoRef
from tests.fixtures import build_zips


def test_multi_photo_zip_counts_and_no_extra_properties(multi_photo_zip):
    export = reader.read_export(multi_photo_zip)
    assert [len(o.photos) for o in export.observations] == [2, 4, 1]
    assert sum(len(o.photos) for o in export.observations) == 7
    for obs in export.observations:
        # `photos` is now a known field (schema.py) - it must not leak into
        # extra_properties the way an unrecognised key would.
        assert obs.extra_properties == {}


def test_multi_photo_zip_scalar_photo_mirrors_first_array_entry(multi_photo_zip):
    export = reader.read_export(multi_photo_zip)
    for obs in export.observations:
        assert obs.photo == obs.photos[0].photo


def test_multi_photo_zip_obs_id_is_not_the_photo_basename(multi_photo_zip):
    # Re-confirms the §2/§8 "join by literal property value" rule for the new
    # array exactly as it already held for the old scalar.
    export = reader.read_export(multi_photo_zip)
    for obs in export.observations:
        for entry in obs.photos:
            assert obs.obs_id not in entry.photo


def test_multi_photo_zip_ref_photo_is_none_in_this_fixture(multi_photo_zip):
    export = reader.read_export(multi_photo_zip)
    for obs in export.observations:
        for entry in obs.photos:
            assert entry.ref_photo is None


def test_sample_zip_synthesises_one_photo_ref_per_scalar(sample_zip):
    # sample.zip has no `photos` key at all (an old export) - every photo'd
    # observation must still get exactly one PhotoRef, matching the scalar.
    export = reader.read_export(sample_zip)
    photo_count = 0
    for obs in export.observations:
        if obs.photo is not None:
            assert obs.photos == (PhotoRef(photo=obs.photo, ref_photo=obs.ref_photo),)
            photo_count += 1
        else:
            assert obs.photos == ()
    assert photo_count == 5


def test_old_export_missing_photos_key_entirely():
    # build_old_export_zip() deletes `photos` (and photo is null) entirely, not
    # just sets it null - the harder case handoff §3/§8 requires tolerating.
    export = _synthetic_export(build_zips.build_old_export_zip())
    obs = export.observations[0]
    assert obs.photos == ()
    assert obs.photo is None


def _synthetic_export(zip_bytes: bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return reader.read_export(zf)


def test_tolerance_empty_array_with_scalar_synthesises_one_entry():
    export = _synthetic_export(build_zips.build_photos_tolerance_zip())
    obs = export.observations[0]
    assert obs.photos == (PhotoRef(photo="01TOLERANCEFILE00000001.jpg", ref_photo=None),)


def test_tolerance_bare_string_entry():
    export = _synthetic_export(build_zips.build_photos_tolerance_zip())
    obs = export.observations[1]
    assert obs.photos == (PhotoRef(photo="01TOLERANCEFILE00000002.jpg", ref_photo=None),)


def test_tolerance_scalar_absent_from_array_is_prepended():
    export = _synthetic_export(build_zips.build_photos_tolerance_zip())
    obs = export.observations[2]
    assert obs.photos == (
        PhotoRef(photo="01TOLERANCEFILE00000003.jpg", ref_photo=None),
        PhotoRef(photo="01TOLERANCEFILE00000004.jpg", ref_photo=None),
    )


def test_tolerance_duplicate_entries_are_deduped():
    export = _synthetic_export(build_zips.build_photos_tolerance_zip())
    obs = export.observations[3]
    assert obs.photos == (PhotoRef(photo="01TOLERANCEFILE00000005.jpg", ref_photo=None),)


def test_photos_must_be_a_list():
    doc = _minimal_doc(photos="not-a-list")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("session.geojson", json.dumps(doc))
    with zipfile.ZipFile(buf) as zf:
        with pytest.raises(SurveyFormatError, match="photos"):
            reader.read_export(zf)


def test_photos_entry_must_have_a_photo_key():
    doc = _minimal_doc(photos=[{"ref_photo": None}])
    with pytest.raises(SurveyFormatError, match="photo"):
        reader.parse_session_geojson(_encode(doc))


def test_photos_entry_must_be_string_or_object():
    doc = _minimal_doc(photos=[123])
    with pytest.raises(SurveyFormatError, match="photos"):
        reader.parse_session_geojson(_encode(doc))


def _encode(doc: dict) -> bytes:
    return json.dumps(doc).encode("utf-8")


def _minimal_doc(*, photos) -> dict:
    return {
        "type": "FeatureCollection",
        "survey_session": {
            "id": "01MINIMALSESSION00000001",
            "name": "Minimal",
            "started_at": "2026-01-01T00:00:00.000Z",
        },
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                "properties": {
                    "obs_id": "01MINIMALOBS0000000000001",
                    "recorded_at": "2026-01-01T00:00:00.000Z",
                    "fix_at": "2026-01-01T00:00:00.000Z",
                    "lat": 0.0,
                    "lon": 0.0,
                    "gps_accuracy_m": 1.0,
                    "note": "",
                    "photo": None,
                    "photos": photos,
                    "audio": None,
                    "position_source": "gps",
                    "session_name": "Minimal",
                    "app_version": "0.1.0",
                },
            }
        ],
    }
