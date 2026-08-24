"""Builds the three layer renderers in code (handoff §5). scripts/build_styles.py
runs these once to freeze committed .qml files; styling.py falls back to calling
these directly at runtime if loading a .qml ever fails, so the builders are both
the generator and the safety net - they can't rot silently.

Rotation convention (verified empirically, not assumed - see the plan's
"de-risking checks"): QGIS marker angle is clockwise from north, and an offset
rotates with the angle - exactly heading_deg's semantics (0=north, clockwise,
handoff §3). That's what makes the "circle + offset arrow, data-defined angle"
construction below produce the ♂-style symbol the handoff asks for.
"""
from qgis.core import (
    Qgis,
    QgsGeometryGeneratorSymbolLayer,
    QgsMarkerSymbol,
    QgsProperty,
    QgsRuleBasedRenderer,
    QgsSimpleFillSymbolLayer,
    QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsSymbolLayer,
)
from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtGui import QColor

# Colours: solid, moderately saturated, distinguishable in both light/dark canvas
# backgrounds. Not trying to match the app pixel-for-pixel - "mirror its visual
# grammar" (§5) is about the *rules*, not an exact palette.
_GPS_COLOR = QColor("#c0392b")  # red - matches the plugin icon
_PATH_COLOR = QColor("#1f78b4")  # blue
_BOUNDARY_STROKE = QColor("#33a02c")  # green
_BOUNDARY_FILL = QColor(51, 160, 44, 45)  # same green, ~18% alpha

_CIRCLE_SIZE_MM = 2.6
_ARROW_SIZE_MM = 2.8
_ARROW_OFFSET_MM = -3.6  # negative Y = "up" in symbol space before rotation = north


def _circle_layer(*, filled: bool) -> QgsSimpleMarkerSymbolLayer:
    layer = QgsSimpleMarkerSymbolLayer()
    layer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    layer.setSize(_CIRCLE_SIZE_MM)
    layer.setColor(_GPS_COLOR if filled else QColor(0, 0, 0, 0))
    layer.setStrokeColor(_GPS_COLOR)
    layer.setStrokeWidth(0.5)
    if not filled:
        layer.setStrokeStyle(Qt.DashLine)
    return layer


def _arrow_layer() -> QgsSimpleMarkerSymbolLayer:
    """The ♂-construction: a triangle offset outward from the circle's edge, with
    a data-defined rotation bound to heading_deg. Only ever added to a rule whose
    filter already guarantees heading_deg IS NOT NULL - never rendered with a
    static/default angle (handoff §5: "never render a default-north arrow").
    """
    layer = QgsSimpleMarkerSymbolLayer()
    layer.setShape(QgsSimpleMarkerSymbolLayer.Triangle)
    layer.setSize(_ARROW_SIZE_MM)
    layer.setColor(_GPS_COLOR)
    layer.setStrokeColor(_GPS_COLOR)
    layer.setOffset(QPointF(0, _ARROW_OFFSET_MM))
    layer.setDataDefinedProperty(QgsSymbolLayer.PropertyAngle, QgsProperty.fromField("heading_deg"))
    return layer


def _point_symbol(*, filled: bool, with_arrow: bool) -> QgsMarkerSymbol:
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)  # drop the default layer QgsMarkerSymbol() ships with
    symbol.appendSymbolLayer(_circle_layer(filled=filled))
    if with_arrow:
        symbol.appendSymbolLayer(_arrow_layer())
    return symbol


def _append_base_point_rules(parent, *, extra_filter: str = "") -> None:
    """The 4 rules on the two axes the handoff insists stay visible (§5):
    heading_deg present/absent (arrow yes/no) and position_source gps/map
    (solid/hollow - a measured point must never look like an eyeballed one).
    Appended as children of `parent`, optionally AND-ed with `extra_filter` so
    the revisit-aware renderer can scope these to non-station features only.
    """
    rules = [
        (
            "GPS fix, heading recorded",
            '"position_source" = \'gps\' AND "heading_deg" IS NOT NULL',
            _point_symbol(filled=True, with_arrow=True),
        ),
        (
            "Marked on map, heading recorded",
            '"position_source" = \'map\' AND "heading_deg" IS NOT NULL',
            _point_symbol(filled=False, with_arrow=True),
        ),
        (
            "GPS fix, no heading",
            '"position_source" = \'gps\' AND "heading_deg" IS NULL',
            _point_symbol(filled=True, with_arrow=False),
        ),
    ]
    for label, expression, symbol in rules:
        filter_expr = f"({expression}) AND ({extra_filter})" if extra_filter else expression
        parent.appendChild(QgsRuleBasedRenderer.Rule(symbol, 0, 0, filter_expr, label))

    else_symbol = _point_symbol(filled=False, with_arrow=False)
    else_label = "Marked on map / other, no heading"
    if extra_filter:
        # A rule can't combine isElse with its own filter (isElse means "whatever
        # matched nothing else, full stop") - so under a filtered parent, spell
        # the complement out explicitly instead.
        covered = " OR ".join(expr for _label, expr, _symbol in rules)
        else_rule = QgsRuleBasedRenderer.Rule(
            else_symbol, 0, 0, f"NOT ({covered}) AND ({extra_filter})", else_label
        )
    else:
        else_rule = QgsRuleBasedRenderer.Rule(else_symbol, 0, 0, "", else_label)
        else_rule.setIsElse(True)
    parent.appendChild(else_rule)


