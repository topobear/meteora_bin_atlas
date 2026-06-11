"""Spatiotemporal 3D renderer: bin lattice × time × liquidity (platformer view)."""

from __future__ import annotations

import io
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgb
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from meteora_bin_atlas.temporal.seismic import (
    GlobalFrame,
    SnapshotTrace,
    _clamp_to_atlas,
    _hex_to_rgb,
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
# Camera: time (X) reads lower-left (NOW) → upper-right (past) on screen.
PLATFORMER_ELEV = 28.0
PLATFORMER_AZIM = 142.0
# Older time slices mute toward the background (no alpha fade).
SLICE_MUTE_DECAY = 0.94
SLICE_MUTE_MIN = 0.42
# Active-bin drift ribbon sits slightly above the liquidity surface.
DRIFT_LIFT = 0.04
# Muted watercolor-cybernetic palette — dusty teals/mauves rather than neon.
SPATIOTEMPORAL_TOKEN_COLORS = {
    "X": "#A8789E",
    "Y": "#6AABB8",
    "mix": "#958FA8",
    "empty": "#1A1E24",
}
SPATIOTEMPORAL_ACTIVE_COLOR = "#C99562"
RIDGE_DARKEN = 0.62
RIDGE_LINEWIDTH = 0.35


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
    x_rgb = _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["X"])
    return tuple(int(round(y_rgb[i] + (x_rgb[i] - y_rgb[i]) * share_x)) for i in range(3))


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


def _time_x(layer_age: int, *, history: int) -> float:
    """Map layer age to X: NOW (age=0) at 0, oldest slot in the window at 1.

    Uses a fixed history span so early frames grow from the front toward the back
    instead of compressing visible slices to fill the full axis (which puts the
    second snapshot at the far end before the middle exists).
    """
    span = max(1, history - 1)
    return layer_age / span


def _liquidity_at_active(trace: SnapshotTrace) -> float:
    mask = trace.bin_ids == trace.active_bin_id
    if mask.any():
        return float(trace.liquidity[mask][0])
    return 0.0


def _envelope_indices(liquidity: np.ndarray) -> tuple[int, int]:
    """First/last bin indices with liquidity in a aligned grid row."""
    nz = np.flatnonzero(liquidity > 0)
    if len(nz) == 0:
        return 0, max(0, len(liquidity) - 1)
    return int(nz[0]), int(nz[-1])


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

    def add_profile_cap(trace_idx: int, *, layer_age: int) -> None:
        """Vertical face at one time slice: floor → surface along bin id."""
        bins, liquidity, x_amount, y_amount = grids[trace_idx]
        x_time = x_times[trace_idx]
        trace = traces[indices[trace_idx]]
        lo, hi = envelopes[trace_idx]
        for i in range(lo, hi):
            z0 = float(liquidity[i]) * z_scale
            z1 = float(liquidity[i + 1]) * z_scale
            if z0 <= 0.0 and z1 <= 0.0:
                continue
            y0, y1 = float(bins[i]), float(bins[i + 1])
            rgb = _bin_rgb(
                trace,
                int(bins[i]),
                float(x_amount[i]),
                float(y_amount[i]),
                layer_age=layer_age,
                style=style,
            )
            _add_quad(
                quads,
                face_colors,
                edge_colors,
                [(x_time, y0, z0), (x_time, y1, z1), (x_time, y1, 0.0), (x_time, y0, 0.0)],
                rgb,
            )

    def add_side_wall(t: int, *, side: str) -> None:
        """Side skirt between two time slices at the liquidity envelope edge."""
        bins, liq0, xa0, ya0 = grids[t]
        _, liq1, xa1, ya1 = grids[t + 1]
        x0, x1 = x_times[t], x_times[t + 1]
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

    quads: list[list[tuple[float, float, float]]] = []
    face_colors: list[tuple[float, float, float, float]] = []
    edge_colors: list[tuple[float, float, float, float]] = []

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

    # NOW (front) and oldest (back) end caps.
    add_profile_cap(len(indices) - 1, layer_age=0)
    if len(indices) > 1:
        add_profile_cap(0, layer_age=current_index - indices[0])

    # Left/right side walls along time so the volume is closed.
    for t in range(len(indices) - 1):
        add_side_wall(t, side="left")
        add_side_wall(t, side="right")

    if not quads:
        return

    mesh = Poly3DCollection(quads, linewidths=RIDGE_LINEWIDTH)
    mesh.set_facecolor(face_colors)
    mesh.set_edgecolor(edge_colors)
    ax.add_collection3d(mesh)


