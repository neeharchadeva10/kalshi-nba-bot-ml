import sys, json, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
MAIN = HERE.parent / "nba_score_model" / "main"
sys.path.insert(0, str(MAIN))
import numpy as np, pandas as pd
import sm_lib as S, models as M, run_select as rs, run_test as rt
t0=time.time()
frozen = json.loads((MAIN.parent/"frozen.json").read_text())
seasons = ["2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
D = rs.load_stage(seasons)
pred = np.full(len(D), np.nan)
for s in seasons:
    m=(D.season==s).values
    pred[m]=rt.season_predictions(frozen, D[m], s, ["E1"])["E1"]
D["pred"]=pred
D.to_parquet(HERE/"_cache"/"e1_team_preds.parquet")
for name,ss in (("val",seasons[:3]),("test",seasons[3:])):
    d=D[D.season.isin(ss)]
    m=M.game_metrics(d.game_id.values,d.home.values,d.pts.values,d.pred.values)
    print(name,m["n_games"],repr(m["total_mae"]),m["bias"])
print(time.time()-t0)
