# Pre-registration — research log #42: exhaustive NBA totals formula grid, every one backtested after fees and slippage

Written 2026-10-21 ~20:20Z by Opus. The user's words:
- "exhaust all possible formulas and make sure the numbers are right, every combination, every logical formula";
- "you can base it off my formula or create your own";
- "backtest every one of these".

User choices:
- **Full grid.**
- **Use the untouched second half for one test of the winner.**
- **Pick the winner in a way that includes fees and slippage.** The user asked for the best method, and it is
  defined below.

Changes go in "Amendments" with a date.

## Known before writing (discovery outcomes have been seen)

- **#37, #38 and #39** (31 formula versions) all lost to Kalshi's T-1h line on discovery.
  - Best MAE 15.30 vs the market's 14.67.
  - Every residual coefficient was negative.
  - Formula side picks won 48.6-51.1% vs the median line, and every bet rule lost after fees (exploratory).
- **The unseen half** (661 games from 2026-01-23 02:00Z) has **never been loaded** by any totals code.
- **Literature:** a ratings-only model adds nothing beyond the line (slope 0.02, 48.1% when it disagreed by 2+).

## The bet each strategy makes (the user's live rule, extended with a confidence threshold)

- **Per game, at T-1h:** take the KXNBATOTAL strike whose mid is nearest 0.50 (mid 15-85c, ties to the lower
  strike).
  - Side = OVER if proj > strike, UNDER if proj < strike.
  - Bet only if |proj - strike| >= Tpts.
- **Buy 1 contract as a taker:**
  - price = the chosen side's ask (YES ask, or NO ask = 1 - YES bid), **plus 1c slippage**;
  - plus Kalshi's taker fee at count 1, 0.07 x P x (1 - P) at the ask, rounded up to $0.000001.
  - Hold to settlement; Kalshi's yes/no decides.
  - **Net per contract = 1(win) - ask - 0.01 - fee.**
- **Tpts:** {0, 2, 4, 6, 8} points. This is the user's "bet when more confident" idea, as a filter.

## The formula grid (every combination; each formula sees only box scores dated before the game's ET date D)

