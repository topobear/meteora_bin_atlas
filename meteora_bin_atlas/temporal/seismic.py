"""Seismic-style wiggle renderer for temporal bin-atlas snapshots."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from meteora_bin_atlas.explore.labels import bar_color_for_bin

# Skyline rises from the plot floor; this fraction caps peak height below the top margin.
CURRENT_DEFLECTION_RATIO = 0.86
GHOST_HISTORY = 7
CURRENT_FILL_ALPHA = 88
CURRENT_OUTLINE_ALPHA = 130

# Muted token palette for the blackboard (keeps notebook TOKEN_COLORS unchanged).
SEISMIC_TOKEN_COLORS = {
    "X": "#5E4F78",
    "Y": "#35566E",
    "mix": "#3F6862",
    "empty": "#2A3340",
}


@dataclass(frozen=True)
class SeismicStyle:
    background: tuple[int, int, int] = (6, 10, 18)
    grid_major: tuple[int, int, int, int] = (40, 55, 80, 45)
    grid_minor: tuple[int, int, int, int] = (25, 35, 55, 25)
    active_bin: tuple[int, int, int, int] = (175, 145, 90, 120)
    hud: tuple[int, int, int, int] = (150, 165, 180, 200)
    wiggle_outline: tuple[int, int, int, int] = (160, 170, 185, 70)


@dataclass(frozen=True)
class GlobalFrame:
    """Fixed bin-id axis spanning the union of all snapshots in a series."""

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


def _plot_background(plot_width: int, plot_height: int) -> np.ndarray:
    y, x = np.ogrid[:plot_height, :plot_width]
    cx = plot_width / 2
    cy = plot_height / 2
    dist = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2)
    dist = np.clip(dist * 1.35, 0.0, 1.0)
    r = (10 + (4 - 10) * dist).astype(np.uint8)
    g = (16 + (8 - 16) * dist).astype(np.uint8)
    b = (28 + (14 - 28) * dist).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


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


def _muted_rgb(hex_color: str, *, layer_age: int) -> tuple[int, int, int]:
    rgb = _hex_to_rgb(hex_color)
    bg = SeismicStyle.background
    mute = 0.38 + 0.62 * (0.72 ** layer_age)
    return tuple(int(bg[i] + (rgb[i] - bg[i]) * mute) for i in range(3))


def _ghost_alphas(layer_age: int) -> tuple[int, int]:
    """Steady-state alpha for a layer `layer_age` snapshots behind the current one."""
    if layer_age <= 0:
        return CURRENT_FILL_ALPHA, CURRENT_OUTLINE_ALPHA
    fill = max(10, int(62 * (0.60 ** layer_age)))
    outline = max(14, int(78 * (0.64 ** layer_age)))
    return fill, outline


def _layer_style(layer_age: int, transition_blend: float) -> LayerStyle:
    blend = max(0.0, min(1.0, transition_blend))
    ghost_fill, ghost_outline = _ghost_alphas(layer_age)

    if layer_age == 0:
        return LayerStyle(
            fill_alpha=int(CURRENT_FILL_ALPHA * blend),
            outline_alpha=int(CURRENT_OUTLINE_ALPHA * blend),
            outline_width=2,
        )
    if layer_age == 1:
        return LayerStyle(
            fill_alpha=int(CURRENT_FILL_ALPHA + (ghost_fill - CURRENT_FILL_ALPHA) * blend),
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
    style: SeismicStyle,
) -> None:
    left, top, right, bottom = plot_box
    cell_w = _bin_cell_width(frame=frame, plot_left=left, plot_right=right)

    for offset in range(frame.n_bins):
        bin_id = frame.bin_id_min + offset
        x = _x_edge_for_bin_id(bin_id, frame=frame, plot_left=left, cell_w=cell_w)
        color = style.grid_major if offset % 10 == 0 else style.grid_minor
        draw.line([(x, top), (x, bottom)], fill=color, width=1)


def _draw_active_bin_marker(
    draw: ImageDraw.ImageDraw,
    *,
    active_bin_id: int,
    frame: GlobalFrame,
    plot_box: tuple[int, int, int, int],
    style: SeismicStyle,
) -> float:
    left, top, right, bottom = plot_box
    cell_w = _bin_cell_width(frame=frame, plot_left=left, plot_right=right)
    active_x = _x_center_for_bin_id(active_bin_id, frame=frame, plot_left=left, cell_w=cell_w)
    draw.line([(active_x, top), (active_x, bottom)], fill=style.active_bin, width=2)
    draw.text((active_x + 4, top + 4), f"active bin ({active_bin_id})", fill=style.hud)
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
    wiggle_points: list[tuple[float, float]] = []
    empty_rgb = _hex_to_rgb(SEISMIC_TOKEN_COLORS["empty"])

    for bin_id, liquidity, x_amount, y_amount in zip(
        trace.bin_ids,
        trace.liquidity,
        trace.x_amount,
        trace.y_amount,
        strict=True,
    ):
        bin_id = int(bin_id)
        x_center = _x_center_for_bin_id(bin_id, frame=frame, plot_left=plot_left, cell_w=cell_w)
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
        rgb = _muted_rgb(color_hex, layer_age=layer_age)
        if rgb == empty_rgb:
            continue

        x_left = _x_edge_for_bin_id(bin_id, frame=frame, plot_left=plot_left, cell_w=cell_w)
        x_right = x_left + cell_w
        polygon = [(x_left, trace_y), (x_left, peak_y), (x_right, peak_y), (x_right, trace_y)]
        draw.polygon(polygon, fill=(*rgb, layer.fill_alpha))

    if len(wiggle_points) >= 2 and layer.outline_alpha > 0:
        draw.line(
            wiggle_points,
            fill=(*SeismicStyle.wiggle_outline[:3], layer.outline_alpha),
            width=layer.outline_width,
            joint="curve",
        )


def render_seismic_frame(
    traces: list[SnapshotTrace],
    *,
    frame: GlobalFrame,
    current_index: int,
    transition_blend: float = 1.0,
    zoom_bins: int | None = None,
    liquidity_scale: float,
    token_x: str,
    token_y: str,
    pool_address: str,
    width: int = 1400,
    height: int = 800,
    style: SeismicStyle = SeismicStyle(),
) -> np.ndarray:
    """Render the blackboard in a fixed global bin-id frame; active bin marker slides."""
    _ = zoom_bins
    current_index = max(0, min(current_index, len(traces) - 1))
    total_traces = len(traces)

    img = Image.new("RGBA", (width, height), style.background + (255,))
    plot_box = (100, 70, width - 30, height - 90)
    left, top, right, bottom = plot_box
    plot_bg = Image.fromarray(_plot_background(right - left, bottom - top), mode="RGB")
    img.paste(plot_bg, (left, top))

    draw = ImageDraw.Draw(img, "RGBA")
    _draw_grid(draw, frame=frame, plot_box=plot_box, style=style)

    trace_y = float(bottom)
    max_deflection = (bottom - top) * CURRENT_DEFLECTION_RATIO
    first_layer = max(0, current_index - GHOST_HISTORY)

    for layer_index in range(first_layer, current_index + 1):
        layer_age = current_index - layer_index
        layer = _layer_style(layer_age, transition_blend)
        _draw_horizontal_wiggle_trace(
            draw,
            trace=traces[layer_index],
            frame=frame,
            trace_y=trace_y,
            liquidity_scale=liquidity_scale,
            max_deflection=max_deflection,
            plot_left=left,
            plot_right=right,
            layer=layer,
            layer_age=layer_age,
        )

    current = traces[current_index]
    _draw_active_bin_marker(
        draw,
        active_bin_id=current.active_bin_id,
        frame=frame,
        plot_box=plot_box,
        style=style,
    )

    axis_y = height - 72
    draw.text((left, axis_y), str(frame.bin_id_min), fill=style.hud)
    draw.text((right - 52, axis_y), str(frame.bin_id_max), fill=style.hud)
    draw.text(
        (left + (right - left) / 2 - 70, axis_y),
        "bin id (global lattice)",
        fill=style.hud,
    )

    ghost_count = current_index - first_layer
    if ghost_count > 0:
        draw.text(
            (left - 88, top + 10),
            f"{ghost_count} fading trace{'s' if ghost_count != 1 else ''}",
            fill=(*style.hud[:3], 140),
        )

    title = "DLMM liquidity seismogram"
    subtitle = (
        f"{token_x}/{token_y} · {pool_address[:6]}…{pool_address[-4:]} · "
        f"snap {current.snapshot_index + 1}/{total_traces} · {current.fetched_label}"
    )
    draw.text((left, 18), title, fill=style.hud)
    draw.text((left, 40), subtitle, fill=(*style.hud[:3], 170))

    legend_x = right - 210
    legend_y = top + 8
    draw.text(
        (legend_x, legend_y),
        f"■ {token_y} (Y)",
        fill=(*_muted_rgb(SEISMIC_TOKEN_COLORS["Y"], layer_age=0), 180),
    )
    draw.text(
        (legend_x, legend_y + 18),
        f"■ {token_x} (X)",
        fill=(*_muted_rgb(SEISMIC_TOKEN_COLORS["X"], layer_age=0), 180),
    )
    draw.text(
        (legend_x, legend_y + 36),
        f"■ {token_x} + {token_y}",
        fill=(*_muted_rgb(SEISMIC_TOKEN_COLORS["mix"], layer_age=0), 180),
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
