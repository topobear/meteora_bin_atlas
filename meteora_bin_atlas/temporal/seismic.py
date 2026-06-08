"""Seismic-style wiggle renderer for temporal bin-atlas snapshots."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from meteora_bin_atlas.explore.labels import TOKEN_COLORS, bar_color_for_bin

# Fixed row height (px) keeps traces thin; rows pack upward from the plot bottom.
TRACE_ROW_PX = 18
TRACE_DEFLECTION_RATIO = 0.42


@dataclass(frozen=True)
class SeismicStyle:
    background: tuple[int, int, int] = (6, 10, 18)
    grid_major: tuple[int, int, int, int] = (40, 55, 80, 45)
    grid_minor: tuple[int, int, int, int] = (25, 35, 55, 25)
    trace_baseline: tuple[int, int, int, int] = (70, 85, 105, 70)
    active_bin: tuple[int, int, int, int] = (255, 200, 80, 160)
    hud: tuple[int, int, int, int] = (180, 210, 230, 220)
    wiggle_outline: tuple[int, int, int, int] = (220, 230, 240, 90)


@dataclass(frozen=True)
class SnapshotTrace:
    snapshot_index: int
    fetched_label: str
    distances: np.ndarray
    liquidity: np.ndarray
    x_amount: np.ndarray
    y_amount: np.ndarray


def _bin_distances(zoom_bins: int) -> np.ndarray:
    return np.arange(-zoom_bins, zoom_bins + 1, dtype=np.int32)


def _column_for_distances(group: pd.DataFrame, zoom_bins: int, column: str) -> np.ndarray:
    distances = _bin_distances(zoom_bins)
    indexed = (
        group.set_index("distance_from_active")[column]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0)
    )
    return indexed.reindex(distances, fill_value=0).to_numpy(dtype=np.float64)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def prepare_snapshot_traces(
    series_df: pd.DataFrame,
    *,
    zoom_bins: int,
) -> tuple[list[SnapshotTrace], float]:
    """Align each snapshot to a fixed bin-distance grid and compute a global wiggle scale."""
    snapshot_indices = sorted(series_df["snapshot_index"].unique())
    traces: list[SnapshotTrace] = []
    liquidity_max = 0.0

    for snapshot_index in snapshot_indices:
        group = series_df[series_df["snapshot_index"] == snapshot_index]
        liquidity = _column_for_distances(group, zoom_bins, "liquidity")
        x_amount = _column_for_distances(group, zoom_bins, "x_amount")
        y_amount = _column_for_distances(group, zoom_bins, "y_amount")
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
                distances=_bin_distances(zoom_bins),
                liquidity=liquidity,
                x_amount=x_amount,
                y_amount=y_amount,
            )
        )

    scale = liquidity_max * 1.05 if liquidity_max > 0 else 1.0
    return traces, scale


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


def _x_for_distance(
    distance: int,
    *,
    zoom_bins: int,
    plot_left: int,
    plot_right: int,
) -> float:
    span = plot_right - plot_left
    return plot_left + ((distance + zoom_bins) / (2 * zoom_bins)) * span


def _y_for_trace(
    trace_index: int,
    *,
    plot_bottom: int,
) -> float:
    """Oldest snapshot near the bottom; newer snapshots stack directly above."""
    return plot_bottom - (trace_index + 0.5) * TRACE_ROW_PX


def _draw_grid(
    draw: ImageDraw.ImageDraw,
    *,
    zoom_bins: int,
    total_traces: int,
    plot_box: tuple[int, int, int, int],
    style: SeismicStyle,
) -> None:
    left, top, right, bottom = plot_box

    for distance in range(-zoom_bins, zoom_bins + 1):
        x = _x_for_distance(distance, zoom_bins=zoom_bins, plot_left=left, plot_right=right)
        color = style.grid_major if distance % 10 == 0 else style.grid_minor
        draw.line([(x, top), (x, bottom)], fill=color, width=1)

    for trace_index in range(total_traces):
        y = _y_for_trace(trace_index, plot_bottom=bottom)
        if top <= y <= bottom:
            draw.line([(left, y), (right, y)], fill=style.grid_minor, width=1)

    active_x = _x_for_distance(0, zoom_bins=zoom_bins, plot_left=left, plot_right=right)
    draw.line([(active_x, top), (active_x, bottom)], fill=style.active_bin, width=2)


def _draw_horizontal_wiggle_trace(
    draw: ImageDraw.ImageDraw,
    *,
    trace: SnapshotTrace,
    trace_y: float,
    liquidity_scale: float,
    max_deflection: float,
    zoom_bins: int,
    plot_left: int,
    plot_right: int,
    style: SeismicStyle,
    highlight: bool,
) -> None:
    draw.line(
        [(plot_left, trace_y), (plot_right, trace_y)],
        fill=style.trace_baseline,
        width=1,
    )

    fill_alpha = 200 if highlight else 140
    outline_alpha = 230 if highlight else style.wiggle_outline[3]
    outline_width = 2 if highlight else 1

    wiggle_points: list[tuple[float, float]] = []
    for distance, liquidity, x_amount, y_amount in zip(
        trace.distances,
        trace.liquidity,
        trace.x_amount,
        trace.y_amount,
        strict=True,
    ):
        x = _x_for_distance(
            int(distance),
            zoom_bins=zoom_bins,
            plot_left=plot_left,
            plot_right=plot_right,
        )
        offset = (float(liquidity) / liquidity_scale) * max_deflection
        wiggle_points.append((x, trace_y - offset))

    for index in range(len(wiggle_points) - 1):
        x0, y0 = wiggle_points[index]
        x1, y1 = wiggle_points[index + 1]
        distance = int(trace.distances[index])
        color_hex = bar_color_for_bin(
            float(trace.x_amount[index]),
            float(trace.y_amount[index]),
            distance,
        )
        rgb = _hex_to_rgb(color_hex)
        if rgb == _hex_to_rgb(TOKEN_COLORS["empty"]):
            continue
        polygon = [(x0, trace_y), (x0, y0), (x1, y1), (x1, trace_y)]
        draw.polygon(polygon, fill=(*rgb, fill_alpha))

    if len(wiggle_points) >= 2:
        draw.line(
            wiggle_points,
            fill=(*style.wiggle_outline[:3], outline_alpha),
            width=outline_width,
            joint="curve",
        )


def render_seismic_frame(
    traces: list[SnapshotTrace],
    *,
    visible_count: int,
    zoom_bins: int,
    liquidity_scale: float,
    token_x: str,
    token_y: str,
    pool_address: str,
    width: int = 1400,
    height: int = 800,
    style: SeismicStyle = SeismicStyle(),
) -> np.ndarray:
    """Render one accumulation frame: thin horizontal traces, oldest bottom → newest top."""
    visible_count = max(1, min(visible_count, len(traces)))
    total_traces = len(traces)

    img = Image.new("RGBA", (width, height), style.background + (255,))
    plot_box = (100, 70, width - 30, height - 90)
    left, top, right, bottom = plot_box
    plot_bg = Image.fromarray(_plot_background(right - left, bottom - top), mode="RGB")
    img.paste(plot_bg, (left, top))

    draw = ImageDraw.Draw(img, "RGBA")
    _draw_grid(
        draw,
        zoom_bins=zoom_bins,
        total_traces=total_traces,
        plot_box=plot_box,
        style=style,
    )

    max_deflection = TRACE_ROW_PX * TRACE_DEFLECTION_RATIO

    for trace_index in range(visible_count):
        trace_y = _y_for_trace(trace_index, plot_bottom=bottom)
        _draw_horizontal_wiggle_trace(
            draw,
            trace=traces[trace_index],
            trace_y=trace_y,
            liquidity_scale=liquidity_scale,
            max_deflection=max_deflection,
            zoom_bins=zoom_bins,
            plot_left=left,
            plot_right=right,
            style=style,
            highlight=trace_index == visible_count - 1,
        )

    active_x = _x_for_distance(0, zoom_bins=zoom_bins, plot_left=left, plot_right=right)
    draw.text((active_x + 4, top + 4), "active bin", fill=style.hud)

    axis_y = height - 72
    draw.text((left, axis_y), f"-{zoom_bins}", fill=style.hud)
    draw.text((right - 36, axis_y), f"+{zoom_bins}", fill=style.hud)
    draw.text(
        (left + (right - left) / 2 - 95, axis_y),
        "distance from active bin",
        fill=style.hud,
    )

    oldest_y = _y_for_trace(0, plot_bottom=bottom)
    newest_y = _y_for_trace(visible_count - 1, plot_bottom=bottom)
    draw.text((left - 88, oldest_y + 4), "oldest", fill=(*style.hud[:3], 150))
    draw.text((left - 88, newest_y - 14), "newest ↑", fill=style.hud)

    current = traces[visible_count - 1]
    title = "DLMM liquidity seismogram"
    subtitle = (
        f"{token_x}/{token_y} · {pool_address[:6]}…{pool_address[-4:]} · "
        f"snap {current.snapshot_index + 1}/{total_traces} · {current.fetched_label}"
    )
    draw.text((left, 18), title, fill=style.hud)
    draw.text((left, 40), subtitle, fill=(*style.hud[:3], 170))

    legend_x = right - 210
    legend_y = top + 8
    draw.text((legend_x, legend_y), f"■ {token_y} (Y)", fill=(*_hex_to_rgb(TOKEN_COLORS["Y"]), 220))
    draw.text((legend_x, legend_y + 18), f"■ {token_x} (X)", fill=(*_hex_to_rgb(TOKEN_COLORS["X"]), 220))
    draw.text(
        (legend_x, legend_y + 36),
        f"■ {token_x} + {token_y}",
        fill=(*_hex_to_rgb(TOKEN_COLORS["mix"]), 220),
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