| Dimension | Options | Count |
|---|---|---|
| A. Combine (team A points per possession) | w x O_A + (1-w) x D_B for w in {1.0, 0.75, 0.5 (the user's), 0.25, 0.0}; multiplicative O_A x D_B / L_o; additive O_A + D_B - L_o | 7 |
| B. Pace | own (P_A for A's points); average (P_A + P_B)/2; multiplicative P_A x P_B / L_p; term-own (each rating term times its own team's pace: the points-per-game forms) | 4 |
| C. Ratings | season (#38 R1); exponential decay with half-life 5, 10, 20 games; last 5, 10, 15 games; KenPom opponent-adjusted (#39 R3); Massey ridge; four-factor | 10 |
| D. Prior weight k (last season counts as k games) | 0, 10, 20, 40 | 4 |
| E. League shift | off; on, n_lg / (n_lg + 100) | 2 |
| F. Rest | none; back-to-back (c fitted with an intercept, slope only); rest-days categories 0 / 1 / 2 / 3+ | 3 |
| G. Home/away split | off; on (each team's home-only or away-only ratings, blended with its overall ratings at n_split / (n_split + 10)) | 2 |
| H. Shrink toward league level | off; on: proj' = Lt + b x (proj - Lt), where Lt = 2 x L_o x L_p, the league total at D | 2 |

- **Totals:** 7 x 4 x 10 x 4 x 2 x 3 x 2 x 2 = **26,880 formulas**. With 5 thresholds that is **134,400 strategies
  (versions)**.
- **Massey ridge:** for each D, fit the per-game points per possession = mu + o_team - d_opp by ridge regression
  (lambda = 10) on the current-season games before D. O = mu + o, D = mu + d (the opponent-allowed scale).
  - The prior is the same fit on the full previous regular season.
- **Four-factor:**
  - O_A = the 2024-25 OLS fit of team-game ORtg on (eFG%, TOV%, ORB%, FT/FGA), applied to team A's own
    rate-weighted factors before D.
  - D_A = the same fit applied to the factors A allowed.
  - The fit uses 2024-25 regular-season team-games only. The prior is the same on the previous season.
- **Constants:** the rest effects (c or the rest-day categories) and the shrink slope b are fitted per (A, C)
  pair, on **2024-25 regular-season** walk-forward projections (prior 2023-24), with k = 20, shift on, pace = the
  average, no split. No 2025-26 data is used to fit them. Every 2025-26 rule from #38 and #39 still applies:
  - the turnover fix;
  - D = ET date of min(tip, T - 1 h);
  - hoopR-only actuals;
  - gate-stage box scores before 2026-01-23 ET.

## Choosing the winner on discovery (the method for "fees and slippage")

1. **Every strategy is backtested on discovery.** Report:
   - n bets, win rate, average price paid (with slippage), mean net per contract and total $;
   - the **lower bound** LB = mean - 1.96 x sd / sqrt(n). One bet per game, so the bets are independent across
     games.
2. **Eligible:** n >= 150 discovery bets.
3. **Winner = the highest LB.**
   - This uses profit after fees and slippage, but favours steady profit over a lucky few big wins.
   - Ties go to the fewest non-default parts.
4. **Luck check (reported):** the same selection over 2,000 strategies that pick a random side per game, using the
   same games, strike, threshold structure and costs. It shows how high the best LB gets from luck alone.
5. **Also reported for the winner** (information only):
   - MAE, Brier and the residual test vs the market, as in #37;
   - the full leaderboard, the top 50 by LB, and a summary by each dimension.

**Departure from the model-test gates, stated openly:** #37 to #39 required accuracy gates before any betting test.
Here the user chose a profit-based selection, so the accuracy gates are reported, not enforced. The unseen pass bar
below is the decider.

## One test on the unseen half (the winner only)

- Run the winner's exact strategy once on all 661 unseen games, with full-season walk-forward ratings and the same
  costs.
- **Pass bar (all must hold):**
  1. Mean net per contract > 0.
  2. 95% bootstrap lower bound > 0. Resample games, 10,000 draws, seed 20261021.
  3. At least 100 unseen bets.
  4. **Coin flip:** at most 5% of 500 copies do as well. Each copy uses the same bet games and strike, with a
     random side and the same costs. Seed 20261021.
  5. At least $2 per week at 10 contracts.
- **If it fails, it's reported as a failure.** No second winner is tried on the unseen half.

## Correctness checks (before any discovery number is computed)

- Unit tests with hand-computed answers for every option of every dimension.
- Walk-forward asserts (no same-date or later box score) and finite projections for all 26,880 formulas.
- **An independent Opus leak-audit:** it recomputes a sample of formulas from scratch and checks costs and the bet
  rule.
- **A cross-check:** the user's base formula reproduces #38's projections. The F1/F2/F3/F4 analogues reproduce
  #39's projections exactly where the settings coincide.

## Versions

**134,400** (26,880 formulas x 5 thresholds), plus the 1 unseen test.

## Amendments

- **2026-10-21 ~20:00Z (Opus), after the Opus leak-audit.** Written before any discovery outcome metric was
  computed.
  1. **Massey:** one unweighted row per team-game. lambda = 10 means 10 games of shrinkage, the literal reading of
     "per-game points per possession ... lambda = 10".
     - o_t = sum(ppp - mu - d_opp) / (n + 10). The same form is used for d_t.
     - This replaces the builder's possession-weighted version, where the penalty was about 0.1 game.
  2. **Rest and shrink order:** proj = Lt + b x (base - Lt) + rest adjustment. Shrink applies to the unadjusted
     projection, and the rest effects (fitted as additive shifts) are added after it, unscaled.
  3. **Main strike:** the strike whose mid is nearest 0.50 among strikes with mid 15-85c **and spread
     (ask - bid) <= 0.10**.
     - This excludes empty books, such as 0.01/0.99. On discovery, 5 of 660 picks were empty books.
     - The same filter is added to #40 and to the live pick list.
  4. **Recorded builder choices:**
     - **Home/away split:** the venue rating is blended with the prior at k first, then with the overall rating at
       n_eff / (n_eff + 10). KenPom and Massey use one restricted update against the full-league fit. Decay and
       last-N count only that venue's games.
     - **Rest:** 3+ days is the reference category (season openers are 3+). Rest and shrink intercepts are fitted
       but not applied.
     - **Pace:** term-own pace for mult = (O_A P_A)(D_B P_B) / (L_o L_p); for add = O_A P_A + D_B P_B - L_o L_p.
     - **Bets:** proj == strike means no bet (impossible with X.5 strikes). The fee is charged at the ask. $/week is
       net x 10 over the unseen T span.
     - **Four-factor:** the 2024-25 coefficients are used in-sample when fitting the 2024-25 constants.
     - **Residual test:** #37's loop (information only).
     - **Tie order:** LB within 1e-9, then fewest non-default parts, then smaller T, then lower formula index.
  5. **Duplicates:** 1,920 formulas are exact duplicates (e.g. mult with term-own pace equals mult/mult), leaving
     24,960 distinct formulas. The tie order handles them. The version count stays 134,400 as registered.
  6. **Luck checks (information only):**
     - the registered 2,000 random-side strategies;
     - one random-side copy per real strategy (134,400);
     - **a shared-flip null:** one random side per game shared across all strategies, take the maximum LB over all
       strategies, repeated 200 times (seed 20261021).
  7. **Wording:** unseen outcomes are never **joined to or used** by `discovery`. The inherited #37 loaders read the
     whole season into memory, and 3 unseen games tipping late on 2026-01-22 ET sit in the discovery box scores but
     are never used, because only box scores dated before D count.
  8. **Freeze:** the SHA-256 of winner_frozen.json is written to the research log right after discovery.
  9. **Fills:** a fill at the T-1h best ask with no depth data is assumed (the median main-strike spread is 1c).
