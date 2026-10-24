"""Independent re-computation of the E2 injury features (research #62 double check 1).
Written from PREREGISTRATION.md only. No shared code with main/. No outcomes are analysed.
"""
import json
import re
import unicodedata
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"%USERPROFILE%\Documents\Kalshi-PM")
RAW = ROOT / "data" / "raw" / "nba_open"
PDFDIR = ROOT / "data" / "raw" / "nba_injury"
INJ_TM = ROOT / "reports" / "output" / "nba_teammate_out" / "injury_parsed_tm.jsonl"
INJ = ROOT / "reports" / "output" / "nba_model" / "injury_parsed_v3.json"
OUT = ROOT / "reports" / "output" / "nba_score_model_e2" / "check"
ET = "America/New_York"
SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}
OUT_STATUS = {"out", "doubtful"}
Q_STATUS = {"questionable"}
ROT_MIN = 15.0
NBA30 = None  # filled at load


# ---------------------------------------------------------------- names
def strip_accents(s):
    s = s.translate(str.maketrans({"\u00f8": "o", "\u00d8": "O", "\u0111": "d", "\u0110": "D", "\u0142": "l", "\u0141": "L"}))
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def alnum(s):
    return re.sub(r"[^a-z0-9]", "", strip_accents(s).lower())


def key_hoopr(display):
    if not isinstance(display, str):
        return ""
    toks = [t for t in re.split(r"\s+", display.strip()) if t]
    while len(toks) > 1 and alnum(toks[-1]) in SUFFIX:
        toks.pop()
    return alnum("".join(toks))


def report_name_parts(name):
    """'BagleyIII,Marvin' -> ('bagley','marvin'); suffix glued to last name is removed."""
    last, _, first = name.partition(",")
    last = alnum(last)
    changed = True
    while changed:
        changed = False
        for suf in ("iii", "jr", "sr", "iv", "ii"):
            if last.endswith(suf) and len(last) > len(suf) + 1:
                last = last[: -len(suf)]
                changed = True
                break
    return last, alnum(first)


def key_report(name):
    last, first = report_name_parts(name)
    return first + last


# ---------------------------------------------------------------- reports
def parse_pub_time(fname):
    m = re.match(r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})(?:_(\d{2}))?(AM|PM)\.pdf$", fname)
    if not m:
        raise ValueError(fname)
    d, hh, mm, ap = m.group(1), int(m.group(2)), int(m.group(3) or 0), m.group(4)
    hh = hh % 12 + (12 if ap == "PM" else 0)
    ts = pd.Timestamp(f"{d} {hh:02d}:{mm:02d}:00")
    return ts.tz_localize(ET, ambiguous=False, nonexistent="shift_forward")


def pdf_creation_utc(path):
    """/CreationDate of the PDF (raw bytes, no PDF library): D:YYYYMMDDHHMMSS+hh'mm' -> UTC Timestamp, or None"""
    try:
        b = open(path, "rb").read()
    except OSError:
        return None
    m = re.search(rb"/CreationDate\s*\(D:(\d{14})(?:([+-])(\d{2})'?(\d{2})?'?|Z)?", b)
    if not m:
        return None
    ts = pd.Timestamp(pd.to_datetime(m.group(1).decode(), format="%Y%m%d%H%M%S"))
    if m.group(2):
        off = pd.Timedelta(hours=int(m.group(3)), minutes=int(m.group(4) or 0))
        ts = ts - off if m.group(2) == b"+" else ts + off
    return ts.tz_localize("UTC")


LOAD_STATS = {}


