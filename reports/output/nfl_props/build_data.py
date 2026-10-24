"""Build the bet table for a split.  Usage: python build_data.py discovery [--plan]
Downloads (cached, GET-only, adaptive pacing) hourly candles ending at the decision time D = T - 1h for the 2-strike scope of
(a) every shock-beneficiary player and (b) a seeded capped sample of other players (luck-test pool).  Unseen refuses without AUDIT_OK."""
import sys, json, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
import feat, shocks, lib, nfl_lib as L

H = Path(__file__).parent
POOL_CAP = 10          # other-player units per (stat, week)
HOLD = 3 * 3600        # expected_expiration_time - 3h = kickoff = T


def eligible_split(events, sched, stats_tg):
    """events: {(series,event): row}. Returns (ordered eligible (season,week) list, discovery set, unseen set, mapping)."""
    mp = {}
    for (s, e), r in events.items():
        g = feat.map_event_to_game(e, sched)
        if g is None:
            continue
        ok = any(len(shocks.prior_games(stats_tg, t, int(g.season), int(g.gkey))) >= shocks.MIN_PRIOR
                 for t in (g.away_team, g.home_team))
        mp[(s, e)] = (g, ok)
    weeks = sorted({(int(g.season), int(g.week)) for g, ok in mp.values() if ok})
    h = len(weeks) // 2
    return weeks, set(weeks[:h]), set(weeks[h:]), mp


