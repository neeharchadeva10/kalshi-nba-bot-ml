import os, sys
from datetime import date
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import live_scores_dashboard as L


def test_elapsed():
    assert L.elapsed_minutes(1, "12:00") == 0
    assert L.elapsed_minutes(2, "3:38") == pytest.approx(20.3667, abs=0.01)
    assert L.elapsed_minutes(4, "0:00") == 48
    assert L.elapsed_minutes(5, "2:00") == 51
    assert L.elapsed_minutes(None, "2:00") is None
    assert L.elapsed_minutes(2, "") is None
    assert L.elapsed_minutes(2, "abc") is None
    assert L.elapsed_minutes(0, "5:00") is None


def test_pace():
    assert L.pace_projection(10, None) is None
    assert L.pace_projection(10, 2.9) is None
    assert L.pace_projection(100, 24) == 200
    assert L.pace_projection(230, 51) == 230


def test_bet_state():
    assert L.bet_state("OVER", 220.5, 221, False) == ("WON (locked)", 0)
    assert L.bet_state("OVER", 220.5, 220, False) == ("LIVE", 1)
    assert L.bet_state("OVER", 220.5, 220, True) == ("LOST", 1)
    assert L.bet_state("UNDER", 220.5, 221, False)[0] == "LOST (locked)"
    assert L.bet_state("UNDER", 220.5, 220, False) == ("LIVE", 0)
    assert L.bet_state("UNDER", 220.5, 220, True)[0] == "WON"
    assert L.bet_state("UNDER", 241.5, 199, False) == ("LIVE", 42)


def test_chance():
    assert L.market_chance("OVER", "0.60", "0.70") == pytest.approx(0.65)
    assert L.market_chance("UNDER", "0.60", "0.70") == pytest.approx(0.35)
    assert L.market_chance("OVER", None, "0.7") is None


def test_keys():
    assert L.date_code(date(2026, 10, 21)) == "26OCT21"
    assert L.parse_key("KXNBATOTAL-26OCT21MINMIA") == ("26OCT21MINMIA", "MIN", "MIA")
    assert L.parse_key("KXNBAGAME-26OCT21MINMIA") is None


def test_pick_selection():
    rows = [{"ts": "2026-10-21T19:00:00", "game": "G", "strike": 1},
            {"ts": "2026-10-21T22:00:00", "game": "G", "strike": 2},
            {"ts": "2026-10-21T23:30:00", "game": "G", "strike": 3},
            {"ts": "2026-10-21T20:00:00", "game": "X", "strike": 9}]
    assert L.select_pick(rows, "G", "2026-10-21", "2026-10-21T23:00:00Z")["strike"] == 2
    assert L.select_pick(rows, "G", "2026-10-21", "2026-10-21T10:00:00Z")["strike"] == 3
    assert L.select_pick(rows, "Z", "2026-10-21", "x") is None


def test_fills():
    raw = {"fills": {"a": [
        {"ticker": "KXNBATOTAL-26OCT21MINMIA-242", "side": "no", "action": "buy", "yes_price_dollars": "0.48",
         "no_price_dollars": "0.52", "fee_cost": "0.02", "count_fp": "2.00"},
        {"ticker": "KXNBATOTAL-26OCT21MINMIA-242", "side": "no", "action": "sell", "no_price_dollars": "0.9", "count_fp": "1"},
        {"ticker": "KXNBAGAME-26OCT21MINMIA", "side": "yes", "action": "buy", "yes_price_dollars": "0.5", "count_fp": "1"}],
        "b": [{"ticker": "KXNBATOTAL-26OCT21CHITOR-238", "side": "yes", "action": "buy", "yes_price_dollars": "0.5",
               "fee_cost": "0.01", "count": 1}]}}
    out = {f["game"]: f for f in L.parse_fills(raw, "26OCT21")}
    # V2: every fill is a long leg on its side (action only says which book was hit)
    assert out["26OCT21MINMIA"]["side"] == "UNDER" and out["26OCT21MINMIA"]["count"] == 3
    assert abs(out["26OCT21MINMIA"]["entry"] - (0.52 * 2 + 0.9) / 3) < 1e-12
    assert out["26OCT21CHITOR"]["side"] == "OVER" and len(out) == 2


def test_fills_bot_v2_sell_yes_is_no_buy():
    """The bot's IOC NO buy comes back as action=sell, side=no, outcome_side=no, book_side=ask (real fill, 2026-10-22)."""
    raw = {"fills": {"3": [{"action": "sell", "book_side": "ask", "count_fp": "1.00", "fee_cost": "0.017500",
                            "no_price_dollars": "0.5200", "outcome_side": "no", "side": "no",
                            "ticker": "KXNBATOTAL-26OCT22CLEPHI-238", "yes_price_dollars": "0.4800"}]}}
    (b,) = L.parse_fills(raw, "26OCT22")
    assert b["side"] == "UNDER" and b["count"] == 1 and b["entry"] == 0.52 and b["fees"] == 0.0175


def test_sync_off_by_default_in_tests():
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    assert d.sync is False


class FakeResp:
    def __init__(self, j):
        self.j = j

    def raise_for_status(self):
        pass

    def json(self):
        return self.j


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if url.endswith("/milestones"):
            return FakeResp({"milestones": [
                {"id": "m1", "type": "basketball_game", "title": "t", "start_date": "2026-10-21T23:00:00Z",
                 "related_event_tickers": ["KXNBATOTAL-26OCT21MINMIA"]},
                {"id": "m2", "type": "other", "related_event_tickers": ["KXNBATOTAL-26OCT21AAABBB"]}], "cursor": ""})
        return FakeResp({})

    def __getattr__(self, n):
        raise AssertionError("non-GET used: " + n)


def test_only_get_to_kalshi():
    s = FakeSession()
    api = L.Kalshi(s)
    g = L.scan_games(api, date(2026, 10, 21))
    assert list(g) == ["26OCT21MINMIA"] and g["26OCT21MINMIA"]["away"] == "MIN"
    api.get("/markets/X")
    assert s.calls and all(c.startswith(L.BASE) for c in s.calls)


def test_build_no_network():
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession()), logs="/nonexistent")
    d.games = {"26OCT21MINMIA": {"key": "26OCT21MINMIA", "away": "MIN", "home": "MIA", "id": "m1",
                                 "start": "2026-10-21T23:00:00Z"}}
    d.scores = {"26OCT21MINMIA": {"away_points": 55, "home_points": 54, "period": 2, "period_remaining_time": "3:38"}}
    d.fills = [{"ticker": "KXNBATOTAL-26OCT21MINMIA-242", "side": "UNDER", "count": 1.0, "cost": 0.5, "fees": 0.01,
                "entry": 0.5, "game": "26OCT21MINMIA"}]
    d.markets = {"KXNBATOTAL-26OCT21MINMIA-242": {"floor_strike": 241.5, "yes_bid_dollars": "0.60",
                                                  "yes_ask_dollars": "0.70"}}
    b = d.build()["bets"][0]
    assert b["points"] == 241 - 109 and b["badge"] == "BEHIND ON PACE" and b["chance"] == pytest.approx(0.35)


# ---------- extension tests ----------
from datetime import timedelta


