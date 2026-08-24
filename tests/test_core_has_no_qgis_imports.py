"""field_survey_import.core must stay importable without QGIS installed, so it can be
unit-tested with plain pytest. This walks the package source and fails the build the
moment anything under core/ imports qgis or PyQt5 (directly, aliased, or via `from`).
"""
import ast
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1] / "field_survey_import" / "core"
FORBIDDEN_ROOTS = {"qgis", "PyQt5", "osgeo"}


def _imported_roots(tree: ast.Module) -> set[str]:
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_package_has_no_qgis_or_pyqt_imports():
    offenders = {}
    for path in sorted(CORE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hit = _imported_roots(tree) & FORBIDDEN_ROOTS
        if hit:
            offenders[str(path.relative_to(CORE_DIR.parents[1]))] = hit

    assert not offenders, (
        "field_survey_import/core must have zero qgis/PyQt5/osgeo imports "
        f"(it must run under plain pytest, no QGIS install): {offenders}"
    )


def test_core_dir_is_not_accidentally_empty():
    # Guards against the above test passing vacuously if core/ gets emptied by mistake.
    assert list(CORE_DIR.rglob("*.py")), "core/ has no .py files to check"
