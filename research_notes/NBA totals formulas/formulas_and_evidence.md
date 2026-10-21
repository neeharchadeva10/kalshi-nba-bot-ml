# NBA game-total projection: formulas and evidence (researched 2026-10-21)

Legend: [V] = verified in a fetched page this session; [S] = seen only in a search snippet; [K] = standard/derived or from my own knowledge, NOT verified by a fetched source; [X] = marketing/tout/unsourced.
Several fetches failed (kenpom.com 403, masseyratings.com 403, Dartmouth cert error), so KenPom and Massey details come from search snippets only.

## 1. Formulas

Notation: Pace = possessions per 48 min; ORtg/DRtg = points scored/allowed per 100 possessions; Lg = league average.

### Building blocks
- Pace (team-game): 48 * (TmPoss + OppPoss) / (2 * (TmMP/5)) [V] https://faq.stathead.com/knowledge/basketball-advanced-stats . For a 48-min game this is (TmPoss+OppPoss)/2.
- Possessions, KenPom (college): Poss = FGA - OREB + TOV + 0.475*FTA [S] https://kenpom.com/blog/ratings-glossary/ (0.475 is KenPom's college constant; NBA uses 0.44 or 0.4 below [K]).
- Possessions, Basketball-Reference/Oliver: Poss = 0.5*[(FGA + 0.4*FTA - 1.07*(OREB/(OREB+OppDREB))*(FGA-FGM) + TOV) + (same for opponent, roles swapped)] [K]. Simple NBA version: Poss = FGA + 0.44*FTA - OREB + TOV [K].
- ORtg = 100*Pts/Poss; DRtg = 100*OppPts/Poss [V, BR definitions, Dean Oliver]. Four factors: eFG% = (FGM+0.5*3PM)/FGA; TOV% = TOV/(FGA+0.44*FTA+TOV); ORB% = OREB/(OREB+OppDREB); FT rate = FTM/FGA (or FTA/FGA) [K].

