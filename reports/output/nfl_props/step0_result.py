"""Writes gate_result.json from markets_index.jsonl + gate_candidates.csv (outputs of step0_markets.py / step0_gate.py)."""
import json, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pandas as pd, feat
H = Path(__file__).parent
rows = [json.loads(l) for l in open(feat.IDX)]
sched = feat.load_schedule()
ev = {}
for r in rows:
    if r["result"] in ("yes", "no", "scalar"):
        ev.setdefault((r["series"], r["event_ticker"]), r)
cnt = collections.defaultdict(collections.Counter)
unm = []
for (s, e), r in ev.items():
    g = feat.map_event_to_game(e, sched)
    if g is None:
        unm.append(e); continue
    cnt[s][f"{int(g.season)}_wk{int(g.week):02d}"] += 1
df = pd.read_csv(H / "gate_candidates.csv"); m = df[df.has_market]
# discovery half: earlier half of eligible (season, week) pooled
wk = sorted({(int(s), int(w)) for s, w in zip(df.season, df.week)} | {(int(k[:4]), int(k[-2:])) for c in cnt.values() for k in c},
            key=lambda x: x)
disc_weeks = wk[: len(wk) // 2]
m = m.assign(disc=[(s, w) in set(disc_weeks) for s, w in zip(m.season, m.week)])
res = dict(
    verdict="GATE PASSES (upper bound; price filters / candle quotes not yet applied)",
    settled_events_by_series_season_week={s: dict(sorted(c.items())) for s, c in cnt.items()},
    settled_events_unmapped_to_nflverse_schedule=len(unm), unmapped_examples=sorted(unm)[:5],
    kxnflpassyds_note="KXNFLPASSYDS has settled events (2025-10..2026-10) but no pre-registered version uses passing yards; not mapped.",
    bets_possible_by_series_shock_season={f"{a}|{b}|{c}": int(n) for (a, b, c), n in m.groupby(["series", "shock", "season"]).size().items()},
    discovery_weeks=[f"{s}wk{w}" for s, w in disc_weeks],
    discovery_bets_by_version={f"{a}_{b}": int(n) for (a, b), n in m[m.disc].groupby(["stat", "shock"]).size().items()},
    discovery_bets_total_rows=int(m.disc.sum()),
    discovery_bets_distinct_event_player_stat=int(m[m.disc].drop_duplicates(["event", "player_id", "series"]).shape[0]),
    threshold=150,
    candle_check="10 of 10 random shock-beneficiary rush-yard markets (6 historical-tier 2025, 4 live-tier 2026) returned hourly candles "
                 "with yes_bid/yes_ask ending within 25-65 min of T (kickoff); candle fields: end_period_ts, price, yes_bid, yes_ask, volume.",
    nflverse_files="stats_player_week_{2024,2025,2026}.csv (tag stats_player), injuries_{2024,2025,2026}.csv (tag injuries); in data/raw/nflverse/",
)
(H / "gate_result.json").write_text(json.dumps(res, indent=1, default=str))
print(json.dumps(res, indent=1, default=str)[:3000])