def test_implied_total():
    pts = [(230.5, .70, .72), (232.5, .60, .62), (234.5, .40, .42), (236.5, .20, .22)]
    v = L.implied_total(pts)
    assert v == pytest.approx(232.5 + (0.61 - 0.5) / (0.61 - 0.41) * 2)
    assert L.implied_total([(1, .6, .62), (2, .55, .57)]) == "> top strike"
    assert L.implied_total([(1, .4, .42), (2, .3, .32)]) == "< bottom strike"
    # invalid books dropped: crossed, wide spread, zero bid
    assert L.implied_total([(1, .0, .5), (2, .9, .5), (3, .3, .6)]) is None
    pts2 = pts + [(228.5, .1, .9)]  # wide, ignored
    assert L.implied_total(pts2) == pytest.approx(v)


def test_clv_sign():
    assert L.clv_cents("OVER", 0.60, 0.52) == pytest.approx(8)
    assert L.clv_cents("UNDER", 0.60, 0.52) == pytest.approx(-12)
    assert L.clv_cents("UNDER", 0.40, 0.52) == pytest.approx(8)


def test_cashout_and_bestworst():
    assert L.taker_fee(1, 0.5) == 0.0175
    co = L.cashout("UNDER", 2, "0.60", "0.70", 1.0, 0.04)
    assert co["price"] == pytest.approx(0.30)
    assert co["fee"] == L.taker_fee(2, 0.30)
    assert co["net"] == pytest.approx(0.6 - co["fee"])
    assert co["pnl_now"] == pytest.approx(co["net"] - 1.04)
    assert L.cashout("OVER", 1, "0.60", "0.70", 0.5, 0)["price"] == 0.60
    assert L.best_worst(2, 1.0, 0.04) == (pytest.approx(0.96), pytest.approx(-1.04))


def test_ping_and_track_and_order():
    assert L.fmt_et(L.ping_time("2026-10-21T23:00:00Z")) == "5:45 PM"
    assert L.on_track(238.0, 231.8) == "YES (gap 6.2 over)"
    assert L.on_track(230.0, 232.1) == "no (gap 2.1)"
    assert L.on_track(230.0, "> top strike").startswith("n/a")
    assert L.order_line("UNDER", "241.5", 52) == "BUY NO (UNDER 241.5) x1 @ 52c LIMIT"
    assert L.order_line("OVER", "219.5", 51).startswith("BUY YES (OVER")


def test_scoreboard():
    def fl(g, side, cost, fee):
        return {"ticker": "KXNBATOTAL-%s-200" % g, "game": g, "side": side, "count": 1.0, "cost": cost, "fees": fee}
    fills = [fl("26OCT21AAABBB", "OVER", .5, .02), fl("26OCT21CCCDDD", "UNDER", .5, .02),
             fl("26OCT22EEEFFF", "UNDER", .5, .02), fl("26OCT20GGGHHH", "OVER", .5, .02),
             fl("26NOV04GGGHHH", "OVER", .5, .02)]
    res = {fills[0]["ticker"]: "yes", fills[1]["ticker"]: "yes"}
    sb = L.scoreboard(fills, res, [4.0, -2.0])
    assert (sb["won"], sb["lost"], sb["pending"]) == (1, 1, 1)
    assert sb["pnl"] == pytest.approx((1 - .52) + (-.52))
    assert sb["staked"] == pytest.approx(3 * .52)
    assert sb["roi"] == pytest.approx(sb["pnl"] / 1.04)
    assert sb["breakeven"] == pytest.approx(0.52) and sb["win_rate"] == 0.5
    assert sb["avg_clv"] == 1.0


def test_scoreboard_total_record_by_night_side_and_strategy_filter():
    def fl(g, side, cost, fee):
        return {"ticker": "KXNBATOTAL-%s-200" % g, "game": g, "side": side, "count": 1.0, "cost": cost, "fees": fee}
    fills = [fl("26OCT21AAABBB", "UNDER", .5, .02), fl("26OCT21CCCDDD", "OVER", .5, .02),
             fl("26OCT22EEEFFF", "UNDER", .5, .02), fl("26OCT22GGGHHH", "OVER", .5, .02),   # GGGHHH: not a strategy pick
             fl("26OCT23IIIJJJ", "UNDER", .5, .02)]                                          # still open
    res = {fills[0]["ticker"]: "no", fills[1]["ticker"]: "no", fills[2]["ticker"]: "no", fills[3]["ticker"]: "yes"}
    picked = {"2026-10-21|26OCT21AAABBB": {"side": "UNDER"}, "2026-10-21|26OCT21CCCDDD": {"side": "OVER"},
              "2026-10-22|26OCT22EEEFFF": {"side": "UNDER"}, "2026-10-22|26OCT22GGGHHH": {"side": "UNDER"},
              "2026-10-23|26OCT23IIIJJJ": {"side": "UNDER"}}
    sb = L.scoreboard(fills, res, picked=picked)
    assert (sb["won"], sb["lost"], sb["pending"]) == (2, 1, 1)            # GGGHHH's OVER fill is not the picked side
    assert [(x["date"], x["won"], x["lost"], x["pending"]) for x in sb["by_night"]] == [
        ("2026-10-21", 1, 1, 0), ("2026-10-22", 1, 0, 0), ("2026-10-23", 0, 0, 1)]
    assert sb["by_night"][0]["pnl"] == pytest.approx((1 - .52) - .52)
    assert sb["by_side"] == {"OVER": [0, 1], "UNDER": [2, 0]}
    assert sb["pnl"] == pytest.approx(sum(x["pnl"] for x in sb["by_night"]))
    assert L.scoreboard(fills, res)["won"] + L.scoreboard(fills, res)["lost"] == 4    # no filter: every fill counts


def test_formula_vs_final():
    x = L.formula_vs_final(230.0, 235.5, 231, "OVER")
    assert x["closer"] == "formula" and x["pick_won"] is False and x["formula_err"] == 1
    x = L.formula_vs_final(230.0, 235.5, 236, "UNDER")
    assert x["closer"] == "line" and x["pick_won"] is False
    assert L.formula_vs_final(230.0, 235.5, 236, None)["pick_won"] is None


def test_last_close_forms():
    assert L.last_close([{"yes_bid": {"close_dollars": "0.40"}, "yes_ask": {"close_dollars": "0.44"}}]) == (0.40, 0.44)
    assert L.last_close([{"yes_bid": {"close": 40}, "yes_ask": {"close": 44}}]) == (0.40, 0.44)
    assert L.last_close([]) is None


def test_quarter_label():
    q = lambda **k: L.quarter_label(k, "10:00 PM")
    assert q(period=2, period_remaining_time="3:32", status="inprogress", away_points=1) == ("2nd Qtr · 3:32 left", "live")
    assert q(period=2, period_remaining_time="0:00", status="inprogress", away_points=1)[0] == "Halftime"
    assert q(period=3, period_remaining_time="12:00", status="inprogress", last_play="Halftime", away_points=1)[0] == "Halftime"
    assert q(period=1, period_remaining_time="0:00", status="inprogress", away_points=1)[0] == "End of 1st"
    assert q(period=3, period_remaining_time="0:00", status="inprogress", away_points=1)[0] == "End of 3rd"
    assert q(period=5, period_remaining_time="2:10", status="inprogress", away_points=1)[0] == "OT1 · 2:10 left"
    assert q(period=4, status="closed", away_points=1) == ("Final", "final")
    assert q(period=5, status="closed", away_points=1)[0] == "Final/OT"
    assert q(period=0, status="scheduled") == ("Tip 10:00 PM", "pre")
    assert L.played_text("live", 20.37) == "20.4 of 48 min played"


