# Pre-registration — research log #66: NFL game totals (formula families, researched factors, Kalshi alt lines)

Written by Opus on 2026-10-23 at about 22:30Z. Rules are fixed before any test is run. Changes go in "Amendments".

## What has already been seen (disclosed)

- **The literature survey** (research_notes/NFL and NBA totals factors/survey.md) ran descriptive over/under splits on
  nflverse closes for **2015-2025**: wind bands, primetime, cold, dome, rest, referees, QB changes, interim coaches, the
  distribution of (total − close) and key-number landing rates. These are logged as 45 versions.
  - So 2015-2025 is a **disclosed second look** for every factor rule (Part B) and for the factor features inside
    A-RES.
  - Nothing compared a formula's prediction with the line, so 2015-2025 is clean for the A-PTS / A-EPA / A-DRIVE
    sides.
- **Leads** (hypotheses, not facts):
  - observed wind 10-19 mph → under (about 59%, 2015-25, but about 50% in 2024-25);
  - primetime unders (about 54%, z about -2 after about 25 splits);
  - Kalshi ladders appear to already follow the right-skewed shape (one snapshot).
- **Literature:** NFL closes are close to efficient; the old "high-total under" died about 2010 (Kelly & Chen);
  open-to-close moves are about half reversed but not after vig (Moskowitz 2021); referee totals trends are noise.
- **Expected outcome:** most of this FAILS. The aim is to find either a PASS or a PROMISING lead worth forward paper
  trading. Nothing is called an edge unless it passes below.

## Data (free, cached, read-only GET)

- **nflverse** games.csv (closing `total_line`; `roof`, `temp`, `wind`, coaches, referee, QB ids, rest, div_game,
  gametime ET), pbp 1999-2026, injuries 2009-2026, officials, rosters.
  - Downloaded 2026-10-23 with sha256 manifests (reports/output/nfl_totals/data/).
  - games.csv has no license, so it is private research only.
- **Kalshi** KXNFLTOTAL markets, settlements and candles (hourly from listing; 1-minute for the last 6 h), from public
  GETs (reports/output/nfl_totals/kalshi/).
  - fee_type quadratic_with_maker_fees, multiplier 1.
- **Weather forecasts:** Open-Meteo (free, no key).
  - The Previous Runs API is used for forecasts issued about 1 day before; the Historical Forecast API for short lead.
  - Stadium coordinates come from a hand-checked table with sources.
  - Forward: the Open-Meteo forecast API, fetched before each decision time and stored with its fetch time.
- **Finals:** nflverse home_score + away_score.
  - Checked against Kalshi settlement brackets for every Kalshi game (smoke: 83/83 agree).
  - The 39 games whose pbp running score disagrees are excluded from pbp features only (their schedule finals stand).

## Universe and costs

- **Games:** regular season and playoffs with a non-missing `total_line`. Team codes are mapped across eras
  (OAK→LV, SD→LAC, STL→LA).
- **Sportsbook P&L:** flat −110. A win is +0.90909 per 1 staked, a loss −1, and a push (integer line) 0. Break-even is
  52.38% of decided bets.
- **Kalshi P&L:** per contract, 1/0 payout − (ask + 1c slippage) − fee.
  - The fee is 0.07 × P × (1 − P) at P = ask + 1c, rounded UP to $0.0001. That matches the live fills, e.g. 1.75c at
    51c.
  - Taker only; held to settlement.
  - **Decision time T-60** = kickoff − 60 min, using the last 1-minute candle at or before it, two-sided quotes only.
  - The main strike is the strike whose YES mid is nearest 0.50. The Kalshi "line" is the implied median (the
    interpolated YES-mid 0.50 crossing).
  - A ladder whose main strike has spread > 10c is skipped.

## Windows

