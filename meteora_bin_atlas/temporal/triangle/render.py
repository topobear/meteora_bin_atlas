"""Composite triangle MP4 from three leg seismic strips."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from meteora_bin_atlas.paths import DATA_PROCESSED, PLOTS_DIR
from meteora_bin_atlas.temporal.render import resolve_token_labels
from meteora_bin_atlas.temporal.reserve import build_price_map
from meteora_bin_atlas.temporal.seismic import (
    DRIFT_WINDOW_SECONDS,
    GlobalFrame,
    compute_display_frame,
    encode_mp4,
    prepare_snapshot_traces,
    render_seismic_frame,
)
from meteora_bin_atlas.temporal.triangle.resolve import TriangleLeg, TriangleSpec

# Leg strips reuse the temporal seismic renderer, but as compact edge-facing plots.
DEFAULT_LEG_DPI = 150
TRIANGLE_WIDTH = 3600
TRIANGLE_HEIGHT = 3400
TRIANGLE_RADIUS = 1040
TRIANGLE_EDGE_COVERAGE = 0.92
TRIANGLE_STRIP_WIDTH = 1800
TRIANGLE_STRIP_HEIGHT = 1200
TRIANGLE_STRIP_EDGE_GAP = 8

TRIANGLE_TOKEN_COLORS = {
    "X": "#FF9A24",
    "Y": "#3BA7FF",
    "mix": "#E8F4FF",
    "empty": "#000000",
}

# Shorter skyline bars; active bin capped to neighbor heights.
# Shorter than the standalone temporal view, but tall enough to read as a side ribbon.
TRIANGLE_DEFLECTION_RATIO = 0.32
TRIANGLE_LIQUIDITY_SCALE_HEADROOM = 2.65
TRIANGLE_ACTIVE_BIN_NEIGHBOR_CAP = 1.0
TRIANGLE_DRIFT_HEADROOM = 2.15
TRIANGLE_DISPLAY_PAD_BINS = 2
TRIANGLE_MIN_DISPLAY_BINS = 12
TRIANGLE_VIEWPORT_EDGE_BAND = 4
TRIANGLE_ZOOM_IN_RATIO = 0.44
TRIANGLE_ROLLING_SNAPSHOTS = 3
TRIANGLE_HISTORY = 36
TRIANGLE_LANDSCAPE_STEP = 30
TRIANGLE_LANDSCAPE_ALPHA = 90
TRIANGLE_LANDSCAPE_RIDGE_ALPHA = 170
RADAR_INNER_SCALE = 0.42
RADAR_GUIDE_SCALE = 0.36
RADAR_TRAIL_HISTORY = 120


def _leg_strip_dimensions(dpi: int) -> tuple[int, int]:
    """Return a compact source strip; ``dpi`` is retained for CLI compatibility."""
    _ = dpi
    return TRIANGLE_STRIP_WIDTH, TRIANGLE_STRIP_HEIGHT


@dataclass(frozen=True)
class LegRenderContext:
    leg: TriangleLeg
    traces: list
    liquidity_scale: float
    atlas_frame: GlobalFrame
    token_x: str
    token_y: str
    price_for_bin: dict[int, float]


def _load_mono_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _triangle_vertices(
    width: int,
    height: int,
    radius: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    cx = width / 2
    cy = height / 2 + radius * 0.06
    top = (cx, cy - radius)
    bottom_left = (cx - radius * math.sqrt(3) / 2, cy + radius / 2)
    bottom_right = (cx + radius * math.sqrt(3) / 2, cy + radius / 2)
    return top, bottom_left, bottom_right


def _vertex_for_symbol(spec: TriangleSpec) -> dict[str, tuple[float, float]]:
    top, bottom_left, bottom_right = _triangle_vertices(
        TRIANGLE_WIDTH,
        TRIANGLE_HEIGHT,
        TRIANGLE_RADIUS,
    )
    symbols = [t.symbol for t in spec.tokens]
    return {
        symbols[0]: top,
        symbols[1]: bottom_left,
        symbols[2]: bottom_right,
    }


def _edge_angle_deg(
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    return math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))


def _triangle_centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def _inward_edge_normal(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    centroid: tuple[float, float],
) -> tuple[float, float]:
    mid_x = (start[0] + end[0]) / 2
    mid_y = (start[1] + end[1]) / 2
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    if (centroid[0] - mid_x) * nx + (centroid[1] - mid_y) * ny > 0:
        nx, ny = -nx, -ny
    return nx, ny


def _prepare_leg_context(
    leg: TriangleLeg,
    series_csv: Path,
    *,
    processed_dir: Path,
    zoom_bins: int = 30,
) -> LegRenderContext:
    series_df = pd.read_csv(series_csv)
    if "fetched_at_utc" in series_df.columns:
        series_df["fetched_at_utc"] = pd.to_datetime(series_df["fetched_at_utc"], utc=True)

    token_x, token_y = resolve_token_labels(leg.pool_address, processed_dir)
    if leg.flip_display:
        token_x, token_y = token_y, token_x

    traces, liquidity_scale, atlas_frame = prepare_snapshot_traces(series_df, zoom_bins=zoom_bins)
    if not traces:
        raise ValueError(f"No snapshots in {series_csv}")

    return LegRenderContext(
        leg=leg,
        traces=traces,
        liquidity_scale=liquidity_scale,
        atlas_frame=atlas_frame,
        token_x=token_x,
        token_y=token_y,
        price_for_bin=build_price_map(series_df),
    )


def _render_leg_strip(
    ctx: LegRenderContext,
    *,
    current_index: int,
    display_frame: GlobalFrame,
    ghost_indices: list[int],
    drift_window: int,
    dpi: int,
) -> Image.Image:
    width, height = _leg_strip_dimensions(dpi)
    rgba = render_seismic_frame(
        ctx.traces,
        frame=display_frame,
        current_index=current_index,
        transition_blend=1.0,
        ghost_indices=[],
        liquidity_scale=ctx.liquidity_scale * TRIANGLE_LIQUIDITY_SCALE_HEADROOM,
        drift_window=drift_window,
        token_x=ctx.token_x,
        token_y=ctx.token_y,
        pool_address=ctx.leg.pool_address,
        price_for_bin=ctx.price_for_bin,
        width=width,
        height=height,
        edge_strip=True,
        deflection_ratio=TRIANGLE_DEFLECTION_RATIO,
        drift_headroom=TRIANGLE_DRIFT_HEADROOM,
        token_colors=TRIANGLE_TOKEN_COLORS,
        token_color_mode="active_sides",
        highlight_active_bin=False,
        active_bin_neighbor_cap=TRIANGLE_ACTIVE_BIN_NEIGHBOR_CAP,
        left_drift_color=(59, 167, 255),
        right_drift_color=(255, 154, 36),
        drift_trace_width=5,
        drift_fill_alpha=112,
        drift_panel_alpha=170,
    )
    image = Image.fromarray(rgba, mode="RGBA")
    strip = image.crop(_plot_crop_box(width, height))
    _draw_edge_landscape_trail(
        strip,
        ctx=ctx,
        current_index=current_index,
        display_frame=display_frame,
        ghost_indices=ghost_indices,
    )
    _draw_edge_price_ticker(strip, ctx=ctx, current_index=current_index)
    return strip


def _format_edge_price(price: float) -> str:
    if price >= 1_000:
        return f"{price:,.0f}"
    if price >= 10:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.3f}"
    return f"{price:.4g}"


def _draw_edge_price_ticker(
    strip: Image.Image,
    *,
    ctx: LegRenderContext,
    current_index: int,
) -> None:
    if not ctx.price_for_bin:
        return
    current = ctx.traces[current_index]
    spot_price = ctx.price_for_bin.get(int(current.active_bin_id))
    if spot_price is None or spot_price <= 0:
        return

    prev_active = (
        int(ctx.traces[current_index - 1].active_bin_id)
        if current_index > 0
        else int(current.active_bin_id)
    )
    delta = int(current.active_bin_id) - prev_active
    if delta < 0:
        accent = (59, 167, 255)
    elif delta > 0:
        accent = (255, 154, 36)
    else:
        accent = (180, 196, 214)

    overlay = Image.new("RGBA", strip.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    label_font = _load_mono_font(36)
    price_font = _load_mono_font(60)

    panel = [116, 8, 650, 184]
    draw.rectangle(panel, fill=(4, 7, 12, 228), outline=(*accent, 220), width=3)
    center_x = (panel[0] + panel[2]) / 2
    draw.text(
        (center_x, panel[1] + 18),
        f"{ctx.token_y}/{ctx.token_x}",
        fill=(175, 195, 215, 245),
        font=label_font,
        anchor="ma",
    )
    draw.text(
        (center_x, panel[1] + 86),
        _format_edge_price(float(spot_price)),
        fill=(*accent, 255),
        font=price_font,
        anchor="ma",
    )
    # The whole strip is flipped before being pasted outward. Pre-flip the text
    # overlay so the final edge-local ticker stays readable.
    strip.alpha_composite(overlay.transpose(Image.Transpose.FLIP_TOP_BOTTOM))


def _strip_bin_geometry(strip: Image.Image, frame: GlobalFrame) -> tuple[float, float, float]:
    # After cropping, the seismic plot starts just to the right of the drift panel.
    plot_left = 112.0
    plot_right = float(strip.width)
    cell_w = (plot_right - plot_left) / max(1, frame.n_bins)
    return plot_left, plot_right, cell_w


def _strip_bin_color(bin_id: int, active_id: int) -> tuple[int, int, int]:
    if bin_id < active_id:
        return (59, 167, 255)
    if bin_id > active_id:
        return (255, 154, 36)
    return (232, 244, 255)


def _trace_landscape_segments(
    ctx: LegRenderContext,
    *,
    trace_index: int,
    slot: int,
    frame: GlobalFrame,
    strip: Image.Image,
) -> list[tuple[float, float, float, float, tuple[int, int, int], bool]]:
    trace = ctx.traces[trace_index]
    plot_left, _plot_right, cell_w = _strip_bin_geometry(strip, frame)
    base_y = strip.height - 4 - slot * TRIANGLE_LANDSCAPE_STEP
    max_deflection = strip.height * TRIANGLE_DEFLECTION_RATIO
    liquidity_scale = ctx.liquidity_scale * TRIANGLE_LIQUIDITY_SCALE_HEADROOM
    segments: list[tuple[float, float, float, float, tuple[int, int, int], bool]] = []

    for bin_id, liquidity in zip(trace.bin_ids, trace.liquidity, strict=True):
        bin_id = int(bin_id)
        if bin_id < frame.bin_id_min or bin_id > frame.bin_id_max:
            continue
        if float(liquidity) <= 0:
            continue
        x_left = plot_left + (bin_id - frame.bin_id_min) * cell_w
        x_right = x_left + cell_w
        peak_y = base_y - (float(liquidity) / liquidity_scale) * max_deflection
        active = bin_id == int(trace.active_bin_id)
        color = _strip_bin_color(bin_id, int(trace.active_bin_id))
        segments.append((x_left, x_right, base_y, peak_y, color, active))
    return segments


def _draw_edge_landscape_trail(
    strip: Image.Image,
    *,
    ctx: LegRenderContext,
    current_index: int,
    display_frame: GlobalFrame,
    ghost_indices: list[int],
) -> None:
    history = [idx for idx in ghost_indices[-TRIANGLE_HISTORY:] if idx < current_index]
    if not history:
        return

    draw = ImageDraw.Draw(strip, "RGBA")
    plot_left, plot_right, _cell_w = _strip_bin_geometry(strip, display_frame)
    rows: list[tuple[int, int, list[tuple[float, float, float, float, tuple[int, int, int], bool]]]] = []
    ordered = list(reversed(history))
    for slot, trace_index in enumerate(ordered, start=1):
        segments = _trace_landscape_segments(
            ctx,
            trace_index=trace_index,
            slot=slot,
            frame=display_frame,
            strip=strip,
        )
        if segments:
            rows.append((slot, trace_index, segments))

    for slot, _trace_index, segments in reversed(rows):
        lane_y = strip.height - 4 - slot * TRIANGLE_LANDSCAPE_STEP
        lane_alpha = max(28, int(70 * (0.985 ** slot)))
        draw.line(
            [(plot_left, lane_y), (plot_right, lane_y)],
            fill=(150, 170, 195, lane_alpha),
            width=1,
        )

        fill_alpha = max(36, int(TRIANGLE_LANDSCAPE_ALPHA * (0.98 ** slot)))
        ridge_alpha = max(62, int(TRIANGLE_LANDSCAPE_RIDGE_ALPHA * (0.985 ** slot)))
        ridge_points: list[tuple[float, float]] = []
        for x_left, x_right, base_y, peak_y, color, active in segments:
            segment_alpha = 210 if active else fill_alpha
            draw.rectangle(
                [x_left, peak_y, x_right, base_y],
                fill=(*color, segment_alpha),
            )
            if active:
                draw.line(
                    [(x_left, peak_y), (x_right, peak_y)],
                    fill=(255, 255, 255, 230),
                    width=2,
                )
            ridge_points.append(((x_left + x_right) / 2, peak_y))
        if len(ridge_points) >= 2:
            draw.line(
                ridge_points,
                fill=(232, 244, 255, ridge_alpha),
                width=2,
                joint="curve",
            )

    # Stitch adjacent history slices so the eye reads a surface, not isolated bars.
    for (slot_a, _idx_a, segs_a), (slot_b, _idx_b, segs_b) in zip(rows, rows[1:], strict=False):
        _ = slot_a, slot_b
        by_bin_b = {round((x_left + x_right) / 2, 2): (x_left, x_right, base_y, peak_y, color, active)
                    for x_left, x_right, base_y, peak_y, color, active in segs_b}
        for x_left, x_right, _base_y, peak_y, color, active in segs_a:
            key = round((x_left + x_right) / 2, 2)
            other = by_bin_b.get(key)
            if other is None:
                continue
            ox_left, ox_right, _obase_y, opeak_y, _ocolor, _oactive = other
            draw.polygon(
                [(x_left, peak_y), (x_right, peak_y), (ox_right, opeak_y), (ox_left, opeak_y)],
                fill=(*color, 62 if active else 46),
            )


def _scale_strip_to_edge(strip: Image.Image, edge_length: float) -> Image.Image:
    """Down/up-scale the compact strip to sit on the triangle edge."""
    if strip.width <= 0:
        return strip
    scale = (edge_length * TRIANGLE_EDGE_COVERAGE) / strip.width
    if abs(scale - 1.0) < 1e-3:
        return strip
    new_size = (max(1, int(round(strip.width * scale))), max(1, int(round(strip.height * scale))))
    return strip.resize(new_size, Image.Resampling.LANCZOS)


def _plot_crop_box(width: int, height: int) -> tuple[int, int, int, int]:
    return (12, 48, width - 12, height - 36)


def _paste_strip_on_edge(
    canvas: Image.Image,
    strip: Image.Image,
    *,
    start: tuple[float, float],
    end: tuple[float, float],
    centroid: tuple[float, float],
    edge_gap: float = TRIANGLE_STRIP_EDGE_GAP,
) -> None:
    edge_length = math.hypot(end[0] - start[0], end[1] - start[1])
    strip_rgba = _scale_strip_to_edge(strip.convert("RGBA"), edge_length)
    # Local +Y is outward after the vertical flip, so the plot baseline hugs the
    # triangle side and the liquidity skyline grows away from the triangle.
    strip_rgba = strip_rgba.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    angle = _edge_angle_deg(start, end)
    rotated = strip_rgba.rotate(
        -angle,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=(0, 0, 0, 0),
    )
    mid_x = (start[0] + end[0]) / 2
    mid_y = (start[1] + end[1]) / 2
    outward_x, outward_y = _inward_edge_normal(start, end, centroid=centroid)
    shift = (strip_rgba.height / 2) + edge_gap
    paste_x = int(mid_x - rotated.width / 2 + outward_x * shift)
    paste_y = int(mid_y - rotated.height / 2 + outward_y * shift)
    canvas.paste(rotated, (paste_x, paste_y), rotated)


def _lerp_point(
    a: tuple[float, float],
    b: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _point_toward(
    point: tuple[float, float],
    target: tuple[float, float],
    scale_from_target: float,
) -> tuple[float, float]:
    return (
        target[0] + (point[0] - target[0]) * scale_from_target,
        target[1] + (point[1] - target[1]) * scale_from_target,
    )


def _active_edge_fraction(
    ctx: LegRenderContext,
    trace_index: int,
    frame: GlobalFrame,
) -> float:
    """Active bin x-position mapped onto the actual edge strip footprint."""
    active = int(ctx.traces[trace_index].active_bin_id)
    crop_left, _crop_top, crop_right, _crop_bottom = _plot_crop_box(
        TRIANGLE_STRIP_WIDTH,
        TRIANGLE_STRIP_HEIGHT,
    )
    strip_width = float(crop_right - crop_left)
    plot_left = 112.0
    plot_right = strip_width
    cell_w = (plot_right - plot_left) / max(1, frame.n_bins)
    bin_center_x = plot_left + (active - int(frame.bin_id_min) + 0.5) * cell_w
    strip_fraction = min(1.0, max(0.0, bin_center_x / strip_width))
    edge_pad = (1.0 - TRIANGLE_EDGE_COVERAGE) / 2.0
    return min(1.0, max(0.0, edge_pad + strip_fraction * TRIANGLE_EDGE_COVERAGE))


def _radar_geometry(
    leg_contexts: list[LegRenderContext],
    vertices: dict[str, tuple[float, float]],
    *,
    centroid: tuple[float, float],
    trace_index: int,
    display_frames: list[GlobalFrame],
) -> tuple[
    list[tuple[LegRenderContext, tuple[float, float], tuple[float, float], tuple[int, int, int]]],
    tuple[float, float],
]:
    spokes: list[tuple[LegRenderContext, tuple[float, float], tuple[float, float], tuple[int, int, int]]] = []
    inner_points: list[tuple[float, float]] = []
    for ctx, frame in zip(leg_contexts, display_frames, strict=True):
        t = _active_edge_fraction(ctx, trace_index, frame)
        start = vertices[ctx.leg.token_a]
        end = vertices[ctx.leg.token_b]
        outer = _lerp_point(start, end, t)
        inner = _point_toward(outer, centroid, RADAR_INNER_SCALE)
        color = (59, 167, 255) if t < 0.5 else (255, 154, 36)
        spokes.append((ctx, outer, inner, color))
        inner_points.append(inner)

    balance = _triangle_centroid(inner_points)
    return spokes, balance


def _draw_center_radar(
    canvas: Image.Image,
    leg_contexts: list[LegRenderContext],
    vertices: dict[str, tuple[float, float]],
    *,
    centroid: tuple[float, float],
    current_index: int,
    display_frames: list[GlobalFrame],
    radar_history: list[tuple[float, float]],
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    guide_vertices = [
        _point_toward(point, centroid, RADAR_GUIDE_SCALE)
        for point in vertices.values()
    ]

    # Static target geometry: rings, centerlines, and the arbitrage-free guide triangle.
    for radius, alpha in ((52, 76), (116, 54), (190, 34)):
        draw.ellipse(
            [
                centroid[0] - radius,
                centroid[1] - radius,
                centroid[0] + radius,
                centroid[1] + radius,
            ],
            outline=(145, 178, 208, alpha),
            width=2,
        )
    for point in guide_vertices:
        draw.line([centroid, point], fill=(145, 178, 208, 50), width=2)
    draw.polygon(guide_vertices, outline=(145, 178, 208, 82), fill=(10, 18, 28, 22))

    spokes, balance = _radar_geometry(
        leg_contexts,
        vertices,
        centroid=centroid,
        trace_index=current_index,
        display_frames=display_frames,
    )
    inner_points = [inner for _ctx, _outer, inner, _color in spokes]

    if len(inner_points) == 3:
        draw.polygon(inner_points, fill=(8, 14, 22, 74), outline=(232, 244, 255, 104))

    for _ctx, outer, inner, color in spokes:
        draw.line([outer, inner], fill=(*color, 132), width=3)
        draw.ellipse(
            [outer[0] - 9, outer[1] - 9, outer[0] + 9, outer[1] + 9],
            fill=(*color, 220),
            outline=(255, 255, 255, 205),
            width=2,
        )
        draw.ellipse(
            [inner[0] - 6, inner[1] - 6, inner[0] + 6, inner[1] + 6],
            fill=(232, 244, 255, 205),
        )

    trail = (radar_history + [balance])[-RADAR_TRAIL_HISTORY:]
    if len(trail) >= 2:
        for age, (p0, p1) in enumerate(zip(trail, trail[1:], strict=False)):
            progress = (age + 1) / max(1, len(trail) - 1)
            alpha = int(24 + 178 * progress)
            width = 2 + int(4 * progress)
            draw.line([p0, p1], fill=(232, 244, 255, alpha), width=width)
    for age, point in enumerate(trail[:-1]):
        progress = (age + 1) / max(1, len(trail))
        radius = 3 + int(4 * progress)
        alpha = int(24 + 118 * progress)
        draw.ellipse(
            [point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius],
            fill=(59, 167, 255, alpha),
        )

    gap_x = balance[0] - centroid[0]
    gap_y = balance[1] - centroid[1]
    draw.line([centroid, balance], fill=(255, 154, 36, 154), width=3)
    draw.line(
        [(balance[0] - 34, balance[1]), (balance[0] + 34, balance[1])],
        fill=(255, 255, 255, 220),
        width=3,
    )
    draw.line(
        [(balance[0], balance[1] - 34), (balance[0], balance[1] + 34)],
        fill=(255, 255, 255, 220),
        width=3,
    )
    draw.ellipse(
        [balance[0] - 28, balance[1] - 28, balance[0] + 28, balance[1] + 28],
        outline=(255, 255, 255, 235),
        width=4,
    )
    draw.ellipse(
        [balance[0] - 10, balance[1] - 10, balance[0] + 10, balance[1] + 10],
        fill=(255, 154, 36, 245),
        outline=(255, 255, 255, 245),
        width=2,
    )
    if abs(gap_x) > 1 or abs(gap_y) > 1:
        draw.ellipse(
            [centroid[0] - 7, centroid[1] - 7, centroid[0] + 7, centroid[1] + 7],
            fill=(145, 178, 208, 190),
        )

    radar_history.append(balance)


def _draw_triangle_frame(
    spec: TriangleSpec,
    leg_contexts: list[LegRenderContext],
    *,
    frame_index: int,
    display_frames: list[GlobalFrame | None],
    prior_indices: list[list[int]],
    radar_history: list[tuple[float, float]],
    drift_window: int,
    dpi: int,
) -> np.ndarray:
    canvas = Image.new("RGBA", (TRIANGLE_WIDTH, TRIANGLE_HEIGHT), (0, 0, 0, 255))
    vertices = _vertex_for_symbol(spec)
    vertex_points = [vertices[t.symbol] for t in spec.tokens]
    centroid = _triangle_centroid(vertex_points)

    n_frames = min(len(ctx.traces) for ctx in leg_contexts)
    current_index = min(frame_index, n_frames - 1)

    leg_strips: list[tuple[LegRenderContext, Image.Image, tuple[float, float], tuple[float, float]]] = []
    for leg_idx, ctx in enumerate(leg_contexts):
        display_frames[leg_idx] = compute_display_frame(
            ctx.traces,
            current_index,
            display_frames[leg_idx],
            atlas=ctx.atlas_frame,
            pad_bins=TRIANGLE_DISPLAY_PAD_BINS,
            min_display_bins=TRIANGLE_MIN_DISPLAY_BINS,
            viewport_edge_band=TRIANGLE_VIEWPORT_EDGE_BAND,
            rolling_snapshots=TRIANGLE_ROLLING_SNAPSHOTS,
            zoom_in_ratio=TRIANGLE_ZOOM_IN_RATIO,
        )
        ghost_indices = prior_indices[leg_idx][-TRIANGLE_HISTORY:]
        strip = _render_leg_strip(
            ctx,
            current_index=current_index,
            display_frame=display_frames[leg_idx],  # type: ignore[arg-type]
            ghost_indices=ghost_indices,
            drift_window=drift_window,
            dpi=dpi,
        )
        start = vertices[ctx.leg.token_a]
        end = vertices[ctx.leg.token_b]
        leg_strips.append((ctx, strip, start, end))
        prior_indices[leg_idx].append(current_index)

    for _ctx, strip, start, end in leg_strips:
        _paste_strip_on_edge(
            canvas,
            strip,
            start=start,
            end=end,
            centroid=centroid,
        )

    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.polygon(vertex_points, outline=(100, 130, 160, 240), fill=None)
    for i in range(3):
        draw.line(
            [vertex_points[i], vertex_points[(i + 1) % 3]],
            fill=(100, 130, 160, 200),
            width=2,
        )
    radar_display_frames = [frame for frame in display_frames if frame is not None]
    if len(radar_display_frames) != len(leg_contexts):
        raise RuntimeError("Radar requires one display frame per triangle leg")
    _draw_center_radar(
        canvas,
        leg_contexts,
        vertices,
        centroid=centroid,
        current_index=current_index,
        display_frames=radar_display_frames,
        radar_history=radar_history,
    )

    label_font = _load_mono_font(42)
    title_font = _load_mono_font(44)
    hud_font = _load_mono_font(30)

    for symbol, point in vertices.items():
        draw.ellipse(
            [point[0] - 10, point[1] - 10, point[0] + 10, point[1] + 10],
            fill=(255, 138, 0, 255),
            outline=(255, 255, 255, 200),
        )
        bbox = draw.textbbox((0, 0), symbol, font=label_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(
            (point[0] - tw / 2, point[1] - th - 18),
            symbol,
            fill=(232, 244, 255, 255),
            font=label_font,
        )

    title = f"CURRENCY TRIANGLE · {spec.triangle_id.upper().replace('_', '/')}"
    if spec.used_fallback and spec.fallback_from:
        title += f" (fallback from {spec.fallback_from})"
    draw.text((36, 24), title, fill=(190, 210, 230, 255), font=title_font)
    draw.text(
        (36, 84),
        f"FRAME {current_index + 1}/{n_frames}",
        fill=(175, 195, 215, 230),
        font=hud_font,
    )

    return np.asarray(canvas.convert("RGB"))


def build_triangle_temporal_mp4(
    spec: TriangleSpec,
    leg_csv_paths: tuple[Path, Path, Path],
    *,
    output_path: Path | None = None,
    fps: int = 24,
    processed_dir: Path = DATA_PROCESSED,
    zoom_bins: int = 30,
    dpi: int = DEFAULT_LEG_DPI,
) -> Path:
    """Build a triangle composite MP4 from three leg series CSVs."""
    if len(spec.legs) != 3 or len(leg_csv_paths) != 3:
        raise ValueError("Triangle render requires exactly three legs and three CSV paths")

    leg_contexts = [
        _prepare_leg_context(leg, csv_path, processed_dir=processed_dir, zoom_bins=zoom_bins)
        for leg, csv_path in zip(spec.legs, leg_csv_paths, strict=True)
    ]

    n_frames = min(len(ctx.traces) for ctx in leg_contexts)
    if n_frames <= 0:
        raise ValueError("No snapshots available across triangle legs")

    video_duration_sec = max(1e-6, n_frames / fps)
    index_span = max(1, n_frames - 1)
    snapshots_per_video_sec = index_span / video_duration_sec
    drift_window = max(2, int(round(DRIFT_WINDOW_SECONDS * snapshots_per_video_sec)))

    display_frames: list[GlobalFrame | None] = [None, None, None]
    prior_indices: list[list[int]] = [[], [], []]
    radar_history: list[tuple[float, float]] = []
    frame_arrays: list[np.ndarray] = []

    for frame_index in range(n_frames):
        frame_arrays.append(
            _draw_triangle_frame(
                spec,
                leg_contexts,
                frame_index=frame_index,
                display_frames=display_frames,
                prior_indices=prior_indices,
                radar_history=radar_history,
                drift_window=drift_window,
                dpi=dpi,
            )
        )

    if output_path is None:
        PLOTS_DIR.mkdir(parents=True, exist_ok=True)
        from datetime import UTC, datetime

        ts = datetime.now(UTC).isoformat().replace(":", "-").replace(".", "-")
        output_path = PLOTS_DIR / f"triangle_temporal_{spec.triangle_id}_{ts}.mp4"

    output_path = output_path.resolve()
    encode_mp4(frame_arrays, output_path, fps=fps)
    print(f"Triangle MP4: {output_path}")
    return output_path
