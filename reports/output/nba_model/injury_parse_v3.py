"""Parser for ALL generations of the NBA official injury-report PDFs (research log #32). Parse-only; nothing is executed.
parse_any(path) dispatches on the header: the 2023-24+ layout has single-word headers ('GameDate', 'PlayerName', glued names such as
'Dowtin,Jeff'); the 2021-22 / 2022-23 layout has two-word headers ('Game' 'Date'), names with spaces ('Trent Jr., Gary'), team names
with spaces and a multi-word reason. Both return the same structure as injury_parse.parse_report:
  dict(header, rows=[{date,time,matchup,team,player,status}], nys=[(date,matchup,team)], problems, layout)
The old layout is converted to the new one's text form (player = words glued together: 'TrentJr.,Gary'; team glued: 'MilwaukeeBucks')
so injury_parse.norm_injury_name and the team-name map work unchanged.
"""
from __future__ import annotations

import re

import pdfplumber

import injury_parse as IP

STATUSES = IP.STATUSES
DATE_RE, MATCH_RE, HDR_RE = IP.DATE_RE, IP.MATCH_RE, IP.HDR_RE
TIME_OLD_RE = re.compile(r"^\d{2}:\d{2}$")


def layout_of(path) -> str:
    with pdfplumber.open(path) as pdf:
        words = pdf.pages[0].extract_words()
    return "new" if any(w["text"] == "GameDate" for w in words) else "old"


def parse_old(path):
    out = {"header": None, "rows": [], "nys": [], "problems": [], "layout": "old"}
    with pdfplumber.open(path) as pdf:
        bounds = None
        date = time_ = match = team = None
        for pi, page in enumerate(pdf.pages):
            words = page.extract_words()
            if not words:
                continue
            m = HDR_RE.search(" ".join(w["text"] for w in words if w["top"] < 40))
            if m and out["header"] is None:
                out["header"] = " ".join(m.groups())
            # cluster words into lines by 'top' (records are 18pt apart; words of one record differ by < 3pt)
            ws = sorted(words, key=lambda w: (w["top"], w["x0"]))
            lines, cur = [], []
            for w in ws:
                if cur and abs(w["top"] - cur[0]["top"]) > 3:
                    lines.append(cur)
                    cur = []
                cur.append(w)
            if cur:
                lines.append(cur)
            for ln in lines:
                txts = [w["text"] for w in ln]
                if "Matchup" in txts and "Reason" in txts:      # column header line (repeated per page)
                    xs = {t: min(w["x0"] for w in ln if w["text"] == t) for t in ("Matchup", "Team", "Player", "Current", "Reason")}
                    gx = sorted(w["x0"] for w in ln if w["text"] == "Game")
                    if len(gx) == 2:
                        xs["Time"] = gx[1]
                    bounds = xs
                    continue
                if ln[0]["top"] < 40 or txts[0] == "Page" or txts[0] == "Injury":
                    continue
                b = bounds or {"Time": 95, "Matchup": 170, "Team": 244, "Player": 371, "Current": 499, "Reason": 605}
                b_time, b_match, b_team, b_player, b_stat, b_reason = (b.get("Time", 95) - 8, b["Matchup"] - 8, b["Team"] - 8,
                                                                      b["Player"] - 8, b["Current"] - 8, b["Reason"] - 8)
                team_w, player_w, stat_w, reason_w = [], [], [], []
                for w in ln:
                    x, t = w["x0"], w["text"]
                    if x < b_time:
                        if DATE_RE.match(t):
                            date = t
                    elif x < b_match:
                        if TIME_OLD_RE.match(t):
                            time_ = t + "(ET)"
                    elif x < b_team:
                        if MATCH_RE.match(t):
                            match = t
                    elif x < b_player:
                        team_w.append(t)
                    elif x < b_stat:
                        player_w.append(t)
                    elif x < b_reason:
                        stat_w.append(t)
                    else:
                        reason_w.append(t)
                if team_w:
                    team = "".join(team_w)
                joined_reason = " ".join(reason_w + player_w + stat_w).upper().replace(" ", "")
                if "NOTYETSUBMITTED" in joined_reason:
                    out["nys"].append((date, match, team))
                    continue
                status = " ".join(stat_w)
                player = "".join(player_w)
                if not player and not status:
                    continue                                   # wrapped reason text
                if status in STATUSES and player:
                    out["rows"].append({"date": date, "time": time_, "matchup": match, "team": team, "player": player, "status": status})
                elif player and not status:
                    out["problems"].append(("player_without_status", player))
                elif status:
                    out["problems"].append(("unknown_or_orphan_status", status))
    return out


def parse_any(path):
    lay = layout_of(path)
    if lay == "old":
        return parse_old(path)
    r = IP.parse_report(path)
    r["layout"] = "new"
    return r
