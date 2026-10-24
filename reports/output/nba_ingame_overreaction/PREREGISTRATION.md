# Pre-registration — research log #46: does Kalshi's live NBA total overreact to early scoring?

Written by Opus on 2026-10-22 at about 06:50Z, before any in-game Kalshi total price was compared with a later
outcome. The idea is from the user's slate ("In-game total overreaction"). Changes go in "Amendments".

## Where the line-setter is weak (the idea)

During a game, the live total is pulled toward the current scoring pace. A hot or cold first quarter is mostly
noise: shooting luck regresses. If the live market extrapolates the early pace too much, its in-game line ends up too
far from the pregame line. Betting back toward the pregame line (fading) then wins. If the market under-reacts,
following wins.

**Who would pay:** live bettors chasing the scoreboard.

## Known before writing

- Angelini & De Angelis: Kalshi NBA live *moneyline* prices pass through only about 0.64 of game-state changes, an
  under-reaction. That is for winners, not totals.
- Our NFL in-game dips and jumps (#18/#19) were priced right.
- No in-game KXNBATOTAL price has been examined.

## Data (official, free)

- **Kalshi KXNBATOTAL 1-minute candles,** from tip to settlement, every 2025-26 game.
  - Download: the #44 pattern (historical endpoint, period_interval = 1, raw cached), extended past tip.
  - Reuse #44's cached pre-tip candles for the pregame line.
- **hoopR play-by-play 2025-26** (data/raw/nba_open/play_by_play_2026.parquet): the wallclock of the first event of
  Q2, Q3 and Q4. These are the checkpoints.

## Definitions

- **Pregame line L0:** the implied median total (the YES-mid 0.50 crossing, as in #44, books with spread ≤ 0.15) at
  the last candle ending at or before tip.
- **Checkpoint C:** the wallclock of the first pbp event of Q2 (end of Q1), Q3 (halftime) or Q4 (end of Q3).
  - The decision candle is the first candle ending at or after C + 60 s.
  - The live line L_C is taken at that candle.
- **Move** = L_C - L0. **Trigger:** |move| ≥ T points.
- **FADE:** bet back toward L0. If L_C > L0, buy NO on the live main strike (mid nearest 0.50, spread ≤ 0.10);
  otherwise buy YES. **FOLLOW:** the opposite side.
- **Execution:** taker at that candle's ask of the chosen side, 1 contract, fee 0.07·P·(1-P) rounded up to $0.0001.
  - Slippage: +1c on the entry price, because in-game books move fast and the candle close may not be fillable.
- **Exit:**
  - **HOLD:** to settlement; net = 1(win) - entry - fee.
  - **NEXT:** sell at the bid at the next checkpoint's decision candle (or the last candle before the end of Q4),
    with the taker fee and -1c slippage.

## Versions (all counted)

checkpoint {Q1 end, halftime, Q3 end} x T {4, 6, 8, 10} x direction {fade, follow} x exit {hold, next} =
3 x 4 x 2 x 2 = **48 versions**. One bet per game per version, at the first checkpoint that triggers for that
version's checkpoint.

## Split, selection and luck test

- **Discovery:** ET dates 2025-10-21 to 2026-01-22. **Unseen:** 2026-01-23 to the end of the regular season,
  run once for the selected version.
- **Selection:** the highest discovery LB = mean - 1.96·sd/√n (net per contract) among versions with n ≥ 60.
- **Luck test:** shared random direction per game and checkpoint, 500 reps, seed 20261022, with the same 48-version
  selection re-run.
  - If the share of reps with best LB ≥ the real best LB is more than 5%: FAIL (no unseen run).

## Pass bar (the selected version on UNSEEN; all must hold)

1. Mean net > 0 and the bootstrap 95% LB (by game, 10,000 draws, seed 20261022) > 0.
2. At least 60 unseen bets.
3. Coin flip: random direction at the same checkpoint candles, 500 copies; at most 5% do as well.
4. The discovery luck test passed.

## Reported regardless

- Calibration: for each checkpoint, the final total minus L_C against the move. A negative slope means
  overreaction.
- Spreads at checkpoints.
- Games skipped and why: no pbp match, missing candles, no crossing.

## Versions

48.

## Amendments

- **2026-10-22 08:22Z (Opus), data-quality guards from the pre-unseen leak audit.** These were added after the discovery run and
  BEFORE any unseen data was analysed. The auditor checked that each one leaves discovery unchanged.
  1. **bad_wallclock:**
     - Skip a game if its Q1 checkpoint C is outside [tip + 20 min, tip + 80 min].
     - This catches hoopR wallclock errors, e.g. the +1 h shift on the DST day, 2026-03-08, that would put a "Q1" bet
       in Q3.
     - Every used discovery Q1 record lies in [28.3, 63.5] min.
  2. **In-game quotes come from in-game candles only:**
     - L_C, the main strike and exits use only candles ending after tip. Pre-tip candles are used for L0 only.
     - Discovery used no pre-tip quote at any checkpoint.
  3. **stale_L0:** skip a game whose Kalshi decision base T is more than 1 h before the hoopR tip (a game played a day
     late). No discovery game is affected.
  4. **Coverage gate:**
     - The unseen run refuses unless every unseen market has a final download state (ok or 404) in BOTH the pre-tip
       and the in-game candle tables.
     - Permanent 404s are skipped and reported by reason.
  5. **Reporting:** unseen writes per-bet records and the same diagnostic blocks as discovery (checkpoint skip
     reasons, calibration, spreads, quote ages), as the "reported regardless" section requires.
  6. **No rule change:** the selected version (Q1, |move| ≥ 10, follow, hold), the costs and the pass bar are unchanged
     and pinned in code.
