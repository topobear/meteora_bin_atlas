"""Reserve-space (x, y) projection of the temporal bin-atlas histogram.

The seismic renderer draws the liquidity distribution in ``(p, L)`` space — each
bar sits at a bin price ``p`` with height equal to the bin liquidity ``L``.  This
module renders the *same* per-snapshot data as the **reserve curve** the pool
actually traces in ``(x, y) = (SOL, USDC)`` space.

A DLMM bin is constant-sum at its fixed price ``p``: its reserves satisfy
``p * x + y = const``, i.e. a straight segment of slope ``-p``.  Over the bin's
price range ``[p_a, p_b]`` (``p_b = p_a * (1 + bin_step / 10000)``) the bin can
hold, in the canonical concentrated-liquidity parameterisation,

    dx = L * (1/sqrt(p_a) - 1/sqrt(p_b))     (SOL capacity, X)
    dy = L * (sqrt(p_b)   - sqrt(p_a))       (USDC capacity, Y)

so the bin's segment has horizontal extent ``dx``, vertical extent ``dy`` and
slope ``-dy/dx = -sqrt(p_a * p_b) ~= -p``.  Laying the per-bin segments tip-to-tail
in price order builds the piecewise-linear **constant-product envelope** the pool
approximates: high-price bins (steep segments) sit top-left, low-price bins
(shallow segments) sit bottom-right, and the kink at the active bin is the pool's
current operating point.

This is a purely additive view: it reuses :mod:`meteora_bin_atlas.temporal.seismic`
trace structures and styling and never mutates the existing pipeline.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from meteora_bin_atlas.temporal.seismic import (
    CHANNEL_FONT_SIZE,
    GHOST_OUTLINE_DECAY,
    SEISMIC_ACTIVE_COLOR,
    SEISMIC_TOKEN_COLORS,
    SUBTITLE_FONT_SIZE,
    TITLE_FONT_SIZE,
    GlobalFrame,
    LayerStyle,
    SeismicStyle,
    SnapshotTrace,
    _composition_rgb,
    _dim_rgb,
    _hex_to_rgb,
    _layer_style,
    _load_mono_font,
    _plot_background,
    _resolve_ghost_layers,
    _trace_outline_rgb,
)

# Auto-fit headroom: the largest cumulative reserve maps to (extent / headroom)
# so the envelope always keeps a margin away from the plot edges.
RESERVE_HEADROOM = 1.10
RESERVE_HUD_FONT_SIZE = 19
_TRACE_TIP_RGB = (232, 244, 255)

# One envelope segment per bin: (bin_id, x_amount, y_amount, start_xy, end_xy).
ReserveSegment = tuple[int, float, float, tuple[float, float], tuple[float, float]]


def build_price_map(series_df: pd.DataFrame) -> dict[int, float]:
    """Map ``bin_id -> price`` (USDC per SOL) from the series.

    The bin price is a deterministic geometric function of ``bin_id`` so it is
    constant across snapshots; we collapse to one value per bin.
    """
    work = series_df[["bin_id", "price_per_token"]].copy()
    work["bin_id"] = pd.to_numeric(work["bin_id"], errors="coerce")
    work["price_per_token"] = pd.to_numeric(work["price_per_token"], errors="coerce")
    work = work.dropna()
    grouped = work.groupby("bin_id")["price_per_token"].median()
    return {int(bin_id): float(price) for bin_id, price in grouped.items() if price > 0}


def _infer_ratio(price_for_bin: dict[int, float]) -> float:
    """Geometric price step between adjacent bins (``1 + bin_step/10000``)."""
    ordered = sorted(price_for_bin)
    ratios = [
        price_for_bin[b + 1] / price_for_bin[b]
        for b in ordered
        if (b + 1) in price_for_bin and price_for_bin[b] > 0
    ]
    return float(np.median(ratios)) if ratios else 1.0001


def _bin_deltas(
    liquidity: float, price_lo: float, price_hi: float, liquidity_scale: float
) -> tuple[float, float]:
    """Constant-sum (dx, dy) capacity for one bin over ``[price_lo, price_hi]``.

    ``L`` is normalised by ``liquidity_scale`` to keep magnitudes near unity; the
    auto-fit transform makes the absolute scale irrelevant.
    """
    if liquidity <= 0 or price_lo <= 0 or price_hi <= price_lo or liquidity_scale <= 0:
        return 0.0, 0.0
    ln = liquidity / liquidity_scale
    r_lo = math.sqrt(price_lo)
    r_hi = math.sqrt(price_hi)
    dx = ln * (1.0 / r_lo - 1.0 / r_hi)
    dy = ln * (r_hi - r_lo)
    return max(0.0, dx), max(0.0, dy)


def _visible_bin_deltas(
    trace: SnapshotTrace,
    *,
    frame: GlobalFrame,
    price_for_bin: dict[int, float],
    ratio: float,
    liquidity_scale: float,
) -> list[tuple[int, float, float, float, float]]:
    """``(bin_id, x_amount, y_amount, dx, dy)`` for visible, funded bins (ascending)."""
    out: list[tuple[int, float, float, float, float]] = []
    for bin_id, liquidity, x_amount, y_amount in zip(
        trace.bin_ids, trace.liquidity, trace.x_amount, trace.y_amount, strict=True
    ):
        bin_id = int(bin_id)
        if bin_id < frame.bin_id_min or bin_id > frame.bin_id_max:
            continue
        if float(liquidity) <= 0:
            continue
        price_lo = price_for_bin.get(bin_id)
        if price_lo is None or price_lo <= 0:
            continue
        price_hi = price_lo * ratio
        dx, dy = _bin_deltas(float(liquidity), price_lo, price_hi, liquidity_scale)
        if dx <= 0 and dy <= 0:
            continue
        out.append((bin_id, float(x_amount), float(y_amount), dx, dy))
    out.sort(key=lambda item: item[0])
    return out


def _build_envelope(
    deltas: list[tuple[int, float, float, float, float]],
) -> list[ReserveSegment]:
    """Stack per-bin constant-sum segments tip-to-tail into the reserve curve.

    Walks from the top-left (all USDC) down to the bottom-right (all SOL): start
    at ``(0, sum dy)`` and, for each bin in descending price order, step by
    ``(dx, -dy)``.  Each step is one bin's segment of slope ``-dy/dx ~= -p``.
    """
    descending = list(reversed(deltas))
    total_dy = sum(item[4] for item in descending)
    segments: list[ReserveSegment] = []
    x, y = 0.0, total_dy
    for bin_id, x_amount, y_amount, dx, dy in descending:
        nx, ny = x + dx, y - dy
        segments.append((bin_id, x_amount, y_amount, (x, y), (nx, ny)))
        x, y = nx, ny
    return segments


def _envelope_extent(
    traces: list[SnapshotTrace],
    ghost_layers: list[tuple[int, int]],
    *,
    frame: GlobalFrame,
    price_for_bin: dict[int, float],
    ratio: float,
    liquidity_scale: float,
) -> tuple[float, float]:
    """Largest cumulative ``(x, y)`` across the visible ghost window."""
    x_max = 0.0
    y_max = 0.0
    for layer_index, _ in ghost_layers:
        deltas = _visible_bin_deltas(
            traces[layer_index],
            frame=frame,
            price_for_bin=price_for_bin,
            ratio=ratio,
            liquidity_scale=liquidity_scale,
        )
        for _bin_id, _xa, _ya, start, end in _build_envelope(deltas):
            x_max = max(x_max, start[0], end[0])
            y_max = max(y_max, start[1], end[1])
    if x_max <= 0:
        x_max = 1.0
    if y_max <= 0:
        y_max = 1.0
    return x_max * RESERVE_HEADROOM, y_max * RESERVE_HEADROOM


def _draw_reserve_grid(
    draw: ImageDraw.ImageDraw,
    *,
    plot_box: tuple[int, int, int, int],
    x_max: float,
    y_max: float,
    style: SeismicStyle,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    divisions: int = 6,
) -> None:
    left, top, right, bottom = plot_box
    for i in range(divisions + 1):
        frac = i / divisions
        gx = left + frac * (right - left)
        gy = bottom - frac * (bottom - top)
        draw.line([(gx, top), (gx, bottom)], fill=style.grid_major, width=1)
        draw.line([(left, gy), (right, gy)], fill=style.grid_major, width=1)
        x_val = frac * x_max
        y_val = frac * y_max
        if i > 0:
            draw.text((gx + 3, bottom - 16), f"{x_val:.2f}", fill=(150, 165, 180, 150), font=label_font)
            draw.text((left + 4, gy + 2), f"{y_val:.2f}", fill=(150, 165, 180, 150), font=label_font)


def _draw_envelope_layer(
    draw: ImageDraw.ImageDraw,
    *,
    segments: list[ReserveSegment],
    active_bin_id: int,
    layer: LayerStyle,
    layer_age: int,
    to_screen,
) -> tuple[int, float, float] | None:
    """Draw one snapshot's reserve envelope; return the active segment midpoint."""
    if layer.outline_alpha <= 0 and layer.fill_alpha <= 0:
        return None
    if not segments:
        return None

    seg_alpha = max(layer.outline_alpha, layer.fill_alpha)
    active_mid: tuple[int, float, float] | None = None

    for bin_id, x_amount, y_amount, start, end in segments:
        s0 = to_screen(*start)
        s1 = to_screen(*end)
        if bin_id == active_bin_id:
            base_rgb = _hex_to_rgb(SEISMIC_ACTIVE_COLOR)
        else:
            base_rgb = _composition_rgb(x_amount, y_amount)
        rgb = _dim_rgb(base_rgb, layer_age=layer_age)
        width = layer.outline_width + (2 if bin_id == active_bin_id else 1)
        if bin_id == active_bin_id:
            draw.line([s0, s1], fill=(0, 0, 0, seg_alpha), width=width + 3)
            mid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            mx, my = to_screen(*mid)
            active_mid = (bin_id, mx, my)
        draw.line([s0, s1], fill=(*rgb, seg_alpha), width=width)

    # Vertex dots only on the freshest (NOW) layer to mark the per-bin kinks.
    if layer_age == 0:
        for _bin_id, _xa, _ya, start, _end in segments:
            sx, sy = to_screen(*start)
            draw.ellipse([sx - 2, sy - 2, sx + 2, sy + 2], fill=(*_TRACE_TIP_RGB, seg_alpha))
        last = segments[-1]
        ex, ey = to_screen(*last[4])
        draw.ellipse([ex - 2, ey - 2, ex + 2, ey + 2], fill=(*_TRACE_TIP_RGB, seg_alpha))

    return active_mid