def load_reports():
    d = json.load(open(INJ))
    n_v3 = len(d)
    for line in open(INJ_TM):
        if line.strip():
            r = json.loads(line)
            d.setdefault(r["name"], r)  # dedup by PDF name (v3 wins on a clash)
    LOAD_STATS["n_reports_v3"] = n_v3
    cache_f = OUT / "creation_cache.json"
    cache = json.load(open(cache_f)) if cache_f.exists() else {}
    reps, skipped = [], []
    for k, v in d.items():
        if k not in cache:
            t = pdf_creation_utc(PDFDIR / k)
            cache[k] = None if t is None else t.isoformat()
        if cache[k] is None:
            skipped.append(k)
            continue
        reps.append((pd.Timestamp(cache[k]), k, v))
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(cache, open(cache_f, "w"))
    LOAD_STATS.update(n_reports_union=len(d), n_skipped_no_creationdate=len(skipped), skipped_examples=skipped[:5])
    reps.sort(key=lambda x: (x[0], x[1]))
    return reps


def latest_report_idx(pubs, D):
    """index of the latest report created at or before D (pubs sorted ascending UTC), or -1"""
    return int(np.searchsorted(pubs, D.tz_convert("UTC"), side="right")) - 1


def load_box():
    pb = pd.concat([pd.read_parquet(RAW / f"player_box_{y}.parquet") for y in range(2021, 2027)], ignore_index=True)
    pb = pb[(pb.season_type == 2) & pb.athlete_id.notna()].copy()
    pb["tip"] = pd.to_datetime(pb.game_date_time, utc=True).dt.tz_convert(ET)
    pb["etdate"] = pb.tip.dt.tz_localize(None).dt.normalize()
    pb["played"] = (~pb.did_not_play.astype(bool)) & (pb.minutes.fillna(0) > 0)
    tb = pd.concat([pd.read_parquet(RAW / f"team_box_{y}.parquet") for y in range(2022, 2027)], ignore_index=True)
    tb = tb[tb.season_type == 2].copy()
    tb["tip"] = pd.to_datetime(tb.game_date_time, utc=True).dt.tz_convert(ET)
    tb["etdate"] = tb.tip.dt.tz_localize(None).dt.normalize()
    return pb, tb


def nba30(tb):
    # the 30 franchises = abbreviations that appear in >= 60 games every season (excludes All-Star etc.)
    c = tb.groupby(["season", "team_abbreviation"]).size().unstack(0).fillna(0)
    return set(c.index[(c >= 60).all(axis=1)])


def team_games(tb, n30):
    t = tb[tb.team_abbreviation.isin(n30) & tb.opponent_team_abbreviation.isin(n30)]
    t = t[["season", "game_date", "game_id", "team_abbreviation", "opponent_team_abbreviation", "tip", "etdate"]]
    t = t.rename(columns={"team_abbreviation": "team", "opponent_team_abbreviation": "opp"})
    t["game_date"] = t.game_date.astype(str)
    t["game_id"] = t.game_id.astype(str)
    return t.sort_values(["season", "game_date", "game_id", "team"]).reset_index(drop=True)


def draw_sample(tg):
    f = ROOT / "reports" / "output" / "nba_score_model_e2" / "sample_ids.csv"
    if f.exists():
        s = pd.read_csv(f, dtype={"game_id": str})
        return s, "sample_ids.csv"
    idx = np.random.default_rng(20261024).choice(len(tg), 200, replace=False)
    s = tg.iloc[idx][["game_id", "team"]].copy()
    return s, "own draw"


