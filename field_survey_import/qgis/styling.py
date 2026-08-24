"""Applies symbology to a freshly-loaded layer (handoff §9.3): try the shipped
.qml first (inspectable, user-overridable on disk), fall back to building the
renderer in code (renderers.py) if loading ever fails - so the builders are both
the generator (via scripts/build_styles.py) and the runtime safety net.
"""
from pathlib import Path

from qgis.core import QgsVectorLayer

from ..core.model import GeometryType
from . import renderers

_STYLES_DIR = Path(__file__).resolve().parent.parent / "styles"

_QML_NAME = {
    GeometryType.POINT: "points.qml",
    GeometryType.LINE_STRING: "paths.qml",
    GeometryType.POLYGON: "boundaries.qml",
}

_BUILD_RENDERER = {
    GeometryType.POINT: renderers.build_points_renderer,
    GeometryType.LINE_STRING: renderers.build_paths_renderer,
    GeometryType.POLYGON: renderers.build_boundaries_renderer,
}


def apply_style(
    layer: QgsVectorLayer, geometry_type: GeometryType, *, revisit: bool = False
) -> bool:
    """Returns True if a shipped .qml was applied, False if the in-code fallback
    renderer was used instead (still a successful style, just worth knowing for
    diagnostics/tests).

    revisit=True (points only - handoff §7 station state has no meaning on
    Paths/Boundaries) always uses the in-code renderer rather than the shipped
    points.qml: station styling depends on which sessions turn out to *be*
    revisits, so there's nothing sensible to freeze into a static file for it -
    it's generated fresh every time, same as the .qml's own fallback path.
    """
    if revisit and geometry_type is GeometryType.POINT:
        layer.setRenderer(renderers.build_points_renderer(revisit=True))
        layer.triggerRepaint()
        return False

    qml_path = _STYLES_DIR / _QML_NAME[geometry_type]
    if qml_path.exists():
        # loadNamedStyle returns (message, success) - message first, NOT
        # (success, message) as its argument order might suggest.
        _msg, ok = layer.loadNamedStyle(str(qml_path))
        if ok:
            layer.triggerRepaint()
            return True

    layer.setRenderer(_BUILD_RENDERER[geometry_type]())
    layer.triggerRepaint()
    return False
