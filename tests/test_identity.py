from datetime import datetime, timezone

from field_survey_import.core import identity


def test_content_hash_is_deterministic(sample_zip_path):
    h1 = identity.content_hash(sample_zip_path)
    h2 = identity.content_hash(sample_zip_path)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest


def test_content_hash_changes_with_content(tmp_path):
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    a.write_bytes(b"hello")
    b.write_bytes(b"world")
    assert identity.content_hash(a) != identity.content_hash(b)


def test_slugify_basic():
    assert identity.slugify("Hedgerow Survey") == "hedgerow-survey"
    assert identity.slugify("  Ash & Oak / North  ") == "ash-oak-north"
    assert identity.slugify("") == ""
    assert identity.slugify("!!!") == ""


def test_session_slug_uses_started_at_date_not_name():
    # sample.zip's own gotcha: name="2026-08-12" but started_at date is 2026-08-13.
    started = datetime(2026, 8, 13, 12, 48, 49, tzinfo=timezone.utc)
    slug = identity.session_slug("2026-08-12", started, "01KZXJP1ZEPS7PYV04HRH6MKBH")
    assert slug == "2026-08-12-2026-08-13"  # name slugifies to a literal date string, kept as-is
    assert slug.endswith("2026-08-13")  # the real date, from started_at


def test_session_slug_falls_back_to_session_id_when_name_is_empty():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    slug = identity.session_slug("", started, "01KZXJP1ZEPS7PYV04HRH6MKBH")
    assert slug == "hrh6mkbh-2026-01-01"


def test_session_slug_falls_back_when_name_is_pure_punctuation():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    slug = identity.session_slug("???", started, "01KZXJP1ZEPS7PYV04HRH6MKBH")
    assert slug.startswith("hrh6mkbh-")


def test_unique_slug_no_collision():
    assert identity.unique_slug("hedgerow-2026-08-24", set()) == "hedgerow-2026-08-24"


def test_unique_slug_collision_appends_suffix():
    taken = {"hedgerow-2026-08-24", "hedgerow-2026-08-24-2"}
    assert identity.unique_slug("hedgerow-2026-08-24", taken) == "hedgerow-2026-08-24-3"
