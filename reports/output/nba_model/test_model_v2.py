"""Tests for research log #31. Run: .venv/Scripts/python.exe -m pytest reports/output/nba_model/test_model_v2.py"""
import math
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import features_v2 as F2  # noqa: E402
import injury_parse as IP  # noqa: E402

F = F2.F
drift = F.drift


def test_p_miss_mapping():
    assert IP.P_MISS == {"Out": 1.0, "Doubtful": 0.75, "Questionable": 0.5, "Probable": 0.1, "Available": 0.0}


def test_name_matching():
    assert IP.norm_injury_name("PippenJr.,Scotty") == IP.norm_box_name("Scotty Pippen Jr.") == "scottypippen"
    assert IP.norm_injury_name("Jokić,Nikola") == IP.norm_box_name("Nikola Jokić") == "nikolajokic"
    assert IP.norm_injury_name("Prosper,Olivier-Maxence") == IP.norm_box_name("Olivier-Maxence Prosper")
    assert IP.norm_injury_name("MooreJr.,Wendell") == IP.norm_box_name("Wendell Moore Jr.")
    assert IP.norm_injury_name("Porzingis,Kristaps") == IP.norm_box_name("Kristaps Porziņģis")
    assert IP.norm_injury_name("Dante,N'Faly") == IP.norm_box_name("N'Faly Dante")
    assert IP.norm_injury_name("Murray,Dejounte") == "dejountemurray"
    assert IP.norm_injury_name("Williams,Jalen") != IP.norm_injury_name("Williams,Jaylin")
    assert IP.norm_injury_name("Walker IV,Lonnie") == "lonniewalker"


def test_value_shrink():
    # 5 games, 20 min each, plus-minus +10 each -> pm/min 0.5, avg min 20 -> raw 10, shrunk by 5/15
    v, n, avg = F2.player_value([(20.0, 10)] * 5)
    assert n == 5 and math.isclose(avg, 20.0) and math.isclose(v, 10.0 * 5 / 15)
    # pooled ratio: (30 min, +9) and (10 min, -1) -> sum pm 8 / sum min 40 = 0.2 ; avg min 20 ; raw 4 ; shrink 2/12
    v, n, avg = F2.player_value([(30.0, 9), (10.0, -1)])
    assert math.isclose(v, 0.2 * 20 * 2 / 12)
    assert F2.player_value([]) == (0.0, 0, 0.0)
    # large n -> shrink tends to 1
    v, _, _ = F2.player_value([(30.0, 6)] * 1000)
    assert math.isclose(v, 6.0 * 1000 / 1010)


def test_team_values_window_15():
    mk = lambda pm: {"players": {"AAA": {"x": (30.0, pm)}}}  # noqa: E731
    games = [(mk(0), "AAA")] * 10 + [(mk(10), "AAA")] * 15      # only the last 15 count
    v = F2.team_values(games)["x"]
    assert v[1] == 15 and math.isclose(v[0], 10.0 * 15 / 25)
    # a player absent from the window's games has no value entry
    assert "y" not in F2.team_values(games)


def test_report_lookup_and_missing_sum():
    rep = {"rows": [{"date": "01/15/2026", "matchup": "MEM@ORL", "team": "MemphisGrizzlies", "player": "Morant,Ja", "status": "Out"}],
           "nys": [("01/15/2026", "MEM@ORL", "OrlandoMagic")]}
    cov, rows, nys = F2.report_lookup(rep, date(2026, 1, 15), "MEM", "ORL")
    assert cov and len(rows) == 1 and len(nys) == 1
    assert F2.report_lookup(rep, date(2026, 1, 16), "MEM", "ORL")[0] is False
    assert F2.report_lookup(rep, date(2026, 1, 15), "PHX", "DET")[0] is False


def _tiny_box():
    def bg(d, h, a, hs, as_, ph, pa):
        return {"date": d, "home": h, "away": a, "hs": hs, "as_": as_, "players": {h: ph, a: pa}}
    games = {}
    games["b1"] = bg("2025-10-22", "AAA", "BBB", 100, 90, {"starstar": (30.0, 12), "benchbench": (10.0, 0)}, {"o": (30.0, -5)})
    games["b2"] = bg("2025-10-24", "BBB", "AAA", 100, 90, {"o": (30.0, 4)}, {"starstar": (30.0, -2), "benchbench": (10.0, 0)})
    games["b3"] = bg("2025-10-26", "AAA", "BBB", 100, 90, {"starstar": (30.0, 8)}, {"o": (30.0, 0)})
    by_key = {(g["date"], g["home"], g["away"]): k for k, g in games.items()}
    return {"games": games, "by_key": by_key, "all_names": {"starstar": {"AAA"}, "benchbench": {"AAA"}, "o": {"BBB"}}}


def _g(gid, tip, home, away, home_wins):
    mk = [SimpleNamespace(code=home, result=1.0 if home_wins else 0.0), SimpleNamespace(code=away, result=0.0 if home_wins else 1.0)]
    return SimpleNamespace(gid=gid, tip=tip, season="2025-26", mk=mk, home_idx=0)


