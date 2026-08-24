"""Pins the two riskiest symbology assumptions (handoff §5, §8) against
regression: the marker rotation convention, and the trace_gaps 1-based-over-
segments geometry-generator arithmetic.
"""
import json

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsPointXY,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType, QSize
from qgis.PyQt.QtGui import QColor

from field_survey_import.qgis import renderers


def _render_points(cases):
    """cases: list of (position_source, heading_deg, (x, y)). Returns the
    rendered QImage plus a helper to check for non-background ink outside each
    point's circle body (i.e. in the arrow zone).
    """
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "t", "memory")
    dp = layer.dataProvider()
    dp.addAttributes(
        [
            QgsField("position_source", QMetaType.Type.QString),
            QgsField("heading_deg", QMetaType.Type.Double),
        ]
    )
    layer.updateFields()

    feats = []
    for src, hdg, (x, y) in cases:
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        f.setAttribute("position_source", src)
        f.setAttribute("heading_deg", hdg)
        feats.append(f)
    dp.addFeatures(feats)
    layer.setRenderer(renderers.build_points_renderer())

    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setOutputSize(QSize(1000, 200))
    settings.setExtent(QgsRectangle(-100, -100, 900, 100))
    settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    settings.setBackgroundColor(QColor(255, 255, 255))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    return job.renderedImage()


def _arrow_ink_direction(img, x_map, y_map, *, extent=(-100, -100, 900, 100), radius=25):
    w, h = img.width(), img.height()
    x_min, y_min, x_max, y_max = extent
    sx = w / (x_max - x_min)
    sy = h / (y_max - y_min)
    px = (x_map - x_min) * sx
    py = (y_max - y_min - (y_map - y_min)) * sy

    xs, ys = [], []
    for yy in range(max(0, int(py - radius * 1.5)), min(h, int(py + radius * 1.5))):
        for xx in range(max(0, int(px - radius * 1.5)), min(w, int(px + radius * 1.5))):
            c = img.pixel(xx, yy)
            r, g, b, a = (c >> 16) & 255, (c >> 8) & 255, c & 255, (c >> 24) & 255
            if a > 0 and not (r > 240 and g > 240 and b > 240):
                dist = ((xx - px) ** 2 + (yy - py) ** 2) ** 0.5
                if dist > radius * 0.6:
                    xs.append(xx)
                    ys.append(yy)
    if len(xs) <= 3:
        return None  # no arrow ink
    cx = sum(xs) / len(xs) - px
    cy = sum(ys) / len(ys) - py
    ns = "N" if cy < -3 else "S" if cy > 3 else ""
    ew = "E" if cx > 3 else "W" if cx < -3 else ""
    return (ns + ew) or "centre"


def test_arrow_rotation_matches_heading_clockwise_from_north():
    cases = [
        ("gps", 0.0, (0, 0)),
        ("gps", 90.0, (200, 0)),
        ("gps", 180.0, (400, 0)),
        ("gps", 270.0, (600, 0)),
    ]
    img = _render_points(cases)
    expected = {0: "N", 90: "E", 180: "S", 270: "W"}
    for _src, hdg, (x, y) in cases:
        direction = _arrow_ink_direction(img, x, y)
        msg = f"heading={hdg}: expected {expected[hdg]}, got {direction}"
        assert direction == expected[hdg], msg


def test_null_heading_never_renders_an_arrow():
    cases = [("gps", None, (0, 0)), ("map", None, (200, 0))]
    img = _render_points(cases)
    for _src, _hdg, (x, y) in cases:
        assert _arrow_ink_direction(img, x, y) is None


def test_map_source_gets_an_arrow_too_when_heading_present():
    cases = [("map", 90.0, (0, 0))]
    img = _render_points(cases)
    assert _arrow_ink_direction(img, 0, 0) == "E"


# --- trace_gaps geometry-generator arithmetic (handoff §8) ------------------------


def _eval(expr_str: str, geometry_wkt: str, gaps: list[int]):
    fields = QgsFields()
    fields.append(QgsField("trace_gaps", QMetaType.Type.QString))
    feature = QgsFeature(fields)
    feature.setGeometry(QgsGeometry.fromWkt(geometry_wkt))
    feature.setAttribute("trace_gaps", json.dumps(gaps))

    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(None))
    ctx.setFeature(feature)

    expr = QgsExpression(expr_str)
    expr.prepare(ctx)
    assert not expr.hasParserError(), expr.parserErrorString()
    value = expr.evaluate(ctx)
    assert not expr.hasEvalError(), expr.evalErrorString()
    return value.asWkt(1)


