"""Resolve currency triangle presets to three DLMM pool legs."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from meteora_bin_atlas.paths import PROJECT_ROOT

DEFAULT_TRIANGLE_ID = "sol_usdc_weth"
TRIANGLES_DIR = PROJECT_ROOT / "data" / "triangles"
# Complete triangle on Meteora when WETH/USDT legs are absent from datapi.
COMPLETE_FALLBACK_ID = "sol_usdc_cbtbtc"


@dataclass(frozen=True)
class TriangleToken:
    symbol: str
    mint: str


@dataclass(frozen=True)
class TriangleLeg:
    """One edge of the triangle from token_a (start vertex) to token_b (end vertex)."""

    token_a: str
    token_b: str
    pool_address: str
    token_x_mint: str
    token_y_mint: str
    flip_display: bool = False


@dataclass(frozen=True)
class TriangleSpec:
    triangle_id: str
    tokens: tuple[TriangleToken, TriangleToken, TriangleToken]
    legs: tuple[TriangleLeg, TriangleLeg, TriangleLeg]
    used_fallback: bool = False
    fallback_from: str | None = None


def _load_preset(triangle_id: str) -> dict:
    path = TRIANGLES_DIR / f"{triangle_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Triangle preset not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _leg_key(symbol_a: str, symbol_b: str) -> str:
    return f"{symbol_a}-{symbol_b}"


def _ordered_legs(tokens: tuple[TriangleToken, TriangleToken, TriangleToken]) -> list[tuple[str, str, str, str]]:
    """Return (token_a_symbol, token_b_symbol, mint_a, mint_b) for each edge CCW."""
    a, b, c = tokens
    return [
        (a.symbol, b.symbol, a.mint, b.mint),
        (b.symbol, c.symbol, b.mint, c.mint),
        (c.symbol, a.symbol, c.mint, a.mint),
    ]


def _resolve_via_npm(
    preset: dict,
    *,
    project_root: Path,
) -> dict[str, dict[str, str]]:
    """Call TypeScript resolver for mint-pair pool lookup."""
    preset_path = TRIANGLES_DIR / f"{preset['id']}.json"
    cmd = [
        "npm",
        "run",
        "resolve:triangle-legs",
        "--",
        "--preset",
        str(preset_path),
    ]
    result = subprocess.run(
        cmd,
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Triangle leg resolution failed: {stderr}")

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Triangle leg resolver produced no output")
    return json.loads(lines[-1])


def _build_spec(
    preset: dict,
    resolved: dict[str, dict[str, str]],
    *,
    used_fallback: bool = False,
    fallback_from: str | None = None,
) -> TriangleSpec:
    tokens = tuple(
        TriangleToken(symbol=t["symbol"], mint=t["mint"]) for t in preset["tokens"]
    )
    legs: list[TriangleLeg] = []
    for token_a, token_b, mint_a, mint_b in _ordered_legs(tokens):
        key = _leg_key(token_a, token_b)
        leg_info = resolved.get(key)
        if leg_info is None:
            raise ValueError(f"Missing resolved pool for leg {key}")

        pool = leg_info["pool_address"]
        token_x_mint = leg_info["token_x_mint"]
        token_y_mint = leg_info["token_y_mint"]

        # Swap seismic X/Y labels when pool token_x is at the leg's end vertex.
        flip_display = token_x_mint == mint_b

        legs.append(
            TriangleLeg(
                token_a=token_a,
                token_b=token_b,
                pool_address=pool,
                token_x_mint=token_x_mint,
                token_y_mint=token_y_mint,
                flip_display=flip_display,
            )
        )

    return TriangleSpec(
        triangle_id=preset["id"],
        tokens=tokens,
        legs=(legs[0], legs[1], legs[2]),
        used_fallback=used_fallback,
        fallback_from=fallback_from,
    )


def _try_resolve_preset(
    triangle_id: str,
    *,
    project_root: Path,
) -> TriangleSpec | None:
    preset = _load_preset(triangle_id)
    try:
        resolved = _resolve_via_npm(preset, project_root=project_root)
    except RuntimeError:
        return None

    missing = [key for key, info in resolved.items() if not info.get("pool_address")]
    if missing:
        return None

    return _build_spec(preset, resolved)


def _fallback_chain(primary_id: str) -> list[str]:
    chain: list[str] = []
    seen: set[str] = set()

    def add(tid: str) -> None:
        if tid in seen:
            return
        seen.add(tid)
        chain.append(tid)

    add(primary_id)
    try:
        preset = _load_preset(primary_id)
        if preset.get("fallback_id"):
            add(str(preset["fallback_id"]))
    except FileNotFoundError:
        pass
    add(COMPLETE_FALLBACK_ID)
    return chain


def resolve_triangle(
    triangle_id: str = DEFAULT_TRIANGLE_ID,
    *,
    project_root: Path | None = None,
) -> TriangleSpec:
    """Resolve a triangle preset, falling back when any leg pool is missing."""
    project_root = project_root or PROJECT_ROOT

    for index, candidate_id in enumerate(_fallback_chain(triangle_id)):
        spec = _try_resolve_preset(candidate_id, project_root=project_root)
        if spec is None:
            continue

        if index == 0:
            print(f"Triangle resolved: {spec.triangle_id}")
        else:
            print(
                f"WARNING: {triangle_id} incomplete — using fallback preset {spec.triangle_id}"
            )
            spec = TriangleSpec(
                triangle_id=spec.triangle_id,
                tokens=spec.tokens,
                legs=spec.legs,
                used_fallback=True,
                fallback_from=triangle_id,
            )

        for leg in spec.legs:
            print(f"  {leg.token_a}-{leg.token_b}: {leg.pool_address}")
        return spec

    print(
        f"ERROR: Could not resolve a complete triangle for {triangle_id}.",
        file=sys.stderr,
    )
    sys.exit(1)