| Window | Seasons | Use |
|---|---|---|
| W | 1999-2005 | Rating warm-up only, no bets |
| D | 2006-2014 | Selection (Part A). Clean confirmation for B1/B4 (never examined here) |
| H1 | 2015-2022 sportsbook | Part A holdout (clean for the model side); Part B second look |
| H2 | 2023-2025 sportsbook | Part A holdout; Part B second look |
| H3 | 2026 season, all games final before the holdout run | Clean for everything (small) |
| HK | Kalshi, every settled matched game with a usable T-60 ladder (2025 season + 2026 to the run date) | Real prices and costs |
| HK-sel / HK-test | Kalshi games before 2026-01-01 / on or after 2026-01-01 | Part C selection / holdout |
| F | Games from 2026-10-25 on, via the paper tape | Forward paper trading |

## Part A — formula families (bet the model's side of the close)

Every feature for a game uses only data strictly before its kickoff: previous games' scores and plays; injury reports
dated before the game date; coaches as of previous games.

- **A-PTS (points ratings).**
  - Per team: an offensive rating O = exponentially weighted (EW) mean of points scored, and a defensive rating
    D = EW mean of points allowed, over its previous games (regular season and playoffs, including last season) with a
    half-life of h games.
  - At each season start, last season's final O and D are pulled 1/3 of the way to the league mean. This season's games
    blend in with weight n/(n + k), n = games played this season.
  - L = league mean points per team-game over all games in the 365 days before kickoff.
  - Prediction = [L + (O_home − L) + (D_away − L)] + [L + (O_away − L) + (D_home − L)].
  - Grid: h ∈ {4, 8, 16} × k ∈ {2, 4, 8} = 9 models.
- **A-EPA (efficiency × pace).**
  - Per team: EW offensive EPA/play and defensive EPA/play allowed (pass and run plays with non-null epa; no kneels or
    spikes), and EW offensive plays per game. Half-life h games; the same season-start shrink, with k = 4.
  - Team points = c0 + c1·oEPA_team + c2·dEPA_opp + c3·plays_team + c4·plays_opp + c5·L, fitted by OLS on D team-games
    only. Prediction = the sum for both teams.
  - Grid: h ∈ {4, 8, 16} = 3 models.
- **A-DRIVE (drives × points per drive).**
  - EW (h = 8, k = 4) offensive points per drive, defensive points per drive allowed, and drives per game.
  - Team points = E[drives] × (PPD_off_team + PPD_def_opp)/2, where E[drives] = the mean of the two teams' EW drives per
    game. 1 model.
- **A-RES (market residual).** Ridge regression predicting (total − close), fitted on D with λ chosen by
  leave-one-season-out inside D. Features:
  - the three gaps (A-PTS h8 k4 − close, A-EPA h8 − close, A-DRIVE − close);
  - EW neutral-situation seconds per play and pass-rate-over-expected for both teams (pbp `pass_oe`, null → 0);
  - QB-out flag: the team's previous starting QB listed Out/Doubtful on the last injury report before the game date
    (2009+; 0 before);
  - head-coach change: the HC differs from the team's previous game this season, with the game within 4 of the change;
  - first-year HC;
  - rest days (each team) and short week;
  - primetime, divisional, neutral site, week, playoff;
  - the referee's penalties per game over his previous two seasons (pbp penalty plays; league mean if fewer than 10
    games);
  - the close itself.
  - **No weather, and no observed values from the game.**
- **Bet rule:**
  - A-PTS/EPA/DRIVE: OVER if prediction − close ≥ G, UNDER if close − prediction ≥ G, with G ∈ {1.5, 3, 4.5, 6}.
  - A-RES: bet the sign of the predicted residual when |prediction| ≥ g, with g ∈ {0.5, 1, 1.5, 2}.
- **Versions:** (9 + 3 + 1) × 4 + 1 × 4 = **56**.
- **Selection on D:** the highest mean P&L per bet among versions with ≥ 200 bets. Ties go to more bets.
  - **STOP** if the chosen D mean ≤ 0.
  - Every version's D row is reported.

