"""Reserve-space (x, y) projection of the temporal bin-atlas histogram.

The seismic renderer draws the liquidity distribution in ``(p, L)`` space — each
bar sits at a bin price ``p`` with height equal to the bin liquidity ``L``.  This
module renders the *same* per-snapshot data through the constant-product
embedding the pool reserves obey,

    p = y / x        L = x * y

so a bin's ``(p, L)`` pair maps to a virtual reserve point

    x = sqrt(L / p)        y = sqrt(L * p)        (x, y) = (SOL, USDC).

A single histogram bar is the vertical segment ``{(p, l) : 0 <= l <= L}`` at a
fixed price ``p``.  Under the embedding that segment maps to a straight **line
segment from the origin** out to ``(sqrt(L/p), sqrt(L*p))`` — every point on it
keeps ``y/x = p`` while the radius grows with ``sqrt(l)``.  So each bin becomes a
ray whose angle encodes its price and whose length encodes its liquidity; the
collection of rays is "where the local liquidity line segments fall in (x, y)".

This is a purely additive view: it reuses :mod:`meteora_bin_atlas.temporal.seismic`
trace structures and styling and never mutates the existing pipeline.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from meteora_bin_atlas.explore.labels import bar_color_for_bin
from meteora_bin_atlas.temporal.seismic import (
    CHANNEL_FONT_SIZE,
    GHOST_OUTLINE_DECAY,
    SEISMIC_TOKEN_COLORS,
    SUBTITLE_FONT_SIZE,
    TITLE_FONT_SIZE,
    GlobalFrame,
    LayerStyle,
    SeismicStyle,
    SnapshotTrace,
    _hex_to_rgb,
    _layer_rgb,
    _layer_style,
    _load_mono_font,
    _plot_background,
    _resolve_ghost_layers,
    _trace_outline_rgb,
)

# Auto-fit headroom: the widest visible tip maps to (extent / headroom) so the
# fan of segments always keeps a margin away from the plot edges.
RESERVE_HEADROOM = 1.10
RESERVE_HUD_FONT_SIZE = 19
_TRACE_TIP_RGB = (232, 244, 255)


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


def _reserve_point(liquidity: float, price: float, liquidity_scale: float) -> tuple[float, float]:
    """Virtual reserves ``(x, y) = (sqrt(L/p), sqrt(L*p))`` for one bin.

    ``L`` is normalised by ``liquidity_scale`` purely to keep the magnitudes near
    unity; the auto-fit transform makes the absolute scale irrelevant.
    """
    if price <= 0 or liquidity <= 0 or liquidity_scale <= 0:
        return 0.0, 0.0
    ln = liquidity / liquidity_scale
    return math.sqrt(ln / price), math.sqrt(ln * price)


def _visible_tips(
    trace: SnapshotTrace,
    *,
    frame: GlobalFrame,
    price_for_bin: dict[int, float],
    liquidity_scale: float,
) -> list[tuple[int, float, float, float, float]]:
    """Return ``(bin_id, x, y, x_amount, y_amount)`` for visible, funded bins."""
    tips: list[tuple[int, float, float, float, float]] = []
    for bin_id, liquidity, x_amount, y_amount in zip(
        trace.bin_ids, trace.liquidity, trace.x_amount, trace.y_amount, strict=True
    ):
        bin_id = int(bin_id)
        if bin_id < frame.bin_id_min or bin_id > frame.bin_id_max:
            continue
        if float(liquidity) <= 0:
            continue
        price = price_for_bin.get(bin_id)
        if price is None or price <= 0:
            continue
        rx, ry = _reserve_point(float(liquidity), price, liquidity_scale)
        if rx <= 0 and ry <= 0:
            continue
        tips.append((bin_id, rx, ry, float(x_amount), float(y_amount)))
    return tips


def _reserve_extent(
    traces: list[SnapshotTrace],
    ghost_layers: list[tuple[int, int]],
    *,
    frame: GlobalFrame,
    price_for_bin: dict[int, float],
    liquidity_scale: float,
) -> tuple[float, float]:
    """Largest ``(x, y)`` tip across the visible ghost window (origin-anchored)."""
    x_max = 0.0
    y_max = 0.0
    for layer_index, _ in ghost_layers:
        for _bin_id, rx, ry, _xa, _ya in _visible_tips(
            traces[layer_index],
            frame=frame,
            price_for_bin=price_for_bin,
            liquidity_scale=liquidity_scale,
        ):
            x_max = max(x_max, rx)
            y_max = max(y_max, ry)
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


def _draw_reserve_layer(
    draw: ImageDraw.ImageDraw,
    *,
    trace: SnapshotTrace,
    frame: GlobalFrame,
    price_for_bin: dict[int, float],
    liquidity_scale: float,
    layer: LayerStyle,
    layer_age: int,
    origin: tuple[float, float],
    to_screen,
) -> tuple[int, float, float] | None:
    """Draw one snapshot's reserve segments; return the active bin's screen tip."""
    if layer.outline_alpha <= 0 and layer.fill_alpha <= 0:
        return None

    tips = _visible_tips(
        trace, frame=frame, price_for_bin=price_for_bin, liquidity_scale=liquidity_scale
    )
    if not tips:
        return None

    seg_alpha = max(layer.outline_alpha, layer.fill_alpha)
    frontier: list[tuple[float, float]] = []
    active_tip: tuple[int, float, float] | None = None

    for bin_id, rx, ry, x_amount, y_amount in tips:
        sx, sy = to_screen(rx, ry)
        frontier.append((sx, sy))

        distance = bin_id - trace.active_bin_id
        if distance == 0:
            color_hex = SEISMIC_TOKEN_COLORS["mix"]
            active_tip = (bin_id, sx, sy)
        else:
            color_hex = bar_color_for_bin(
                x_amount, y_amount, distance, colors=SEISMIC_TOKEN_COLORS
            )
        rgb = _layer_rgb(color_hex, layer_age=layer_age)
        draw.line([origin, (sx, sy)], fill=(*rgb, seg_alpha), width=layer.outline_width)

    # Frontier polyline links the tips in bin order — the reserve-space liquidity
    # profile (the warped image of the histogram envelope).
    if len(frontier) >= 2:
        outline_rgb = _trace_outline_rgb(layer_age)
        draw.line(frontier, fill=(*outline_rgb, seg_alpha), width=layer.outline_width, joint="curve")

    # Tip dots only on the freshest (NOW) layer to avoid clutter in the trail.
    if layer_age == 0:
        for sx, sy in frontier:
            draw.ellipse([sx - 2.5, sy - 2.5, sx + 2.5, sy + 2.5], fill=(*_TRACE_TIP_RGB, seg_alpha))

    return active_tip


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
    """Render the reserve-space ``(x, y)`` projection of one snapshot."""
    current_index = max(0, min(current_index, len(traces) - 1))
    total_traces = len(traces)

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
    x_max, y_max = _reserve_extent(
        traces,
        ghost_layers,
        frame=frame,
        price_for_bin=price_for_bin,
        liquidity_scale=liquidity_scale,
    )

    def to_screen(rx: float, ry: float) -> tuple[float, float]:
        sx = left + (rx / x_max) * (right - left)
        sy = bottom - (ry / y_max) * (bottom - top)
        return sx, sy

    origin = to_screen(0.0, 0.0)

    _draw_reserve_grid(
        draw,
        plot_box=plot_box,
        x_max=x_max,
        y_max=y_max,
        style=style,
        label_font=grid_label_font,
    )

    active_tip: tuple[int, float, float] | None = None
    for layer_index, layer_age in ghost_layers:
        layer = _layer_style(layer_age, transition_blend)
        tip = _draw_reserve_layer(
            draw,
            trace=traces[layer_index],
            frame=frame,
            price_for_bin=price_for_bin,
            liquidity_scale=liquidity_scale,
            layer=layer,
            layer_age=layer_age,
            origin=origin,
            to_screen=to_screen,
        )
        if layer_age == 0 and tip is not None:
            active_tip = tip

    # Active bin: bright origin->tip ray plus a marker dot and label.
    if active_tip is not None:
        active_bin_id, ax, ay = active_tip
        draw.line([origin, (ax, ay)], fill=(0, 0, 0, 150), width=5)
        draw.line([origin, (ax, ay)], fill=style.active_bin, width=2)
        draw.ellipse([ax - 5, ay - 5, ax + 5, ay + 5], fill=style.active_bin)
        label = f"ACTIVE {active_bin_id}"
        tx, ty = ax + 8, ay - 22
        bbox = hud_font.getbbox(label)
        draw.rectangle(
            [tx + bbox[0] - 5, ty + bbox[1] - 3, tx + bbox[2] + 5, ty + bbox[3] + 3],
            fill=(200, 90, 0, 230),
        )
        draw.text((tx, ty), label, fill=(255, 255, 255, 255), font=hud_font)

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
    title = "DLMM RESERVE MAP"
    subtitle = (
        f"{token_x}/{token_y} | {pool_address[:6]}...{pool_address[-4:]} | "
        f"SNAP {current.snapshot_index + 1}/{total_traces} | {current.fetched_label}"
    )
    draw.text((left, 13), title, fill=style.hud, font=title_font)
    draw.text((left, 38), subtitle, fill=(*style.hud[:3], 240), font=subtitle_font)

    # Axis captions describing the embedding.
    axis_y = height - 62
    draw.text(
        (left + (right - left) / 2 - 150, axis_y),
        f"x = SQRT(L/p)  ·  {token_x} RESERVE (X)",
        fill=style.hud,
        font=hud_font,
    )
    draw.text(
        (left, top - 26),
        f"y = SQRT(L*p)  ·  {token_y} RESERVE (Y)",
        fill=style.hud,
        font=hud_font,
    )

    legend_x = right - 230
    legend_y = top + 26
    legend_line = int(CHANNEL_FONT_SIZE * 1.3)
    draw.text(
        (legend_x, legend_y),
        f"| {token_y} (Y)",
        fill=(*_layer_rgb(SEISMIC_TOKEN_COLORS["Y"], layer_age=0), 240),
        font=channel_font,
    )
    draw.text(
        (legend_x, legend_y + legend_line),
        f"| {token_x} (X)",
        fill=(*_layer_rgb(SEISMIC_TOKEN_COLORS["X"], layer_age=0), 240),
        font=channel_font,
    )
    draw.text(
        (legend_x, legend_y + 2 * legend_line),
        f"| {token_x}+{token_y}",
        fill=(*_layer_rgb(SEISMIC_TOKEN_COLORS["mix"], layer_age=0), 240),
        font=channel_font,
    )

    return np.asarray(img.convert("RGB"))