def test_build_extensions():
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    k = "26OCT21MINMIA"
    d.games = {k: {"key": k, "away": "MIN", "home": "MIA", "id": "m1", "start": "2026-10-21T23:00:00Z"}}
    d.scores = {k: {"away_points": 55, "home_points": 54, "period": 2, "period_remaining_time": "3:38",
                    "possession": "home", "last_play": "Official timeout."}}
    d.evmk = {k: [("t1", 230.5, "0.70", "0.72"), ("t2", 232.5, "0.40", "0.42")]}
    d.fills = [{"ticker": "KXNBATOTAL-26OCT21MINMIA-242", "side": "UNDER", "count": 1.0, "cost": 0.5, "fees": 0.01,
                "entry": 0.5, "game": k}]
    d.fills_all = d.fills
    d.cash = {"0": "1.5", "1": "2.0"}
    d.markets = {d.fills[0]["ticker"]: {"floor_strike": 241.5, "yes_bid_dollars": "0.60", "yes_ask_dollars": "0.70"}}
    s = d.build()
    b = s["bets"][0]
    assert b["game_info"]["ball"] == "MIA" and "timeout" in b["game_info"]["last_play"]
    assert "market expects" in b["market_txt"] and b["cashout"] and b["clv_txt"].startswith("pending")
    assert s["cash"]["total"] == 3.5 and s["night"]["open_worst"] == pytest.approx(-0.51)


def test_filter_upcoming():
    up = [{"key": "A"}, {"key": "B"}]
    assert L.filter_upcoming(up, {"A"}) == [{"key": "B"}]


def test_halftime_elapsed():
    d = {"period": 2, "period_remaining_time": "12:00", "status": "halftime", "away_points": 5}
    lab, _ = L.quarter_label(d, "x")
    assert lab == "Halftime" and L.label_elapsed(lab, L.elapsed_minutes(2, "12:00")) == 24.0
    d = {"period": 1, "period_remaining_time": "0:00", "status": "inprogress", "away_points": 5}
    lab, _ = L.quarter_label(d, "x")
    assert lab == "End of 1st" and L.label_elapsed(lab, L.elapsed_minutes(1, "0:00")) == 12.0
    d = {"period": 3, "period_remaining_time": "12:00", "status": "endperiod", "away_points": 5}
    lab, _ = L.quarter_label(d, "x")
    assert lab == "End of 3rd" and L.label_elapsed(lab, 24.0) == 36.0
    assert L.label_elapsed("End of 4th", None) == 48.0
    assert L.label_elapsed("2nd Qtr \u00b7 3:00 left", 21.0) == 21.0


def test_pregame_badge_neutral_and_halftime_pace():
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    k = "26OCT21GSWLAL"
    d.games = {k: {"key": k, "away": "GSW", "home": "LAL", "id": "m", "start": "2026-10-22T02:00:00Z"}}
    d.scores = {}
    d.evmk = {k: [("t1", 232.5, "0.60", "0.62"), ("t2", 234.5, "0.40", "0.42")]}
    d.fills = [{"ticker": "KXNBATOTAL-%s-233" % k, "side": "UNDER", "count": 1.0, "cost": 0.5, "fees": 0.01,
                "entry": 0.5, "game": k}]
    d.fills_all = d.fills
    d.markets = {d.fills[0]["ticker"]: {"floor_strike": 233.5, "yes_bid_dollars": "0.5", "yes_ask_dollars": "0.52"}}
    s = d.build()
    assert s["bets"][0]["badge"] == "PRE-GAME" and "market expects" in s["bets"][0]["market_txt"]
    assert s["upcoming"] == []
    k2 = "26OCT21CHITOR"
    d.games[k2] = {"key": k2, "away": "CHI", "home": "TOR", "id": "m2", "start": "2026-10-22T00:00:00Z"}
    d.scores[k2] = {"away_points": 60, "home_points": 62, "period": 2, "period_remaining_time": "12:00",
                    "status": "halftime"}
    g = [x for x in d.build()["others"] if x["key"] == k2][0]
    assert g["elapsed"] == 24.0 and g["pace"] == pytest.approx(244.0)


def test_page_grid_css():
    assert "grid-template-columns" in L.PAGE and "100vh" in L.PAGE
    assert "clamp(14px, 0.72vw, 20px)" in L.PAGE and "minmax(24rem" in L.PAGE and "minmax(22rem" in L.PAGE
    assert "@media(min-width:1800px)" in L.PAGE and "overflow-y:auto" in L.PAGE
    assert "@media(max-width:1100px)" in L.PAGE and "@media(max-width:700px)" in L.PAGE
    assert "overflow-x:hidden" in L.PAGE


def _order(t, status="resting", rem="1.00", side="no"):
    return {"ticker": t, "side": side, "action": "buy", "status": status, "yes_price_dollars": "0.48",
            "no_price_dollars": "0.52", "remaining_count_fp": rem, "fill_count_fp": "0.00", "created_time": "x"}


def test_parse_resting_orders():
    raw = {"orders": {"0": [_order("KXNBATOTAL-26OCT21GSWLAL-233"),
                            _order("KXNBATOTAL-26OCT21PHXPOR-231", status="executed", rem="0.00"),
                            _order("KXNBATOTAL-26OCT21SACLAC-226", rem="0.00"),
                            _order("KXNBAGAME-26OCT21GSWLAL")]}}
    r = L.parse_resting_orders(raw, "26OCT21")
    assert len(r) == 1 and r[0]["game"] == "26OCT21GSWLAL" and r[0]["side"] == "UNDER" and r[0]["price"] == 0.52
    assert L.waiting_games(r, {"26OCT21GSWLAL"}) == set()
    assert L.waiting_games(r, set()) == {"26OCT21GSWLAL"}


def _dash_with(resting, fills):
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    k = "26OCT21GSWLAL"
    d.games = {k: {"key": k, "away": "GSW", "home": "LAL", "id": "m", "start": "2026-10-22T02:00:00Z"}}
    d.scores = {}
    d.resting, d.fills, d.fills_all = resting, fills, fills
    d.markets = {"KXNBATOTAL-%s-233" % k: {"floor_strike": 233.5, "yes_bid_dollars": "0.5", "yes_ask_dollars": "0.52"}}
    return d, k


def test_resting_order_waiting_card():
    raw = {"orders": {"0": [_order("KXNBATOTAL-26OCT21GSWLAL-233")]}}
    d, k = _dash_with(L.parse_resting_orders(raw, "26OCT21"), [])
    s = d.build()
    assert s["bets"] == [] and s["upcoming"] == [] and s["others"] == []
    w = s["waiting"][0]
    assert w["badge"] == "ORDER WAITING" and w["strike"] == 233.5 and "cancel by 9:45 PM" in w["note"]
    assert s["totals"]["n"] == 0 and s["totals"]["cost"] == 0 and s["night"]["open_worst"] == 0
    assert s["scoreboard"]["staked"] == 0