## Part B — researched factor rules (no selection; each rule is fixed here)

| Rule | Definition | Side |
|---|---|---|
| B1 | Open-air stadium (roof = outdoors/open; NOT dome, closed or any retractable stadium), OBSERVED kickoff wind (games.csv) 10-19 mph | UNDER |
| B2 | Same stadiums, FORECAST wind at the kickoff hour 10-19 mph from the Open-Meteo run issued ≥ 24 h before kickoff (Previous Runs "day1"), wind at 10 m | UNDER |
| B3 | Control: B2 with a forecast ≥ 20 mph | UNDER (expected about 50%) |
| B4 | Primetime regular season: Sunday or Monday kickoff ≥ 19:00 ET (SNF, MNF) | UNDER |
| B5 | Control: Thursday regular-season games, week ≥ 2 | UNDER (expected about 50%) |

- **B2 falsification:** split B2's bets by (observed − forecast) wind ≥ 0 vs < 0.
  - If the win rate is > 52.38% only where observed exceeded the forecast, B2 is reported as "edge was hindsight",
    whatever its mean.
- **Clean sets:**
  - B1, B4, B5: D (2006-14, plus 1999-2005 reported separately) and H3.
  - B2, B3: H3 and HK only. Forecast archives start about 2021, and 2021-25 is a second look, reported but not used for
    the verdict.
- **Versions:** 5.

## Part C — Kalshi alt lines (the whole ladder)

- **Fair model**, fitted on nflverse finals 1999-2024 only, using no Kalshi prices:
  - P(T = t | M) ∝ f((t − M − μ)/σ_b) × w_t.
  - f = a Gaussian-kernel-smoothed density of (total − close), bandwidth 2 points.
  - μ = 0. σ_b by close band (≤ 40.5, 41-44.5, 45-48.5, ≥ 49).
  - w_t = the integer landing weight: empirical frequency of t / kernel-smoothed frequency, clipped to [0.4, 2.0].
  - Normalized over t = 0..120. M = the Kalshi implied median at the decision time.
  - fair_YES(s) = Σ_{t > s} P(T = t | M).
- **Bets:** each strike s at decision time τ:
  - buy YES if ask_yes + 1c + fee < fair_YES − m;
  - buy NO if ask_no + 1c + fee < (1 − fair_YES) − m, where ask_no = 1 − yes_bid.
  - At most one side per strike; every qualifying strike in a game is bet.
- **Grid:** τ ∈ {T-24h (hourly candle), T-60} × m ∈ {0, 2c, 4c} × zone ∈ {all, |s − M| ≤ 7, |s − M| > 7} = 18.
- **C-LS (longshot):** buy NO on every strike with YES ask ≤ 10c (taker), τ ∈ {T-24h, T-60} = 2.
- **Versions:** 20.
- **Selection on HK-sel:** the highest mean net per contract with ≥ 100 bets, ties to more bets. STOP if ≤ 0. Test once
  on HK-test.

## Verdicts (pre-declared; holdouts run ONCE)

**Part A (chosen version):**
- **PASS:** all three of
  1. pooled H1 + H2 mean > 0 with game-bootstrap 95% lower bound > 0;
  2. HK mean net > 0 with ≥ 30 bets;
  3. luck < 5%.
- **PROMISING:** H1, H2, H3 (if ≥ 10 bets) and HK (if ≥ 10 bets) point estimates all > 0, and luck < 10%.
- **Luck copies:** 500 copies with random sides on the same games.

**Part B (each rule):**
- **PASS:** clean-set mean > 0 with LB > 0, HK > 0 with ≥ 30 bets (B2/B4), and luck < 5%.
- **PROMISING:** every clean point estimate > 0 and luck < 10%.
- **Luck copies:** 500 copies with the same number of under bets per season on random games of that season.

