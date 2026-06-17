"""Seismic-style wiggle renderer for temporal bin-atlas snapshots."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

# Skyline rises from the plot floor; this fraction caps peak height below the top margin.
CURRENT_DEFLECTION_RATIO = 0.86
GHOST_HISTORY = 8
ROLLING_SNAPSHOTS = 5
DISPLAY_PAD_BINS = 4
ZOOM_IN_RATIO = 0.72
MIN_DISPLAY_BINS = 25
# Active bin must stay at least this many bins away from the viewport edge
# before the window scrolls.  Gives a natural left/right float within the plot.
VIEWPORT_EDGE_BAND = 10
CURRENT_FILL_ALPHA = 150     # NOW frame: solidly colored so there is color to fade from
CURRENT_OUTLINE_ALPHA = 235
# Colored ghost fills collapse FAST: an afterimage should darken to grey and
# vanish within a couple of snapshots so the eye locks onto the live active bar
# rather than a long white smear of stale markers.
GHOST_FILL_BASE = 120        # age-1 already well below NOW fill
GHOST_FILL_DECAY = 0.42      # 120→50→21→9→4→… effectively gone by age 4
GHOST_OUTLINE_BASE = 130
GHOST_OUTLINE_DECAY = 0.45   # outline fade tracking the fill
GHOST_FILL_CUTOFF_AGE = 99   # no hard cutoff — let the gradual decay run the whole trail
GHOST_TRACE_Y_OFFSET = 3.0
GHOST_TRACE_X_DRIFT = 0.35

# Sideways drift seismograph in the far-left margin: time runs top→bottom,
# horizontal deflection = active bin's drift left/right from its series centre.
DRIFT_STRIP_LEFT = 18
DRIFT_STRIP_RIGHT = 154
DRIFT_STRIP_EDGE_PAD = 4.0
# Centre baseline for the drift strip = trailing rolling mean of the active bin.
DRIFT_ROLLING_WINDOW = 20
# Rekordbox-style scrolling: NOW is pinned at the bottom and history scrolls up
# off the top.  The visible window is sized in seconds of video; the renderer
# converts it to a snapshot count from the active fps (fallback used directly
# when no explicit window is supplied).
DRIFT_WINDOW_SECONDS = 5.0
DRIFT_WINDOW = 120
# Auto-rescale headroom: peak deflection maps to (half-width / headroom), so the
# trace zooms out horizontally as drift grows and always keeps a width margin.
DRIFT_HEADROOM = 1.18

# Neon instrument palette for temporal MP4 (notebook TOKEN_COLORS unchanged).
SEISMIC_TOKEN_COLORS = {
    "X": "#FF2BD6",
    "Y": "#00F0FF",
    "mix": "#E8F4FF",
    "empty": "#000000",
}
# Active bin is always painted orange so it stays legible regardless of its token
# mix. Every other bin is coloured by its actual proportional composition: a
# continuous blend from cyan (all Y / USDC) through purple (balanced) to magenta
# (all X / SOL). See _composition_rgb.
SEISMIC_ACTIVE_COLOR = "#FF8A00"

_TRACE_OUTLINE_BASE = (232, 244, 255)
TITLE_FONT_SIZE = 26
SUBTITLE_FONT_SIZE = 20
HUD_FONT_SIZE = 19
CHANNEL_FONT_SIZE = 17
_MONO_FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "C:/Windows/Fonts/consola.ttf",
)


@dataclass(frozen=True)
class SeismicStyle:
    background: tuple[int, int, int] = (0, 0, 0)
    grid_major: tuple[int, int, int, int] = (60, 80, 100, 35)
    baseline: tuple[int, int, int, int] = (80, 100, 120, 90)
    active_bin: tuple[int, int, int, int] = (255, 255, 255, 255)
    hud: tuple[int, int, int, int] = (190, 210, 230, 255)
    wiggle_outline: tuple[int, int, int, int] = (232, 244, 255, 220)


@dataclass(frozen=True)
class GlobalFrame:
    """Visible bin-id viewport (dynamic rolling window or full atlas extent)."""

    bin_id_min: int
    bin_id_max: int

    @property
    def bin_ids(self) -> np.ndarray:
        return np.arange(self.bin_id_min, self.bin_id_max + 1, dtype=np.int32)

    @property
    def n_bins(self) -> int:
        return int(self.bin_id_max - self.bin_id_min + 1)


@dataclass(frozen=True)
class SnapshotTrace:
    snapshot_index: int
    fetched_label: str
    active_bin_id: int
    bin_ids: np.ndarray
    liquidity: np.ndarray
    x_amount: np.ndarray
    y_amount: np.ndarray


@dataclass(frozen=True)
class LayerStyle:
    fill_alpha: int
    outline_alpha: int
    outline_width: int


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _resolve_global_frame(series_df: pd.DataFrame) -> GlobalFrame:
    bin_ids = pd.to_numeric(series_df["bin_id"], errors="coerce").dropna().astype(int)
    return GlobalFrame(bin_id_min=int(bin_ids.min()), bin_id_max=int(bin_ids.max()))


def _active_bin_id(group: pd.DataFrame) -> int:
    active_mask = group["is_active_bin"].astype(str).str.lower().eq("true")
    if not active_mask.any():
        active_mask = group["is_active_bin"] == True  # noqa: E712
    if not active_mask.any():
        raise ValueError("No active bin row in snapshot group")
    return int(group.loc[active_mask, "bin_id"].iloc[0])


def _column_for_bin_ids(group: pd.DataFrame, frame: GlobalFrame, column: str) -> np.ndarray:
    indexed = (
        group.set_index("bin_id")[column]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0)
    )
    return indexed.reindex(frame.bin_ids, fill_value=0).to_numpy(dtype=np.float64)


def prepare_snapshot_traces(
    series_df: pd.DataFrame,
    *,
    zoom_bins: int | None = None,
) -> tuple[list[SnapshotTrace], float, GlobalFrame]:
    """
    Align each snapshot to a fixed global bin-id grid.

    zoom_bins is accepted for CLI compatibility but the axis spans all bin ids
    observed across the series (union of bounded fetches).
    """
    _ = zoom_bins
    frame = _resolve_global_frame(series_df)
    snapshot_indices = sorted(series_df["snapshot_index"].unique())
    traces: list[SnapshotTrace] = []
    liquidity_max = 0.0

    for snapshot_index in snapshot_indices:
        group = series_df[series_df["snapshot_index"] == snapshot_index]
        liquidity = _column_for_bin_ids(group, frame, "liquidity")
        x_amount = _column_for_bin_ids(group, frame, "x_amount")
        y_amount = _column_for_bin_ids(group, frame, "y_amount")
        liquidity_max = max(liquidity_max, float(liquidity.max()))

        fetched_at = group["fetched_at_utc"].iloc[0]
        if hasattr(fetched_at, "strftime"):
            fetched_label = fetched_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            fetched_label = str(fetched_at)

        traces.append(
            SnapshotTrace(
                snapshot_index=int(snapshot_index),
                fetched_label=fetched_label,
                active_bin_id=_active_bin_id(group),
                bin_ids=frame.bin_ids,
                liquidity=liquidity,
                x_amount=x_amount,
                y_amount=y_amount,
            )
        )

    scale = liquidity_max * 1.05 if liquidity_max > 0 else 1.0
    return traces, scale, frame


def _observed_extent(trace: SnapshotTrace) -> tuple[int, int]:
    """Bin-id span with liquidity plus the snapshot's active bin."""
    has_liquidity = trace.liquidity > 0
    lo = int(trace.active_bin_id)
    hi = int(trace.active_bin_id)
    if has_liquidity.any():
        observed = trace.bin_ids[has_liquidity]
        lo = min(lo, int(observed.min()))
        hi = max(hi, int(observed.max()))
    return lo, hi