def test_fill_shows_normal_card_not_waiting():
    fills = [{"ticker": "KXNBATOTAL-26OCT21GSWLAL-233", "side": "UNDER", "count": 1.0, "cost": 0.5, "fees": 0.01,
              "entry": 0.5, "game": "26OCT21GSWLAL"}]
    raw = {"orders": {"0": [_order("KXNBATOTAL-26OCT21GSWLAL-233")]}}
    d, k = _dash_with(L.parse_resting_orders(raw, "26OCT21"), fills)
    s = d.build()
    assert len(s["bets"]) == 1 and s["waiting"] == [] and s["upcoming"] == []
    raw2 = {"orders": {"0": [_order("KXNBATOTAL-26OCT21GSWLAL-233", status="executed", rem="0")]}}
    d2, _ = _dash_with(L.parse_resting_orders(raw2, "26OCT21"), fills)
    assert d2.build()["waiting"] == []


def test_par_helpers():
    assert L.par_now(240.0, 24.0) == 120.0
    assert L.par_now(240.0, 51.0) == 240.0
    assert L.par_now(240.0, 48.0) == 240.0
    assert L.par_now(240.0, None) is None
    assert L.par_now(None, 10) is None
    assert L.vs_par(122, 120.0) == 2.0 and L.vs_par(5, None) is None


def test_par_text_wording():
    assert L.par_text("OVER", 3) == ("3 pts AHEAD of the over pace", "g")
    assert L.par_text("OVER", -5) == ("5 pts BEHIND the over pace", "r")
    assert L.par_text("UNDER", -4) == ("4 pts UNDER the line's pace", "g")
    assert L.par_text("UNDER", 6) == ("6 pts OVER the line's pace", "r")
    assert L.par_text("OVER", 0.8) == ("right on pace", "n")
    assert L.par_text("UNDER", -1.0) == ("right on pace", "n")
    assert L.par_text("UNDER", None) == ("", "n")


def test_build_par_fields():
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    k = "26OCT21CHITOR"
    d.games = {k: {"key": k, "away": "CHI", "home": "TOR", "id": "m", "start": "2026-10-21T23:00:00Z"}}
    d.scores = {k: {"away_points": 60, "home_points": 62, "period": 2, "period_remaining_time": "12:00",
                    "status": "halftime"}}
    d.fills = [{"ticker": "KXNBATOTAL-%s-237" % k, "side": "UNDER", "count": 1.0, "cost": 0.5, "fees": 0.0,
                "entry": 0.5, "game": k}]
    d.fills_all = d.fills
    d.markets = {d.fills[0]["ticker"]: {"floor_strike": 237.5}}
    b = d.build()["bets"][0]
    assert b["par"] == pytest.approx(118.75) and b["vs_par"] == pytest.approx(3.25)
    assert b["par_text"] == "3 pts OVER the line's pace" and b["par_color"] == "r"
    assert "pace" in b["game_info"] and b["game_info"]["pace"] is not None  # naive pace kept


def _cd(**k):
    d = {"status": "inprogress", "period": 2, "period_remaining_time": "6:43", "last_play": "T.Johnson makes a two-point shot.",
         "last_event_is_timeout": False}
    d.update(k)
    return d


def test_clock_running():
    assert L.clock_running(_cd()) is True
    assert L.clock_running(_cd(last_play="MIN defensive rebound.")) is True
    for w in L.STOP_WORDS:
        assert L.clock_running(_cd(last_play="Something %s here" % w.upper())) is False, w
    assert L.clock_running(_cd(last_event_is_timeout=True)) is False
    assert L.clock_running(_cd(period_remaining_time="0:00")) is False
    assert L.clock_running(_cd(status="closed")) is False
    # made basket in last 2 min of Q4 stops the clock; same play in Q2 or earlier than 2:00 does not
    assert L.clock_running(_cd(period=4, period_remaining_time="1:30")) is False
    assert L.clock_running(_cd(period=4, period_remaining_time="1:30", last_play="defensive rebound")) is True
    assert L.clock_running(_cd(period=4, period_remaining_time="5:00")) is True
    assert L.clock_running(_cd(period=2, period_remaining_time="1:30")) is True
    assert L.clock_running(_cd(period=5, period_remaining_time="1:00")) is False


def test_choose_anchor():
    now = 1000000.0
    assert L.choose_anchor({"last_event_created_ts": now - 4}, now, 5.0) == now - 4
    assert L.choose_anchor({"last_event_created_ts": now + 60}, now, 5.0) == 5.0   # future
    assert L.choose_anchor({"last_event_created_ts": now - 3600}, now, 5.0) == 5.0  # too old
    assert L.choose_anchor({}, now, 7.0) == 7.0


def test_poll_interval():
    assert L.poll_interval("live") == 2.0 and L.poll_interval("pre") == 30.0
    assert L.poll_interval("final") is None and L.poll_interval(None) == 2.0


def test_429_backoff():
    class R429(FakeResp):
        status_code = 429

    class S:
        def __init__(self):
            self.n = 0

        def get(self, url, params=None, timeout=None):
            self.n += 1
            return R429({}) if self.n == 1 else FakeResp({"ok": 1})

    api = L.Kalshi(S(), min_interval=0.01, backoff_sleep=0)
    assert api.cur_interval() == 0.01
    with pytest.raises(L.requests.HTTPError):
        api.get("/x")
    assert api.cur_interval() == 0.02  # doubled during the 60 s slow window
    assert api.get("/x") == {"ok": 1}


def test_state_clock_fields():
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    k = "26OCT21CHITOR"
    d.games = {k: {"key": k, "away": "CHI", "home": "TOR", "id": "m", "start": "2026-10-21T23:00:00Z"}}
    import time as _t
    now = _t.time()
    d.scores = {k: dict(_cd(away_points=10, home_points=12), last_event_created_ts=now - 3)}
    d.fetched = {k: now - 1}
    s = d.build()
    g = s["others"][0]
    assert g["clock_secs"] == pytest.approx(403) and g["running"] is True and g["clock_prefix"] == "2nd Qtr"
    assert g["anchor_ts"] == pytest.approx(now - 3) and 0 <= g["fetch_age"] < 5 and "server_ts" in s
    assert g["elapsed"] == pytest.approx(12 + (12 - 6 - 43 / 60), abs=0.01)  # real clock, not the estimate


def test_page_hides_no_bet_games():
    assert "OTHER GAMES" not in L.PAGE and "id=oth" not in L.PAGE
    assert "s.finished.filter(g=>g.bet)" in L.PAGE
    assert "id=upPanel style=\"display:none\"" in L.PAGE and "78fr 22fr" in L.PAGE


def test_api_keeps_others_and_finished_fields():
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    k = "26OCT21CHITOR"
    d.games = {k: {"key": k, "away": "CHI", "home": "TOR", "id": "m", "start": "2026-10-21T23:00:00Z"}}
    d.scores = {k: {"away_points": 100, "home_points": 110, "period": 4, "status": "closed", "winner": "home"}}
    s = d.build()
    assert "others" in s and len(s["finished"]) == 1 and s["finished"][0]["bet"] is False


