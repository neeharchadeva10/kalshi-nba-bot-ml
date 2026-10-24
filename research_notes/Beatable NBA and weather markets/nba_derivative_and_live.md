# NBA derivative and in-game markets: evidence of mispricing

Overall: rigorous, after-cost evidence of exploitable mispricing in NBA derivative or live markets is thin. Most hits were old, pre-2018, or practitioner/unverified. Only ~11 searches/fetches were run; two key arXiv PDFs were unreadable in full (abstract only).

## 1. Efficiency of game totals, team totals, quarter/half lines (biases, magnitudes, after vig)

### Takeaway
Full-game NBA totals look largely efficient after vig in the academic record. The one repeated anomaly is early-season under bias, and it is old (1995-2012 samples). I found no rigorous study at all of quarter, half, or team-total line bias.

### Cited Findings
- 10,325 games, 2000-01 to 2007-08 (Gold Sheet): overs won 5,059, unders 5,086 (over 49.87%), a fair bet; log-likelihood test also vs 52.38% break-even (vig-inclusive). Totals 211-220 had over rates above 52.38% in 8 of 10 levels, max 54.39% at 215-216, none significant at 10%. Pre-2018. — [The Sport Journal](https://thesportjournal.org/article/nba-gambling-inefficiencies-a-second-look/)
- Paul, Weinbach & Wilson, 1995-96 to 2001-02: pattern of over-betting the overs on high-scoring games (totals above 200), but no result rejected no-profitability after commission. Pre-2018, abstract only. — [IDEAS](https://ideas.repec.org/a/eee/quaeco/v44y2004i4p624-632.html)
- Baryla et al. (Finance Research Letters, 2007): totals lines significantly biased early in the season, sides lines not; betting against closing line won 56.72%. Pre-2018; sample size not seen. — [ETSU](https://dc.etsu.edu/etsu-works/17780)
- Journal of Prediction Markets paper (2009-2012 data): totals efficient most of season, but in week 1 the under hit 58.2% of games. Sample size and vig treatment not seen. — [UWF](https://ircommons.uwf.edu/esploro/outputs/journalArticle/Early-season-NBA-overunder-bias/99380090347306600)
- NBA.com: over hit 49.7% since 2003 (under 50.3%). Descriptive, not a vig test. — [NBA.com](https://www.nba.com/nbabet/what-percent-of-nba-games-go-over-the-total)
- 2023-24 monthly O/U (Action Network, practitioner): Nov and Dec ~54.6% over, Jan ~42.5% over; article text and table disagree internally; no quarter/half claims; small monthly samples (about 50-230 games). Anecdotal. — [Action Network](https://www.actionnetwork.com/nba/what-we-learned-nba-over-unders-all-star-break)
- Quarter/half lines: only a 2008 SBR forum thread (opinion that quarter lines are not a simple formula of the game line) and unverified SEO blogs claiming first-half totals are priced separately. No data. — [SBR thread](https://www.sportsbookreview.com/forum/handicapper-think-tank/337660-nba-1st-quarter-lines-archive)
- Moskowitz, Journal of Finance 2021 (NBA/NFL/MLB/NHL, 100k+ contracts, ~3 decades): momentum in line moves from open to close, reversed by results (delayed overreaction), but returns "fail to overcome transactions costs." Pre-game full-game lines. — [NBER](https://conference.nber.org/conf_papers/f92072/f92072.pdf)

### Inferences
- Any quarter/half/team-total edge is untested in public literature; a Kalshi trader would need to build the evidence from scratch.
- Early-season (Oct-Nov) totals under bias is the best-documented but dated anomaly; it needs re-testing on 2018-2026 data before use.

### Gaps
- No post-2018 peer-reviewed NBA totals study with pace; no quarter/half/team-total efficiency study; no home/away split totals study found.

## 2. Consistency/arbitrage relationships (team totals vs game total, halves vs full)

### Takeaway
No quantitative evidence found that these relationships are violated after costs. Team totals are mechanically derivable from spread + total, a claim only from forums/unverified sources.

### Cited Findings
- SBR thread explains team total = (game total +/- spread)/2 logic; opinion-level. — [SBR](https://www.sportsbookreview.com/forum/handicapper-think-tank/52073-deriving-team-total-value-from-spread-game-total-ganchrow)
- An unverified duckdns.org "data audit" doc says team totals are priced less efficiently than game totals; origin and performance unverifiable (do not rely). — [link](https://edge-assist.duckdns.org/data-status/docs/nba/ruling/NBA%20Team%20Totals%20Data%20Audit%20Ruling)
- "1.5-point differences between books" are line shopping, not riskless arbitrage (search summary of blogs).

### Inferences
- On Kalshi, a cross-market check (ladder monotonicity; sum of 1Q-4Q medians vs game total) is feasible but wide spreads and fees likely swallow small inconsistencies; untested.

### Gaps
- No data on frequency of violations. Pairs of Kalshi markets could be checked directly with its public API (not done here).

## 3. In-game/live markets: over/underreaction, momentum, speed

### Takeaway
The best recent evidence (June 2026, Kalshi NBA) says live prices adjust fast but incompletely, with larger underreaction when liquidity is low; the effect predicts drift over minutes. Net tradability after fees is not established in what I could read.

### Cited Findings
- Angelini & De Angelis, arXiv 2606.07811 (June 2026, working paper): one-minute Kalshi NBA winner contracts merged with play-by-play; a 1-minute change in out-of-sample benchmark probability maps to only ~0.64 contemporaneous price change; the unadjusted gap predicts drift over following minutes; underreaction much larger in low-liquidity states. — [arXiv](https://arxiv.org/abs/2606.07811) (abstract only; full PDF unreadable, so sample size, fees not verified)
- Moshrefi, arXiv 2607.14430 (July 2026): 23M Kalshi moneyline trades; calibration near perfect mid-life but deviates sharply in final 10 minutes before settlement (Prelec curvature well above 1, read as insurance demand); cross-game parlays priced above product of legs, worse with more legs. Moneyline, not spreads/totals. — [arXiv](https://arxiv.org/abs/2607.14430)
- Dias thesis (ISCTE, 2016): 4 NBA Finals games, second-by-second, live odds inefficient vs logit win-probability model (8 seasons of plays). Tiny sample, old, master's thesis. — [ISCTE](https://repositorio-iul.iscte.pt/handle/10071/13473?locale=en)
- Latency (vendor claims, low reliability): sharp books reprice in ~1-5s vs recreational 15-60s; streams run 5-8s behind events; one 410-wager test had FanDuel median acceptance 1.4s. — [SharpAPI](https://sharpapi.io/learn/how-live-is-real-time-odds-data), [SBC News](https://sbcnews.co.uk/uncategorized/2026/05/20/cost-stream-delay-live-betting)
- End-game fouling: a 2018 JSA paper values late-game tactics via win probability; NCAA Markov study 2025; no paper linking fouling to live totals mispricing. — [JSA](https://content.iospress.com/doi/10.3233/JSA-180231)
- Momentum: Byrnes & Farinella report 56.5% hot/cold-streak ATS (vs 52.4% break-even), pre-game spreads, sample not seen, older. — [Sport Journal](https://thesportjournal.org/article/the-effect-of-momentum-on-the-nba-point-spread-market/)

### Inferences
- Because the drift lasts minutes, a retail trader with a free-data win-probability model might not need millisecond speed on thin Kalshi books, but thin liquidity (where the underreaction is largest) is also where spreads/fills hurt most. Needs own backtest. Consistent with prior project finding that live NFL/NBA tests lost (see memory).

### Gaps
- No study of runs/timeouts/garbage time effects on live totals or quarter lines; no latency study specific to Kalshi; full-text numbers for 2606.07811 unread.

## 4. Alternate-line ladders and tail mispricing

### Takeaway
No NBA-specific ladder study found. Related Kalshi evidence is about time-to-expiry miscalibration and parlays, not spread/total ladders.

### Cited Findings
- 2607.14430 above (late-expiry distortion; parlay overpricing). — [arXiv](https://arxiv.org/abs/2607.14430)
- Polymarket student analysis: NBA pre-game markets well calibrated at 1 week/1 day/1 hour horizons. Informal. — [Laidlaw](https://laidlawscholars.network/posts/polymarket-calibration-analysis-across-4-domains)
- Practitioner claim (unverified): scanner comparing Kalshi spread/total to de-vigged Pinnacle with normal model, ~4c edge threshold after fee; no performance data. — [link](https://edge-assist.duckdns.org/data-status/docs/nba/strategy)
- Secondhand report: Kalshi had best pricing on game totals/moneylines in 47 of 67 March Madness games (college, not NBA). — via search summary, source not verified.

### Inferences
- Pinnacle de-vigged ladder as a fair-value anchor is the obvious test but Pinnacle's historical lines are not free.

### Gaps
- No evidence on favorite-longshot bias in NBA total/spread ladders; no Kalshi ladder dataset analysis found.

## 5. Data needed and free availability

### Takeaway
Play-by-play and box scores are free; historical quarter/half/alt-line odds are not found free.

### Cited Findings
- Free PBP 1996/97-2024/25 (stats.nba.com etc.), no betting lines. — [nba_data](https://github.com/shufinskiy/nba_data)
- nba_api (rate-limited) and nba_on_court packages. — [PyPI](https://pypi.org/project/nba-on-court/0.2.0)
- DunkAPI: full-game ML/spread/total lines 2013-14 to 2023-24, freemium (500 free credits); not quarter lines. — [DunkAPI](https://mcpservers.org/servers/noahowsh/dunkapi-mcp)
- Possible repo with Vegas lines (unverified contents). — papagorgio23/VegasLines on GitHub.

### Inferences
- Kalshi's own public API market history (candlesticks/trades) is the only realistic free source of quarter/half/ladder prices; a forward-collection (paper-trade) approach is likely needed.

### Gaps
- Free historical quarter/half lines not found; user would need to collect forward.
