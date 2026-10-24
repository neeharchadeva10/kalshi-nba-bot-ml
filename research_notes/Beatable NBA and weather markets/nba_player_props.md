# NBA Player Prop Inefficiencies (sportsbooks and prediction markets)

Overall caveat: about 12 searches/fetches. I found NO peer-reviewed or rigorous out-of-sample study of NBA player-prop pricing efficiency after costs. Everything prop-specific below is practitioner or anecdotal. Academic evidence found is about NBA game totals/sides and season win totals, and is mostly pre-2018.

## 1. What do academic papers and serious quant analyses find about NBA player-prop efficiency (over/under bias, favorite-longshot bias, stat-type differences)?

### Takeaway
No rigorous academic or quant study of NBA player-prop efficiency turned up. Adjacent academic work (game totals, season win totals, Kalshi moneylines) shows small, mostly older or time-limited biases; the public-overs bias is documented for game totals but fading it has not been reliably profitable.

### Cited Findings
- Search tools found no peer-reviewed paper on NBA player-prop over/under bias, favorite-longshot bias, or stat-type differences; the closest results are game and season totals (flagged: older, not props) — [Learning, Price Formation and Early Season Bias in the NBA](https://dc.etsu.edu/etsu-works/17780)
- Baryla et al. (2007, Finance Research Letters, OLD): NBA totals lines biased early each season, sides lines not; a simple strategy against the closing totals line won 56.72%; bias not fully removed by closing lines. Game totals, not props — [UWF repository](https://ircommons.uwf.edu/esploro/outputs/journalArticle/Early-season-NBA-overunder-bias/99380090347306600)
- Woodland and Woodland (2015): NBA season win totals over/under slightly more efficient than NFL but profitable strategies still exist, partly from overvaluing prior-season performance. Not props, pre-2018 — [IDEAS](https://ideas.repec.org/a/jsf/intjsf/v10y2015i2p160-174.html)
- A 2004-2007 study of four sportsbooks (OLD, game totals): overs receive a much higher share of bets than unders and the share rises with the total; contrarian betting did not win more often than implied by efficiency — [Paul and Weinbach 2008](https://kylewoodward.com/blog-data/pdfs/references/paul+weinbach-international-journal-of-sport-finance-2008A.pdf)
- Action Network (media, selective samples): fading the public in NBA Finals totals went 18-20-1 since 2011; league-wide overs 382-390-14 (49.5%) in one 2018-19 stretch. Small samples, sportsbook-affiliated — [Action Network](https://www.actionnetwork.com/nba/nba-finals-fade-the-public-betting-tip-warriors-raptors); [Action Network 2019](https://www.actionnetwork.com/nba/nba-betting-overs-totals-bill-simmons-2019)
- Break-even at standard -110 is 52.38% — same sources as above.
- Practitioner guides (not rigorous) claim points props are the most efficiently priced and rebounds among the most predictable because they track opportunity — [arXiv search result listing pointing to leans.ai guide](https://leans.ai/nba-player-prop-strategy/). Treat as unverified marketing.
- Favorite-longshot theory (Whelan 2023, Economica): bias in fixed-odds markets can arise from bettor disagreement plus bookmaker risk aversion; not NBA-prop specific — [Whelan](https://www.karlwhelan.com/Papers/EconomicaFinal.pdf)
- 2026 arXiv preprint (Moshrefi), Kalshi, 23 million moneyline trades across major sports: calibration near-perfect mid-life but departs sharply in the final ten minutes before expiry (step-like curve); cross-game parlays priced above the product of leg prices, overpricing growing with leg count. Abstract does not mention player props; league-level and cost details not read — [arXiv abstract](https://arxiv.org/abs/2607.14430)
- Same preprint's summary notes reverse favorite-longshot bias documented in MLB/NHL moneylines and some US point-spread markets (second-hand via search snippet) — [arXiv PDF](https://arxiv.org/pdf/2607.14430)

### Inferences
- The "overs bias from public betting" has documented basis for game totals only; there is no evidence found that it carries to player props or survives vig.
- The Kalshi preprint suggests (for moneylines) mid-life prices are fairly calibrated and any tail miscalibration is near expiry; unknown whether prop ladders behave the same.

### Gaps
- No study on prop over/under bias, alt-ladder tails, stat-type or star-vs-role-player differences. Search tools did not index SSRN/JQAS directly; worth a manual SSRN/Google Scholar check.
- Did not read the full Moshrefi paper (PDF was binary); saved copy at %USERPROFILE%\.claude\projects\C--Users-Jarvis-Documents-Kalshi-PM\202e17c4-729e-4c0b-a7d3-ce149a4ff08b\tool-results\webfetch-1792560170531-q3zfrs.pdf for local extraction.

## 2. Which specific testable patterns are reported (minutes after teammate injuries, blowouts, back-to-backs, news staleness, alt ladders), with magnitudes and recent persistence?

### Takeaway
The only pattern repeatedly named is stale prop prices right after injury/lineup news (books move spreads first, props later), plus minutes/usage projection differences. All evidence is anecdotal with no sample sizes or after-cost ROI.

### Cited Findings
- A bettor claims 30% ROI on 2020 NBA betting "mostly rooted in prop betting"; no bet count or stakes given, unverifiable — [amicsta Substack](https://amicsta.substack.com/p/how-to-beat-nba-props)
- Same source: books adjust game spreads first and props more slowly around injury report times (1:30, 5:30, 8:30 ET) and tip-off; largest projection differences between sources are in minutes — [amicsta Substack](https://amicsta.substack.com/p/how-to-beat-nba-props)
- Usage/rebound rates shift when teammates are out (Harden/Westbrook example); on/off data used to adjust. Method description, not a test — [Stokastic](https://www.stokastic.com/articles/nba-dfs/nba-dfs-research)
- RotoGrinders: if news breaks your way you are likely getting 1-2 points of closing line value (anecdotal, 2022) — [RotoGrinders](https://rotogrinders.com/articles/nba-situational-player-projections-dfs-betting-props-analysis-3-8-22-3767948)
- Denver Gazette: stat projections can lag new minute loads in the playoffs (anecdotal) — [Denver Gazette](https://www.denvergazette.com/?p=507627)
- Caution: initial injury reports may overstate severity (generic advice) — same search listing, unverified.
- A duckdns.org "Panel Ruling" claims ~60% of a missing player's usage is redistributed; authorship and backtest unknown, do NOT rely on it.

### Inferences
- Stale-line-on-news is the most plausible edge but it is a speed game against sportsbook and professional market makers; a small trader using official injury reports is likely late.

### Gaps
- No evidence found on blowout/garbage-time, back-to-back, pace/matchup, or alt-ladder tail mispricing. No magnitudes with sample sizes and costs.

## 3. How do sportsbooks/market makers set prop lines, and where do professionals say softness is?

### Takeaway
Only informal claims found: props are softer than sides/totals because of low limits and the difficulty of monitoring every line; no primary source from Pinnacle/Unabated/projection providers surfaced.

### Cited Findings
- Blog claim: props less efficient than game lines because books impose low limits and cannot monitor every prop; unsourced — [sportsbook-practices blog via search](https://stealinglines.substack.com/p/betting-101)
- Model-vendor claim that prop markets are thinner and slower to correct is marketing with no methodology — [ascendexponentially](https://ascendexponentially-com.lovable.app/insights/nba-player-props-ai-predictions)
- Bettor advice: shop across books (DraftKings, FanDuel, MGM, etc.) because small line differences add up, and winners get limited — [amicsta Substack](https://amicsta.substack.com/p/how-to-beat-nba-props)

### Inferences
- The softness sharps cite exists at sportsbooks (shoppable, limited-stake); none of it is evidence about Kalshi prices, where a taker has no line-shopping edge unless comparing to books.

### Gaps
- Nothing found on how books/projection vendors set lines or shade; Pinnacle/Unabated resources not surfaced.

## 4. Prediction-market (Kalshi/Polymarket) player props: liquidity, spreads, makers, efficiency vs sportsbooks

### Takeaway
Player props are the thinnest segment on Polymarket (about $253 mean volume per market); Kalshi prop depth data is absent, but makers there are professional firms and fees are non-trivial. No efficiency comparison to sportsbooks found.

### Cited Findings
- Polymarket NBA study (arXiv 2605.00864, 75M+ order-book snapshots, 173 games): 5,074 player-prop markets totaling 1,285,141 USDC, mean 253 USDC and 13.3 trades per market, versus 1.43M USDC mean for full-game moneylines — [arXiv](https://arxiv.org/html/2605.00864v1)
- Same: median bid-ask 392 bps pre-game, 1,031 bps in-game; zero fees on NBA markets; single-market arbitrage extremely rare (7 episodes, median 3.6 s); props not analyzed for arbitrage, no sportsbook comparison — [arXiv](https://arxiv.org/html/2605.00864v1)
- Kalshi: the maker side of trades is almost always a professional/institutional market maker; Susquehanna reportedly large; takers' NBA stake was $18.5M with many millions more across props, spreads, totals — [InGame](https://ingame.com/susquehanna-biggest-sports-kalshi-volume-knicks)
- Kalshi liquidity incentives program filed with CFTC (payments for tight quotes); author is an ex-Kalshi-MM employee, commentary — [Substack](https://fiftycentdollars.substack.com/p/kalshis-new-liquidity-incentives)
- Kalshi football prop-style contracts listed around August 21, 2025 (reader comment, unconfirmed); author opines props/SGPs carry higher effective vig at sportsbooks and exchange pricing compresses it; fee estimate ~1% of volume is the author's assumption — [Substack](https://ufoholdings.substack.com/p/kalshi-swipes-at-sportsbooks-profit)
- Third-party fee blogs give taker fee 0.07 x C x P x (1-P) rounded up; one says maker coefficient 0.0175, another says fee-free resting orders on most sports; conflicting, third-party. Your memory notes say makers are free on BTC series; verify sports against Kalshi's official PDF — [sailgp.com](https://sailgp.com/prediction-markets/kalshi/fees)
- A tracker showed ~40 live Kalshi NBA points contracts, one May 2025 snapshot, no spreads — [simplefunctions.dev](https://simplefunctions.dev/markets/series/KXNBAPTS)

### Inferences
- Thin prop books plus pro makers imply wide spreads and little size for a small trader; ladder edges would need to exceed spread plus the taker fee (peaks near 50c prices).

### Gaps
- No Kalshi prop spread/depth statistics, no Kalshi-vs-sportsbook prop efficiency study, no Polymarket prop calibration.

## 5. Free data and quality of free minutes/usage projections

### Takeaway
No evaluation of free-projection accuracy found. Practitioners treat minutes as the key input and use on/off (WOWY) splits.

### Cited Findings
- Minutes is the base input; usage and rebound rates adjusted via on/off data — [Stokastic](https://www.stokastic.com/articles/nba-dfs/nba-dfs-research)
- Rotowire lists teammate injuries as a minutes/projection input, with no accuracy test — [Rotowire](https://www.rotowire.com/basketball/article/nba-projected-minutes-explained-fantasy-basketball-97473)
- Tools named: Fantasy Labs on/off and Add More Funds WOWY (paid/third-party) — [amicsta Substack](https://amicsta.substack.com/p/how-to-beat-nba-props)

### Inferences
- A free pipeline (official injury report + nba_api/hoopR box scores for rolling minutes and teammate-out splits) is feasible, but its accuracy versus market prices is untested here; any model should first be calibration-tested against Kalshi prices.

### Gaps
- No data on hoopR/nba_api coverage, official injury report timing reliability, or free-projection accuracy.
