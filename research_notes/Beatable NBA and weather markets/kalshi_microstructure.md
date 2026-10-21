# Kalshi / event-exchange microstructure: who wins, who loses (sports and weather focus)

Research date: 2026-10-21. About 17 tool calls. Primary-source text was read directly only for the Bürgi-Deng-Whelan paper (PDF, Jan 2026 version), the Kalshi LIP help page, and several press/blog pages. Kalshi's official fee-schedule PDF returned HTTP 429 and could not be read, so fee details below rest on secondary sources and are flagged.

## 1. What do academic papers find about returns by trader type and price level?

### Takeaway
The only rigorous peer-style Kalshi dataset (Bürgi, Deng, Whelan) shows a strong favorite-longshot bias. Sub-10c contracts lose over 60%, contracts above 50c earn small positive returns, and makers lose less than takers (-9.6% vs -31.5% post-fee) but are still negative on average. Almost all of the maker edge comes from buying favorites. A large 2026 think-tank study (Roosevelt Institute) estimates retail takers lost about $584M, two-thirds of it in sports. It uses "taker = retail" as a proxy, which Kalshi disputes.

### Cited Findings
- Paper: "Makers and Takers: The Economics of the Kalshi Prediction Market," Bürgi (UCD), Deng, Whelan; CESifo WP / CEPR DP20631; Jan 2026 version. Data: Kalshi API transaction data from 2021 through April 2025. 46,282 Yes contracts in 12,403 events (each open 24h or more, final volume at least $1,000, final spread 20c or less). Hourly crypto/index markets excluded. Daily prices back to 10 days before close give 156,986 Yes prices, so 313,972 purchased-contract observations. Median market volume is $8,982; mean trade $100, median $35 — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- Contracts bought at 10c or less lose over 60% (post-fee). Contracts above 50c earn small positive returns. Above 70c the returns are statistically significant but small. A 5c contract that wins 3% of the time has -40% pre-fee return. A 95c contract winning 98% has +3.1% pre-fee — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- Average return is about -20% across all contracts (the paper says pre-fee; the VoxEU summary says about -22% after fees). This is not a paradox: the asymmetry between cheap and expensive contracts, weighted by contract count, makes the unweighted mean negative even though dollar-weighted pre-fee returns net to zero — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf); [search summary of VoxEU column](https://cepr.org/voxeu/columns/economics-kalshi-prediction-market) (the column itself returned 403; the -22% figure is from a search snippet)
- Maker vs taker: average post-fee return is -9.64% for makers and -31.46% for takers. Makers who buy contracts priced above 50c earn statistically significant but small positive returns; the paper puts the post-commission maker return on 50c-and-over contracts at 2.6%, with a standard deviation of 33% per contract — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- Takers buy most of the cheap contracts. Makers are 43.5% of buyers at 1-10c and 56.5% at 90-99c. About two-thirds of observations are below 10c or above 90c — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- The bias holds across categories (financials, climate and weather, crypto, politics, entertainment, economics) and is weaker for politics and entertainment. It is not explained by trade size (largest-transaction quintile has the largest coefficient). It weakens in 2025 data but is still present — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- Authors' stated reasons the bias persists: small volumes (top-decile markets average only $526,245 total; order books are thin), high per-contract variance, and (third reason, not read in full) limits on arbitrage capital — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- Sports caveat: the dataset ends April 2025, so it contains only about 3-4 months of sports. The authors say results are not sensitive to dropping 2025 or the sports category. Their evidence therefore says little directly about the sports-heavy regime of late 2025 to 2026 — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- Older, non-Kalshi: Intrade showed a favorite-longshot pattern only far from expiry, and "Yogi Berra bias" in late-game sports (trailing teams overpriced in the last 15 min). IEM showed no longshot bias — cited within [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- Roosevelt Institute analysis: over 400 million Kalshi trades, over $32B volume, July 2021 to May 2026, from a Dune snapshot. Estimated net retail (taker) losses were $583.5M, of which sports were $371.6M and exotics over $100M. Mean bet $75.20. 63.2% of taker revenue went to the 6.3% of matched orders worth $200 or more. Median prediction-market user lost 8% of amounts bet vs 5% for sportsbook users (their cited comparison). Limitations: single snapshot, "retail = taker" proxy, Kalshi disputes the framing — [Roosevelt Institute](https://rooseveltinstitute.org/blog/since-kalshis-launch-ordinary-users-have-lost-half-a-billion-dollars/); [Casino.org on Kalshi rebuttal](https://www.casino.org/news/kalshi-rebuffs-think-tank-claim-regular-traders-lost-584-million-on-platform/) (snippet only)
- Kalshi spokesperson (as reported): 2.9 users lose money for every 1 who profits over the past month — [search snippet via Roosevelt/Gigazine coverage](https://gigazine.net/gsc_news/en/20260507-prediction-markets-almost-user-loses) (not read in full)
- Polymarket concentration (non-Kalshi): top 1% of users captured 76.5% of trading gains (Akey et al., over 500M trades, 2022-2026); WSJ found 67% of profits went to 0.1% of users — both cited in [Roosevelt Institute](https://rooseveltinstitute.org/blog/since-kalshis-launch-ordinary-users-have-lost-half-a-billion-dollars/); original papers not read
- Parlays: Gambling Insider analysis (reported) of about 4.6M combo markets, July 14-27, 2026: YES buyers staked $226M and lost $79M (35c per dollar; 16.6c excluding the World Cup final). Sportico cites a roughly 15% Kalshi parlay hold — [On360 summary](https://on360.ca/kalshi-parlay-bettors-losing-far-more-than-headline-294m-figure-suggests-report/); [Sportico](https://www.sportico.com/business/sports-betting/2026/kalshi-parlays-retail-bettor-losses-rfq-1234894471/) (snippets only)

### Inferences
- A positive-expectancy passive strategy suggested by the paper is "maker buying contracts above 50c." The measured edge (about +2.6% per contract, SD 33%) is the average over all makers in a period with free maker fees. It excludes maker fees that began after April 2025 and does not account for adverse selection on a specific quoting style.
- The "maker" label is not a skill proxy: makers include informed traders and professionals, so the 2.6% is likely an upper bound for an unsophisticated passive small maker.
- The favorite-longshot result implies the cleanest "negative edge to avoid" is taking cheap (under 10-20c) contracts. Selling longshots as a maker is the mirror image, but it requires a taker to pay for them; the sources do not report the return to that specific side after adverse selection.

### Gaps
- No peer-reviewed study of Kalshi sports contracts or weather contracts specifically after 2025. I did not find Kalshi-specific work on in-game sports adverse selection.
- Weather-specific coefficients in Table 8 of the paper came out garbled in PDF text extraction and are not reported here.
- I did not find the Polymarket-specific academic papers (Akey et al.) themselves.

## 2. Fees and the Liquidity Incentive Program

### Takeaway
Taker fee is ceil(0.07 x C x P x (1-P)) to the cent (peaks about 1.75-1.77% of price at 50c). Maker fees are zero on most series but apply on some sports/event series since 2025 (exact list not verified from the official schedule). LIP pays pro-rata by a size-and-proximity score, with $1 to $1,000 per market per day; there is no independent evidence that a small maker nets a profit after adverse selection.

### Cited Findings
- Pre-April-2025 regime: takers paid $0.07 x P(1-P) per contract rounded up on the order total; makers paid nothing. Makers started being charged after April 2025 on some markets. The rounding makes the effective rate on a 100-contract order at 50c about 1.77% of price instead of 1.75% — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- 2026 secondary sources agree on the taker formula ceil(0.07 x C x P x (1-P)): $1.75 on 100 contracts at 50c, $0.63 at 10c. Maker fees reported as zero on most markets, with a quarter-rate formula (ceil(0.0175 x C x P x (1-P))) on certain series, and combo markets with a maker multiplier. Kalshi sets multipliers per series. One source disagrees by showing 2c at 50c in Kalshi's table, probably per-order rounding — [Chudi.dev](https://chudi.dev/blog/kalshi-fees); [pm.wiki](https://pm.wiki/it/learn/prediction-market-arbitrage-after-fees). Secondary sources only; treat as unverified.
- Kalshi help page: some markets have maker fees, charged only when a resting order executes. Cancelling is free. Fees vary by market, often for special events such as elections, awards and major sports championships — [Kalshi Help: fees](https://help.kalshi.com/en/articles/13823805-fees)
- Kalshi added a combo (parlay) market-making fee in August 2026, which was not on the published schedule at the time — [Bitcoin.com / Citizens JMP](https://news.bitcoin.com/kalshi-beats-the-books-on-nfl-totals-loses-to-fanduel-on-moneylines/)
- Citizens JMP: average Kalshi fee of $1.62 per 100 contracts in NFL Week 1 2026 (1.6% on outcomes, 1.8% on totals) — [Bitcoin.com](https://news.bitcoin.com/kalshi-beats-the-books-on-nfl-totals-loses-to-fanduel-on-moneylines/)
- Polymarket (non-Kalshi): now charges taker fees in crypto, sports and other categories; makers pay zero and receive rebates funded by taker fees (Sacra; secondary) — [Sacra](https://www.sacra.com/c/polymarket/)
- LIP rules (official): program ends January 1, 2027, and Kalshi can end or change it any time. Per market per time period, Kalshi sets a Target Size (more than 100 and fewer than 20,000 contracts) and a Discount Factor (up to 1.00). The book is sampled once per second at a random moment. The Reference Price is the first level, walking down from the best bid, where cumulative size reaches 1/5 of Target Size. Orders at or better than the Reference Price get multiplier 1.0, and worse orders get Discount^ticks. Raw score = size x multiplier, normalized by the side total. Snapshot score = yes share + no share. A snapshot is excluded unless both sides meet Target Size. Reward = your share of all snapshot scores x period reward x (valid snapshots / total snapshots), rounded down to the cent. Periods run up to 31 days and can overlap. Daily rewards run $1 to $1,000 per market. Minimum payout $1. Excluded: Kalshi affiliates and employees, IBs, FCMs and customers, non-U.S. users. SSN needed above IRS thresholds. Abuse can lead to revocation — [Kalshi Help: LIP](https://help.kalshi.com/incentive-programs/liquidity-incentive-program)
- Commentary by a former Kalshi Trading market maker (disclosed bias): short "snapshot-farming" quotes would earn about 10% of a steady maker's rewards; designated and affiliated market makers (SIG, Kalshi Trading) are not eligible for rewards, and it was unclear at the time whether their resting size dilutes the denominator. The author says adverse selection is the main reason few quote for free and that rewards are not quantified against it. His "six figures a month" and "$5M/month" figures are explicit speculation, not data — [UFO Holdings Substack](https://ufoholdings.substack.com/p/kalshis-new-liquidity-incentives)
- The older Volume Incentive Program (rewarded executed volume) is being terminated amid CFTC scrutiny — [DeFiRate](https://defirate.com/news/kalshi-volume-rewards-cftc-scrutinizes-prediction-markets/) (snippet only)

### Inferences
- Because the score is a share of all qualifying size, rewards on a market with deep professional quoting are diluted by roughly (your size)/(total qualifying size). The Target Size floor (more than 100 contracts on each side) means a small account must post both sides at 100+ contracts to count at all, which means taking two-sided inventory risk in the very markets where adverse selection is highest.
- The two-sided requirement means LIP pays for spread-straddling quotes, not for one-sided favorite buying.
- Zero maker fees on a series (the user's memory notes BTC 15-min series) are a series-level feature; sports and other series may differ, so every candidate series needs its fee multiplier checked via the API/market page.

### Gaps
- Official fee schedule PDF unread (HTTP 429), so the list of series with maker fees and sports multipliers is unconfirmed.
- No published analysis of realized LIP payouts per dollar of capital for small makers, and no confirmation of how affiliate/DMM size enters the denominator. The user's own LIP forward test is the only evidence for that.

## 3. Professional market makers on Kalshi sports; spreads and depth

### Takeaway
Susquehanna (SIG) has been a dedicated institutional MM since April 2024, Jump (stake-for-liquidity, reported Feb 2026) and Wintermute have joined, and Kalshi runs its own affiliate (Kalshi Trading). Kalshi says 95% of bid matches come from 2,000+ smaller makers. Spreads on liquid pre-game markets are about 1c but depth collapses in-game; sports are viewed as a lower-prestige, higher-risk area for institutions.

### Cited Findings
- Kalshi named Susquehanna Government Products its first dedicated institutional market maker in April 2024 — [Business Wire](https://www.businesswire.com/news/home/20240403664852/en/Kalshi-Onboards-Its-First-Dedicated-Institutional-Market-Maker); [Bloomberg](https://www.bloomberg.com/news/articles/2024-04-03/susquehanna-starts-trading-desk-for-event-contracts-on-kalshi)
- June 2025 state-court suits (MA, OH, SC) named Kalshi and Susquehanna International Group over alleged illegal gambling losses; outcomes not found — [Bloomberg Law / search summary](https://news.bloomberglaw.com/crypto/susquehanna-starts-trading-desk-for-event-contracts-on-kalshi)
- Jump Trading to take small equity stakes in Kalshi and Polymarket in exchange for liquidity (Bloomberg, Feb 2026, via secondary outlets); Wintermute began quoting two-sided on event contracts including Kalshi and Polymarket — [Casino.org](https://www.casino.org/news/jump-trading-taking-small-stakes-in-kalshi-polymarket/); [The Defiant](https://thedefiant.io/wintermute-starts-quoting-prediction-markets-as-event-contract-volume-tops-60b-in-2026). No source found confirming Citadel Securities makes Kalshi sports markets.
- Kalshi co-founder Luana Lopes Lara: only 5% of bid matches come from big institutional MMs; 95% from more than 2,000 smaller makers, some individuals. Sports trading is viewed as a lesser, higher-risk market by electronic firms, and Kalshi "actually has to incentivize market makers" — [eFinancialCareers](https://www.efinancialcareers.com/news/electronic-market-makers-trading-on-kalshi-are-small-fish-in-a-big-pond)
- Kalshi Trading (in-house MM): Lopes Lara said it is "not profitable" and handled less than 6% of sports making volume in November 2025 (about $310M of about $5.17B sports volume, an InGame estimate; total Nov volume about $5.82B) — [InGame](https://www.ingame.com/kalshi-in-house-trading-arm-not-profitable/)
- Sports share of volume: about 68% as of March 2026 per one report, 80% per another (conflicting) — [The Defiant](https://thedefiant.io/wintermute-starts-quoting-prediction-markets-as-event-contract-volume-tops-60b-in-2026) / search summary
- Blockworks Research on March Madness games: spreads about 1.11% by about 48h before tip and near a 1% floor through the game; in-game depth falls 76% (average 16.3K contracts vs 73K pre-game peak) — [Blockworks Research summary](https://app.blockworks.com/report/from-betting-to-trading-how-kalshi-is-reshaping-sports-markets) (only the search snippet was read; the report page redirected)
- Citizens JMP, NFL Week 1 2026 pre-game implied vig (28 data points; vs FanDuel / DraftKings): game outcomes 4.14% vs 4.08% / 4.31%; totals 4.50% vs 4.80% / 4.71%; parlays 23.8% vs 22.0% / 22.0%. Full 2025 NFL season average Kalshi 4.84%, trailing both books by 30-40 bps. These figures appear to exclude Kalshi's fee; Citizens' view is that adding fees puts Kalshi above both books — [Bitcoin.com](https://news.bitcoin.com/kalshi-beats-the-books-on-nfl-totals-loses-to-fanduel-on-moneylines/)
- Conflicting claim of 0.5-1.5% vig on flagship Kalshi markets — [Tech Insider](https://tech-insider.org/sports-prediction-markets/) (unclear fee treatment; low reliability)

### Inferences
- Thin, low-profile sports props and weather brackets are where institutions are least present, which is where a small maker faces less competition but also where depth is shallowest and informed-flow identification is hardest.
- Pre-game liquid markets (about 1c spread) leave little to capture net of adverse selection for a small participant competing against SIG/Jump-class quoting; the 1c is gross of the maker's information disadvantage.

### Gaps
- No public data on professional MM profitability, quoting style on props, or exact spreads and depth for NBA player props and weather brackets.
- Blockworks report not read beyond the snippet; sample size and exact dates unknown.

## 4. Adverse selection and cross-venue pricing

### Takeaway
The best direct evidence is indirect: the Kalshi paper explains makers' better returns as a willingness to cancel when news arrives, and the co-founder says the in-house MM loses money. I found no quantified study of stale-quote sniping or news-driven fills in Kalshi sports or weather, and no rigorous cross-venue (Kalshi vs Polymarket vs sportsbook) arbitrage study.

### Cited Findings
- Authors attribute maker outperformance to makers seeking better prices and having to "cancel those orders if new evidence emerges" — i.e. the maker edge depends on active quote management — [paper PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf)
- InGame notes that market making is generally hard to make profitable because of adverse selection, in the context of Kalshi Trading being unprofitable — [InGame](https://www.ingame.com/kalshi-in-house-trading-arm-not-profitable/)
- Whelan predicts sports prediction markets will need live data feeds and suspensions during major events (a forecast, not a finding) — [Whelan presentation page](https://www.karlwhelan.com/presentation-of-prediction-markets-and-their-future/) (snippet only)
- Cross-venue: only secondary material found. Claims of 1-5c gross gaps shrinking to 0.5-1.5c after fees on flagship markets, and a cited but unverified 2025 study (Clinton and Huang) of 2024 election price differences across venues — [pm.wiki](https://pm.wiki/uk/learn/polymarket-kalshi-arbitrage); [search summary]. Treat as unverified. Different resolution rules between venues are a documented risk in these sources.
- Weather: practitioner claims that raw NWS gridpoint forecasts overshoot observed highs (about 1-3F in some cities) and that most weather traders are not meteorologists. These are blog and vendor claims, an unaudited paper-trading experiment, and one bot anecdote of $1,800 peak profit that later stopped working — [Weather Edge MCP listing](https://mcpservers.org/es/servers/rjw34/weather-edge-mcp); [Weather Station Advisor](https://weatherstationadvisor.com/home-weather-station-prediction-market/)

### Inferences
- Kalshi's rapid growth to sports plus the unprofitability of the affiliate MM at less than 6% share suggests passive sports quoting is not an easy edge even for staffed professionals.
- The prior in this repo (memory: NFL/NBA/MM/weather tests all failed) is consistent with the literature; nothing found here contradicts it.

### Gaps
- No quantified evidence of stale-quote sniping rates, latency requirements, or fill toxicity on Kalshi sports/weather.
- No academic cross-venue sports study (Kalshi vs Polymarket vs sportsbooks) with sample sizes.

## 5. Documented small-trader strategies with audited or after-fee results

### Takeaway
I found no audited, after-fee, multi-period small-trader strategy result on Kalshi. Everything is anecdote, vendor marketing, or aggregate loss statistics.

### Cited Findings
- Aggregate evidence is negative: Kalshi's own 2.9-to-1 loser ratio; Roosevelt estimate of $583.5M retail taker losses; when retail takers do win it is mostly on a few largest bets — [Roosevelt Institute](https://rooseveltinstitute.org/blog/since-kalshis-launch-ordinary-users-have-lost-half-a-billion-dollars/)
- Polymarket winner concentration: finance-magnate-reported finding that only traders with more than $500,000 in activity had positive returns (cited by Roosevelt; original not read) — [Roosevelt Institute](https://rooseveltinstitute.org/blog/since-kalshis-launch-ordinary-users-have-lost-half-a-billion-dollars/)
- A "How to make money trading on Kalshi" Substack and weather-bot blog posts exist but give no audited P&L — [4AM Club](https://www.4amclub.substack.com/p/how-to-make-money-trading-on-kalshi) (appeared in search results; not opened)

### Inferences
- Structural positions with some documented support: (a) maker orders on favorites above 50c, average +2.6% post-fee in 2021-Apr 2025 with high variance and without current maker fees or adverse-selection controls; (b) avoiding taker purchases below 20c, where losses are largest. Positions without support: LIP capture by a small maker, passive sports quoting against professionals, longshot selling with after-fee audited results.
- Edge, if any, for a small account is more plausibly in specialized forecasting (for example weather bias correction) than in structure, but that is unproven here.

### Gaps
- No audited small-trader track record found; no source reports realized P&L for LIP participants or weather traders.
