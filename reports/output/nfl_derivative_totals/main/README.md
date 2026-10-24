# #68 NFL derivative totals vs the game line: main implementation

Files: `ldr_lib.py` (everything), `fit_r1_main.py` (r1, already run), `gates.py` (sign check, bracket gates, counts), `make_agree.py`
(agreement sample, after the download), `run_select.py` and `run_test.py` (NOT run), `make_audit_hashes.py`, `test_ldr.py` (31 tests).
Re-uses read-only from `reports/output/nfl_totals/main`: `nfl_lib.py` (games, fee/net, bootstrap, luck, audit helpers) and `part_c.py`
(snapshot, two-sided test, implied median, main-strike picker, verdict rule).

Run order after the download (2026-10-24 02:00Z+): `gates.py` (bracket + counts) -> `make_agree.py` -> Opus agreement check -> `run_select.py`
-> audit -> AUDIT_OK + test_started.json -> `run_test.py`. Nothing here prints or saves a bet outcome unless run_select / run_test is run.

## Spread sign convention
S = implied median HOME margin (home points - away points). Home favourite -> S > 0.
`sp_markets` row (team T, strike K): YES iff T wins by MORE than K (K a half-integer > 0; every ladder has positive strikes for BOTH teams).
Home-team row: point (x = +K, P(home margin > x) = YES mid). Away-team row: YES iff home margin < -K, so P(home margin > -K) = 1 - YES mid:
point (x = -K, bid' = 1 - ask, ask' = 1 - bid). One ladder in x, median by `C.implied_median` over strikes with spread <= 10c.
Checked on 10 named games in `gates.py` (printed, all 10 agree with the sign of nflverse spread_line, positive = home favoured).

## Readings needing Opus sign-off (the most literal reading was implemented)
1. **Spread quote time.** sqlite has only sparse 1-minute quotes (`ts` = candle end). For both taus I use the last quote with ts in
   [decision - max_age, decision] with the same windows as the totals snapshot (T60: 60 min; T-24h: 3 h), two-sided test as #66.
2. **Spread data ends 2026-10-13** (sqlite). Games after that have no S, so team-total bets are impossible for them (halves need only M).
   No newer spread download is wired in. Spread ladders with <2 strikes of spread <= 10c, or no 0.50 crossing, give S = NaN (e.g. 2025_01_CIN_CLE).
3. **Median spread filter.** M_tot, S and every D_x use only strikes with spread <= 10c (as the pre-registration says); `C.add_median`'s
   default (all strikes) is NOT used. Main strike = `C.pick_main_strike` (spread <= 10c, mid nearest 0.50, tie -> lower strike).
4. **Gap threshold** `gap >= g` / `gap <= -g` with a 1e-9 float tolerance (gap 0.9999999999 counts as 1).
5. **r1** (Amendment 1) = mean over games of end-of-Q2 points / final points incl. OT; 1H points = `total_home_score + total_away_score` on the LAST pbp play with qtr <= 2. 39 pbp-bad games excluded; 12 of 2743 games dropped. **r1 = 0.50551** (n = 2731). Settlement uses the same 1H definition.
6. **Team ownership of KXNFLTEAMTOTAL ladders**: letters of `ticker_suffix` (e.g. `PHI43` -> PHI), compared to the nflverse home/away code
   after Kalshi alias normalisation (JAC->JAX, LAR->LA, WSH->WAS). Unresolved markets are dropped and counted (`gates.py`). The event
   ticker is assumed to start with `KXNFLTEAMTOTAL-`. The real file layout is unverified (smoke has only KXNFLTOTAL); if the suffix
   format differs, `team_side` is the one function to change.
7. **Unsettled** (Amendment 1): a derivative ladder (per series, per game) is dropped unless all its markets settled yes/no (`drop_unsettled_derivative`). Outcomes still come from nflverse / pbp.
8. **2H settles on final - 1H** (includes OT), 1H on pbp. Strike YES iff actual > K; NO otherwise (no pushes at half-integer strikes).
9. **Windows by nflverse ET gameday**: select < 2026-01-01, test >= 2026-01-01. Prices for both windows are built together (price side only);
   outcomes are attached per window and `attach_actual` raises on rows of the other window.
10. **Luck**: per game, the same number of bets as the real version drawn on random strikes (without replacement) and random sides from the
    pool of strikes with spread <= 10c of that family's USABLE ladders at that tau (real asks, +1c, fee); 500 copies, seed 20261024.
    Bootstrap resamples games (10,000, seed 20261023), 2.5th percentile = LB, verdict = `C.verdict_c` (PASS: mean>0, LB>0, n>=30, luck<5%).
11. **Several bets per game** (up to 2 per family) share one game in the bootstrap (game-level resampling).
12. **Usable ladder** = D (crossing), fair centre (needs M, and S for team totals), main strike and its quote all present.
13. **Candles**: `C.snapshot` takes the last candle ending in the window, and `snapshots_for` asserts end_ts <= decision. The data holds
    candles up to kickoff+15 min; none after the decision is used.
14. **Agreement sample**: seed 20261028, 100 (game, tau, family) snapshots without replacement from the sorted usable list; one row per usable
    ladder with M, S, fair, D, gap, K, bid, ask and the side at g = 1, 2, 3.
15. `AUDIT_FILES` includes the 498 MB `data/nfl_spread.sqlite` (hash takes seconds); its -wal file is empty and the db was last written 2026-10-13.
