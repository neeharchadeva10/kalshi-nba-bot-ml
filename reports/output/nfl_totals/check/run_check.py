import sys, time, itertools
import numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from check_lib import *

t0 = time.time()
BASE = ROOT / "reports/output/nfl_totals"
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
M = Model()
print("model built", round(time.time() - t0, 1))

# 1. A-PTS predictions on sample games (predictions only, no outcomes)
sg = pd.read_csv(BASE / "sample_games.csv").game_id
rows = []
for gid in sg:
    for h, k in itertools.product((4, 8, 16), (2, 4, 8)):
        d = M.apts(gid, h, k)
        rows.append(dict(game_id=gid, h=h, k=k, **d))
pd.DataFrame(rows)[["game_id", "h", "k", "pred", "O_home", "D_home", "O_away", "D_away", "L"]].to_csv(OUT / "check_apts_predictions.csv", index=False)

pd.DataFrame({'game_id': list(sg), 'pred_adrive': [M.adrive(i) for i in sg]}).to_csv(OUT / 'check_adrive_predictions.csv', index=False)

# 2. EPA / drive features on sample team-games
stg = pd.read_csv(BASE / "sample_teamgames.csv")
rows = []
for gid, team in zip(stg.game_id, stg.team):
    H, A, kick, s = M.game(gid)
    tm = TEAMMAP.get(team, team)
    r = dict(game_id=gid, team=team)
    for h in (4, 8, 16):
        f = M.epa_feat(tm, kick, s, h, 4)
        r[f"off_epa_h{h}"] = f["off_epa"]; r[f"def_epa_h{h}"] = f["def_epa"]; r[f"plays_h{h}"] = f["plays"]
    f = M.drive_feat(tm, kick, s, 8, 4)
    r["DPG"] = f["DPG"]; r["PPD_off"] = f["PPD_off"]; r["PPD_def"] = f["PPD_def"]
    rows.append(r)
pd.DataFrame(rows).to_csv(OUT / "check_epa_teamgames.csv", index=False)

# 3. D versions. D = seasons 2006-2014 only.
g = M.g
D = g[(g.season >= 2006) & (g.season <= 2014) & g.total_line.notna() & g.home_score.notna() & g.away_score.notna()]
D = D.sort_values("kick")
ids = list(D.game_id)
tot = (D.home_score + D.away_score); tot.index = D.game_id
line = D.set_index("game_id").total_line

preds = {}
for h, k in itertools.product((4, 8, 16), (2, 4, 8)):
    preds[f"A-PTS|h{h}|k{k}"] = pd.Series({i: M.apts(i, h, k)["pred"] for i in ids})
# A-EPA: OLS per h on D team-games (both teams of a game valid)
for h in (4, 8, 16):
    X, y, gi = [], [], []
    XX = {}
    for i in ids:
        xh, xa = M.epa_X(i, h)
        XX[i] = (xh, xa)
        if np.isnan(xh).any() or np.isnan(xa).any():
            continue
        r = D.set_index("game_id").loc[i]
        X += [xh, xa]; y += [r.home_score, r.away_score]
    X = np.array(X); y = np.array(y)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    print(f"EPA h{h}: n_teamgames={len(y)} beta={np.round(beta, 4).tolist()}")
    preds[f"A-EPA|h{h}"] = pd.Series({i: float((XX[i][0] @ beta) + (XX[i][1] @ beta)) if not (np.isnan(XX[i][0]).any() or np.isnan(XX[i][1]).any()) else np.nan for i in ids})
preds["A-DRIVE"] = pd.Series({i: M.adrive(i) for i in ids})

res = []
vs = pd.read_csv(BASE / "sample_versions_A.csv").version
for v in vs:
    parts = v.split("|")
    G = float(parts[-1][1:])
    key = "|".join(parts[:-1])
    p = preds[key]
    w = l = pu = 0
    pnl = 0.0
    for i in ids:
        pr = p[i]
        if np.isnan(pr):
            continue
        diff = pr - line[i]
        if diff >= G: side = "O"
        elif -diff >= G: side = "U"
        else: continue
        r, o = settle(tot[i], line[i], side)
        pnl += r
        if o == "W": w += 1
        elif o == "L": l += 1
        else: pu += 1
    n = w + l + pu
    res.append(dict(version=v, n_bets=n, wins=w, losses=l, pushes=pu, mean_pnl=pnl / n if n else np.nan))
pd.DataFrame(res).to_csv(OUT / "check_versions_D.csv", index=False)
print("done", round(time.time() - t0, 1), "s")
