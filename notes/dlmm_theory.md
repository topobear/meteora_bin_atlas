# DLMM theory

Terse, theory-only companion to `dlmm_notes.md`. Definition → claim, minimal prose.
Prerequisite: Uniswap V2. We build from it and add nothing not strictly needed.

Tokens $X$ (base), $Y$ (quote). Price $p$ = units of $Y$ per unit of $X$.

## 1. Recall: Uniswap V2

A V2 pool is reserves $(x, y)$ under the invariant

$$
x \cdot y = k.
$$

Spot price $p = y/x$ (since $-\mathrm{d}y/\mathrm{d}x = y/x$ on the curve). Liquidity
$L = \sqrt{k} = \sqrt{xy}$. Every trade slides $(x, y)$ along one fixed hyperbola, so
price varies continuously with size and every nonzero trade has slippage. Liquidity
covers $p \in (0, \infty)$, most of it at prices never visited.

## 2. The coordinate chart $(x, y) \to (p, L)$

The two scalars above are a change of coordinates. Define

$$
\Phi : (0, \infty)^2 \to (0, \infty)^2, \quad (x, y) \mapsto (p, L) = \left(\frac{y}{x}, \sqrt{xy}\right).
$$

$\Phi$ is a diffeomorphism of the open positive quadrant, with inverse

$$
x = \frac{L}{\sqrt{p}}, \qquad y = L\sqrt{p}.
$$

**Claim (V2 in chart).** In $(p, L)$ coordinates the invariant $x \cdot y = k$ is exactly
the leaf $L = \sqrt{k} = \mathrm{const}$. A V2 trade slides along that leaf: $L$ is fixed, $p$
is free.

This is the whole reframing: **$L$ is the structural coordinate, $p$ the free one.**
A pool is then just a choice of how much liquidity sits at each price — a *profile*
$p \mapsto L(p)$. V2 is the degenerate case $L(p) \equiv \mathrm{const}$ over all prices.

## 3. Reminder: Uniswap V3

V3 keeps the chart and lets the profile vary: $L(p)$ is **piecewise constant** on
price ranges (ticks). Concentrating a position with liquidity $L$ on $[p_a, p_b]$
gives, via the inverse chart integrated over the range,

$$
x = L\left(\frac{1}{\sqrt{p}} - \frac{1}{\sqrt{p_b}}\right), \qquad
y = L\left(\sqrt{p} - \sqrt{p_a}\right).
$$

($p_a \to 0$, $p_b \to \infty$ recovers $x = L/\sqrt{p}$, $y = L\sqrt{p}$, i.e. V2.) Within a range the
local invariant is still constant-product, so price moves continuously in-range.
A V3 pool is the liquidity profile $L(\cdot)$; spot $p$ walks across ranges as it trades.

DLMM is the next step: discretize the price axis and change the in-cell invariant.

## 4. The price grid (bins)

**Def (bin step).** Fix $s = \mathrm{bin\_step} / 10{,}000$. The grid is the geometric ladder

$$
p_i = (1 + s)^i, \quad i \in \mathbb{Z}.
$$

Integer $i$ is the **bin id**; $p_i$ its price. Adjacent bins differ by the fixed
ratio $1 + s$ (constant relative gap $s$, e.g. 25 bps $\Rightarrow$ 0.25%). The grid is
multiplicative because price is a ratio. DLMM puts liquidity only on this grid:
$L(\cdot)$ becomes a discrete profile $\{L_i\}$.

## 5. The bin (constant-sum cell)

Here DLMM departs from V3: each bin runs **constant-sum**, not constant-product.

**Def (bin invariant).** A bin $i$ with reserves $(x, y)$ carries liquidity

$$
L = p_i x + y.
$$

(Note: this is the constant-sum measure of liquidity, not the $\sqrt{xy}$ of §2–3.)
Trading inside bin $i$ conserves $L$ at the fixed price $p_i$ — move along the line
$p_i x + y = L$. Hence:

