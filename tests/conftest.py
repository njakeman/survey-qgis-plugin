import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ZIP = REPO_ROOT / "sample.zip"


@pytest.fixture
def sample_zip_path() -> Path:
    if not SAMPLE_ZIP.exists():
        pytest.skip("sample.zip not present in the repo root")
    return SAMPLE_ZIP


@pytest.fixture
def sample_zip(sample_zip_path):
    with zipfile.ZipFile(sample_zip_path) as zf:
        yield zf
