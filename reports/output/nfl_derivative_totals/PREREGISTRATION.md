# Pre-registration — research log #68: NFL Kalshi derivative totals vs the game line

Written by Opus at 2026-10-23T23:08:19Z (`date -u`). Written BEFORE any KXNFLTEAMTOTAL / KXNFL1HTOTAL / KXNFL2HTOTAL price
history was downloaded or examined. That download starts at 2026-10-24 02:00Z inside #66's downloader. Changes go in
"Amendments".

## Idea, and who loses

- Kalshi lists thin "derivative" totals ladders next to the deep game-total ladder (KXNFLTOTAL) and spread ladder
  (KXNFLSPREAD):
  - team totals (KXNFLTEAMTOTAL);
  - first-half totals (KXNFL1HTOTAL);
  - second-half totals including overtime (KXNFL2HTOTAL).
- Their centres must hang together:
  - home TT + away TT = game total;
  - home TT − away TT = home margin;
  - 1H + 2H = game total.
- **Hypothesis:** the thin ladders lag or drift from what the deep markets imply, and the gap is big enough to pay for
  the spread and fee.
- **Who loses:** makers and takers in the thin ladders who don't re-price off the deep markets.
- **Known before writing:**
  - The NBA analogue (#49, game vs team totals vs 1H) FAILED: thin books with 14-16c spreads; best -0.25c; luck 45%.
  - A public snapshot (the paper tape, 2026-10-23) shows NFL TT/1H/2H ladders exist with 176-355 open markets. Only
    counts were seen; no prices were analysed.
  - Expected: FAIL for the same reason as #49, unless NFL derivative books are tighter.

## Data (read-only public GETs; cached)

- KXNFLTEAMTOTAL, KXNFL1HTOTAL, KXNFL2HTOTAL and KXNFLTOTAL markets plus hourly and 1-minute candles, from #66's
  downloader (reports/output/nfl_totals/kalshi/). Each series is filtered by its own event_ticker prefix.
- KXNFLSPREAD 1-minute quotes: data/nfl_spread.sqlite (sp_quotes, 2025-08-26 to 2026-10-13), plus any newer download.
- Finals: nflverse home/away scores. Half scores come from pbp (the score at the end of Q2), checked against Kalshi
  settlement brackets for each series.

## Fair centres at decision time τ (from deep markets only)

- **M_tot** = the KXNFLTOTAL implied median (YES-mid 0.50 crossing, strikes with spread ≤ 10c).
- **S** = the KXNFLSPREAD implied median home margin (the same method on its ladder).
- **Team-total fair centre:** home (M_tot + S)/2, away (M_tot − S)/2.
- **Halves:** 1H fair centre = r1 × M_tot, with r1 = the mean of (1H points / game points) over nflverse 2015-2024
  (pbp). The 2H fair centre = M_tot − 1H fair centre.
- **Each derivative ladder's own implied median** D_x uses the same method (strikes with spread ≤ 10c). A ladder with
  no crossing is skipped.

## Bets

- For each derivative ladder x with gap = D_x − fair_x:
  - if gap ≥ +g, buy NO (UNDER) at x's main strike;
  - if gap ≤ −g, buy YES (OVER) at x's main strike.
- Main strike = YES mid nearest 0.50 among two-sided strikes with spread ≤ 10c.
- 1 contract, taker ask + 1c + fee 0.07·P·(1−P), P = ask + 1c, rounded up to /usr/bin/bash.0001.
- **Grid:** τ ∈ {T-24h, T-60} × g ∈ {1, 2, 3} points × family ∈ {team totals, halves} = **12 versions**.

## Windows

- **Selection:** games before 2026-01-01 (the 2025 regular season). The chosen version has the highest mean net per
  contract with ≥ 60 bets. STOP if ≤ 0.
- **Holdout, run once after an Opus audit:** games on or after 2026-01-01 (2025 playoffs and 2026 to the run date).
- **Forward:** the paper tape.

## Verdict (as #66 Part C)

- **PASS:** holdout mean net > 0 with game-bootstrap LB > 0 (10,000, seed 20261023), ≥ 30 bets, and luck < 5%. Luck
  copies: 500, the same number of bets per game on random strikes and sides of the same derivative ladders at their
  real prices, seed 20261024.
- **PROMISING:** holdout mean > 0 and luck < 10% → forward paper tracking.
- **FAIL:** otherwise.

## Double checks

- A second implementation of the fair centres and bet decisions on 100 random game-snapshots must agree exactly.
- Settlement bracket gates for each series.
- The spread-sign convention is checked on 10 games by hand (home favourite → S > 0).
- No candle after τ.
- An Opus leak audit, then AUDIT_OK, then the holdout once.

## Versions

12.

## Amendments

(none)
- **Amendment 1, 2026-10-23T23:32:12Z (Opus; date -u), before any derivative-ladder price was examined: sign-off of
  main/README.md readings 1-15, with two changes and one data step.**
  - **Changes:**
    - **Reading 5 is changed:** 1H points = total_home_score + total_away_score on the LAST play with qtr ≤ 2 (the
      end-of-play score). The first Q3 play could itself score, e.g. on a kickoff-return TD. The per-series bracket
      gate verifies this against Kalshi settlement.
    - **Reading 7 is changed:** a derivative ladder whose markets are not all settled yes/no is dropped, the same as
      #66.
  - **Data step:** KXNFLSPREAD quotes are extended past 2026-10-13 with the existing read-only collector
    (`kpm spread-collect`) after tonight's totals download, before selection. Games still without S have no
    team-total bets; halves are unaffected.
  - **Readings 1-4, 6 and 8-15 are accepted as written.** The team_side format in reading 6 is verified on the real
    download by the team-resolution gate.
