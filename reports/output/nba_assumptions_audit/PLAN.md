# Research log #63: assumptions audit of the E1 score model (DESCRIPTIVE diagnostic)

Written by Opus on 2026-10-23T03:14Z. The user: "lets diagnose everything we are assuming like this in this formula" and "keep our
options open".

## Nature

- Descriptive only. No rule or model is chosen here.
- The data are residuals r = actual - E1 (game total, and team score) on 2019-20..2025-26 regular season. These games
  are out of sample for every fitted E1 part (the ridge was trained on 2008-19).
- About 25 slices are examined, so about 1 in 20 will look "significant" by chance. Any fix suggested here must be
  pre-registered and confirmed on NEW games (the forward tracker) before it counts.

## Assumptions to check

Each one gets: mean residual and game-bootstrap 95% CI (seed 20261023, 2,000 draws), the n of games, and the points it
could add back.

**Data**

| Id | Check |
|---|---|
| D1 | Final scores: hoopR = Kalshi (done, 578/578) and = OddsData (16 OddsData typos) |
| D2 | The possession formula: 0.96 x (FGA - OREB + TOV + 0.44 x FTA). Sensitivity of team ratings to 0.40/0.475 FTA weights (correlation and level) |
| D3 | Neutral-site games (2020 bubble from 2020-07-30, NBA Cup finals) treated as home/away |
| D4 | All-Star/exhibition rows excluded (done) |

**Model**

| Id | Assumption | Slice |
|---|---|---|
| M1 | Calibration: E1's predictions are not too extreme or too flat | Slope of actual on predicted, in deciles of the predicted total |
| M2 | Team quality is stable within the season | Residual by month and by games played (early season, post-deadline, last 2 weeks) |
| M3 | Last season's ratings carry over early | Residual for each team's first 10 games, by roster continuity (share of last season's minutes returning) |
| M4 | Home court is the same everywhere | Residual by home team (30), and altitude (DEN/UTA) |
| M5 | Rest effects are simple | Back-to-back (one or both), 3-in-4, 4-in-6, long rest (3+ days) |
| M6 | Travel and time zones don't matter | West-to-east and east-to-west trips, road-trip game number (1st..5th+), time-zone change since the last game |
| M7 | Matchup is additive (offence and defence average) | Residual by pace pairing (fast vs fast, slow vs slow) and offence-vs-defence quality pairing |
| M8 | Blowouts are not special | Residual by predicted margin size (big favourites) |
| M9 | Overtime is not modelled | Residual of OT games vs non-OT, and OT rate by predicted margin |
| M10 | The scoring era is stable | Residual by season (bias drift) |
| M11 | The same model fits every team | Residual by team (own and opponent side) |
| M12 | Motivation and schedule don't matter | The last 10 games of the season for teams out of playoff contention vs contenders (standings to date), nationally televised games are not available (noted), rivalry not available |
| M13 | Injuries | Done in #62: the total is unaffected, team scores improve |
| M14 | Late-season rest and tanking | Minutes of the usual starters in the last 2 weeks (after the fact; descriptive) |

**Evaluation**

| Id | Check |
|---|---|
| E1 | MAE vs RMSE: is E1 tuned for the median? Mean vs median of the residual |

Output: AUDIT.md with one plain-language row per assumption: holds / violated / size in points / possible fix.

## Part B: judgment calls (definitions, thresholds, windows), added 03:26Z

The user: "like what about assumptions like key players". This part is descriptive sensitivity analysis. For each item:
- what we assumed;
- the alternatives tried;
- whether the conclusion or accuracy changes (and by how much).

**Player definitions**

| Id | Assumption | Alternatives |
|---|---|---|
| J1 | Key player = 28+ mpg | 24/28/32 x {player's own last 10 games played, team's last 10 games (min 5)}. Show how the "key players out" counts and the E1-vs-line gaps change |
| J2 | E2 rotation threshold = 15 mpg | 10/15/20 |
| J3 | Player window = last 10 games | 5/10/20 |
| J4 | Questionable handling | Separate, 0.5 weight, ignored |
| J5 | Player value = recent PPG | Minutes share, PPG, on-court +/- (from box plus_minus if present). Which best explains the team-score residual when he is out (2021-24 only; correlation, descriptive) |
| J6 | Unmatched names count as 0 | Who they are (share rookies / new signings / two-way); the residual in games with unmatched rotation-likely names |

J2-J4 report the E2-style validation MAE on 2023-24 only. This is descriptive and changes no frozen model.

**Timing**

| Id | Assumption | Check |
|---|---|---|
| J7 | The report at T-60 is the one that matters | The share of listed players whose status changes between the T-60 report and the last report before tip (late scratches and late returns) |
| J8 | League-average "settles" after 50 games | 25/50/100 |

**Betting math**

| Id | Assumption | Check |
|---|---|---|
| J9 | +1c slippage vs the price seen | Every REAL fill in logs/trade_ledger.csv and logs/early_overs_bot.jsonl: fill price vs the ask at order time (bot log body/limit; ledger vs 1-min candles where cached). Realised slippage distribution |
| J10 | Kalshi line = the YES-mid 0.50 crossing (spread ≤ 0.15) | The main-strike mid, a volume-weighted crossing, and a spread cap of 0.10. MAE of each vs the actual (2025-26) |
| J11 | The fee formula | Real fills vs 0.07·P·(1-P) rounded (ledger) |
