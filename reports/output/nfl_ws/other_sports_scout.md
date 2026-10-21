# Other-sports scout: do Kalshi sports have the NFL thin-vs-deep structure?

Read-only scout of Kalshi public market data, run 2026-10-20 ~00:30-00:50 EDT. "Next 7 days" = event dates 2026-10-20 to 2026-10-27. No orders, no portfolio endpoints, no existing files edited.

## Method and caveats

- `GET /series?category=Sports` returns all 4,014 series, and every one is labeled "Sports". The category filter does not separate sports, so I grouped series by ticker prefix and title keywords instead (KXNFL*, KXNBA*, KXNCAAMB*, etc.).
- The analog search matched keywords in tickers and titles ("Game", "Spread", "Margin", "Tie", "Draw", "Protected"). It is not an exhaustive sweep. A missing series means no match was found, not that none exists.
- Event counts use `GET /events?series_ticker=...&status=open`, paginated, filtered on the date code in the event ticker.
- Example-game market counts use `GET /markets?event_ticker=<SERIES>-<suffix>` for every series with the sport prefix. This matched the nested-markets probe for NFL and NBA. One anomaly: `/events/{t}?with_nested_markets=true` returned 0 markets for `KXEPLGAME-26OCT23IPSNFO`, but `/markets` returned 3. Nested counts can undercount, so I used the `/markets` counts.
- Fee types come from `GET /series/{ticker}`. `quadratic_with_maker_fees` means the series has a maker-fee variant. Fee multipliers are 1 unless noted.

## Per-sport table

Series are listed as: winner / spread / win-margin / tie / protected. "none" means no series found.

| Sport | Winner (fee_type, mult) | Spread | Win-margin buckets | Tie | Protected props | Open events, next 7 days (winner / spread / margin / prot) | Markets per example game |
|---|---|---|---|---|---|---|---|
| **NFL** | KXNFLGAME (qwmf, 1) | KXNFLSPREAD (qwmf, 1) | KXNFLWINMARGIN (quadratic, 1) | KXNFLTIE (quadratic, 1) | KXNFLRECYDSPROT, KXNFLRECPROT, KXNFLTDPROT, KXNFLPASSYDSPROT, KXNFLPASSTDSPROT, KXNFLRSHYDSPROT (all quadratic, 1) | 14 / 14 / 1 / 1 each (RECYDSPROT has 2 open events in total) | Example KXNFLGAME-26OCT22NECHI: 1,031 active markets in 67 series. Margin 7, tie 1, KXNFLGAME 2 (see below). |
| **NBA** | KXNBAGAME (qwmf, 1) | KXNBASPREAD (qwmf, 1) | KXNBAWINMARGIN (quadratic, 1) | none | KXNBAPTSPROT, KXNBAREBPROT, KXNBAASTPROT, KXNBA3PTPROT (quadratic, 1). **0 events ever** (all-status query returned 0 for PTSPROT) | 52 / 18 / 14 / 0 | Example KXNBAGAME-26OCT21MILWAS: 129 active markets in 12 series. Margin 8, spread 33, total 29, team total 38, winner 2 |
| WNBA | KXWNBAGAME (qwmf, 1) | KXWNBASPREAD (quadratic, 1) | none | none | none | 3 / 1 / - / - | not probed |
| NHL | KXNHLGAME (qwmf, 1) | KXNHLSPREAD (quadratic, 1) | none | none | none | 47 / 25 / - / - | not probed |
| MLB | KXMLBGAME (qwmf, 0.5) | KXMLBSPREAD (quadratic, 0.5) | none | none | none | 4 / 1 / - / - | not probed |
| NCAA football | KXNCAAFGAME (qwmf, 1) | KXNCAAFSPREAD (qwmf, 1) | none | none | none | 113 / 56 / - / - (224 winner events open in total) | not probed |
| NCAA men's basketball | KXNCAAMBGAME (qwmf, 1) | KXNCAAMBSPREAD (qwmf, 1) | KXNCAAMBWINMARGIN (quadratic, 1) | none | none | 0 / 0 / 0 / - (first game 26NOV01) | Example KXNCAAMBGAME-26NOV01NDVILL: 2 markets, 1 series |
| NCAA women's basketball | KXNCAAWBGAME (quadratic, 1) | KXNCAAWBSPREAD (quadratic, 1) | KXNCAAWBWINMARGIN (quadratic, 1) | none | none | 0 / 0 / 0 / - (first game 26NOV01) | Example KXNCAAWBGAME-26NOV01VILLND: 2 markets, 1 series |
| NCAA college basketball (other) | KXNCAABGAME (quadratic, 1) | none | none | none | none | not checked | not probed |
| EPL | KXEPLGAME (qwmf, 1) | KXEPLSPREAD (quadratic, 1) | none (KXEPLPOINTMARGIN is a season-champion market) | **in-game**: TIE outcome inside KXEPLGAME events (e.g. KXEPLGAME-26OCT23IPSNFO-TIE). No separate tie series. | none | 10 / 10 / - / - | not probed (only 3 markets seen for one game) |
| Champions League | KXUCLGAME (qwmf, 1) | KXUCLSPREAD (quadratic, 1) | none | not checked (probably in-game, like EPL) | none | 18 / 18 / - / - | not probed |
| MLS | KXMLSGAME (quadratic, 1) | KXMLSSPREAD (quadratic, 1) | none | not checked | none | 15 / 15 / - / - | not probed |
| Bundesliga | KXBUNDESLIGAGAME (qwmf, 1) | KXBUNDESLIGAWSPREAD (quadratic, 1) | none | not checked | none | 9 / 0 / - / - | not probed |
| Ligue 1 | KXLIGUE1GAME (qwmf, 1) | KXLIGUE1SPREAD (quadratic, 1) | none | not checked | none | 9 / 9 / - / - | not probed |
| La Liga | none found | KXLALIGASPREAD (quadratic, 1) | none | not checked | none | - / 10 / - / - | not probed |
| Liga MX | none found (KXLIGAMXSPREAD is titled "Liga MX Game") | KXLIGAMXSPREAD (quadratic, 1) | none | not checked | none | - / 18 / - / - | not probed |
| UFC | KXUFCFIGHT (quadratic, 1) | none | none | none | none | 14 / - / - / - | not probed |
| Tennis (ATP / WTA) | KXATPGAME, KXWTAGAME (quadratic, 1) | KXATPGAMESPREAD (quadratic, 1) | none | none | none | 0 / 0 (ATP, WTA) | not probed |
| UFL | KXUFLGAME (quadratic, 1) | none | none | none | none | 0 | not probed |