def _draw_drift_path(
    ax,
    *,
    traces: list[SnapshotTrace],
    t_start: int,
    current_index: int,
    liquidity_scale: float,
    history: int,
    style: SpatiotemporalStyle,
) -> None:
    indices = list(range(t_start, current_index + 1))
    if len(indices) < 2:
        return

    layer_ages = [current_index - i for i in indices]
    xs = [_time_x(age, history=history) for age in layer_ages]
    ys = [float(traces[i].active_bin_id) for i in indices]
    z_scale = 1.0 / max(liquidity_scale, 1e-9)
    zs = [_liquidity_at_active(traces[i]) * z_scale + DRIFT_LIFT for i in indices]

    drift_rgb = style.drift
    for seg in range(len(indices) - 1):
        seg_age = max(layer_ages[seg], layer_ages[seg + 1])
        seg_rgb = _mute_rgb(drift_rgb, layer_age=seg_age, background=style.background)
        ax.plot(
            xs[seg : seg + 2],
            ys[seg : seg + 2],
            zs[seg : seg + 2],
            color=seg_rgb,
            linewidth=3.2,
            zorder=10,
        )
    ax.scatter(
        [xs[-1]],
        [ys[-1]],
        [zs[-1]],
        color=drift_rgb,
        s=48,
        depthshade=False,
        zorder=11,
    )


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
) -> None:
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(frame.bin_id_min, frame.bin_id_max)
    ax.set_zlim(0.0, 1.08)

    ax.set_xlabel("TIME · NOW →", color=style.hud, labelpad=10)
    ax.set_ylabel("BIN ID", color=style.hud, labelpad=10)
    ax.set_zlabel("LIQUIDITY", color=style.hud, labelpad=10)

    ax.tick_params(colors=style.hud, labelsize=8)
    ax.xaxis.pane.fill = False
    ax.yaxis.pane.fill = False
    ax.zaxis.pane.fill = False
    ax.xaxis.pane.set_edgecolor(style.grid[:3] + (0.35,))
    ax.yaxis.pane.set_edgecolor(style.grid[:3] + (0.35,))
    ax.zaxis.pane.set_edgecolor(style.grid[:3] + (0.35,))
    ax.grid(True, color=[c / 255 for c in style.grid[:3]], alpha=0.25, linewidth=0.6)

    subtitle = (
        f"{token_x}/{token_y} · {pool_address[:6]}…{pool_address[-4:]} · "
        f"SNAP {current.snapshot_index + 1}/{total_traces}"
    )
    ax.set_title(
        "DLMM SPATIOTEMPORAL · platformer view",
        color=style.hud,
        fontsize=14,
        pad=16,
    )
    ax.text2D(
        0.02,
        0.96,
        subtitle,
        transform=ax.transAxes,
        color=style.hud,
        fontsize=10,
    )

    y_rgb = tuple(c / 255 for c in _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["Y"]))
    x_rgb = tuple(c / 255 for c in _hex_to_rgb(SPATIOTEMPORAL_TOKEN_COLORS["X"]))
    mix_rgb = tuple(c / 255 for c in _composition_rgb(1.0, 1.0))
    drift_rgb = style.drift
    legend_x = 0.99
    legend_top = 0.88
    legend_line = 0.055
    for i, (label, rgb) in enumerate(
        (
            (f"{token_y} (Y)", y_rgb),
            (f"{token_x} (X)", x_rgb),
            ("mix", mix_rgb),
            ("active / drift", drift_rgb),
        )
    ):
        ax.text2D(
            legend_x,
            legend_top - i * legend_line,
            f"■ {label}",
            transform=ax.transAxes,
            color=rgb,
            fontsize=9,
            ha="right",
            va="top",
        )


def render_spatiotemporal_frame(
    traces: list[SnapshotTrace],
    *,
    frame: GlobalFrame,
    current_index: int,
    liquidity_scale: float,
    token_x: str,
    token_y: str,
    pool_address: str,
    width: int = 1400,
    height: int = 800,
    dpi: int = 100,
    history: int = SPATIOTEMPORAL_HISTORY,
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

    _draw_drift_path(
        ax,
        traces=traces,
        t_start=t_start,
        current_index=current_index,
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
    )

    # Platformer camera: mid-lower-left looking toward mid-upper-right.
    ax.view_init(elev=PLATFORMER_ELEV, azim=PLATFORMER_AZIM)
    ax.dist = 9.5

    fig.subplots_adjust(left=0.02, right=0.90, top=0.94, bottom=0.06)
    rgb = _figure_to_rgb(fig)
    plt.close(fig)
    return rgb


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
    width: int = 1400,
    height: int = 800,
    dpi: int = 100,
) -> None:
    """Encode a spatiotemporal MP4 from prepared snapshot traces."""
    indices = trace_indices if trace_indices is not None else list(range(len(traces)))
    frame_arrays: list[np.ndarray] = []
    display_frame: GlobalFrame | None = None

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
                atlas=atlas_frame,
            )
        )

    encode_mp4(frame_arrays, output_path, fps=fps)


__all__ = [
    "build_spatiotemporal_mp4",
    "prepare_snapshot_traces",
    "render_spatiotemporal_frame",
]
