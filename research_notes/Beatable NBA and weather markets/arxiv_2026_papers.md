# arXiv 2026 papers on sports prediction markets: Angelini & De Angelis (2606.07811) and Moshrefi (2607.14430)

Source status: both full-text PDFs were downloaded from arxiv.org/pdf and converted with pdftotext. I read all of both papers (31 pages and about 6 pages). Page numbers below are the printed page numbers of paper 1. Paper 2 is an IEEE-style two-column paper without page numbers, so I cite sections. Figures could not be read, only their captions. Quotes are under 15 words. Anything I computed or inferred is marked "INFERRED".

URLs: Paper 1 https://arxiv.org/pdf/2606.07811 ; Paper 2 https://arxiv.org/pdf/2607.14430

## Paper 1 (2606.07811): data, venue, benchmark model

### Takeaway
Kalshi NBA winner contracts, 1-minute bid/ask data. Live midpoints move only about 0.64 per 1.00 of benchmark win-probability change, and the shortfall predicts further drift over 1-15 minutes. The authors themselves find the drift is NOT exploitable once you cross the spread (Appendix B, Table 11).

### Cited Findings
- Venue and sport: "live National Basketball Association (NBA) event contracts traded on Kalshi" (Sec 1, p2). Contract pays $1 if the named team wins the game. Kalshi only, NBA only. (Sec 2, p4)
- Sample (Table 1, p6): 1,438 NBA games; 2,876 team-level contracts (two per game); 409,512 contract-minute observations; sample period 2025-04-15 to 2026-05-25. This spans the 2024-25 playoffs and the 2025-26 season; the paper does not name seasons. (INFERRED from the dates.) Regular season versus playoffs are not split out.
- Pre-game sample: 2,839 contracts from 1,421 games. (Sec 2, p5)
- Regression samples: 356,769 contract-minutes for the benchmark regressions (Table 5, p16); 362,133 for the directional regressions (Table 4, p14).
- Sampling: one-minute observations per contract with best YES bid, best YES ask, trading volume, open interest. Main price is the midpoint (bid+ask)/2. (Sec 2, p4-5)
- Data source: the Kalshi quote data provider is NOT named. Game state comes from "timestamped NBA play-by-play data" (scores, turnovers, fouls, timeouts, rebounds, possessions, clock). Each contract-minute gets the most recent game state. (Sec 2, p4-5)
- Benchmark q (Sec 5.1, p15): a logit of the final payoff on the pre-game closing Kalshi price, score margin, absolute margin, game-clock time remaining, period indicators, home status, recent net scoring, and nonlinear margin x time-remaining interactions. It excludes the contemporaneous Kalshi live price.
- Out-of-sample method: five-fold cross-fitting with folds split by game. Brier score is 0.164 for the benchmark, 0.164 for the Kalshi live midpoint, and 0.211 for the pre-game closing price. (Sec 5.1, p15) So the benchmark is no more accurate than the market; both are at 0.164.
- Pre-game price is well calibrated: calibration slope 0.985 at close, intercept 0.006 (Table 2, p11). Brier falls from 0.204 (24h before) to 0.199 (close).
- Pass-through: coefficient of 1-minute change in Kalshi midpoint on 1-minute change in benchmark prob = 0.630 (no controls) and 0.638 (SE 0.010, with controls); p(H0: =1) < 0.001. (Table 5, p16). Reading given: "10 percentage point increase ... only about a 6.4 percentage point" rise. (p16)
- Drift regressions (Table 6, p17): dependent variable is the future midpoint change; regressor is the gap = benchmark change minus price change in the same minute (Eq 9: Gap = Δq − Δp). Coefficients:
  - Raw future drift, horizon 1/2/5/10/15 min: 0.150 / 0.164 / 0.195 / 0.196 / 0.236, all ***.
  - Net of future benchmark changes: 0.379 / 0.414 / 0.459 / 0.458 / 0.484, all ***.
  - Note: the pdftotext layout scrambled the column order of Table 6; I assigned values by row order and checked them against the text, which says 2.0 pp raw and 4.6 pp net at 5 min.