# done/no_access/skipped (handoff §7); not_visited stations have no feature at
# all in this session's zip, so there's no row to style - they only ever appear
# in the revisit dock's station list (services/revisit - see revisit.py).
_REVISIT_STATE_COLORS = {
    "done": QColor("#2e8b3d"),
    "no_access": QColor("#c0392b"),
    "skipped": QColor("#e0a300"),
}


def _halo_layer(color: QColor) -> QgsSimpleMarkerSymbolLayer:
    layer = QgsSimpleMarkerSymbolLayer()
    layer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    layer.setSize(_CIRCLE_SIZE_MM + 2.4)
    layer.setColor(QColor(0, 0, 0, 0))
    layer.setStrokeColor(color)
    layer.setStrokeWidth(0.9)
    return layer


def _revisit_station_symbol(color: QColor) -> QgsMarkerSymbol:
    # A plain solid dot + coloured halo - deliberately not the ♂ heading
    # treatment here, so a station's visit-state colour reads unambiguously
    # rather than competing with the arrow/hollow-vs-solid vocabulary.
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)
    symbol.appendSymbolLayer(_halo_layer(color))
    symbol.appendSymbolLayer(_circle_layer(filled=True))
    return symbol


def build_points_renderer(*, revisit: bool = False) -> QgsRuleBasedRenderer:
    root = QgsRuleBasedRenderer.Rule(None)

    if not revisit:
        _append_base_point_rules(root)
        return QgsRuleBasedRenderer(root)

    non_station = QgsRuleBasedRenderer.Rule(
        None, 0, 0, '"revisit_state" IS NULL', "Not a revisit station"
    )
    _append_base_point_rules(non_station, extra_filter='"revisit_state" IS NULL')
    root.appendChild(non_station)

    station = QgsRuleBasedRenderer.Rule(
        None, 0, 0, '"revisit_state" IS NOT NULL', "Revisit station"
    )
    for state, color in _REVISIT_STATE_COLORS.items():
        rule = QgsRuleBasedRenderer.Rule(
            _revisit_station_symbol(color), 0, 0, f'"revisit_state" = \'{state}\'', state
        )
        station.appendChild(rule)
    root.appendChild(station)

    return QgsRuleBasedRenderer(root)


def build_paths_renderer() -> QgsRuleBasedRenderer:
    """Solid line; a rule-based renderer (not a plain single-symbol one) so the
    trace_gaps dotted-segment sub-rule (§5 stretch) can sit alongside a fast-path
    plain line for the common gapless case.
    """
    root = QgsRuleBasedRenderer.Rule(None)

    plain = QgsSimpleLineSymbolLayer(_PATH_COLOR)
    plain.setWidth(0.66)
    plain_symbol = _line_symbol(plain)
    plain_rule = QgsRuleBasedRenderer.Rule(
        plain_symbol, 0, 0, "\"trace_gaps\" IS NULL", "No inferred gaps"
    )
    root.appendChild(plain_rule)

    gapped_symbol = _line_symbol_with_gaps()
    gapped_rule = QgsRuleBasedRenderer.Rule(gapped_symbol, 0, 0, "", "Has inferred gaps")
    gapped_rule.setIsElse(True)
    root.appendChild(gapped_rule)

    return QgsRuleBasedRenderer(root)


def _line_symbol(layer: QgsSimpleLineSymbolLayer):
    from qgis.core import QgsLineSymbol

    symbol = QgsLineSymbol()
    symbol.deleteSymbolLayer(0)
    symbol.appendSymbolLayer(layer)
    return symbol


# --- trace_gaps geometry-generator expressions ------------------------------------
# §8: gap index i (1-based, over SEGMENTS) means the segment from coordinate i-1 to
# coordinate i is inferred, not walked. QGIS point_n() is 1-based over COORDINATES,
# so coordinate i-1 is point_n(g, i) and coordinate i is point_n(g, i+1) - verified
# against a synthetic 5-point line with trace_gaps [2,4] before being committed here
# (see the plan's "de-risking checks": produced MultiLineString((1 0,2 0),(3 0,4 0))
# for the gap segments and ((0 0,1 0),(2 0,3 0)) for the walked ones, as expected).
_WALKED_SEGMENTS_EXPR = (
    "collect_geometries(array_foreach("
    "array_filter(generate_series(1, num_points($geometry) - 1),"
    " array_contains(from_json(coalesce(\"trace_gaps\", '[]')), @element) IS FALSE),"
    "make_line(point_n($geometry, @element), point_n($geometry, @element + 1))))"
)
_GAP_SEGMENTS_EXPR = (
    "collect_geometries(array_foreach("
    "from_json(coalesce(\"trace_gaps\", '[]')),"
    "make_line(point_n($geometry, @element), point_n($geometry, @element + 1))))"
)