# ---------------------------------------------------------------- features
class Calc:
    def __init__(self, pb, tb, n30, reps):
        self.pb = pb
        self.n30 = n30
        self.reps = reps
        self.pubs = pd.DatetimeIndex([r[0] for r in reps], tz="UTC") if not reps else pd.DatetimeIndex([r[0] for r in reps]).tz_convert("UTC")
        loc = tb.drop_duplicates("team_abbreviation")
        self.full2abbr = {alnum(a + b): c for a, b, c in zip(loc.team_location, loc.team_name, loc.team_abbreviation)}
        self.full2abbr.update({alnum(a): c for a, c in zip(loc.team_display_name, loc.team_abbreviation)})
        pl = pb[pb.played].sort_values(["tip", "game_id"])
        self.played_by_ath = {k: g for k, g in pl.groupby("athlete_id")}
        self.name_of = pb.drop_duplicates("athlete_id").set_index("athlete_id").athlete_display_name.to_dict()
        self.pb_by_team_season = {k: g for k, g in pb.groupby(["team_abbreviation", "season"])}
        self.tg_by_team_season = {k: g for k, g in tb[tb.team_abbreviation.isin(n30)].groupby(["team_abbreviation", "season"])}
        self.pb_sorted = pb.sort_values(["tip", "game_id"])
        self.unmatched = defaultdict(int)
        self.match_tier = defaultdict(int)
        self.report_cache = {}
        self.lists_cache = {}
        self.latest_cache = {}

    # does report ri list the game (either team of the game with that ET date, in rows or nys)?
    def lists_game(self, ri, etdate, teams):
        ck = (ri, etdate, tuple(sorted(teams)))
        if ck in self.lists_cache:
            return self.lists_cache[ck]
        _, _, rep = self.reps[ri]
        ds = etdate.strftime("%m/%d/%Y")
        ok = False
        for r in rep["rows"]:
            if r["date"] == ds and self.full2abbr.get(alnum(r["team"])) in teams:
                ok = True
                break
        if not ok:
            for n in rep.get("nys", []):
                if n[0] == ds and self.full2abbr.get(alnum(n[2])) in teams:
                    ok = True
                    break
        self.lists_cache[ck] = ok
        return ok

    def find_report(self, teams, etdate, D):
        """latest report with CreationDate <= D that lists the game; look back at most 72 h; else -1"""
        i = latest_report_idx(self.pubs, D)
        lo = D.tz_convert("UTC") - pd.Timedelta(hours=72)
        while i >= 0 and self.pubs[i] >= lo:
            if self.lists_game(i, etdate, teams):
                return i
            i -= 1
        return -1

    def report_rows(self, ri, etdate, team):
        ck = (ri, etdate, team)
        if ck in self.report_cache:
            return self.report_cache[ck]
        _, _, rep = self.reps[ri]
        ds = etdate.strftime("%m/%d/%Y")
        rank = {"out": 3, "doubtful": 2, "questionable": 1}
        got = {}
        for r in rep["rows"]:
            if r["date"] == ds and self.full2abbr.get(alnum(r["team"])) == team and r["player"]:
                st = r["status"].strip().lower()
                prev = got.get(r["player"])
                if prev is None or rank.get(st, 0) > rank.get(prev, 0):
                    got[r["player"]] = st
        state = "rows" if got else "norows"
        if not got:
            for n in rep.get("nys", []):
                if n[0] == ds and self.full2abbr.get(alnum(n[2])) == team:
                    state = "nys"
        self.report_cache[ck] = (got, state)
        return got, state

    # X players: team of the most recent regular-season box row (played or not) strictly before the
    # ET date, this season else last season
    def latest_team(self, season, before):
        ck = (season, before)
        if ck in self.latest_cache:
            return self.latest_cache[ck]
        x = self.pb_sorted
        x = x[(x.season.isin([season, season - 1])) & (x.etdate < before)]
        last = x.groupby("athlete_id").team_abbreviation.last()
        out = defaultdict(set)
        for a, t in last.items():
            out[t].add(a)
        self.latest_cache[ck] = out
        return out

    def match(self, name, team, season, before):
        rk = key_report(name)
        last, first = report_name_parts(name)
        pool = self.latest_team(season, before).get(team, set())
        keys = defaultdict(set)
        for a in pool:
            keys[key_hoopr(self.name_of[a])].add(a)
        if len(keys.get(rk, ())) == 1:
            self.match_tier["exact_team"] += 1
            return next(iter(keys[rk]))
        self.match_tier["unmatched"] += 1
        return None

    def rates(self, aid, season, before, prev_full=False):
        g = self.played_by_ath.get(aid)
        if g is None:
            return None
        g = g[g.etdate < before]
        cur = g[g.season == season]
        if len(cur) >= 3:
            last10 = cur.tail(10)
            return float(last10.minutes.mean()), float(last10.points.mean()), "cur"
        prev = g[g.season == season - 1]
        if len(prev):
            if not prev_full:
                prev = prev.tail(10)
            return float(prev.minutes.mean()), float(prev.points.mean()), "prev"
        return None

    def team_feats(self, team, season, etdate, D, opp=None, prev_full=False):
        res = dict(out_pts=0.0, q_pts=0.0, back_pts=0.0, out_min=0.0)
        info = dict(report_file=None, report_state="no_report", n_unmatched=0, n_listed_out=0, n_listed_q=0,
                    used_prev_fallback=0)
        teams = {team} | ({opp} if opp else set())
        ri = self.find_report(teams, etdate, D)
        listed_out_ids = set()
        if ri >= 0:
            info["report_file"] = self.reps[ri][1]
            rows, state = self.report_rows(ri, etdate, team)
            info["report_state"] = state
            for name, st in rows.items():
                if st not in OUT_STATUS and st not in Q_STATUS:
                    continue
                aid = self.match(name, team, season, etdate)
                if aid is None:
                    info["n_unmatched"] += 1
                    self.unmatched[season] += 1
                    continue
                rt = self.rates(aid, season, etdate, prev_full)
                if rt is None or rt[0] < ROT_MIN:
                    continue
                mpg, ppg, src = rt
                info["used_prev_fallback"] += src == "prev"
                if st in OUT_STATUS:
                    res["out_pts"] += ppg
                    res["out_min"] += mpg / 240.0
                    listed_out_ids.add(aid)
                    info["n_listed_out"] += 1
                else:
                    res["q_pts"] += ppg
                    info["n_listed_q"] += 1
        # back_pts: current season only; 0 until X has played 10 games this season. Candidates = players with
        # minutes > 0 for X at least once this season before the date.
        back = 0.0
        tls = self.tg_by_team_season.get((team, season))
        if tls is not None:
            w = tls[tls.etdate < etdate].sort_values(["tip", "game_id"]).tail(10)
            if len(w) == 10:
                pbt = self.pb_by_team_season[(team, season)]
                pbt = pbt[pbt.etdate < etdate]
                wins = set(w.game_id)
                cands = set(pbt[pbt.played].athlete_id) & self.latest_team(season, etdate).get(team, set())
                cnt = pbt[pbt.played & pbt.game_id.isin(wins)].groupby("athlete_id").size()
                for aid in cands:
                    if aid in listed_out_ids or cnt.get(aid, 0) >= 5:
                        continue
                    rt = self.rates(aid, season, etdate, prev_full)
                    if rt is None or rt[0] < ROT_MIN:
                        continue
                    back += rt[1]
        res["back_pts_norowsok"] = back  # alternative: back_pts even when the team has no listed rows
        # primary (amendment item 2): a team with no rows (or nys / no report) has all four features 0
        res["back_pts"] = back if info["report_state"] == "rows" else 0.0
        return res, info


