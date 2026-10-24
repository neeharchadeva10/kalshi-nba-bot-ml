"""Research log #48: star-out events, beneficiaries, bets for all 24 versions, over a date range, game by game.

No look-ahead: a game's decision time is D = T-1h; only injury reports with CreationDate <= D, only box-score games with an
ET date strictly before the game date, only the candle ending at or before D.  Outcomes (settlement, actual stat) are read
only AFTER a bet is fixed and are never used for selecting it.
"""
from __future__ import annotations

from collections import Counter, defaultdict

import tm_lib as L


def load_settlements(tickers):
    want = set(tickers)
    out = {}
    for (t, stat, gd, gc, team, pk, pname, fs, res, occ, exp, sv) in L.load_market_defs():
        if t in want:
            out[t] = (res, sv)
    return out


def report_sets(cov_rows, team, box):
    """-> (out_aids, doubtful_aids, n_unmatched_names) for one team from the rows of the covering report."""
    ro = box.roster.get((team, 2026), {})
    out, dbt, unm = set(), set(), 0
    for r in cov_rows:
        if L.TEAMNAME2CODE.get(r["team"]) != team or r["status"] not in ("Out", "Doubtful"):
            continue
        ids = ro.get(L.norm_injury_name(r["player"]), set())
        if len(ids) != 1:
            unm += 1
            continue
        (out if r["status"] == "Out" else dbt).add(next(iter(ids)))
    return out, dbt, unm


def collect(box, reports, sel, lo, hi, settle):
    """sel: selection rows.  Games with lo <= game_date <= hi.
    -> dict(bets {version: [bet]}, pool [pool bet], counts Counter, vskips {version: Counter}, events list, base rows)."""
    cnt = Counter()
    vskips = {v: Counter() for v in L.VERSIONS}
    bets = {v: [] for v in L.VERSIONS}
    pool, base, events = [], [], []
    by_game = defaultdict(list)
    for r in sel:
        if lo <= r["game_date"] <= hi:
            by_game[(r["game_date"], r["game_code"])].append(r)
    by_pst = defaultdict(list)       # (gd, gc, aid, stat) -> rows
    for k, rows in by_game.items():
        for r in rows:
            by_pst[(k[0], k[1], r["aid"], r["stat"])].append(r)
    for (gd, gc), rows in sorted(by_game.items()):
        cnt["games"] += 1
        tip = rows[0]["tip"]
        d_ts = tip - 3600
        away, home = gc[:3], gc[3:]
        matchup = f"{away}@{home}"
        # ---- decision quotes of every selected market of the game
        quote = {}
        for r in rows:
            cs = L.read_candles(r["ticker"], gd)
            if cs is None:
                quote[r["ticker"]] = ("not_downloaded", None)
            else:
                quote[r["ticker"]] = ("ok", L.decision_candle(cs, d_ts))
        cov = reports.last_covering(d_ts, L.mdy_of(gd), matchup)
        if cov is None:
            cnt["games_no_report"] += 1
        else:
            cnt["games_report_not_newest"] += 1 if cov[3] > 0 else 0
        out_all = set()
        outs, dbts = {}, {}
        for team in (away, home):
            if cov is None:
                outs[team], dbts[team] = set(), set()
                continue
            o, d, unm = report_sets(cov[2], team, box)
            outs[team], dbts[team] = o, d
            cnt["out_names_unmatched"] += unm
            out_all |= o | d
        # ---- pool (price-matched luck test) and base-rate rows: every selected market with a usable quote
        for r in rows:
            stt, c = quote[r["ticker"]]
            if stt != "ok":
                cnt["pool_not_downloaded"] += 1
                continue
            res = settle.get(r["ticker"])
            try:
                sv, nb = L.settlement_value(res[0], res[1]) if res else (None, None)
            except ValueError:
                sv = None
            if sv is None:
                cnt["pool_bad_settlement"] += 1
                continue
            if c is not None and L.valid_quote(c[1], c[2]):
                base.append(dict(ticker=r["ticker"], rank=r["rank"], stat=r["stat"], bid=c[1], ask=c[2], settle=sv, nonbinary=nb,
                                 mid=(c[1] + c[2]) / 2, spread=c[2] - c[1]))
            ok, why, bid, ask, mid, sp = L.classify_quote(c)
            if not ok:
                continue
            if r["aid"] in out_all:
                cnt["pool_excluded_listed_out_or_doubtful"] += 1
                continue
            net, _ = L.bet_net(sv, ask)
            pool.append(dict(key=r["ticker"], night=gd, stat=r["stat"], ask=ask, player=r["aid"], net=net, game=(gd, gc)))
        if cov is None:
            continue
        # ---- events and bets
        for team in (away, home):
            stars = {M: [] for M in L.STAR_MINS}
            for aid in outs[team]:
                ws = box.window_stats(aid, team, gd)
                if ws is None:
                    continue
                for M in L.STAR_MINS:
                    if L.star_flags(ws["avg_min"], ws["played_last3"], M):
                        stars[M].append(aid)
            if not any(stars.values()):
                continue
            nm = {a: L.norm_name(box.names[a]) for a in outs[team]}
            # late flag per star: first report (<= D, last 72 h) that lists him Out
            late_of = {}
            for M in L.STAR_MINS:
                for aid in stars[M]:
                    if aid not in late_of:
                        pn = None
                        for r in cov[2]:
                            if L.TEAMNAME2CODE.get(r["team"]) == team and r["status"] == "Out":
                                ids = box.roster.get((team, 2026), {}).get(L.norm_injury_name(r["player"]), set())
                                if ids == {aid}:
                                    pn = L.norm_injury_name(r["player"])
                                    break
                        fo = reports.first_out(d_ts, L.mdy_of(gd), matchup, team, pn) if pn else None
                        late_of[aid] = L.first_out_late(fo, d_ts)
            active = []
            for aid in box.roster.get((team, 2026), {}).values():
                for a in aid:
                    if a in outs[team] or a in dbts[team]:
                        continue
                    ws = box.window_stats(a, team, gd)
                    if ws is None:
                        continue
                    active.append(dict(aid=a, avg_min=ws["avg_min"], usage_pm=ws["usage_pm"]))
            for M in L.STAR_MINS:
                if not stars[M]:
                    continue
                late = any(late_of[a] for a in stars[M])
                cnt[f"events_M{M}"] += 1
                cnt[f"events_M{M}_late"] += 1 if late else 0
                events.append(dict(game=f"{gd} {gc}", team=team, M=M, stars=[box.names[a] for a in stars[M]], late=late))
                for kind in L.BENS:
                    bens = L.pick_beneficiaries(active, kind)
                    if not bens:
                        cnt[f"no_beneficiary_M{M}_{kind}"] += 1
                    for stat in L.STATS:
                        for timing in L.TIMINGS:
                            if timing == "late" and not late:
                                continue
                            v = (stat, M, kind, timing)
                            for b in bens:
                                cand_rows = by_pst.get((gd, gc, b["aid"], stat), [])
                                if not cand_rows:
                                    vskips[v]["beneficiary_has_no_selected_market"] += 1
                                    continue
                                if any(quote[x["ticker"]][0] == "not_downloaded" for x in cand_rows):
                                    vskips[v]["not_downloaded"] += 1
                                    continue
                                chosen, why = L.pick_strike_quote(
                                    [dict(strike=x["floor_strike"], ticker=x["ticker"], candle=quote[x["ticker"]][1]) for x in cand_rows])
                                if chosen is None:
                                    vskips[v][why] += 1
                                    continue
                                res = settle.get(chosen["ticker"])
                                try:
                                    sv, nb = L.settlement_value(res[0], res[1])
                                except (ValueError, TypeError):
                                    vskips[v]["bad_settlement"] += 1
                                    continue
                                net, fee = L.bet_net(sv, chosen["ask"])
                                act = box.actual.get((b["aid"], gd), {}).get(stat)
                                bets[v].append(dict(version=v, key=chosen["ticker"], game=(gd, gc), night=gd, team=team, aid=b["aid"],
                                                    stat=stat, strike=chosen["strike"], bid=chosen["bid"], ask=chosen["ask"],
                                                    mid=chosen["mid"], settle=sv, nonbinary=nb, fee=fee, net=net, actual=act,
                                                    late=late, stars="|".join(box.names[a] for a in stars[M])))
    return dict(bets=bets, pool=pool, base=base, counts=cnt, vskips=vskips, events=events)


