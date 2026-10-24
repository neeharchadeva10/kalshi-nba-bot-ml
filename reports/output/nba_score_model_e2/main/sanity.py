"""Research log #62, step 2: report sanity (SANITY.md).  Uses box scores AFTER the fact only to check the reports; no outcome (points)
and nothing from this file feeds a model.

Per season: the share of OUT-listed rotation-quality players (matched, >= 15 mpg prior-10) who did not play that game (box DNP or
absent from the box), the mismatches (listed Out but played), and injury-report coverage (report found, not-yet-submitted, unmatched
names, and how many unmatched names look like rotation players under a league-wide name match - a diagnostic only).
Run:  .venv/Scripts/python.exe reports/output/nba_score_model_e2/main/sanity.py"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2_lib as E  # noqa: E402


def parse_listed(s):
    out = []
    for tok in (s or "").split(";"):
        if tok:
            a, st, m = tok.split(":")
            out.append((int(a), st, float(m)))
    return out


def sanity_tables(F: pd.DataFrame, box: E.Box):
    """-> (share table per season x status, mismatch rows, coverage table, unmatched diagnostic table).  Pure given F and box."""
    names_by_norm = {}
    for a, n in box.names.items():
        names_by_norm.setdefault(E.norm_name(n), []).append(a)
    recs, mism, unm = [], [], []
    for r in F.itertuples(index=False):
        if not r.box_found:
            continue
        for a, st, mpg in parse_listed(r.listed_rot_own):
            played = a in box.played_in.get((r.team_k, int(r.game_id)), ())
            recs.append((r.season, st, played))
            if st == "Out" and played:
                mism.append({"season": r.season, "date": str(pd.Timestamp(r.date).date()), "game_id": int(r.game_id), "team": r.team_k,
                             "athlete_id": a, "name": box.names.get(a, "?"), "status": st, "prior10_mpg": mpg, "report": r.report_name})
        for tok in (r.unmatched_names_own or "").split(";"):
            if not tok:
                continue
            raw, st = tok.rsplit("|", 1)
            cand = names_by_norm.get(E.norm_injury_name(raw), [])
            rot = False
            if len(cand) == 1:
                w = box.stats(cand[0], E.day_int(r.date), E.year_of(r.season))
                rot = bool(w is not None and w[0] >= E.ROT_MPG)
            unm.append((r.season, st, len(cand) == 1, rot))
    S = pd.DataFrame(recs, columns=["season", "status", "played"])
    share = (S.groupby(["season", "status"]).played.agg(n="size", n_played="sum")
             .assign(share_did_not_play=lambda x: 1 - x.n_played / x.n).reset_index())
    cov = F.groupby("season").agg(team_games=("game_id", "size"), report_found=("report_found", "mean"), nys=("nys", "mean"),
                                  box_found=("box_found", "mean"), listed=("n_listed_own", "sum"), unmatched=("n_unmatched_own", "sum"),
                                  out_rot=("n_out_rot_own", "sum"), q_rot=("n_q_rot_own", "sum"), back=("n_back_own", "sum")).reset_index()
    cov["unmatched_share"] = cov.unmatched / cov.listed.clip(lower=1)
    U = pd.DataFrame(unm, columns=["season", "status", "league_unique", "league_rot"])
    ud = U.groupby("season").agg(unmatched=("status", "size"), league_unique_name=("league_unique", "sum"),
                                 league_unique_rotation=("league_rot", "sum")).reset_index()
    return share, pd.DataFrame(mism), cov, ud


def fmt(df, floatfmt="{:.4f}"):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        lines.append("| " + " | ".join(floatfmt.format(v) if isinstance(v, float) else str(v) for v in r) + " |")
    return "\n".join(lines)


def main():
    F = pd.read_parquet(E.FEATURES_PARQUET)
    box = E.load_box()
    share, mism, cov, ud = sanity_tables(F, box)
    mism.to_csv(E.STUDY / "sanity_mismatches.csv", index=False)
    out = share[share.status == "Out"]
    L = ["# SANITY - research #62: do the injury reports agree with the box scores?\n",
         "Box scores are used here only AFTER the fact, to check the reports.  'Rotation-quality' = >= 15 mpg over the last 10 games played "
         "before the game; 'did not play' = box DNP or absent from the box.  Expected share for OUT-listed players: about 99%.\n",
         "## OUT-listed rotation players who did not play\n", fmt(out), "\n",
         "## Other statuses (context)\n", fmt(share[share.status != "Out"]), "\n",
         f"## Mismatches (listed Out, rotation-quality, but played): {len(mism)} rows, all in sanity_mismatches.csv.  "
         "By season: " + (", ".join(f"{k} {v}" for k, v in mism.groupby('season').size().items()) if len(mism) else "none") + "\n"]
    if len(mism):
        L += ["First 40 (sorted by date):\n", fmt(mism.sort_values(["date", "game_id"]).head(40).drop(columns=["report"]), "{:.2f}"), "\n"]
    L += ["## Injury-report coverage per season (team-game rows)\n", fmt(cov), "\n",
          "report_found = a report published at or before D = tip - 60 min covered the game (72 h look-back); nys = the team was 'not yet "
          "submitted' (no information); unmatched = listed Out/Doubtful/Questionable names with no unique match among the team's players "
          "(they contribute 0).\n",
          "## Unmatched-name diagnostic (not used by any feature)\n", fmt(ud), "\n",
          "league_unique_name = the unmatched name has exactly one league-wide normalised-name match in the box files; "
          "league_unique_rotation = that player was rotation-quality before the game (so a feature value was missed).  Mostly "
          "newly-acquired or long-term-injured players with no box row for the team.\n"]
    (E.STUDY / "SANITY.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:6]))
    print(cov.to_string())
    print(ud.to_string())


if __name__ == "__main__":
    main()