- Magnitude, quoted in text (p17): "10 percentage point initial updating gap" predicts 2.0 pp raw and 4.6 pp net drift at 5 min; 2.4 pp and 4.8 pp at 15 min.
- Signal-direction mean 1-minute price changes (Table 3, p13): last-minute net points +/-2.8 pp; made 3pt +3.7/-3.8 pp; made 2pt about 2.4 pp; turnover about 2.2 pp; lead change about 4.5 pp; 8-0 run 2.5 pp; 10-0 run 2.8 pp.
- Liquidity measurement: a standardized illiquidity index that "increases with bid-ask spreads and decreases with recent trading volume and open interest" (mean 0, SD 1). Salience index is built from made 3s, lead changes, scoring runs and an event-level salience score. (Sec 6, p19) The paper gives no spread, volume or open-interest dollar thresholds.
- Liquidity interaction (Table 7, p21): outcome is directional underreaction UR = sign(Δq)(Δq − Δp), sample limited to |Δq| >= 0.0025. Coefficients: illiquidity +0.0015; salience −0.0026 (salient events are under-reacted to LESS); salience x illiquidity +0.0014. Lead change x illiquidity +0.0043, 3pt x illiquidity +0.0027, 8-0 run x illiquidity +0.0021 (from the column garbled by pdftotext; the text says lead changes have the largest interaction). Authors: "A lead change ... in a thin market it is not fully incorporated." (Sec 6, p20) These are in units of probability per 1 SD, so they are small (about 0.1-0.4 pp).
- Drift by salience and liquidity (Table 8, p23): updating-gap coefficient (net drift) 0.502 / 0.514 / 0.549 at 5/10/15 min. Gap x Salience is positive and significant (0.023, 0.013, 0.025). Gap x Illiquidity is not positive (-0.022, -0.032, -0.037). Authors: illiquidity "increases the initial failure ... but does not accelerate subsequent correction." (p22) The column order in the extracted text was scrambled, so treat the mapping of the interaction coefficients to rows as likely but not certain.
- Game phase (Appendix C, Table 12, p31): pass-through by phase is Q1 0.641, Q2 0.663, Q3 0.673, Q4 0.620, non-clutch Q4 0.700, clutch 0.509. Clutch is the final 5 min of Q4 with margin <= 5. Clutch has 14,856 contract-minutes in 616 games. Underreaction is present in every phase and strongest in clutch.
- Robustness (Appendices A and B, p27-29): alternative benchmarks (parsimonious, flexible, no-recent-scoring, chronological 70/30 split) give pass-through 0.637-0.698 and 5-min net drift 0.379-0.493 (Table 9, p28). Quote-quality subsamples (positive volume, narrow spread below median, non-stale, high-quality) give pass-through 0.656-0.663 and 5-min net drift about 0.49-0.53 (Table 10, p29).
- Trading evaluation (Appendix B, Table 11, p30): strategy is to buy YES if gap > 0, sell YES if gap < 0, held 5 minutes. Executable returns buy at the ask and exit at the bid. When no quote existed, bid/ask was approximated as midpoint ± half the quoted spread.
  - |Gap| >= 0 bp: midpoint return +0.0039, executable −0.0120 (N 300,920). High-quality sample: +0.0037 / −0.0078.
  - |Gap| >= 200 bp: midpoint +0.0087, executable −0.0092 (N 92,665). High-quality: +0.0082 / −0.0036 (N 60,965).
  - Quote: executable-style returns "remain negative even for large gaps." (App B, p29) Their conclusion is that the drift "should not be interpreted as a frictionless arbitrage opportunity."
- No Kalshi fee schedule is modelled in the paper. Only the bid-ask spread is charged. Fees are mentioned only as a caveat (Sec 1, p2). Midpoint returns of 0.4-0.9 cents per 5-minute trade are the ceiling before any cost.

