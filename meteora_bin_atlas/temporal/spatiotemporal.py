"""Spatiotemporal 3D renderer: bin lattice × time × liquidity (platformer view)."""

from __future__ import annotations

import io
import textwrap
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgb
from matplotlib.ticker import FuncFormatter, MaxNLocator
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

from meteora_bin_atlas.temporal.seismic import (
    GlobalFrame,
    SeismicStyle,
    SnapshotTrace,
    _clamp_to_atlas,
    _draw_drift_seismograph,
    _hex_to_rgb,
    _load_mono_font,
    _observed_extent,
    compute_display_frame,
    encode_mp4,
    prepare_snapshot_traces,
)

# Visible history along the time (X) axis — NOW at lower-left, oldest at upper-right.
SPATIOTEMPORAL_HISTORY = 64
# Wider bin viewport than the 2D seismic renderer — room for drift across the landscape.
SPATIOTEMPORAL_PAD_BINS = 18
SPATIOTEMPORAL_MIN_BINS = 90
SPATIOTEMPORAL_EDGE_BAND = 24
SPATIOTEMPORAL_DEFAULT_BINS_LEFT = 70
SPATIOTEMPORAL_DEFAULT_BINS_RIGHT = 70
# Drift strip scrolls over a longer wall-clock window than the 3D landscape history.
SPATIOTEMPORAL_DRIFT_WINDOW_SECONDS = 30.0
# Compact canvas — tighter than the 14×8 temporal frame; less dead margin on the flanks.
SPATIOTEMPORAL_WIDTH_IN = 10.5
SPATIOTEMPORAL_HEIGHT_IN = 8.0
SPATIOTEMPORAL_DRIFT_LEFT = 6
SPATIOTEMPORAL_DRIFT_RIGHT = 86
SPATIOTEMPORAL_DRIFT_GAP = 8
SPATIOTEMPORAL_FIG_RIGHT = 0.955
SPATIOTEMPORAL_FIG_BOTTOM = 0.10
INSCRIPTION_GAP = 10
INSCRIPTION_TOP_PX = 8
HEADLINE_FONT_SIZE = 9
GLOSS_FONT_SIZE = 8.5
HEADLINE_LINE_PX = 11
GLOSS_LINE_PX = 10
LEGEND_LINE_PX = 13
CAPTION_LEGEND_GAP_PX = 5
PLOT_CAPTION_GAP_PX = 4
LEGEND_RIGHT_PAD_PX = 18
LEGEND_FONT_SIZE = 9
MONO_CHAR_PX = 7.0
DRIFT_TIME_LABEL_STEP_SEC = 5.0
# Camera: time (X) reads lower-left (NOW) → upper-right (past) on screen.
PLATFORMER_ELEV = 28.0
PLATFORMER_AZIM = 142.0
# Older time slices mute toward the background (no alpha fade).
SLICE_MUTE_DECAY = 0.94
SLICE_MUTE_MIN = 0.42
# Active-bin drift ribbon sits slightly above the liquidity surface.
# Muted watercolor-cybernetic palette — dusty teals/mauves rather than neon.
SPATIOTEMPORAL_TOKEN_COLORS = {
    "X": "#A8789E",
    "Y": "#6AABB8",
    "mix": "#8F6FD0",
    "empty": "#1A1E24",
}
SPATIOTEMPORAL_ACTIVE_COLOR = "#FFFFFF"
# Drift trace: white centreline over the muted X/Y fill bands.
SPATIOTEMPORAL_DRIFT_TRACE = (255, 255, 255)
SPATIOTEMPORAL_PRICE_LEFT = (255, 92, 110)
SPATIOTEMPORAL_PRICE_RIGHT = (80, 170, 255)
SPATIOTEMPORAL_PRICE_FLAT = (180, 196, 214)
RIDGE_DARKEN = 0.62
RIDGE_LINEWIDTH = 0.35
CAP_LINEWIDTH = 0.0
# Push end caps a hair outside the roof mesh so mplot3d's painter sort stops
# swapping them with adjacent surface quads frame-to-frame.
CAP_X_EPSILON = 0.006
CAP_BAR_X_EPSILON = 0.002
CAP_BAR_LINEWIDTH = 0.32
CAP_BAR_ALPHA = 0.72


def _as_rgb_tuple(rgb) -> tuple[float, float, float]:
    return tuple(float(c) for c in rgb)


def _ridge_edge(rgb: tuple[float, float, float]) -> tuple[float, float, float, float]:
    return (*tuple(max(0.0, c * RIDGE_DARKEN) for c in rgb), 1.0)


@dataclass(frozen=True)
class SpatiotemporalStyle:
    background: tuple[float, float, float] = (0.01, 0.01, 0.02)
    grid: tuple[float, float, float, float] = (0.24, 0.31, 0.39, 0.22)
    hud: tuple[float, float, float] = (0.75, 0.82, 0.90)
    drift: tuple[float, float, float] = (0.79, 0.58, 0.38)


