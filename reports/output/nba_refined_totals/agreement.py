import json, numpy as np, pandas as pd
from pathlib import Path
O = Path(r"C:\Users\Jarvis\Documents\Kalshi-PM\reports\output\nba_refined_totals")
out = {}
# ---- B features
m = pd.read_parquet(O / "main_out/b_features.parquet"); c = pd.read_csv(O / "check/out/b_check.csv")
m["game_id"] = m.game_id.astype(str); c["game_id"] = c.game_id.astype(str)
MAP = {"SA": "SAS", "UTAH": "UTA", "GS": "GSW", "NY": "NYK", "NO": "NOP", "WSH": "WAS"}
c["tk"] = c.team_abbreviation.map(lambda t: MAP.get(t, t)); m["tk"] = m.team.map(lambda t: MAP.get(t, t))
j = c.merge(m, on=["game_id", "tk"], how="left", suffixes=("_c", "_m"))
print("b rows", len(c), "matched", j.team.notna().sum())
def ids_main(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return ""
    if isinstance(v, str) and not v.strip(): return ""
    L = json.loads(v) if isinstance(v, str) else list(v)
    ids = []
    for x in L:
        ids.append(str(int(x["athlete_id"])) if isinstance(x, dict) else str(int(x)))
    return ";".join(sorted(ids, key=int))
bad = []
for col in ("S_report_M20", "S_report_M28", "S_box_M20", "S_box_M28"):
    a = j[col + "_c"] if col + "_c" in j else j[col]
    b = j[col + "_m"] if col + "_m" in j else None
    a = pd.to_numeric(j[col + "_c"]); b = pd.to_numeric(j[col + "_m"])
    both_nan = a.isna() & b.isna()
    d = (a - b).abs()
    nd = int(((d > 1e-9) | (a.isna() ^ b.isna())).sum())
    print(col, "disagree", nd, "max diff", float(d.max()) if d.notna().any() else None, "both-NaN", int(both_nan.sum()))
    if nd: bad.append(j.loc[(d > 1e-9) | (a.isna() ^ b.isna()), ["game_id", "team_abbreviation", "season_file", col + "_c", col + "_m"]])
for fl, M in (("report", 20), ("report", 28), ("box", 20), ("box", 28)):
    mc = f"abs_{fl}_M{M}"
    im = j[mc].map(ids_main) if mc in j else None
    ic = j[f"ids_{fl}_M{M}"].fillna("").astype(str).map(lambda s: ";".join(sorted([x.split('.')[0] for x in s.split(";") if x], key=int)))
    nd = int((im != ic).sum()); print("ids", fl, M, "disagree", nd)
    if nd: print(j.loc[im != ic, ["game_id", "team_abbreviation", "season_file"]].assign(main=im[im != ic], check=ic[im != ic]).head(8).to_string())
for b_ in bad: print(b_.head(10).to_string())
# ---- flags
fm = pd.read_parquet(O / "main_out/flags.parquet"); fc = pd.read_csv(O / "check/out/flags_check.csv")
fm["game_id"] = fm.game_id.astype(str); fc["game_id"] = fc.game_id.astype(str)
jf = fc.merge(fm, on="game_id", suffixes=("_c", "_m"))
print("flags matched", len(jf), "of", len(fc), {k: int((jf[k + "_c"].astype(bool) != jf[k + "_m"].astype(bool)).sum()) for k in ("q_skip", "new_skip", "early_skip")})
# ---- lean
lm = pd.read_parquet(O / "main_out/lean.parquet"); lc = pd.read_csv(O / "check/out/lean_check.csv")
lm["d"] = pd.to_datetime(lm.et_date).dt.strftime("%Y-%m-%d"); lc["d"] = pd.to_datetime(lc.game_date).dt.strftime("%Y-%m-%d")
lc["season"] = lc.season_file.map(lambda f: f"{f-1}-{str(f)[2:]}")
jl = lc.merge(lm, on=["season", "model", "d"], how="left", suffixes=("_c", "_m"))
dd = (jl.b_c - jl.b_m).abs()
print("lean rows", len(lc), "matched", int(jl.b_m.notna().sum()), "max |b diff|", float(dd.max()), "n disagree", int((jl.n - jl.n_games_before).abs().gt(0).sum()))
if dd.max() > 1e-9: print(jl[dd > 1e-9].head(10).to_string())
# ---- versions
vm = pd.read_csv(O / "main_out/versions_sample_main.csv"); vc = pd.concat([pd.read_csv(O / "check/out/versions_check.csv"), pd.read_csv(O / "check/out/versions_check_B.csv")])
vc = vc.rename(columns={"bets": "n"})
jv = vc.merge(vm, on="version", suffixes=("_c", "_m"))
jv["ok"] = (jv.n_c == jv.n_m) & (jv.wins_c == jv.wins_m) & (jv.losses_c == jv.losses_m) & (jv.pushes_c == jv.pushes_m) & ((jv.mean_pnl_c - jv.mean_pnl_m).abs() < 1e-9)
print(jv[["version", "n_c", "n_m", "wins_c", "wins_m", "mean_pnl_c", "mean_pnl_m", "ok"]].to_string())