- **Claim (zero local slippage).** Any trade contained in one bin executes entirely
  at $p_i$. The bin is a collapsed V3 tick whose in-range price is frozen.
- **Claim (finite depth).** A bin absorbs at most its inventory: $x$ units of $X$ on
  the buy side, $y$ units of $Y$ on the sell side. When a reserve hits $0$ the bin is
  exhausted at $p_i$.

So slippage is exported from *within* a cell to *between* cells:

$$
\begin{aligned}
\text{V3 range} &: \quad x \cdot y = k \quad \text{on } [p_a, p_b] \quad \text{(price varies in-range)} \\
\text{DLMM bin} &: \quad p_i x + y = L \quad \text{on } \{p_i\} \quad \text{(price pinned in-bin)}
\end{aligned}
$$

A bin is the limit of a V3 tick whose width collapses to a point and whose price is
held constant — a clean "resting order at $p_i$."

## 6. Reserve composition across bins

Let $a$ be the **active bin** id (current price $p_a$). Concentrated-liquidity
geometry forces each bin's inventory:

$$
\begin{aligned}
i > a &\Rightarrow \text{ holds only } X \quad \text{(sold as price rises)} \\
i = a &\Rightarrow \text{ holds both } X, Y \quad \text{(the only mixed bin)} \\
i < a &\Rightarrow \text{ holds only } Y \quad \text{(buys } X \text{ as price falls)}
\end{aligned}
$$

**Claim (uniqueness).** At most one bin holds both assets; it is the active bin —
the discrete analogue of a V3 position being one-sided away from spot.

A pool is thus the discrete profile $i \mapsto L_i$ with origin at $a$.

## 7. Price dynamics

A buy of $X$ (price up) drains $X$ from bin $a$ at fixed $p_a$. The instant its $X$
hits $0$:

$$
a \leftarrow a + 1, \qquad p \leftarrow p_a (1 + s).
$$

Realized price is **piecewise constant within a bin, jumping across bins**: a step
function, not V2's smooth curve. A trade of size $\Delta$ walks bins, each segment cleared
at its own $p_i$; the average fill is the liquidity-weighted mean of crossed $p_i$.
As $s \to 0$ the steps shrink and continuity returns.

## 8. Dynamic fees (sketch)

Fee rate per bin crossed splits into fixed and volatility terms:

$$
\begin{aligned}
f &= f_{\mathrm{base}} + f_{\mathrm{var}}, \\
f_{\mathrm{base}} &= B \cdot s \quad (B = \text{base factor}), \\
f_{\mathrm{var}} &= A \cdot (v_a s)^2 \quad (A = \text{variable control}).
\end{aligned}
$$

$v_a$ is a **volatility accumulator**: it rises by the number of bins a swap crosses
and decays toward a reference between swaps. Fees are quadratic in both bin step and
recent price travel, so income spikes during fast multi-bin moves — compensating LPs
for the inventory risk that constant-sum bins concentrate.

## 9. What carries over

| Concept | Uniswap V2 | Uniswap V3 | DLMM |
|---|---|---|---|
| Free coordinate | $p$ | $p$ | $p$ (gridded $p_i$) |
| Structural coordinate | $L = \sqrt{xy}$ const | profile $L(p)$ p.w. const | profile $\{L_i\}$ discrete |
| Local invariant | $x \cdot y = k$ | $x \cdot y = k$ per range | $p_i x + y = L$ per bin |
| In-cell price | varies | varies | pinned at $p_i$ |
| Slippage | every trade | every trade | only between bins |
| State summary | $(x, y)$ | $L(\cdot) + p$ | active bin $a$ + $\{L_i\}$ |

The mental upgrade: pick the chart $(x,y) \to (p,L)$, treat liquidity as a profile over
price, then let DLMM discretize that profile and freeze price inside each cell. The
interesting structure is the *shape* of $\{L_i\}$ around the active bin $a$.