def test_static_text_only_in_info_popover():
    page = L.PAGE
    i = page.index("<span class=pop id=infoPop>")
    j = page.index("</span></span>", i)
    popover, rest = page[i:j], page[:i] + page[j:]
    for phrase in ("Final decision is made at the ping", "hold to settlement", "Small sample", "Read-only",
                   "Scores: Kalshi live data API"):
        assert phrase in popover, phrase
    low = rest.lower()
    for phrase in ("final decision is made", "info only", "hold to settlement", "small sample", "read-only",
                   "no pace yet", "data age", "pre-registered", "kalshi live data api"):
        assert phrase not in low, phrase


def test_page_has_visual_elements():
    for tok in ("<svg class=ring", "class=\"sd ", "stroke-dasharray", "class=rng", "class=wr", "class=more", "▲", "▼"):
        assert tok in L.PAGE, tok


def test_decide_outcome():
    assert L.decide_outcome("OVER", "no", "LOST", True) == (False, False)
    assert L.decide_outcome("UNDER", "no", "x", False) == (True, False)
    assert L.decide_outcome("UNDER", "", "WON", True) == (True, True)
    assert L.decide_outcome("OVER", "", "LOST", True) == (False, True)
    assert L.decide_outcome("OVER", "", "WON (locked)", False) == (None, False)  # live game: no outcome yet


def _final_dash(side, strike, a, h, res, bid="0.50", ask="0.52"):
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    k = "26OCT21CHABKN"
    d.games = {k: {"key": k, "away": "CHA", "home": "BKN", "id": "m", "start": "2026-10-21T23:00:00Z"}}
    d.scores = {k: {"away_points": a, "home_points": h, "period": 4, "status": "closed", "winner": "away"}}
    t = "KXNBATOTAL-%s-%d" % (k, int(strike))
    d.fills = d.fills_all = [{"ticker": t, "side": side, "count": 1.0, "cost": 0.5, "fees": 0.01, "entry": 0.5, "game": k}]
    d.markets = {t: {"floor_strike": strike, "yes_bid_dollars": bid, "yes_ask_dollars": ask, "result": res}}
    return d


def test_final_bet_chance_from_outcome():
    b = _final_dash("OVER", 219.5, 110, 103, "no").build()["bets"][0]   # 213 vs over 219.5, settled no
    assert b["chance"] == 0.0 and b["badge"] == "LOST" and b["unrealized"] == pytest.approx(-0.51)
    s = _final_dash("UNDER", 241.5, 120, 117, "").build()               # 237 vs under, not yet settled
    b = s["bets"][0]
    assert b["chance"] == 1.0 and b["badge"] == "WON" and b["awaiting_settlement"] is True
    assert b["unrealized"] == pytest.approx(0.49) and s["totals"]["won"] == 1
    assert s["scoreboard"]["won"] == 1 and s["scoreboard"]["awaiting"] == 1  # scoreboard now synced with finished panel


def test_wide_book_flag_and_ring():
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    k = "26OCT21UTAMEM"
    d.games = {k: {"key": k, "away": "UTA", "home": "MEM", "id": "m", "start": "2026-10-21T23:00:00Z"}}
    d.scores = {k: {"away_points": 100, "home_points": 100, "period": 4, "period_remaining_time": "5:48", "status": "inprogress"}}
    t = "KXNBATOTAL-%s-242" % k
    d.fills = d.fills_all = [{"ticker": t, "side": "OVER", "count": 1.0, "cost": 0.5, "fees": 0.0, "entry": 0.5, "game": k}]
    d.markets = {t: {"floor_strike": 242.5, "yes_bid_dollars": "0.05", "yes_ask_dollars": "0.40"}}
    assert d.build()["bets"][0]["chance_wide"] is True
    d.markets[t]["yes_ask_dollars"] = "0.10"
    assert d.build()["bets"][0]["chance_wide"] is False
    assert "wide?'~':''" in L.PAGE


def test_page_moves_finished_bets_to_right_panel():
    assert "const isFin=b=>b.game_info.phase=='final'||b.result=='yes'||b.result=='no'" in L.PAGE
    assert "s.bets.filter(b=>!isFin(b)).map(bet)" in L.PAGE        # cards: not-final only
    assert "s.bets.filter(isFin)" in L.PAGE and "fb.map(fin)" in L.PAGE  # finished list
    assert "fb.reduce((a,b)=>a+(b.unrealized||0),0)" in L.PAGE and "class=frow1" in L.PAGE  # summary + rows
    assert "grid-auto-rows:min-content" in L.PAGE and "minmax(22rem,1fr)" in L.PAGE


def test_api_still_lists_finished_bets():
    b = _final_dash("OVER", 219.5, 110, 103, "no").build()
    assert len(b["bets"]) == 1 and b["bets"][0]["game_info"]["phase"] == "final"


def test_bar_window():
    assert L.bar_window([150, 160, 242.5, 229.7]) == (0.0, 300.0)   # fixed scale, ignores the values
    assert L.bar_pos(150) == 50.0 and L.bar_pos(0) == 0.0
    assert L.bar_pos(300) == 100.0 and L.bar_pos(350) == 100.0 and L.bar_pos(-5) == 0.0   # clamped


def test_page_bar_has_no_text_labels():
    for tok in ("'line '+line", "'par '+", "'mkt '+", "class=ml", ".ml{"):
        assert tok not in L.PAGE, tok
    for tok in ("class=tri", "class=mk", "class=fm", "gl-line", "const SCALE=300"):
        assert tok in L.PAGE, tok


def test_fmt_money():
    assert L.fmt_money(-0.0007) == "$0.00" and L.fmt_money(0.4968) == "+$0.50"
    assert L.fmt_money(-0.5044) == "\u2212$0.50" and L.fmt_money(None) == "\u2013"
    assert "Math.abs(x)<0.005?'$0.00'" in L.PAGE and "Math.abs(v)<0.005?'n'" in L.PAGE


def test_scoreboard_syncs_with_finished_panel():
    d = _final_dash("OVER", 219.5, 110, 103, "")           # final 213, OVER 219.5 -> lost, unsettled
    s = d.build()
    b = s["bets"][0]
    sb = s["scoreboard"]
    assert b["awaiting_settlement"] and sb["awaiting"] == 1 and sb["pending"] == 0
    assert (sb["won"], sb["lost"]) == (s["totals"]["won"], s["totals"]["lost"]) == (0, 1)
    assert sb["pnl"] == pytest.approx(b["unrealized"]) == pytest.approx(-0.51)
    # official result arrives (same outcome): numbers unchanged, awaiting drops to 0
    d2 = _final_dash("OVER", 219.5, 110, 103, "no")
    s2 = d2.build()
    assert s2["scoreboard"]["awaiting"] == 0 and (s2["scoreboard"]["won"], s2["scoreboard"]["lost"]) == (0, 1)
    assert s2["scoreboard"]["pnl"] == pytest.approx(sb["pnl"])
    # official result disagreeing with the score-based outcome wins
    d3 = _final_dash("OVER", 219.5, 110, 103, "yes")
    assert d3.build()["scoreboard"]["won"] == 1


