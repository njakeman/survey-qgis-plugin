from datetime import datetime, timezone

import pytest

from field_survey_import.core import media, reader
from field_survey_import.core.errors import MediaJoinError
from field_survey_import.core.model import Geometry, GeometryType, Observation

_DUMMY_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _dummy_obs(*, photo=None, audio=None, obs_id="OBS1"):
    return Observation(
        geometry=Geometry(type=GeometryType.POINT, coordinates=(0.0, 0.0)),
        obs_id=obs_id,
        recorded_at=_DUMMY_TIME,
        fix_at=_DUMMY_TIME,
        lat=0.0,
        lon=0.0,
        gps_accuracy_m=1.0,
        altitude_m=None,
        altitude_accuracy_m=None,
        heading_deg=None,
        heading_accuracy_deg=None,
        note="",
        photo=photo,
        audio=audio,
        audio_duration_ms=None,
        feature_layer=None,
        feature_id=None,
        feature_label=None,
        os_grid_ref=None,
        position_source="gps",
        trace_length_m=None,
        trace_gaps=None,
        ref_obs_id=None,
        ref_photo=None,
        session_name="s",
        app_version="0.1.0",
    )


def test_media_join_clean_on_sample(sample_zip):
    export = reader.read_export(sample_zip)
    resolved_photo_count = 0
    resolved_audio_count = 0
    for obs in export.observations:
        photo_ref, audio_ref = media.resolve_media(obs, sample_zip)
        assert (photo_ref is not None) == (obs.photo is not None)
        assert (audio_ref is not None) == (obs.audio is not None)
        if photo_ref:
            assert photo_ref.zip_entry == f"photos/{obs.photo}"
            resolved_photo_count += 1
        if audio_ref:
            assert audio_ref.zip_entry == f"audio/{obs.audio}"
            resolved_audio_count += 1
    assert resolved_photo_count == 5
    assert resolved_audio_count == 2


def test_media_join_is_by_property_value_not_obs_id(sample_zip):
    # Construct an observation whose obs_id does NOT match any real media filename,
    # but whose `photo` property names a real entry - the join must still succeed,
    # proving it never falls back to obs_id + '.jpg'.
    # Note: in sample.zip the media basename happens to equal the owning obs_id
    # (a coincidence of this particular fixture) - so this test proves the join
    # works correctly with a deliberately WRONG obs_id, rather than relying on
    # that coincidence to demonstrate anything.
    export = reader.read_export(sample_zip)
    real_photo_obs = next(o for o in export.observations if o.photo is not None)

    fake = _dummy_obs(obs_id="NOT-THE-REAL-OBS-ID", photo=real_photo_obs.photo)
    photo_ref, _ = media.resolve_media(fake, sample_zip)
    assert photo_ref is not None
    assert photo_ref.zip_entry == f"photos/{real_photo_obs.photo}"


def test_media_join_raises_on_missing_reference(sample_zip):
    fake = _dummy_obs(photo="does-not-exist-in-the-zip.jpg")
    with pytest.raises(MediaJoinError):
        media.resolve_media(fake, sample_zip)


def test_extract_media_writes_expected_files(sample_zip, tmp_path):
    export = reader.read_export(sample_zip)
    refs = set()
    for obs in export.observations:
        photo_ref, audio_ref = media.resolve_media(obs, sample_zip)
        if photo_ref:
            refs.add(photo_ref)
        if audio_ref:
            refs.add(audio_ref)

    written = media.extract_media(sample_zip, refs, tmp_path)
    assert len(written) == 7  # 5 photos + 2 audio
    for path in written.values():
        assert path.exists()
        assert path.stat().st_size > 0
        assert path.is_relative_to(tmp_path)
