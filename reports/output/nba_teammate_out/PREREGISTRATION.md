# Pre-registration — research log #48: when a star is ruled out, do his teammates' Kalshi props under-adjust?

Written by Opus on 2026-10-22 at about 07:20Z, before any Kalshi prop price was compared with injury news or
outcomes. The idea is from the user's slate ("Teammate-out prop boost"; "think of yourself as DraftKings and
counter that"). Changes go in "Amendments".

## Where the line-setter is weak (the idea)

When a high-usage player is ruled Out, his shots, rebounds and assists go to teammates. A prop market that adjusts
the star's own lines quickly may move the **teammates'** lines too little, especially role players with thin prop
markets. Buying the over on the beneficiaries' main lines then wins.

**Who would pay:** prop sellers who don't re-price the supporting cast.

## Known before writing

- #31 and #32: injury news is priced into game lines by T-1h.
- Props have never been tested here. #36 was pre-registered but paused before any data was analysed.
- **Kalshi prop rule:** "If a player is active but never takes the court, the market settles to the last fair market
  price before game start." Such settlements are non-binary. They are valued at their settlement value, and counted
  and reported.

## Data (free; official)

- **Kalshi KXNBAPTS / KXNBAREB / KXNBAAST**, 2025-26 (props start 2025-11-18).
  - Definitions are cached in reports/output/nba_props/nba_props.sqlite (`mk`).
  - Hourly candles come from the historical market candlestick endpoint (per market; the event-level endpoint
    returns nothing for historical-tier events), T-4h to T-1h. Raw responses are cached.
- **Download scope, to cut about 77k markets down:** for each player-stat-game, only the 2 strikes nearest the
  player's pre-game expectation. That is the mean of his last 10 games of that stat before that date (hoopR
  player_box), with no look-ahead. This applies to every player in the game, so the luck test has a matched pool.
- **Official NBA injury reports** (data/raw/nba_injury, parsed by reports/output/nba_model/injury_parse_v3.py), with
  publish times from /CreationDate.
- **hoopR player box 2025-26:** minutes, usage proxies (FGA + 0.44·FTA + TOV per minute) and outcomes.

## Definitions

- **Decision time:** T-1h, where T = the KXNBAGAME occurrence - 3 h (the repo rule). Prices come from the last hourly
  candle ending at or before T-1h.
- **Star Out event:** a player with average minutes ≥ M_star over his team's last 10 games before the date, listed
  **Out** on the last official report published before T-1h for that game.
  - He must have played in at least 1 of his team's previous 3 games, so a long-term absence is already priced.
- **Beneficiaries:**
  - **top1:** the active teammate with the highest usage-per-minute over the last 10 games, with ≥ 20 min average;
  - **rotation:** all active teammates averaging ≥ 20 min.
  - "Active" means not listed Out or Doubtful on that report.
- **Bet:** buy YES (over) on the beneficiary's stat at the downloaded strike whose YES mid is nearest 0.50 and within
  15-85c, with ask - bid ≤ 0.10.
  - Taker at the ask + 1c slippage, plus the fee 0.07·P·(1-P) rounded up to $0.0001, 1 contract, held to settlement.
  - Net = settlement value - price - 1c - fee.

## Versions (all counted)

stat {PTS, REB, AST} x M_star {28, 32} x beneficiaries {top1, rotation} x news timing {any, late = the Out first
appeared on a report published within 3 h before T-1h} = 3 x 2 x 2 x 2 = **24 versions**.

## Split, selection and luck test

- **Discovery:** game dates 2025-11-18 to 2026-01-22. **Unseen:** 2026-01-23 to the end of the regular season,
  run once for the selected version.
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n among versions with n ≥ 60.
- **Luck test, price-matched:** 500 reps, seed 20261022. Each bet is replaced by a random over bet on another active
  player's prop with the same stat on the same night, with entry price within 2c, and the 24-version selection is
  re-run.
  - FAIL if more than 5% of reps reach the real best LB.
  - This separates "teammate out" from "props overs in general".

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0.
2. At least 60 unseen bets.
3. The price-matched coin flip on unseen: at most 5% of 500 copies do as well.
4. The discovery luck test passed.

## Reported regardless

- **The general props-over base rate:** win rate vs price for all main-line overs, discovery only.
- **Calibration of beneficiary bets:** actual stat minus the strike.
- **Counts:** "settled at fair price" (player never took the court) cases, and skipped bets with reasons.

## Versions

24.