**Part C (chosen version):**
- **PASS:** HK-test mean net > 0 with LB > 0 and ≥ 30 bets, and luck < 5%.
- **PROMISING:** HK-test mean > 0 and luck < 10%.
- **Luck copies:** 500 copies, each the same number of bets per game on random eligible strikes with random sides at
  their real prices.

**Common to all parts:**
- **Bootstrap:** by game, 10,000 draws, seed 20261023. Luck test seed 20261024.
- **FAIL:** anything else.
- **What follows:**
  - PASS and PROMISING go to forward paper trading on F (dashboard/log only, never Discord). PROMISING is labelled
    weak evidence.
  - Real money only by the user's decision, after the Hybrid policy's 4+ weeks and 100+ bets.

## Forward paper trading starting 2026-10-25 (independent of the backtest verdicts)

- **B2** (forecast wind 10-19 → under) and **B4** (primetime under) are paper-traded from the paper tape as
  **PRE-REGISTERED HYPOTHESES, not passers.**
  - Kalshi: the main strike at T-60, taker ask + 1c + fee, 1 contract.
  - B2 uses the forecast fetched ≥ 24 h before kickoff and stored with its fetch time.
- **Controls** on the same games: B3, B5, always-under, always-over and a coin-flip side.
- **Verdict:** after ≥ 100 bets (B4 gets about 2 per week, so this takes seasons) the forward record is reported
  against the controls. Nothing is promoted on the forward record alone before then.

## Double checks before selection

1. **Independent implementation** (check/, no shared code). It must agree exactly, or the differences are explained and
   amended before selection:
   - A-PTS predictions on 200 random games (seed 20261025);
   - A-EPA team features on 200 team-games;
   - B2 forecast joins on 100 games;
   - Part C fair values and bet decisions on 200 strike-snapshots;
   - bet counts and mean P&L in D for 10 random Part A versions (seed 20261026).
2. **Gates:**
   - nflverse total = home + away;
   - Kalshi settlement brackets agree with nflverse finals on every HK game;
   - the T-60 main-strike picker reproduces #42's choice on a synthetic ladder;
   - stadium coordinates are within 2 km of each stadium's listed coordinates.
3. **No-look-ahead unit tests:**
   - features exclude game g and anything later;
   - the injury report date is before the game date;
   - forecast issue time ≤ kickoff − 24 h for B2;
   - Kalshi candles end ≤ the decision time;
   - the A-EPA and A-RES fits use D rows only.
4. **Multi-source:**
   - nflverse close vs the Kalshi T-60 median (median |diff|, HK);
   - observed vs forecast wind (correlation, mean difference);
   - Kalshi finals vs nflverse.
5. **Rehearsal:** the holdout code paths run on D first.
6. **Opus leak audit** → AUDIT_OK (SHA-256 of code and inputs) → test_started marker → holdouts once → results logged,
   even if bad.

## Versions

56 (A) + 5 (B) + 20 (C) = **81**.

## Amendments

