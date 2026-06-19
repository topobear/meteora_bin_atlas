"""Composite triangle MP4 from three leg seismic strips."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from meteora_bin_atlas.paths import DATA_PROCESSED, PLOTS_DIR
from meteora_bin_atlas.temporal.render import resolve_token_labels, subsample_trace_indices
from meteora_bin_atlas.temporal.reserve import build_price_map
from meteora_bin_atlas.temporal.seismic import (
    DRIFT_STRIP_RIGHT,
    DRIFT_WINDOW_SECONDS,
    GlobalFrame,
    compute_display_frame,
    encode_mp4_stream,
    prepare_snapshot_traces,
    render_seismic_frame,
)
from meteora_bin_atlas.temporal.triangle.resolve import TriangleLeg, TriangleSpec

# Leg strips reuse the temporal seismic renderer, but as compact edge-facing plots.
DEFAULT_LEG_DPI = 150
TRIANGLE_WIDTH = 3600
TRIANGLE_CONTENT_HEIGHT = 3400
TRIANGLE_FOOTER_HEIGHT = 760
TRIANGLE_HEIGHT = TRIANGLE_CONTENT_HEIGHT + TRIANGLE_FOOTER_HEIGHT
TRIANGLE_RADIUS = 1040
TRIANGLE_CENTER_Y_OFFSET = -150
TRIANGLE_EDGE_COVERAGE = 0.92
TRIANGLE_STRIP_WIDTH = 1800
TRIANGLE_STRIP_HEIGHT = 1200
TRIANGLE_STRIP_EDGE_GAP = 8
TRIANGLE_CALLOUT_WIDTH = 540
TRIANGLE_CALLOUT_HEIGHT = 154
TRIANGLE_CALLOUT_GAP = 32

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
TRIANGLE_DRIFT_HEADROOM = 1.28
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
NO_ARB_DEAD_BAND_LOG = 0.003
NO_ARB_VISUAL_LOG_RANGE = 0.05
NO_ARB_PRESSURE_RADIUS = 178
CYCLE_RING_RADIUS = 178
CYCLE_PULSE_BASE_ANGLE = -math.pi / 2
VERTEX_TICKER_WIDTH = 430
VERTEX_TICKER_HEIGHT = 142


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
    snapshot_times_utc: tuple[pd.Timestamp, ...]


@dataclass(frozen=True)
class NoArbState:
    edge_prices: tuple[float, float, float]
    log_prices: tuple[float, float, float]
    projected_log_prices: tuple[float, float, float]
    residual_log: float


def _snapshot_times_from_series(series_df: pd.DataFrame) -> tuple[pd.Timestamp, ...]:
    times: list[pd.Timestamp] = []
    for snapshot_index in sorted(series_df["snapshot_index"].unique()):
        group = series_df[series_df["snapshot_index"] == snapshot_index]
        fetched_at = pd.to_datetime(group["fetched_at_utc"].iloc[0], utc=True)
        times.append(fetched_at)
    return tuple(times)


def _format_clock_timestamp(value: pd.Timestamp) -> str:
    ts = value.tz_convert("UTC") if value.tzinfo is not None else value.tz_localize("UTC")
    return f"{ts.strftime('%Y-%m-%d %H:%M:%S')}.{int(ts.microsecond / 1000):03d} UTC"


def _frame_clock_label(
    leg_contexts: list[LegRenderContext],
    current_index: int,
) -> str:
    latest: pd.Timestamp | None = None
    for ctx in leg_contexts:
        if current_index >= len(ctx.snapshot_times_utc):
            continue
        candidate = ctx.snapshot_times_utc[current_index]
        if latest is None or candidate > latest:
            latest = candidate
    if latest is None:
        return "--"
    return _format_clock_timestamp(latest)


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
    _ = height
    cx = width / 2
    cy = TRIANGLE_CONTENT_HEIGHT / 2 + radius * 0.06 + TRIANGLE_CENTER_Y_OFFSET
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
        snapshot_times_utc=_snapshot_times_from_series(series_df),
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
    return strip


def _format_edge_price(price: float) -> str:
    if price >= 1_000:
        return f"{price:,.0f}"
    if price >= 10:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.3f}"
    return f"{price:.4g}"


def _draw_edge_price_callout(
    canvas: Image.Image,
    *,
    ctx: LegRenderContext,
    current_index: int,
    start: tuple[float, float],
    end: tuple[float, float],
    centroid: tuple[float, float],
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

    edge_length = math.hypot(end[0] - start[0], end[1] - start[1])
    crop_left, crop_top, crop_right, crop_bottom = _plot_crop_box(
        TRIANGLE_STRIP_WIDTH,
        TRIANGLE_STRIP_HEIGHT,
    )
    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top
    scale = (edge_length * TRIANGLE_EDGE_COVERAGE) / max(1, crop_width)
    scaled_strip_width = crop_width * scale
    scaled_strip_height = crop_height * scale
    outward_x, outward_y = _inward_edge_normal(start, end, centroid=centroid)
    edge_unit_x = (end[0] - start[0]) / max(1e-9, edge_length)
    edge_unit_y = (end[1] - start[1]) / max(1e-9, edge_length)
    mid_x = (start[0] + end[0]) / 2
    mid_y = (start[1] + end[1]) / 2
    panel_w = TRIANGLE_CALLOUT_WIDTH
    panel_h = TRIANGLE_CALLOUT_HEIGHT
    strip_center_x = mid_x + outward_x * (scaled_strip_height / 2 + TRIANGLE_STRIP_EDGE_GAP)
    strip_center_y = mid_y + outward_y * (scaled_strip_height / 2 + TRIANGLE_STRIP_EDGE_GAP)
    drift_local_x = min(
        scaled_strip_width - panel_w / 2,
        max(panel_w / 2, _strip_plot_left() * 0.42 * scale),
    )
    drift_anchor_x = (
        strip_center_x
        + edge_unit_x * (drift_local_x - scaled_strip_width / 2)
        + outward_x * (scaled_strip_height / 2 + 8)
    )
    drift_anchor_y = (
        strip_center_y
        + edge_unit_y * (drift_local_x - scaled_strip_width / 2)
        + outward_y * (scaled_strip_height / 2 + 8)
    )
    center_x = drift_anchor_x + outward_x * (panel_h / 2 + 18)
    center_y = drift_anchor_y + outward_y * (panel_h / 2 + 18)
    center_x = min(
        canvas.width - panel_w / 2 - 36,
        max(panel_w / 2 + 36, center_x),
    )
    center_y = min(
        canvas.height - panel_h / 2 - 36,
        max(panel_h / 2 + 124, center_y),
    )
    panel = [
        int(round(center_x - panel_w / 2)),
        int(round(center_y - panel_h / 2)),
        int(round(center_x + panel_w / 2)),
        int(round(center_y + panel_h / 2)),
    ]

    outer_anchor = (drift_anchor_x, drift_anchor_y)
    panel_anchor = (
        center_x - outward_x * (panel_h / 2),
        center_y - outward_y * (panel_h / 2),
    )

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    label_font = _load_mono_font(36)
    price_font = _load_mono_font(60)

    draw.line([outer_anchor, panel_anchor], fill=(*accent, 136), width=3)
    draw.rectangle(panel, fill=(4, 7, 12, 228), outline=(*accent, 220), width=3)
    panel_center_x = (panel[0] + panel[2]) / 2
    ask_label = ctx.leg.token_a
    bid_label = ctx.leg.token_b
    slash = "/"
    ask_w = float(draw.textlength(ask_label, font=label_font))
    slash_w = float(draw.textlength(slash, font=label_font))
    bid_w = float(draw.textlength(bid_label, font=label_font))
    label_x = panel_center_x - (ask_w + slash_w + bid_w) / 2
    label_y = panel[1] + 18
    draw.text(
        (label_x, label_y),
        ask_label,
        fill=(59, 167, 255, 255),
        font=label_font,
    )
    draw.text(
        (label_x + ask_w, label_y),
        slash,
        fill=(202, 218, 235, 245),
        font=label_font,
    )
    draw.text(
        (label_x + ask_w + slash_w, label_y),
        bid_label,
        fill=(255, 154, 36, 255),
        font=label_font,
    )
    draw.text(
        (panel_center_x, panel[1] + 82),
        _format_edge_price(float(spot_price)),
        fill=(*accent, 255),
        font=price_font,
        anchor="ma",
    )
    canvas.alpha_composite(overlay)


def _strip_bin_geometry(strip: Image.Image, frame: GlobalFrame) -> tuple[float, float, float]:
    # After cropping, the seismic plot starts just to the right of the drift panel.
    plot_left = _strip_plot_left()
    plot_right = float(strip.width)
    cell_w = (plot_right - plot_left) / max(1, frame.n_bins)
    return plot_left, plot_right, cell_w


def _strip_plot_left() -> float:
    crop_left, _crop_top, _crop_right, _crop_bottom = _plot_crop_box(
        TRIANGLE_STRIP_WIDTH,
        TRIANGLE_STRIP_HEIGHT,
    )
    return float(DRIFT_STRIP_RIGHT + 8 - crop_left)


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
    plot_left = _strip_plot_left()
    plot_right = strip_width
    cell_w = (plot_right - plot_left) / max(1, frame.n_bins)
    bin_center_x = plot_left + (active - int(frame.bin_id_min) + 0.5) * cell_w
    strip_fraction = min(1.0, max(0.0, bin_center_x / strip_width))
    edge_pad = (1.0 - TRIANGLE_EDGE_COVERAGE) / 2.0
    return min(1.0, max(0.0, edge_pad + strip_fraction * TRIANGLE_EDGE_COVERAGE))


def _directed_active_price(ctx: LegRenderContext, trace_index: int) -> float | None:
    if not ctx.price_for_bin:
        return None
    trace = ctx.traces[trace_index]
    raw_price = ctx.price_for_bin.get(int(trace.active_bin_id))
    if raw_price is None or raw_price <= 0:
        return None
    if ctx.leg.flip_display:
        return 1.0 / float(raw_price)
    return float(raw_price)


def _compute_no_arb_state(
    leg_contexts: list[LegRenderContext],
    *,
    trace_index: int,
) -> NoArbState | None:
    prices: list[float] = []
    for ctx in leg_contexts:
        price = _directed_active_price(ctx, trace_index)
        if price is None or price <= 0:
            return None
        prices.append(price)

    logs = tuple(math.log(price) for price in prices)
    residual = sum(logs)
    projected = tuple(log_price - residual / 3.0 for log_price in logs)
    return NoArbState(
        edge_prices=(prices[0], prices[1], prices[2]),
        log_prices=(logs[0], logs[1], logs[2]),
        projected_log_prices=(projected[0], projected[1], projected[2]),
        residual_log=residual,
    )


def _no_arb_pressure(residual_log: float) -> tuple[float, int]:
    magnitude = abs(residual_log)
    if magnitude <= NO_ARB_DEAD_BAND_LOG:
        return 0.0, 0
    pressure = (magnitude - NO_ARB_DEAD_BAND_LOG) / max(
        1e-9,
        NO_ARB_VISUAL_LOG_RANGE - NO_ARB_DEAD_BAND_LOG,
    )
    return min(1.0, pressure), 1 if residual_log > 0 else -1


def _active_liquidity_weight(ctx: LegRenderContext, trace_index: int) -> float:
    trace = ctx.traces[trace_index]
    active_id = int(trace.active_bin_id)
    liquidity = 0.0
    for bin_id, bin_liquidity in zip(trace.bin_ids, trace.liquidity, strict=True):
        if int(bin_id) == active_id:
            liquidity = max(0.0, float(bin_liquidity))
            break
    normalised = liquidity / max(1e-9, ctx.liquidity_scale)
    return max(0.02, normalised)


def _vertex_pressure_weights(
    leg_contexts: list[LegRenderContext],
    *,
    trace_index: int,
) -> tuple[float, float, float]:
    """Attribute the global no-arb residual to vertices via opposite-leg weakness.

    With only three active prices, each two-hop/direct comparison is the same
    residual. The visual attribution chooses the vertex opposite the leg where
    local active-bin depth is weakest, i.e. the least supported place for the
    triangle to close.
    """
    leg_depths = [
        _active_liquidity_weight(ctx, trace_index)
        for ctx in leg_contexts
    ]
    # Vertex 0 is opposite leg 1, vertex 1 opposite leg 2, vertex 2 opposite leg 0.
    weakness = (
        1.0 / leg_depths[1],
        1.0 / leg_depths[2],
        1.0 / leg_depths[0],
    )
    total = sum(weakness)
    if total <= 0:
        return (1 / 3, 1 / 3, 1 / 3)
    return tuple(value / total for value in weakness)  # type: ignore[return-value]


def _weighted_point(
    points: list[tuple[float, float]],
    weights: tuple[float, float, float],
) -> tuple[float, float]:
    return (
        sum(point[0] * weight for point, weight in zip(points, weights, strict=True)),
        sum(point[1] * weight for point, weight in zip(points, weights, strict=True)),
    )


def _vertex_ticker_position(
    canvas: Image.Image,
    point: tuple[float, float],
) -> tuple[int, int, int, int]:
    left = int(round(point[0] - VERTEX_TICKER_WIDTH / 2))
    top = int(round(point[1] - VERTEX_TICKER_HEIGHT - 76))
    left = max(36, min(canvas.width - VERTEX_TICKER_WIDTH - 36, left))
    top = max(130, min(canvas.height - VERTEX_TICKER_HEIGHT - 36, top))
    return (left, top, left + VERTEX_TICKER_WIDTH, top + VERTEX_TICKER_HEIGHT)


def _draw_vertex_rate_tickers(
    canvas: Image.Image,
    vertices: dict[str, tuple[float, float]],
    *,
    no_arb: NoArbState | None,
    vertex_weights: tuple[float, float, float],
    pressure: float,
    direction: int,
    accent: tuple[int, int, int],
) -> None:
    if no_arb is None:
        return

    symbols = list(vertices.keys())
    points = [vertices[symbol] for symbol in symbols]
    max_weight = max(vertex_weights) if vertex_weights else 1.0
    max_weight = max(max_weight, 1e-9)
    visual_residual = max(
        -NO_ARB_VISUAL_LOG_RANGE,
        min(NO_ARB_VISUAL_LOG_RANGE, no_arb.residual_log),
    )
    cycle_ratio = math.exp(visual_residual)
    reverse_ratio = 1.0 / cycle_ratio if cycle_ratio > 0 else 1.0
    draw = ImageDraw.Draw(canvas, "RGBA")
    label_font = _load_mono_font(22)
    ratio_font = _load_mono_font(38)

    for i, (symbol, point, weight) in enumerate(zip(symbols, points, vertex_weights, strict=True)):
        prev_symbol = symbols[(i - 1) % len(symbols)]
        next_symbol = symbols[(i + 1) % len(symbols)]
        local_pull = max(0.0, min(1.0, weight / max_weight))
        panel = _vertex_ticker_position(canvas, point)
        alpha = int(96 + 118 * pressure * local_pull)
        outline = (150, 168, 188, 132)
        if pressure > 0 and local_pull > 0.72:
            outline = (*accent, min(220, alpha + 20))
        draw.rectangle(panel, fill=(4, 7, 12, 210), outline=outline, width=2)

        route = f"{prev_symbol}>{symbol}>{next_symbol}"
        direct = f"vs {prev_symbol}>{next_symbol}"
        route_w = draw.textlength(route, font=label_font)
        direct_w = draw.textlength(direct, font=label_font)
        draw.text(
            ((panel[0] + panel[2] - route_w) / 2, panel[1] + 14),
            route,
            fill=(202, 218, 235, 216),
            font=label_font,
        )
        draw.text(
            ((panel[0] + panel[2] - direct_w) / 2, panel[1] + 42),
            direct,
            fill=(150, 168, 188, 190),
            font=label_font,
        )

        ratio_text = f"{cycle_ratio:.4f}x"
        ratio_w = draw.textlength(ratio_text, font=ratio_font)
        ratio_fill = (*accent, 235) if direction != 0 else (202, 218, 235, 220)
        draw.text(
            ((panel[0] + panel[2] - ratio_w) / 2, panel[1] + 72),
            ratio_text,
            fill=ratio_fill,
            font=ratio_font,
        )
        reverse_text = f"reverse {reverse_ratio:.4f}x"
        reverse_w = draw.textlength(reverse_text, font=label_font)
        draw.text(
            ((panel[0] + panel[2] - reverse_w) / 2, panel[1] + 112),
            reverse_text,
            fill=(150, 168, 188, 170),
            font=label_font,
        )

        bar_left = panel[0] + 26
        bar_right = panel[2] - 26
        bar_y = panel[3] - 10
        draw.line([(bar_left, bar_y), (bar_right, bar_y)], fill=(150, 168, 188, 54), width=2)
        draw.line(
            [(bar_left, bar_y), (bar_left + (bar_right - bar_left) * local_pull * pressure, bar_y)],
            fill=(*accent, 178),
            width=3,
        )


def _draw_triangle_footer(canvas: Image.Image, *, clock_label: str) -> None:
    """Draw the reserved bottom panel; clock is anchored bottom-right."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    top = TRIANGLE_CONTENT_HEIGHT + 56
    left = 120
    right = canvas.width - 120
    bottom = canvas.height - 56
    panel = [left, top - 24, right, bottom]

    draw.line(
        [(left, TRIANGLE_CONTENT_HEIGHT + 26), (right, TRIANGLE_CONTENT_HEIGHT + 26)],
        fill=(100, 130, 160, 96),
        width=2,
    )
    draw.rectangle(
        panel,
        fill=(4, 7, 12, 164),
        outline=(100, 130, 160, 132),
        width=2,
    )

    clock_font = _load_mono_font(28)
    clock_color = (190, 210, 230, 238)
    margin_x = 48
    margin_y = 40
    clock_w = draw.textlength(clock_label, font=clock_font)
    bbox = draw.textbbox((0, 0), clock_label, font=clock_font)
    clock_h = bbox[3] - bbox[1]
    draw.text(
        (right - margin_x - clock_w, bottom - margin_y - clock_h),
        clock_label,
        fill=clock_color,
        font=clock_font,
    )