def test_markets_poll_skips_settled():
    class S2(FakeSession):
        def get(self, url, params=None, timeout=None):
            self.calls.append(url)
            return FakeResp({"market": {"result": "no"}})
    ss = S2()
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(ss, 0), logs="/nonexistent")
    d.fills = [{"ticker": "KXNBATOTAL-26OCT21AAABBB-200", "game": "26OCT21AAABBB"}]
    d.do_markets()
    assert len(ss.calls) == 1 and d.results
    d.do_markets()
    assert len(ss.calls) == 1   # settled: not polled again


def test_build_id_in_state_and_page():
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    st = d.build()
    assert st["build"] == L.BUILD_ID and len(L.BUILD_ID) == 12
    html = L.render_page()
    assert "const BUILD='%s'" % L.BUILD_ID in html and "__BUILD__" not in html
    assert "s.build&&s.build!==BUILD" in L.PAGE and "location.reload()" in L.PAGE
    assert "sessionStorage" in L.PAGE and "30000" in L.PAGE


def test_page_bar_fixed_scale_ticks_and_formula():
    for tok in ("[0,100,200,300]", "left:33.3%", "left:66.7%", "class=fm", "◆", "class=\"tv fv\"", "Formula",
                "gauge(g,b.strike,b.par,pre,b)"):
        assert tok in L.PAGE, tok
    assert "barWin" not in L.PAGE


def test_nba_night():
    from datetime import datetime as dt
    E = L.ET
    assert L.nba_night(dt(2026, 10, 22, 0, 7, tzinfo=E)) == date(2026, 10, 21)
    assert L.nba_night(dt(2026, 10, 22, 5, 59, tzinfo=E)) == date(2026, 10, 21)
    assert L.nba_night(dt(2026, 10, 22, 6, 0, tzinfo=E)) == date(2026, 10, 22)
    assert L.nba_night(dt(2026, 10, 22, 23, 0, tzinfo=E)) == date(2026, 10, 22)
    assert "nba_night(datetime.now(ET))" in open(L.__file__, encoding="utf-8").read()


def test_page_total_row_bubble_no_par_no_bracket():
    for tok in ('class="sr tot"', ">TOTAL</small>", "class=bub", "function bubble(g,line,P)"):
        assert tok in L.PAGE, tok
    for tok in ("class=pk", ".pk{", "gl-par", "bubbleBr", "class=\"br ", "class=\"bl ", ".br{", ".bl{",
                "'over by '", "'needs '", "'room '", "dashed #f59e0b"):
        assert tok not in L.PAGE, tok
    assert "Total now" not in L.PAGE and "gauge(g,b.strike,b.par,pre,b)" in L.PAGE
    assert "vs pace" in L.PAGE   # the vs-pace chip stays


def test_points_left_tile_and_clean_bet_line():
    for tok in ("Points left", "'to go over'", "'until it goes over'", "'went over'", "'✓'", "'✗'", "class=\"tile big\"",
                "const ml=b.side+' '+b.strike;"):
        assert tok in L.PAGE, tok
    assert "can allow" not in L.PAGE and "more points" not in L.PAGE
    assert "pT}${lT}${mT}${fT}" in L.PAGE      # POINTS LEFT is the first tile


def _traj_dash(tmp_path):
    d = L.Dash(date(2026, 10, 21), api=L.Kalshi(FakeSession(), 0), logs=str(tmp_path))
    k1, k2 = "26OCT21CHITOR", "26OCT21PHXPOR"
    d.games = {k1: {"key": k1, "away": "CHI", "home": "TOR", "id": "a", "start": "2026-10-21T23:00:00Z"},
               k2: {"key": k2, "away": "PHX", "home": "POR", "id": "b", "start": "2026-10-21T23:00:00Z"}}
    live = {"away_points": 60, "home_points": 62, "period": 2, "period_remaining_time": "6:43", "status": "inprogress",
            "last_play": "defensive rebound"}
    d.scores = {k1: dict(live), k2: dict(live)}
    t1 = "KXNBATOTAL-%s-237" % k1
    d.fills = d.fills_all = [{"ticker": t1, "side": "UNDER", "count": 1.0, "cost": 0.5, "fees": 0.0, "entry": 0.5, "game": k1}]
    d.markets = {t1: {"floor_strike": 237.5, "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.44"}}
    return d, k1, k2


