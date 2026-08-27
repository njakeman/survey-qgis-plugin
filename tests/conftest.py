import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ZIP = REPO_ROOT / "sample.zip"
MULTI_PHOTO_ZIP = REPO_ROOT / "multiple-photo-test-2026-08-25.zip"


@pytest.fixture
def sample_zip_path() -> Path:
    if not SAMPLE_ZIP.exists():
        pytest.skip("sample.zip not present in the repo root")
    return SAMPLE_ZIP


@pytest.fixture
def sample_zip(sample_zip_path):
    with zipfile.ZipFile(sample_zip_path) as zf:
        yield zf


@pytest.fixture
def multi_photo_zip_path() -> Path:
    if not MULTI_PHOTO_ZIP.exists():
        pytest.skip("multiple-photo-test-2026-08-25.zip not present in the repo root")
    return MULTI_PHOTO_ZIP


@pytest.fixture
def multi_photo_zip(multi_photo_zip_path):
    with zipfile.ZipFile(multi_photo_zip_path) as zf:
        yield zf