def main(split, plan_only=False):
    if split == "unseen" and not (H / "AUDIT_OK").exists():
        raise SystemExit("REFUSED: reports/output/nfl_props/AUDIT_OK is missing (Opus audit required before unseen).")
    rows = {}
    for l in open(feat.IDX):
        r = json.loads(l)
        if r["series"] in feat.STAT_FOR and r["result"] in ("yes", "no"):
            rows[r["ticker"]] = r if r["ticker"] not in rows or r["tier"] == "hist" else rows[r["ticker"]]
    events, evmk = {}, collections.defaultdict(list)
    for r in rows.values():
        events.setdefault((r["series"], r["event_ticker"]), r); evmk[(r["series"], r["event_ticker"])].append(r)
    sched = feat.load_schedule(); stats = feat.load_stats(); inj = feat.load_injuries()
    tg = feat.team_games(stats); inj_out = shocks.build_inj_out(inj)
    groups = {"RB": feat.group_frame(stats, "RB"), "REC": feat.group_frame(stats, "REC")}
    weeks, disc, unseen, mp = eligible_split(events, sched, tg)
    use = disc if split == "discovery" else unseen
    pid_of = {(r.nm, int(r.season), r.team): r.player_id for r in stats.drop_duplicates(["nm", "season", "team"]).itertuples()}
    plays = {pid: g.sort_values("gkey")[["gkey", "rushing_yards", "receiving_yards", "receptions"]]
             for pid, g in stats.groupby("player_id")}
    rng = np.random.default_rng(L.SEED)
    units, skip = [], collections.Counter()
    diag_kick = []
    for (ser, ev), (g, ok) in sorted(mp.items()):
        if (int(g.season), int(g.week)) not in use or not ok:
            continue
        stat = feat.STAT_FOR[ser]; grp = feat.GROUP[stat]
        mk = evmk[(ser, ev)]
        ee = pd.Timestamp(mk[0]["expected_expiration_time"]).timestamp() if mk[0].get("expected_expiration_time") else None
        if ee is None:
            skip["no_expected_expiration"] += 1; continue
        T = int(ee - HOLD); D = T - 3600
        ko = pd.Timestamp(f"{g.gameday} {g.gametime}", tz="America/New_York").timestamp() if g.gametime else None
        if ko:
            diag_kick.append((T - ko) / 60)
        det = {t: shocks.detect(groups[grp], inj_out, tg, t, int(g.season), int(g.gkey)) for t in (g.away_team, g.home_team)}
        tags = collections.defaultdict(set)
        for t, d in det.items():
            if d["A"]: tags[d["A"]].add("A")
            for J in ("B10", "B20"):
                for p in d[J]: tags[p].add(J)
        byplayer = collections.defaultdict(list)
        for m in mk:
            byplayer[(m.get("yes_sub_title") or "").split(":")[0]].append(m)
        for name, ms in byplayer.items():
            nm = feat.norm_name(name)
            pid = None
            for t in (g.away_team, g.home_team):
                pid = pid or pid_of.get((nm, int(g.season), t))
            if pid is None:
                skip["player_not_in_nflverse"] += 1; continue
            pl = plays.get(pid)
            prior = pl[pl.gkey < int(g.gkey)].tail(4) if pl is not None else None
            if prior is None or len(prior) < 4:
                skip["player_lt4_prior_games"] += 1; continue
            mean4 = float(prior[feat.STAT_COL[stat]].mean())
            thr = {m["ticker"]: m["floor_strike"] + 0.5 for m in ms if m.get("floor_strike") is not None}
            sc = L.scope_strikes(sorted(set(thr.values())), mean4)
            units.append(dict(series=ser, stat=stat, event=ev, season=int(g.season), week=int(g.week), gameday=g.gameday,
                              pid=pid, name=name, tags=sorted(tags.get(pid, [])), D=D, T=T, mean4=mean4,
                              scope=[(m["ticker"], thr[m["ticker"]], m["tier"], m["result"]) for m in ms
                                     if m["ticker"] in thr and thr[m["ticker"]] in sc]))
    # pool sampling: tagged units always; untagged capped per (stat, week)
    tagged = [u for u in units if u["tags"]]
    other = collections.defaultdict(list)
    for u in units:
        if not u["tags"]:
            other[(u["stat"], u["week"], u["season"])].append(u)
    keep = list(tagged)
    for k in sorted(other):
        o = other[k]; idx = rng.permutation(len(o))[:POOL_CAP]
        keep += [o[i] for i in sorted(idx)]
    nreq = sum(len(u["scope"]) for u in keep)
    print(f"split={split} weeks={sorted(use)} units_total={len(units)} tagged={len(tagged)} kept={len(keep)} "
          f"candle_requests={nreq} skip={dict(skip)}", flush=True)
    if diag_kick:
        print("T(kalshi expected_exp-3h) minus schedule kickoff, minutes: median", np.median(diag_kick),
              "p10/p90", np.percentile(diag_kick, [10, 90]), flush=True)
    if plan_only:
        return
    c = lib.KClient(); out = []
    for i, u in enumerate(keep):
        quotes = {}; info = {}
        for tk, thr, tier, res in u["scope"]:
            cs = lib.candles(c, u["series"], tk, u["D"] - 6 * 3600, u["D"], tier == "hist")
            q = L.quote_at(cs, u["D"])
            quotes[thr] = (q[0], q[1]) if q else None
            info[thr] = (tk, res, q[2] if q else None)
        main_thr = L.pick_main(quotes)
        rec = dict(u, tags="|".join(u["tags"]), n_scope=len(u["scope"]), has_main=main_thr is not None)
        rec.pop("scope")
        if main_thr is not None:
            bid, ask = quotes[main_thr]; tk, res, ce = info[main_thr]
            paid = L.entry_price(ask)
            rec.update(ticker=tk, strike=main_thr, bid=bid, ask=ask, paid=paid, quote_end=ce, result=res,
                       net=L.net_per_contract(paid, res == "yes") if paid < 1 else None)
        out.append(rec)
        if (i + 1) % 50 == 0:
            print(i + 1, "/", len(keep), c.stats, "rate", c.pacer.rate, flush=True)
    pd.DataFrame(out).to_csv(H / f"bets_{split}.csv", index=False)
    json.dump(dict(split=split, weeks=sorted(use), all_eligible_weeks=weeks, skip=dict(skip), units=len(units),
                   kept=len(keep), stats=c.stats), open(H / f"build_{split}.json", "w"), default=str, indent=1)
    print("written", len(out), c.stats)


if __name__ == "__main__":
    main(sys.argv[1], "--plan" in sys.argv)
