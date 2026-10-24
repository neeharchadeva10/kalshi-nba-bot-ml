# Pre-registration — research log #50: NFL player props after usage shocks (Kalshi)

Written by Opus on 2026-10-22 at about 07:40Z, before any NFL prop price or outcome was analysed. The idea is from
the user's slate ("NFL player props"; "think of yourself as DraftKings and counter that"). Changes go in
"Amendments".

## Where the line-setter is weak (the idea)

Prop lines lean on a player's season averages. When usage shifts suddenly, the role player's line may lag:
- a starter at the same position is ruled Out (RB1 out → RB2 carries; WR1 out → WR2/TE targets);
- or his recent snaps or targets jump.

Buying the over on the beneficiary then wins.

**Who would pay:** prop sellers anchored to season averages.

## Known before writing

- Settled KXNFLRSHYDS events exist for the 2026 season (e.g. 26OCT19WASSF; checked 2026-10-22). Whether 2025 props
  exist on Kalshi is unknown.
- No NFL prop price has been looked at.

## Step 0, feasibility gate

- Count settled Kalshi NFL prop markets with hourly candles at T-1h, for KXNFLRSHYDS, KXNFLRECYDS, KXNFLREC and
  KXNFLPASSYDS, by season.
- Unless at least 150 qualifying beneficiary bets can exist in a discovery half, STOP and convert this idea to a
  forward paper test (the user's hybrid plan). No backtest is run then.

## Data (free)

- **Kalshi NFL prop definitions and hourly candles** to T-1h, from the historical/live market endpoints, cached.
  Same 2-strike scope rule as #48: the strikes nearest the player's mean over his last 4 games before the date.
- **nflverse open data** (GitHub releases, free): weekly player stats (carries, targets, receptions, yards) and the
  weekly injury reports (game status Out / Doubtful / Questionable), for 2024-2026.
  - Download the season files once into data/raw/nflverse/, GET only.

## Definitions

- **Decision:** T-1h (T = Kalshi occurrence - 3 h).
- **Shock A, teammate out:** a same-position-group teammate (RB for rush yards; WR/TE for receiving) with ≥ 50%
  share of the group's carries or targets over the last 4 games is listed Out on the final injury report.
  - Beneficiary: the active player with the next-highest share.
- **Shock B, usage jump:** the player's share over the last 2 games ≥ his season-to-date share + J (in percentage
  points).
- **Bet:** YES (over) at the main strike (mid nearest 0.50, 15-85c, spread ≤ 0.10), as a taker at the ask + 1c,
  fee 0.07·P·(1-P) rounded up, 1 contract, held to settlement.

## Versions (all counted)

stat {rush yds, rec yds, receptions} x shock {A, B with J = 10, B with J = 20} = 3 x 3 = **9 versions**.

## Split, selection and luck test (if the gate passes)

- **Discovery:** the earlier half of eligible weeks by date. **Unseen:** the later half, run once.
- **Selection:** the highest discovery LB among versions with n ≥ 40.
- **Luck test:** price-matched random over bets on other players' props of the same stat in the same week, 500 reps,
  seed 20261022, with the selection re-run. FAIL if more than 5% of reps reach the real best LB.

## Pass bar (unseen)

1. Mean net > 0 and the bootstrap LB (by game) > 0.
2. n ≥ 40.
3. The price-matched coin flip at most 5%.
4. The luck test passed.

## Versions

9.
