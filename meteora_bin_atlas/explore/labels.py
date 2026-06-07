"""Token labels and bin coloring helpers."""

from __future__ import annotations

from dataclasses import dataclass

TOKEN_COLORS = {
    "X": "#9945FF",
    "Y": "#2775CA",
    "mix": "#14B8A6",
    "empty": "#D1D5DB",
}


@dataclass(frozen=True)
class TokenLabels:
    token_x: str
    token_y: str


def parse_token_labels(pool_label: str, pool_address: str) -> TokenLabels:
    """Infer human-readable token names from a pool pair label."""
    label = str(pool_label) if pool_label else pool_address
    if "-" in label:
        parts = label.split("-")
        token_x = "SOL" if "SOL" in parts[0] else "token X"
        token_y = parts[-1]
    else:
        token_x, token_y = "token X", "token Y"
    return TokenLabels(token_x=token_x, token_y=token_y)


def bar_color_for_bin(
    x_amount: float,
    y_amount: float,
    distance_from_active: int | None = None,
    colors: dict[str, str] | None = None,
) -> str:
    palette = colors or TOKEN_COLORS
    if distance_from_active == 0:
        return palette["mix"]
    if x_amount > 0 and y_amount > 0:
        return palette["mix"]
    if y_amount > 0:
        return palette["Y"]
    if x_amount > 0:
        return palette["X"]
    return palette["empty"]


def side_label(distance: int) -> str:
    if distance < 0:
        return "Bid (below active)"
    if distance == 0:
        return "Active"
    return "Ask (above active)"


def stocked_label(x_amount: float, y_amount: float, token_x: str, token_y: str) -> str:
    if x_amount > 0 and y_amount > 0:
        return f"X + Y ({token_x} + {token_y})"
    if y_amount > 0:
        return f"Y only ({token_y})"
    if x_amount > 0:
        return f"X only ({token_x})"
    return "empty"


def role_label(distance: int, token_x: str, token_y: str) -> str:
    if distance < 0:
        return f"Pool pays {token_y} if price drops to this shelf"
    if distance == 0:
        return "Current price / handoff between sides"
    return f"Pool pays {token_x} if price rises to this shelf"