def test_line_gap_segments_are_1_based_over_segments_not_coordinates():
    # 6-coord line (5 segments), gaps=[2,4]: segment i is coord i-1 -> coord i.
    line = "LineString(0 0, 1 0, 2 0, 3 0, 4 0, 5 0)"
    walked = _eval(renderers._WALKED_SEGMENTS_EXPR, line, [2, 4])
    gaps = _eval(renderers._GAP_SEGMENTS_EXPR, line, [2, 4])
    assert walked == "MultiLineString ((0 0, 1 0),(2 0, 3 0),(4 0, 5 0))"
    assert gaps == "MultiLineString ((1 0, 2 0),(3 0, 4 0))"


def test_ring_gap_can_be_the_closing_segment():
    ring = "Polygon((0 0, 1 0, 1 1, 0 1, 0 0))"  # 5 coords, 4 segments
    gaps = _eval(renderers._RING_GAP_EXPR, ring, [4])  # the closing segment
    walked = _eval(renderers._RING_WALKED_EXPR, ring, [4])
    assert gaps == "MultiLineString ((0 1, 0 0))"
    assert "0 1, 0 0" not in walked


def test_gapless_fast_path_is_inert():
    line = "LineString(0 0, 1 0, 2 0)"
    walked = _eval(renderers._WALKED_SEGMENTS_EXPR, line, [])
    gaps = _eval(renderers._GAP_SEGMENTS_EXPR, line, [])
    assert walked == "MultiLineString ((0 0, 1 0),(1 0, 2 0))"
    assert gaps == ""  # collect_geometries() over an empty array -> a null/empty geometry


# --- revisit station styling (handoff §7) ------------------------------------------


def _render_revisit_points(cases):
    """cases: list of (position_source, heading_deg, revisit_state, (x, y))."""
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "t", "memory")
    dp = layer.dataProvider()
    dp.addAttributes(
        [
            QgsField("position_source", QMetaType.Type.QString),
            QgsField("heading_deg", QMetaType.Type.Double),
            QgsField("revisit_state", QMetaType.Type.QString),
        ]
    )
    layer.updateFields()

    feats = []
    for src, hdg, state, (x, y) in cases:
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        f.setAttribute("position_source", src)
        f.setAttribute("heading_deg", hdg)
        f.setAttribute("revisit_state", state)
        feats.append(f)
    dp.addFeatures(feats)
    layer.setRenderer(renderers.build_points_renderer(revisit=True))

    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setOutputSize(QSize(900, 200))
    settings.setExtent(QgsRectangle(-100, -100, 800, 100))
    settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    settings.setBackgroundColor(QColor(255, 255, 255))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    return job.renderedImage()


def _dominant_color_near(img, x_map, y_map, *, extent=(-100, -100, 800, 100), radius=20):
    w, h = img.width(), img.height()
    x_min, y_min, x_max, y_max = extent
    sx = w / (x_max - x_min)
    sy = h / (y_max - y_min)
    px = (x_map - x_min) * sx
    py = (y_max - y_min - (y_map - y_min)) * sy

    counts = {}
    for yy in range(max(0, int(py - radius)), min(h, int(py + radius))):
        for xx in range(max(0, int(px - radius)), min(w, int(px + radius))):
            c = img.pixel(xx, yy)
            r, g, b, a = (c >> 16) & 255, (c >> 8) & 255, c & 255, (c >> 24) & 255
            if a > 0 and not (r > 240 and g > 240 and b > 240):
                counts[(r, g, b)] = counts.get((r, g, b), 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def test_revisit_station_states_get_the_right_halo_colour():
    cases = [
        ("gps", None, "done", (0, 0)),
        ("gps", None, "no_access", (200, 0)),
        ("gps", None, "skipped", (400, 0)),
    ]
    img = _render_revisit_points(cases)
    expected = {
        "done": (46, 139, 61),
        "no_access": (192, 57, 43),
        "skipped": (224, 163, 0),
    }
    for _src, _hdg, state, (x, y) in cases:
        colour = _dominant_color_near(img, x, y)
        assert colour == expected[state], f"{state}: expected {expected[state]}, got {colour}"


def test_non_station_point_keeps_normal_arrow_treatment_in_revisit_layer():
    # A feature with revisit_state IS NULL must still get the ordinary ♂
    # circle+arrow rules, not a halo - the two dimensions must not interfere.
    cases = [("gps", 90.0, None, (0, 0))]
    img = _render_revisit_points(cases)
    direction = _arrow_ink_direction(img, 0, 0, extent=(-100, -100, 800, 100))
    assert direction == "E"
