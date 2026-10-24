"""Writes main_window_debug.csv: every listed (Out/Doubtful/Questionable) player of both teams in the given games, with the window
used by main (game ids, dates, minutes, points, ppg, mpg).  Debug aid only; not an input to anything."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2_lib as E  # noqa: E402

TARGETS = [401469054, 401469076, 401469151, 401585449, 401810778, 401810914, 401810894, 401360764]


def main():
    games = E.load_games()
    box = E.load_box()
    rep = E.Reports()
    df = box.df
    out = []
    for gid in TARGETS:
        gg = games[games.game_id == gid]
        away, home = gg[gg.venue == "away"].iloc[0], gg[gg.venue == "home"].iloc[0]
        tip = float(gg.tip.iloc[0]); d = E.day_int(gg.date.iloc[0]); year = E.year_of(gg.season.iloc[0])
        mdy = pd.Timestamp(gg.date.iloc[0]).strftime("%m/%d/%Y")
        cov = rep.last_covering(tip - E.D_OFFSET_S, mdy, f"{away.team_k}@{home.team_k}")
        prior = df[df.d < d].sort_values(["d", "game_id"], kind="stable")
        last = prior.groupby("aid").tail(1)
        for r in gg.itertuples(index=False):
            k = r.team_k
            roster = [int(a) for a, yr in zip(last[last.team == k].aid, last[last.team == k].yr) if yr >= year - 1]
            cn, _ = E._cands(box, roster)
            listed, _n = E.team_status_map(cov[2], cov[3], k) if cov else ({}, False)
            for raw, st in listed.items():
                if st not in E.STATUS_RANK:
                    continue
                a = E.match_name(raw, cn)
                base = {"game_id": gid, "team": r.team, "team_k": k, "player_report_name": raw, "status": st, "aid": a,
                        "report": cov[1] if cov else ""}
                if a is None:
                    out.append({**base, "player": "UNMATCHED"})
                    continue
                pl = prior[(prior.aid == a) & prior.played]
                cur = pl[pl.yr == year]
                src = "cur" if len(cur) >= E.MIN_CUR else "prev"
                w = cur.tail(E.WINDOW) if src == "cur" else pl[pl.yr == year - 1].tail(E.WINDOW)
                out.append({**base, "player": box.names[a], "source": src, "n_games": len(w),
                            "window_game_ids": ";".join(str(int(x)) for x in w.game_id),
                            "window_dates": ";".join(str(np.datetime64(int(x), "D")) for x in w.d),
                            "window_minutes": ";".join(f"{x:g}" for x in w.minutes), "window_points": ";".join(f"{x:g}" for x in w.pts),
                            "ppg": w.pts.mean() if len(w) else np.nan, "mpg": w.minutes.mean() if len(w) else np.nan,
                            "rotation_quality": bool(len(w) and w.minutes.mean() >= E.ROT_MPG)})
    pd.DataFrame(out).to_csv(E.STUDY / "main_window_debug.csv", index=False)
    print(len(out), "rows")


if __name__ == "__main__":
    main()
