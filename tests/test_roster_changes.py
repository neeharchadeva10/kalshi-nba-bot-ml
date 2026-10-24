import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import roster_changes as R


def row(player, mpg, ppg, age=27, team="BOS", direction="IN", gp=50):
    return {"player": player, "mpg": mpg, "ppg": ppg, "age": age, "tier": R.T.tier_of(mpg), "team": team, "direction": direction,
            "veteran": R.T.is_veteran(age), "gp": gp, "few_games": gp < 10, "from_team": "X", "to_team": "Y", "rpg": 1.0, "apg": 1.0, "pm_pg": None}


def test_net_math_toy():
    ins = [row("Star", 34.0, 25.0), row("Mid", 15.0, 6.0, age=33), row("Mid2", 24.0, 10.0), row("Deep", 5.0, 1.0)]
    outs = [row("OutStar", 30.0, 20.0, direction="OUT"), row("OutBench", 14.0, 4.0, direction="OUT"), row("Rookie", None, None, direction="OUT")]
    s = R.net_summary(ins, outs)
    assert s["n_in"] == 4 and s["n_out"] == 3
    assert s["net_mpg"] == pytest.approx(78 - 44) and s["net_ppg"] == pytest.approx(42 - 24)
    assert s["net_mpg_star"] == pytest.approx(4.0) and s["net_ppg_star"] == pytest.approx(5.0)
    assert s["net_mpg_starter"] == pytest.approx(24.0)            # Mid2 in
    assert s["net_mpg_bench"] == pytest.approx(15.0 - 14.0)
    assert s["net_mpg_deep"] == pytest.approx(5.0)                # "none" tier (Rookie) counts as deep with 0 production
    assert s["bench_up_mpg"] == pytest.approx((15 + 24) - 14) and s["bench_up_ppg"] == pytest.approx((6 + 10) - 4)
    assert s["vets_added"] == ["Mid"]
    # tier nets add up to the overall net
    assert sum(s["net_mpg_" + t] for t in R.TIERS) == pytest.approx(s["net_mpg"])


def test_boundaries_in_bench_band():
    s = R.net_summary([row("A", 28.0, 20.0), row("B", 12.0, 5.0), row("C", 11.9, 4.0)], [])
    assert s["bench_up_mpg"] == pytest.approx(12.0)               # 28.0 is a star (excluded), 12.0 included, 11.9 excluded


def test_empty_team_and_ranking():
    tabs = R.team_tables([row("A", 30.0, 20.0, team="AAA"), row("B", 30.0, 20.0, team="BBB", direction="OUT")], ["AAA", "BBB", "CCC"])
    assert [t["team"] for t in tabs] == ["AAA", "CCC", "BBB"]
    assert "no recorded changes" in tabs[1]["text"]


def test_build_rows_and_dedupe():
    m = pd.DataFrame([{"athlete_id": 1, "player": "P", "from_team": "A", "to_team": "B", "label": "moved", "type": "offseason", "source": "player_moves.csv"}])
    rows = R.build_rows(m, {1: 33}, {1: {"gp": 5, "mpg": 21.0, "ppg": 8.0, "rpg": 2.0, "apg": 1.0, "pm_pg": -1.5}})
    assert {(r["team"], r["direction"]) for r in rows} == {("B", "IN"), ("A", "OUT")}
    assert rows[0]["tier"] == "starter" and rows[0]["veteran"] and rows[0]["few_games"]
    assert R.build_rows(m, {}, {})[0]["tier"] == "none"


def test_line_text_mentions_not_prediction_in_md(tmp_path):
    tabs = R.team_tables([row("A", 30.0, 20.0, team="AAA", age=35)], ["AAA"])
    p = tmp_path / "x.md"
    R.write_md(tabs, p, "2026-10-23")
    t = p.read_text(encoding="utf-8")
    assert "not a prediction" in t and "Veterans (32+) added: A" in tabs[0]["text"] and "| 1 | AAA |" in t