FEATS = ["out_pts", "q_pts", "back_pts", "out_min"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pb, tb = load_box()
    n30 = nba30(tb)
    pb = pb[pb.team_abbreviation.isin(n30)].copy()  # All-Star / exhibition teams excluded everywhere
    tg = team_games(tb, n30)
    sample, how = draw_sample(tg)
    sample["game_id"] = sample.game_id.astype(str)
    reps = load_reports()
    calc = Calc(pb, tb, n30, reps)
    gi = tg.set_index(["game_id", "team"])
    rows = []
    missing = 0
    for gid, team in zip(sample.game_id, sample.team):
        if (gid, team) not in gi.index:
            missing += 1
            continue
        r = gi.loc[(gid, team)]
        season, etdate, tip, opp = int(r.season), r.etdate, r.tip, r.opp
        D = tip - pd.Timedelta(minutes=60)
        row = dict(game_id=int(gid), team=team, opp=opp, season=f"{season - 1}-{str(season)[2:]}",
                   date=str(etdate.date()), tip=str(tip), D=str(D))
        for suffix, tm in (("own", team), ("opp", opp)):
            f, info = calc.team_feats(tm, season, etdate, D, opp=(opp if tm == team else team))
            fa, _ = calc.team_feats(tm, season, etdate, D, opp=(opp if tm == team else team), prev_full=True)
            for k in FEATS:
                row[f"{k}_{suffix}"] = f[k]
                row[f"{k}_{suffix}_prevfull"] = fa[k]
            row[f"back_pts_{suffix}_norowsok"] = f["back_pts_norowsok"]
            row[f"report_name_{suffix}"] = info["report_file"]
            row[f"report_state_{suffix}"] = info["report_state"]
            row[f"nys_{suffix}"] = info["report_state"] == "nys"
            row[f"n_unmatched_{suffix}"] = info["n_unmatched"]
            row[f"n_listed_out_{suffix}"] = info["n_listed_out"]
            row[f"n_listed_q_{suffix}"] = info["n_listed_q"]
            row[f"used_prev_fallback_{suffix}"] = info["used_prev_fallback"]
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_parquet(OUT / "injury_features_check.parquet", index=False)
    stats = dict(sample_source=how, n_sample=len(df), missing_from_my_team_game_list=missing,
                 n_team_games_mine=len(tg), unmatched_by_season=dict(calc.unmatched),
                 match_tiers=dict(calc.match_tier), n30=len(n30), **LOAD_STATS)
    json.dump(stats, open(OUT / "run_stats.json", "w"), default=str, indent=1)
    print(stats)




DEBUG = [("OKC", "401469054"), ("BOS", "401469076"), ("MEM", "401469151"), ("ATL", "401585449"),
         ("MIA", "401810778"), ("DAL", "401810914"), ("ORL", "401810894"), ("LAL", "401360764")]


def window_debug():
    pb, tb = load_box()
    n30 = nba30(tb)
    pb = pb[pb.team_abbreviation.isin(n30)].copy()
    tg = team_games(tb, n30)
    calc = Calc(pb, tb, n30, load_reports())
    gi = tg.set_index(["game_id", "team"])
    out = []
    for team, gid in DEBUG:
        r = gi.loc[(gid, team)]
        season, etdate, opp = int(r.season), r.etdate, r.opp
        D = r.tip - pd.Timedelta(minutes=60)
        ri = calc.find_report({team, opp}, etdate, D)
        if ri < 0:
            out.append(dict(game_id=gid, team=team, player="(no report)"))
            continue
        rows, state = calc.report_rows(ri, etdate, team)
        for name, st in rows.items():
            base = dict(game_id=gid, team=team, opp=opp, et_date=etdate.date(), report=calc.reps[ri][1],
                        report_state=state, player=name, status=st)
            aid = calc.match(name, team, season, etdate) if st in ("out", "doubtful", "questionable") else None
            if aid is None:
                out.append(dict(base, matched=False))
                continue
            g = calc.played_by_ath[aid] if aid in calc.played_by_ath else pd.DataFrame()
            g = g[g.etdate < etdate] if len(g) else g
            cur = g[g.season == season] if len(g) else g
            if len(cur) >= 3:
                w, src = cur.tail(10), "current"
            else:
                w, src = g[g.season == season - 1].tail(10) if len(g) else g, "last_season"
            base.update(matched=True, hoopr_name=calc.name_of[aid], athlete_id=aid, window_source=src, n_games=len(w))
            if len(w):
                base.update(window_dates=";".join(str(x.date()) for x in w.etdate),
                            window_game_ids=";".join(str(x) for x in w.game_id),
                            window_teams=";".join(w.team_abbreviation),
                            window_minutes=";".join(f"{x:g}" for x in w.minutes),
                            window_points=";".join(f"{x:g}" for x in w.points),
                            mpg=float(w.minutes.mean()), ppg=float(w.points.mean()),
                            rotation=bool(w.minutes.mean() >= ROT_MIN))
            out.append(base)
    pd.DataFrame(out).to_csv(OUT / "window_debug.csv", index=False)
    print(len(out), "debug rows")


if __name__ == "__main__":
    main()
    window_debug()