### A. Multiplicative (KenPom / log5 style)
- Game pace: Pace_g = Pace_A * Pace_B / Pace_Lg [K; KenPom tempo described as product of each team's % of national avg tempo, scaled by the average: S https://www.rockytoptalk.com/2008/2/27/165520/578]
- Points per 100 for A: ORtg_A * DRtg_B / ORtg_Lg [K]. KenPom form: game-adjusted offense = raw_off * national_avg_eff / opp_adj_def [S https://kenpom.com/blog/ratings-explanation/].
- Total = Pace_g * [ORtg_A*DRtg_B + ORtg_B*DRtg_A] / (ORtg_Lg * 100) [K]. (ORtg_Lg = DRtg_Lg in a closed league.)

### B. Additive
- ORtg_A,exp = ORtg_A + DRtg_B - ORtg_Lg [K]; same for B; Total = Pace_g * (sum of the two)/100.
- Multiplicative and additive differ by well under 1 point except for extreme matchups [K, my arithmetic, not checked numerically].

### C. "Instagram" simple average
- ExpPts_A = Pace_g * avg(ORtg_A, DRtg_B)/100 [K; no citable source found, folk method, treat as [X]]. It equals B only if the league average equals the midpoint of the two terms; otherwise it is biased toward the middle of the pair.

### D. Points-per-game (no possessions)
- ExpPts_A = (PPG_A + OppPPG_B)/2, Total = sum for both teams [K]. Better additive form: PPG_A + OppPPG_B - PPG_Lg. No pace correction. No accuracy source found.

### E. Opponent-adjusted least squares (Massey)
- Per team-game equations: P_a = O_a - D_b + (h/2 for home side, -h/2 for road side) + e [S, search summary of https://masseyratings.com/theory/massey.htm]. D positive = good defense lowers opponent score. Constraints: mean D = 0, and O_i + D_i = R_i (overall margin rating); the off/def split is post-processing of the margin rating.
- Predicted total = (O_a - D_b) + (O_b - D_a) [derived, K]; the home term h cancels, so a pure Massey total has no HCA term.
- Ridge: add lambda*||ratings||^2 to the LS problem [K]; no NBA lambda found anywhere.
- Iterative SRS-style: BBR schedule-adjusted ratings in pts/100 poss: Off, Def, Ovr = Off + Def; LgRtg = 108.06 (full season), HCA = 3.42 (3.47 post-deadline) [V https://www.basketball-reference.com/blog/index5ad6.html?p=9217, a 2012-era post per its comments]. Neutral ratings: ORtg = off_BBR + LgRtg; DRtg = def_BBR + LgRtg [V].
- Pythagorean win% = ORtg^14/(ORtg^14 + DRtg^14) [V same page]. Log5 with HCA: p_home = H(1-R)*0.61 / [H(1-R)*0.61 + (1-H)*R*0.39] [V same page]. (Win-prob tools, not totals.)

### F. Recency weighting
- No NBA-totals half-life found in any source. Related, none for totals: exponential smoothing alpha = 0.10 (weight 0.9 per step) in an NFL example [S]; Fangraphs: prior-year weight about 62% of current year maximizes correlation (MLB) [S https://tht.fangraphs.com/the-math-of-weighting-past-results/]; Harvard Sports Analysis: season-time-dependent Elo K-factor decay improved log loss on about 11,578 NBA games, value not in snippet [S https://harvardsportsanalysis.org/2019/01/a-simple-improvement-to-fivethirtyeights-nba-elo-model/]. The half-life has to be fitted walk-forward by us. Anyone quoting one without data is [X].

### G. Home court and rest
- HCA on margin: 3.5 pts raw, about 3.25 "true" (rest-adjusted) [V https://www.inpredictable.com/2012/02/nba-home-court-advantage-and-rest.html]; BBR 3.42 [V]. For a TOTAL, HCA cancels to first order; no source reports an HCA effect on totals.
- Rest: a 0-days-rest team is penalized 1.25 pts in the SPREAD (corrected from 0.75); no extra effect for 2-in-3 or 3-in-4; HCA by rest: home0 vs road0 3.25, home0 vs road rested 2.00, home rested vs road0 4.50 [V same page, 2003-10 data]. It does NOT address totals.
- Back-to-back effect on totals: only betting-guide claims (-1 to -2 pts first night, -3 to -5 compounding) [X] https://www.gambling.com/us/sportsbooks/strategy/nba-basketball-over-under-betting-key-factors-to-consider-1536900 (unsourced). arXiv 2606.27957 reports B2B win rate 44% vs 50% expected [S]. No peer-reviewed total-points number found.

### H. Four-factor projection
- Adjust each of eFG%, TOV%, ORB%, FT rate for opponent (A/B style), then convert to points per possession [K]. No published total-projection equation or accuracy found. A practical route from box scores: regress team points per possession on own four factors and opponent's four factors. Untested.

### I. Shrinkage / regression to mean
- None reported for NBA totals in any source found. The early-season bias evidence (below) says a prior matters in the first ~20 games.

## 2. Accuracy of totals (weak evidence; no clean peer-reviewed MAE found)
- The premise "closing-total MAE about 14-15" is NOT confirmed by any source. Only data point: total RMSE 20.72 for posted lines 2022-23 to 2025-26 (n = 4,374 games) [V, secondary and Claude-generated GitHub issue; lines "not certified closes"; earlier seasons excluded for broken data] https://github.com/isaacrome21-sys/SportsEdge/issues/2395 . Same issue: simple ratings model total RMSE 21.46 vs line 20.72; over/under record when model differs from line by 2+ pts: 1407-1517 (48.1%); slope of (result - line) on (model - line) = 0.02, so the model adds no information beyond the line. No MAE reported.
- If errors are near-normal, MAE is about 0.8 * RMSE, so about 16.5 [K arithmetic]. Compute it ourselves from data.
- The "13.4" SD of total-minus-line is NFL, NOT NBA [V https://rotogrinders.com/lessons/introduction-808877]. Do not use.
- Mean actual total 192.72 vs mean line 192.27 over 10,325 games, 2000-01 to 2007-08 [V thesportjournal link below].
- Dartmouth CS174 2013 project (Shu) predicted combined score by regression and reported RMSE vs bookmaker; numbers not retrieved [S]. arXiv 1408.2039 says lines are very accurate because oddsmakers use information such as injury reports [S].
- Neil Paine: scoring fell from 115.2 ppg through Feb 2024 to 111.9 after Mar 1; totals too low early, matched Jan-Feb, too high late; extremes: 211.5 line vs 152 actual, 240.5 vs 302, 238.5 vs 351 [V https://neilpaine.substack.com/p/where-sixers-knicks-ranks-among-recent]. Shows within-season level drift.
- Our box-score formulas (A-E) have no published totals MAE. Expect them not to beat the line.

## 3. Market efficiency evidence
- Baryla, Borghesi, Dare, Dennis (2007), "Learning, Price Formation and the Early Season Bias in the NBA," Finance Research Letters 4(3):155-164: totals lines significantly biased early each season, sides not; lines move the right way open to close but the bias is not removed; strategy won 56.72% vs closing totals [V abstract https://dc.etsu.edu/etsu-works/17780]. Break-even at -110 is 52.38%. Direction of bias not given in the abstract I saw. Sample predates 2007.
- The Sport Journal, "NBA Gambling Inefficiencies: A Second Look" (2000-01 to 2007-08, 10,325 games): overs 5,059 vs unders 5,086 (49.87% over, LLR 0.07), a fair bet; at totals 211-220 overs beat the 52.38% breakeven in 8 of 10 line values (212: 53.41%, 215: 54.39%) but none significant (max LLR 2.63 < 3.841) [V https://thesportjournal.org/article/nba-gambling-inefficiencies-a-second-look/].
- Paul, Weinbach, Wilson (2004), "Efficient markets, fair bets, and profitability in NBA totals 1995-96 to 2001-02," Q. Rev. Econ. Finance 44(4):624-632: existence confirmed only, no abstract retrieved [V https://ideas.repec.org/a/eee/quaeco/v44y2004i4p624-632.html]. Do not cite findings.
- A "Week 1, 58.2% unders" Journal of Prediction Markets result is NFL. Ignore for NBA.
- TeamRankings snippet: since 2021-22, 57 games where the close moved 5+ pts below the open, over went 33-24 (57.9%) [S, small n, one page].
- Injury timing: "top-15 player out moves the total down 3-5 pts within 20 min" and "spread moves 2.1 / 5.4 pts" are betting-industry claims with no data [X]. No academic study of NBA totals reaction to injury news found. arXiv 2606.07811 (real-time prediction markets and public information) surfaced but was not read.
- Overall: the peer-reviewed literature is old (data through about 2008), finds NBA totals roughly fair except early-season bias; nothing modern is verified. Modern evidence (one AI-assisted GitHub issue) says a ratings model carries no residual information vs the line.

## 4. What can be computed from free team box scores alone
Computable: possessions, pace, ORtg/DRtg, four factors, PPG/OppPPG, formulas A-D, Massey/ridge offense-defense ratings (E), exponential or last-N weighting (F, parameter fitted), rest days and B2B flags from dates, home/away, league-average priors, early-season shrinkage toward prior season.
Not computable from team box scores: player-level injury impact (needs injury report plus player minutes/usage), travel/altitude, referee effects, and the true closing line (needed as benchmark; Kalshi total-market prices or logged lines must be collected separately).

## Takeaways
1. Formulas are standard and simple. The unknowns (recency half-life, shrinkage lambda, B2B effect on totals) must be fitted walk-forward; no source supplies reliable constants.
2. No verified published NBA closing-total MAE; the 14-15 premise is unconfirmed. Secondary source gives RMSE about 20.7 (line) vs 21.5 (ratings model).
3. Efficiency literature: fair bet overall; early-season totals bias is the one documented anomaly (2007). Kalshi fees must be beaten on top of that.
