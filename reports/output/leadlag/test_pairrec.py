"""Offline tests for pairrec.py (no network, no credentials)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pairrec as pr  # noqa: E402


def kgame():
    return dict(
        game="26OCT20PHINYK", away="PHI", home="NYK", events={},
        ml={"PHI": dict(ticker="KXNBAGAME-26OCT20PHINYK-PHI", title="Philadelphia wins", sub="Philadelphia"),
            "NYK": dict(ticker="KXNBAGAME-26OCT20PHINYK-NYK", title="New York wins", sub="New York")},
        spreads=[dict(ticker="KXNBASPREAD-26OCT20PHINYK-NYK6", code="NYK", floor=5.5, title="New York wins by over 5.5 points",
                      strike_type="greater"),
                 dict(ticker="KXNBASPREAD-26OCT20PHINYK-PHI4", code="PHI", floor=3.5, title="Philadelphia wins by over 3.5 points",
                      strike_type="greater")])


def gamma():
    return dict(title="76ers vs. Knicks", markets=[
        dict(question="76ers vs. Knicks", sportsMarketType="moneyline", outcomes='["76ers", "Knicks"]',
             clobTokenIds='["111", "112"]', conditionId="0xa"),
        dict(question="76ers vs. Knicks: Team to Score First", sportsMarketType="basketball_team_to_score_first",
             outcomes=["76ers", "Knicks"], clobTokenIds=["121", "122"]),
        dict(question="Spread: Knicks (-5.5)", sportsMarketType="spreads", outcomes=["Knicks", "76ers"],
             clobTokenIds=["131", "132"]),
        dict(question="Spread: 76ers (-3.5)", sportsMarketType="spreads", outcomes=["76ers", "Knicks"],
             clobTokenIds=["141", "142"]),
        dict(question="Spread: Knicks (-8.5)", sportsMarketType="spreads", outcomes=["Knicks", "76ers"],
             clobTokenIds=["151", "152"]),                                     # no Kalshi strike
        dict(question="1H Spread: Knicks (-2.5)", sportsMarketType="first_half_spreads", outcomes=["Knicks", "76ers"],
             clobTokenIds=["161", "162"]),                                     # ignored
    ])


def test_split_and_codes():
    assert pr.split_game("KXNBAGAME-26OCT20PHINYK") == ("26OCT20PHINYK", "26OCT20", "PHI", "NYK")
    assert pr.kalshi_code("KXNBAGAME-26OCT20PHINYK-NYK", "KXNBAGAME") == "NYK"
    assert pr.kalshi_code("KXNBASPREAD-26OCT20PHINYK-NYK6", "KXNBASPREAD") == "NYK"
    assert len(set(pr.NICK2CODE.values())) == 30


def test_team_resolution():
    codes = {"PHI", "NYK"}
    assert pr.resolve_team("76ers", codes) == "PHI"
    assert pr.resolve_team("Knicks", codes) == "NYK"
    assert pr.resolve_team("Philadelphia", codes) == "PHI"
    assert pr.resolve_team("Celtics", codes) is None                 # not in this game
    assert pr.resolve_team("Celtics", None) == "BOS"
    tm = pr.build_title_map([dict(code="NYK", title="New York wins", sub="New York")])
    assert tm == {"new york": "NYK"}


def test_match_game_moneyline_and_spread_strike():
    pairs, unm = pr.match_game(kgame(), gamma())
    by = {p["kalshi"]: p for p in pairs}
    ml = by["KXNBAGAME-26OCT20PHINYK-PHI"]                           # first outcome 76ers -> PHI, token[0]
    assert ml["type"] == "moneyline" and ml["poly_token"] == "111" and ml["team"] == "PHI"
    sp = by["KXNBASPREAD-26OCT20PHINYK-NYK6"]                        # Knicks -5.5 <-> floor_strike 5.5
    assert sp["poly_token"] == "131" and sp["line"] == -5.5 and sp["floor"] == 5.5
    sp2 = by["KXNBASPREAD-26OCT20PHINYK-PHI4"]
    assert sp2["poly_token"] == "141" and sp2["team"] == "PHI"
    assert len(pairs) == 3
    assert [u["poly_question"] for u in unm] == ["Spread: Knicks (-8.5)"]
    assert "no Kalshi spread market" in unm[0]["why"]


def test_wrong_strike_not_matched():
    kg = kgame()
    kg["spreads"][0]["floor"] = 6.5                                  # Kalshi strike moved: must not pair with -5.5
    pairs, unm = pr.match_game(kg, gamma())
    assert "KXNBASPREAD-26OCT20PHINYK-NYK6" not in {p["kalshi"] for p in pairs}


def test_poly_book_and_price_change_top():
    pb = pr.PolyBooks(["T1"])
    book = {"event_type": "book", "asset_id": "T1", "market": "0xm", "timestamp": "1757908892351",
            "bids": [{"price": "0.48", "size": "30"}, {"price": "0.49", "size": "20"}, {"price": "0.50", "size": "15"}],
            "asks": [{"price": "0.54", "size": "10"}, {"price": "0.53", "size": "60"}, {"price": "0.52", "size": "25"}]}
    out = pb.feed(book, 100)
    assert out == [("T1", 100, 1757908892351, (0.50, 15.0, 0.52, 25.0))]          # unsorted input handled
    # a deeper level changes: top unchanged -> no emission
    pc = {"event_type": "price_change", "market": "0xm", "timestamp": "1757908892400",
          "price_changes": [{"asset_id": "T1", "price": "0.48", "size": "99", "side": "BUY", "best_bid": "0.5",
                             "best_ask": "0.52"}]}
    assert pb.feed(pc, 101) == []
    # size change at the top bid: emits (top is price AND size)
    pc["price_changes"] = [{"asset_id": "T1", "price": "0.50", "size": "40", "side": "BUY", "best_bid": "0.5",
                            "best_ask": "0.52"}]
    assert pb.feed(pc, 102)[0][3] == (0.50, 40.0, 0.52, 25.0)
    # new better bid
    pc["price_changes"] = [{"asset_id": "T1", "price": "0.51", "size": "7", "side": "BUY", "best_bid": "0.51",
                            "best_ask": "0.52"}]
    assert pb.feed(pc, 103)[0][3] == (0.51, 7.0, 0.52, 25.0)
    # top ask removed (size 0) -> next ask 0.53
    pc["price_changes"] = [{"asset_id": "T1", "price": "0.52", "size": "0", "side": "SELL", "best_bid": "0.51",
                            "best_ask": "0.53"}]
    assert pb.feed(pc, 104)[0][3] == (0.51, 7.0, 0.53, 60.0)
    # identical re-send of the same top: no emission; other asset ignored
    pc["price_changes"] = [{"asset_id": "OTHER", "price": "0.1", "size": "1", "side": "BUY"},
                           {"asset_id": "T1", "price": "0.53", "size": "60", "side": "SELL"}]
    assert pb.feed(pc, 105) == []
    assert pb.mismatch == 0


def test_poly_price_change_before_snapshot_ignored_and_invalidate():
    pb = pr.PolyBooks(["T1"])
    pc = {"event_type": "price_change", "price_changes": [{"asset_id": "T1", "price": "0.5", "size": "1", "side": "BUY"}]}
    assert pb.feed(pc, 1) == []
    pb.feed({"event_type": "book", "asset_id": "T1", "bids": [{"price": "0.4", "size": "1"}], "asks": []}, 2)
    assert pb.top("T1") == (0.4, 1.0, None, None)
    assert pb.invalidate() == ["T1"] and pb.top("T1") == (None, None, None, None)


def test_poly_heals_stale_levels_to_server_best():
    pb = pr.PolyBooks(["T1"])
    pb.feed({"event_type": "book", "asset_id": "T1", "bids": [{"price": "0.09", "size": "97"}, {"price": "0.08", "size": "5"}],
             "asks": [{"price": "0.12", "size": "150"}]}, 1)
    out = pb.feed({"event_type": "price_change", "price_changes": [
        {"asset_id": "T1", "price": "0.08", "size": "327", "side": "BUY", "best_bid": "0.08", "best_ask": "0.12"}]}, 2)
    assert pb.mismatch == 1 and out[0][3][0] == 0.08                 # stale 0.09 bid dropped to match the server's best_bid


def test_kalshi_bookset_top_change_only():
    bs = pr.lf.make_fast_bookset()
    snap = {"type": "orderbook_snapshot", "sid": 1, "seq": 1,
            "msg": {"market_ticker": "M", "yes_dollars_fp": [["0.4000", "10.00"]], "no_dollars_fp": [["0.5500", "20.00"]]}}
    tops = list(bs.feed({"recv_ns": 1, "msg_obj": snap}))
    assert len(tops) == 1 and tops[0].best_yes_bid == 0.40 and abs(tops[0].best_yes_ask - 0.45) < 1e-9
    deep = {"type": "orderbook_delta", "sid": 1, "seq": 2,
            "msg": {"market_ticker": "M", "price_dollars": "0.3000", "delta_fp": "5.00", "side": "yes"}}
    assert list(bs.feed({"recv_ns": 2, "msg_obj": deep})) == []                  # not the top: no emission
    top = {"type": "orderbook_delta", "sid": 1, "seq": 3,
           "msg": {"market_ticker": "M", "price_dollars": "0.4000", "delta_fp": "5.00", "side": "yes"}}
    t2 = list(bs.feed({"recv_ns": 3, "msg_obj": top}))
    assert len(t2) == 1 and t2[0].bid_size == 15.0


def test_build_map_with_fakes():
    def fake_fetch(slug):
        return gamma() if slug == "nba-nyk-phi-2026-10-20" else None          # forces the home/away swap path
    doc = pr.build_map("26OCT20", "t", fetch=fake_fetch, kgames={"26OCT20PHINYK": kgame()})
    assert doc["games"]["26OCT20PHINYK"]["slug"] == "nba-nyk-phi-2026-10-20"
    assert doc["misses"] == [dict(game="26OCT20PHINYK", slug="nba-phi-nyk-2026-10-20", err="404/empty")]
    assert len(doc["pairs"]) == 3 and len(doc["unmatched"]) == 1
    assert sorted(doc["kalshi_only"]) == ["KXNBAGAME-26OCT20PHINYK-NYK"]
