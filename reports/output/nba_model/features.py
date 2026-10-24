"""Research log #29 features (reports/output/nba_model/PREREGISTRATION.md). Strict no-look-ahead.

build_features(games, pts, spread, expv) -> (rows, stats), one row per game in (tip, gid) order.
Features for a game use only games with a strictly earlier tip time T, plus the game's own PTS listing.
Games sharing the same T are all computed from the state before that T group.
INTERPRETATION lists every choice where the pre-registration is silent (it is copied into the README).
"""
from __future__ import annotations

import math
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_drift"))
sys.path.insert(0, str(ROOT / "src"))
import drift  # noqa: E402,F401  (reuse load_nba, split_index, fee, at, valid_quote, bootstrap_ci)

ELO_K, ELO_HOME, ELO_START = 20.0, 100.0, 1500.0
RECENT, REGULAR_MIN, REST_CAP = 10, 5, 4
KEY_PLAYER_AVG = 15.0
MON = {m: n + 1 for n, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}

INTERPRETATION = [
    "Games: exactly drift.load_nba() (#24): clean KXNBAGAME results, T >= 2025-10-21, sorted by (tip, gid); split = first "
    "ceil(N/2) games discovery, the rest unseen (identical to #24).",
    "Margin: the exact final winning margin. KXNBASPREAD ladders are SPARSE (not every k.5 strike exists), so the "
    "ladder only brackets the margin ('over 3.5' YES = at least 4; the lowest NO strike above it caps it). The exact "
    "integer margin is the market's expiration_value (same on both teams' markets), used when it lies inside the ladder "
    "bounds; outside the bounds it is rejected and counted. With no expiration_value, the ladder is used only if its "
    "bounds coincide; otherwise (expiration_value is the text 'Yes'/'No' on about 590 events) the midpoint of the ladder "
    "bounds (the lower bound when no upper strike exists) is used as an ESTIMATE and counted as ladder_bracket_estimate. "
    "The winner comes from the KXNBAGAME result when no ladder YES exists. "
    "KXNBAWINMARGIN has no 2025-26 markets, so spreads are the only source. A game with no usable margin updates Elo "
    "as a 1-point win (smallest possible) and is left out of rolling net margin.",
    "Elo: K=20, home advantage 100, all teams start 1500 on 2025-10-21 (no carry-over), 538 NBA multiplier "
    "((|margin|+3)^0.8) / (7.5 + 0.006 * winner_elo_diff), winner_elo_diff = winner's Elo (+100 if home) minus the "
    "loser's Elo (+100 if home). Feature = pre-game home Elo minus away Elo (the 100 home edge is left to the intercept).",
    "Rolling net margin: mean margin (from that team's side) over its last 10 games before g (fewer if fewer exist; 0 if "
    "none). Feature = home minus away.",
    "Rest: calendar days (ticker local game date) since the team's previous game, capped at 4; 4 for a team's first game. "
    "Back-to-back = rest 1. Features = home minus away rest and home minus away back-to-back flag.",
    "Home flag: every row is from the home team's point of view (target = home win), so the home flag is the "
    "intercept; the model learns its size there.",
    "Listing: a team-game is 'observed' when at least one KXNBAPTS market with that team's code exists for the game. "
    "The 'last 10 games' windows for regulars use the team's last 10 OBSERVED games (an unobserved game says nothing "
    "about who was listed). Unobserved current team-game -> missing points 0 (neutral). Sensitivity: the full model "
    "restricted to games observed for both teams is reported.",
    "Regulars: players (key = ticker part 3 minus the team code, e.g. VWEMBANYAMA1; a jersey-number change looks like a "
    "new player) listed for the team in >= 5 of its last 10 observed games.",
    "Points scored = floor_strike + 0.5 of the highest strike settled YES (0 if none). A game counts as 'played' for a "
    "player when at least one of his markets settled yes/no; if every market settled scalar (inactive, void) he did not "
    "play and the game is skipped. Rolling average = mean points over his last 10 played games (any team) before g.",
    "Missing points for a team = sum of rolling averages of its regulars not listed for it in g. A regular who is listed "
    "for the OTHER team in g (traded) is not counted as missing. Feature = home minus away.",
    "Key player missing (breakdown only): some regular with rolling average >= 15 points is not listed, for either team.",
]


# ------------------------------------------------------------------ pure helpers
def elo_prob_home(rh: float, ra: float) -> float:
    return 1.0 / (1.0 + 10 ** (-(rh + ELO_HOME - ra) / 400.0))