def _line_symbol_with_gaps():
    from qgis.core import QgsLineSymbol

    symbol = QgsLineSymbol()
    symbol.deleteSymbolLayer(0)

    walked = QgsGeometryGeneratorSymbolLayer.create({})
    walked.setGeometryExpression(_WALKED_SEGMENTS_EXPR)
    walked.setSymbolType(Qgis.SymbolType.Line)
    walked_sub = walked.subSymbol()
    walked_sub.deleteSymbolLayer(0)
    walked_line = QgsSimpleLineSymbolLayer(_PATH_COLOR)
    walked_line.setWidth(0.66)
    walked_sub.appendSymbolLayer(walked_line)
    symbol.appendSymbolLayer(walked)

    gaps = QgsGeometryGeneratorSymbolLayer.create({})
    gaps.setGeometryExpression(_GAP_SEGMENTS_EXPR)
    gaps.setSymbolType(Qgis.SymbolType.Line)
    gaps_sub = gaps.subSymbol()
    gaps_sub.deleteSymbolLayer(0)
    gap_line = QgsSimpleLineSymbolLayer(_PATH_COLOR)
    gap_line.setWidth(0.66)
    gap_line.setPenStyle(Qt.DotLine)
    gaps_sub.appendSymbolLayer(gap_line)
    symbol.appendSymbolLayer(gaps)

    return symbol


def build_boundaries_renderer() -> QgsRuleBasedRenderer:
    """Outlined polygon, light translucent fill (§5); same gapless-fast-path /
    trace_gaps dotted-outline split as paths, applied to the exterior ring.
    """
    root = QgsRuleBasedRenderer.Rule(None)

    plain_symbol = _fill_symbol()
    plain_rule = QgsRuleBasedRenderer.Rule(
        plain_symbol, 0, 0, "\"trace_gaps\" IS NULL", "No inferred gaps"
    )
    root.appendChild(plain_rule)

    gapped_symbol = _fill_symbol_with_gaps()
    gapped_rule = QgsRuleBasedRenderer.Rule(gapped_symbol, 0, 0, "", "Has inferred gaps")
    gapped_rule.setIsElse(True)
    root.appendChild(gapped_rule)

    return QgsRuleBasedRenderer(root)


def _fill_symbol():
    from qgis.core import QgsFillSymbol

    symbol = QgsFillSymbol()
    symbol.deleteSymbolLayer(0)
    fill = QgsSimpleFillSymbolLayer()
    fill.setColor(_BOUNDARY_FILL)
    fill.setStrokeColor(_BOUNDARY_STROKE)
    fill.setStrokeWidth(0.66)
    symbol.appendSymbolLayer(fill)
    return symbol


# exterior_ring() applied to a Polygon geometry, same 1-based point_n() arithmetic
# as the line case above. The ring is closed (first coord == last, handoff §3), so
# num_points(ring) - 1 already excludes double-counting the closing vertex.
_RING_WALKED_EXPR = _WALKED_SEGMENTS_EXPR.replace("$geometry", "exterior_ring($geometry)")
_RING_GAP_EXPR = _GAP_SEGMENTS_EXPR.replace("$geometry", "exterior_ring($geometry)")


def _fill_symbol_with_gaps():
    from qgis.core import QgsFillSymbol

    symbol = QgsFillSymbol()
    symbol.deleteSymbolLayer(0)

    fill = QgsSimpleFillSymbolLayer()
    fill.setColor(_BOUNDARY_FILL)
    fill.setStrokeStyle(Qt.NoPen)  # outline drawn by the two generators below instead
    symbol.appendSymbolLayer(fill)

    walked = QgsGeometryGeneratorSymbolLayer.create({})
    walked.setGeometryExpression(_RING_WALKED_EXPR)
    walked.setSymbolType(Qgis.SymbolType.Line)
    walked_sub = walked.subSymbol()
    walked_sub.deleteSymbolLayer(0)
    walked_line = QgsSimpleLineSymbolLayer(_BOUNDARY_STROKE)
    walked_line.setWidth(0.66)
    walked_sub.appendSymbolLayer(walked_line)
    symbol.appendSymbolLayer(walked)

    gaps = QgsGeometryGeneratorSymbolLayer.create({})
    gaps.setGeometryExpression(_RING_GAP_EXPR)
    gaps.setSymbolType(Qgis.SymbolType.Line)
    gaps_sub = gaps.subSymbol()
    gaps_sub.deleteSymbolLayer(0)
    gap_line = QgsSimpleLineSymbolLayer(_BOUNDARY_STROKE)
    gap_line.setWidth(0.66)
    gap_line.setPenStyle(Qt.DotLine)
    gaps_sub.appendSymbolLayer(gap_line)
    symbol.appendSymbolLayer(gaps)

    return symbol
