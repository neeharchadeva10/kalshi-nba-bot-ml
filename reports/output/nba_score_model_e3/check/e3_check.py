"""Independent re-computation of the E3 features (research #64 double check 1).
Written from PREREGISTRATION.md (#64, #62 + amendments) and FEATURES_E3.md only. Does not read main/ or any e3 parquet.
Reuses the #62 checker (nba_score_model_e2/check/check_features.py) for box loading, report loading, matching.
No outcomes (points of the target game) are analysed; box points before the game date are inputs only.
"""
import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\Jarvis\Documents\Kalshi-PM")
E2CHK = ROOT / "reports" / "output" / "nba_score_model_e2" / "check"
E3 = ROOT / "reports" / "output" / "nba_score_model_e3"
OUT = E3 / "check"
sys.path.insert(0, str(E2CHK))
import check_features as cf  # noqa: E402

cf.OUT = OUT  # creation_cache.json etc. go to the e3 check folder
ROT = 10.0
STAR, STARTER = 28.0, 20.0
VET, YOUNG = 32, 23
CORE = ROOT / "data" / "raw" / "nba_open" / "hoopr_extra" / "player_core"
MOVES = ROOT / "data" / "derived" / "player_moves.csv"
HOOPR2K = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
BASES = ["pts", "min", "reb", "ast", "tpm", "dfn", "usg", "pm", "tier_star", "tier_starter", "tier_bench",
         "age_vet", "age_young", "q_pts", "q_min", "new_share", "gone_share"]


def kcode(t):
    return HOOPR2K.get(t, t)