def mov_multiplier(margin: float, winner_elo_diff: float) -> float:
    """538 NBA: ((|MOV|+3)^0.8) / (7.5 + 0.006 * winner_elo_diff)."""
    return ((abs(margin) + 3.0) ** 0.8) / (7.5 + 0.006 * winner_elo_diff)


def elo_update(rh: float, ra: float, home_won: bool, margin: float):
    """New (home, away) ratings. margin = winner's margin (>= 1)."""
    p = elo_prob_home(rh, ra)
    wd = (rh + ELO_HOME) - ra if home_won else ra - (rh + ELO_HOME)
    d = ELO_K * mov_multiplier(margin, wd) * ((1.0 if home_won else 0.0) - p)
    return rh + d, ra - d


def margin_from_ladders(ladders: dict, expiration_values: dict | None = None, winner_hint: str | None = None):
    """ladders {team: {strike: result}} for one game -> (winner or None, margin, source) or None.
    The spread ladders are sparse (not every strike exists), so the market's expiration_value (the integer winning
    margin, identical on both teams' markets) is the exact source. The ladder gives bounds: 'over 3.5' YES means
    margin >= 4; the lowest NO strike k.5 above it means margin <= k. Source 'exp_value' = value inside the ladder
    bounds, 'exp_value_outside_ladder_bounds' = value contradicts the ladder (margin rejected, returns None).
    Without a usable value: exact if the ladder bounds coincide, else None. Winner is None when no YES exists."""
    best, nos = {}, {}
    for tm, lad in ladders.items():
        ys = [k for k, r in lad.items() if r == "yes"]
        best[tm] = max(ys) if ys else None
    winners = [t for t, bb in best.items() if bb is not None]
    if len(winners) > 1:
        return None
    w = winners[0] if winners else None
    lo, hi = 1, None
    wl = w if w is not None else winner_hint
    if wl in ladders:
        floor_ = best[wl] if best.get(wl) is not None else 0.0
        if best.get(wl) is not None:
            lo = int(math.ceil(best[wl]))
        up = [k for k, r in ladders[wl].items() if r == "no" and k > floor_]
        hi = int(math.floor(min(up))) if up else None
    xv = None
    for v in (expiration_values or {}).values():
        try:
            xv = int(v)
            break
        except ValueError:
            continue
    if xv is not None:
        if xv < lo or (hi is not None and xv > hi) or xv < 1:
            return w, xv, "exp_value_outside_ladder_bounds"
        return w, xv, "exp_value"
    if hi is not None and lo == hi:
        return w, lo, "ladder_exact"
    if wl is not None and wl in ladders:
        # bracket only: midpoint of the bounds (lower bound when no upper strike exists)
        return w, int(round((lo + hi) / 2)) if hi is not None else lo, "ladder_bracket_estimate"
    return None


def player_game_points(ladder: dict):
    """(played, points) from {strike: result}; points = highest strike settled YES (+0.5), 0 if none."""
    if all(r not in ("yes", "no") for r in ladder.values()):
        return False, 0.0
    ys = [k for k, r in ladder.items() if r == "yes"]
    return True, (max(ys) + 0.5 if ys else 0.0)


def availability(window_listed, listed_now, listed_other, player_hist):
    """window_listed: list of frozensets (team's last <=10 observed games). Returns (missing_points, key_missing, n_regulars)."""
    cnt = defaultdict(int)
    for s in window_listed:
        for k in s:
            cnt[k] += 1
    regs = [k for k, n in cnt.items() if n >= REGULAR_MIN]
    miss, key = 0.0, 0
    for k in regs:
        if k in listed_now or k in listed_other:
            continue
        ph = player_hist.get(k, [])[-RECENT:]
        avg = sum(ph) / len(ph) if ph else 0.0
        miss += avg
        key |= int(avg >= KEY_PLAYER_AVG)
    return miss, key, len(regs)


def parse_suffix_date(suf: str) -> date:
    return date(2000 + int(suf[:2]), MON[suf[2:5]], int(suf[5:7]))


# ------------------------------------------------------------------ data access
def load_tables(db: Path | None = None):
    c = sqlite3.connect(db or ROOT / "data" / "nba_model.sqlite")
    pts = defaultdict(lambda: defaultdict(dict))  # suffix -> (team,key) -> {strike: result}
    for ev, team, key, strike, res in c.execute(
            "SELECT event_ticker,team,player_key,floor_strike,result FROM mk WHERE series='KXNBAPTS' AND team IS NOT NULL"):
        pts[ev.split("-", 1)[1]][(team, key)][strike] = res
    spread = defaultdict(lambda: defaultdict(dict))  # suffix -> team -> {strike: result}
    expv = defaultdict(dict)
    for ev, tk, strike, res, xv in c.execute(
            "SELECT event_ticker,ticker,floor_strike,result,expiration_value FROM mk WHERE series='KXNBASPREAD'"):
        suf = ev.split("-", 1)[1]
        team = "".join(ch for ch in tk.split("-")[2] if ch.isalpha())  # 'NYK10' -> 'NYK'
        spread[suf][team][strike] = res
        if xv not in (None, ""):
            expv[suf][team] = xv
    return pts, spread, expv


