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
    _hex_to_rgb,
    compute_display_frame,
    encode_mp4,
    prepare_snapshot_traces,
)

# Visible history along the time (X) axis — NOW at lower-left, oldest at upper-right.
SPATIOTEMPORAL_HISTORY = 48
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


def _draw_liquidity_slice(
    ax,
    *,
    trace: SnapshotTrace,
    frame: GlobalFrame,
    x_time: float,
    liquidity_scale: float,
    layer_age: int,
    style: SpatiotemporalStyle,
) -> None:
    quads: list[list[tuple[float, float, float]]] = []
    face_colors: list[tuple[float, float, float, float]] = []
    edge_colors: list[tuple[float, float, float, float]] = []

    visible = (trace.bin_ids >= frame.bin_id_min) & (trace.bin_ids <= frame.bin_id_max)
    bin_ids = trace.bin_ids[visible]
    liquidity = trace.liquidity[visible]
    x_amount = trace.x_amount[visible]
    y_amount = trace.y_amount[visible]

    z_scale = 1.0 / max(liquidity_scale, 1e-9)
    for i in range(len(bin_ids) - 1):
        liq = float(liquidity[i])
        if liq <= 0 and float(liquidity[i + 1]) <= 0:
            continue

        y0, y1 = float(bin_ids[i]), float(bin_ids[i + 1])
        z0 = liq * z_scale
        z1 = float(liquidity[i + 1]) * z_scale

        if int(bin_ids[i]) == trace.active_bin_id:
            rgb = _as_rgb_tuple(to_rgb(SPATIOTEMPORAL_ACTIVE_COLOR))
        else:
            rgb = tuple(c / 255.0 for c in _composition_rgb(float(x_amount[i]), float(y_amount[i])))
        rgb = _mute_rgb(rgb, layer_age=layer_age, background=style.background)

        quad = [(x_time, y0, 0.0), (x_time, y0, z0), (x_time, y1, z1), (x_time, y1, 0.0)]
        quads.append(quad)
        face_colors.append((*rgb, 1.0))
        edge_colors.append(_ridge_edge(rgb))

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
) -> np.ndarray:
    """Render one 3D frame: X=time, Y=bin id, Z=liquidity; camera from lower-left."""
    current_index = max(0, min(current_index, len(traces) - 1))
    t_start = max(0, current_index - history + 1)

    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor=style.background)
    ax = fig.add_subplot(111, projection="3d", facecolor=style.background)

    for ti in range(t_start, current_index):
        layer_age = current_index - ti
        x_time = _time_x(layer_age, history=history)
        _draw_liquidity_slice(
            ax,
            trace=traces[ti],
            frame=frame,
            x_time=x_time,
            liquidity_scale=liquidity_scale,
            layer_age=layer_age,
            style=style,
        )

    # NOW slice drawn last so it sits on top when slices overlap in depth.
    _draw_liquidity_slice(
        ax,
        trace=traces[current_index],
        frame=frame,
        x_time=_time_x(0, history=history),
        liquidity_scale=liquidity_scale,
        layer_age=0,
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
            )
        )

    encode_mp4(frame_arrays, output_path, fps=fps)


__all__ = [
    "build_spatiotemporal_mp4",
    "prepare_snapshot_traces",
    "render_spatiotemporal_frame",
]