def version_stats(bets):
    out = {}
    for v in L.VERSIONS:
        b = bets[v]
        s = L.mean_lb([x["net"] for x in b])
        s["win_pct"] = (100.0 * sum(1 for x in b if x["settle"] == 1.0) / len(b)) if b else None
        s["n_nonbinary"] = sum(1 for x in b if x["nonbinary"])
        s["n_games"] = len({x["game"] for x in b})
        ex = [x["actual"] - x["strike"] for x in b if x["actual"] is not None]
        s["mean_actual_minus_strike"] = (sum(ex) / len(ex)) if ex else None
        s["n_actual_missing"] = sum(1 for x in b if x["actual"] is None)
        s["mean_ask"] = (sum(x["ask"] for x in b) / len(b)) if b else None
        out[v] = s
    return out


def base_rate_table(base):
    """Main-line (rank 1 = strike nearest the player's mean) overs: bucket by YES mid, 10c bands, all valid quotes.
    Also the all-selected-strikes table and the 15-85c / spread<=10c subset."""
    def table(rows):
        bands = [(i / 100, (i + 10) / 100) for i in range(0, 100, 10)]
        res = []
        for lo, hi in bands:
            sub = [x for x in rows if lo <= x["mid"] < hi or (hi == 1.0 and x["mid"] == 1.0)]
            if not sub:
                res.append(dict(band=f"{int(lo * 100)}-{int(hi * 100)}c", n=0))
                continue
            nets = [L.bet_net(x["settle"], x["ask"])[0] for x in sub if x["ask"] + L.SLIP < 1.0]
            res.append(dict(band=f"{int(lo * 100)}-{int(hi * 100)}c", n=len(sub),
                            yes_rate=sum(1 for x in sub if x["settle"] == 1.0) / len(sub),
                            mean_settle=sum(x["settle"] for x in sub) / len(sub), mean_mid=sum(x["mid"] for x in sub) / len(sub),
                            mean_ask=sum(x["ask"] for x in sub) / len(sub),
                            mean_net_buy_at_ask=(sum(nets) / len(nets)) if nets else None,
                            n_nonbinary=sum(1 for x in sub if x["nonbinary"])))
        return res
    r1 = [x for x in base if x["rank"] == 1]
    filt = [x for x in r1 if L.PX_LO <= x["mid"] <= L.PX_HI and x["spread"] <= L.MAX_SPREAD + 1e-9]
    nets = [L.bet_net(x["settle"], x["ask"])[0] for x in filt]
    return dict(main_line_rank1=table(r1), all_selected_strikes=table(base),
                filtered_15_85_spread10=dict(n=len(filt), mean_net=(sum(nets) / len(nets)) if nets else None,
                                             yes_rate=(sum(1 for x in filt if x["settle"] == 1.0) / len(filt)) if filt else None))
