"""Regenerates field_survey_import/styles/*.qml from field_survey_import/qgis/
renderers.py. Run under the QGIS-bundled interpreter (needs `qgis`):

    & "C:\\Program Files\\QGIS 3.44.8\\bin\\python-qgis-ltr.bat" scripts\\build_styles.py

The committed .qml files are what styling.apply_style() loads at runtime (falling
back to calling renderers.py directly if a .qml ever fails to load) - regenerate
and re-commit them whenever renderers.py changes, so the two never drift apart
in a way that's only noticed at runtime.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from qgis.core import QgsApplication, QgsVectorLayer  # noqa: E402

QgsApplication.setPrefixPath(r"C:\Program Files\QGIS 3.44.8\apps\qgis-ltr", True)
app = QgsApplication([], False)
app.initQgis()

from field_survey_import.core.model import GeometryType  # noqa: E402
from field_survey_import.core.schema import ALL_FIELDS  # noqa: E402
from field_survey_import.qgis import renderers  # noqa: E402
from field_survey_import.qgis.fields import build_qgs_fields  # noqa: E402

STYLES_DIR = REPO_ROOT / "field_survey_import" / "styles"
STYLES_DIR.mkdir(parents=True, exist_ok=True)

_WKB_URI = {
    GeometryType.POINT: "Point",
    GeometryType.LINE_STRING: "LineString",
    GeometryType.POLYGON: "Polygon",
}
_BUILD_RENDERER = {
    GeometryType.POINT: renderers.build_points_renderer,
    GeometryType.LINE_STRING: renderers.build_paths_renderer,
    GeometryType.POLYGON: renderers.build_boundaries_renderer,
}
_QML_NAME = {
    GeometryType.POINT: "points.qml",
    GeometryType.LINE_STRING: "paths.qml",
    GeometryType.POLYGON: "boundaries.qml",
}


def main() -> None:
    for kind in (GeometryType.POINT, GeometryType.LINE_STRING, GeometryType.POLYGON):
        layer = QgsVectorLayer(f"{_WKB_URI[kind]}?crs=EPSG:4326", "template", "memory")
        layer.dataProvider().addAttributes(build_qgs_fields(ALL_FIELDS))
        layer.updateFields()
        layer.setRenderer(_BUILD_RENDERER[kind]())

        out_path = STYLES_DIR / _QML_NAME[kind]
        ok, msg = layer.saveNamedStyle(str(out_path))
        if not ok:
            raise SystemExit(f"failed saving {out_path}: {msg}")
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
    app.exitQgis()