def _padded_bounds(lo: int, hi: int, *, pad_bins: int = DISPLAY_PAD_BINS) -> tuple[int, int]:
    padded_lo = lo - pad_bins
    padded_hi = hi + pad_bins
    span = padded_hi - padded_lo + 1
    if span >= MIN_DISPLAY_BINS:
        return padded_lo, padded_hi
    center = (padded_lo + padded_hi) // 2
    half = MIN_DISPLAY_BINS // 2
    return center - half, center + half


def _rolling_observed_bounds(
    traces: list[SnapshotTrace],
    current_index: int,
    *,
    rolling_snapshots: int = ROLLING_SNAPSHOTS,
) -> tuple[int, int]:
    """Union of bins with liquidity (plus active) across the recent snapshot window."""
    start = max(0, current_index - rolling_snapshots + 1)
    lo = int(traces[start].active_bin_id)
    hi = lo
    for trace in traces[start : current_index + 1]:
        trace_lo, trace_hi = _observed_extent(trace)
        lo = min(lo, trace_lo)
        hi = max(hi, trace_hi)
    return lo, hi


def _rolling_content_bounds(
    traces: list[SnapshotTrace],
    current_index: int,
    *,
    rolling_snapshots: int = ROLLING_SNAPSHOTS,
    pad_bins: int = DISPLAY_PAD_BINS,
) -> tuple[int, int]:
    """Padded rolling bounds used for zoom-out decisions."""
    lo, hi = _rolling_observed_bounds(traces, current_index, rolling_snapshots=rolling_snapshots)
    return _padded_bounds(lo, hi, pad_bins=pad_bins)


def _clamp_to_atlas(lo: int, hi: int, atlas: GlobalFrame | None) -> tuple[int, int]:
    if atlas is None:
        return lo, hi
    return max(atlas.bin_id_min, lo), min(atlas.bin_id_max, hi)