def _draw_arc_arrow(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[float, float],
    radius: float,
    start_angle: float,
    sweep: float,
    color: tuple[int, int, int],
    alpha: int,
    width: int,
) -> None:
    steps = 18
    angles = [start_angle + sweep * i / steps for i in range(steps + 1)]
    points = [
        (center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius)
        for angle in angles
    ]
    draw.line(points, fill=(*color, alpha), width=width, joint="curve")
    end = points[-1]
    prev = points[-2]
    tangent = math.atan2(end[1] - prev[1], end[0] - prev[0])
    head_len = 18 + width
    spread = 0.62
    head = [
        end,
        (
            end[0] - math.cos(tangent - spread) * head_len,
            end[1] - math.sin(tangent - spread) * head_len,
        ),
        (
            end[0] - math.cos(tangent + spread) * head_len,
            end[1] - math.sin(tangent + spread) * head_len,
        ),
    ]
    draw.polygon(head, fill=(*color, alpha))


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
    no_arb = _compute_no_arb_state(leg_contexts, trace_index=current_index)
    pressure, direction = (
        _no_arb_pressure(no_arb.residual_log)
        if no_arb is not None
        else (0.0, 0)
    )
    if direction > 0:
        accent = (255, 154, 36)
        counter = (59, 167, 255)
    elif direction < 0:
        accent = (59, 167, 255)
        counter = (255, 154, 36)
    else:
        accent = (232, 244, 255)
        counter = (145, 178, 208)
    vertex_weights = _vertex_pressure_weights(
        leg_contexts,
        trace_index=current_index,
    )

    # Static no-arb target geometry: gray cycle ring plus neutral band.
    for radius, alpha in ((48, 100), (112, 72), (CYCLE_RING_RADIUS, 84)):
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
    dead_band_radius = 34
    draw.ellipse(
        [
            centroid[0] - dead_band_radius,
            centroid[1] - dead_band_radius,
            centroid[0] + dead_band_radius,
            centroid[1] + dead_band_radius,
        ],
        fill=(10, 18, 28, 62),
        outline=(232, 244, 255, 132),
        width=2,
    )
    for radius, alpha in ((CYCLE_RING_RADIUS * 0.52, 46), (CYCLE_RING_RADIUS * 0.76, 38)):
        draw.ellipse(
            [
                centroid[0] - radius,
                centroid[1] - radius,
                centroid[0] + radius,
                centroid[1] + radius,
            ],
            outline=(145, 178, 208, int(alpha)),
            width=1,
        )

    if no_arb is not None:
        pulse_alpha = 80 + int(155 * pressure)
        pulse_width = 4 + int(7 * pressure)
        sweep = 0.86 if direction >= 0 else -0.86
        rotation = direction * (current_index * 0.18 + pressure * 0.7)
        pulse_angle = CYCLE_PULSE_BASE_ANGLE + rotation
        if pressure > 0:
            _draw_arc_arrow(
                draw,
                center=centroid,
                radius=CYCLE_RING_RADIUS,
                start_angle=pulse_angle,
                sweep=sweep,
                color=accent,
                alpha=pulse_alpha,
                width=pulse_width,
            )
            dot_angle = pulse_angle + sweep
            dot_x = centroid[0] + math.cos(dot_angle) * CYCLE_RING_RADIUS
            dot_y = centroid[1] + math.sin(dot_angle) * CYCLE_RING_RADIUS
            dot_radius = 8 + int(12 * pressure)
            draw.ellipse(
                [
                    dot_x - dot_radius,
                    dot_y - dot_radius,
                    dot_x + dot_radius,
                    dot_y + dot_radius,
                ],
                fill=(*accent, min(245, pulse_alpha + 20)),
                outline=(255, 255, 255, 205),
                width=2,
            )

    draw.line(
        [(centroid[0] - 42, centroid[1]), (centroid[0] + 42, centroid[1])],
        fill=(232, 244, 255, 112),
        width=2,
    )
    draw.line(
        [(centroid[0], centroid[1] - 42), (centroid[0], centroid[1] + 42)],
        fill=(232, 244, 255, 112),
        width=2,
    )
    draw.ellipse(
        [centroid[0] - 12, centroid[1] - 12, centroid[0] + 12, centroid[1] + 12],
        outline=(232, 244, 255, 158),
        width=3,
    )

    if direction == 0:
        balance = centroid
    else:
        target = _weighted_point(guide_vertices, vertex_weights)
        tx = target[0] - centroid[0]
        ty = target[1] - centroid[1]
        length = math.hypot(tx, ty)
        if length <= 1:
            marker_radius = dead_band_radius + pressure * NO_ARB_PRESSURE_RADIUS
            balance = (centroid[0], centroid[1] - direction * marker_radius)
        else:
            pull = 0.18 + 0.82 * pressure
            balance = (centroid[0] + tx * pull, centroid[1] + ty * pull)

    trail = (radar_history + [balance])[-RADAR_TRAIL_HISTORY:]
    if len(trail) >= 2:
        for age, (p0, p1) in enumerate(zip(trail, trail[1:], strict=False)):
            progress = (age + 1) / max(1, len(trail) - 1)
            alpha = int(18 + 168 * progress)
            width = 2 + int(5 * progress)
            draw.line([p0, p1], fill=(*accent, alpha), width=width)
    for age, point in enumerate(trail[:-1]):
        progress = (age + 1) / max(1, len(trail))
        radius = 3 + int(4 * progress)
        alpha = int(24 + 118 * progress)
        draw.ellipse(
            [point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius],
            fill=(*counter, alpha),
        )

    gap_x = balance[0] - centroid[0]
    gap_y = balance[1] - centroid[1]
    if direction != 0:
        draw.line([centroid, balance], fill=(*accent, 96 + int(112 * pressure)), width=3)
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
        outline=(*accent, 235),
        width=4,
    )
    draw.ellipse(
        [balance[0] - 10, balance[1] - 10, balance[0] + 10, balance[1] + 10],
        fill=(*accent, 245),
        outline=(255, 255, 255, 245),
        width=2,
    )
    if abs(gap_x) > 1 or abs(gap_y) > 1:
        draw.ellipse(
            [centroid[0] - 7, centroid[1] - 7, centroid[0] + 7, centroid[1] + 7],
            fill=(145, 178, 208, 190),
        )

    _draw_vertex_rate_tickers(
        canvas,
        vertices,
        no_arb=no_arb,
        vertex_weights=vertex_weights,
        pressure=pressure,
        direction=direction,
        accent=accent,
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
    for ctx, _strip, start, end in leg_strips:
        _draw_edge_price_callout(
            canvas,
            ctx=ctx,
            current_index=current_index,
            start=start,
            end=end,
            centroid=centroid,
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
    _draw_triangle_footer(
        canvas,
        clock_label=_frame_clock_label(leg_contexts, current_index),
    )

    return np.asarray(canvas.convert("RGB"))


def build_triangle_temporal_mp4(
    spec: TriangleSpec,
    leg_csv_paths: tuple[Path, Path, Path],
    *,
    output_path: Path | None = None,
    fps: float = 24,
    output_frames: int | None = None,
    output_stem_prefix: str = "triangle_temporal",
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

    n_snapshots = min(len(ctx.traces) for ctx in leg_contexts)
    if n_snapshots <= 0:
        raise ValueError("No snapshots available across triangle legs")

    trace_indices = list(range(n_snapshots))
    if output_frames is not None:
        trace_indices = subsample_trace_indices(n_snapshots, output_frames)

    n_frames = len(trace_indices)
    video_duration_sec = max(1e-6, n_frames / fps)
    index_span = max(1, trace_indices[-1] - trace_indices[0])
    snapshots_per_video_sec = index_span / video_duration_sec
    drift_window = max(2, int(round(DRIFT_WINDOW_SECONDS * snapshots_per_video_sec)))

    display_frames: list[GlobalFrame | None] = [None, None, None]
    prior_indices: list[list[int]] = [[], [], []]
    radar_history: list[tuple[float, float]] = []

    if output_path is None:
        PLOTS_DIR.mkdir(parents=True, exist_ok=True)
        from datetime import UTC, datetime

        ts = datetime.now(UTC).isoformat().replace(":", "-").replace(".", "-")
        output_path = PLOTS_DIR / f"{output_stem_prefix}_{spec.triangle_id}_{ts}.mp4"

    output_path = output_path.resolve()
    def _frames() -> Iterable[np.ndarray]:
        for snapshot_index in trace_indices:
            yield _draw_triangle_frame(
                spec,
                leg_contexts,
                frame_index=snapshot_index,
                display_frames=display_frames,
                prior_indices=prior_indices,
                radar_history=radar_history,
                drift_window=drift_window,
                dpi=dpi,
            )

    frame_count = encode_mp4_stream(
        _frames(),
        output_path,
        fps=fps,
        width=TRIANGLE_WIDTH,
        height=TRIANGLE_HEIGHT,
    )
    print(f"Triangle MP4: {output_path}")
    if output_frames is not None and n_snapshots > output_frames:
        print(
            f"  streamed {frame_count} frames @ {fps} fps "
            f"(subsampled {n_snapshots} snapshots → {output_frames})"
        )
    else:
        print(f"  streamed {frame_count} frames @ {fps} fps")
    return output_path
