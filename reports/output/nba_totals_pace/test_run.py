import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import run  # noqa: E402


def toy():
    rows = []
    for g in range(40):
        for k, (bid, ask) in enumerate([(0.74, 0.76), (0.50, 0.52), (0.28, 0.30)]):
            strike = 210.5 + 3 * k
            mid = (bid + ask) / 2
            y = float((g + k) % 2)
            rows.append(dict(event=f"E{g}", ticker=f"E{g}-{k}", strike=strike, strike_type="greater", bid=bid,
                             ask=ask, mid=mid, y=y, p=0.9 - 0.3 * k, cvol=5.0))
    return pd.DataFrame(rows)


def test_run_bets_one_per_game_and_deterministic():
    s = toy()
    g = pd.DataFrame({"event": [f"E{i}" for i in range(40)], "T": np.arange(40) * 86400.0})
    b = run.run_bets(g, s, 0.03)
    assert b.event.is_unique and len(b) <= 40 and len(b) > 0
    assert (b.edge >= 0.03 - 1e-12).all() and b.strike.notna().all()
    mids = {(r.event, r.ticker): r.mid for r in s.itertuples()}
    assert all(0.15 <= mids[(r.event, r.id)] <= 0.85 for r in b.itertuples())
    # the row picked is the largest-edge strike-side among all candidates
    e0 = s[s.event == "E0"]
    best = max(max(lib.side_edges(r.p, r.bid, r.ask)[:2]) for r in e0.itertuples())
    assert b[b.event == "E0"].edge.iloc[0] == pytest.approx(best)
    c1, c2 = run.coin_flip(g, s, b, n=20), run.coin_flip(g, s, b, n=20)
    assert c1 == c2 and 0 <= c1["frac_copies_ge_actual"] <= 1
    rep = run.bet_report(b, g)
    assert rep["n"] == len(b) and rep["ci95"][0] <= rep["mean_net_per_contract"] <= rep["ci95"][1]


def test_gate3_count_and_logit_clip():
    q = pd.DataFrame({"p": [0.6, 0.5], "mid": [0.5, 0.5], "bid": [0.49, 0.49], "ask": [0.51, 0.51]})
    n, per_wk = run.gate3_count(q, 2.0)
    assert n == 1 and per_wk == 0.5
    assert np.isfinite(lib.logit([0.0, 1.0])).all()