def compute_display_frame(
    traces: list[SnapshotTrace],
    current_index: int,
    previous: GlobalFrame | None,
    *,
    atlas: GlobalFrame | None = None,
    pad_bins: int = DISPLAY_PAD_BINS,
    min_display_bins: int = MIN_DISPLAY_BINS,
    viewport_edge_band: int = VIEWPORT_EDGE_BAND,
    rolling_snapshots: int = ROLLING_SNAPSHOTS,
    zoom_in_ratio: float = ZOOM_IN_RATIO,
) -> GlobalFrame:
    """
    Sliding-window viewport with a dead-band.

    The active bin floats freely within the plot; the window only scrolls when
    the active bin drifts within viewport_edge_band bins of either edge.  The
    span grows to accommodate content and shrinks (with hysteresis) when content
    is much narrower than the current window.
    """
    observed_lo, observed_hi = _rolling_observed_bounds(
        traces,
        current_index,
        rolling_snapshots=rolling_snapshots,
    )
    observed_lo, observed_hi = _clamp_to_atlas(observed_lo, observed_hi, atlas)
    active = int(traces[current_index].active_bin_id)

    # Required span: content + padding on each side, at least min_display_bins.
    content_span = max(
        observed_hi - observed_lo + 1 + 2 * pad_bins,
        min_display_bins,
    )

    if previous is None:
        # First frame: center on the active bin.
        span = content_span
        lo = active - span // 2
        hi = lo + span - 1
        return GlobalFrame(*_clamp_to_atlas(lo, hi, atlas))

    prev_span = previous.n_bins

    # Decide new span: only grow, or shrink if content is much narrower.
    if content_span < zoom_in_ratio * prev_span:
        span = content_span
    else:
        span = max(prev_span, content_span)

    # Inherit the previous window position, resizing around its centre if needed.
    lo = previous.bin_id_min
    hi = previous.bin_id_max
    if span != prev_span:
        mid = (lo + hi) // 2
        lo = mid - span // 2
        hi = lo + span - 1

    # Scroll minimally so the active bin stays inside the middle ±30% of the
    # viewport. The dead-band width is 40% of the span (20% on each side of
    # center); scrolling only kicks in when the bar drifts past the outer 30%.
    margin = max(viewport_edge_band, round(0.30 * span))
    if active - lo < margin:
        shift = margin - (active - lo)
        lo -= shift
        hi -= shift
    elif hi - active < margin:
        shift = margin - (hi - active)
        lo += shift
        hi += shift

    # Safety: make sure observed content is still inside the window.
    if observed_lo < lo:
        delta = lo - observed_lo
        lo -= delta
        hi -= delta
    if observed_hi > hi:
        delta = observed_hi - hi
        lo += delta
        hi += delta

    return GlobalFrame(*_clamp_to_atlas(lo, hi, atlas))


@lru_cache(maxsize=8)
def _load_mono_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _MONO_FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _plot_background(plot_width: int, plot_height: int) -> np.ndarray:
    bg = np.full((plot_height, plot_width, 3), (2, 2, 4), dtype=np.uint8)
    bg[::2] = np.minimum(bg[::2] + 1, 255)
    return bg


def _bin_cell_width(*, frame: GlobalFrame, plot_left: int, plot_right: int) -> float:
    return (plot_right - plot_left) / frame.n_bins


def _x_center_for_bin_id(
    bin_id: int,
    *,
    frame: GlobalFrame,
    plot_left: int,
    cell_w: float,
) -> float:
    index = int(bin_id - frame.bin_id_min)
    return plot_left + (index + 0.5) * cell_w


def _x_edge_for_bin_id(
    bin_id: int,
    *,
    frame: GlobalFrame,
    plot_left: int,
    cell_w: float,
) -> float:
    index = int(bin_id - frame.bin_id_min)
    return plot_left + index * cell_w


def _dim_rgb(rgb: tuple[int, int, int], *, layer_age: int) -> tuple[int, int, int]:
    """Fade an RGB toward the background for ghost layers (age > 0)."""
    if layer_age <= 0:
        return rgb
    bg = SeismicStyle.background
    # Aggressive hue dimming: a bar drops toward grey/black within a frame or two
    # so afterimages read as faint ghosts, not solid columns.
    mute = 0.5**layer_age
    return tuple(int(bg[i] + (rgb[i] - bg[i]) * mute) for i in range(3))


def _layer_rgb(hex_color: str, *, layer_age: int) -> tuple[int, int, int]:
    return _dim_rgb(_hex_to_rgb(hex_color), layer_age=layer_age)


def _composition_rgb(
    x_amount: float,
    y_amount: float,
    *,
    token_colors: dict[str, str] | None = None,
    token_color_mode: str = "blend",
) -> tuple[int, int, int]:
    """Colour a bin by its actual token mix: Y hue → blend → X hue.

    The blend fraction is the bin's share of token X, so a bin holding only Y
    is the Y colour, only X is the X colour, and a balanced bin sits at the
    midpoint — a continuous read of inventory composition.
    """
    palette = token_colors or SEISMIC_TOKEN_COLORS
    x = max(0.0, x_amount)
    y = max(0.0, y_amount)
    total = x + y
    if total <= 0:
        return _hex_to_rgb(palette["empty"])
    share_x = x / total
    y_rgb = _hex_to_rgb(palette["Y"])
    x_rgb = _hex_to_rgb(palette["X"])
    if token_color_mode == "dominant":
        return x_rgb if share_x >= 0.5 else y_rgb
    return tuple(int(round(y_rgb[i] + (x_rgb[i] - y_rgb[i]) * share_x)) for i in range(3))


def _trace_outline_rgb(layer_age: int) -> tuple[int, int, int]:
    if layer_age <= 0:
        return _TRACE_OUTLINE_BASE
    bg = SeismicStyle.background
    mute = 0.45**layer_age
    return tuple(int(bg[i] + (_TRACE_OUTLINE_BASE[i] - bg[i]) * mute) for i in range(3))


def _ghost_alphas(layer_age: int) -> tuple[int, int]:
    """Steady-state alpha for a layer `layer_age` snapshots behind the current one."""
    if layer_age <= 0:
        return CURRENT_FILL_ALPHA, CURRENT_OUTLINE_ALPHA
    fill = max(0, int(GHOST_FILL_BASE * (GHOST_FILL_DECAY**layer_age)))
    if layer_age >= GHOST_FILL_CUTOFF_AGE:
        fill = 0
    outline = max(4, int(GHOST_OUTLINE_BASE * (GHOST_OUTLINE_DECAY**layer_age)))
    return fill, outline


