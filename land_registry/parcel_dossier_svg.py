"""SVG pictures for the Typst parcel dossier.

The geometry helpers (local metric projection, polygon clipping, round scale-bar lengths, axis ticks) are the ones the
ReportLab dossier used in ``parcel_report_graphics``, ported to emit SVG strings that Typst embeds as images. Nothing
here needs the network, a raster library or map tiles: the locator map is the parcel outline and its neighbours drawn
from the cadastral geometry itself. Every builder returns ``None`` when there is nothing meaningful to draw, so callers
can skip the picture.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any
from xml.sax.saxutils import escape

_INK = "#101828"
_MUTED = "#475467"
_GRID = "#d0d5dd"
_FRAME_BG = "#f4f7fa"
_NEIGHBOUR_FILL = "#e4e7ec"
_NEIGHBOUR_STROKE = "#98a2b3"
_TARGET_FILL = "#0f766e"
_TARGET_STROKE = "#115e59"
_BAR = "#0f766e"
_BAR_SOFT = "#99d5cf"
_FONT = "Liberation Sans, DejaVu Sans, Arial, sans-serif"

_NICE_LENGTHS_M = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)
_METERS_PER_DEGREE_LAT = 111_132.0

Point = tuple[float, float]
Polygon = list[list[Point]]


def rings(geometry: Any) -> list[Polygon]:
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
    result: list[Polygon] = []
    for polygon in polygons or []:
        parts: Polygon = []
        for ring in polygon or []:
            points: list[Point] = []
            for position in ring or []:
                try:
                    points.append((float(position[0]), float(position[1])))
                except (TypeError, ValueError, IndexError):
                    continue
            if len(points) >= 3:
                parts.append(points)
        if parts:
            result.append(parts)
    return result


def make_projector(polygons: list[Polygon]) -> Callable[[float, float], Point]:
    """Local equirectangular projection to metres around the polygons (identity for projected input)."""
    xs = [x for polygon in polygons for ring in polygon for x, _ in ring]
    ys = [y for polygon in polygons for ring in polygon for _, y in ring]
    if not xs:
        return lambda x, y: (x, y)
    if max(abs(v) for v in xs) <= 180 and max(abs(v) for v in ys) <= 90:
        lon0, lat0 = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        scale_x = _METERS_PER_DEGREE_LAT * math.cos(math.radians(lat0))
        return lambda x, y: ((x - lon0) * scale_x, (y - lat0) * _METERS_PER_DEGREE_LAT)
    return lambda x, y: (x, y)


def clip_ring(ring: list[Point], xmin: float, ymin: float, xmax: float, ymax: float) -> list[Point]:
    """Sutherland–Hodgman clip of a ring to an axis-aligned rectangle."""

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


def nice_length(max_meters: float) -> float:
    candidates = [length for length in _NICE_LENGTHS_M if length <= max_meters]
    return float(candidates[-1]) if candidates else max(max_meters, 1.0)


def fmt_length(meters: float) -> str:
    return f"{meters / 1000:g} km" if meters >= 1000 else f"{meters:g} m"


def axis_ticks(maximum: float, count: int = 4) -> list[float]:
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


def shorten(text: Any, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def geometry_perimeter_m(geometry: Any) -> float | None:
    """Outer-ring perimeter of a Polygon/MultiPolygon in metres (``None`` when there is no usable geometry)."""
    polygons = rings(geometry)
    if not polygons:
        return None
    project = make_projector(polygons)
    total = 0.0
    for polygon in polygons:
        outer = [project(x, y) for x, y in polygon[0]]
        total += sum(math.dist(a, b) for a, b in zip(outer, outer[1:] + outer[:1], strict=True))
    return total or None


# -- SVG primitives -------------------------------------------------------------------------------------------------


def _num(value: float) -> str:
    return f"{value:.1f}"


def _text(x: float, y: float, content: str, *, size: float, fill: str = _INK, anchor: str = "start", weight: str = "400") -> str:
    return (
        f'<text x="{_num(x)}" y="{_num(y)}" font-family="{_FONT}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}">{escape(content)}</text>'
    )


def _path(polygon: list[list[Point]], **style: str) -> str:
    data = " ".join("M" + " L".join(f"{_num(x)},{_num(y)}" for x, y in ring) + " Z" for ring in polygon)
    attrs = " ".join(f'{key.replace("_", "-")}="{value}"' for key, value in style.items())
    return f'<path d="{data}" fill-rule="evenodd" {attrs}/>'


def _svg(width: float, height: float, body: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width)}" height="{_num(height)}" '
        f'viewBox="0 0 {_num(width)} {_num(height)}">' + "".join(body) + "</svg>"
    )


# -- locator map ----------------------------------------------------------------------------------------------------


def locator_svg(
    geometry: Any,
    neighbours: Sequence[Any] | None,
    width: float,
    height: float,
    *,
    caption: str | None = None,
    max_neighbours: int = 60,
) -> str | None:
    """The parcel outline (highlighted) over its neighbouring parcels, with north arrow and scale bar."""
    target = rings(geometry)
    if not target:
        return None
    context: list[Polygon] = []
    for neighbour in list(neighbours or [])[:max_neighbours]:
        context.extend(rings(neighbour))
    project = make_projector(target + context)

    def projected(polygons: list[Polygon]) -> list[Polygon]:
        return [[[project(x, y) for x, y in ring] for ring in polygon] for polygon in polygons]

    target_m, context_m = projected(target), projected(context)

    # Frame the parcel with some margin; neighbours only extend the view, so a huge neighbour cannot shrink the parcel
    # to a dot. The view keeps the frame's aspect ratio so a wide parcel is not squeezed into a square.
    flat = [point for polygon in target_m for ring in polygon for point in ring]
    min_x, max_x = min(p[0] for p in flat), max(p[0] for p in flat)
    min_y, max_y = min(p[1] for p in flat), max(p[1] for p in flat)
    cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
    pad, footer = 10.0, 24.0
    avail_w, avail_h = width - 2 * pad, height - 2 * pad - footer
    margin = 1.35
    scale = min(avail_w / max(max_x - min_x, 1.0), avail_h / max(max_y - min_y, 1.0)) / margin  # px per metre
    ox, oy = pad + avail_w / 2, pad + avail_h / 2

    def to_page(x: float, y: float) -> Point:
        return ox + (x - cx) * scale, oy - (y - cy) * scale  # SVG y grows downwards

    clip_box = (pad, pad, width - pad, height - pad - footer)

    def visible(polygon: Polygon) -> Polygon:
        parts: Polygon = []
        for ring in polygon:
            clipped = clip_ring([to_page(x, y) for x, y in ring], *clip_box)
            if len(clipped) >= 3:
                parts.append(clipped)
        return parts

    body = [f'<rect width="{_num(width)}" height="{_num(height)}" rx="5" fill="{_FRAME_BG}" stroke="{_GRID}" stroke-width="0.8"/>']
    for polygon in context_m:
        parts = visible(polygon)
        if parts:
            body.append(_path(parts, fill=_NEIGHBOUR_FILL, stroke=_NEIGHBOUR_STROKE, stroke_width="0.6"))
    for polygon in target_m:
        parts = visible(polygon)
        if parts:
            body.append(_path(parts, fill=_TARGET_FILL, fill_opacity="0.30", stroke=_TARGET_STROKE, stroke_width="1.8"))
    if caption:
        body.append(_text(pad + 2, pad + 9, caption, size=9, weight="700"))

    # Scale bar (bottom left): up to ~40 % of the frame width, in a round length.
    bar_m = nice_length(0.4 * avail_w / scale)
    bar_px = bar_m * scale
    bx, by = pad + 2, height - pad - 8
    half = bar_px / 2
    body.append(f'<rect x="{_num(bx)}" y="{_num(by)}" width="{_num(half)}" height="4" fill="{_INK}" stroke="{_INK}" stroke-width="0.5"/>')
    body.append(f'<rect x="{_num(bx + half)}" y="{_num(by)}" width="{_num(half)}" height="4" fill="#fff" stroke="{_INK}" stroke-width="0.5"/>')
    for offset, label in ((0, "0"), (half, fmt_length(bar_m / 2)), (bar_px, fmt_length(bar_m))):
        body.append(_text(bx + offset, by - 3, label, size=7, fill=_MUTED, anchor="middle"))

    # North arrow (bottom right).
    nx, ny = width - pad - 10, height - pad - 3
    body.append(f'<line x1="{_num(nx)}" y1="{_num(ny)}" x2="{_num(nx)}" y2="{_num(ny - 16)}" stroke="{_INK}" stroke-width="1.1"/>')
    body.append(f'<path d="M{_num(nx)},{_num(ny - 21)} L{_num(nx - 3.6)},{_num(ny - 13)} L{_num(nx + 3.6)},{_num(ny - 13)} Z" fill="{_INK}"/>')
    body.append(_text(nx, ny - 24, "N", size=8, anchor="middle", weight="700"))
    return _svg(width, height, body)


# -- charts ---------------------------------------------------------------------------------------------------------


def bar_chart_svg(
    rows: Sequence[tuple[str, float | None]],
    width: float,
    *,
    value_format: Callable[[float], str] = lambda v: f"{v:g}",
    max_rows: int = 14,
    highlight: int | None = None,
) -> str | None:
    """Horizontal value bars (one row per label), e.g. the share of taxpayers per income bracket."""
    usable = [(label, value) for label, value in rows if value is not None and value >= 0][:max_rows]
    if not usable:
        return None
    row_h, top, bottom = 18.0, 4.0, 4.0
    height = top + bottom + row_h * len(usable)
    label_w = min(width * 0.30, 150.0)
    plot_x0, plot_x1 = label_w + 8, width - 52
    maximum = max(value for _, value in usable) or 1.0
    body: list[str] = []
    for index, (label, value) in enumerate(usable):
        y = top + index * row_h
        bar_w = max((plot_x1 - plot_x0) * value / maximum, 1.0)
        fill = _BAR if highlight is None or highlight == index else _BAR_SOFT
        body.append(_text(label_w, y + 12, shorten(label, 30), size=9.5, anchor="end"))
        body.append(f'<rect x="{_num(plot_x0)}" y="{_num(y + 3)}" width="{_num(bar_w)}" height="10" rx="2" fill="{fill}"/>')
        body.append(_text(plot_x0 + bar_w + 5, y + 12, value_format(value), size=9, fill=_MUTED))
    return _svg(width, height, body)


def range_bar_chart_svg(
    rows: Sequence[tuple[str, float | None, float | None]],
    width: float,
    *,
    value_format: Callable[[float], str] = lambda v: f"{v:g}",
    max_rows: int = 12,
) -> str | None:
    """Horizontal min–max bars (one row per label), e.g. OMI sale prices per property typology."""
    usable = [(label, low, high) for label, low, high in rows if low is not None and high is not None and high >= low]
    usable = usable[:max_rows]
    if not usable:
        return None
    row_h, top, bottom = 22.0, 6.0, 20.0
    height = top + bottom + row_h * len(usable)
    label_w = min(width * 0.36, 170.0)
    plot_x0, plot_x1 = label_w + 8, width - 84
    maximum = max(high for _, _, high in usable) or 1.0
    ticks = axis_ticks(maximum)
    axis_max = ticks[-1] if ticks[-1] >= maximum else ticks[-1] + (ticks[-1] - ticks[-2] if len(ticks) > 1 else maximum)
    axis_max = axis_max or maximum

    def x_of(value: float) -> float:
        return plot_x0 + (plot_x1 - plot_x0) * value / axis_max

    body: list[str] = []
    for tick in ticks:
        x = x_of(tick)
        body.append(f'<line x1="{_num(x)}" y1="{_num(top)}" x2="{_num(x)}" y2="{_num(height - bottom)}" stroke="{_GRID}" stroke-width="0.6"/>')
        body.append(_text(x, height - 6, value_format(tick), size=8, fill=_MUTED, anchor="middle"))
    for index, (label, low, high) in enumerate(usable):
        y = top + index * row_h
        body.append(_text(label_w, y + 14, shorten(label, 40), size=9.5, anchor="end"))
        width_px = max(x_of(high) - x_of(low), 2.5)
        body.append(f'<rect x="{_num(x_of(low))}" y="{_num(y + 5)}" width="{_num(width_px)}" height="11" rx="2" fill="{_BAR_SOFT}" stroke="{_BAR}" stroke-width="0.9"/>')
        body.append(_text(min(x_of(high) + 6, width - 2), y + 14, f"{value_format(low)}–{value_format(high)}", size=8.5, fill=_MUTED))
    return _svg(width, height, body)


def column_chart_svg(
    rows: Sequence[tuple[str, float | None]],
    width: float,
    height: float = 150.0,
    *,
    value_format: Callable[[float], str] = lambda v: f"{v:g}",
    highlight_max: bool = True,
) -> str | None:
    """Vertical columns (one per label), e.g. the age structure of a census section."""
    usable = [(label, value) for label, value in rows if value is not None and value >= 0]
    if not usable:
        return None
    top, bottom = 16.0, 20.0
    slot = width / len(usable)
    bar_w = min(slot * 0.62, 38.0)
    maximum = max(value for _, value in usable) or 1.0
    plot_h = height - top - bottom
    peak = max(range(len(usable)), key=lambda i: usable[i][1]) if highlight_max else -1
    body = [f'<line x1="0" y1="{_num(height - bottom)}" x2="{_num(width)}" y2="{_num(height - bottom)}" stroke="{_GRID}" stroke-width="0.8"/>']
    for index, (label, value) in enumerate(usable):
        x = slot * index + (slot - bar_w) / 2
        bar_h = max(plot_h * value / maximum, 1.0)
        y = top + plot_h - bar_h
        fill = _BAR if index == peak else _BAR_SOFT
        body.append(f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(bar_w)}" height="{_num(bar_h)}" rx="2" fill="{fill}"/>')
        body.append(_text(x + bar_w / 2, y - 4, value_format(value), size=8.5, fill=_MUTED, anchor="middle"))
        body.append(_text(x + bar_w / 2, height - 6, shorten(label, 9), size=8.5, fill=_MUTED, anchor="middle"))
    return _svg(width, height, body)


def cash_flow_chart_svg(
    rows: Sequence[dict[str, Any]],
    width: float = 520.0,
    height: float = 220.0,
    *,
    annual_label: str = "Annual net cash flow",
    cumulative_label: str = "Cumulative cash flow",
    payback_label: str = "Simple payback",
    value_format: Callable[[float], str] = lambda v: f"{v:g}",
) -> str | None:
    """Plot yearly net cash flow and its cumulative break-even path."""
    usable = []
    for row in rows:
        try:
            year = int(row["year"])
            annual = float(row["cash_flow_eur"])
            cumulative = float(row["cumulative_cash_flow_eur"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(annual) and math.isfinite(cumulative):
            usable.append((year, annual, cumulative))
    if len(usable) < 2:
        return None

    pad_l, pad_r = 54.0, 12.0
    plot_w = width - pad_l - pad_r
    annual_top, annual_h = 21.0, 67.0
    cumulative_top, cumulative_h = 128.0, 67.0
    bottom = height - 18.0

    def y_of(value: float, values: list[float], top: float, plot_h: float) -> float:
        low, high = min(0.0, min(values)), max(0.0, max(values))
        span = high - low or 1.0
        return top + plot_h * (high - value) / span

    def x_of(index: int) -> float:
        return pad_l + plot_w * index / (len(usable) - 1)

    annual_values = [row[1] for row in usable]
    cumulative_values = [row[2] for row in usable]
    annual_zero = y_of(0.0, annual_values, annual_top, annual_h)
    cumulative_zero = y_of(0.0, cumulative_values, cumulative_top, cumulative_h)
    body = [
        _text(pad_l, 12, annual_label, size=8.5, fill=_MUTED, weight="700"),
        _text(pad_l, 119, cumulative_label, size=8.5, fill=_MUTED, weight="700"),
        f'<line x1="{_num(pad_l)}" y1="{_num(annual_zero)}" x2="{_num(width - pad_r)}" y2="{_num(annual_zero)}" stroke="{_GRID}" stroke-width="0.8"/>',
        f'<line x1="{_num(pad_l)}" y1="{_num(cumulative_zero)}" x2="{_num(width - pad_r)}" y2="{_num(cumulative_zero)}" stroke="{_GRID}" stroke-width="0.8"/>',
    ]
    slot = plot_w / max(1, len(usable) - 1)
    bar_w = min(slot * 0.62, 15.0)
    for index, (year, annual, _) in enumerate(usable):
        x = x_of(index)
        y = y_of(annual, annual_values, annual_top, annual_h)
        bar_y = min(y, annual_zero)
        bar_h = max(abs(annual_zero - y), 1.0)
        fill = _BAR if annual >= 0 else "#b42318"
        body.append(
            f'<rect x="{_num(x - bar_w / 2)}" y="{_num(bar_y)}" width="{_num(bar_w)}" height="{_num(bar_h)}" rx="1" fill="{fill}"/>'
        )
        if year == 0 or year == usable[-1][0] or year % 5 == 0:
            body.append(_text(x, bottom, str(year), size=7.5, fill=_MUTED, anchor="middle"))

    cumulative_points = [
        (x_of(index), y_of(value, cumulative_values, cumulative_top, cumulative_h))
        for index, value in enumerate(cumulative_values)
    ]
    point_text = " ".join(f"{_num(x)},{_num(y)}" for x, y in cumulative_points)
    body.append(f'<polyline points="{point_text}" fill="none" stroke="{_BAR}" stroke-width="1.8"/>')
    for x, y in cumulative_points:
        body.append(f'<circle cx="{_num(x)}" cy="{_num(y)}" r="1.7" fill="{_BAR}"/>')

    for index, (left, right) in enumerate(zip(usable, usable[1:])):
        if left[2] < 0 <= right[2] and right[2] > left[2]:
            fraction = -left[2] / (right[2] - left[2])
            payback_x = x_of(index) + (x_of(index + 1) - x_of(index)) * fraction
            body.append(
                f'<line x1="{_num(payback_x)}" y1="{_num(cumulative_top)}" x2="{_num(payback_x)}" y2="{_num(cumulative_top + cumulative_h)}" stroke="#7f56d9" stroke-width="1" stroke-dasharray="3 2"/>'
            )
            body.append(_text(min(payback_x + 4, width - pad_r), cumulative_top + 8, payback_label, size=7, fill="#6941c6"))
            break
    return _svg(width, height, body)


def band_line_chart_svg(
    points: Sequence[dict[str, Any]],
    width: float,
    height: float = 150.0,
    *,
    value_format: Callable[[float], str] = lambda v: f"{v:g}",
) -> str | None:
    """Min–max band with the midpoint line over time; ``points`` are ``{"label", "min", "max", "mid"}``."""
    usable = [p for p in points if p.get("min") is not None and p.get("max") is not None]
    if len(usable) < 2:
        return None
    pad_l, pad_r, top, bottom = 44.0, 12.0, 10.0, 22.0
    low = min(p["min"] for p in usable)
    high = max(p["max"] for p in usable)
    span = (high - low) or 1.0
    plot_w, plot_h = width - pad_l - pad_r, height - top - bottom

    def x_of(index: int) -> float:
        return pad_l + plot_w * index / (len(usable) - 1)

    def y_of(value: float) -> float:
        return top + plot_h * (1 - (value - low) / span)

    upper = [(x_of(i), y_of(p["max"])) for i, p in enumerate(usable)]
    lower = [(x_of(i), y_of(p["min"])) for i, p in reversed(list(enumerate(usable)))]
    mid = " ".join(f"{_num(x_of(i))},{_num(y_of(p.get('mid', (p['min'] + p['max']) / 2)))}" for i, p in enumerate(usable))
    body = []
    for tick in axis_ticks(high - low, 3):
        value = low + tick
        y = y_of(value)
        body.append(f'<line x1="{_num(pad_l)}" y1="{_num(y)}" x2="{_num(width - pad_r)}" y2="{_num(y)}" stroke="{_GRID}" stroke-width="0.6"/>')
        body.append(_text(pad_l - 5, y + 3, value_format(value), size=8, fill=_MUTED, anchor="end"))
    body.append(_path([upper + lower], fill=_BAR_SOFT, fill_opacity="0.7", stroke="none"))
    body.append(f'<polyline points="{mid}" fill="none" stroke="{_BAR}" stroke-width="1.8"/>')
    last_x, last_y = x_of(len(usable) - 1), y_of(usable[-1].get("mid", (usable[-1]["min"] + usable[-1]["max"]) / 2))
    body.append(f'<circle cx="{_num(last_x)}" cy="{_num(last_y)}" r="3.4" fill="{_BAR}"/>')
    body.append(_text(pad_l, height - 6, str(usable[0].get("label", "")), size=8.5, fill=_MUTED))
    body.append(_text(width - pad_r, height - 6, str(usable[-1].get("label", "")), size=8.5, fill=_MUTED, anchor="end"))
    return _svg(width, height, body)


def qr_svg(text: str) -> str | None:
    """QR code as SVG, or ``None`` when the ``qrcode`` package is unavailable."""
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError:  # pragma: no cover - optional dependency
        return None
    code = qrcode.QRCode(border=1, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(text)
    code.make(fit=True)
    image = code.make_image(image_factory=SvgPathImage)
    return image.to_string().decode("utf-8") if isinstance(image.to_string(), bytes) else str(image.to_string())