def _rows(tmp_path):
    import json as _j
    p = tmp_path / "live_dashboard" / "2026-10-21.jsonl"
    return [_j.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_trajectory_logger(tmp_path):
    d, k1, k2 = _traj_dash(tmp_path)
    d.do_traj(now=1000.0)
    rows = _rows(tmp_path)
    assert len(rows) == 1 and rows[0]["game"] == k1            # no-bet game skipped
    r = rows[0]
    for f in ("ts_utc", "game", "ticker", "side", "strike", "period", "clock", "minutes_played", "away_pts",
              "home_pts", "total", "par", "vs_par", "points_left", "market_implied_total", "chance", "yes_bid",
              "yes_ask", "proj", "phase"):
        assert f in r, f
    assert r["clock"] == "6:43" and r["period"] == 2 and r["total"] == 122 and r["points_left"] == 115
    assert r["yes_bid"] == 0.40 and r["chance"] == pytest.approx(0.58) and r["phase"] == "live"
    d.do_traj(now=1010.0)                                       # throttled (30 s)
    assert len(_rows(tmp_path)) == 1
    d.do_traj(now=1031.0)
    assert len(_rows(tmp_path)) == 2
    d.scores[k1] = {"away_points": 100, "home_points": 110, "period": 4, "status": "closed", "winner": "home"}
    d.do_traj(now=1040.0)                                       # turns final: one final row
    d.do_traj(now=1100.0)
    rows = _rows(tmp_path)
    assert [r["phase"] for r in rows] == ["live", "live", "final"]
    # a restarted process does not re-log the final row
    d2, _, _ = _traj_dash(tmp_path)
    d2.scores[k1] = d.scores[k1]
    d2.do_traj(now=2000.0)
    assert len(_rows(tmp_path)) == 3


def test_holder_rollover():
    from datetime import datetime as _dt
    made = []

    class FakeDash:
        def __init__(self, d):
            self.d, self.started, self.alive = d, False, True
            made.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.alive = False

    first = FakeDash(date(2026, 10, 21))
    h = L.Holder(first, fixed=False, make=FakeDash)
    # 2026-10-22 05:59 ET still belongs to the 10-21 night: no swap
    assert h.rollover(_dt(2026, 10, 22, 5, 59, tzinfo=L.ET)) is False and h.current is first
    # 06:01 ET: new night 10-22, new Dash started, old stopped
    assert h.rollover(_dt(2026, 10, 22, 6, 1, tzinfo=L.ET)) is True
    assert h.current.d == date(2026, 10, 22) and h.current.started and not first.alive
    # fixed --date never rolls over
    h2 = L.Holder(FakeDash(date(2026, 10, 21)), fixed=True, make=FakeDash)
    assert h2.rollover(_dt(2026, 10, 25, 12, 0, tzinfo=L.ET)) is False


def test_bot_status(tmp_path):
    import json as _j, time as _t
    from datetime import datetime as _dt, timezone as _tz
    assert L.bot_status(str(tmp_path), _t.time()) is None
    (tmp_path / "early_overs_bot.heartbeat").write_text(_j.dumps(
        {"ts_utc": _dt.now(_tz.utc).isoformat(), "live": True, "placing": True}))
    b = L.bot_status(str(tmp_path), _t.time())
    assert b["live"] and b["placing"] and 0 <= b["age_s"] <= 5


def test_watch_and_expand_controls():
    page = L.PAGE
    assert "https://www.nba.com/games?date=${DATE}" in page and "target=_blank rel=noopener" in page
    assert "function xp(k)" in page and "id=bd" in page and "Escape" in page and ".card.big{" in page


# ---------- SCORE MODEL panel (research #59 forward tracking; file read only) ----------
def _sm_summary(night="2026-10-22"):
    return {"updated_utc": "2026-10-22T20:00:00Z",
            "metrics": {"n_games": 20, "E1": {"total_mae": 14.25, "pct_within_10": 0.45}, "B1": {"total_mae": 15.31, "pct_within_10": 0.4}},
            "tonight": {"night": night, "rows": [
                {"key": "26OCT22CLEPHI", "away": "CLE", "home": "PHI", "locked": True, "e1_away": 115.6, "e1_home": 111.2, "e1_total": 226.8,
                 "b1_total": 229.1},
                {"key": "26OCT22DENOKC", "away": "DEN", "home": "OKC", "locked": True, "e1_away": 114.0, "e1_home": 119.0, "e1_total": 233.0,
                 "b1_total": 232.0},
                {"key": "26OCT22LALGSW", "away": "LAL", "home": "GSW", "locked": True, "e1_away": 110.0, "e1_home": 110.0, "e1_total": 220.0,
                 "b1_total": 221.0},
                {"key": "26OCT22BOSNYK", "away": "BOS", "home": "NYK", "locked": False, "reason_not_locked": "started"}]}}


def test_score_model_panel_rows_and_cum():
    gout = {"26OCT22CLEPHI": {"phase": "final", "total": 224}, "26OCT22DENOKC": {"phase": "live", "total": 120},
            "26OCT22LALGSW": {"phase": "final", "total": 245}}
    p = L.score_model_panel(_sm_summary(), gout, "2026-10-22")
    rows = {r["key"]: r for r in p["rows"]}
    assert len(rows) == 3 and "26OCT22BOSNYK" not in rows                     # unlocked games are not shown
    a = rows["26OCT22CLEPHI"]
    assert (a["model_away"], a["model_home"], a["model_total"], a["formula_total"]) == (116, 111, 227, 229)
    assert a["state"] == "final" and a["total"] == 224 and a["off"] == pytest.approx(2.8) and a["ok"] is True
    assert rows["26OCT22DENOKC"]["state"] == "live" and rows["26OCT22DENOKC"]["total"] == 120 and rows["26OCT22DENOKC"]["off"] is None
    b = rows["26OCT22LALGSW"]
    assert b["off"] == pytest.approx(25.0) and b["ok"] is False
    assert p["cum"] == {"n": 20, "model_mae": 14.25, "formula_mae": 15.31, "model_within10": 0.45, "formula_within10": 0.4}


def test_score_model_panel_uses_summary_final_when_dashboard_has_no_score():
    s = _sm_summary()
    s["tonight"]["rows"][0]["final_total"] = 230
    p = L.score_model_panel(s, {}, "2026-10-22")
    r = [x for x in p["rows"] if x["key"] == "26OCT22CLEPHI"][0]
    assert r["state"] == "final" and r["total"] == 230 and r["off"] == pytest.approx(3.2)
    assert [x["state"] for x in p["rows"] if x["key"] != "26OCT22CLEPHI"] == ["pre", "pre"]


def test_score_model_panel_other_night_or_missing():
    p = L.score_model_panel(_sm_summary("2026-10-21"), {}, "2026-10-22")
    assert p["rows"] == [] and p["cum"]["n"] == 20                            # cumulative line still shown
    assert L.score_model_panel(None, {}, "2026-10-22") is None
    assert L.sm_load("/nonexistent/summary.json") is None
    empty = L.score_model_panel({"metrics": {"n_games": 0, "E1": None, "B1": None}}, {}, "2026-10-22")
    assert empty["cum"]["n"] == 0 and empty["cum"]["model_mae"] is None


def test_dash_reads_summary_file_at_most_every_60s(tmp_path):
    import json as _j
    p = tmp_path / "summary.json"
    p.write_text(_j.dumps(_sm_summary()))
    d = L.Dash(date(2026, 10, 22), api=L.Kalshi(FakeSession(), 0), logs="/nonexistent")
    d.sm_path = str(p)
    assert d.sm_summary(now=1000.0)["metrics"]["n_games"] == 20
    p.write_text(_j.dumps({"metrics": {"n_games": 99}}))
    assert d.sm_summary(now=1030.0)["metrics"]["n_games"] == 20               # cached
    assert d.sm_summary(now=1061.0)["metrics"]["n_games"] == 99               # refreshed after 60 s
    k = "26OCT22CLEPHI"
    d.games = {k: {"key": k, "away": "CLE", "home": "PHI", "id": "m", "start": "2026-10-22T23:00:00Z"}}
    d.scores = {k: {"away_points": 100, "home_points": 124, "period": 4, "status": "closed", "winner": "home"}}
    p.write_text(_j.dumps(_sm_summary()))
    d._sm = (0.0, None)
    s = d.build()
    assert s["score_model"]["rows"][0]["state"] == "final" and s["score_model"]["rows"][0]["total"] == 224
    d.sm_path = str(tmp_path / "missing.json")
    d._sm = (0.0, None)
    assert d.build()["score_model"] is None


def test_page_has_score_model_panel_and_text_only_in_popover():
    page = L.PAGE
    assert "id=smPanel" in page and "id=sm class=card" in page and "function smRow" in page and "s.score_model" in page
    assert "model ${r.model_away}–${r.model_home} (${r.model_total}) · your formula" in page
    assert "Model avg off" in page and "within 10:" in page
    i = page.index("<span class=pop id=infoPop>")
    j = page.index("</span></span>", i)
    assert "Score model:" in page[i:j] and "Score model:" not in page[:i] + page[j:]


# ---------------- trade wire panel (reads logs/trades/summary.json) ----------------
def _tw_summary():
    return {"updated_utc": "2026-10-23T15:00:00Z", "feeds": {"a": {"ok": True}, "b": {"ok": False, "error": "x"}},
            "alerts": [{"ts_utc": "2026-10-23T14:00:00Z", "type": "TRADE", "headline": "<img src=x onerror=1> A trade", "tier": "star",
                        "veteran": True, "status": "confirmed by roster data"}]
            + [{"ts_utc": "2026-10-23T%02d:00:00Z" % h, "type": "WEIRD", "headline": "h%d" % h, "tier": "??", "status": "reported"} for h in range(1, 12)]}


def test_trade_wire_panel_latest_eight_and_sanitised():
    w = L.trade_wire_panel(_tw_summary())
    assert len(w["alerts"]) == 8 and w["alerts"][0]["headline"].startswith("<img") and w["alerts"][0]["confirmed"] is True
    assert w["alerts"][0]["veteran"] is True and w["alerts"][0]["tier"] == "star"
    assert all(a["type"] in L.TW_TYPES and a["tier"] in L.TW_TIERS for a in w["alerts"])     # unknown values coerced
    assert w["feed_errors"] == ["b"]
    assert w["alerts"][1]["t"].startswith("Oct 23")
    for bad in (None, {}, {"alerts": "x"}, {"alerts": [None, {"headline": 3}]}):
        w2 = L.trade_wire_panel(bad)
        assert w2 is None or w2["alerts"] == []


def test_trade_wire_dash_reads_file_every_60s(tmp_path):
    import json
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(_tw_summary()))
    d = L.Dash(date(2026, 10, 23), api=object(), logs=str(tmp_path))
    d.tw_path = str(p)
    assert len(d.tw_panel(now=1000.0)["alerts"]) == 8
    p.write_text(json.dumps({"alerts": []}))
    assert len(d.tw_panel(now=1030.0)["alerts"]) == 8          # cached
    assert d.tw_panel(now=1061.0)["alerts"] == []              # re-read after 60 s
    p.unlink()
    assert d.tw_panel(now=1200.0) is None                      # missing file -> hidden