def parse_pm(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return np.nan
    try:
        return float(str(x).replace("+", "").strip())
    except ValueError:
        return np.nan


def stat_vector(w):
    """per-game means over window rows w (DataFrame of played rows)"""
    usg = w.field_goals_attempted + 0.44 * w.free_throws_attempted + w.turnovers
    return dict(pts=w.points.mean(), mpg=w.minutes.mean(), reb=w.rebounds.mean(), ast=w.assists.mean(),
                tpm=w.three_point_field_goals_made.mean(), dfn=(w.steals + w.blocks).mean(), usg=usg.mean(),
                pm=w.pm.mean())


def load_dob():
    dob = {}
    for y in range(2021, 2027):
        p = pd.read_parquet(CORE / f"player_core_{y}.parquet", columns=["athlete_id", "date_of_birth"])
        for a, d in zip(p.athlete_id, p.date_of_birth):
            if isinstance(d, str) and len(d) >= 10:
                dob[int(a)] = pd.Timestamp(d[:10])  # later year overrides earlier
    return dob


def age_on(dob, date):
    """completed years on `date`; None if unknown or outside 18-45"""
    if dob is None:
        return None
    a = date.year - dob.year - ((date.month, date.day) < (dob.month, dob.day))
    return a if 18 <= a <= 45 else None


def window(calc, aid, season, before):
    """player's window rows: >=3 games this season -> last <=10 this season; else last <=10 last season; else None"""
    g = calc.played_by_ath.get(aid)
    if g is None:
        return None
    g = g[g.etdate < before]
    cur = g[g.season == season]
    if len(cur) >= 3:
        return cur.tail(10)
    prev = g[g.season == season - 1]
    if len(prev):
        return prev.tail(10)
    return None


class E3Calc:
    def __init__(self):
        pb, tb = cf.load_box()
        self.n30 = cf.nba30(tb)
        pb = pb[pb.team_abbreviation.isin(self.n30)].copy()
        pb["pm"] = pb.plus_minus.map(parse_pm)
        self.tb = tb
        self.pb = pb
        self.tg = cf.team_games(tb, self.n30)
        self.reps = cf.load_reports()
        self.calc = cf.Calc(pb, tb, self.n30, self.reps)
        self.dob = load_dob()
        mv = pd.read_csv(MOVES)
        mv["first_date_new"] = pd.to_datetime(mv.first_date_new)
        self.moves = mv.sort_values(["first_date_new"], kind="stable").reset_index(drop=True)
        self.moves_by_ath = {k: g for k, g in self.moves.groupby("athlete_id")}
        self.moves_to = {k: g for k, g in self.moves.groupby("to_team")}
        self.moves_from = {k: g for k, g in self.moves.groupby("from_team")}

    # ---------------------------------------------------------------- report-based families
    def report_fams(self, team, opp, season, etdate, D):
        c = self.calc
        res = {b: 0.0 for b in BASES if b not in ("new_share", "gone_share")}
        info = dict(n_unmatched=0, n_age_unknown=0, n_age_known=0, n_out_rot=0, n_q_rot=0, report_found=False)
        ri = c.find_report({team, opp}, etdate, D)
        if ri < 0:
            return res, info
        info["report_found"] = True
        rows, state = c.report_rows(ri, etdate, team)
        for name, st in rows.items():
            if st not in cf.OUT_STATUS and st not in cf.Q_STATUS:
                continue
            aid = c.match(name, team, season, etdate)
            if aid is None:
                info["n_unmatched"] += 1
                continue
            w = window(c, aid, season, etdate)
            if w is None:
                continue
            v = stat_vector(w)
            if v["mpg"] < ROT:
                continue
            if st in cf.Q_STATUS:
                res["q_pts"] += v["pts"]
                res["q_min"] += v["mpg"] / 240.0
                info["n_q_rot"] += 1
                continue
            info["n_out_rot"] += 1
            res["pts"] += v["pts"]
            res["min"] += v["mpg"] / 240.0
            res["reb"] += v["reb"]
            res["ast"] += v["ast"]
            res["tpm"] += v["tpm"]
            res["dfn"] += v["dfn"]
            res["usg"] += v["usg"]
            res["pm"] += v["pm"]
            if v["mpg"] >= STAR:
                res["tier_star"] += v["pts"]
            elif v["mpg"] >= STARTER:
                res["tier_starter"] += v["pts"]
            else:
                res["tier_bench"] += v["pts"]
            age = age_on(self.dob.get(int(aid)), etdate)
            if age is None:
                info["n_age_unknown"] += 1
            else:
                info["n_age_known"] += 1
                if age >= VET:
                    res["age_vet"] += v["mpg"]
                if age <= YOUNG:
                    res["age_young"] += v["mpg"]
        return res, info

    # ---------------------------------------------------------------- NEW family
    def new_fams(self, team, season, etdate):
        k = kcode(team)
        new_share = 0.0
        g = self.pb[(self.pb.team_abbreviation == team) & (self.pb.season == season) & (self.pb.etdate < etdate)]
        gids = g.drop_duplicates("game_id").sort_values(["tip", "game_id"]).tail(10).game_id
        if len(gids):
            gm = g[g.game_id.isin(set(gids)) & g.played]
            tot = gm.minutes.sum()
            mt = self.moves_to.get(k)
            if mt is not None and tot > 0:
                ids = set(mt[(mt.season_new == season) & (mt.first_date_new < etdate)].athlete_id)
                new_share = gm[gm.athlete_id.isin(ids)].minutes.sum() / tot
        gone_share = 0.0
        ly = self.pb[(self.pb.team_abbreviation == team) & (self.pb.season == season - 1) & self.pb.played]
        tot = ly.minutes.sum()
        mf = self.moves_from.get(k)
        if tot > 0 and mf is not None:
            cand = set(mf[mf.first_date_new < etdate].athlete_id)
            gone = set()
            for a in cand:
                ms = self.moves_by_ath[a]
                ms = ms[ms.first_date_new < etdate]
                if ms.iloc[-1].to_team != k:  # latest visible move (stable order by date)
                    gone.add(a)
            gone_share = ly[ly.athlete_id.isin(gone)].minutes.sum() / tot
        return new_share, gone_share

    def features_for(self, team, opp, season, etdate, D):
        r, info = self.report_fams(team, opp, season, etdate, D)
        ns, gs = self.new_fams(team, season, etdate)
        r["new_share"], r["gone_share"] = ns, gs
        return r, info


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    e = E3Calc()
    sample = pd.read_csv(E3 / "sample_ids.csv", dtype={"game_id": str})
    gi = e.tg.set_index(["game_id", "team"])
    rows, miss = [], 0
    for gid, team, tk in zip(sample.game_id, sample.team, sample.team_k):
        if (gid, team) not in gi.index:
            miss += 1
            continue
        r = gi.loc[(gid, team)]
        season, etdate, tip, opp = int(r.season), r.etdate, r.tip, r.opp
        row = dict(game_id=int(gid), season=f"{season - 1}-{str(season)[2:]}", date=str(etdate.date()), team=team,
                   opp=opp, team_k=tk, opp_k=kcode(opp), tip=int(tip.tz_convert("UTC").timestamp()))
        row["D60"], row["D20"] = row["tip"] - 3600, row["tip"] - 1200
        for cname, mins in (("c60", 60), ("c20", 20)):
            D = tip - pd.Timedelta(minutes=mins)
            for side, tm, op in (("own", team, opp), ("opp", opp, team)):
                f, info = e.features_for(tm, op, season, etdate, D)
                for b in BASES:
                    row[f"{b}_{side}_{cname}"] = float(f[b])
                row[f"report_found_{side}_{cname}"] = info["report_found"]
                row[f"n_unmatched_{side}_{cname}"] = info["n_unmatched"]
                row[f"n_out_rot_{side}_{cname}"] = info["n_out_rot"]
                row[f"n_q_rot_{side}_{cname}"] = info["n_q_rot"]
                row[f"n_age_known_{side}_{cname}"] = info["n_age_known"]
                row[f"n_age_unknown_{side}_{cname}"] = info["n_age_unknown"]
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_parquet(OUT / "e3_features_check.parquet", index=False)
    st = dict(n=len(df), missing=miss, **cf.LOAD_STATS)
    json.dump(st, open(OUT / "run_stats.json", "w"), default=str, indent=1)
    print(st)


if __name__ == "__main__":
    main()