- **Amendment 0, 2026-10-23 ~22:45Z (Opus), before any code or result: exact readings for both implementations.**
  These are clarifications only.

  1. **Games and history**
     - Games are rows of games.csv with total_line present and game_type in REG/WC/DIV/CON/SB.
     - Kickoff = gameday + gametime, America/New_York.
     - Team codes map OAK→LV, SD→LAC, STL→LA.
     - "Previous games" of team X before game g = X's games (any game_type) with kickoff < g's kickoff and both scores
       present.
  2. **A-PTS**
     - This-season exponentially weighted (EW) mean over X's previous games in g's season, with weights 0.5^(j/h),
       j = 0 for the most recent game.
       - O_cur = EW mean of points scored; D_cur = EW mean of points allowed; n = the number of those games.
     - O_end(S−1) / D_end(S−1) = the same EW over all of X's games in season S−1, with j counted back from X's last
       game of S−1.
     - Prior = end + (Lprev − end)/3, where Lprev = the league mean points per team-game of season S−1. With no S−1
       games, prior = Lprev.
     - O = n/(n+k)·O_cur + k/(n+k)·O_prior, and likewise for D.
     - L = the league mean points per team-game over all games with kickoff in [kickoff − 365 days, kickoff). No
       prediction if that window has fewer than 50 games.
     - Prediction = O_home + D_away + O_away + D_home − 2L.
  3. **A-EPA**
     - Plays: play_type ∈ {pass, run}, qb_kneel ≠ 1, qb_spike ≠ 1, epa not null, posteam not null.
     - The 39 pbp-inconsistent games are excluded from pbp features.
     - Per team-game: off_epa = mean epa where posteam = X; def_epa = mean epa where defteam = X; plays = the count
       where posteam = X.
     - EW, prior and blend as in A-PTS, with k = 4. The prior is pulled toward that season's league mean of the same
       statistic.
     - The OLS is fitted per h on D (2006-14) team-games of games with total_line.
  4. **A-DRIVE**
     - A drive is a (game_id, fixed_drive) with posteam not null.
     - Drive points = posteam_score_post on the drive's last play − posteam_score on its first play, floored at 0.
     - Per team-game: offensive drives and points, defensive drives faced and points allowed.
     - PPD_off = Σw·points / Σw·drives; DPG = Σw·drives / Σw. The same EW, prior and blend, with h = 8 and k = 4.
     - Team points = mean(DPG_home, DPG_away) × (PPD_off_team + PPD_def_opp)/2.
  5. **Part C fair model**
     - f_b = a Gaussian kernel density estimate (bandwidth 2 points) of r = total − close over games 1999-2024 in
       close band b.
     - P(T = t | M) ∝ f_b(t − M) × w_t for t = 0..120, normalised. The band is taken from M.
     - w_t = the empirical frequency of total t over 1999-2024 / the kernel-smoothed frequency of totals (bandwidth 2),
       clipped to [0.4, 2.0].
     - The σ_b / μ wording above is superseded: the kernel estimate carries the residual's own location and shape.
     - Implied median M = the linear interpolation of the YES mid across strikes at 0.50.
     - Quotes: T-60 = the last 1-minute candle ending ≤ the decision time and ≥ decision − 60 min. T-24h = the last
       hourly candle ending ≤ the decision time and ≥ decision − 3 h. Both need yes_bid and yes_ask (close values) to
       be present.
     - Strike s (floor_strike, strike_type greater) settles YES iff total > s.
  6. **Part B**
     - "Open-air" = roof ∈ {outdoors, open} at a stadium whose roof category in weather/stadiums.csv is "open-air"
       (retractable and dome stadiums are excluded).
     - SNF/MNF = REG games with a Sunday or Monday ET date and gametime ≥ 19:00. TNF = REG, Thursday, week ≥ 2.
     - B1 wind = games.csv `wind` (mph) in [10, 19]. B2/B3 use the Open-Meteo forecast at the kickoff hour (kickoff
       floored to the hour), 10 m wind in mph.
- **Amendment 1, 2026-10-23 ~22:40Z (Opus), before any result: wind boundaries.**
  - Forecast wind is a real number. "10-19 mph" means 10 ≤ wind < 20, and "≥ 20" means wind ≥ 20.
  - For B1, games.csv wind is an integer, so this is the same as 10-19 inclusive.
  - This matches the #67 forward paper-league reading.
