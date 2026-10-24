# NBA news timing: injury/rest/lineup news and market reaction speed

Research depth note: about 14 tool calls. Search snippets were thin. Rigorous quantitative evidence on reaction speed, questionable-to-play rates and prop under-reaction was NOT found. Those are flagged as gaps.

## (a) NBA injury report rules, schedule, late changes

### Takeaway
The official report has fixed deadlines (5 pm local day before; 11 am-1 pm game day; 1 pm for the second night of a back-to-back) and is updated through the day. Teams are fined for misreporting. After the Oct 2025 gambling indictments the league reviewed the rules and floated faster updates. Starting lineups are due 30 min before tip but can be amended.

### Cited Findings
- Deadlines: by 5 pm local the day before a game; for the second game of a back-to-back, by 1 pm local on game day; game-day report between 11 am and 1 pm local (8-10 am if tip is 5 pm or earlier), excluding the second night of a back-to-back; updated continually through the day. Covers injury, illness, other medical conditions or rest. The page itself lists no penalties or status categories. — [NBA Official: Injury Report 2025-26](https://official.nba.com/nba-injury-report-2025-26-season/)
- The official page links timestamped PDFs at 15-minute intervals (e.g., 12:00, 12:15, 12:30 a.m. ET on Oct 21, 2026), which shows PDF files carry timestamps usable for timing studies. The page does not state a general publication schedule. — [NBA Official](https://official.nba.com/nba-injury-report-2025-26-season/)
- Nominal status key on team reports: Probable 75%, Questionable 50%, Doubtful 25%, Out 0%. These are labels, not empirical rates. — [Kings injury report posts on nba.com](https://www.nba.com/kings/blog/kings-mavericks-iu-101917) (older, team blog)
- Enforcement examples: 76ers fined $100,000 (announced about Dec 3, 2025) for listing Embiid "out" for the Nov 30, 2025 game vs Atlanta; he was upgraded to questionable and played (18 pts, double-OT game). Prior Embiid-related fines: $75,000 (Feb 2024), $100,000 (Oct 29, 2024). — [CBS Sports](https://www.cbssports.com/nba/news/joel-embiid-injury-nba-fines-76ers), [ESPN](https://africa.espn.com/nba/story/_/id/47190324/76ers-fined-injury-report-violation-regarding-joel-embiid). Orlando Magic fined $25,000 (April 2026) after Anthony Black was listed "Out" in the initial report and then played. — [NBA.com](https://www.nba.com/news/magic-fined-25k-violating-league-injury-reporting-rules)
- Player Participation Policy (adopted Sept 2023): applies to nationally televised and in-season tournament games; "star" = All-Star or All-NBA in past three seasons; fines $100,000 first, $250,000 second, then +$1M per further violation; injured stars exempt; back-to-back exceptions. Sources are from adoption, not 2025-26 enforcement. — [Fox News](https://foxnews.com/sports/nba-approves-new-rules-introduces-harsher-penalties-in-latest-effort-to-curb-load-management.amp), [Mediaite](https://www.mediaite.com/media/sports/nba-approves-stricter-rules-in-an-effort-to-curb-load-management-among-star-players)
- Lineups: Board of Governors moved the starting-lineup submission deadline from 10 to 30 minutes before tip (Sept 2019); teams may amend starters before the game (e.g., warm-up injury). — [NBA.com / casino.org coverage](https://www.casino.org/news/nba-could-change-lineup-submission-time-helping-bettors), [Action Network (pre-change discussion, says 10 min then)](https://www.actionnetwork.com/nba/nba-starting-lineup-announcements-timing-move-earlier-report)
- Oct 27, 2025 league memo: review of injury-disclosure rules, monitoring (incl. AI on betting/social data), and prop-bet integrity, after federal cases (Rozier, Billups, Damon Jones, the last accused of tipping bettors on LeBron/Davis health before it was public). — [Covers](https://www.covers.com/industry/nba-to-review-injury-policies-integrity-monitoring-after-betting-scandal-oct-28-2025), [NewsNation](https://www.newsnationnow.com/us-news/sports/lakers-other-teams-asked-to-turn-in-cell-phones-for-nba-gambling-probe-report/amp/)
- Dec 2025 memo (reported as proposed/adopted tweaks, implementation unconfirmed): game-day report 11 am-1 pm local; NBA.com updates every 15 min on status change versus hourly. — [Legal Sports Report](https://www.legalsportsreport.com/249720/nba-betting-scandals-prompt-injury-reporting-overhaul/), [AOL/AP](https://www.aol.com/articles/nba-tweaks-injury-reporting-rules-234520316.html)

### Inferences
- Two Embiid/Black-style cases show "Out" is not fully reliable in the initial report, and fines imply teams are policed on this. Large pre-tip status flips are rare enough to generate fines individually.
- Fines only target misreporting; they do not force early disclosure of lineup decisions, so the last 30-90 minutes before tip remain the informational window.

### Gaps
- No empirical data found on how often "questionable" players play, or how often statuses change after game-day deadlines (would require archiving injury-report PDFs against box scores).
- Whether the 15-minute update rule was formally in force for 2026-27 not confirmed.
- Official penalty schedule text not retrieved.

## (b) Market reaction speed (sportsbooks, exchanges, prediction markets)

### Takeaway
Only unsourced industry assertions ("seconds, not minutes") were found. No timestamped study of NBA injury-news reaction was located. Prediction-market sports prices track sportsbooks closely, and Kalshi/Polymarket lag was not directly measured.

### Cited Findings
- Action Network guide: news-to-adjustment is "seconds, not minutes"; books pull games off the board; some books run keyword scrapers on social/news feeds; late scratches move lines near tip. Explicitly no measured dataset. — [Action Network](https://www.actionnetwork.com/education/how-injuries-affect-betting-lines-a-guide-to-market-movement)
- Dare, Dennis & Paul (2015, *Finance Research Letters* 13:130-136): NBA opening lines contain significant pricing errors when players are absent; no profitable strategy at closing lines. Older (pre-2022). — [RePEc](https://ideas.repec.org/a/eee/finlet/v13y2015icp130-136.html) (abstract paraphrased by fetch tool; sample size not retrieved)
- Princeton preprint (arXiv, July 2026) on 23M Kalshi moneyline trades: calibration deteriorates near expiry; prices not always valid probabilities. — [arXiv 2607.14430](https://arxiv.org/abs/2607.14430)
- Claremont thesis, 285 NFL games 2025-26: Kalshi marginally more accurate than DraftKings by Brier, small and marginally significant; both worse nearer game time. Undergraduate thesis, NFL. — [Claremont](https://scholarship.claremont.edu/cmc_theses/4214)
- Blockworks (industry report, not peer-reviewed): 3.1K games, $8.5B notional, Kalshi correlation with sportsbooks up to 0.996, but 2.75% cost threshold and thin in-game liquidity leave few arbitrageable gaps. — [Blockworks](https://app.blockworksresearch.com/research/from-betting-to-trading-how-kalshi-is-reshaping-sports-markets)
- Pinnacle's API is closed to the public, limiting public lag studies. — [OddsPapi](https://oddspapi.io/blog/?p=2489)
- Kalshi lists "Will [athlete] compete" availability markets (NFL focus in sources; self-certified Feb 2026); resolve on appearing in the official game record. CFTC pressed Polymarket to withdraw; CFTC in June flagged injury occurrence/severity contracts as likely contrary to public interest. NBA-specific coverage not found. — [Casinobeats](https://casinobeats.com/2026/08/31/polymarket-pulls-nfl-player-availability-bets-at-cftc-request-kalshi-keeps-them/), [Legal Sports Report](https://legalsportsreport.com/274126/kalshi-launches-markets-on-whether-athletes-will-play)

### Inferences
- Because Kalshi NBA prices correlate ~0.99 with sportsbooks, they most plausibly follow book moves; any lag edge would be seconds-scale and need automated cross-book feeds (paid), not a free-data approach. This is inference, not measured.

### Gaps
- No published measurement of Kalshi/Polymarket lag after NBA injury news; no Pinnacle vs US-book vs exchange ordering study found.

## (c) Documented edges from speed or from under/over-reaction

### Takeaway
No credible documented net-of-cost edge was found. Available evidence says closing lines price star absences well; any inefficiency is in opening lines, which are pre-news and need book access.

### Cited Findings
- Action Network (Matt Moore, Nov 2019, informal, 42 star players): ATS 49.1% with star vs 51.3% without; concludes closing line prices star absences well. Old, small, non-peer-reviewed. — [Action Network](https://www.actionnetwork.com/nba/nba-betting-picks-odds-angles-matt-moore-november-2-2019)
- Practitioner claims (unsourced): sharps use usage-rate models and target secondary prop lines before books adjust; public overreaction creates "buy-back" value on the injured team; star worth ~4-6 spread points; questionable player priced at roughly probability x value. — [Action Network guide](https://www.actionnetwork.com/education/how-injuries-affect-betting-lines-a-guide-to-market-movement)
- Insider-information abuse (federal cases, Oct 2025) shows the real timing edge was non-public information, now illegal and under heightened surveillance. — [Covers](https://www.covers.com/industry/nba-to-review-injury-policies-integrity-monitoring-after-betting-scandal-oct-28-2025)
- An unattributed planning doc assumed ~60% of a missing player's usage is redistributed; not evidence. — [edge-assist doc](https://edge-assist.duckdns.org/data-status/docs/nba-player-props/ruling/NBA%20Player%20Props%20Research%20Pipeline%20Council%20Ruling) (low quality)

### Inferences
- Consistent with the user's prior memory (all tested Kalshi edges lost), an injury-news edge on Kalshi would need to beat a market that follows sportsbook lines within seconds, plus Kalshi taker fees. Taker round-trip fees at small size plus thin books make this very unlikely.
- The only plausible small-trader angle (untested): Kalshi spreads/totals/props that are slow to reprice versus books on thin markets; needs forward logging of Kalshi vs book prices around report timestamps.

### Gaps
- No peer-reviewed post-2022 evidence on prop under-reaction to star absences, back-to-backs or minutes restrictions.
- No data on prop lines adjusting to teammates' absences.

## (d) Professional operators and realistic latency; (e) free data feeds

### Takeaway
Pros rely on automated scrapers of insider accounts and team feeds; books do the same. A free-data retail trader is minutes late at best.

### Cited Findings
- Books scrape social media/news feeds; first-break sources are team accounts, league insiders, and beat reporters (shootaround reports). — [Action Network](https://www.actionnetwork.com/education/how-injuries-affect-betting-lines-a-guide-to-market-movement)
- Official report PDFs are timestamped, published through the day (15-min file granularity visible), and free. — [NBA Official](https://official.nba.com/nba-injury-report-2025-26-season/)
- League says it is expanding monitoring of betting and social data to detect misuse of nonpublic info. — [Covers](https://www.covers.com/industry/nba-to-review-injury-policies-integrity-monitoring-after-betting-scandal-oct-28-2025)

### Inferences
- Free feeds: injury PDF (polled every minute), team/beat X accounts (X API is paid; not verified here), 30-min pre-tip lineup release. All are public simultaneously to books' automated systems, so a hobbyist cannot be first.

### Gaps
- No source on actual latencies of pro news scrapers or X API access limits/costs; no reputable writeup on Shams/Woj-driven line moves with timestamps.
