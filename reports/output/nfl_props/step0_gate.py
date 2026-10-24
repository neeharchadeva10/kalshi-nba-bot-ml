"""Step 0 gate: count possible beneficiary bets per season / series / version (upper bound: price filters not applied)."""
import sys, json, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pandas as pd
import feat, shocks

rows = [json.loads(l) for l in open(feat.IDX)]
sched = feat.load_schedule(); stats = feat.load_stats(); inj = feat.load_injuries()
tg = feat.team_games(stats); inj_out = shocks.build_inj_out(inj)
groups = {"RB": feat.group_frame(stats, "RB"), "REC": feat.group_frame(stats, "REC")}
# players per (series, event)
evp = collections.defaultdict(set); evres = collections.defaultdict(list)
for r in rows:
    if r["result"] not in ("yes", "no", "scalar"):
        continue
    evp[(r["series"], r["event_ticker"])].add(feat.norm_name((r.get("yes_sub_title") or r["title"]).split(":")[0]))
unmapped = 0; out = []; evinfo = {}
name2 = stats.drop_duplicates(["player_id", "season"])[["player_id", "nm", "season"]]
for (ser, ev), names in evp.items():
    if ser not in feat.STAT_FOR:
        continue
    g = feat.map_event_to_game(ev, sched)
    if g is None:
        unmapped += 1; continue
    stat = feat.STAT_FOR[ser]
    evinfo[(ser, ev)] = (int(g.season), int(g.week), g.gameday, g.game_type)
    for team in (g.away_team, g.home_team):
        det = shocks.detect(groups[feat.GROUP[stat]], inj_out, tg, team, int(g.season), int(g.gkey))
        nm = lambda pid: stats.loc[stats.player_id == pid, "nm"].iloc[0] if (stats.player_id == pid).any() else None
        for tag, pids in (("A", [det["A"]] if det["A"] else []), ("B10", det["B10"]), ("B20", det["B20"])):
            for pid in pids:
                has = nm(pid) in names
                out.append(dict(series=ser, stat=stat, event=ev, season=int(g.season), week=int(g.week),
                                gameday=g.gameday, shock=tag, player_id=pid, has_market=has))
df = pd.DataFrame(out)
df.to_csv(Path(__file__).parent / "gate_candidates.csv", index=False)
ev = pd.DataFrame([dict(series=k[0], event=k[1], season=v[0], week=v[1], gameday=v[2], gt=v[3]) for k, v in evinfo.items()])
print("unmapped events", unmapped, "mapped", len(ev))
print(ev.groupby(["series", "season"]).event.nunique().unstack())
print(ev.groupby(["season", "week"]).event.nunique().unstack(0))
m = df[df.has_market]
print("\nBETS WITH MARKET by series x shock x season")
print(m.groupby(["series", "shock", "season"]).size().unstack([2]).fillna(0).astype(int))
# discovery half: earlier half of eligible (season, week) pooled by date
wk = ev.groupby(["season", "week"]).gameday.min().sort_values()
keys = list(wk.index); half = len(keys) // 2
disc = set(keys[:half]); print("eligible weeks", len(keys), "discovery weeks", sorted(disc))
m = m.assign(disc=[(s, w) in disc for s, w in zip(m.season, m.week)])
d = m[m.disc]
print("\nDISCOVERY bets by version(stat x shock)")
print(d.groupby(["stat", "shock"]).size())
print("TOTAL discovery bets (all 9 versions summed):", len(d), " distinct (event,player,stat):", d.drop_duplicates(["event", "player_id", "series"]).shape[0])
print("max single version in discovery:", d.groupby(["stat", "shock"]).size().max())
print("all-period bets per version:"); print(m.groupby(["stat", "shock"]).size())