def _composition_rgb(x_amount: float, y_amount: float) -> tuple[int, int, int]:
    x = max(0.0, x_amount)
    y = max(0.0, y_amount)
    total = x + y
    if total <= 0:
        return _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["empty"])
    share_x = x / total
    y_rgb = _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["Y"])
    mix_rgb = _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["mix"])
    x_rgb = _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["X"])
    if share_x <= 0.5:
        t = share_x * 2.0
        return tuple(int(round(y_rgb[i] + (mix_rgb[i] - y_rgb[i]) * t)) for i in range(3))
    t = (share_x - 0.5) * 2.0
    return tuple(int(round(mix_rgb[i] + (x_rgb[i] - mix_rgb[i]) * t)) for i in range(3))


def _format_price_value(price: float) -> str:
    """Compact human price labels for the DLMM price axis."""
    if price >= 1_000:
        return f"{price:,.0f}"
    if price >= 10:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.3f}"
    return f"{price:.4g}"


def _infer_price_step(price_for_bin: dict[int, float]) -> float | None:
    ordered = sorted(price_for_bin)
    ratios = [
        price_for_bin[b + 1] / price_for_bin[b]
        for b in ordered
        if (b + 1) in price_for_bin and price_for_bin[b] > 0.0
    ]
    if not ratios:
        return None
    return float(np.median(ratios))


def _price_for_axis_bin(bin_id: int, price_for_bin: dict[int, float]) -> float | None:
    price = price_for_bin.get(bin_id)
    if price is not None and price > 0.0:
        return float(price)
    if not price_for_bin:
        return None
    ratio = _infer_price_step(price_for_bin)
    if ratio is None:
        return None
    nearest = min(price_for_bin, key=lambda known: abs(known - bin_id))
    nearest_price = price_for_bin.get(nearest)
    if nearest_price is None or nearest_price <= 0.0:
        return None
    return float(nearest_price) * (ratio ** (bin_id - nearest))


def _mute_rgb(
    rgb: tuple[float, float, float],
    *,
    layer_age: int,
    background: tuple[float, float, float],
) -> tuple[float, float, float]:
    rgb = _as_rgb_tuple(rgb)
    if layer_age <= 0:
        return rgb
    mute = max(SLICE_MUTE_MIN, SLICE_MUTE_DECAY**layer_age)
    return tuple(background[i] + (rgb[i] - background[i]) * mute for i in range(3))


def _figure_to_rgb(fig: plt.Figure) -> np.ndarray:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), edgecolor="none")
    buf.seek(0)
    from PIL import Image

    img = Image.open(buf).convert("RGB")
    return np.asarray(img)


def _plot_box_pixels(
    height: int,
    *,
    top_frac: float = 0.82,
    bottom_frac: float = SPATIOTEMPORAL_FIG_BOTTOM,
) -> tuple[int, int, int, int]:
    """PIL (left, top, right, bottom) aligned with the 3D axes vertical span."""
    y_top = int(round(height * (1.0 - top_frac)))
    y_bottom = int(round(height * (1.0 - bottom_frac)))
    return (0, y_top, 0, y_bottom)


def _figure_left_frac(width_px: int) -> float:
    """Place the 3D axes immediately after the drift strip."""
    return (SPATIOTEMPORAL_DRIFT_RIGHT + SPATIOTEMPORAL_DRIFT_GAP) / max(width_px, 1)


def _inscription_x() -> int:
    return SPATIOTEMPORAL_DRIFT_RIGHT + INSCRIPTION_GAP


@dataclass(frozen=True)
class CaptionLayout:
    """Pixel layout for the top caption band (inscription + vertical legend)."""

    headline_lines: tuple[str, ...]
    gloss_lines: tuple[str, ...]
    inscription_bottom_px: int
    legend_top_px: int
    plot_top_frac: float


def _inscription_wrap_cols(width_px: int) -> int:
    text_px = width_px - _inscription_x() - LEGEND_RIGHT_PAD_PX
    return max(36, int(text_px / MONO_CHAR_PX))


