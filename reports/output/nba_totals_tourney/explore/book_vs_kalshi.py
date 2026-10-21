"""[EXPLORATORY - 2025-26, both halves (the unseen half was used by #42 on 2026-10-21)] Sportsbook OU line
(OddsData odds_2025-26; book and timestamp unknown, probably a close) vs Kalshi T-1h median total."""
import sys, sqlite3
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tourney_run as trun  # noqa: E402
v2 = trun.v2
games, strikes, info = v2.load_games()
g25 = games[games.season == "2025-26"].copy()
gd, _ = v2.attach_outcomes(g25, strikes, ["discovery"])
gu, _ = v2.attach_outcomes(g25, strikes, ["unseen"])
g = pd.concat([gd, gu]).dropna(subset=["median", "actual"])
NAMES = {"Atlanta Hawks":"ATL","Boston Celtics":"BOS","Brooklyn Nets":"BKN","Charlotte Hornets":"CHA","Chicago Bulls":"CHI",
"Cleveland Cavaliers":"CLE","Dallas Mavericks":"DAL","Denver Nuggets":"DEN","Detroit Pistons":"DET","Golden State Warriors":"GSW",
"Houston Rockets":"HOU","Indiana Pacers":"IND","Los Angeles Clippers":"LAC","LA Clippers":"LAC","Los Angeles Lakers":"LAL","Memphis Grizzlies":"MEM",
"Miami Heat":"MIA","Milwaukee Bucks":"MIL","Minnesota Timberwolves":"MIN","New Orleans Pelicans":"NOP","New York Knicks":"NYK",
"Oklahoma City Thunder":"OKC","Orlando Magic":"ORL","Philadelphia 76ers":"PHI","Phoenix Suns":"PHX","Portland Trail Blazers":"POR",
"Sacramento Kings":"SAC","San Antonio Spurs":"SAS","Toronto Raptors":"TOR","Utah Jazz":"UTA","Washington Wizards":"WAS"}
c = sqlite3.connect("file:" + str(HERE.parents[3] / "data/raw/nba_odds_samples/OddsData.sqlite") + "?mode=ro", uri=True)
b = pd.read_sql('select Date, Home, Away, OU, Points from "odds_2025-26"', c)
b["home"] = b.Home.map(NAMES); b["away"] = b.Away.map(NAMES)
print("book rows", len(b), "unmapped teams", sorted(set(b.Home[b.home.isna()]) | set(b.Away[b.away.isna()])))
m = g.merge(b.dropna(subset=["home", "away", "OU"]), left_on=["date", "home", "away"], right_on=["Date", "home", "away"], how="inner")
print("matched games", len(m), "of", len(g), "| book Points == hoopR actual:", f"{(m.Points == m.actual).mean():.1%}")
d = m.OU - m["median"]
print(f"book - Kalshi: mean {d.mean():+.2f}, MAE {d.abs().mean():.2f}, |diff|>=1: {(d.abs()>=1).mean():.0%}, >=2: {(d.abs()>=2).mean():.0%}, >=3: {(d.abs()>=3).mean():.0%}")
print(f"MAE vs actual: book {(m.OU-m.actual).abs().mean():.2f}, Kalshi T-1h {(m['median']-m.actual).abs().mean():.2f}")
for thr in (1, 2, 3):
    x = m[d.abs() >= thr]; s = np.sign(x.OU - x["median"]); won = np.sign(x.actual - x["median"]) == s
    x = x[x.actual != x["median"]]; won = won.loc[x.index]
    n = len(x); p = won.mean(); se = np.sqrt(p*(1-p)/n)
    print(f"|diff|>={thr}: n={n}, actual landed on the book's side of Kalshi's line {p:.1%} (95% CI {p-1.96*se:.1%}-{p+1.96*se:.1%})")