def test_page_trade_wire_escaped_and_text_only_in_popover():
    page = L.PAGE
    assert "id=twPanel" in page and "id=tw class=card" in page and "s.trade_wire" in page
    assert "${esc(a.headline)}" in page and "✓" in page
    i = page.index("<span class=pop id=infoPop>")
    j = page.index("</span></span>", i)
    assert "Trade wire:" in page[i:j] and "Trade wire:" not in page[:i] + page[j:]


# ---------------- injury wire panel (reads logs/injuries/summary.json) ----------------
def _iw_summary():
    off = {"source": "OFFICIAL", "official": True, "type": "STATUS_CHANGE", "ts_utc": "2026-10-23T22:00:00Z", "team": "LAL", "opponent": "DAL",
           "player": "<b>LeBron</b>", "old_status": "Questionable", "new_status": "Out", "tier": "star", "veteran": True, "late": True,
           "bot_game": True, "headline": "x"}
    news = {"source": "NEWS", "official": False, "type": "NEWS_INJURY", "ts_utc": "2026-10-23T23:00:00Z", "team": "DAL", "player": "Luka",
            "new_status": "WEIRD", "tier": "??", "headline": "<img src=x onerror=1> Luka ruled out"}
    more = [dict(off, ts_utc="2026-10-23T%02d:00:00Z" % h, type="WEIRD", source="OFFICIAL") for h in range(1, 12)]
    return {"updated_utc": "2026-10-23T23:05:00Z", "feeds": {"a": {"ok": True}, "b": {"ok": False, "error": "x"}},
            "last_official": {"creation_et": "2026-10-23 18:30 ET"}, "alerts": [off, news] + more}


def test_injury_wire_panel_latest_ten_and_sanitised():
    w = L.injury_wire_panel(_iw_summary())
    assert len(w["alerts"]) == 10 and w["alerts"][0]["official"] is False and w["alerts"][0]["headline"].startswith("<img")
    assert w["alerts"][0]["new"] is None and w["alerts"][0]["tier"] == "none"                    # unknown values coerced
    o = [a for a in w["alerts"] if a["official"]][0]
    assert o["late"] is True and o["bot"] is True and o["veteran"] is True and o["tier"] == "star" and o["headline"] == ""
    assert all(a["type"] in L.IW_TYPES for a in w["alerts"]) and w["feed_errors"] == ["b"] and w["last_report"].startswith("2026-10-23")
    # an item merely claiming source OFFICIAL without official=True is shown as NEWS
    assert L.injury_wire_panel({"alerts": [{"type": "NEW", "source": "OFFICIAL", "headline": "h"}]})["alerts"][0]["official"] is False
    for bad in (None, {}, {"alerts": "x"}, {"alerts": [None, {"headline": 3}]}):
        w2 = L.injury_wire_panel(bad)
        assert w2 is None or w2["alerts"] == []


def test_injury_wire_dash_reads_file_every_60s(tmp_path):
    import json
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(_iw_summary()))
    d = L.Dash(date(2026, 10, 23), api=object(), logs=str(tmp_path))
    d.iw_path = str(p)
    assert len(d.iw_panel(now=1000.0)["alerts"]) == 10
    p.write_text(json.dumps({"alerts": []}))
    assert len(d.iw_panel(now=1030.0)["alerts"]) == 10          # cached
    assert d.iw_panel(now=1061.0)["alerts"] == []               # re-read after 60 s
    p.unlink()
    assert d.iw_panel(now=1200.0) is None                       # missing file -> hidden


def test_page_injury_wire_escaped_and_text_only_in_popover():
    page = L.PAGE
    assert "id=iwPanel" in page and "id=iw class=card" in page and "s.injury_wire" in page
    assert "${esc(a.headline)}" in page and "'OFFICIAL'" in page and "'BOT GAME'" in page and "'LATE'" in page
    i = page.index("<span class=pop id=infoPop>")
    j = page.index("</span></span>", i)
    assert "Injury wire:" in page[i:j] and "Injury wire:" not in page[:i] + page[j:]


def _pl_summary():
    rules = {"N1": {"sport": "NBA", "label": "F42 gap >= 4", "control": False, "n": 3, "won": 2, "lost": 1, "open": 0, "net": 0.5, "mean_net": 0.1666667},
             "N5": {"sport": "NBA", "label": "always UNDER", "control": True, "n": 2, "won": 0, "lost": 0, "open": 2, "net": 0.0, "mean_net": None},
             "bad": "x"}
    return {"rules": rules, "order": ["N1", "N5", "bad", "gone"], "bets_needed": 100, "updated_utc": "2026-10-24T01:00:00Z"}


def test_paper_league_panel_rows_and_note():
    p = L.paper_league_panel(_pl_summary())
    assert [r["id"] for r in p["rows"]] == ["N1", "N5"]
    a, b = p["rows"]
    assert (a["won"], a["lost"], a["open"], a["n"], a["need"], a["control"]) == (2, 1, 0, 3, 100, False)
    assert a["mean_c"] == 16.7 and a["net"] == 0.5 and b["mean_c"] is None and b["control"] is True and b["open"] == 2
    assert p["note"].startswith("Verdict only after 4 weeks and 100+ bets per rule")
    for bad in (None, {}, {"rules": []}, "x"):
        assert L.paper_league_panel(bad) is None


def test_paper_league_dash_reads_file_every_60s(tmp_path):
    import json
    f = tmp_path / "summary.json"
    f.write_text(json.dumps(_pl_summary()))
    d = L.Dash(date(2026, 10, 23), api=object(), logs=str(tmp_path))
    d.pl_path = str(f)
    assert len(d.pl_panel(now=1000.0)["rows"]) == 2
    f.write_text(json.dumps({"rules": {}, "order": []}))
    assert len(d.pl_panel(now=1030.0)["rows"]) == 2             # cached
    assert d.pl_panel(now=1061.0)["rows"] == []
    f.unlink()
    assert d.pl_panel(now=1200.0) is None                       # missing -> hidden


def test_page_paper_league_panel_present_and_escaped():
    page = L.PAGE
    assert "PAPER LEAGUE · NO RULE HAS PASSED" in page and "id=plPanel" in page and "s.paper_league" in page
    assert "${esc(r.id)}" in page or "esc(r.id)" in page and "esc(p.note)" in page