- **Amendment 2, 2026-10-23 ~23:00Z (Opus), before any result: forecast archive availability and "issued".**
  - Open-Meteo's `previous_day1` wind series only starts 2024-01-19. So B2/B3 archive values exist for the 2024-25
    and 2026 seasons only (forecast_ok for 448 open-air games). The "2021-25 second look" becomes a 2024-25 second look.
  - Those seasons were already examined descriptively by the survey (observed wind), so this stays a disclosed second
    look, reported but not used for the verdict.
  - The verdict sets are unchanged: H3 (2026) and HK (Kalshi 2025-26).
  - **"Issued ≥ 24 h before kickoff"** means the model run was initialised ≥ 24 h before the kickoff hour.
    - The weather agent checked this against the Single Runs API: day1 matched runs initialised 25-28 h earlier.
    - Runs become public a few hours after initialisation, so day1 was public about ≥ 20 h before kickoff.
    - Robustness (reported, not used for the verdict): the same B2/B3 rules with `previous_day2` (initialised
      ≥ 48 h before).
    - Forward paper trading (#67) is stricter: the forecast must have been FETCHED ≥ 24 h before kickoff.
  - **Venues:** the three 2026 games that games.csv tags `dome` at open-air venues (Melbourne, Paris, Munich) stay
    excluded under the literal rule (roof must be outdoors/open).
- **Amendment 3, 2026-10-23 ~23:15Z (Opus), at the agreement check, before selection: the A-DRIVE drive definition.**
  - **What the agreement check found:**
    - A-PTS predictions (1,800) and A-EPA features (200 team-games × 3 h) agree to ≤ 5e-11.
    - 9 of the 10 sampled versions agree exactly in D.
    - A-DRIVE|G4.5 disagrees: 538 vs 662 bets.
  - **Cause:** the checker took a drive's last play over ALL plays of the fixed_drive. When the other team's play
    (e.g. the PAT after a defensive TD) ends the group, posteam_score_post is that other team's score.
  - **Fixed reading (main's):**
    - The drive's plays are those whose posteam equals the posteam of its first play (ordered by play_id).
    - Points = last non-null posteam_score_post − first non-null posteam_score among those plays, floored at 0.
    - If either is missing, points = 0 and the drive still counts.
  - Opus saw both implementations' D rows for the 10 sampled versions (the selection window), which is allowed.
  - Nothing else changes.
- **Amendment 4, 2026-10-23 ~23:25Z (Opus), before selection: sign-off of main/README.md readings 1-23 as
  implemented.**
  - **Reading 14 supersedes the sentence "a ladder whose main strike has spread > 10c is skipped".** The main strike is
    the strike whose YES mid is nearest 0.50 AMONG two-sided strikes with spread ≤ 10c (ties go to the lower strike).
    This is #42's machinery, gated identical to grid_bets.main_strike_table.
  - **Reading 9 limitation:** the QB-out flag needs a dated injury row before the game, so it is 0 in 2009 and
    2025-26. The 2025-26 files have no date_modified. This is disclosed. There is no leak; at worst A-RES is weaker
    there.
  - **Reading 6:** A-DRIVE's built-in under-lean (prediction − close about -2.2 on all games, from defensive and
    special-teams points not being drive points) is part of the registered model and is not "fixed".
  - All other readings (1-5, 7-8, 10-13, 15-23) are accepted as written.
- **Amendment 5, 2026-10-23 ~23:50Z (Opus), after selection on D, before ANY holdout outcome.**
  - **Selection** (run once; results_select.json, d_versions.csv, top20_A.csv): chosen **A-RES|g1.5**, D 633 bets,
    57.4%, +0.093 per $1.
    - #2 is A-PTS|h8|k8|G4.5: 342 bets, +0.073.
    - 48 of 56 versions are eligible.
  - **Disclosed flaw:** A-RES's D number is IN-SAMPLE. Its ridge was fitted on all D games, and λ was chosen by
    leave-one-season-out. The selection compared it with families that have no fitted-to-close parameters, so it is
    favoured by fit optimism. The registered verdict still applies to A-RES|g1.5. Added before any holdout:
    1. **Secondary candidate:** the best D version among the families with no fitted residual-vs-close model,
       A-PTS|h8|k8|G4.5. Its holdout is run in the same single run with the same verdict rules and reported with its
       own verdict.
    2. **D-only diagnostic:** out-of-fold A-RES. For each D season, the ridge is refitted on the other 8 D seasons
       with λ re-chosen by leave-one-season-out inside them, and that season is predicted. D results are reported for
       g {0.5, 1, 1.5, 2}, adding 4 versions.
       - If OOF A-RES|g1.5 is ≤ 0 in D, the A-RES pick is reported as "selected on fit optimism" next to its
         holdout.
       - The verdict rules are unchanged.
- **Amendment 6, 2026-10-23T23:04:16Z (Opus; real clock time), after the Opus leak audit (verdict "NOT SAFE: B-1"), before any
  holdout outcome.**
  - **Timestamps:** the "~HH:MMZ" stamps on Amendments 0-5 and the header were estimates, not clock readings.
    - True order from file times (UTC): pre-registration and samples about 22:10-22:13; Amendment 0 before the first
      main file (22:23:55); Amendments 1-4 before selection (22:41:20); Amendment 5 at 22:42:15.
    - Every stamp from here on is `date -u`.
  - **Already seen, extended:**
    - The survey's season.py printed per-season over% and mean(total − line) for 2006-2025.
    - coach.py split coaches over 2002-2025.
    - The quoted literature gives wind-under rates for 1999-2015.
    - So **B1's clean set becomes H3 only.** D and W are reported as second looks.
    - B4 and B5 keep D + H3 (no primetime/Thursday split was examined before 2015). Season-level over rates were
      seen, which is disclosed.
  - **Code fixes, required before AUDIT_OK:**
    1. Kalshi rows are filtered to the KXNFLTOTAL series in every analysis path and in the bracket gate (B-1), with a
       mixed-series unit test.
    2. HK is restricted to nflverse seasons 2025-2026 (M-6), with counts reported.
    3. A gate is added: nflverse close vs the Kalshi implied median at T-60 and T-24h (median, p95, max |diff|; games
       with > 3 points listed) (M-4).
    4. Any game whose KXNFLTOTAL markets are not all settled yes/no is dropped from Kalshi windows.
    5. The kickoff gate uses Kalshi's own time fields.
    6. `--rehearsal-counts` refuses once AUDIT_OK exists.
  - **Independent checks, required before AUDIT_OK (M-1):**
    - check/ reproduces A-RES predictions for the 200 games of sample_games.csv (its own features and D-only ridge)
      and the B2 forecast join for the 100 games of sample_weather_games.csv;
    - after the download, Part C fair values and bet decisions on 200 strike-snapshots.
  - **Reported controls, labels only (the verdict rules are unchanged) (M-2):**
    - A-RES|g1.5 is about 99% OVER; the secondary is 57-76% OVER.
    - For both Part A candidates and every Part B rule, the report adds:
      - the side mix;
      - always-OVER and always-UNDER on all games of the same windows;
      - a side-preserving luck test: 500 copies with the same number of OVER and UNDER bets per season on random
        games of that season, seed 20261024.
    - If the side-preserving luck share is ≥ 10%, the result is labelled "not separable from era over/under bias",
      whatever its tier.
  - **Disclosed, not changed:**
    - the A-RES HK leg uses the sportsbook close, which is set at kickoff, up to 60 min after T-60;
    - L includes games still in progress at the decision (about ±0.1-0.2 points);
    - the coach-change flag uses game g's listed head coach (interim coaches are announced before kickoff);
    - Amendment 3's drive reading was chosen after seeing the D rows of A-DRIVE|G4.5 under both readings, and gap_drive
      feeds A-RES;
    - two Part A candidates roughly double the false-PROMISING chance;
    - the rehearsal ran the real holdout paths in memory (counts only, no means) instead of on D;
    - main_out/fair_model.npz contains the 1999-2024 residuals, including holdout seasons, and must not be opened;
    - the main README's note that pass_oe is absent before 2009 is wrong (the data was used correctly).
  - **To check after the download, and to amend before the run if needed:** false implied-median crossings from wide
    quotes. If more than 1% of HK games have |close − M| > 3 at T-60, M is recomputed from strikes with spread ≤ 10c
    only.
