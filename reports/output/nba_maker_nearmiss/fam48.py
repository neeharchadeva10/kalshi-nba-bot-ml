"""#53, #48 family: resting buy (YES/over) at the bid on the #48 beneficiary props, signal evaluated at E0 instead of T-1h.

Reuses #48's tm_lib (selection.csv, Box, Reports, star/beneficiary rules, strike choice) and analysis.report_sets unchanged.
Per (beneficiary, stat) the post time is E0 = the earliest candle end among its selected strike files (the 2 strikes); a strike
whose file has no candle ending exactly at E0 is unavailable at that post time (no_candle).  The injury-report cutoff, star/late
flags and beneficiaries are ALL evaluated at that E0 (last report with CreationDate <= E0).  Outcomes are read only after the
order simulation inputs (quote, fill) are fixed and never influence which bet is made.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_teammate_out"))
import tm_lib as L  # noqa: E402
import analysis as A48  # noqa: E402

import nm_lib as N  # noqa: E402

VERSIONS = L.VERSIONS            # 24 : (stat, M_star, beneficiaries, timing)


def read_ohlc(ticker: str, game_date: str):
    """None if not downloaded else list of candle tuples (end, bid, ask, vol, plow, phigh) from the cached raw response."""
    p = L.candle_path(ticker, game_date)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        rec = json.load(fh)
    return N.parse_candles(rec.get("response"))


def load_settlements(tickers):
    return A48.load_settlements(tickers)


def pick_strike_at(cands):
    """cands: [dict(strike, ticker, candle)] with candle = tuple at E0 or None.  Exactly #48's pick (nearest-0.50 mid, 15-85c mid,
    spread <= 10c).  Returns (chosen | None, reason).  Uses tm_lib.pick_strike_quote on (end, bid, ask) tuples."""
    return L.pick_strike_quote([dict(strike=c["strike"], ticker=c["ticker"],
                                     candle=None if c["candle"] is None else c["candle"][:4]) for c in cands])


def collect(box, reports, sel, lo, hi, settle, fee_types):
    """sel: selection rows already filtered to the discovery range.  -> dict(bets {version: [bet]}, pool [..], counts, vskips)."""
    cnt = Counter()
    vskips = {v: Counter() for v in VERSIONS}
    bets = {v: [] for v in VERSIONS}
    pool = []
    by_game = defaultdict(list)
    for r in sel:
        if lo <= r["game_date"] <= hi:
            by_game[(r["game_date"], r["game_code"])].append(r)
    for (gd, gc), rows in sorted(by_game.items()):
        cnt["games"] += 1
        T = rows[0]["tip"]
        away, home = gc[:3], gc[3:]
        matchup = f"{away}@{home}"
        candles = {}
        for r in rows:
            candles[r["ticker"]] = read_ohlc(r["ticker"], gd)
        # group rows by (aid, stat); post time per group
        groups = defaultdict(list)
        for r in rows:
            groups[(r["aid"], r["stat"])].append(r)
        e0_of_group, group_state = {}, {}
        for gk, rs in groups.items():
            if any(candles[r["ticker"]] is None for r in rs):
                group_state[gk] = "not_downloaded"
                continue
            firsts = [min(c[0] for c in candles[r["ticker"]]) for r in rs if candles[r["ticker"]]]
            if not firsts:
                group_state[gk] = "no_candles"
                continue
            e0 = min(firsts)
            if e0 > T - N.MIN_LEAD_E0:
                group_state[gk] = "no_post_window"
                continue
            e0_of_group[gk] = e0
            group_state[gk] = "ok"
        for st in group_state.values():
            cnt[f"groups_{st}"] += 1
        struct_cache = {}

        def struct(e):
            """Event structure of this game with every information source cut at e (E0).  Cached per e."""
            if e in struct_cache:
                return struct_cache[e]
            cov = reports.last_covering(e, L.mdy_of(gd), matchup)
            S = dict(cov=cov, outs={}, dbts={}, events={}, active={}, out_all=set())
            for team in (away, home):
                if cov is None:
                    S["outs"][team], S["dbts"][team] = set(), set()
                    continue
                o, d, unm = A48.report_sets(cov[2], team, box)
                S["outs"][team], S["dbts"][team] = o, d
                S["out_all"] |= o | d
                cnt["out_names_unmatched"] += unm
            if cov is not None:
                for team in (away, home):
                    stars = {M: [] for M in L.STAR_MINS}
                    for aid in S["outs"][team]:
                        ws = box.window_stats(aid, team, gd)
                        if ws is None:
                            continue
                        for M in L.STAR_MINS:
                            if L.star_flags(ws["avg_min"], ws["played_last3"], M):
                                stars[M].append(aid)
                    late_of = {}
                    for M in L.STAR_MINS:
                        for aid in stars[M]:
                            if aid in late_of:
                                continue
                            pn = None
                            for rr in cov[2]:
                                if L.TEAMNAME2CODE.get(rr["team"]) == team and rr["status"] == "Out":
                                    ids = box.roster.get((team, 2026), {}).get(L.norm_injury_name(rr["player"]), set())
                                    if ids == {aid}:
                                        pn = L.norm_injury_name(rr["player"])
                                        break
                            fo = reports.first_out(e, L.mdy_of(gd), matchup, team, pn) if pn else None
                            late_of[aid] = L.first_out_late(fo, e)
                    active = []
                    for ids in box.roster.get((team, 2026), {}).values():
                        for a in ids:
                            if a in S["outs"][team] or a in S["dbts"][team]:
                                continue
                            ws = box.window_stats(a, team, gd)
                            if ws is None:
                                continue
                            active.append(dict(aid=a, avg_min=ws["avg_min"], usage_pm=ws["usage_pm"]))
                    S["events"][team] = (stars, late_of)
                    S["active"][team] = active
            struct_cache[e] = S
            return S

        def quote_set(gk, e):
            """[(row, candle at exactly e | None)] for the strikes of group gk."""
            return [(r, N.candle_at(candles[r["ticker"]], e)) for r in groups[gk]]

        def order(row, c, e):
            """Quote -> resting YES order at the bid, fill sim; settlement is read only for the fill value."""
            res = settle.get(row["ticker"])
            try:
                sv, nb = N.settlement_yes(res[0], res[1]) if res else (None, None)
            except (ValueError, TypeError):
                return None, "bad_settlement"
            if sv is None:
                return None, "bad_settlement"
            o = N.simulate_order("yes", c[1], c[2], candles[row["ticker"]], e, T, sv, fee_types.get(N.series_of(row["ticker"])))
            o.update(settle=sv, nonbinary=nb)
            return o, "ok"

        # ---- luck pool: every selected market with a usable quote at its E0, players not listed Out/Doubtful at E0
        for gk, e in e0_of_group.items():
            S = struct(e)
            for row, c in quote_set(gk, e):
                if row["aid"] in S["out_all"]:
                    cnt["pool_excluded_listed_out_or_doubtful"] += 1
                    continue
                ok, why, bid, ask, mid, sp = L.classify_quote(None if c is None else c[:4])
                if not ok:
                    continue
                o, why2 = order(row, c, e)
                if o is None or not o["posted"]:
                    continue
                pool.append(dict(key=row["ticker"], night=gd, stat=row["stat"], limit=o["limit"], player=row["aid"],
                                 filled=o["filled"], net=o["net"], game=(gd, gc)))
        # ---- bets
        all_e = sorted(set(e0_of_group.values()))
        first_e = all_e[0] if all_e else None
        for e in all_e:
            S = struct(e)
            if S["cov"] is None:
                if e == first_e:
                    cnt["games_no_report"] += 1
                continue
            for team in (away, home):
                stars, late_of = S["events"][team]
                for M in L.STAR_MINS:
                    if not stars[M]:
                        continue
                    late = any(late_of[a] for a in stars[M])
                    if e == first_e:
                        cnt[f"events_M{M}"] += 1
                        cnt[f"events_M{M}_late"] += 1 if late else 0
                    for kind in L.BENS:
                        bens = L.pick_beneficiaries(S["active"][team], kind)
                        if not bens and e == first_e:
                            cnt[f"no_beneficiary_M{M}_{kind}"] += 1
                        for stat in L.STATS:
                            for timing in L.TIMINGS:
                                if timing == "late" and not late:
                                    continue
                                v = (stat, M, kind, timing)
                                for b in bens:
                                    gk = (b["aid"], stat)
                                    if gk not in groups:
                                        if e == first_e:
                                            vskips[v]["beneficiary_has_no_selected_market"] += 1
                                        continue
                                    state = group_state[gk]
                                    if state != "ok":
                                        if e == first_e:       # counted once per game, not once per post time
                                            vskips[v][state] += 1
                                        continue
                                    if e0_of_group[gk] != e:
                                        continue               # evaluated under its own E0 only
                                    qs = quote_set(gk, e)
                                    chosen, why = pick_strike_at([dict(strike=r["floor_strike"], ticker=r["ticker"], candle=c)
                                                                  for r, c in qs])
                                    if chosen is None:
                                        vskips[v][why] += 1
                                        continue
                                    row = next(r for r, _ in qs if r["ticker"] == chosen["ticker"])
                                    c = N.candle_at(candles[row["ticker"]], e)
                                    o, why2 = order(row, c, e)
                                    if o is None:
                                        vskips[v][why2] += 1
                                        continue
                                    if not o["posted"]:
                                        vskips[v]["limit_out_of_range"] += 1
                                        continue
                                    act = box.actual.get((b["aid"], gd), {}).get(stat)
                                    bets[v].append(dict(
                                        family="48", version=v, key=row["ticker"], unit=(gd, gc), night=gd, team=team, aid=b["aid"],
                                        stat=stat, strike=row["floor_strike"], bid=chosen["bid"], ask=chosen["ask"], e0=e, T=T,
                                        limit=o["limit"], filled=o["filled"], settle=o["settle"], nonbinary=o["nonbinary"],
                                        win_value=o["win_value"], net=o["net"], net1c=o["net1c"], actual=act, late=late,
                                        side="yes", stars="|".join(box.names[a] for a in stars[M])))
    return dict(bets=bets, pool=pool, counts=cnt, vskips=vskips)
