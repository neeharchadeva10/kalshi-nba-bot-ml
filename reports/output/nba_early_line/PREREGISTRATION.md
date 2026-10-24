# Pre-registration — research log #58: does the points formula beat Kalshi's EARLY total line (hours before tip)?

Written by Opus on 2026-10-22 at 22:09Z. The user chose "Bet earlier lines" (2026-10-22) after #56 and #57 found
nothing at or near the close. Changes go in "Amendments".

## Idea

Kalshi's NBA total markets open hours before tip, when fewer traders have priced them. If the early line is softer,
a formula using the same box-score information might be closer to the final than the early line, even though it
loses to the T-1h line (#56). The bot would then bet earlier.

**Who would pay:** early traders and makers quoting stale totals before the market firms up.

## Known before writing (disclosed)

- #56 confirmation, 2025-26: the formula vs the Kalshi T-1h median: MAE 15.38 vs 14.59, formula side 47% at 4+.
  - The early line is usually close to the T-1h line, so this test is NOT independent of #56.
  - A pass here must also pass a forward test on 2026-27 games before any money.
- #24 (moneylines): the pregame drift was unpredictable. #54 (totals): moves from T-5h to T-1h are small (sd about 1.1
  points over 4h) and don't continue.
- No early-line vs formula comparison has been computed.

## Data (on disk)

- **Kalshi KXNBATOTAL 2025-26:**
  - #44 1-minute candles, every strike, T-8h to T: reports/output/nba_injury_speed/candles.sqlite.
  - #37/#42 hourly candles T-4h..T-1h: reports/output/nba_totals_pace/totals.sqlite.
  - T = the scheduled start.
- **Formulas (both already frozen, no new fitting):**
  - F56 = #56's frozen primary (flat 7791), via reports/output/nba_formula_accuracy/fa_lib.py.
  - F42c = the live #42 winner + 4.0 points (the lean correction pre-registered as #40 v6).
  - Inputs are only box scores from games before the ET game date. That is the same information at any hour of the
    game day.

## Definitions

- **Early time t_e ∈ {T-8h, T-6h, T-4h}.**
- **Early line L_e:** the implied median total (the YES-mid 0.50 crossing; books with spread ≤ 0.15) from each
  strike's last 1-minute candle ending at or before t_e, no older than 30 min. Missing → skip ("no early line").
- **Main strike at t_e:** YES mid nearest 0.50, within 15-85c, spread ≤ 0.10, quote no older than 30 min.
- **Bet:**
  - If |proj - L_e| ≥ g, buy the formula's side at the main strike: YES (over) at yes_ask; NO (under) at
    1 - yes_bid.
  - Taker, +1c slippage on the entry, fee 0.07·P·(1-P) rounded up to $0.0001, 1 contract, held to settlement.

## Versions (all counted)

formula {F56, F42c} x t_e {T-8h, T-6h, T-4h} x g {2, 4, 6} = **18 versions**.

## Multi-source data check (the user: "make sure you are using multiple sources")

- **T-4h:** L_e from the 1-minute table vs the hourly table (separate downloads and pipelines). Report agreement
  (median |diff|, share within 0.5 point). Any game differing by more than 1.5 points is excluded from BOTH tables'
  results and listed.
- **Finals:** the hoopR final vs Kalshi end-of-game (NO-settled strikes) vs the odds-API close file's game rows. All
  three are reported; Kalshi settlement results decide P&L.
- **Formula:** F56 is reproduced to 0.0 against #56's frozen projections on all 2025-26 games before any bet is
  computed.

## Split, selection and luck test

- **Discovery:** ET dates 2025-10-21 to 2026-01-22. **Unseen:** 2026-01-23 to 2026-04-12 (regular season), run once
  after an Opus audit creates AUDIT_OK (with code hashes).
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n (net per contract) among versions with n ≥ 60.
- **Luck test:** shared random sides per game and t_e, 500 reps, seed 20261022, with the 18-version selection
  re-run. FAIL if more than 5% of reps reach the real best LB.

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the game-bootstrap 95% LB > 0 (10,000 draws, seed 20261022).
2. At least 60 unseen bets.
3. Coin flip (random side, same strikes and candles, 500 copies) ≤ 5%.
4. The discovery luck test passed.

A pass then needs a forward test on 2026-27 games (paper first, the user's hybrid policy) before any money.

## Reported regardless (discovery only)

- MAE of L_e vs the actual final for each t_e, beside the T-1h median and the formula's MAE. Is the early line
  softer?
- The drift L_(T-1h) - L_e, and whether the formula's side predicts the drift direction.
- Book availability and spreads at each t_e.

## Versions

18.

## Amendments

(none)