Notes on the table:
- Tournament- or season-level margin markets exist but are not per game, so they are excluded: KXPGAWINMARGIN, KXWCWINMARGIN, KXPRESCUPWINMARGIN, KXBUNDESLIGAPOINTMARGIN, KXEPLPOINTMARGIN, KXLIGUE1POINTMARGIN, KXLALIGAPOINTMARGIN, KXSERIEAPOINTMARGIN.
- NFL weekly tie series (KXNFLTIES, KXNFLWEEKTIE) exist alongside KXNFLTIE. Not analyzed.

## Example-game quotes: win-margin buckets

**NFL, KXNFLWINMARGIN-26OCT22NECHI** (the only NFL margin event in the next 7 days; 7 buckets). Yes bid / yes ask, open interest:

| Bucket | Yes bid | Yes ask | OI |
|---|---|---|---|
| Tie | 0.01 | 0.02 | 1 |
| New England by 1 to 6 | 0.12 | 0.20 | 275 |
| New England by 7 to 14 | 0.12 | 0.15 | 270 |
| New England by 15 or more | 0.01 | 0.11 | 0 |
| Chicago by 1 to 6 | 0.12 | 0.25 | 46 |
| Chicago by 7 to 14 | 0.11 | 0.21 | 0 |
| Chicago by 15 or more | 0.06 | 0.19 | 0 |

**NBA, KXNBAWINMARGIN (14 events in the next 7 days)**. Buckets are "1-5 / 6-10 / 11-20 / 21+ points" per team, 8 per game, no tie. Sample of 14 games:
- In 10 of 14 sampled games, all 8 buckets had zero yes bid. In a further game, 7 of 8 did. Median yes spread per game ranged from about 0.10 to 0.20.
- Open interest is 0 on most buckets. Where it is nonzero, it is concentrated in one or two buckets per game (e.g. KXNBAWINMARGIN-26OCT21CHABKN: Brooklyn 1-5 at 0.02/0.21 with OI 1,155; Brooklyn 6-10 at 0.00/0.61 with OI 1,140).
- Examples: KXNBAWINMARGIN-26OCT20BOSDET, Boston 1-5 at 0.02/0.20 (OI 88), Boston 6-10 at 0.04/0.20 (OI 94). KXNBAWINMARGIN-26OCT20PHINYK, Philadelphia 1-5 at 0.01/0.83 (OI 400).

## Ranking: which sports look most like the NFL setup

1. **NBA.** Closest match. It has a game winner, a spread ladder, and a per-game win-margin bucket series with 14 games in the next 7 days, plus many bucket markets that show no bids. It has no tie market, and its protected-prop series have zero events, so the protected side of the NFL setup is missing.
2. **NFL (reference).** It has all five analogs, and protected props are plentiful (89 markets on the example game's receiving-yards protected series). The margin series is thin in time: only 1 game (NECHI, 2026-10-22) has margin buckets in the next 7 days.
3. **Soccer (EPL, UCL, MLS, Bundesliga, Ligue 1, La Liga, Liga MX).** Winner and spread series are active, with 9-18 games per league in the next 7 days. The draw is an in-game outcome, which is a loose analog of the NFL tie market. No per-game margin or protected series was found, so this is only a partial match.
4. **NCAA men's and women's basketball.** Both have per-game win-margin series, which fits the margin analog. However, the first events are 2026-11-01, so there are no games in the window. No protected series was found.
5. **NHL, MLB, NCAA football, WNBA.** Winner and spread series only. NCAA football has the most games (113 in the window). No margin, tie, or protected series was found.
6. **Tennis, UFC, UFL.** Little or no structure in the window (no spread or margin series with open events, and tennis and UFL have no open winner events).

## Files

- Series list and metadata: `C:/Users/Jarvis/AppData/Local/Temp/claude/C--Users-Jarvis-Documents-Kalshi-PM/202e17c4-729e-4c0b-a7d3-ce149a4ff08b/scratchpad/series.json` (4,014 series)
- Analog series details: `.../scratchpad/series_detail.json`
- Event counts by series: `.../scratchpad/step3.json`
- Example-game market counts: `.../scratchpad/example_counts.json`
- Margin quotes: `.../scratchpad/quotes.json`
