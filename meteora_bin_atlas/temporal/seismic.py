"""Seismic-style wiggle renderer for temporal bin-atlas snapshots."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from meteora_bin_atlas.explore.labels import bar_color_for_bin

# Skyline rises from the plot floor; this fraction caps peak height below the top margin.
CURRENT_DEFLECTION_RATIO = 0.86
GHOST_HISTORY = 10
ROLLING_SNAPSHOTS = 5
DISPLAY_PAD_BINS = 4
ZOOM_IN_RATIO = 0.72
MIN_DISPLAY_BINS = 25
CURRENT_FILL_ALPHA = 28
CURRENT_OUTLINE_ALPHA = 220
GHOST_FILL_BASE = 18
GHOST_FILL_DECAY = 0.55
GHOST_OUTLINE_BASE = 160
GHOST_OUTLINE_DECAY = 0.68
GHOST_FILL_CUTOFF_AGE = 6
GHOST_TRACE_Y_OFFSET = 3.0
GHOST_TRACE_X_DRIFT = 0.35

# Neon instrument palette for temporal MP4 (notebook TOKEN_COLORS unchanged).
SEISMIC_TOKEN_COLORS = {
    "X": "#FF2BD6",
    "Y": "#00F0FF",
    "mix": "#E8F4FF",
    "empty": "#000000",
}

_TRACE_OUTLINE_BASE = (232, 244, 255)
TITLE_FONT_SIZE = 27
SUBTITLE_FONT_SIZE = 21
HUD_FONT_SIZE = 20
CHANNEL_FONT_SIZE = 18
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


def _padded_bounds(lo: int, hi: int) -> tuple[int, int]:
    padded_lo = lo - DISPLAY_PAD_BINS
    padded_hi = hi + DISPLAY_PAD_BINS
    span = padded_hi - padded_lo + 1
    if span >= MIN_DISPLAY_BINS:
        return padded_lo, padded_hi
    center = (padded_lo + padded_hi) // 2
    half = MIN_DISPLAY_BINS // 2
    return center - half, center + half


def _rolling_observed_bounds(traces: list[SnapshotTrace], current_index: int) -> tuple[int, int]:
    """Union of bins with liquidity (plus active) across the recent snapshot window."""
    start = max(0, current_index - ROLLING_SNAPSHOTS + 1)
    lo = int(traces[start].active_bin_id)
    hi = lo
    for trace in traces[start : current_index + 1]:
        trace_lo, trace_hi = _observed_extent(trace)
        lo = min(lo, trace_lo)
        hi = max(hi, trace_hi)
    return lo, hi


def _rolling_content_bounds(traces: list[SnapshotTrace], current_index: int) -> tuple[int, int]:
    """Padded rolling bounds used for zoom-out decisions."""
    lo, hi = _rolling_observed_bounds(traces, current_index)
    return _padded_bounds(lo, hi)


def _clamp_to_atlas(lo: int, hi: int, atlas: GlobalFrame | None) -> tuple[int, int]:
    if atlas is None:
        return lo, hi
    return max(atlas.bin_id_min, lo), min(atlas.bin_id_max, hi)


def _desired_display_span(
    traces: list[SnapshotTrace],
    current_index: int,
    previous: GlobalFrame | None,
    *,
    content_lo: int,
    content_hi: int,
) -> int:
    """Viewport width: grow only when centered window no longer fits content."""
    content_span = content_hi - content_lo + 1
    if previous is None:
        return max(content_span, MIN_DISPLAY_BINS)

    prev_span = previous.n_bins
    if content_span < ZOOM_IN_RATIO * prev_span:
        return max(content_span, MIN_DISPLAY_BINS)

    active_bin_id = int(traces[current_index].active_bin_id)
    half = (prev_span - 1) // 2
    centered_lo = active_bin_id - half
    centered_hi = centered_lo + prev_span - 1
    if content_lo >= centered_lo and content_hi <= centered_hi:
        return prev_span

    return max(prev_span, content_span, MIN_DISPLAY_BINS)


def _frame_centered_on_active(
    span: int,
    *,
    active_bin_id: int,
    content_lo: int,
    content_hi: int,
    atlas: GlobalFrame | None,
) -> tuple[int, int]:
    """Pick integer bin bounds that pixel-center the active bin when possible."""
    min_span = max(content_hi - content_lo + 1, MIN_DISPLAY_BINS)
    max_span = span
    if atlas is not None:
        max_span = min(max_span, atlas.bin_id_max - atlas.bin_id_min + 1)

    best_lo = content_lo
    best_hi = content_hi
    best_err = float("inf")
    best_span = max_span

    for try_span in range(min_span, max_span + 1):
        ideal_lo = active_bin_id + 0.5 - try_span / 2
        for lo in {int(math.floor(ideal_lo)), int(math.ceil(ideal_lo))}:
            hi = lo + try_span - 1
            if lo > content_lo or hi < content_hi:
                continue
            if atlas is not None and (lo < atlas.bin_id_min or hi > atlas.bin_id_max):
                continue
            err = abs((active_bin_id - lo) + 0.5 - try_span / 2)
            if err < best_err or (math.isclose(err, best_err) and try_span < best_span):
                best_err = err
                best_lo = lo
                best_hi = hi
                best_span = try_span

    if math.isfinite(best_err):
        return best_lo, best_hi

    lo = active_bin_id - (max_span - 1) // 2
    hi = lo + max_span - 1
    if content_lo < lo:
        shift = content_lo - lo
        lo += shift
        hi += shift
    if content_hi > hi:
        shift = content_hi - hi
        lo += shift
        hi += shift
    return _clamp_to_atlas(lo, hi, atlas)


def compute_display_frame(
    traces: list[SnapshotTrace],
    current_index: int,
    previous: GlobalFrame | None,
    *,
    atlas: GlobalFrame | None = None,
) -> GlobalFrame:
    """
    Update the visible bin-id viewport.

    Zooms out when the rolling window escapes the current frame; zooms in when
    the rolling window is much narrower than what is currently shown. Keeps the
    active bin on the plot center instead of monotonically expanding to atlas width.
    """
    padded_lo, padded_hi = _rolling_content_bounds(traces, current_index)
    padded_lo, padded_hi = _clamp_to_atlas(padded_lo, padded_hi, atlas)
    observed_lo, observed_hi = _rolling_observed_bounds(traces, current_index)
    observed_lo, observed_hi = _clamp_to_atlas(observed_lo, observed_hi, atlas)
    active_bin_id = int(traces[current_index].active_bin_id)
    span = _desired_display_span(
        traces,
        current_index,
        previous,
        content_lo=padded_lo,
        content_hi=padded_hi,
    )
    display_lo, display_hi = _frame_centered_on_active(
        span,
        active_bin_id=active_bin_id,
        content_lo=observed_lo,
        content_hi=observed_hi,
        atlas=atlas,
    )
    return GlobalFrame(bin_id_min=display_lo, bin_id_max=display_hi)


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


def _layer_rgb(hex_color: str, *, layer_age: int) -> tuple[int, int, int]:
    rgb = _hex_to_rgb(hex_color)
    if layer_age <= 0:
        return rgb
    bg = SeismicStyle.background
    mute = 0.45**layer_age
    return tuple(int(bg[i] + (rgb[i] - bg[i]) * mute) for i in range(3))


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


def _draw_grid(
    draw: ImageDraw.ImageDraw,
    *,
    frame: GlobalFrame,
    plot_box: tuple[int, int, int, int],
    trace_y: float,
    style: SeismicStyle,
) -> None:
    left, top, right, bottom = plot_box
    cell_w = _bin_cell_width(frame=frame, plot_left=left, plot_right=right)

    for offset in range(0, frame.n_bins, 10):
        bin_id = frame.bin_id_min + offset
        x = _x_edge_for_bin_id(bin_id, frame=frame, plot_left=left, cell_w=cell_w)
        draw.line([(x, top), (x, bottom)], fill=style.grid_major, width=1)

    draw.line([(left, trace_y), (right, trace_y)], fill=style.baseline, width=1)


def _draw_active_bin_marker(
    draw: ImageDraw.ImageDraw,
    *,
    active_bin_id: int,
    frame: GlobalFrame,
    plot_box: tuple[int, int, int, int],
    style: SeismicStyle,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> float:
    left, top, right, bottom = plot_box
    cell_w = _bin_cell_width(frame=frame, plot_left=left, plot_right=right)
    # Draw at the active bin's true data position so the marker always sits on the
    # active liquidity column (never force-snapped to plot center, which detaches it).
    active_x = round(
        _x_center_for_bin_id(active_bin_id, frame=frame, plot_left=left, cell_w=cell_w)
    )
    half = max(4.0, cell_w / 2.0)
    # Thick translucent band spanning the active bin cell — the "spanning" bar that is
    # always present, regardless of whether the active bin happens to hold both tokens.
    draw.rectangle([active_x - half, top, active_x + half, bottom], fill=(255, 255, 255, 80))
    # Crisp center line with a dark backing so it stays visible over bright fills.
    draw.line([(active_x, top), (active_x, bottom)], fill=(0, 0, 0, 150), width=5)
    draw.line([(active_x, top), (active_x, bottom)], fill=style.active_bin, width=3)
    tick = 8
    draw.line([(active_x - tick, top), (active_x + tick, top)], fill=style.active_bin, width=3)
    draw.line([(active_x - tick, bottom), (active_x + tick, bottom)], fill=style.active_bin, width=3)
    draw.text(
        (active_x + 6, top + 4),
        f"ACTIVE {active_bin_id}",
        fill=style.hud,
        font=font,
    )
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
) -> None:
    if layer.fill_alpha <= 0 and layer.outline_alpha <= 0:
        return

    cell_w = _bin_cell_width(frame=frame, plot_left=plot_left, plot_right=plot_right)
    x_drift = layer_age * GHOST_TRACE_X_DRIFT
    wiggle_points: list[tuple[float, float]] = []

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
        peak_y = trace_y - offset
        wiggle_points.append((x_center, peak_y))

        if layer.fill_alpha <= 0:
            continue

        distance = bin_id - trace.active_bin_id
        color_hex = bar_color_for_bin(
            float(x_amount),
            float(y_amount),
            distance,
            colors=SEISMIC_TOKEN_COLORS,
        )
        rgb = _layer_rgb(color_hex, layer_age=layer_age)

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


def render_seismic_frame(
    traces: list[SnapshotTrace],
    *,
    frame: GlobalFrame,
    current_index: int,
    transition_blend: float = 1.0,
    ghost_indices: list[int] | None = None,
    zoom_bins: int | None = None,
    liquidity_scale: float,
    token_x: str,
    token_y: str,
    pool_address: str,
    width: int = 1400,
    height: int = 800,
    style: SeismicStyle = SeismicStyle(),
) -> np.ndarray:
    """Render the blackboard with a viewport recentered on the active bin marker."""
    _ = zoom_bins
    current_index = max(0, min(current_index, len(traces) - 1))
    total_traces = len(traces)

    img = Image.new("RGBA", (width, height), style.background + (255,))
    plot_box = (120, 100, width - 30, height - 100)
    left, top, right, bottom = plot_box
    plot_bg = Image.fromarray(_plot_background(right - left, bottom - top), mode="RGB")
    img.paste(plot_bg, (left, top))

    draw = ImageDraw.Draw(img, "RGBA")
    title_font = _load_mono_font(TITLE_FONT_SIZE)
    subtitle_font = _load_mono_font(SUBTITLE_FONT_SIZE)
    hud_font = _load_mono_font(HUD_FONT_SIZE)
    channel_font = _load_mono_font(CHANNEL_FONT_SIZE)

    trace_y = float(bottom)
    _draw_grid(draw, frame=frame, plot_box=plot_box, trace_y=trace_y, style=style)

    max_deflection = (bottom - top) * CURRENT_DEFLECTION_RATIO
    ghost_layers = _resolve_ghost_layers(current_index, ghost_indices)

    for layer_index, layer_age in ghost_layers:
        layer = _layer_style(layer_age, transition_blend)
        layer_trace_y = trace_y - layer_age * GHOST_TRACE_Y_OFFSET
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
        )

    channel_x = left - 118
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

    current = traces[current_index]
    _draw_active_bin_marker(
        draw,
        active_bin_id=current.active_bin_id,
        frame=frame,
        plot_box=plot_box,
        style=style,
        font=hud_font,
    )

    axis_y = height - 88
    draw.text((left, axis_y), str(frame.bin_id_min), fill=style.hud, font=hud_font)
    draw.text((right - 96, axis_y), str(frame.bin_id_max), fill=style.hud, font=hud_font)
    draw.text(
        (left + (right - left) / 2 - 130, axis_y),
        "BIN ID · GLOBAL LATTICE",
        fill=style.hud,
        font=hud_font,
    )

    title = "DLMM SEISMOGRAM"
    subtitle = (
        f"{token_x}/{token_y} | {pool_address[:6]}...{pool_address[-4:]} | "
        f"SNAP {current.snapshot_index + 1}/{total_traces} | {current.fetched_label}"
    )
    draw.text((left, 22), title, fill=style.hud, font=title_font)
    draw.text((left, 56), subtitle, fill=(*style.hud[:3], 240), font=subtitle_font)

    legend_x = right - 280
    legend_y = top + 8
    legend_font = channel_font
    legend_line = int(CHANNEL_FONT_SIZE * 1.45)
    draw.text(
        (legend_x, legend_y),
        f"| {token_y} (Y)",
        fill=(*_layer_rgb(SEISMIC_TOKEN_COLORS["Y"], layer_age=0), 240),
        font=legend_font,
    )
    draw.text(
        (legend_x, legend_y + legend_line),
        f"| {token_x} (X)",
        fill=(*_layer_rgb(SEISMIC_TOKEN_COLORS["X"], layer_age=0), 240),
        font=legend_font,
    )
    draw.text(
        (legend_x, legend_y + 2 * legend_line),
        f"| {token_x}+{token_y}",
        fill=(*_layer_rgb(SEISMIC_TOKEN_COLORS["mix"], layer_age=0), 240),
        font=legend_font,
    )

    return np.asarray(img.convert("RGB"))


def encode_mp4(frames: list[np.ndarray], output_path: Path, *, fps: int) -> None:
    """Write RGB frame arrays to MP4 via ffmpeg rawvideo pipe."""
    if not frames:
        raise ValueError("No frames to encode")

    height, width, _ = frames[0].shape
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
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
    try:
        for frame in frames:
            if frame.shape != (height, width, 3):
                raise ValueError(
                    f"Frame shape mismatch: expected {(height, width, 3)}, got {frame.shape}"
                )
            proc.stdin.write(frame.astype(np.uint8).tobytes())
    finally:
        proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        return_code = proc.wait()

    if return_code != 0:
        raise RuntimeError(f"ffmpeg failed ({return_code}): {stderr[-2000:]}")
