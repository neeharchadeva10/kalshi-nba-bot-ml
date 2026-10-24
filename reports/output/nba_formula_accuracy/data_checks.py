"""Research log #56 data checks (the 6 checks of PREREGISTRATION.md).  Writes data_checks.json and DATA_CHECKS.md.
READ-ONLY: local files only (the NBA.com cross-check reads the cache written by nba_official.py; this script itself
makes no request).  No outcome of any formula is looked at here: the checks compare data sources with each other.

  .venv/Scripts/python.exe reports/output/nba_formula_accuracy/data_checks.py"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fa_lib as fa  # noqa: E402
import grid_lib as gl  # noqa: E402
import nba_official as no  # noqa: E402
from fa_lib import lib, r2  # noqa: E402

ROOT = fa.ROOT
ET = ZoneInfo("America/New_York")
YEARS = (2022, 2023, 2024, 2025, 2026, 2027)
NICK = {"Hawks": "ATL", "Bobcats": "CHA", "Nets": "BKN", "Celtics": "BOS", "Hornets": "CHA", "Bulls": "CHI", "Cavaliers": "CLE",
        "Mavericks": "DAL", "Nuggets": "DEN", "Pistons": "DET", "Warriors": "GSW", "Rockets": "HOU", "Pacers": "IND",
        "Clippers": "LAC", "Lakers": "LAL", "Grizzlies": "MEM", "Heat": "MIA", "Bucks": "MIL", "Timberwolves": "MIN",
        "Pelicans": "NOP", "Knicks": "NYK", "Thunder": "OKC", "Magic": "ORL", "76ers": "PHI", "Suns": "PHX",
        "Trail Blazers": "POR", "Kings": "SAC", "Spurs": "SAS", "Raptors": "TOR", "Jazz": "UTA", "Wizards": "WAS"}


def code_of(name):
    for k, v in NICK.items():
        if str(name).endswith(k):
            return v
    return None


def all_games(year):
    """hoopR games of one file year, ALL season types: game_id, date (ET), tip, away, home, total, season_type."""
    tb = fa.read_box(year)
    tg = gl.team_games_g(tb, (1, 2, 3, 4, 5))
    a = tg[tg.venue == "away"].drop_duplicates("game_id")
    return pd.DataFrame({"game_id": a.game_id.values, "date": a.date.values, "tip": a.tip.values, "away": a.team.values,
                         "home": a.opp.values, "total": (a.pts + a.opp_pts).values, "season_type": a.season_type.values,
                         "year": year})


# ------------------------------------------------------------------ 1
def check1_finals():
    out = {}
    h = pd.concat([prep_hoopr(2026), prep_hoopr(2027)], ignore_index=True)
    conn = sqlite3.connect(f"file:{(ROOT / 'reports/output/nba_totals_pace/totals.sqlite').as_posix()}?mode=ro", uri=True)
    mk = pd.read_sql("SELECT ticker,event_ticker,status,result,expiration_value,floor_strike FROM markets", conn)
    mk["exp"] = mk.expiration_value.map(lib.parse_expiration_value)
    rows, bad_events = [], []
    for et, g in mk.groupby("event_ticker"):
        pe = lib.parse_event(et)
        if pe is None:
            bad_events.append(et)
            continue
        date, away, home = pe
        no_ = g[(g.status == "finalized") & (g.result == "no")].dropna(subset=["exp"])
        yes = g[(g.status == "finalized") & (g.result == "yes")].dropna(subset=["exp"])
        m = match_hoopr_any(h, date, away, home)
        rows.append({"event": et, "n_no": len(no_), "kalshi_final": float(no_.exp.max()) if len(no_) else np.nan,
                     "no_values_distinct": int(no_.exp.nunique()), "yes_exp_above_final": int((yes.exp > no_.exp.max()).sum()) if len(no_) else 0,
                     "hoopr_total": float(m.total) if m is not None else np.nan, "hoopr_id": int(m.game_id) if m is not None else None})
    d = pd.DataFrame(rows)
    global KALSHI_ROWS
    KALSHI_ROWS = d
    comp = d.dropna(subset=["kalshi_final", "hoopr_total"])
    mism = comp[comp.kalshi_final != comp.hoopr_total]
    out["kalshi_vs_hoopr"] = {
        "events": int(len(d)), "unparsed_event_tickers": len(bad_events), "events_with_a_no_settled_strike": int((d.n_no > 0).sum()),
        "events_without_no_strike": int((d.n_no == 0).sum()), "matched_to_hoopr": int(d.hoopr_total.notna().sum()),
        "unmatched_to_hoopr": [e for e in d[d.hoopr_total.isna()].event.tolist()][:30],
        "compared": int(len(comp)), "equal": int((comp.kalshi_final == comp.hoopr_total).sum()),
        "mismatches": mism.head(40).to_dict("records"), "n_mismatches": int(len(mism)),
        "events_where_no_strikes_disagree_among_themselves": int((d.no_values_distinct > 1).sum()),
        "events_with_a_YES_value_above_final": int((d.yes_exp_above_final > 0).sum()),
        "note": "end-of-game value = max expiration_value over NO-settled strikes; YES strikes settle early at the crossing"}
    return out


KALSHI_ROWS = None


def prep_hoopr(year):
    g = all_games(year)
    g["game_date"] = pd.to_datetime(g.date).dt.strftime("%Y-%m-%d")
    return g


def match_hoopr_any(h, date, away, home):
    d0 = dt.datetime.strptime(date, "%Y-%m-%d")
    cands = {(d0 + dt.timedelta(days=k)).strftime("%Y-%m-%d"): k for k in (0, -1, 1)}
    m = h[(h.away == away) & (h.home == home) & h.game_date.isin(cands)]
    if m.empty:
        return None
    m = m.assign(_k=m.game_date.map(lambda x: abs(cands[x]))).sort_values("_k")
    return m.iloc[0]


def fix_odds_date(x):
    """OddsData raw tables store 'YYYY-YY-MMDD' (season prefix); the *_new tables store ISO dates."""
    m = re.match(r"^(\d{4})-\d{2}-(\d{2})(\d{2})$", x)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
        return f"{y if mo >= 8 else y + 1}-{mo:02d}-{d}"
    return x[:10]


def check1b_odds():
    """hoopR final vs OddsData 'Points' (private research data, read in place), by (date, home, away)."""
    conn = sqlite3.connect(f"file:{(ROOT / 'data/raw/nba_odds_samples/OddsData.sqlite').as_posix()}?mode=ro", uri=True)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    cl = pd.read_parquet(fa.CLOSING).drop_duplicates("game_id").set_index("game_id")
    res = {}
    for y in YEARS[:-1]:
        s = fa.label_of(y)
        h = prep_hoopr(y)
        h = h.assign(key=list(zip(h.game_date, h.home, h.away)))
        hk = {k: r for k, r in zip(h.key, h.itertuples())}
        for t in [t for t in tables if t.endswith(s) or t.endswith(s + "_new")]:
            od = pd.read_sql(f'SELECT * FROM "{t}"', conn)
            od["Date"] = [fix_odds_date(x) for x in od.Date.astype(str)]
            od["hc"], od["ac"] = od.Home.map(code_of), od.Away.map(code_of)
            n_match, mism, unm, ou_diff = 0, [], 0, []
            for r in od.itertuples():
                row = None
                for k in (0, -1, 1):
                    d = (dt.datetime.strptime(r.Date, "%Y-%m-%d") + dt.timedelta(days=k)).strftime("%Y-%m-%d")
                    row = hk.get((d, r.hc, r.ac))
                    if row is not None:
                        break
                if row is None:
                    unm += 1
                    continue
                n_match += 1
                if float(r.Points) != float(row.total):
                    mism.append({"date": r.Date, "home": r.hc, "away": r.ac, "odds_points": r.Points, "hoopr_total": float(row.total)})
                if row.game_id in cl.index and np.isfinite(r.OU):
                    ou_diff.append(abs(float(cl.loc[row.game_id, "over_under"]) - float(r.OU)))
            res[t] = {"season": s, "odds_rows": int(len(od)), "matched_to_hoopr": n_match, "unmatched": unm,
                      "points_mismatches": len(mism), "mismatch_examples": mism[:15],
                      "ou_vs_closing_lines": ({"n": len(ou_diff), "median_abs_diff": float(np.median(ou_diff)),
                                               "share_diff_le_1pt": float(np.mean(np.array(ou_diff) <= 1.0)),
                                               "max_abs_diff": float(np.max(ou_diff))} if ou_diff else None)}
    return res


# ------------------------------------------------------------------ 2, 3
def check2_player_sums():
    out = {}
    for y in YEARS:
        if not (fa.BOX / f"player_box_{y}.parquet").exists():
            out[fa.label_of(y)] = {"status": "no player_box file on disk"}
            continue
        tb = fa.read_box(y)[["game_id", "team_id", "season_type", "team_score", "team_abbreviation"]]
        pb = fa.read_box(y, "player")[["game_id", "team_id", "points", "did_not_play", "season_type"]]
        s = pb.groupby(["game_id", "team_id"]).points.sum(min_count=1).rename("psum").reset_index()
        m = tb.merge(s, on=["game_id", "team_id"], how="left")
        nan_pts_played = int(((pb.points.isna()) & (~pb.did_not_play.fillna(False).astype(bool))).sum())
        miss = m[m.psum.isna()]
        bad = m[m.psum.notna() & (m.psum != m.team_score)]
        out[fa.label_of(y)] = {"team_games": int(len(m)), "no_player_rows": int(len(miss)),
                               "no_player_rows_by_type": {str(k): int(v) for k, v in miss.season_type.value_counts().items()},
                               "mismatches": int(len(bad)),
                               "mismatch_by_type": {str(k): int(v) for k, v in bad.season_type.value_counts().items()},
                               "examples": bad.head(10)[["game_id", "team_abbreviation", "team_score", "psum"]].to_dict("records"),
                               "player_rows_nan_points_but_played": nan_pts_played}
    return out


def check3_ratings():
    out = {}
    for y in YEARS:
        tb = fa.read_box(y)
        reg = tb[tb.season_type == 2]
        tg = gl.team_games_g(tb, (2,))
        dropped = sorted(set(reg.game_id) - set(tg.game_id))
        rows = []
        for t, g in tg.groupby("team"):
            ortg, drtg = g.pts.sum() / g.poss.sum(), g.opp_pts.sum() / g.opp_poss.sum()
            pace = g.pace.mean()
            rows.append({"team": t, "games": len(g), "ppg": g.pts.mean(), "opp_ppg": g.opp_pts.mean(),
                         "d_off": ortg * pace - g.pts.mean(), "d_def": drtg * pace - g.opp_pts.mean(),
                         "ortg100": 100 * ortg, "drtg100": 100 * drtg, "pace": pace})
        d = pd.DataFrame(rows)
        out[fa.label_of(y)] = {"teams": int(len(d)), "games_per_team_min_max": [int(d.games.min()), int(d.games.max())],
                               "reg_season_game_ids_in_file": int(reg.game_id.nunique()),
                               "game_ids_dropped_by_team_games_g": [int(x) for x in dropped],
                               "max_abs_ortg_x_pace_minus_ppg": float(d.d_off.abs().max()),
                               "mean_abs_ortg_x_pace_minus_ppg": float(d.d_off.abs().mean()),
                               "max_abs_drtg_x_pace_minus_oppg": float(d.d_def.abs().max()),
                               "league_ppg": float(tg.pts.mean()), "league_pace": float(tg.pace.mean()),
                               "league_ortg100": float(100 * tg.pts.sum() / tg.poss.sum()),
                               "_table": d}
    return out


NBA_STATUS_FILE = HERE / "nba_official_status.json"


def check3b_nba_official(tables):
    """NBA.com cross-check from the cache only.  Our ORtg/DRtg x100 and pace vs OFF_RATING/DEF_RATING/PACE."""
    st = json.loads(NBA_STATUS_FILE.read_text()) if NBA_STATUS_FILE.exists() else {}
    out = {}
    for s in no.SEASONS:
        off = no.load_cached(s)
        if off is None:
            out[s] = {"status": "not checked", "reason": st.get(s, "no cached response")}
            continue
        d = tables[s]["_table"]
        dif = []
        for r in d.itertuples():
            nm = next((k for k in off if code_of(k) == r.team), None)
            if nm:
                dif.append((r.ortg100 - off[nm]["OFF_RATING"], r.drtg100 - off[nm]["DEF_RATING"], r.pace - off[nm]["PACE"]))
        a = np.array(dif)
        out[s] = {"status": "checked", "teams": int(len(a)),
                  "ortg_diff_mean_max_abs": [float(a[:, 0].mean()), float(np.abs(a[:, 0]).max())],
                  "drtg_diff_mean_max_abs": [float(a[:, 1].mean()), float(np.abs(a[:, 1]).max())],
                  "pace_diff_mean_max_abs": [float(a[:, 2].mean()), float(np.abs(a[:, 2]).max())]}
    return out


# ------------------------------------------------------------------ 4
def season_of_date(d):
    return fa.label_of(d.year + 1 if d.month >= 8 else d.year)


def check4_injury():
    sys.path.insert(0, str(ROOT / "reports/output/nba_model"))
    import injury_parse as IP
    raw = json.loads((ROOT / "reports/output/nba_model/injury_parsed_v3.json").read_text())
    reps, bad_header = [], 0
    for name, r in raw.items():
        m = IP.HDR_RE.search(r.get("header") or "")
        if not m:
            bad_header += 1
            continue
        pub = dt.datetime.strptime(" ".join(m.groups()), "%m/%d/%y %I:%M %p").replace(tzinfo=ET)
        keys = set()
        for x in r["rows"]:
            a, h = x["matchup"].split("@")
            keys.add((dt.datetime.strptime(x["date"], "%m/%d/%Y").strftime("%Y-%m-%d"), a, h))
        for date, mt, _ in r["nys"]:
            if date and mt:
                a, h = mt.split("@")
                keys.add((dt.datetime.strptime(date, "%m/%d/%Y").strftime("%Y-%m-%d"), a, h))
        reps.append((int(pub.timestamp()), name, keys, r["rows"]))
    reps.sort(key=lambda x: x[0])
    epochs = [x[0] for x in reps]
    by_key = {}
    for e, _, keys, _ in reps:
        for k in keys:
            by_key.setdefault(k, []).append(e)
    out = {"report_files": len(raw), "header_unparsed": bad_header, "first_report": dt.datetime.fromtimestamp(epochs[0], ET).isoformat(),
           "last_report": dt.datetime.fromtimestamp(epochs[-1], ET).isoformat(), "seasons": {}}
    for y in YEARS[:-1]:
        s = fa.label_of(y)
        g = all_games(y)
        g = g[g.season_type == 2]
        n = len(g)
        a_ok = b_ok = c_ok = 0
        age = []
        for r in g.itertuples():
            cut = int(r.tip) - 3600
            key = (pd.Timestamp(r.date).strftime("%Y-%m-%d"), r.away, r.home)
            i = bisect.bisect_right(epochs, cut)
            if i and epochs[i - 1] >= cut - 24 * 3600:
                a_ok += 1
            if i and key in reps[i - 1][2]:
                b_ok += 1
            lst = by_key.get(key, [])
            j = bisect.bisect_right(lst, cut)
            if j:
                c_ok += 1
                age.append((cut - lst[j - 1]) / 3600.0)
        # names
        pb = fa.read_box(y, "player")
        box_names = {IP.norm_box_name(x) for x in pb.athlete_display_name.dropna().unique()}
        tot, unm, un_names = 0, 0, Counter()
        for e, name, keys, rows in reps:
            if season_of_date(dt.datetime.fromtimestamp(e, ET).date()) != s:
                continue
            for x in rows:
                if x["status"] in ("Out", "Doubtful", "Questionable"):
                    tot += 1
                    nm = IP.norm_injury_name(x["player"])
                    if nm not in box_names:
                        unm += 1
                        un_names[x["player"]] += 1
        out["seasons"][s] = {"regular_season_games": n,
                             "a_any_report_published_in_the_24h_up_to_T-60": [a_ok, round(100 * a_ok / n, 2)],
                             "b_latest_report_at_or_before_T-60_lists_the_game": [b_ok, round(100 * b_ok / n, 2)],
                             "c_some_report_at_or_before_T-60_lists_the_game": [c_ok, round(100 * c_ok / n, 2)],
                             "median_hours_from_latest_listing_report_to_T-60": float(np.median(age)) if age else None,
                             "status_listings_out_doubtful_questionable": tot, "unmatched_name_listings": unm,
                             "unmatched_share_pct": round(100 * unm / max(tot, 1), 2),
                             "distinct_unmatched_names": len(un_names), "top_unmatched": un_names.most_common(12)}
    inj_dir = ROOT / "data/raw/nba_injury"
    files26 = sorted(p.name for p in inj_dir.glob("Injury-Report_2026-1*.pdf"))
    out["2026-27"] = {"pdf_files_on_disk_from_2026-10": len(files26),
                      "note": "injury_parsed_v3.json ends 2026-06-13; 2026-27 PDFs are on disk but not parsed (not needed for the "
                              "team family; the player model parses what it needs)"}
    return out


# ------------------------------------------------------------------ 5
def check5_lines():
    p = pd.read_parquet(fa.CLOSING)
    out = {"rows": int(len(p)), "duplicate_game_id_rows": int(p.game_id.duplicated().sum()),
           "nan_over_under": int(p.over_under.isna().sum()),
           "outside_180_270": int(((p.over_under < 180) | (p.over_under > 270)).sum()),
           "over_under_min_max_median": [float(p.over_under.min()), float(p.over_under.max()), float(p.over_under.median())],
           "match_method": {k: int(v) for k, v in p.match_method.value_counts().items()},
           "n_books_median_min": [float(p.n_books.median()), int(p.n_books.min())]}
    ct = pd.to_datetime(p.commence_time, utc=True)
    sn = pd.to_datetime(p.snapshot, utc=True)
    lead = (ct - sn).dt.total_seconds() / 60
    out["snapshot_minutes_before_commence"] = {"median": float(lead.median()), "q05": float(lead.quantile(.05)),
                                               "q95": float(lead.quantile(.95)), "negative_(after_tip)": int((lead < 0).sum())}
    gids = set(p.game_id)
    out["by_season"] = {}
    pp = p.drop_duplicates("game_id").set_index("game_id")
    allg = []
    for y in YEARS[:-1]:
        g = all_games(y)
        g["has_line"] = g.game_id.isin(gids)
        allg.append(g)
        reg = g[g.season_type == 2]
        m = g[g.has_line]
        dtip = [abs((pd.Timestamp(pp.loc[i, "commence_time"]).timestamp()) - t) / 60 for i, t in zip(m.game_id, m.tip)]
        out["by_season"][fa.label_of(y)] = {
            "regular_season_games": int(len(reg)), "with_line": int(reg.has_line.sum()),
            "coverage_pct": round(100 * reg.has_line.mean(), 2), "all_types_games": int(len(g)), "all_types_with_line": int(g.has_line.sum()),
            "commence_vs_hoopr_tip_minutes_median_max": [float(np.median(dtip)) if dtip else None, float(np.max(dtip)) if dtip else None],
            "games_with_commence_diff_gt_30min": int(np.sum(np.array(dtip) > 30))}
    allg = pd.concat(allg)
    matched = p.game_id.isin(set(allg.game_id))
    out["rows_matched_to_hoopr_2022_2026"] = int(matched.sum())
    out["rows_in_2022_2026_window"] = int((pd.to_datetime(p.commence_time, utc=True) >= pd.Timestamp("2021-10-01", tz="UTC")).sum())
    out["match_rate_of_window_rows_pct"] = round(100 * out["rows_matched_to_hoopr_2022_2026"] / max(out["rows_in_2022_2026_window"], 1), 2)
    out["rows_before_2021-10_(2020 bubble/2021, not in the study window)"] = int(len(p) - out["rows_in_2022_2026_window"])
    out["rows_in_window_unmatched_examples"] = p[(~matched) & (pd.to_datetime(p.commence_time, utc=True) >= pd.Timestamp("2021-10-01", tz="UTC"))] \
        .head(10)[["game_id", "commence_time"]].to_dict("records")
    out["no_2026-27_lines_in_file"] = bool((pd.to_datetime(p.commence_time, utc=True) < pd.Timestamp("2026-08-01", tz="UTC")).all())
    return out


# ------------------------------------------------------------------ 6
def independent_winner(day, away, home, box26, box27, fz):
    """The frozen #42 winner (A w0.0 / B termown / C decay5 / k 20 / shift off / F cat / split on / no shrink), written
    again from the raw box scores with plain loops (no grid_lib), for one game on ET date `day`."""
    k, split_c = 20, 10.0

    def tov_fix(tt, t):
        return tt if (np.isfinite(tt) and tt >= t) else t

    def rows(tb, types):
        t = tb[tb.season_type.isin(types)].copy()
        t["tov"] = [tov_fix(a, b) for a, b in zip(t.total_turnovers, t.turnovers)]
        t["poss"] = t.field_goals_attempted - t.offensive_rebounds + t.tov + 0.44 * t.free_throws_attempted
        o = t[["game_id", "team_abbreviation", "poss"]].rename(columns={"team_abbreviation": "opponent_team_abbreviation", "poss": "opp_poss"})
        m = t.merge(o, on=["game_id", "opponent_team_abbreviation"])
        g = pd.to_datetime(m.game_date_time)
        m["d"] = g.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
        m["team"] = m.team_abbreviation.map(lib.to_kalshi)
        m["opp"] = m.opponent_team_abbreviation.map(lib.to_kalshi)
        return m.dropna(subset=["team", "opp"]).sort_values("d", kind="stable")

    prior_rows, cur_rows = rows(box26, (2,)), rows(box27, (2, 3, 5))
    cur_rows = cur_rows[cur_rows.d < pd.Timestamp(day)]
    info = {}

    def prior(team):
        g = prior_rows[prior_rows.team == team]
        return g.opponent_team_score.sum() / g.opp_poss.sum(), ((g.poss + g.opp_poss) / 2).mean(), len(g)

    def cur(team, venue=None):
        g = cur_rows[cur_rows.team == team]
        if venue:
            g = g[g.team_home_away == venue]
        n = len(g)
        if n == 0:
            return None
        w = 0.5 ** np.arange(n - 1, -1, -1) / 1.0
        w = 0.5 ** (np.arange(n - 1, -1, -1) / 5.0)
        D = (w * g.opponent_team_score.values).sum() / (w * g.opp_poss.values).sum()
        P = (w * ((g.poss + g.opp_poss) / 2).values).sum() / w.sum()
        return D, P, float(w.sum())

    def blend(pr, cu):
        if cu is None:
            return pr
        n = cu[2]
        return ((k * pr[0] + n * cu[0]) / (k + n), (k * pr[1] + n * cu[1]) / (k + n))

    def team_rating(team, venue):
        pD, pP, pn = prior(team)
        r_all = blend((pD, pP), cur(team))
        cs = cur(team, venue)
        rs = blend((pD, pP), cs)
        ns = cs[2] if cs else 0.0
        w = ns / (ns + split_c)
        info[team] = {"prior_2025_26_games": pn, "cur_2026_27_games": int((cur_rows.team == team).sum()),
                      "cur_venue_games": int(((cur_rows.team == team) & (cur_rows.team_home_away == venue)).sum()),
                      "prior_D": pD, "prior_P": pP}
        return (w * rs[0] + (1 - w) * r_all[0], w * rs[1] + (1 - w) * r_all[1])

    def rest_cat(team):
        g = cur_rows[cur_rows.team == team]
        if len(g) == 0:
            return 3
        return int(min((pd.Timestamp(day) - g.d.max()).days - 1, 3))

    Da, Pa = team_rating(away, "away")
    Dh, Ph = team_rating(home, "home")
    e = list(fz["constants_pair"]["e_cat"]) + [0.0]
    ca, ch = rest_cat(away), rest_cat(home)
    proj = Dh * Ph + Da * Pa + e[ca] + e[ch]
    return {"proj": float(proj), "away": {"D": Da, "P": Pa, "rest_cat": ca}, "home": {"D": Dh, "P": Ph, "rest_cat": ch},
            "inputs": info, "cur_league_rows": int(len(cur_rows))}


def check6_live():
    sys.path.insert(0, str(fa.GRID))
    import tonight
    box27p = ROOT / "data/raw/nba_open/team_box_2027.parquet"
    fz = tonight.load_frozen()
    day = dt.date(2026, 10, 22)
    games = [("CLE@PHI", "CLE", "PHI", None), ("DEN@OKC", "DEN", "OKC", None)]
    theirs = tonight.project_winner_for_date(day, games, box27=box27p)
    b26, b27 = pd.read_parquet(fa.BOX / "team_box_2026.parquet"), pd.read_parquet(box27p)
    out = {"date": str(day), "box27_file": str(box27p.relative_to(ROOT)),
           "box27_regular_season_games_used": int(b27[b27.season_type == 2].game_id.nunique()),
           "box27_last_game_date_et": str(pd.to_datetime(b27.game_date_time).max()),
           "prior_season_file": "team_box_2026.parquet (2025-26 regular season, season_type 2)",
           "prior_2025_26_regular_season_games": int(b26[b26.season_type == 2].game_id.nunique()), "games": {}}
    for key, a, h, _ in games:
        mine = independent_winner(day, a, h, b26, b27, fz)
        out["games"][key] = {"tonight_py": theirs[key], "independent": mine["proj"], "abs_diff": abs(theirs[key] - mine["proj"]),
                             "independent_detail": mine}
    out["max_abs_diff"] = max(v["abs_diff"] for v in out["games"].values())
    return out


# ------------------------------------------------------------------ main
def md_render(res):
    L = ["# Data checks (research log #56)", "", f"Generated by data_checks.py.  All numbers are in data_checks.json.", ""]
    k = res["1_finals"]["kalshi_vs_hoopr"]
    L += ["## 1a. hoopR final vs Kalshi end-of-game value (max expiration_value over NO-settled strikes)",
          f"- events {k['events']}, with a NO-settled strike {k['events_with_a_no_settled_strike']}, matched to hoopR {k['matched_to_hoopr']}, "
          f"compared {k['compared']}, equal {k['equal']}, mismatches {k['n_mismatches']}.",
          f"- events whose NO strikes disagree among themselves: {k['events_where_no_strikes_disagree_among_themselves']}; "
          f"events without a NO strike: {k['events_without_no_strike']}.", ""]
    for t, v in res["1_finals"]["odds_points"].items():
        L.append(f"- OddsData `{t}`: rows {v['odds_rows']}, matched {v['matched_to_hoopr']}, unmatched {v['unmatched']}, "
                 f"final-points mismatches {v['points_mismatches']}; OU vs closing_lines: {v['ou_vs_closing_lines']}")
    L += ["", "## 2. Team points = sum of player points (player_box)"]
    for s, v in res["2_player_sums"].items():
        if "status" in v:
            L.append(f"- {s}: {v['status']}")
            continue
        L.append(f"- {s}: team-games {v['team_games']}, no player rows {v['no_player_rows']} {v['no_player_rows_by_type']}, "
                 f"mismatches {v['mismatches']} {v['mismatch_by_type']}, NaN-points rows for players who played {v['player_rows_nan_points_but_played']}")
    L.append("- mismatch games vs Kalshi: " + json.dumps(res["2_player_sums_mismatch_games_vs_kalshi"], default=str)[:600])
    L += ["", "## 3. ORtg x pace vs PPG per team-season (formula code's definitions)"]
    for s, v in res["3_ratings"].items():
        L.append(f"- {s}: teams {v['teams']}, games/team {v['games_per_team_min_max']}, max |ORtg x pace - PPG| {v['max_abs_ortg_x_pace_minus_ppg']:.3f}, "
                 f"max |DRtg x pace - opp PPG| {v['max_abs_drtg_x_pace_minus_oppg']:.3f}, league ORtg/100 {v['league_ortg100']:.2f}, pace {v['league_pace']:.2f}, "
                 f"reg-season game_ids in file {v['reg_season_game_ids_in_file']} (dropped by team_games_g: {v['game_ids_dropped_by_team_games_g']})")
    L += ["", "### 3b. NBA.com official season team stats"]
    for s, v in res["3b_nba_official"].items():
        L.append(f"- {s}: {v}")
    L += ["", "## 4. Injury reports (latest report at or before T-60 min)"]
    inj = res["4_injury"]
    L.append(f"- report files {inj['report_files']} ({inj['first_report']} .. {inj['last_report']}), unparsed headers {inj['header_unparsed']}")
    for s, v in inj["seasons"].items():
        L.append(f"- {s}: games {v['regular_season_games']}; (a) any report in the 24h before T-60: {v['a_any_report_published_in_the_24h_up_to_T-60']}; "
                 f"(b) latest report lists the game: {v['b_latest_report_at_or_before_T-60_lists_the_game']}; (c) some report lists it: "
                 f"{v['c_some_report_at_or_before_T-60_lists_the_game']}; unmatched names {v['unmatched_name_listings']}/{v['status_listings_out_doubtful_questionable']} "
                 f"({v['unmatched_share_pct']}%), distinct {v['distinct_unmatched_names']}")
    L.append(f"- 2026-27: {inj['2026-27']}")
    L += ["", "## 5. Lines (closing_lines_odds_api.parquet)"]
    ln = res["5_lines"]
    L.append("- " + json.dumps({k: v for k, v in ln.items() if k != "by_season"}, default=str))
    for s, v in ln["by_season"].items():
        L.append(f"- {s}: {v}")
    L += ["", "## 6. Live pipeline (tonight 2026-10-22)", "- " + json.dumps(res["6_live"], default=str)[:4000], ""]
    return "\n".join(L)


def main():
    res = {"1_finals": check1_finals()}
    res["1_finals"]["odds_points"] = check1b_odds()
    res["2_player_sums"] = check2_player_sums()
    bad_ids = [e["game_id"] for v in res["2_player_sums"].values() for e in v.get("examples", [])]
    kk = KALSHI_ROWS[KALSHI_ROWS.hoopr_id.isin(bad_ids)]
    res["2_player_sums_mismatch_games_vs_kalshi"] = {
        "mismatch_example_games": len(bad_ids), "of_which_in_kalshi_events": int(len(kk)),
        "kalshi_final_equals_hoopr_team_box_total": int((kk.kalshi_final == kk.hoopr_total).sum()),
        "rows": kk.to_dict("records")}
    c3 = check3_ratings()
    res["3b_nba_official"] = check3b_nba_official(c3)
    res["3_ratings"] = {s: {k: v for k, v in d.items() if k != "_table"} for s, d in c3.items()}
    res["4_injury"] = check4_injury()
    res["5_lines"] = check5_lines()
    res["6_live"] = check6_live()
    (HERE / "data_checks.json").write_text(json.dumps(res, indent=1, default=str))
    (HERE / "DATA_CHECKS.md").write_text(md_render(res))
    print(md_render(res))


if __name__ == "__main__":
    main()