def render_reserve_frame(
    traces: list[SnapshotTrace],
    *,
    frame: GlobalFrame,
    current_index: int,
    transition_blend: float = 1.0,
    ghost_indices: list[int] | None = None,
    liquidity_scale: float,
    price_for_bin: dict[int, float],
    token_x: str,
    token_y: str,
    pool_address: str,
    width: int = 1400,
    height: int = 800,
    style: SeismicStyle = SeismicStyle(),
) -> np.ndarray:
    """Render the reserve-curve ``(x, y)`` projection of one snapshot."""
    current_index = max(0, min(current_index, len(traces) - 1))
    total_traces = len(traces)
    ratio = _infer_ratio(price_for_bin)

    img = Image.new("RGBA", (width, height), style.background + (255,))
    plot_box = (96, 82, width - 20, height - 76)
    left, top, right, bottom = plot_box
    plot_bg = Image.fromarray(_plot_background(right - left, bottom - top), mode="RGB")
    img.paste(plot_bg, (left, top))

    draw = ImageDraw.Draw(img, "RGBA")
    title_font = _load_mono_font(TITLE_FONT_SIZE)
    subtitle_font = _load_mono_font(SUBTITLE_FONT_SIZE)
    hud_font = _load_mono_font(RESERVE_HUD_FONT_SIZE)
    channel_font = _load_mono_font(CHANNEL_FONT_SIZE)
    grid_label_font = _load_mono_font(15)

    ghost_layers = _resolve_ghost_layers(current_index, ghost_indices)
    x_max, y_max = _envelope_extent(
        traces,
        ghost_layers,
        frame=frame,
        price_for_bin=price_for_bin,
        ratio=ratio,
        liquidity_scale=liquidity_scale,
    )

    def to_screen(rx: float, ry: float) -> tuple[float, float]:
        sx = left + (rx / x_max) * (right - left)
        sy = bottom - (ry / y_max) * (bottom - top)
        return sx, sy

    _draw_reserve_grid(
        draw,
        plot_box=plot_box,
        x_max=x_max,
        y_max=y_max,
        style=style,
        label_font=grid_label_font,
    )

    active_mid: tuple[int, float, float] | None = None
    for layer_index, layer_age in ghost_layers:
        layer = _layer_style(layer_age, transition_blend)
        deltas = _visible_bin_deltas(
            traces[layer_index],
            frame=frame,
            price_for_bin=price_for_bin,
            ratio=ratio,
            liquidity_scale=liquidity_scale,
        )
        segments = _build_envelope(deltas)
        mid = _draw_envelope_layer(
            draw,
            segments=segments,
            active_bin_id=traces[layer_index].active_bin_id,
            layer=layer,
            layer_age=layer_age,
            to_screen=to_screen,
        )
        if layer_age == 0 and mid is not None:
            active_mid = mid

    # Active bin: marker dot + label at the kink where the curve's slope = -p.
    if active_mid is not None:
        active_bin_id, ax, ay = active_mid
        draw.ellipse([ax - 6, ay - 6, ax + 6, ay + 6], fill=(0, 0, 0, 180))
        draw.ellipse([ax - 5, ay - 5, ax + 5, ay + 5], fill=(255, 255, 255, 255))
        label = f"ACTIVE {active_bin_id}"
        tx, ty = ax + 8, ay - 22
        bbox = hud_font.getbbox(label)
        draw.rectangle(
            [tx + bbox[0] - 5, ty + bbox[1] - 3, tx + bbox[2] + 5, ty + bbox[3] + 3],
            fill=(245, 247, 250, 235),
        )
        draw.text((tx, ty), label, fill=(20, 22, 28, 255), font=hud_font)

    # Channel tags down the trail, echoing the seismic NOW / T-n labels.
    for layer_index, layer_age in ghost_layers:
        if layer_age not in (0, 1, 2, 4, 8):
            continue
        label = "NOW" if layer_age == 0 else f"T-{layer_age}"
        alpha = 220 if layer_age == 0 else max(50, int(180 * (GHOST_OUTLINE_DECAY**layer_age)))
        draw.text(
            (left + 8, top + 8 + layer_age * 16),
            label,
            fill=(*_trace_outline_rgb(layer_age), alpha),
            font=channel_font,
        )

    current = traces[current_index]
    title = "DLMM RESERVE CURVE"
    subtitle = (
        f"{token_x}/{token_y} | {pool_address[:6]}...{pool_address[-4:]} | "
        f"SNAP {current.snapshot_index + 1}/{total_traces} | {current.fetched_label}"
    )
    draw.text((left, 13), title, fill=style.hud, font=title_font)
    draw.text((left, 38), subtitle, fill=(*style.hud[:3], 240), font=subtitle_font)

    # Axis captions describing the constant-sum stacking.
    axis_y = height - 62
    draw.text(
        (left + (right - left) / 2 - 170, axis_y),
        f"x = CUMULATIVE {token_x} RESERVE (X)",
        fill=style.hud,
        font=hud_font,
    )
    draw.text(
        (left, top - 26),
        f"y = CUMULATIVE {token_y} RESERVE (Y)  ·  bins are constant-sum (slope -p)",
        fill=style.hud,
        font=hud_font,
    )

    legend_x = right - 230
    legend_y = top + 26
    legend_line = int(CHANNEL_FONT_SIZE * 1.3)
    draw.text(
        (legend_x, legend_y),
        f"| {token_y} (Y)",
        fill=(*_composition_rgb(0.0, 1.0), 240),
        font=channel_font,
    )
    draw.text(
        (legend_x, legend_y + legend_line),
        f"| {token_x} (X)",
        fill=(*_composition_rgb(1.0, 0.0), 240),
        font=channel_font,
    )
    draw.text(
        (legend_x, legend_y + 2 * legend_line),
        f"| {token_x}+{token_y} (mix)",
        fill=(*_composition_rgb(1.0, 1.0), 240),
        font=channel_font,
    )
    draw.text(
        (legend_x, legend_y + 3 * legend_line),
        "| ACTIVE",
        fill=(*_hex_to_rgb(SEISMIC_ACTIVE_COLOR), 255),
        font=channel_font,
    )

    return np.asarray(img.convert("RGB"))
