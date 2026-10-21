"""Parser for the NBA official injury-report PDFs (research log #31). Parse-only: never executes anything from files.
parse_report(path) -> dict(header_time_et, rows=[{date,time,matchup,team,player,status}], nys=[(date,matchup,team)], problems)
Layout: fixed columns GameDate | GameTime | Matchup | Team | PlayerName | CurrentStatus | Reason. A record = a word in
the PlayerName column with a status word at the same height; GameDate/GameTime/Matchup/Team fill down.
"""
from __future__ import annotations

import re
import unicodedata

import pdfplumber

STATUSES = {"Out", "Doubtful", "Questionable", "Probable", "Available"}
P_MISS = {"Out": 1.0, "Doubtful": 0.75, "Questionable": 0.5, "Probable": 0.1, "Available": 0.0}
SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}\(ET\)$")
MATCH_RE = re.compile(r"^[A-Z]{2,3}@[A-Z]{2,3}$")
HDR_RE = re.compile(r"(\d{2}/\d{2}/\d{2}) (\d{2}:\d{2}) (AM|PM)")


def norm_name(s: str) -> str:
    """'Doncic' accents / punctuation / spaces / generational suffix removed, lower case, 'First Last' order."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z]", "", s)
    for suf in SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf) + 3:
            # only strip a real suffix: caller strips on the 'first last' string
            s = s[: -len(suf)]
            break
    return s


def norm_box_name(display: str) -> str:
    """'Scotty Pippen Jr.' -> 'scottypippen' ; 'Nikola Jokic' -> 'nikolajokic'."""
    s = unicodedata.normalize("NFKD", display).encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.split(r"\s+", s.strip()) if t]
    while len(toks) > 2 and re.sub(r"[^a-z]", "", toks[-1]) in SUFFIXES:
        toks.pop()
    return re.sub(r"[^a-z]", "", "".join(toks))


def norm_injury_name(raw: str) -> str:
    """'PippenJr.,Scotty' -> 'scottypippen' ; 'Prosper,Olivier-Maxence' -> 'oliviermaxenceprosper'."""
    if "," in raw:
        last, first = raw.split(",", 1)
    else:
        last, first = raw, ""
    last_c = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode()
    m = re.match(r"^(.*?)(Jr|Sr|II|III|IV)\.?$", last_c)
    if m and len(m.group(1)) > 2:
        last_c = m.group(1)
    return re.sub(r"[^a-z]", "", (first + last_c).lower().replace("jr.", ""))


def parse_report(path):
    out = {"header": None, "rows": [], "nys": [], "problems": []}
    with pdfplumber.open(path) as pdf:
        cols = None
        events = []
        for pi, page in enumerate(pdf.pages):
            words = page.extract_words()
            txt = " ".join(w["text"] for w in words if w["top"] < 60)
            m = HDR_RE.search(txt)
            if m and out["header"] is None:
                out["header"] = " ".join(m.groups())
            hdr = {w["text"]: w["x0"] for w in words if w["text"] in ("GameDate", "GameTime", "Matchup", "Team", "PlayerName", "CurrentStatus", "Reason")}
            hdr_tops = {w["text"]: w["top"] for w in words if w["text"] == "GameDate"}
            if not hdr_tops:
                hdr = {}
            if len(hdr) == 7 and cols is None:
                cols = hdr
            c = cols or {"GameDate": 23, "GameTime": 120, "Matchup": 200, "Team": 264, "PlayerName": 425, "CurrentStatus": 586, "Reason": 666}
            b_time, b_match, b_team, b_player, b_stat, b_reason = (c["GameTime"] - 8, c["Matchup"] - 8, c["Team"] - 8,
                                                                    c["PlayerName"] - 8, c["CurrentStatus"] - 8, c["Reason"] - 8)
            for w in words:
                if hdr and abs(w["top"] - next(iter(hdr_tops.values()))) < 2:
                    continue
                if w["top"] < 60 or w["text"].startswith("Page") or w["text"] == "Injury":
                    continue
                x, t, top = w["x0"], w["text"], w["top"]
                if x < b_time and DATE_RE.match(t):
                    events.append((pi, top - 3, 0, "date", t))
                elif b_time <= x < b_match and TIME_RE.match(t):
                    events.append((pi, top - 3, 1, "time", t))
                elif b_match <= x < b_team and MATCH_RE.match(t):
                    events.append((pi, top - 3, 2, "match", t))
                elif b_team <= x < b_player:
                    events.append((pi, top - 3, 3, "team", t))
                elif b_player <= x < b_stat:
                    events.append((pi, top, 4, "player", t))
                elif b_stat <= x < b_reason:
                    events.append((pi, top, 5, "status", t))
                elif x >= b_reason and t == "NOTYETSUBMITTED":
                    events.append((pi, top, 6, "nys", t))
        events.sort(key=lambda e: (e[0], round(e[1], 1), e[2]))
        date = time_ = match = team = None
        pend_player = None
        # tops of player and status words on one record can differ by a pixel or two: pair by nearest top
        recs = []
        pl = [e for e in events if e[3] == "player"]
        st = [e for e in events if e[3] == "status"]
        used = set()
        for e in events:
            k = e[3]
            if k == "date":
                date = e[4]
            elif k == "time":
                time_ = e[4]
            elif k == "match":
                match = e[4]
            elif k == "team":
                team = e[4]
            elif k == "nys":
                out["nys"].append((date, match, team))
            elif k == "player":
                cand = [s for s in st if s[0] == e[0] and abs(s[1] - e[1]) <= 4 and id(s) not in used]
                if not cand:
                    out["problems"].append(("player_without_status", e[4]))
                    continue
                s = min(cand, key=lambda s_: abs(s_[1] - e[1]))
                used.add(id(s))
                if s[4] not in STATUSES:
                    out["problems"].append(("unknown_status", s[4]))
                    continue
                out["rows"].append({"date": date, "time": time_, "matchup": match, "team": team, "player": e[4], "status": s[4]})
        for s in st:
            if id(s) not in used:
                out["problems"].append(("status_without_player", s[4]))
    return out
