"""H4 step 2 pure functions: LIP reward share under the pre-registered scoring rules (no I/O).

Levels are bids, best (highest price) first: [(price, qty), ...]. Prices and tick are in dollars; rewards in dollars.
Rule C (primary): only orders inside the first `target` contracts of cumulative depth qualify (a price level is one
block; our order is last in its level). Rule L (lenient): every resting order qualifies.
"""
from __future__ import annotations

EPS = 1e-9
Levels = list[tuple[float, float]]


def _share(levels: Levels, target: float, discount: float, q: float, our_price: float, tick: float,
           lenient: bool) -> tuple[float, bool]:
    blocks: list[list[float]] = []            # [price, others_size, ours_size], best price first
    placed = False
    for p, s in levels:
        if s <= 0:
            continue
        if not placed and our_price > p + EPS:
            blocks.append([our_price, 0.0, q])
            placed = True
        if not placed and abs(p - our_price) <= EPS:
            blocks.append([p, s, q])
            placed = True
        else:
            blocks.append([p, s, 0.0])
    if not placed:
        blocks.append([our_price, 0.0, q])
    depth = sum(b[1] + b[2] for b in blocks)
    excluded = depth < target - EPS
    # reference price: first level where cumulative size (including ours) reaches target / 5
    cum, ref = 0.0, blocks[-1][0]
    for p, others, ours in blocks:
        cum += others + ours
        if cum >= target / 5 - EPS:
            ref = p
            break

    def weight(p: float) -> float:
        return 1.0 if p >= ref - EPS else discount ** round((ref - p) / tick)

    ahead, total, mine = 0.0, 0.0, 0.0
    for p, others, ours in blocks:
        w = weight(p)
        if others and (lenient or ahead < target - EPS):
            total += others * w
        if ours and (lenient or ahead + others < target - EPS):
            mine = ours * w
            total += mine
        ahead += others + ours
    return (mine / total if total > 0 else 0.0), excluded


def rule_c_share(levels_best_first: Levels, target: float, discount: float, q: float, our_price: float,
                 tick: float = 0.01) -> tuple[float, bool]:
    """Return (our score share on this side, side excluded). Excluded = total side depth incl. ours < target."""
    return _share(levels_best_first, target, discount, q, our_price, tick, lenient=False)


def rule_l_share(levels_best_first: Levels, target: float, discount: float, q: float, our_price: float,
                 tick: float = 0.01) -> tuple[float, bool]:
    return _share(levels_best_first, target, discount, q, our_price, tick, lenient=True)


def estimate_detail(program: dict, yes_levels: Levels, no_levels: Levels, q: float, now_ts: float | None,
                    tick: float = 0.01, lenient: bool = False) -> tuple[float, float, float, bool]:
    """Returns (reward_usd, yes_share, no_share, excluded_now). Our order sits at each side's best bid.

    program keys: period_reward_usd, target, discount, start_ts, end_ts. With now_ts=None the whole program is
    valued (time fraction 1); otherwise the fraction of the program still to run is used.
    """
    if not yes_levels or not no_levels:
        return 0.0, 0.0, 0.0, True
    fn = rule_l_share if lenient else rule_c_share
    sy, ex_y = fn(yes_levels, program["target"], program["discount"], q, yes_levels[0][0], tick)
    sn, ex_n = fn(no_levels, program["target"], program["discount"], q, no_levels[0][0], tick)
    excluded = ex_y or ex_n
    if now_ts is None:
        frac = 1.0
    else:
        total = max(1.0, program["end_ts"] - program["start_ts"])
        frac = max(0.0, program["end_ts"] - max(now_ts, program["start_ts"])) / total
    reward = 0.0 if excluded else program["period_reward_usd"] * (sy + sn) / 2 * frac
    return reward, sy, sn, excluded


def estimate_program_reward(program: dict, yes_levels: Levels, no_levels: Levels, q: float,
                            now_ts: float | None, tick: float = 0.01) -> float:
    return estimate_detail(program, yes_levels, no_levels, q, now_ts, tick)[0]