### Inferences
- INFERRED: the paper supports a taker strategy only if the signal also identifies the spread being very tight. Even in the best bucket (high-quality quotes, |gap| >= 200 bp) the executable return is still negative (−0.36 cents per contract on a 5-min round trip). A testable variant would be maker-side entry (post at the favourable side rather than crossing), which the paper does not test, or a much higher gap threshold; neither is evaluated.
- INFERRED: the 0.64 pass-through coefficient is measured against a benchmark that has the same Brier score as the market. Because of errors-in-variables, part of the shortfall may be noise in q, though the net-drift regressions are the authors' answer to that.
- INFERRED: the gap uses the benchmark change over a single minute, so a live replication needs a game-state win-probability model fit on the pre-game close plus score, time remaining and home status, updated each minute, and a 5-minute exit.

### Gaps
- The Kalshi data provider/API and the season labels are not stated. Regular season versus playoffs is not broken out.
- Per-minute spread levels, typical spread in cents and median volume are not reported in the text I read (Table 1 only has game counts and durations).
- No Kalshi fee schedule applied; no PnL in dollars; no out-of-time strategy test; the trading table uses the in-sample gap thresholds and is not cross-fitted for the strategy itself.
- Standardized-index coefficients cannot be mapped to a spread in cents.

## Paper 2 (2607.14430): data, calibration near expiry, parlays

### Takeaway
Kalshi moneyline trades (NBA, MLB, NHL), early March to mid-May 2026. Calibration is near-perfect from about 30 min to 4 hours before market close, but in the last 10 minutes the curve is step-like: low-priced sides win far less than their price, and high-priced sides win more. Cross-game parlays are overpriced by a small median amount that grows with legs. The paper does no after-fee or PnL analysis.

### Cited Findings
- Data (Sec III-A): trade-level Kalshi data, regular-season moneyline markets for NBA, MLB, NHL; "early March through mid-May 2026"; 11 weeks of NBA, 10 weeks NHL, 7 weeks MLB. About 23 million executed trades after excluding canceled and voided markets: NBA 13,009,643 (56.6% by my arithmetic), MLB 7,148,254, NHL 2,819,289. Each trade has a contract ID, timestamp, executed price in cents, size, and a binary outcome. Settlement outcome is taken from the platform. Prices exclude exchange fees, "charged separately, on top of the trade price." (Sec III-A)
- Source of the trade data is not named. Note this is playoffs for NBA/NHL in late April-May (INFERRED from dates), though the paper says "regular-season."
- Definition of expiry (Sec III-B): time-to-expiry (TTE) is trade time to "the market closing time of the underlying contract," meaning the scheduled end of trading, "which for our sports markets coincides with the end of the game." They explicitly do not use the later administrative settlement timestamp. So "final ten minutes" means the last 10 minutes of trading before market close, which they say is game end. Note that the paper's own wording elsewhere ("tip-off or first pitch") treats TTE as long before the game for large values.
- Buckets in minutes: [0,10), [10,30), [30,90), [90,240), [240,inf). Midpoints 5, 20, 60, 165, 360. (Sec III-B)
- Models (Sec IV-A): power-law C(p) = p^γ / (p^γ + (1−p)^γ) and Platt scaling C(p) = σ(a·logit(p)+b), fit per league per bucket, weighted by trade count within price bins. Prelec-II used for the last two buckets.
- Results (Sec IV-B): in the 0-10 min bucket γ is 1.27-1.31 across the three leagues; Platt slope a is 1.62 (NBA), 2.05 (MLB), 4.56 (NHL; the text also writes 4.57). "contracts trading at prices near 0 resolve favorably less often ... near 1 ... more often." The ordering of distortion size is NHL > MLB > NBA. NBA is the least distorted league.
- Shape (Sec IV-C): 30-90 and 90-240 minute curves are "visually indistinguishable from the diagonal." In the 0-10 minute bucket the NHL empirical outcome rate is "essentially zero across prices below 0.4" (contracts at 10-40 cents "resolve favorably almost never"), transitions around p = 0.5 and saturates near one above. The 10-30 minute bucket shows a mild Prelec shape. That description is for the NHL (Fig 3); NBA specifics are in figures I could not read.
- Direction of Prelec fit (Sec IV-D): curvature parameter above 1 in the last 10 minutes, "the inverse of the canonical probability-weighting result." Interpretation: favourites are underpriced and underdogs overpriced near expiry, in the sense that prices pull toward 0 or 1. Their explanation is insurance demand by holders of losing positions; they admit alternatives (liquidity withdrawal by informed traders, late uninformed entry, herding) cannot be ruled out with their data.
- Meta-fit: quadratic-in-log-TTE fits with R2 above 0.92 for all six trajectories (Sec IV-B).
- Magnitudes by price bucket: the paper does NOT tabulate win rate versus price bucket. It gives only parameters and curve descriptions (plots in Fig 3 for NHL only).
- Parlay data (Sec V-A): Kalshi parlay trades 29 Apr to 13 May 2026 (15 days); base of 153,173 parlay trades; filters: every leg's TTE between 30 and 240 minutes, cross-game only, every leg priced within 300 s; final N = 12,639 parlays with 2 to 11 legs.
- Overpricing ratio R = executed parlay price / product of leg prices (Table I, Sec V-B). Median R by legs: 2: 0.991; 3: 0.995; 4: 0.999; 5: 1.005; 6: 1.013; 7: 1.037; 8: 1.066; 9: 1.105; 10: 1.223; 11: 1.305. Overall mean 1.028, median 0.996. Cell counts: 5,846 (2 legs), 3,111 (3), 1,523 (4), 928 (5), 584 (6), 371 (7), 140 (8), 67 (9), 57 (10), 12 (11). Means are heavy-tailed (e.g. 10 legs mean 3.078).
- The text says median "1.01 at five legs, 1.07 at eight legs, 1.22 at ten legs, and 1.31 at eleven." Regression of log median R on legs: slope about 0.029 (R2 0.94), about 3% per leg multiplicative. (Sec V-B)
- Parlay calibration (Sec V-C, Fig 6): parlay win rate runs 2 to 10 percentage points below the price across prices under 0.6; "A parlay priced at 0.30 wins on average about 24% of the time; one priced at 0.40, about 30%." This is a roughly uniform downward shift, consistent with an additive markup. This is a statement about parlay price versus outcomes, so it is the closest the paper gets to a realized edge, but it applies to buying the parlay's NO side, not to single games.
- After-fee or trading analysis: none. The paper says only that "quantifying the out-of-sample value of such corrections is the natural next step." (Sec VI)