# ------------------------------------------------------------------ feature builder
def build_features(games, pts, spread, expv):
    games = sorted(games, key=lambda g: (g.tip, g.gid))
    elo = defaultdict(lambda: ELO_START)
    team_hist = defaultdict(list)      # team -> [(date, margin_for_team or None)]
    team_listed = defaultdict(list)    # team -> [frozenset(player keys)] OBSERVED games only
    player_hist = defaultdict(list)    # player key -> [points] in played games, time ordered
    stats = defaultdict(int)
    rows = []
    i = 0
    while i < len(games):
        j = i
        while j < len(games) and games[j].tip == games[i].tip:
            j += 1
        pending = []
        for g in games[i:j]:
            suf = g.gid.split("-", 1)[1]
            h_c, a_c = g.mk[g.home_idx].code, g.mk[1 - g.home_idx].code
            gdate = parse_suffix_date(suf)
            listing = pts.get(suf, {})
            row = {"gid": g.gid, "tip": g.tip, "home": h_c, "away": a_c, "home_win": g.mk[g.home_idx].result}
            for side, tm, other in (("h", h_c, a_c), ("a", a_c, h_c)):
                row["elo_" + side] = elo[tm]
                mg = [m for _, m in team_hist[tm][-RECENT:] if m is not None]
                row["net_" + side] = sum(mg) / len(mg) if mg else 0.0
                hist = team_hist[tm]
                rest = max(min((gdate - hist[-1][0]).days, REST_CAP), 0) if hist else REST_CAP
                row["rest_" + side] = float(rest)
                row["b2b_" + side] = 1.0 if rest == 1 else 0.0
                now = {k for (t2, k) in listing if t2 == tm}
                oth = {k for (t2, k) in listing if t2 == other}
                row["obs_" + side] = 1.0 if now else 0.0
                if now:
                    miss, key, nreg = availability(team_listed[tm][-RECENT:], now, oth, player_hist)
                else:
                    miss, key, nreg = 0.0, 0, 0
                row["miss_" + side], row["keymiss_" + side], row["nreg_" + side] = miss, float(key), float(nreg)
            row["elo_diff"] = row["elo_h"] - row["elo_a"]
            row["net_diff"] = row["net_h"] - row["net_a"]
            row["rest_diff"] = row["rest_h"] - row["rest_a"]
            row["b2b_diff"] = row["b2b_h"] - row["b2b_a"]
            row["miss_diff"] = row["miss_h"] - row["miss_a"]
            row["both_observed"] = row["obs_h"] * row["obs_a"]
            row["key_missing"] = float(row["keymiss_h"] or row["keymiss_a"])
            rows.append(row)
            pending.append((g, suf, gdate, h_c, a_c, listing))
        # only now (whole T group has its features) fold the results into the state
        for g, suf, gdate, h_c, a_c, listing in pending:
            home_won = g.mk[g.home_idx].result == 1.0
            margin_home = None
            mg = margin_from_ladders(spread.get(suf, {}), expv.get(suf), h_c if home_won else a_c) if suf in spread else None
            if mg is not None:
                w, m, src = mg
                stats["margin_" + src] += 1
                if src == "exp_value_outside_ladder_bounds":
                    pass
                elif w is not None and (w == h_c) != home_won:
                    stats["margin_winner_disagrees_result"] += 1
                else:
                    margin_home = m if home_won else -m
            if margin_home is None:
                stats["margin_missing_or_rejected"] += 1
            elo[h_c], elo[a_c] = elo_update(elo[h_c], elo[a_c], home_won, abs(margin_home) if margin_home else 1.0)
            team_hist[h_c].append((gdate, margin_home))
            team_hist[a_c].append((gdate, None if margin_home is None else -margin_home))
            for tm in (h_c, a_c):
                ks = frozenset(k for (t2, k) in listing if t2 == tm)
                if ks:
                    team_listed[tm].append(ks)
            for (tm, k), lad in listing.items():
                played, p_ = player_game_points(lad)
                if played:
                    player_hist[k].append(p_)
        i = j
    return rows, dict(stats)