def _layer_style(layer_age: int, transition_blend: float) -> LayerStyle:
    blend = max(0.0, min(1.0, transition_blend))
    ghost_fill, ghost_outline = _ghost_alphas(layer_age)

    if layer_age == 0:
        return LayerStyle(
            fill_alpha=int(CURRENT_FILL_ALPHA * blend),
            outline_alpha=int(CURRENT_OUTLINE_ALPHA * blend),
            outline_width=3,
        )
    if layer_age == 1:
        return LayerStyle(
            fill_alpha=int(ghost_fill * blend),
            outline_alpha=int(CURRENT_OUTLINE_ALPHA + (ghost_outline - CURRENT_OUTLINE_ALPHA) * blend),
            outline_width=2 if blend < 0.5 else 1,
        )
    return LayerStyle(
        fill_alpha=ghost_fill,
        outline_alpha=ghost_outline,
        outline_width=1,
    )


def _nice_step(raw: int, candidates: tuple[int, ...]) -> int:
    for nice in candidates:
        if nice >= raw:
            return nice
    return max(1, raw)


def _draw_grid(
    draw: ImageDraw.ImageDraw,
    *,
    frame: GlobalFrame,
    plot_box: tuple[int, int, int, int],
    trace_y: float,
    style: SeismicStyle,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    left, top, right, bottom = plot_box
    cell_w = _bin_cell_width(frame=frame, plot_left=left, plot_right=right)

    # Adaptive grid step: target ~15 gridlines across the plot.
    # Snap to a "nice" interval (2, 5, 10, 20 …) so lines always land on round bin ids.
    grid_step = _nice_step(max(1, frame.n_bins // 15), (2, 5, 10, 20, 50, 100))
    # Coarser labelled step (~6 labels) so the axis reads round bin ids.
    label_step = _nice_step(
        max(grid_step, frame.n_bins // 6),
        (10, 20, 25, 50, 100, 200, 250, 500, 1000),
    )

    def aligned_first(step: int) -> int:
        if frame.bin_id_min >= 0:
            return (frame.bin_id_min // step) * step
        return -((-frame.bin_id_min + step - 1) // step) * step

    bin_id = aligned_first(grid_step)
    while bin_id <= frame.bin_id_max:
        if bin_id >= frame.bin_id_min:
            x = _x_edge_for_bin_id(bin_id, frame=frame, plot_left=left, cell_w=cell_w)
            draw.line([(x, top), (x, bottom)], fill=style.grid_major, width=1)
        bin_id += grid_step

    # Faint gray bin-id labels on the coarse gridlines.
    bin_id = aligned_first(label_step)
    while bin_id <= frame.bin_id_max:
        if bin_id >= frame.bin_id_min:
            x = _x_edge_for_bin_id(bin_id, frame=frame, plot_left=left, cell_w=cell_w)
            draw.text((x + 3, top + 4), str(bin_id), fill=(150, 165, 180, 150), font=label_font)
        bin_id += label_step

    draw.line([(left, trace_y), (right, trace_y)], fill=style.baseline, width=1)


def _draw_active_bin_marker(
    draw: ImageDraw.ImageDraw,
    *,
    active_bin_id: int,
    frame: GlobalFrame,
    plot_box: tuple[int, int, int, int],
    style: SeismicStyle,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    label_y: int | None = None,
) -> float:
    left, top, right, bottom = plot_box
    cell_w = _bin_cell_width(frame=frame, plot_left=left, plot_right=right)
    active_x = round(
        _x_center_for_bin_id(active_bin_id, frame=frame, plot_left=left, cell_w=cell_w)
    )
    half = max(4.0, cell_w / 2.0)
    pole_top = label_y if label_y is not None else top
    # White pole/flag marks the active bin; the bars already carry proportional
    # composition colour, so white is a free, unambiguous locator.
    draw.rectangle([active_x - half, pole_top, active_x + half, bottom], fill=(255, 255, 255, 80))
    draw.line([(active_x, pole_top), (active_x, bottom)], fill=(0, 0, 0, 150), width=5)
    draw.line([(active_x, pole_top), (active_x, bottom)], fill=(255, 255, 255, 255), width=3)
    tick = 8
    draw.line([(active_x - tick, pole_top), (active_x + tick, pole_top)], fill=(255, 255, 255, 255), width=3)
    draw.line([(active_x - tick, bottom), (active_x + tick, bottom)], fill=(255, 255, 255, 255), width=3)
    label = f"ACTIVE {active_bin_id}"
    tx = active_x + 6
    ty = label_y if label_y is not None else top + 4
    bbox = font.getbbox(label)
    pad_x, pad_y = 5, 3
    draw.rectangle(
        [tx + bbox[0] - pad_x, ty + bbox[1] - pad_y,
         tx + bbox[2] + pad_x, ty + bbox[3] + pad_y],
        fill=(245, 247, 250, 235),
    )
    draw.text((tx, ty), label, fill=(20, 22, 28, 255), font=font)
    return active_x


def _draw_horizontal_wiggle_trace(
    draw: ImageDraw.ImageDraw,
    *,
    trace: SnapshotTrace,
    frame: GlobalFrame,
    trace_y: float,
    liquidity_scale: float,
    max_deflection: float,
    plot_left: int,
    plot_right: int,
    layer: LayerStyle,
    layer_age: int,
    token_colors: dict[str, str] | None = None,
    token_color_mode: str = "blend",
    highlight_active_bin: bool = True,
    active_bin_deflection_scale: float = 1.0,
    active_bin_neighbor_cap: float | None = None,
    active_bin_neighbor_radius: int = 4,
) -> None:
    if layer.fill_alpha <= 0 and layer.outline_alpha <= 0:
        return

    cell_w = _bin_cell_width(frame=frame, plot_left=plot_left, plot_right=plot_right)
    x_drift = layer_age * GHOST_TRACE_X_DRIFT
    active_id = int(trace.active_bin_id)
    bars: list[tuple[int, float, float, float, float, int]] = []

    for bin_id, liquidity, x_amount, y_amount in zip(
        trace.bin_ids,
        trace.liquidity,
        trace.x_amount,
        trace.y_amount,
        strict=True,
    ):
        bin_id = int(bin_id)
        if bin_id < frame.bin_id_min or bin_id > frame.bin_id_max:
            continue
        if float(liquidity) <= 0:
            continue

        x_center = _x_center_for_bin_id(bin_id, frame=frame, plot_left=plot_left, cell_w=cell_w)
        x_center += x_drift
        offset = (float(liquidity) / liquidity_scale) * max_deflection
        distance = bin_id - active_id
        if distance == 0 and active_bin_deflection_scale != 1.0:
            offset *= active_bin_deflection_scale
        bars.append((bin_id, x_center, offset, float(x_amount), float(y_amount), distance))

    if active_bin_neighbor_cap is not None:
        neighbor_offsets = [offset for _bid, _x, offset, _xa, _ya, dist in bars if dist != 0]
        if neighbor_offsets:
            cap = max(neighbor_offsets) * active_bin_neighbor_cap
            bars = [
                (bin_id, x_center, min(offset, cap) if bin_id == active_id else offset, xa, ya, dist)
                for bin_id, x_center, offset, xa, ya, dist in bars
            ]

    wiggle_points: list[tuple[float, float]] = []
    for bin_id, x_center, offset, x_amount, y_amount, distance in bars:
        peak_y = trace_y - offset
        wiggle_points.append((x_center, peak_y))

        if layer.fill_alpha <= 0:
            continue

        if distance == 0 and highlight_active_bin:
            # Default temporal look: orange active bar stands out from composition blend.
            base_rgb = _hex_to_rgb(SEISMIC_ACTIVE_COLOR)
        elif token_color_mode == "active_sides":
            if distance < 0:
                base_rgb = _hex_to_rgb((token_colors or SEISMIC_TOKEN_COLORS)["Y"])
            elif distance > 0:
                base_rgb = _hex_to_rgb((token_colors or SEISMIC_TOKEN_COLORS)["X"])
            else:
                base_rgb = _hex_to_rgb((token_colors or SEISMIC_TOKEN_COLORS).get("mix", "E8F4FF"))
        else:
            base_rgb = _composition_rgb(
                x_amount,
                y_amount,
                token_colors=token_colors,
                token_color_mode=token_color_mode,
            )
        rgb = _dim_rgb(base_rgb, layer_age=layer_age)

        x_left = _x_edge_for_bin_id(bin_id, frame=frame, plot_left=plot_left, cell_w=cell_w) + x_drift
        x_right = x_left + cell_w
        polygon = [(x_left, trace_y), (x_left, peak_y), (x_right, peak_y), (x_right, trace_y)]
        draw.polygon(polygon, fill=(*rgb, layer.fill_alpha))

    if len(wiggle_points) >= 2 and layer.outline_alpha > 0:
        outline_rgb = _trace_outline_rgb(layer_age)
        if layer_age == 0:
            draw.line(
                wiggle_points,
                fill=(*outline_rgb, max(8, layer.outline_alpha // 5)),
                width=layer.outline_width + 2,
                joint="curve",
            )
        draw.line(
            wiggle_points,
            fill=(*outline_rgb, layer.outline_alpha),
            width=layer.outline_width,
            joint="curve",
        )


def _resolve_ghost_layers(
    current_index: int,
    ghost_indices: list[int] | None,
) -> list[tuple[int, int]]:
    """Return (trace_index, layer_age) pairs oldest-first for ghost + current layers."""
    if ghost_indices is None:
        first_layer = max(0, current_index - GHOST_HISTORY)
        return [(idx, current_index - idx) for idx in range(first_layer, current_index + 1)]

    clipped = [idx for idx in ghost_indices[-GHOST_HISTORY:] if idx < current_index]
    layers = [(idx, len(clipped) - i) for i, idx in enumerate(clipped)]
    layers.append((current_index, 0))
    return layers


def _draw_drift_seismograph(
    draw: ImageDraw.ImageDraw,
    *,
    traces: list[SnapshotTrace],
    current_index: int,
    plot_box: tuple[int, int, int, int],
    style: SeismicStyle,
    window: int = DRIFT_WINDOW,
    strip_left: int = DRIFT_STRIP_LEFT,
    strip_right: int = DRIFT_STRIP_RIGHT,
    drift_headroom: float | None = None,
    time_label_step_sec: float | None = None,
    snapshots_per_video_sec: float = 1.0,
    left_drift_color: tuple[int, int, int] | None = None,
    right_drift_color: tuple[int, int, int] | None = None,
    trace_color: tuple[int, int, int] | None = None,
    centre_line_color: tuple[int, int, int] | None = None,
    trace_width: int = 2,
    fill_alpha: int = 70,
    panel_alpha: int = 130,
) -> None:
    """Vertical sparkline of active-bin drift (left/right) from the series centre.

    Time runs top (oldest) → bottom (newest). The trace is drawn up to the
    current snapshot; horizontal deflection from the centre line encodes how far
    the active bin has drifted, auto-rescaled to fill the strip width.
    """
    if not traces:
        return

    _, top, _, bottom = plot_box
    strip_cx = (strip_left + strip_right) / 2.0
    half_w = (strip_right - strip_left) / 2.0 - DRIFT_STRIP_EDGE_PAD

    active_ids = np.array([t.active_bin_id for t in traces], dtype=np.float64)
    # Trailing rolling-mean centre: each sample's drift is measured against the
    # average active bin over the preceding DRIFT_ROLLING_WINDOW snapshots, so the
    # strip highlights deviation from the recent trend rather than a fixed centre.
    rolling_centre = (
        pd.Series(active_ids)
        .rolling(window=DRIFT_ROLLING_WINDOW, min_periods=1)
        .mean()
        .to_numpy()
    )
    drift = active_ids - rolling_centre
    # Headroom keeps the widest swing off the edge; as drift grows the whole
    # trace zooms out horizontally to stay inside the strip with a margin.
    max_abs = float(np.max(np.abs(drift))) * (drift_headroom if drift_headroom is not None else DRIFT_HEADROOM)
    if max_abs <= 0:
        max_abs = 1.0

    total = len(traces)
    n = max(0, min(current_index, total - 1))

    # Scrolling time window: NOW sits at the bottom; the most recent `window`
    # snapshots fill the strip and older ones scroll off the top.
    window = max(2, window)
    win_lo = n - (window - 1)
    first = max(0, win_lo)

    def y_for(i: int) -> float:
        frac = (i - win_lo) / (window - 1)
        return top + frac * (bottom - top)

    def x_for(i: int) -> float:
        return strip_cx + (drift[i] / max_abs) * half_w

    # Panel + centre baseline (drift = 0).
    draw.rectangle(
        [strip_left - 3, top, strip_right + 3, bottom],
        fill=(6, 10, 14, panel_alpha),
        outline=(*style.hud[:3], 45),
    )
    centre_rgb = centre_line_color or style.baseline[:3]
    draw.line([(strip_cx, top), (strip_cx, bottom)], fill=(*centre_rgb, 130), width=1)
    # 2D seismic: lower bin ids on the LEFT hold Y (cyan); higher ids on the RIGHT
    # hold X (magenta). Callers may override colours/orientation (e.g. spatiotemporal
    # 3D camera reads bin id with X on the left, Y on the right).
    left_color = left_drift_color or _hex_to_rgb(SEISMIC_TOKEN_COLORS["Y"])
    right_color = right_drift_color or _hex_to_rgb(SEISMIC_TOKEN_COLORS["X"])
    trace_rgb = trace_color or _TRACE_OUTLINE_BASE

    # Filled horizontal deflections from the centre line — the "seismic" body.
    for i in range(first, n):
        y0, y1 = y_for(i), y_for(i + 1)
        x0, x1 = x_for(i), x_for(i + 1)
        side = drift[i] + drift[i + 1]
        col = left_color if side < 0 else right_color
        draw.polygon(
            [(strip_cx, y0), (x0, y0), (x1, y1), (strip_cx, y1)],
            fill=(*col, fill_alpha),
        )

    # Bright trace line over the fill.
    points = [(x_for(i), y_for(i)) for i in range(first, n + 1)]
    if len(points) >= 2:
        draw.line(points, fill=(*trace_rgb, 235), width=trace_width, joint="curve")

    # NOW marker pinned at the bottom: gridline + dot at the latest sample.
    nx, ny = x_for(n), y_for(n)
    draw.line([(strip_left, ny), (strip_right, ny)], fill=(255, 255, 255, 70), width=1)
    dot_r = max(4, trace_width + 2)
    draw.ellipse([nx - dot_r, ny - dot_r, nx + dot_r, ny + dot_r], fill=(255, 255, 255, 255))

    # Bold all-caps DRIFT header (double-struck for weight); the spot headline
    # ticker sits just above it.
    label_font = _load_mono_font(36)
    label_xy = (strip_left - 2, top - 44)
    for dx in (0, 1):
        draw.text((label_xy[0] + dx, label_xy[1]), "DRIFT", fill=(235, 246, 255, 255), font=label_font)

    if time_label_step_sec is not None and time_label_step_sec > 0:
        tick_font = _load_mono_font(16)
        tick_rgb = (178, 202, 226, 238)

        def _tick_width(text: str) -> int:
            if hasattr(draw, "textlength"):
                return int(draw.textlength(text, font=tick_font))
            return len(text) * 5

        draw.text(
            (strip_left, ny + 2),
            "T",
            fill=tick_rgb,
            font=tick_font,
        )
        age_sec = time_label_step_sec
        while True:
            snap_age = int(round(age_sec * snapshots_per_video_sec))
            if snap_age >= window or n - snap_age < first:
                break
            label = f"T-{int(age_sec)}"
            y = y_for(n - snap_age)
            draw.text(
                (strip_left, y - 9),
                label,
                fill=tick_rgb,
                font=tick_font,
            )
            age_sec += time_label_step_sec


def _draw_price_ticker(
    draw: ImageDraw.ImageDraw,
    *,
    spot_price: float,
    prev_price: float | None,
    token_x: str,
    token_y: str,
) -> None:
    """In-place spot headline stacked on top of the drift box: ``USDC/SOL``.

    The number is the active-bin ``price_per_token`` and is redrawn every frame
    (updates in place); the box tints green/red by the move versus the previous
    snapshot's spot.
    """
    label_text = f"{token_y}/{token_x}"
    price_text = f"{spot_price:,.2f}"

    label_font = _load_mono_font(22)
    price_font = _load_mono_font(50)

    up = prev_price is None or spot_price >= prev_price
    accent = (60, 230, 150) if up else (255, 92, 110)

    label_bbox = label_font.getbbox(label_text)
    price_bbox = price_font.getbbox(price_text)

    left_x = DRIFT_STRIP_LEFT - 3
    label_y = 3
    price_y = 30
    content_w = max(label_bbox[2], price_bbox[2])
    panel = [left_x - 2, 1, left_x + content_w + 8, price_y + price_bbox[3] + 4]
    draw.rectangle(panel, fill=(8, 12, 18, 180), outline=(*accent, 160))

    draw.text((left_x + 2, label_y), label_text, fill=(175, 195, 215, 235), font=label_font)
    draw.text((left_x + 2, price_y), price_text, fill=(*accent, 255), font=price_font)


def render_seismic_frame(
    traces: list[SnapshotTrace],
    *,
    frame: GlobalFrame,
    current_index: int,
    transition_blend: float = 1.0,
    ghost_indices: list[int] | None = None,
    zoom_bins: int | None = None,
    liquidity_scale: float,
    drift_window: int = DRIFT_WINDOW,
    token_x: str,
    token_y: str,
    pool_address: str,
    price_for_bin: dict[int, float] | None = None,
    width: int = 1400,
    height: int = 800,
    style: SeismicStyle = SeismicStyle(),
    show_drift_strip: bool = True,
    compact_hud: bool = False,
    edge_strip: bool = False,
    deflection_ratio: float | None = None,
    drift_headroom: float | None = None,
    token_colors: dict[str, str] | None = None,
    token_color_mode: str = "blend",
    highlight_active_bin: bool = True,
    active_bin_deflection_scale: float = 1.0,
    active_bin_neighbor_cap: float | None = None,
    active_bin_neighbor_radius: int = 4,
    left_drift_color: tuple[int, int, int] | None = None,
    right_drift_color: tuple[int, int, int] | None = None,
    drift_trace_width: int = 2,
    drift_fill_alpha: int = 70,
    drift_panel_alpha: int = 130,
) -> np.ndarray:
    """Render the blackboard with a viewport recentered on the active bin marker."""
    _ = zoom_bins
    current_index = max(0, min(current_index, len(traces) - 1))
    total_traces = len(traces)
    palette = token_colors or SEISMIC_TOKEN_COLORS

    if edge_strip:
        show_drift_strip = True
        compact_hud = True

    canvas_bg = (0, 0, 0, 0) if edge_strip else style.background + (255,)
    img = Image.new("RGBA", (width, height), canvas_bg)
    plot_left = DRIFT_STRIP_RIGHT + 8 if show_drift_strip else 20
    plot_top = 48 if compact_hud else 82
    plot_box = (plot_left, plot_top, width - 12, height - (36 if compact_hud else 76))
    left, top, right, bottom = plot_box
    plot_bg = Image.fromarray(_plot_background(right - left, bottom - top), mode="RGB")
    img.paste(plot_bg, (left, top))

    draw = ImageDraw.Draw(img, "RGBA")
    title_font = _load_mono_font(TITLE_FONT_SIZE)
    subtitle_font = _load_mono_font(SUBTITLE_FONT_SIZE)
    hud_font = _load_mono_font(HUD_FONT_SIZE)
    channel_font = _load_mono_font(CHANNEL_FONT_SIZE)

    grid_label_font = _load_mono_font(15)
    trace_y = float(bottom)
    _draw_grid(
        draw,
        frame=frame,
        plot_box=plot_box,
        trace_y=trace_y,
        style=style,
        label_font=grid_label_font,
    )

    max_deflection = (bottom - top) * (deflection_ratio if deflection_ratio is not None else CURRENT_DEFLECTION_RATIO)
    ghost_layers = _resolve_ghost_layers(current_index, ghost_indices)

    ghost_trace_y_offset = GHOST_TRACE_Y_OFFSET * (3.2 if edge_strip else 1.0)
    for layer_index, layer_age in ghost_layers:
        layer = _layer_style(layer_age, transition_blend)
        layer_trace_y = trace_y - layer_age * ghost_trace_y_offset
        _draw_horizontal_wiggle_trace(
            draw,
            trace=traces[layer_index],
            frame=frame,
            trace_y=layer_trace_y,
            liquidity_scale=liquidity_scale,
            max_deflection=max_deflection,
            plot_left=left,
            plot_right=right,
            layer=layer,
            layer_age=layer_age,
            token_colors=token_colors,
            token_color_mode=token_color_mode,
            highlight_active_bin=highlight_active_bin,
            active_bin_deflection_scale=active_bin_deflection_scale,
            active_bin_neighbor_cap=active_bin_neighbor_cap,
            active_bin_neighbor_radius=active_bin_neighbor_radius,
        )

    if show_drift_strip:
        _draw_drift_seismograph(
            draw,
            traces=traces,
            current_index=current_index,
            plot_box=plot_box,
            style=style,
            window=drift_window,
            drift_headroom=drift_headroom,
            left_drift_color=left_drift_color,
            right_drift_color=right_drift_color,
            trace_width=drift_trace_width,
            fill_alpha=drift_fill_alpha,
            panel_alpha=drift_panel_alpha,
        )

    channel_x = (DRIFT_STRIP_RIGHT + 8) if show_drift_strip else (left + 4)
    if not compact_hud:
        for layer_index, layer_age in ghost_layers:
            layer_trace_y = trace_y - layer_age * GHOST_TRACE_Y_OFFSET
            label = "NOW" if layer_age == 0 else f"T-{layer_age}"
            alpha = 220 if layer_age == 0 else max(50, int(180 * (GHOST_OUTLINE_DECAY**layer_age)))
            draw.text(
                (channel_x, layer_trace_y - 14),
                label,
                fill=(*_trace_outline_rgb(layer_age), alpha),
                font=channel_font,
            )

    legend_x = right - (120 if compact_hud else 230)
    legend_y = top + (8 if compact_hud else 26)

    current = traces[current_index]
    if not compact_hud:
        _draw_active_bin_marker(
            draw,
            active_bin_id=current.active_bin_id,
            frame=frame,
            plot_box=plot_box,
            style=style,
            font=hud_font,
            label_y=legend_y,
        )

    axis_y = height - (28 if compact_hud else 62)
    if not compact_hud:
        draw.text((left, axis_y), str(frame.bin_id_min), fill=style.hud, font=hud_font)
        draw.text((right - 80, axis_y), str(frame.bin_id_max), fill=style.hud, font=hud_font)
        draw.text(
            (left + (right - left) / 2 - 110, axis_y),
            "BIN ID · GLOBAL LATTICE",
            fill=style.hud,
            font=hud_font,
        )

    if compact_hud:
        title = f"{token_x}/{token_y}"
        subtitle = f"SNAP {current.snapshot_index + 1}/{total_traces}"
        compact_title_font = _load_mono_font(18)
        compact_subtitle_font = _load_mono_font(14)
        draw.text((left, 8), title, fill=style.hud, font=compact_title_font)
        draw.text((left, 26), subtitle, fill=(*style.hud[:3], 240), font=compact_subtitle_font)
    else:
        title = "DLMM SEISMOGRAM"
        subtitle = (
            f"{token_x}/{token_y} | {pool_address[:6]}...{pool_address[-4:]} | "
            f"SNAP {current.snapshot_index + 1}/{total_traces} | {current.fetched_label}"
        )
        draw.text((left, 13), title, fill=style.hud, font=title_font)
        draw.text((left, 38), subtitle, fill=(*style.hud[:3], 240), font=subtitle_font)

    if price_for_bin and not compact_hud:
        spot_price = price_for_bin.get(int(current.active_bin_id))
        prev_price = (
            price_for_bin.get(int(traces[current_index - 1].active_bin_id))
            if current_index > 0
            else None
        )
        if spot_price is not None and spot_price > 0:
            _draw_price_ticker(
                draw,
                spot_price=spot_price,
                prev_price=prev_price,
                token_x=token_x,
                token_y=token_y,
            )

    legend_font = channel_font
    legend_line = int(CHANNEL_FONT_SIZE * 1.3)
    if not compact_hud:
        draw.text(
            (legend_x, legend_y),
            f"| {token_y} (Y)",
            fill=(*_layer_rgb(palette["Y"], layer_age=0), 240),
            font=legend_font,
        )
        draw.text(
            (legend_x, legend_y + legend_line),
            f"| {token_x} (X)",
            fill=(*_layer_rgb(palette["X"], layer_age=0), 240),
            font=legend_font,
        )
        mix_rgb = _hex_to_rgb(palette.get("mix", palette["Y"]))
        draw.text(
            (legend_x, legend_y + 2 * legend_line),
            f"| {token_x}+{token_y} (mix)",
            fill=(*mix_rgb, 240),
            font=legend_font,
        )
        active_legend_rgb = (
            _hex_to_rgb(SEISMIC_ACTIVE_COLOR)
            if highlight_active_bin
            else mix_rgb
        )
        draw.text(
            (legend_x, legend_y + 3 * legend_line),
            "| ACTIVE",
            fill=(*active_legend_rgb, 255),
            font=legend_font,
        )

    if edge_strip:
        return np.asarray(img)
    return np.asarray(img.convert("RGB"))


def encode_mp4(frames: list[np.ndarray], output_path: Path, *, fps: int) -> None:
    """Write RGB frame arrays to MP4 via ffmpeg rawvideo pipe."""
    if not frames:
        raise ValueError("No frames to encode")

    height, width, _ = frames[0].shape
    encode_mp4_stream(frames, output_path, fps=fps, width=width, height=height)


def encode_mp4_stream(
    frames: Iterable[np.ndarray],
    output_path: Path,
    *,
    fps: int,
    width: int,
    height: int,
) -> int:
    """Write RGB frames to MP4 without retaining the full video in memory."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{width}x{height}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    frame_count = 0
    try:
        for frame in frames:
            if frame.shape != (height, width, 3):
                raise ValueError(
                    f"Frame shape mismatch: expected {(height, width, 3)}, got {frame.shape}"
                )
            proc.stdin.write(frame.astype(np.uint8).tobytes())
            frame_count += 1
    finally:
        proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        return_code = proc.wait()

    if return_code != 0:
        raise RuntimeError(f"ffmpeg failed ({return_code}): {stderr[-2000:]}")
    if frame_count == 0:
        raise ValueError("No frames to encode")
    return frame_count
