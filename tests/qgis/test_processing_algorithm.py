"""Milestone verification: batch-friendly Processing algorithm, same import
logic as the dialog, non-interactive duplicate handling via ON_DUPLICATE.
"""
import io
import json
import zipfile

from qgis.core import QgsProcessingContext, QgsProcessingException, QgsProcessingFeedback

from field_survey_import.processing.import_algorithm import (
    ADD_ALONGSIDE,
    FAIL,
    REPLACE,
    SKIP,
    ImportSurveyZipAlgorithm,
)
from field_survey_import.qgis import writer

SAMPLE_SESSION_ID = "01KZXJP1ZEPS7PYV04HRH6MKBH"


def _run(alg, params):
    context = QgsProcessingContext()
    feedback = QgsProcessingFeedback()
    ok, msg = alg.checkParameterValues(params, context)
    assert ok, msg
    return alg.run(params, context, feedback)


def _mutated_zip(sample_zip_path, tmp_path):
    """Same session_id, different content hash - a changed re-export."""
    with zipfile.ZipFile(sample_zip_path) as zf:
        doc = json.loads(zf.read("session.geojson"))
        members = {n: zf.read(n) for n in zf.namelist() if n != "session.geojson"}
    doc["features"][0]["properties"]["note"] += "-edited"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("session.geojson", json.dumps(doc, sort_keys=True, indent=2) + "\n")
        for name, content in members.items():
            out.writestr(name, content)

    mutated = tmp_path / "mutated.zip"
    mutated.write_bytes(buf.getvalue())
    return mutated


def test_fresh_import(sample_zip_path, tmp_path):
    alg = ImportSurveyZipAlgorithm()
    alg.initAlgorithm()
    gpkg_path = tmp_path / "survey.gpkg"

    results, ok = _run(
        alg, {"INPUT": str(sample_zip_path), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": SKIP}
    )
    assert ok
    assert results["POINT_COUNT"] == 8
    assert results["PATH_COUNT"] == 2
    assert results["BOUNDARY_COUNT"] == 1
    assert results["STATUS"] == "imported"
    assert gpkg_path.exists()


def test_already_imported_is_a_noop(sample_zip_path, tmp_path):
    alg = ImportSurveyZipAlgorithm()
    alg.initAlgorithm()
    gpkg_path = tmp_path / "survey.gpkg"
    params = {"INPUT": str(sample_zip_path), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": SKIP}

    _run(alg, params)
    results, ok = _run(alg, params)
    assert ok
    assert results["STATUS"] == "already_imported"


def test_conflict_skip_does_not_modify_existing_import(sample_zip_path, tmp_path):
    alg = ImportSurveyZipAlgorithm()
    alg.initAlgorithm()
    gpkg_path = tmp_path / "survey.gpkg"
    _run(alg, {"INPUT": str(sample_zip_path), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": SKIP})
    mutated = _mutated_zip(sample_zip_path, tmp_path)

    results, ok = _run(
        alg, {"INPUT": str(mutated), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": SKIP}
    )
    assert ok
    assert results["STATUS"] == "skipped_conflict"
    rows = writer.query_sessions_by_session_id(gpkg_path, SAMPLE_SESSION_ID)
    assert len(rows) == 1  # untouched


def test_conflict_fail_raises(sample_zip_path, tmp_path):
    # alg.run() (the full prepare/process/postProcess wrapper used elsewhere in
    # this file) catches QgsProcessingException itself and reports failure via
    # its return value rather than propagating - so this test calls
    # processAlgorithm() directly, which is where our code actually raises.
    alg = ImportSurveyZipAlgorithm()
    alg.initAlgorithm()
    gpkg_path = tmp_path / "survey.gpkg"
    _run(alg, {"INPUT": str(sample_zip_path), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": SKIP})
    mutated = _mutated_zip(sample_zip_path, tmp_path)

    context = QgsProcessingContext()
    feedback = QgsProcessingFeedback()
    params = {"INPUT": str(mutated), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": FAIL}
    prepared = alg.prepareAlgorithm(params, context, feedback)
    assert prepared

    raised = False
    try:
        alg.processAlgorithm(params, context, feedback)
    except QgsProcessingException:
        raised = True
    assert raised


def test_conflict_replace(sample_zip_path, tmp_path):
    alg = ImportSurveyZipAlgorithm()
    alg.initAlgorithm()
    gpkg_path = tmp_path / "survey.gpkg"
    _run(alg, {"INPUT": str(sample_zip_path), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": SKIP})
    mutated = _mutated_zip(sample_zip_path, tmp_path)

    results, ok = _run(
        alg, {"INPUT": str(mutated), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": REPLACE}
    )
    assert ok
    assert results["STATUS"] == "imported"
    rows = writer.query_sessions_by_session_id(gpkg_path, SAMPLE_SESSION_ID)
    assert len(rows) == 1  # replaced, not duplicated


def test_conflict_add_alongside(sample_zip_path, tmp_path):
    alg = ImportSurveyZipAlgorithm()
    alg.initAlgorithm()
    gpkg_path = tmp_path / "survey.gpkg"
    _run(alg, {"INPUT": str(sample_zip_path), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": SKIP})
    mutated = _mutated_zip(sample_zip_path, tmp_path)

    results, ok = _run(
        alg, {"INPUT": str(mutated), "DESTINATION": str(gpkg_path), "ON_DUPLICATE": ADD_ALONGSIDE}
    )
    assert ok
    assert results["STATUS"] == "imported"
    rows = writer.query_sessions_by_session_id(gpkg_path, SAMPLE_SESSION_ID)
    assert len(rows) == 2  # kept alongside the original
