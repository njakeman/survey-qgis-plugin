"""Handoff §1/§8: 'no network access', 'no network, ever'. Machine-checked rather
than aspirational - scans every plugin module (not just core/) for an import of
anything that could make a network request.
"""
import ast
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "field_survey_import"

# Root module names that provide network I/O. QtNetwork covers PyQt5's socket/HTTP
# classes; the rest are stdlib/common third-party networking entry points.
FORBIDDEN_ROOTS = {
    "urllib", "http", "socket", "ftplib", "smtplib", "telnetlib",
    "requests", "httpx", "urllib3",
}
# Dotted prefixes checked separately since PyQt5.QtNetwork is a submodule import,
# not a bare root name.
FORBIDDEN_DOTTED_PREFIXES = ("PyQt5.QtNetwork", "qgis.PyQt.QtNetwork")


def _imports(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_no_plugin_module_imports_networking():
    offenders = {}
    for path in sorted(PLUGIN_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imports(tree)
        hits = {n for n in imported if n.split(".")[0] in FORBIDDEN_ROOTS}
        hits |= {n for n in imported if n.startswith(FORBIDDEN_DOTTED_PREFIXES)}
        if hits:
            offenders[str(path.relative_to(PLUGIN_DIR.parent))] = hits

    assert not offenders, f"networking imports found (handoff: no network, ever): {offenders}"