### Inferences
- INFERRED (simple arithmetic on the paper's own power-law formula, assuming the formula as stated): with γ = 1.30, a contract priced 0.90 has calibrated probability about 0.946, and one at 0.70 about 0.75, in the final 10 minutes. That would be roughly 4-5 cents of edge for buying heavy favourites, before fees and before checking whether those trades are fillable. This is my calculation from the stated pooled parameter range, not a number reported by the paper. NHL/MLB would be larger and NBA smaller (its Platt slope is the lowest).
- INFERRED: a plausible selection/mechanical explanation is that a contract at 10-40 cents in the last 10 minutes of an in-game market is usually a team that is already losing, and prices often lag the known game state. The paper offers insurance demand instead. This matters because it affects whether the bias can be traded: a rule that buys the high side at the end would be competing with that same information.
- INFERRED: "expiry" = market close, which the paper says coincides with game end. If Kalshi markets actually remain open into the final minutes of a decided game, then most 0-10 min trades are near-certain outcomes and the 4-5 cents on a 90-cent contract could be thin in dollar terms and hard to fill at size. The paper gives no volume split per bucket.
- INFERRED: the late-window finding is about trades (executed prices) and is therefore a taker/maker-mixed price series; the paper does not say who initiated the trades.

### Gaps
- No per-bucket table of win rate versus price; no NBA-specific price-bucket magnitudes in the text; Fig 2 and Fig 3 content (NBA parameter curves, trade counts per bucket) was not readable.
- No fee-adjusted or PnL analysis; no data source name; no split of regular season versus playoffs; no out-of-sample test of the correction.
- Whether the "tip-off/first pitch" language means TTE up to 360+ minutes or something else is not further defined beyond the bucket list.