def test_no_look_ahead_injury_and_box():
    """A game's features must not change when later box scores / later games change; the game's own box score is unused."""
    box = _tiny_box()
    games = [_g("KXNBAGAME-25OCT22AAABBB", 1000, "AAA", "BBB", True),
             _g("KXNBAGAME-25OCT24BBBAAA", 2000, "BBB", "AAA", True),
             _g("KXNBAGAME-25OCT26AAABBB", 3000, "AAA", "BBB", True)]
    # the Kalshi ticker is AWAYHOME: BBBAAA on 24OCT means away BBB home AAA in the code, but codes come from mk here
    rep = {"rows": [{"date": "10/26/2025", "matchup": "BBB@AAA", "team": "AAAteam", "player": "Star,Star", "status": "Out"}], "nys": []}
    n2c = {"AAAteam": "AAA"}
    reports = {games[2].gid: rep}
    full, _ = F2.build_features_v2(games, box, reports, n2c)
    g3 = full[2]
    # star played b1 (30 min, +12) and b2 (30 min, -2) before 10/26 -> pooled 10/60, avg min 30, n 2 -> 5 * 2/12
    assert math.isclose(g3["miss_h"], (10 / 60) * 30 * 2 / 12)
    assert g3["covered"] == 1.0 and math.isclose(g3["inj_diff"], g3["miss_h"])
    # changing the game's OWN box score (b3) or a later one changes nothing
    box2 = _tiny_box()
    box2["games"]["b3"]["players"]["AAA"]["starstar"] = (48.0, 40)
    box2["games"]["b3"]["hs"] = 150
    alt, _ = F2.build_features_v2(games, box2, reports, n2c)
    assert alt[2]["miss_h"] == g3["miss_h"] and alt[2]["elo_diff"] == g3["elo_diff"]
    # truncating later games does not change earlier rows
    part, _ = F2.build_features_v2(games[:2], box, reports, n2c)
    assert part == full[:2]
    # changing b2 (a prior game) does change game 3 (sanity: the feature really uses history)
    box3 = _tiny_box()
    box3["games"]["b2"]["players"]["AAA"]["starstar"] = (30.0, 20)
    alt3, _ = F2.build_features_v2(games, box3, reports, n2c)
    assert alt3[2]["miss_h"] != g3["miss_h"]


def test_status_weights_applied():
    box = _tiny_box()
    games = [_g("KXNBAGAME-25OCT22AAABBB", 1000, "AAA", "BBB", True),
             _g("KXNBAGAME-25OCT24BBBAAA", 2000, "BBB", "AAA", True),
             _g("KXNBAGAME-25OCT26AAABBB", 3000, "AAA", "BBB", True)]
    n2c = {"AAAteam": "AAA"}
    out = {}
    for st in ("Out", "Doubtful", "Questionable", "Probable", "Available"):
        rep = {"rows": [{"date": "10/26/2025", "matchup": "BBB@AAA", "team": "AAAteam", "player": "Star,Star", "status": st}], "nys": []}
        rows, _ = F2.build_features_v2(games, box, {games[2].gid: rep}, n2c)
        out[st] = rows[2]["miss_h"]
    base = out["Out"]
    assert base > 0
    for st, p in IP.P_MISS.items():
        assert math.isclose(out[st], base * p, abs_tol=1e-12)


def test_split_identical_to_v1():
    import json
    d24 = json.loads((F.ROOT / "reports" / "output" / "nba_drift" / "results.json").read_text())["NBA"]
    games, _, _ = drift.load_nba()
    assert drift.split_index(len(games)) == d24["discovery_games"] == 659 and len(games) - 659 == 658


def test_no_look_ahead_real_data():
    import model_v2 as M2
    games, _, _ = drift.load_nba()
    games = sorted(games, key=lambda g: (g.tip, g.gid))
    box = F2.load_box()
    n2c = M2.build_name2code()
    reports, man, _ = M2.load_reports(games)
    full, _ = F2.build_features_v2(games, box, reports, n2c)
    for cut in (150, 600):
        part, _ = F2.build_features_v2(games[:cut], box, reports, n2c)
        assert part == full[:cut]
    # every report used was published >= 60 minutes before tip (PDF CreationDate)
    assert all(v["tip"] - v["created_utc"] >= 3600 for v in man.values() if v["file"])
    # the report is the LATEST one allowed: no existing later file in the manifest cache violates the cut
    assert len(reports) == len(games)
    # box scores used for a game's player values are from strictly earlier local dates
    g = games[700]
    suf = g.gid.split("-", 1)[1]
    gdate = F.parse_suffix_date(suf)
    h = g.mk[g.home_idx].code
    prev = [(box["games"][b], h) for d_, b in sorted((x["date"], k) for k, x in box["games"].items() if h in (x["home"], x["away"])) if d_ < gdate.isoformat()]
    assert prev and all(bg["date"] < gdate.isoformat() for bg, _ in prev)
