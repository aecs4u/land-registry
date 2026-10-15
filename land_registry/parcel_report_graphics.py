"""Vector pictures and charts for the parcel dossier PDF.

Everything is drawn with ``reportlab.graphics`` shapes, so the report needs no network access, no raster library and
no map tiles: the locator map is the parcel outline (and its neighbours) from the cadastral geometry itself, drawn in a
local metric projection with a north arrow and a scale bar. Each builder returns ``None`` when there is nothing
meaningful to draw, so callers can simply skip the picture.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from reportlab.graphics.shapes import FILL_EVEN_ODD, Drawing, Line, Path, Rect, String
from reportlab.lib import colors

_INK = colors.HexColor("#101828")
_MUTED = colors.HexColor("#475467")
_GRID = colors.HexColor("#d0d5dd")
_FRAME_BG = colors.HexColor("#f4f7fa")
_NEIGHBOUR_FILL = colors.HexColor("#e4e7ec")
_NEIGHBOUR_STROKE = colors.HexColor("#98a2b3")
_TARGET_FILL = colors.HexColor("#1570ef")
_TARGET_STROKE = colors.HexColor("#153f72")
_BAR = colors.HexColor("#2e90fa")
_BAR_RANGE = colors.HexColor("#84caff")

_NICE_LENGTHS_M = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)
_METERS_PER_DEGREE_LAT = 111_132.0


def _rings(geometry: Any) -> list[list[list[tuple[float, float]]]]:
    """Polygons of a GeoJSON Polygon/MultiPolygon as ``[[outer, hole, …], …]`` lists of (x, y) pairs."""
    if not isinstance(geometry, dict):
        return []
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    if kind == "Polygon":
        polygons = [coordinates]
    elif kind == "MultiPolygon":
        polygons = coordinates
    else:
        return []
    result: list[list[list[tuple[float, float]]]] = []
    for polygon in polygons or []:
        rings = []
        for ring in polygon or []:
            points = []
            for position in ring or []:
                try:
                    points.append((float(position[0]), float(position[1])))
                except (TypeError, ValueError, IndexError):
                    continue
            if len(points) >= 3:
                rings.append(points)
        if rings:
            result.append(rings)
    return result


def _make_projector(polygons: list[list[list[tuple[float, float]]]]) -> Callable[[float, float], tuple[float, float]]:
    """Local equirectangular projection to metres around the first polygon (or identity for projected input)."""
    xs = [x for polygon in polygons for ring in polygon for x, _ in ring]
    ys = [y for polygon in polygons for ring in polygon for _, y in ring]
    if not xs:
        return lambda x, y: (x, y)
    if max(abs(v) for v in xs) <= 180 and max(abs(v) for v in ys) <= 90:
        lon0, lat0 = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        scale_x = _METERS_PER_DEGREE_LAT * math.cos(math.radians(lat0))
        return lambda x, y: ((x - lon0) * scale_x, (y - lat0) * _METERS_PER_DEGREE_LAT)
    return lambda x, y: (x, y)


def _page_path(rings: Iterable[list[tuple[float, float]]], **style):
    """A filled path (even-odd, so holes show) from rings already in page coordinates."""
    path = Path(fillMode=FILL_EVEN_ODD, **style)
    for ring in rings:
        for index, (px, py) in enumerate(ring):
            if index == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)
        path.closePath()
    return path


def _clip_ring(
    ring: list[tuple[float, float]], xmin: float, ymin: float, xmax: float, ymax: float
) -> list[tuple[float, float]]:
    """Sutherland–Hodgman clip of a ring to an axis-aligned rectangle (a Drawing does not clip by itself)."""

    def clip(points, inside, intersect):
        result = []
        for index, current in enumerate(points):
            previous = points[index - 1]
            if inside(current):
                if not inside(previous):
                    result.append(intersect(previous, current))
                result.append(current)
            elif inside(previous):
                result.append(intersect(previous, current))
        return result

    def at_x(x):
        return lambda a, b: (x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]))

    def at_y(y):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]), y)

    points = ring
    for inside, intersect in (
        (lambda p: p[0] >= xmin, at_x(xmin)),
        (lambda p: p[0] <= xmax, at_x(xmax)),
        (lambda p: p[1] >= ymin, at_y(ymin)),
        (lambda p: p[1] <= ymax, at_y(ymax)),
    ):
        if not points:
            break
        points = clip(points, inside, intersect)
    return points


def _nice_length(max_meters: float) -> float:
    candidates = [length for length in _NICE_LENGTHS_M if length <= max_meters]
    return float(candidates[-1]) if candidates else max(max_meters, 1.0)


def _fmt_length(meters: float) -> str:
    return f"{meters / 1000:g} km" if meters >= 1000 else f"{meters:g} m"


def parcel_locator_drawing(
    geometry: Any,
    neighbours: Sequence[Any] | None,
    width: float,
    height: float,
    *,
    area_label: str | None = None,
    reference_label: str | None = None,
    max_neighbours: int = 40,
) -> Drawing | None:
    """The parcel outline (highlighted) over its neighbouring parcels, with north arrow and scale bar."""
    target = _rings(geometry)
    if not target:
        return None
    context = []
    for neighbour in list(neighbours or [])[:max_neighbours]:
        context.extend(_rings(neighbour))
    project = _make_projector(target + context)

    def projected(polygons):
        return [[[project(x, y) for x, y in ring] for ring in polygon] for polygon in polygons]

    target_m, context_m = projected(target), projected(context)

    # Frame the parcel with some margin; neighbours only extend the view up to 2.2x the parcel's own extent so a
    # huge neighbour cannot shrink the parcel to a dot.
    flat = [point for polygon in target_m for ring in polygon for point in ring]
    min_x, max_x = min(p[0] for p in flat), max(p[0] for p in flat)
    min_y, max_y = min(p[1] for p in flat), max(p[1] for p in flat)
    span = max(max_x - min_x, max_y - min_y, 1.0)
    cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
    half = span * 1.1  # view half-extent, in metres, around the parcel centre
    pad = 8.0
    footer = 22.0  # room for the scale bar
    avail_w, avail_h = width - 2 * pad, height - 2 * pad - footer
    scale = min(avail_w, avail_h) / (2 * half)  # points per metre
    ox = pad + avail_w / 2
    oy = pad + footer + avail_h / 2

    def to_page(x: float, y: float) -> tuple[float, float]:
        return ox + (x - cx) * scale, oy + (y - cy) * scale

    drawing = Drawing(width, height)
    drawing.add(Rect(0, 0, width, height, fillColor=_FRAME_BG, strokeColor=_GRID, strokeWidth=0.6))
    clip = (pad, pad + footer, width - pad, height - pad)  # left, bottom, right, top

    def visible(polygon) -> list[list[tuple[float, float]]]:
        rings = []
        for ring in polygon:
            clipped = _clip_ring([to_page(x, y) for x, y in ring], *clip)
            if len(clipped) >= 3:
                rings.append(clipped)
        return rings

    for polygon in context_m:
        rings = visible(polygon)
        if rings:
            drawing.add(_page_path(rings, fillColor=_NEIGHBOUR_FILL, strokeColor=_NEIGHBOUR_STROKE, strokeWidth=0.4))
    for polygon in target_m:
        rings = visible(polygon)
        if rings:
            drawing.add(
                _page_path(rings, fillColor=_TARGET_FILL, fillOpacity=0.35, strokeColor=_TARGET_STROKE, strokeWidth=1.3)
            )

    # Label above the parcel's bounding box when it fits.
    label = " · ".join(part for part in (reference_label, area_label) if part)
    if label:
        drawing.add(String(width / 2, height - pad - 7, label, textAnchor="middle", fontSize=7, fillColor=_INK, fontName="Helvetica-Bold"))

    # Scale bar (bottom left): up to ~40 % of the frame width, in a round length.
    bar_m = _nice_length(0.4 * avail_w / scale)
    bar_pt = bar_m * scale
    bx, by = pad + 2, pad + 8
    half_pt = bar_pt / 2
    drawing.add(Rect(bx, by, half_pt, 3.2, fillColor=_INK, strokeColor=_INK, strokeWidth=0.4))
    drawing.add(Rect(bx + half_pt, by, half_pt, 3.2, fillColor=colors.white, strokeColor=_INK, strokeWidth=0.4))
    for offset, text in ((0, "0"), (half_pt, _fmt_length(bar_m / 2)), (bar_pt, _fmt_length(bar_m))):
        drawing.add(String(bx + offset, by + 5, text, textAnchor="middle", fontSize=5.5, fillColor=_MUTED, fontName="Helvetica"))

    # North arrow (bottom right).
    nx, ny = width - pad - 10, pad + 3
    drawing.add(Line(nx, ny, nx, ny + 14, strokeColor=_INK, strokeWidth=0.9))
    arrow = Path(fillColor=_INK, strokeColor=_INK, strokeWidth=0.4)
    arrow.moveTo(nx, ny + 17)
    arrow.lineTo(nx - 3, ny + 11)
    arrow.lineTo(nx + 3, ny + 11)
    arrow.closePath()
    drawing.add(arrow)
    drawing.add(String(nx, ny + 19, "N", textAnchor="middle", fontSize=6.5, fillColor=_INK, fontName="Helvetica-Bold"))
    return drawing


def _axis_ticks(maximum: float, count: int = 4) -> list[float]:
    if maximum <= 0:
        return [0.0]
    raw = maximum / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next((m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw), 10 * magnitude)
    ticks, value = [], 0.0
    while value <= maximum + step * 0.01:
        ticks.append(value)
        value += step
    return ticks


def range_bar_chart(
    rows: Sequence[tuple[str, float | None, float | None]],
    width: float,
    *,
    value_format: Callable[[float], str] = lambda v: f"{v:g}",
    max_rows: int = 12,
) -> Drawing | None:
    """Horizontal min–max bars (one row per label), e.g. OMI sale prices per property typology."""
    usable = [(label, low, high) for label, low, high in rows if low is not None and high is not None and high >= low]
    usable = usable[:max_rows]
    if not usable:
        return None
    row_h, top, bottom = 15.0, 6.0, 16.0
    height = top + bottom + row_h * len(usable)
    label_w = min(width * 0.38, 150.0)
    right_pad = 62.0
    plot_x0, plot_x1 = label_w + 6, width - right_pad
    maximum = max(high for _, _, high in usable) or 1.0
    ticks = _axis_ticks(maximum)
    axis_max = ticks[-1] if ticks[-1] >= maximum else ticks[-1] + (ticks[-1] - ticks[-2] if len(ticks) > 1 else maximum)
    if axis_max <= 0:
        axis_max = maximum

    def x_of(value: float) -> float:
        return plot_x0 + (plot_x1 - plot_x0) * value / axis_max

    drawing = Drawing(width, height)
    for tick in ticks:
        x = x_of(tick)
        drawing.add(Line(x, bottom - 2, x, height - top, strokeColor=_GRID, strokeWidth=0.35))
        drawing.add(String(x, 4, value_format(tick), textAnchor="middle", fontSize=5.8, fillColor=_MUTED, fontName="Helvetica"))
    for index, (label, low, high) in enumerate(usable):
        y = height - top - (index + 1) * row_h + 3
        drawing.add(String(label_w, y + 2.2, _shorten(label, 46), textAnchor="end", fontSize=6.8, fillColor=_INK, fontName="Helvetica"))
        bar_w = max(x_of(high) - x_of(low), 1.6)
        drawing.add(Rect(x_of(low), y, bar_w, 8, fillColor=_BAR_RANGE, strokeColor=_BAR, strokeWidth=0.6))
        drawing.add(Line(x_of(low), y - 1, x_of(low), y + 9, strokeColor=_BAR, strokeWidth=1.1))
        drawing.add(Line(x_of(high), y - 1, x_of(high), y + 9, strokeColor=_BAR, strokeWidth=1.1))
        drawing.add(
            String(
                min(x_of(high) + 4, width - 2), y + 1.6, f"{value_format(low)}–{value_format(high)}", fontSize=6.2, fillColor=_MUTED, fontName="Helvetica"
            )
        )
    return drawing


def bar_chart(
    rows: Sequence[tuple[str, float | None]],
    width: float,
    *,
    value_format: Callable[[float], str] = lambda v: f"{v:g}",
    max_rows: int = 14,
) -> Drawing | None:
    """Horizontal value bars (one row per label), e.g. the share of taxpayers per income bracket."""
    usable = [(label, value) for label, value in rows if value is not None and value >= 0][:max_rows]
    if not usable:
        return None
    row_h, top, bottom = 13.0, 4.0, 4.0
    height = top + bottom + row_h * len(usable)
    label_w = min(width * 0.30, 110.0)
    value_w = 40.0
    plot_x0, plot_x1 = label_w + 6, width - value_w
    maximum = max(value for _, value in usable) or 1.0
    drawing = Drawing(width, height)
    for index, (label, value) in enumerate(usable):
        y = height - top - (index + 1) * row_h + 2
        drawing.add(String(label_w, y + 2.4, _shorten(label, 28), textAnchor="end", fontSize=6.8, fillColor=_INK, fontName="Helvetica"))
        bar_w = (plot_x1 - plot_x0) * value / maximum
        drawing.add(Rect(plot_x0, y, max(bar_w, 0.8), 8, fillColor=_BAR, strokeColor=_BAR, strokeWidth=0.3))
        drawing.add(String(plot_x0 + max(bar_w, 0.8) + 4, y + 1.8, value_format(value), fontSize=6.4, fillColor=_MUTED, fontName="Helvetica"))
    return drawing


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def geometry_perimeter_m(geometry: Any) -> float | None:
    """Outer-ring perimeter of a Polygon/MultiPolygon in metres (``None`` when there is no usable geometry)."""
    polygons = _rings(geometry)
    if not polygons:
        return None
    project = _make_projector(polygons)
    total = 0.0
    for polygon in polygons:
        outer = [project(x, y) for x, y in polygon[0]]
        total += sum(math.dist(a, b) for a, b in zip(outer, outer[1:] + outer[:1], strict=True))
    return total or None