def _spatiotemporal_inscription(
    token_x: str,
    token_y: str,
    pool_address: str,
    *,
    snapshot_index: int,
    total_snapshots: int,
    wrap_width: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Pool facts plus a concise reading of the landscape."""
    short = f"{pool_address[:6]}…{pool_address[-4:]}"
    headline = (
        f"{token_y} / {token_x}  ·  Meteora DLMM  ·  {short}  ·  "
        f"snapshot {snapshot_index + 1} of {total_snapshots}"
    )
    gloss = (
        f"In a Meteora DLMM pool, makers rest {token_y} and {token_x} in discrete price bins; "
        f"the active bin is where the market presently stands. "
        "Below, snapshots stitch into one watershed—time flows toward you, bin price climbs "
        "the ladder, height is depth at rest, colour names the token held. "
        "The drift scroll at left holds a slower breath: the active bin's wander from its "
        "trailing centre, traced across a longer span than this ridge of terrain."
    )
    headline_lines = textwrap.wrap(headline, width=wrap_width, break_long_words=False)
    gloss_lines = textwrap.wrap(gloss, width=wrap_width, break_long_words=False)
    return tuple(headline_lines[:2]), tuple(gloss_lines)


def _compute_caption_layout(
    width_px: int,
    height_px: int,
    *,
    token_x: str,
    token_y: str,
    pool_address: str,
    snapshot_index: int,
    total_snapshots: int,
    legend_entries: int = 4,
) -> CaptionLayout:
    wrap_width = _inscription_wrap_cols(width_px)
    headline_lines, gloss_lines = _spatiotemporal_inscription(
        token_x,
        token_y,
        pool_address,
        snapshot_index=snapshot_index,
        total_snapshots=total_snapshots,
        wrap_width=wrap_width,
    )
    inscription_h = len(headline_lines) * HEADLINE_LINE_PX + len(gloss_lines) * GLOSS_LINE_PX
    legend_h = legend_entries * LEGEND_LINE_PX
    band_px = (
        INSCRIPTION_TOP_PX
        + inscription_h
        + CAPTION_LEGEND_GAP_PX
        + legend_h
        + PLOT_CAPTION_GAP_PX
    )
    inscription_bottom_px = INSCRIPTION_TOP_PX + inscription_h
    legend_top_px = inscription_bottom_px + CAPTION_LEGEND_GAP_PX
    plot_top_frac = 1.0 - band_px / max(height_px, 1)
    return CaptionLayout(
        headline_lines=headline_lines,
        gloss_lines=gloss_lines,
        inscription_bottom_px=inscription_bottom_px,
        legend_top_px=legend_top_px,
        plot_top_frac=plot_top_frac,
    )


def _draw_figure_inscription(
    fig: plt.Figure,
    layout: CaptionLayout,
    *,
    width_px: int,
    height_px: int,
) -> None:
    """Top caption band in matplotlib monospace — tight line spacing, no extra gaps."""
    inscription_x = _inscription_x() / max(width_px, 1)
    hud = (0.78, 0.85, 0.92)
    gloss_color = (0.59, 0.66, 0.74)
    y_px = INSCRIPTION_TOP_PX

    def _y_frac() -> float:
        return 1.0 - y_px / max(height_px, 1)

    for line in layout.headline_lines:
        fig.text(
            inscription_x,
            _y_frac(),
            line,
            transform=fig.transFigure,
            ha="left",
            va="top",
            fontsize=HEADLINE_FONT_SIZE,
            family="monospace",
            color=hud,
        )
        y_px += HEADLINE_LINE_PX

    for line in layout.gloss_lines:
        fig.text(
            inscription_x,
            _y_frac(),
            line,
            transform=fig.transFigure,
            ha="left",
            va="top",
            fontsize=GLOSS_FONT_SIZE,
            family="monospace",
            color=gloss_color,
        )
        y_px += GLOSS_LINE_PX


def _draw_drift_price_ticker(
    draw,
    *,
    plot_box: tuple[int, int, int, int],
    traces: list[SnapshotTrace],
    current_index: int,
    price_for_bin: dict[int, float] | None,
    token_x: str,
    token_y: str,
) -> None:
    """Small live price readout pinned inside the drift strip."""
    if not price_for_bin or not traces:
        return
    current = traces[current_index]
    spot_price = _price_for_axis_bin(int(current.active_bin_id), price_for_bin)
    if spot_price is None:
        return

    prev_bin = traces[current_index - 1].active_bin_id if current_index > 0 else current.active_bin_id
    delta = int(current.active_bin_id) - int(prev_bin)
    if delta < 0:
        accent = SPATIOTEMPORAL_PRICE_LEFT
    elif delta > 0:
        accent = SPATIOTEMPORAL_PRICE_RIGHT
    else:
        accent = SPATIOTEMPORAL_PRICE_FLAT

    _left, top, _right, _bottom = plot_box
    strip_left = SPATIOTEMPORAL_DRIFT_LEFT
    strip_right = SPATIOTEMPORAL_DRIFT_RIGHT
    panel = [strip_left + 3, top + 7, strip_right - 3, top + 43]
    draw.rectangle(panel, fill=(6, 9, 14, 190), outline=(*accent, 145))

    label_font = _load_mono_font(8)
    price_font = _load_mono_font(14)
    center_x = (strip_left + strip_right) / 2.0
    draw.text(
        (center_x, panel[1] + 5),
        f"{token_y}/{token_x}",
        fill=(165, 184, 204, 235),
        font=label_font,
        anchor="ma",
    )
    draw.text(
        (center_x, panel[1] + 18),
        _format_price_value(spot_price),
        fill=(*accent, 255),
        font=price_font,
        anchor="ma",
    )


def _compose_frame_overlays(
    rgb: np.ndarray,
    layout: CaptionLayout,
    *,
    traces: list[SnapshotTrace],
    current_index: int,
    drift_window: int,
    snapshots_per_video_sec: float,
    token_x: str,
    token_y: str,
    price_for_bin: dict[int, float] | None = None,
) -> np.ndarray:
    """Drift seismograph (left), time ticks, and vertical colour legend."""
    from PIL import Image, ImageDraw

    img = Image.fromarray(rgb).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    plot_box = _plot_box_pixels(rgb.shape[0], top_frac=layout.plot_top_frac)
    # 3D landscape reads lower bin ids on the left as X (mauve), higher on the
    # right as Y (teal) — opposite of the 2D seismic histogram drift convention.
    _draw_drift_seismograph(
        draw,
        traces=traces,
        current_index=current_index,
        plot_box=plot_box,
        style=SeismicStyle(),
        window=drift_window,
        strip_left=SPATIOTEMPORAL_DRIFT_LEFT,
        strip_right=SPATIOTEMPORAL_DRIFT_RIGHT,
        time_label_step_sec=DRIFT_TIME_LABEL_STEP_SEC,
        snapshots_per_video_sec=snapshots_per_video_sec,
        left_drift_color=_hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["X"]),
        right_drift_color=_hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["Y"]),
        trace_color=SPATIOTEMPORAL_DRIFT_TRACE,
        centre_line_color=(255, 255, 255),
    )
    _draw_drift_price_ticker(
        draw,
        plot_box=plot_box,
        traces=traces,
        current_index=current_index,
        price_for_bin=price_for_bin,
        token_x=token_x,
        token_y=token_y,
    )

    legend_font = _load_mono_font(LEGEND_FONT_SIZE)
    entries = (
        (f"{token_y} (Y)", _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["Y"])),
        (f"{token_x} (X)", _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["X"])),
        ("mix", _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["mix"])),
        ("drift", SPATIOTEMPORAL_DRIFT_TRACE),
    )
    legend_x = img.width - LEGEND_RIGHT_PAD_PX
    legend_y = layout.legend_top_px
    for i, (label, color) in enumerate(entries):
        draw.text(
            (legend_x, legend_y + i * LEGEND_LINE_PX),
            f"■ {label}",
            fill=(*color, 255),
            font=legend_font,
            anchor="rt",
        )

    img = Image.alpha_composite(img, overlay)
    return np.asarray(img.convert("RGB"))


def _time_x(layer_age: int, *, history: int) -> float:
    """Map layer age to X: NOW (age=0) at 0, oldest slot in the window at 1.

    Uses a fixed history span so early frames grow from the front toward the back
    instead of compressing visible slices to fill the full axis (which puts the
    second snapshot at the far end before the middle exists).
    """
    span = max(1, history - 1)
    return layer_age / span


def _envelope_indices(liquidity: np.ndarray) -> tuple[int, int]:
    """First/last bin indices with liquidity in a aligned grid row."""
    nz = np.flatnonzero(liquidity > 0)
    if len(nz) == 0:
        return 0, max(0, len(liquidity) - 1)
    return int(nz[0]), int(nz[-1])


def _gap_filled_liquidity(liquidity: np.ndarray) -> np.ndarray:
    """Bridge zero-liquidity holes between positive bins so end caps stay closed."""
    filled = liquidity.astype(np.float64, copy=True)
    nz = np.flatnonzero(filled > 0)
    if len(nz) < 2:
        return filled
    for left, right in zip(nz[:-1], nz[1:], strict=True):
        if right <= left + 1:
            continue
        xs = np.arange(left, right + 1, dtype=np.float64)
        filled[left : right + 1] = np.interp(xs, [float(left), float(right)], [filled[left], filled[right]])
    return filled


def _ensure_landscape_frame(
    traces: list[SnapshotTrace],
    t_start: int,
    current_index: int,
    frame: GlobalFrame,
    atlas: GlobalFrame | None,
    *,
    pad_bins: int,
    min_bins: int,
) -> GlobalFrame:
    """Expand the Y viewport so every visible snapshot's liquidity fits with padding."""
    content_lo = frame.bin_id_min
    content_hi = frame.bin_id_max
    for ti in range(t_start, current_index + 1):
        lo, hi = _observed_extent(traces[ti])
        content_lo = min(content_lo, lo)
        content_hi = max(content_hi, hi)

    padded_lo = content_lo - pad_bins
    padded_hi = content_hi + pad_bins
    span = max(padded_hi - padded_lo + 1, min_bins, frame.n_bins)
    mid = (content_lo + content_hi) // 2
    lo = mid - span // 2
    hi = lo + span - 1
    lo = min(lo, padded_lo, frame.bin_id_min)
    hi = max(hi, padded_hi, frame.bin_id_max)
    if hi - lo + 1 < span:
        mid = (lo + hi) // 2
        lo = mid - span // 2
        hi = lo + span - 1
    return GlobalFrame(*_clamp_to_atlas(lo, hi, atlas))


def _trace_grid(
    trace: SnapshotTrace,
    frame: GlobalFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Align trace columns to the visible bin-id grid."""
    bins = np.arange(frame.bin_id_min, frame.bin_id_max + 1, dtype=np.int32)
    n = len(bins)
    liquidity = np.zeros(n, dtype=np.float64)
    x_amount = np.zeros(n, dtype=np.float64)
    y_amount = np.zeros(n, dtype=np.float64)
    positions = {int(b): i for i, b in enumerate(trace.bin_ids)}
    for j, b in enumerate(bins):
        idx = positions.get(int(b))
        if idx is None:
            continue
        liquidity[j] = trace.liquidity[idx]
        x_amount[j] = trace.x_amount[idx]
        y_amount[j] = trace.y_amount[idx]
    return bins, liquidity, x_amount, y_amount


def _bin_rgb(
    trace: SnapshotTrace,
    bin_id: int,
    x_amount: float,
    y_amount: float,
    *,
    layer_age: int,
    style: SpatiotemporalStyle,
) -> tuple[float, float, float]:
    if int(bin_id) == trace.active_bin_id:
        rgb = _as_rgb_tuple(to_rgb(SPATIOTEMPORAL_ACTIVE_COLOR))
    else:
        rgb = tuple(c / 255.0 for c in _composition_rgb(x_amount, y_amount))
    return _mute_rgb(rgb, layer_age=layer_age, background=style.background)


def _cap_color_key(
    trace: SnapshotTrace,
    bin_id: int,
    x_amount: float,
    y_amount: float,
) -> str:
    """Coarse wall colour bucket; keeps cap geometry in a few stable polygons."""
    if int(bin_id) == trace.active_bin_id:
        return "active"
    x = max(0.0, x_amount)
    y = max(0.0, y_amount)
    total = x + y
    if total <= 0.0:
        return "X" if int(bin_id) <= trace.active_bin_id else "Y"
    share_x = x / total
    if 0.35 < share_x < 0.65:
        return "mix"
    return "X" if share_x >= 0.5 else "Y"


def _cap_key_rgb(
    key: str,
    *,
    layer_age: int,
    style: SpatiotemporalStyle,
) -> tuple[float, float, float]:
    if key == "active":
        rgb = _as_rgb_tuple(to_rgb(SPATIOTEMPORAL_ACTIVE_COLOR))
    elif key == "mix":
        rgb = tuple(c / 255.0 for c in _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["mix"]))
    else:
        rgb = tuple(c / 255.0 for c in _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS[key]))
    return _mute_rgb(rgb, layer_age=layer_age, background=style.background)


def _add_quad(
    quads: list,
    face_colors: list,
    edge_colors: list,
    vertices: list[tuple[float, float, float]],
    rgb: tuple[float, float, float],
) -> None:
    quads.append(vertices)
    face_colors.append((*rgb, 1.0))
    edge_colors.append(_ridge_edge(rgb))


def _add_cap_region(
    polys: list,
    face_colors: list,
    edge_colors: list,
    *,
    x_time: float,
    bins: np.ndarray,
    liquidity: np.ndarray,
    z_scale: float,
    start: int,
    end: int,
    rgb: tuple[float, float, float],
) -> None:
    """Vertical cap section as one monotone polygon, avoiding internal diagonals."""
    if end <= start:
        return
    rgba = (*rgb, 1.0)
    top = [
        (x_time, float(bins[j]), float(liquidity[j]) * z_scale)
        for j in range(start, end + 1)
    ]
    floor = [
        (x_time, float(bins[j]), 0.0)
        for j in range(end, start - 1, -1)
    ]
    polys.append(top + floor)
    face_colors.append(rgba)
    edge_colors.append(_ridge_edge(rgb))


def _add_cap_bar_lines(
    lines: list,
    colors: list,
    *,
    x_time: float,
    bins: np.ndarray,
    liquidity: np.ndarray,
    z_scale: float,
    start: int,
    end: int,
    rgb: tuple[float, float, float],
) -> None:
    """Front facade bin strokes, drawn as lines so no filled triangles overlap."""
    if end <= start:
        return
    line_rgb = tuple(min(1.0, c * 1.18) for c in rgb)
    rgba = (*line_rgb, CAP_BAR_ALPHA)
    for j in range(start, end + 1):
        z = float(liquidity[j]) * z_scale
        if z <= 0.0:
            continue
        y = float(bins[j])
        lines.append([(x_time, y, 0.0), (x_time, y, z)])
        colors.append(rgba)
    for j in range(start, end):
        z0 = float(liquidity[j]) * z_scale
        z1 = float(liquidity[j + 1]) * z_scale
        if max(z0, z1) <= 0.0:
            continue
        lines.append([(x_time, float(bins[j]), z0), (x_time, float(bins[j + 1]), z1)])
        colors.append(rgba)


def _draw_liquidity_landscape(
    ax,
    *,
    traces: list[SnapshotTrace],
    t_start: int,
    current_index: int,
    frame: GlobalFrame,
    liquidity_scale: float,
    history: int,
    style: SpatiotemporalStyle,
) -> None:
    """Continuous bin × time surface instead of disconnected vertical slices."""
    indices = list(range(t_start, current_index + 1))
    if not indices:
        return

    def add_profile_cap(
        trace_idx: int,
        *,
        layer_age: int,
        liquidity_row: np.ndarray | None = None,
        cap_x_time: float | None = None,
        cap_polys: list,
        cap_face_colors: list,
        cap_edge_colors: list,
        cap_bar_lines: list | None = None,
        cap_bar_colors: list | None = None,
    ) -> None:
        """Vertical face at one time slice: floor → surface along bin id."""
        bins, _, x_amount, y_amount = grids[trace_idx]
        liquidity = liquidity_row if liquidity_row is not None else grids[trace_idx][1]
        x_time = x_times[trace_idx] if cap_x_time is None else cap_x_time
        trace = traces[indices[trace_idx]]
        lo, hi = envelopes[trace_idx]
        if hi <= lo:
            hi = min(lo + 1, len(bins) - 1)
        region_start: int | None = None
        region_key: str | None = None

        def flush_region(end: int) -> None:
            nonlocal region_start, region_key
            if region_start is None or region_key is None:
                return
            _add_cap_region(
                cap_polys,
                cap_face_colors,
                cap_edge_colors,
                x_time=x_time,
                bins=bins,
                liquidity=liquidity,
                z_scale=z_scale,
                start=region_start,
                end=end,
                rgb=_cap_key_rgb(region_key, layer_age=layer_age, style=style),
            )
            if cap_bar_lines is not None and cap_bar_colors is not None:
                _add_cap_bar_lines(
                    cap_bar_lines,
                    cap_bar_colors,
                    x_time=x_time - CAP_BAR_X_EPSILON,
                    bins=bins,
                    liquidity=liquidity,
                    z_scale=z_scale,
                    start=region_start,
                    end=end,
                    rgb=_cap_key_rgb(region_key, layer_age=layer_age, style=style),
                )
            region_start = None
            region_key = None

        for i in range(lo, hi):
            z0 = float(liquidity[i]) * z_scale
            z1 = float(liquidity[i + 1]) * z_scale
            if max(z0, z1) <= 0.0:
                flush_region(i)
                continue
            key = _cap_color_key(
                trace,
                int(bins[i]),
                float(x_amount[i]),
                float(y_amount[i]),
            )
            if region_start is None:
                region_start = i
                region_key = key
            elif key != region_key:
                flush_region(i)
                region_start = i
                region_key = key
        flush_region(hi)

    def add_side_wall(t: int, *, side: str) -> None:
        """Side skirt between two time slices at the liquidity envelope edge."""
        bins, liq0, xa0, ya0 = grids[t]
        _, liq1, xa1, ya1 = grids[t + 1]
        x0, x1 = x_times[t], x_times[t + 1]
        if t + 1 == front_idx:
            liq1 = cap_liquidity[front_idx]
        age0 = current_index - indices[t]
        age1 = current_index - indices[t + 1]
        lo0, hi0 = envelopes[t]
        lo1, hi1 = envelopes[t + 1]
        if side == "left":
            i0, i1 = lo0, lo1
        else:
            i0, i1 = hi0, hi1
        trace0 = traces[indices[t]]
        trace1 = traces[indices[t + 1]]

        y0, y1 = float(bins[i0]), float(bins[i1])
        z00 = float(liq0[i0]) * z_scale
        z10 = float(liq1[i1]) * z_scale
        if max(z00, z10) <= 0.0:
            return

        rgb0 = _bin_rgb(
            trace0,
            int(bins[i0]),
            float(xa0[i0]),
            float(ya0[i0]),
            layer_age=age0,
            style=style,
        )
        rgb1 = _bin_rgb(
            trace1,
            int(bins[i1]),
            float(xa1[i1]),
            float(ya1[i1]),
            layer_age=age1,
            style=style,
        )
        rgb = tuple((a + b) / 2.0 for a, b in zip(rgb0, rgb1))
        _add_quad(
            quads,
            face_colors,
            edge_colors,
            [(x0, y0, 0.0), (x0, y0, z00), (x1, y1, z10), (x1, y1, 0.0)],
            rgb,
        )

    z_scale = 1.0 / max(liquidity_scale, 1e-9)
    grids = [_trace_grid(traces[i], frame) for i in indices]
    x_times = [_time_x(current_index - i, history=history) for i in indices]
    envelopes = [_envelope_indices(g[1]) for g in grids]
    front_idx = len(indices) - 1
    cap_liquidity = [g[1] for g in grids]
    cap_liquidity[front_idx] = _gap_filled_liquidity(cap_liquidity[front_idx])
    if front_idx > 0:
        cap_liquidity[0] = _gap_filled_liquidity(cap_liquidity[0])

    quads: list[list[tuple[float, float, float]]] = []
    face_colors: list[tuple[float, float, float, float]] = []
    edge_colors: list[tuple[float, float, float, float]] = []
    front_cap_polys: list[list[tuple[float, float, float]]] = []
    front_cap_face_colors: list[tuple[float, float, float, float]] = []
    front_cap_edge_colors: list[tuple[float, float, float, float]] = []
    front_bar_lines: list[list[tuple[float, float, float]]] = []
    front_bar_colors: list[tuple[float, float, float, float]] = []
    back_cap_polys: list[list[tuple[float, float, float]]] = []
    back_cap_face_colors: list[tuple[float, float, float, float]] = []
    back_cap_edge_colors: list[tuple[float, float, float, float]] = []

    # Top surface: stitch consecutive snapshots along time and bin id.
    for t in range(len(indices) - 1):
        bins, liq0, xa0, ya0 = grids[t]
        _, liq1, xa1, ya1 = grids[t + 1]
        x0, x1 = x_times[t], x_times[t + 1]
        age0 = current_index - indices[t]
        age1 = current_index - indices[t + 1]
        trace_newer = traces[indices[t + 1]]
        trace_older = traces[indices[t]]

        for i in range(len(bins) - 1):
            z00 = float(liq0[i]) * z_scale
            z01 = float(liq0[i + 1]) * z_scale
            z10 = float(liq1[i]) * z_scale
            z11 = float(liq1[i + 1]) * z_scale
            if max(z00, z01, z10, z11) <= 0.0:
                continue

            y0, y1 = float(bins[i]), float(bins[i + 1])
            rgb_newer = _bin_rgb(
                trace_newer,
                int(bins[i]),
                float(xa1[i]),
                float(ya1[i]),
                layer_age=age1,
                style=style,
            )
            rgb_older = _bin_rgb(
                trace_older,
                int(bins[i]),
                float(xa0[i]),
                float(ya0[i]),
                layer_age=age0,
                style=style,
            )
            rgb = tuple((a + b) / 2.0 for a, b in zip(rgb_newer, rgb_older))

            _add_quad(
                quads,
                face_colors,
                edge_colors,
                [
                    (x0, y0, z00),
                    (x0, y1, z01),
                    (x1, y1, z11),
                    (x1, y0, z10),
                ],
                rgb,
            )

    # NOW (front) and oldest (back) end caps: simple profile polygons, separate from the roof mesh.
    add_profile_cap(
        front_idx,
        layer_age=0,
        liquidity_row=cap_liquidity[front_idx],
        cap_x_time=x_times[front_idx] - CAP_X_EPSILON,
        cap_polys=front_cap_polys,
        cap_face_colors=front_cap_face_colors,
        cap_edge_colors=front_cap_edge_colors,
        cap_bar_lines=front_bar_lines,
        cap_bar_colors=front_bar_colors,
    )
    if front_idx > 0:
        add_profile_cap(
            0,
            layer_age=current_index - indices[0],
            liquidity_row=cap_liquidity[0],
            cap_x_time=x_times[0] + CAP_X_EPSILON,
            cap_polys=back_cap_polys,
            cap_face_colors=back_cap_face_colors,
            cap_edge_colors=back_cap_edge_colors,
        )

    # Left/right side walls along time so the volume is closed.
    for t in range(len(indices) - 1):
        add_side_wall(t, side="left")
        add_side_wall(t, side="right")

    if quads:
        surface = Poly3DCollection(quads, linewidths=RIDGE_LINEWIDTH)
        surface.set_facecolor(face_colors)
        surface.set_edgecolor(edge_colors)
        ax.add_collection3d(surface)

    if back_cap_polys:
        back_caps = Poly3DCollection(back_cap_polys, linewidths=CAP_LINEWIDTH, zsort="average")
        back_caps.set_facecolor(back_cap_face_colors)
        back_caps.set_edgecolor(back_cap_edge_colors)
        ax.add_collection3d(back_caps)

    if front_cap_polys:
        front_caps = Poly3DCollection(front_cap_polys, linewidths=CAP_LINEWIDTH, zsort="average")
        front_caps.set_facecolor(front_cap_face_colors)
        front_caps.set_edgecolor(front_cap_edge_colors)
        ax.add_collection3d(front_caps)

    if front_bar_lines:
        bars = Line3DCollection(front_bar_lines, linewidths=CAP_BAR_LINEWIDTH, colors=front_bar_colors)
        ax.add_collection3d(bars)


def _style_axes(
    ax,
    *,
    frame: GlobalFrame,
    style: SpatiotemporalStyle,
    token_x: str,
    token_y: str,
    pool_address: str,
    current: SnapshotTrace,
    total_traces: int,
    history: int,
    snapshots_per_video_sec: float,
    price_for_bin: dict[int, float] | None = None,
) -> None:
    ax.set_xlim(-CAP_X_EPSILON - CAP_BAR_X_EPSILON, 1.0 + CAP_X_EPSILON)
    ax.set_ylim(frame.bin_id_min, frame.bin_id_max)
    ax.set_zlim(0.0, 1.08)

    ax.set_xlabel("← SECONDS AGO", color=style.hud, labelpad=10, fontfamily="monospace")
    ax.set_ylabel(f"PRICE ({token_y}/{token_x})", color=style.hud, labelpad=14, fontfamily="monospace")
    ax.set_zlabel("LIQUIDITY", color=style.hud, labelpad=10, fontfamily="monospace")

    seconds_per_snapshot = 1.0 / max(snapshots_per_video_sec, 1e-9)
    history_span = max(1, history - 1)

    def _format_seconds_tick(value: float, _pos: int) -> str:
        seconds_ago = max(0.0, value) * history_span * seconds_per_snapshot
        if seconds_ago >= 10:
            return f"{seconds_ago:.0f}s"
        return f"{seconds_ago:.1f}s"

    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.xaxis.set_major_formatter(FuncFormatter(_format_seconds_tick))

    if price_for_bin:
        def _format_price_tick(value: float, _pos: int) -> str:
            bin_id = int(round(value))
            price = _price_for_axis_bin(bin_id, price_for_bin)
            if price is None:
                return str(bin_id)
            return f"{_format_price_value(price)}\n{bin_id}"

        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
        ax.yaxis.set_major_formatter(FuncFormatter(_format_price_tick))

    ax.tick_params(colors=style.hud, labelsize=8)
    for label in (*ax.get_xticklabels(), *ax.get_yticklabels(), *ax.get_zticklabels()):
        label.set_fontfamily("monospace")
    ax.xaxis.pane.fill = False
    ax.yaxis.pane.fill = False
    ax.zaxis.pane.fill = False
    ax.xaxis.pane.set_edgecolor(style.grid[:3] + (0.35,))
    ax.yaxis.pane.set_edgecolor(style.grid[:3] + (0.35,))
    ax.zaxis.pane.set_edgecolor(style.grid[:3] + (0.35,))
    ax.grid(True, color=[c / 255 for c in style.grid[:3]], alpha=0.25, linewidth=0.6)


def render_spatiotemporal_frame(
    traces: list[SnapshotTrace],
    *,
    frame: GlobalFrame,
    current_index: int,
    liquidity_scale: float,
    token_x: str,
    token_y: str,
    pool_address: str,
    width: int = int(SPATIOTEMPORAL_WIDTH_IN * 100),
    height: int = int(SPATIOTEMPORAL_HEIGHT_IN * 100),
    dpi: int = 100,
    history: int = SPATIOTEMPORAL_HISTORY,
    drift_window: int = 2,
    snapshots_per_video_sec: float = 24.0,
    price_for_bin: dict[int, float] | None = None,
    style: SpatiotemporalStyle = SpatiotemporalStyle(),
    atlas: GlobalFrame | None = None,
) -> np.ndarray:
    """Render one 3D frame: X=time, Y=bin id, Z=liquidity; camera from lower-left."""
    current_index = max(0, min(current_index, len(traces) - 1))
    t_start = max(0, current_index - history + 1)
    frame = _ensure_landscape_frame(
        traces,
        t_start,
        current_index,
        frame,
        atlas,
        pad_bins=SPATIOTEMPORAL_PAD_BINS,
        min_bins=SPATIOTEMPORAL_MIN_BINS,
    )

    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor=style.background)
    ax = fig.add_subplot(111, projection="3d", facecolor=style.background)

    _draw_liquidity_landscape(
        ax,
        traces=traces,
        t_start=t_start,
        current_index=current_index,
        frame=frame,
        liquidity_scale=liquidity_scale,
        history=history,
        style=style,
    )

    current = traces[current_index]
    _style_axes(
        ax,
        frame=frame,
        style=style,
        token_x=token_x,
        token_y=token_y,
        pool_address=pool_address,
        current=current,
        total_traces=len(traces),
        history=history,
        snapshots_per_video_sec=snapshots_per_video_sec,
        price_for_bin=price_for_bin,
    )

    # Platformer camera: mid-lower-left looking toward mid-upper-right.
    ax.view_init(elev=PLATFORMER_ELEV, azim=PLATFORMER_AZIM)
    ax.dist = 9.5

    caption_layout = _compute_caption_layout(
        width,
        height,
        token_x=token_x,
        token_y=token_y,
        pool_address=pool_address,
        snapshot_index=current.snapshot_index,
        total_snapshots=len(traces),
    )

    fig.subplots_adjust(
        left=_figure_left_frac(width),
        right=SPATIOTEMPORAL_FIG_RIGHT,
        top=caption_layout.plot_top_frac,
        bottom=SPATIOTEMPORAL_FIG_BOTTOM,
    )
    _draw_figure_inscription(
        fig,
        caption_layout,
        width_px=width,
        height_px=height,
    )
    rgb = _figure_to_rgb(fig)
    plt.close(fig)
    return _compose_frame_overlays(
        rgb,
        caption_layout,
        traces=traces,
        current_index=current_index,
        drift_window=drift_window,
        snapshots_per_video_sec=snapshots_per_video_sec,
        token_x=token_x,
        token_y=token_y,
        price_for_bin=price_for_bin,
    )


def build_spatiotemporal_mp4(
    traces: list[SnapshotTrace],
    *,
    atlas_frame: GlobalFrame,
    liquidity_scale: float,
    token_x: str,
    token_y: str,
    pool_address: str,
    output_path,
    fps: int,
    trace_indices: list[int] | None = None,
    price_for_bin: dict[int, float] | None = None,
    width: int = int(SPATIOTEMPORAL_WIDTH_IN * 100),
    height: int = int(SPATIOTEMPORAL_HEIGHT_IN * 100),
    dpi: int = 100,
) -> None:
    """Encode a spatiotemporal MP4 from prepared snapshot traces."""
    indices = trace_indices if trace_indices is not None else list(range(len(traces)))
    frame_arrays: list[np.ndarray] = []
    display_frame: GlobalFrame | None = None

    video_duration_sec = max(1e-6, len(indices) / fps)
    index_span = max(1, indices[-1] - indices[0]) if indices else 1
    snapshots_per_video_sec = index_span / video_duration_sec
    drift_window = max(
        2,
        int(round(SPATIOTEMPORAL_DRIFT_WINDOW_SECONDS * snapshots_per_video_sec)),
    )

    for current_index in indices:
        display_frame = compute_display_frame(
            traces,
            current_index,
            display_frame,
            atlas=atlas_frame,
            pad_bins=SPATIOTEMPORAL_PAD_BINS,
            min_display_bins=SPATIOTEMPORAL_MIN_BINS,
            viewport_edge_band=SPATIOTEMPORAL_EDGE_BAND,
            rolling_snapshots=SPATIOTEMPORAL_HISTORY,
            zoom_in_ratio=0.85,
        )
        frame_arrays.append(
            render_spatiotemporal_frame(
                traces,
                frame=display_frame,
                current_index=current_index,
                liquidity_scale=liquidity_scale,
                token_x=token_x,
                token_y=token_y,
                pool_address=pool_address,
                width=width,
                height=height,
                dpi=dpi,
                drift_window=drift_window,
                snapshots_per_video_sec=snapshots_per_video_sec,
                price_for_bin=price_for_bin,
                atlas=atlas_frame,
            )
        )

    encode_mp4(frame_arrays, output_path, fps=fps)


__all__ = [
    "build_spatiotemporal_mp4",
    "prepare_snapshot_traces",
    "render_spatiotemporal_frame",
]
