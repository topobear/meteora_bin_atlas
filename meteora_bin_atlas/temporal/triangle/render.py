"""Composite triangle MP4 from three leg seismic strips."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from meteora_bin_atlas.paths import DATA_PROCESSED, PLOTS_DIR
from meteora_bin_atlas.temporal.render import resolve_token_labels
from meteora_bin_atlas.temporal.reserve import build_price_map
from meteora_bin_atlas.temporal.seismic import (
    DRIFT_WINDOW_SECONDS,
    GHOST_HISTORY,
    GlobalFrame,
    compute_display_frame,
    encode_mp4,
    prepare_snapshot_traces,
    render_seismic_frame,
)
from meteora_bin_atlas.temporal.triangle.resolve import TriangleLeg, TriangleSpec

# Leg strips reuse the temporal seismic renderer, but as compact edge-facing plots.
DEFAULT_LEG_DPI = 150
TRIANGLE_WIDTH = 3600
TRIANGLE_HEIGHT = 3400
TRIANGLE_RADIUS = 860
TRIANGLE_EDGE_COVERAGE = 0.86
TRIANGLE_STRIP_WIDTH = 1800
TRIANGLE_STRIP_HEIGHT = 360
TRIANGLE_STRIP_EDGE_GAP = 8
LEG_LABEL_OFFSET = 26

TRIANGLE_TOKEN_COLORS = {
    "X": "#FF335C",
    "Y": "#3BA7FF",
    "mix": "#E8F4FF",
    "empty": "#000000",
}

# Shorter skyline bars; active bin capped to neighbor heights.
TRIANGLE_DEFLECTION_RATIO = 0.28
TRIANGLE_LIQUIDITY_SCALE_HEADROOM = 2.85
TRIANGLE_ACTIVE_BIN_NEIGHBOR_CAP = 1.0
TRIANGLE_DRIFT_HEADROOM = 2.15
TRIANGLE_DISPLAY_PAD_BINS = 2
TRIANGLE_MIN_DISPLAY_BINS = 12
TRIANGLE_VIEWPORT_EDGE_BAND = 4
TRIANGLE_ZOOM_IN_RATIO = 0.44
TRIANGLE_ROLLING_SNAPSHOTS = 3


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
    cx = width / 2
    cy = height / 2 + radius * 0.06
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


def _outward_label_point(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    centroid: tuple[float, float],
    offset: float,
) -> tuple[float, float]:
    mid_x = (start[0] + end[0]) / 2
    mid_y = (start[1] + end[1]) / 2
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    if (centroid[0] - mid_x) * nx + (centroid[1] - mid_y) * ny > 0:
        nx, ny = -nx, -ny
    return mid_x + nx * offset, mid_y + ny * offset


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
        ghost_indices=ghost_indices,
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
    )
    image = Image.fromarray(rgba, mode="RGBA")
    return image.crop(_plot_crop_box(width, height))


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
    return (20, 48, width - 12, height - 36)


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


def _draw_triangle_frame(
    spec: TriangleSpec,
    leg_contexts: list[LegRenderContext],
    *,
    frame_index: int,
    display_frames: list[GlobalFrame | None],
    prior_indices: list[list[int]],
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
        ghost_indices = prior_indices[leg_idx][-GHOST_HISTORY:]
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

    label_font = _load_mono_font(20)
    title_font = _load_mono_font(28)
    hud_font = _load_mono_font(18)

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

    for ctx, _strip, start, end in leg_strips:
        leg_label = f"{ctx.leg.token_a}/{ctx.leg.token_b}"
        lx, ly = _outward_label_point(
            start,
            end,
            centroid=centroid,
            offset=LEG_LABEL_OFFSET,
        )
        lb = draw.textbbox((0, 0), leg_label, font=hud_font)
        lw = lb[2] - lb[0]
        lh = lb[3] - lb[1]
        draw.text(
            (lx - lw / 2, ly - lh / 2),
            leg_label,
            fill=(190, 210, 230, 220),
            font=hud_font,
        )

    title = f"CURRENCY TRIANGLE · {spec.triangle_id.upper().replace('_', '/')}"
    if spec.used_fallback and spec.fallback_from:
        title += f" (fallback from {spec.fallback_from})"
    draw.text((36, 24), title, fill=(190, 210, 230, 255), font=title_font)
    draw.text(
        (36, 58),
        f"FRAME {current_index + 1}/{n_frames}",
        fill=(175, 195, 215, 230),
        font=hud_font,
    )

    return np.asarray(canvas.convert("RGB"))


def build_triangle_temporal_mp4(
    spec: TriangleSpec,
    leg_csv_paths: tuple[Path, Path, Path],
    *,
    output_path: Path | None = None,
    fps: int = 24,
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

    n_frames = min(len(ctx.traces) for ctx in leg_contexts)
    if n_frames <= 0:
        raise ValueError("No snapshots available across triangle legs")

    video_duration_sec = max(1e-6, n_frames / fps)
    index_span = max(1, n_frames - 1)
    snapshots_per_video_sec = index_span / video_duration_sec
    drift_window = max(2, int(round(DRIFT_WINDOW_SECONDS * snapshots_per_video_sec)))

    display_frames: list[GlobalFrame | None] = [None, None, None]
    prior_indices: list[list[int]] = [[], [], []]
    frame_arrays: list[np.ndarray] = []

    for frame_index in range(n_frames):
        frame_arrays.append(
            _draw_triangle_frame(
                spec,
                leg_contexts,
                frame_index=frame_index,
                display_frames=display_frames,
                prior_indices=prior_indices,
                drift_window=drift_window,
                dpi=dpi,
            )
        )

    if output_path is None:
        PLOTS_DIR.mkdir(parents=True, exist_ok=True)
        from datetime import UTC, datetime

        ts = datetime.now(UTC).isoformat().replace(":", "-").replace(".", "-")
        output_path = PLOTS_DIR / f"triangle_temporal_{spec.triangle_id}_{ts}.mp4"

    output_path = output_path.resolve()
    encode_mp4(frame_arrays, output_path, fps=fps)
    print(f"Triangle MP4: {output_path}")
    return output_path
