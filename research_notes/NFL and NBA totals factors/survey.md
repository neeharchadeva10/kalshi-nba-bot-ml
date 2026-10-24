# NFL (and NBA) game totals: evidence base for well-researched assumptions

Started 2026-10-23. Read-only web research. Every claim has a URL. "Not found" means I looked and did not find a number in a reputable source.

Evidence grades used below:
- **A** = peer-reviewed or large-sample (10+ seasons, 2,000+ games) analysis with method shown.
- **B** = credible quantitative analyst / bookmaker write-up with a stated sample, not peer reviewed.
- **C** = blog/tout/betting-site claim, small sample, or no method shown. Treat as a lead, not a fact.

Status: COMPLETE (2026-10-23). Sections 1-9 follow the brief; section 10 holds my own checks on free nflverse data; section 11 lists testable hypotheses.

## Bottom line (plain language)
1. **NFL totals markets are close to efficient at the closing line.** The famous "bet unders on high totals" rule won 58.7% (1979-2000) and 59.7% (2001-2009) but lost from 2010-2018 (47.1%) because books stopped shading lines, even though the public still bets overs (Kelly & Chen). Line moves from open to close are about half reversed by results (Moskowitz, J. Finance 2021), but not by enough to beat sportsbook vig.
2. **Wind is the only weather factor with consistent evidence.** Real scoring drops ~4 points from calm to 16+ mph. Against the closing total, games with *observed* 10-19 mph wind went under ~58-60% in 2015-2023 (my nflverse check; Sports Insights 55.9% at 10+ mph 2003-2015; Borghesi 2008 peer-reviewed). Extreme wind (20+ mph) is fully priced (~50%). Big caveats: every study uses observed wind (hindsight), not the forecast you could bet on, and 2024-2025 were ~50%. This needs a test with archived forecasts (H1).
3. **Cold, domes, rest/byes, Thursday games, divisional games, coaching changes, backup QBs and referees show no reliable edge against closing totals** in recent seasons. NFL referee over/under "trends" are statistically indistinguishable from coin flips (my test: chi2 10.4 on 16 df; trend-following rule 49.1%).
4. **Primetime (SNF/MNF) unders** are a small, persistent lean (~56% under in 399 games, 2015-2025, both halves) with no proven cause. Treat it as a hypothesis.
5. **Shape of the total-points distribution matters for ladders:** (total - closing line) is right-skewed (mean +0.4, median -0.5, SD 13.2). A normal curve would overprice "over by 1-8" strikes by ~2 probability points and underprice huge-over tails. A one-day snapshot of Kalshi KXNFLTOTAL ladders suggests Kalshi already prices this skew. Kalshi has a documented general favourite-longshot bias (Bürgi, Deng & Whelan 2025), which could make cheap tail strikes overpriced (H6-H7).
6. **NBA:** the only repeated academic totals finding is an early-season bias (week-1 unders 58.2% in 2009-2012; 56.7% strategy in Baryla et al. 2007), which has not been re-tested since. Referee crews differ in fouls, but no evidence shows that moves totals. Mid-season coaching changes rarely improve teams (12-15% of cases), and nothing has been published on pace.

---------------------------------------------------------------------
## 1. Weather

### 1.1 Wind (the only weather factor with consistent evidence)
Effect on points (raw, not vs line):
- nflanalytic.com explainer, 7,276 games 1999-2025, nflverse game log, single game-time wind reading, grouped means (no controls): outdoor avg total **44.7 (0-5 mph), 43.5 (6-10), 42.0 (11-15), 40.5 (16+ mph)** -> ~4.2 pt calm-to-windy gap. Dome/closed 46.9 vs outdoor 43.4. Grade B (big sample, but raw means; team quality and era confounded). https://nflanalytic.com/explainer-weather-and-scoring.html
- Wharton Sports Analytics (2022, nflfastR, weather parsed from text field available from 2014): "significant linear drop off in Pass EPA/play" from little wind to extreme wind; passing variance also falls in extreme wind. Grade B (student analytics journal). https://wsb.wharton.upenn.edu/wp-content/uploads/2022/09/2022_Football_Parks_Weather.pdf

Effect vs the betting total (this is what matters):
- **Borghesi (2008)**, "Weather biases in the NFL totals market", *Applied Financial Economics* 18(12):947-953, NFL 1984-2004. Heat, wind and rain reduce scoring and bettors underestimate it; a weather-based under strategy won out-of-sample at a rate "significantly above" 52.38%. Exact win rate not in abstract (full text paywalled -> not found). Grade A but OLD. https://ideas.repec.org/a/taf/apfiec/v18y2008i12p947-953.html
- **Paul, Weinbach & Weinbach**, "The Impact of Atmospheric Conditions on Actual and Expected Scoring in the NFL" (FiT Publishing journal; abstract only): humidity significantly related to actual-minus-total; simple strategies on humidity, wind, or both "reject market efficiency". Numbers not found (abstract only). https://fitpublishing.com/node/3503 (page returned 404 when fetched 2026-10-23; abstract quoted via search snippet).
- Sports Insights (David Solar, 2016-11-28), Pinnacle closing totals 2003-2015: unders in avg wind **10+ mph: 430-339, 55.9%, +8.6% ROI** (769 games); 5+ mph 51.5% (break-even); all games under 50.6%. When wind >= 10 mph the total fell during the week in 54.3% of games, rose in 33.0%. Public took the under in only ~11-16% of games. Grade B. https://www.sportsinsights.com/blog/how-do-sharp-nfl-bettors-use-weather-forecasts-to-make-money/
- Action Network / Bet Labs (database, not peer reviewed): unders **469-368-10 (56%)** in double-digit wind since 2003; also "nearly 50/50 when the over/under has fallen at least 1.5 points from open to close" (i.e. the edge is concentrated in games where the market did NOT already move). Hot (>= 85F) unders 64-49-1 (56.6%) since 2003; cold (< 30F) overs 90-62-1 (59.2%) (period not stated). Grade C+ (small cells, data-mined). https://www.actionnetwork.com/how-to-bet-on-sports/nfl/nfl-over-under-betting-weather-trends-wind-hot-cold-temperatures ; https://www.actionnetwork.com/nfl/super-bowl-54-betting-tip-weather-wind-49ers-vs-chiefs-miami-hard-rock-stadium
- "Jeff's Edge" Substack (2026-09-02), ~7,000 regular-season games 1999-2025 with closing totals: calm games finished ~**+1.5 over** the closing total on average, 20+ mph games **>2 under**. Under rate: **<10 mph 49%, 10-14 mph ~55%, 15+ mph 56%, 18+ mph ~59%**. 15+ mph by era: **56% (1999-2007), 58% (2008-2015), 55% (2016-2025)**. SD of (actual - closing total) barely changes: 13.8 calm vs 14.1 windiest. Grade B-/C (blog, but method stated and matches the other sources). https://jeffsedge.substack.com/p/i-evaluated-7000-nfl-games-to-see

- **Salaga & Howley (2024)**, "The impact of weather on betting outcomes and market behaviour in the NCAA football totals market", *Applied Economics Letters* 31(8):779-782 (college, not NFL): weather significantly predicts totals OUTCOMES but does NOT significantly predict the size of totals LINE MOVEMENTS -> the market under-uses weather; simple 3-variable weather strategies were profitable. Numbers not in abstract (paywalled). Grade A- (short letter). https://ideas.repec.org/a/taf/apeclt/v31y2024i8p779-782.html
- theScore betting writer claims the opposite (market OVER-corrects for wind/precip); FOX Sports research quoted as unders 54% in 20+ mph wind since 2015. No method. https://www.thescore.com/nfl/news/1815753/tips-for-betting-nfl-totals ; https://amp.foxsports.com/stories/nfl/nfl-odds-weather

**CRITICAL CAVEAT (look-ahead):** every one of these wind-vs-closing-total studies uses the OBSERVED game-time wind (PFR/nflverse), not the forecast available at bet time. A game that turned out windier than forecast is selected into the "windy" bucket after the fact, so part of the 55-59% under rate is hindsight. I found NO study that uses archived forecasts (searched; not found). The Action Network note that the edge falls to ~50/50 once the total has dropped 1.5+ points is consistent with "the market prices the forecast; the residual edge is forecast error". **Well-researched assumption:** wind >= 15 mph observed is worth ~-2 to -4 points of real scoring; whether a FORECAST of 15 mph beats the CLOSING total today is unproven and must be tested with archived forecasts (e.g. Open-Meteo historical forecast API, free) not observed wind.

Persistence: per the Jeff's Edge era split, the 15+ mph under rate was still 55% in 2016-2025. My own nflverse check (section 10) confirms it for 2020-2025 with observed wind: 10-14 mph 40.8% over (230 games), 15-19 mph 42.2% over (92 games); but 2024 and 2025 alone were ~50% (52 overs / 53 unders in 10-19 mph games).

### 1.2 Temperature / cold
- 4for4 (John Paulsen, 2016), 3,935 outdoor regular-season games since 1994: temperature has almost no relationship with total points (R^2 = 0.0082). <= 32F: 42.6 avg vs 42.1 overall; >= 80F: 41.5. https://www.4for4.com/how-does-cold-weather-impact-nfl-scoring
- nflanalytic, 1999-2025 outdoor: <= 32F 43.4 (384 g), 33-49F 43.0, 50-69F 43.7, >= 70F 43.0. Flat. https://nflanalytic.com/explainer-weather-and-scoring.html
- Vs the line: Sports Insights 2003-2015, games < 32F went OVER **122-97 (55.7%)**, regular season 57.8%, average ~2.5 pts over the closing total -> suggests the market OVER-adjusts down for cold. Action Network: < 30F overs 90-62-1. Jeff's Edge: <= 32F under 47% (i.e. over 53%). Three sources agree in direction (cold = slight over vs line), each small (150-250 games). Grade B-/C. Assumption: **cold by itself does not lower scoring; the market may shade cold games too low.** Not proven after vig in 2020+.
- Heat: Borghesi (2008) says heat lowers scoring and is underpriced (1984-2004). Action Network >= 85F unders 56.6% (114 games). Small.

### 1.3 Precipitation / snow
- No large-sample modern study with numbers found. Borghesi (2008) says rain lowers scoring and is underpriced (1984-2004, numbers not in abstract). Jeff's Edge explicitly skipped it (no reliable precipitation data). nflverse has no precipitation column (PFR weather text only from 2014 in nflfastR). **Not found.**

### 1.4 Dome vs outdoor
- Sports Insights 2003-2015: dome avg total line 44.6, avg points 46.2, overs 423-374 (53.1%); outdoor line 41.7, points 42.4, overs 49.4%. https://www.sportsinsights.com/blog/how-do-sharp-nfl-bettors-use-weather-forecasts-to-make-money/
- Sharp Football Analysis (no period stated): domes went over 52% (1,457 games) vs outdoor 48%; they call it "a mild tendency, not an edge". https://www.sharpfootballanalysis.com/betting/nfl-weather-betting/amp/
- Jeff's Edge: calm dome games under 49% (over 51%). Not beatable at -110 (needs 52.4%).
- Assumption: domes score ~3-4 more points than outdoor games, and the market prices nearly all of it.

### 1.5 Altitude (Denver)
- No quantitative study of Denver totals vs the line found. Anecdotal only: 3 of the 8 longest outdoor FGs ever were in Denver (CBS Sports). https://www.cbssports.com/nfl/news/cowboys-brandon-aubrey-nfl-longest-field-goal-record-denver . **Effect size: not found.**

### 1.6 Free data note (checked 2026-10-23)
- nflverse `games.csv` (https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv) has per game: total_line (one closing-ish line), over/under odds (2006+), roof, surface, temp, wind (observed), away/home_rest, div_game, home/away coach, referee, QB ids/names. I counted coverage: temp/wind present for ~95% of outdoor games every season EXCEPT **2022 (107 of 198 outdoor games) and 2023 (158 of 199)**; referee and coach present every game 1999-2025. No opening line, no forecast, no precipitation.
- Archived forecasts (needed to remove hindsight): Open-Meteo Historical Forecast API stitches the first hours of each model run (HRRR from 2018, ECMWF IFS HRES from 2017, GFS from 2021) -- i.e. short-lead forecasts; the Previous Runs API gives fixed 1-7-day lead times (GFS from March 2021, most others from January 2024). Hourly wind speed, gusts and precipitation. Free for non-commercial use (check terms). https://open-meteo.com/en/docs/historical-forecast-api

---------------------------------------------------------------------
## 2. Pace, scheme and efficiency (EPA, success rate, PROE)

What is established:
- **Offense is more stable than defense.** FiveThirtyEight (2009-2018): prior-season offensive DVOA explained 18.9% vs defensive DVOA 9.7% of next season (as quoted in search results; original page now redirects to ABC News, so not re-verified). https://fivethirtyeight.com/features/why-the-nfl-cant-rely-on-defense
- Year-to-year DVOA correlations vary a lot by year (secondary report of FTN/Football Outsiders data): 2022->2023 overall .66, offense .51, defense .26, special teams .28; 2021->2022 overall .37, offense .27, defense .33, ST .23. Grade B (FTN page blocked when fetched; numbers from search snippet). https://ftnfantasy.com/?p=33807
- nfelo "Weighted EPA" (2020, 1999-2019 pbp): down-weighting fumble recoveries and other random plays and weighting offense and defense separately makes first-8-games EPA better at predicting last-8-games point margin; splitting offense/defense weights "doubled" the lift; WEPA beat DVOA mid-season. Exact R^2 not in text (charts only). Grade B. https://www.nfeloapp.com/analysis/weighted-EPA-methodology-and-performance/
- Pace: "neutral pace" (play-clock seconds used when within 7 points in Q1-Q3) is the standard pace metric; play-clock use reflects coach intent better than seconds between plays. Sharp Football Analysis. https://www.sharpfootballanalysis.com/stats-nfl/nfl-team-pace-stats/ ; league pace jumped in 2020 https://www.sharpfootballanalysis.com/analysis/nfl-pace-2021-kingsbury-kyler-prescott/amp/
- Play-caller continuity drives stability: 4for4 (Chris Raybon, 2016) -- teams that keep their play-caller repeat tendencies; when the play-caller changes "only pace and running back target percentage have anything more than a weak correlation"; a new hire's pace from his previous job correlates r = .58 with his new team's pace. https://www.4for4.com/offense-coaching-impact
- Defensive tendencies carry over even after coordinator changes: pass% against r ~ .44 year to year (4for4 defense piece) https://www.4for4.com/2016/preseason/how-coaching-impacts-fantasy-football-part-2-defense ; PFF: blitz rate correlation 0.40-0.44 even after HC/DC change (paywalled snippet) https://www.pff.com/news/fantasy-football-how-predictable-are-blitz-rates-year-over-year

What is NOT established (searched, not found):
- A published split-half reliability number for team EPA/play or PROE in games (how many weeks to stabilize). Not found. Commonly quoted "half a season" is folklore here.
- Any published test that pace/PROE/EPA beat the CLOSING total after vig. Not found. The market (and every public model) already uses these; Moskowitz (2021) finds bettors OVER-react to recent scoring (section 7.2), which argues AGAINST chasing recent pace/scoring.
- **Assumption for us:** use multi-season, opponent-adjusted offensive efficiency + neutral pace as a baseline total (like the market), regress defense harder to the mean than offense, and treat a new play-caller as "reset pace/PROE toward his previous team's pace" (r ~ .58). Do not expect an edge from these alone.

---------------------------------------------------------------------
## 3. Personnel and injuries

- QB value (practitioner consensus, not peer reviewed): a starter-to-backup swap is usually worth **~3-7 points on the spread**, up to ~10 for an elite QB (Rodgers per ESPN/Vegas). Most other single positions move lines <= 0.5-1 point. ESPN Chalk: https://www.espn.com/chalk/story/_/id/28162566/how-much-every-nfl-qb-worth-spread ; CBS: https://www.cbssports.com/nfl/news/nfl-betting-2020-knowing-how-to-adjust-lines-when-each-starting-qb-is-ruled-out-during-the-season ; Deuces Cracked (2026 guide): https://www.deucescracked.com/blog/backup-quarterback-betting-impact-nfl-lines-guide-2026 . Grade B/C. Effect of a QB change on the TOTAL specifically: no systematic published estimate found.
- Speed of adjustment: practitioner view is that QB news is priced "almost instantly"; one guide claims totals adjust less efficiently than spreads on injury news because fewer people bet totals on news -- untested claim. https://www.deucescracked.com/blog/nfl-injury-reports-betting-line-movement-guide
- Action Network backup-QB index (Evan Abrams, updated 2026-10-08, Bet Labs, "600+ games" last decade): totals in 2024-2025 when a backup faced a starter: **overs 70-52 (57.4%)**; 2026 to date unders 9-2. When the line moved 7+ points against the backup's team (77 games): first half 44-32-1 to the under (58%), second half 36-39-2 (48%). Direction flips between windows -> noise. Grade C. https://www.actionnetwork.com/nfl/nfl-backup-qb-results-index-spreads-situational-edge-totals-trends
- My nflverse check (no-hindsight label = team starts a different QB than in its previous game): 2015-2025 **515 games, 47.4% over**; 2020-2025 314 games, **49.8% over**. Market prices QB changes in the total. (A "hindsight" label -- QB is not the team's most frequent starter -- gave 45.7% over, but that label is biased: bad backups get more starts.)
- Offensive line: only single-player on/off splits found (e.g. Lions with C Frank Ragnow on field +9.0 EPA per 60 plays, 2023). Small samples; no betting test. https://the33rdteam.com/?p=58240 . Grade C.
- Key defensive injuries: no quantitative totals study found. Not found.
- **Assumption for us:** QB changes are the only injury class with a well-documented, large effect, and the market prices them; the possible edge is timing (betting before late news is priced), which needs timestamped prices and news -- not in free data.

---------------------------------------------------------------------
## 4. Coaching changes

Published (all betting/media, no peer-reviewed NFL totals study found):
- Action Network (Evan Abrams, 2025-10-16), mid-season firings since 2000: before firing 27.9% SU / 37.3% ATS; after 33.3% SU / **49% ATS** (no edge). Defensive-minded interims 53.6% ATS rest of season, offensive-minded 45.3%. **No totals records given.** https://www.actionnetwork.com/nfl/how-nfl-teams-perform-after-firing-their-head-coach-midseason-titans-fire-brian-callahan
- Sportico (2023, 37 mid-season changes since 2000, via search snippet; page paywalled): win% 27% under old coach -> 39% under interim; ATS 19-18 in first game after the firing, 97-89-5 rest of season; author attributes the bump to regression to the mean. https://www.sportico.com/leagues/football/2023/nfl-coaches-fired-record-frank-reich-1234749569/
- NBC Sports Boston citing ESPN Research: since 2014, 15-5 ATS in the first game under an interim, but 2-7 in the three most recent seasons (small samples, cherry-picked window). https://www.nbcsportsboston.com/nfl/new-england-patriots/titans-interim-coach-bump-trend-tells-story/738788/?amp=1
- Play-caller changes: see section 2 (4for4: tendencies stop carrying over when the play-caller changes, except pace). No study of first-year play-callers vs totals found.

My nflverse check (coach names per game; "replacement coach" = team's coach differs from its week-1 coach that season):
- 2002-2025: games involving a mid-season replacement coach **242 games, 50.0% over**; first game under the replacement **38 games, 60.5% over** (z +1.3, not significant). 2015-2025: 115 games 48.2%; first game 18 games 66.7%.
- Games involving a first-year head coach of that team: 2002-2025 2,339 games 48.3% over; weeks 1-4 only 575 games 48.6%; 2015-2025 weeks 1-4 278 games 47.5%.
- **Assumption for us:** no evidence that coaching changes create a totals edge. The interim-first-game over (60% in 38 games) is a weak lead only.
- NFL coordinator (OC/DC) and play-caller data are not in nflverse; would need hand collection.

---------------------------------------------------------------------
## 5. Schedule and situation

- **Rest / bye weeks (best evidence, spreads not totals):** Lopez & Bliss (2024), *Frontiers in Behavioral Economics* (also arXiv 2408.10867), Bayesian state-space models on results and betting lines: before 2011 a bye was worth about **+2.2 points per game**; since the 2011 CBA (fewer full-pad practices) neither bye-week nor "mini-bye" rest shows a significant edge; the market prices it. Authors are NFL employees (disclosed). Grade A. https://www.frontiersin.org/articles/10.3389/frbhe.2024.1479832/full ; https://arxiv.org/abs/2408.10867v1 . Tainsky & Sung (2002-2009, U. Illinois): market efficient apart from a post-bye bias. https://news.illinois.edu/a-better-bet-wagering-on-teams-coming-off-a-bye-week/ . Totals: no study found. My nflverse check: one team off bye 294 games 49.3% over; both off bye 29 games 37.9% (too small).
- **Thursday games:** popular "TNF unders" claims are window-dependent (e.g. Action Network "16-5 under last 21", "TNF totals <= 40: 19-7 under since 2010"; another source: over 23-15 since 2012). https://www.actionnetwork.com/nfl/nfl-week-10-betting-trends-stats-notes-action-network-betting-primer-2 . My nflverse check 2015-2025: Thursday (excl. week 1) **186 games 50.3% over** in both halves. **No evidence of a Thursday under edge.**
- **Primetime (SNF/MNF):** Action Network: primetime unders 164-102-3 since 2019 (as of 2023); MNF unders 55-28-1 since 2019 (as of 2023). https://www.actionnetwork.com/nfl/panthers-vs-bears-primetime-unders-continue-to-crush-it ; FOX Sports 2023 https://amp.foxsports.com/stories/nfl/2023-nfl-odds-unders-continue-to-overperform-for-sportsbooks . My nflverse check 2015-2025: Mon 43.6% over (204), SNF 44.0% (195), all Thu/Mon/SNF 45.8% (596, z -2.0), similar in both halves. Small but persistent lean to the under in Sunday/Monday night games; mechanism unknown (public over-betting in nationally televised games is a plausible but untested explanation; Kelly & Chen show the public bets overs in general). Grade C+ (data-mined).
- **Time zones / body clock:** Smith, Guilleminault & Efron (1997, *Sleep*): MNF 1970-1994, West Coast teams beat the spread vs East Coast teams (p < 0.01). 2013 follow-up (Harvard/Stanford, *Sleep* abstract): night games (after 8pm ET) 1970-2011, West Coast teams covered **66%** by 5.26 points; no effect in day games. https://sleepreviewmag.com/sleep-health/parameters/quality/circadian-timing-may-give-edge-to-west-coast-nfl-teams-in-night-games/ ; https://sciencedaily.com/releases/2013/11/131127115357.htm . This is a SIDE (spread) effect, not totals; nothing found after 2011. Grade A but old.
- **International games:** RotoWire: 39 European games 2007-2023 averaged 21.7 points per team vs league 22.4; 2023 European games 16.6 vs 21.7. No totals-vs-line study. https://www.rotowire.com/article/nfl-across-borders-analyzing-the-impact-of-international-games-on-players-and-scores-76960 . My nflverse check: neutral-site regular-season games 2015-2025 49 games 46.9% over, mean -2.2 points vs line (too small).
- **Divisional games:** Shank (2019), "NFL betting market efficiency, divisional rivals, and profitable strategies", *Studies in Economics and Finance* 36(4):567-580, 2003-2016: divisional games have a lower chance of going over; combined model wins "nearly 57%" out of sample (abstract only; paywalled). https://www.doi.org/10.1108/SEF-11-2018-0354 . nflanalytic (1999-2025): divisional overs 48.3% vs 50.1%. My nflverse check 2015-2025: 48.7% (2015-19 47.2%, 2020-25 49.9%). **Effect, if real, has faded to below break-even.**
- **Late season / motivation / resting starters:** only touts; contradictory (unders when 10-pt favourite weeks 13-17 vs "blind overs" when starters rest). https://vsin.com/nfl/how-to-bet-nfl-games-when-teams-rest-starters/ ; https://www.4for4.com/2024/w18/nfl-spread-and-ou-picks-hunters-week-18-best-bets . My nflverse check: weeks 15+ 51.1% over (2020-25 52.7%). Not found as a reliable effect.

---------------------------------------------------------------------
## 6. Officials (NFL)

- Crew penalty rates genuinely differ (several penalties per game between strictest and most lenient crews in 2010; veteran-led crews drive between-crew inconsistency; ESPN reporting). https://www.nbcphiladelphia.com/news/sports/examining-nfl-referees-in-2010/1901263/ ; https://africa.espn.com/blog/nflnation/post/_/id/189064/how-roger-goodell-knows-nfl-officiating-is-inconsistent
- Lopez (2016, *Economic Inquiry*): pass interference and holding calls peak near the sidelines -> officials respond to pressure (bias evidence, not totals). https://works.bepress.com/michael-lopez/3
- **No study linking NFL crews to over/under results found.** My own nflverse test (section 10.1): referee over rates 2015-2025 are no more dispersed than chance (chi2 10.4, df 16), do not persist (season-to-season r = -0.09), and a "follow last season's referee trend" rule went 608-631 (49.1%). **Assumption: NFL referee totals trends are noise.**

---------------------------------------------------------------------
## 7. Market efficiency of NFL totals

### 7.1 Classic "bet the under on high totals" (Paul & Weinbach 2002) -- DEAD since ~2010
- **Paul & Weinbach (2002)**, "Market efficiency and a profitable betting rule: Evidence from totals on professional football", *Journal of Sports Economics* 3(3):256-263. Sample: NFL 1979-2000. Overall market could not be rejected as fair, but betting the under on totals 5-7+ points above the mean (posted total >= 47.5 in their era) won **58.7%**, profitable after -110 vig. Explanation offered: bettors prefer overs in expected shootouts, books shade totals up. Grade A (old). https://ideas.repec.org/a/sae/jospec/v3y2002i3p256-263.html
- **Kelly & Chen**, "Bettor biases and market efficiency in the NFL totals market", *Research in Business and Economics Journal* vol. 14 (AABRI). Sample: all regular-season games 2001-2018 (4,600 games, Covers.com closing totals). Grade A-/B+ (small journal but clear method). https://mobile.aabri.com/manuscripts/193138.pdf
  - Under on posted total >= 47.5, 2001-2009: **141-95, 59.7%**, one-sided binomial p = 0.014 vs 52.4% breakeven.
  - Same rule 2010-2018: under **218-245, 47.1%** (lost money). Only 2015, 2017, 2018 profitable.
  - Re-centred rule for the higher-scoring era (total >= 51.5, i.e. ~7 above mean) 2010-2018: under **82-87, 48.5%**, lost money.
  - Scoring context: mean points 42.7 (2001-09) -> 45.2 (2010-18); mean posted total 41.6 -> 44.8.
  - Betting-volume data (Sports Insights % of bets, 2004-2018, 3,840 games): public still bets overs; in 75% of games >= 56-57% of tickets were on the over; each +1 point of posted total adds ~0.78 pct-pts (2004-09) to ~1.01 pct-pts (2010-18) of tickets on the over. So the **public over bias persists, but books stopped shading for it** (authors attribute it to competition, higher limits, sharper bettors).
  - **Assumption for us:** "public loves overs" is real at the ticket level, but it is NOT a profitable rule at modern closing lines. Do not assume high-total unders win.
- Kochman & Goodwin (2006), "An under bias in the football betting market: fact or fiction?", *New York Economic Review* 37:32-36 (cited by Kelly & Chen; full text not read).

### 7.2 Line movement: open-to-close moves are ~half reversed, but not after costs (Moskowitz 2021)
- **Moskowitz, "Asset Pricing and Sports Betting"**, *Journal of Finance* 2021 (DOI 10.1111/jofi.13082). NFL 7,035 games 1985-2013 (also NBA 18,681 games 1999-2013, MLB, NHL); spread, moneyline and over/under contracts. Grade A. NBER draft: https://conference.nber.org/conf_papers/f92072/f92072.pdf ; summary https://bfi.uchicago.edu/insights/asset-pricing-and-sports-betting/
  - Regressing close-to-outcome return on open-to-close price move gives a coefficient of **about -0.50 for Spread and Over/Under** in both NBA and NFL: roughly half of the open-to-close move is reversed by the result ("overreaction", or half of line moves carry no information).
  - For O/U, momentum in recent point totals pushes the total from open to close, then fully reverses at the outcome; open-to-end cumulative return ~0 -> **opening totals are on average efficient; bettors push closing totals too far.**
  - BUT: "returns ... fail to overcome transactions costs"; the vig (~10% round trip in his setup, paid twice for open-to-close trades) "easily wipes out any profits". So: real but not exploitable at -110 in the 1985-2013 data.
  - Assumption for us: a big total move driven by recent scoring (not by news like injuries/weather) is more likely to be partly wrong. Only exploitable where fees are lower than sportsbook vig (e.g. a maker order on an exchange) -- untested.
- nflanalytic.com betting page (nflverse, 7,276 games 1999-2025): all games overs 48.7%, unders 49.8%, pushes 1.5%. In 15+ mph wind, overs hit **43.0% (684 games)** even though the market already set those totals 1.3 points lower. Divisional overs 48.3% vs 50.1% non-division. 2024: closing total avg 44.5, actual 46.0, overs 53.9%; 2025: total 44.9, actual 46.0, overs 51.9%. Grade B (descriptive, nflverse closing line). https://nflanalytic.com/betting.html
- Opening vs closing line accuracy: arXiv 1211.4000 ("The Performance of Betting Lines for Predicting the Outcome of NFL Games") found no statistically significant difference in predictive value between opening and closing lines (sides, not totals); ~10% of games moved 2+ points. https://arxiv.org/pdf/1211.4000 . TeamRankings has NFL O/U results by opening total and line movement for 4,820 games since 2008, but the pages returned HTTP 403 to my fetcher (not read). https://betiq.teamrankings.com/nfl/betting-trends/over-under-results-by-line-movement/
- Does the market lag league-wide scoring shifts? My nflverse check: per-season mean (total - line) in weeks 1-6 vs weeks 7+ for 2006-2025 has correlation **-0.03** (20 seasons). E.g. 2022 weeks 1-6 over 39.8% then 46.9%; 2023 39.1% then 49.4%; 2024 51.6% then 55.1%. An early-season under/over lean does NOT carry into the rest of the season. (TeamRankings: 2023 unders 61.3% through week 6, per FOX https://amp.foxsports.com/stories/nfl/2023-nfl-odds-unders-continue-to-overperform-for-sportsbooks .)
- Pfitzner, Lang & Rishel (2014), "Factors Affecting Scoring in NFL Games and Beating the Over/Under Line", *The Sport Journal*: one season (2010), box-score regression; all-games strategy 97-78-5 (55.4%); dome not significant. Too small to trust (Grade C). https://thesportjournal.org/article/factors-affecting-scoring-in-nfl-games-and-beating-the-overunder-line/

---------------------------------------------------------------------
## 8. Distribution of NFL total points, key numbers, and alt-total (ladder) pricing

### 8.1 Published key-number tables
- Covers (Shawn Wronka, updated 2025-08-25), share of games landing on each combined score, 2014-2023 vs 2004-2013: 37: 3.6% (3.8%), 40: 3.7% (3.5%), 41: 3.5% (3.9%), 43: 3.2% (3.7%), 44: 3.7% (4.1%), 47: 3.1% (3.2%), 48: 2.7% (3.3%), 51: 3.9% (3.6%); "dead" numbers 42: 1.7%, 38: 1.7%, 53: 1.3%, 56: 1.1%. Covers calls totals key numbers "key ranges". Grade B. https://www.covers.com/nfl/key-numbers
- Action Network: 2003-17 41 most common (3.9%), then 37 and 44 (3.8%); after the 2015 PAT move 44 fell from 4.1% (2003-14) to 2.9% for three seasons and 43 became most common (4.0%) (small window). https://www.actionnetwork.com/nfl/nfl-key-betting-numbers-over-unders-totals-line-value ; https://www.actionnetwork.com/nfl/nfl-over-under-betting-tips-key-numbers-betting-analysis
- Consensus across sources: each key total is only ~3-4% of games, so a half point on a total is worth far less than a half point through 3 or 7 on a spread. https://www.deucescracked.com/blog/nfl-key-numbers-buying-points-betting-strategy

### 8.2 My own descriptive check (nflverse games.csv, 2015-2025 incl. playoffs, n = 3,028 games with a closing total)
Method: nflverse `total` vs `total_line` (single closing-type line). Normal model = for each game, total ~ Normal(closing line, SD of (total - line)). Not a backtest; no vig; one line source. Scripts: research_notes/NFL and NBA totals factors/scripts/dist.py, tails.py; Kalshi snapshot: ladder.py (public read-only API).
- Mean points 45.67, SD of total 13.88. Mean (total - closing line) = **+0.39**, but **median = -0.5**; SD of (total - line) = **13.22** (2020-2025: mean +0.65, SD 13.06). Overs 1,465 / unders 1,535 / pushes 28 -> **48.8% over** (about 1.3 standard errors below 50%; not significant).
- **Shape: (total - line) is right-skewed (skew +0.32, excess kurtosis +0.23).** Implication: the mean sits above the line while the median sits slightly below it. Big overs happen more than a normal curve says; moderate overs and big unders happen less.
- Empirical P(total > line + d) vs Normal(0, 13.22):
  - d = +3.5: **37.2% vs 39.6%** (normal overprices "a bit over" by ~2.4 pts of probability; ~2.7 SE)
  - d = +7.5: 27.0% vs 28.5%
  - d = +14.5: 13.6% vs 13.6% (equal)
  - d = +20.5: **6.9% vs 6.0%**; d = +24.5: **4.4% vs 3.2%** (normal underprices big overs; ~3 SE)
  - d = -20.5 (i.e. P(under line - 20.5)): **4.9% vs 6.0%** (normal overprices blowout unders)
  - d = -3.5: 59.1% vs 60.4%.
- Per-integer landing frequency vs a smooth normal (2015-2025): spikes at 30 (1.45x), 33 (1.44x), 37 (1.43x), 40 (1.30x), 41 (1.28x), 43 (1.26x), 44 (1.31x), 47 (1.17x), 51 (1.48x), 57 (1.22x); holes at 42 (0.65x), 53 (0.54x), 56 (0.51x), 38 (0.73x), 31 (0.72x). 2020-2025 pattern the same (37 1.56x, 44 1.40x, 51 1.54x, 42 0.65x, 53 0.50x, 56 0.47x).
- By closing-line size (2015-2025): over rate <= 40: 51.7% (352 g); 40.5-44: 48.4%; 44.5-48: 48.5%; 48.5-51.5: 48.9%; >= 52: 47.5% (240 g). No bucket is beatable at 52.4%. SD of (total - line) rises slightly with the line (12.85 at 40.5-44 to 13.6-13.8 above 48.5).
- **Assumption for alt lines / Kalshi ladders:** any market that prices the strikes of a ladder off a symmetric normal around the main total will (a) overprice "over by 1-8" strikes by up to ~2-2.5 probability points and (b) underprice the far-over tail (+20 and beyond) by ~1 point and overprice the far-under tail by ~1 point; (c) strikes sitting on 42.5/52.5/53.5/55.5/56.5 cross "dead" numbers so moving the strike by 1 point there is worth less than near 37/40/41/43/44/47/51. Whether Kalshi or sportsbooks actually price this way is UNTESTED (needs archived ladder prices).

### 8.3 Kalshi NFL totals ladders (KXNFLTOTAL) -- what I could verify read-only (public API, 2026-10-23)

- Series KXNFLTOTAL ("Football totals"; contract terms https://assets.kalshi.com/contract_terms/FOOTBALLTOTALS.pdf ), settlement source = the league (nfl.com). **fee_type = `quadratic_with_maker_fees`, fee_multiplier 1** (public endpoint https://api.elections.kalshi.com/trade-api/v2/series/KXNFLTOTAL). Per our stored Kalshi notes: taker fee 0.07 x P x (1-P) per contract, maker 0.0175 x P x (1-P) (https://docs.kalshi.com/getting_started/fee_rounding). At P = 0.50 a taker pays 1.75c -> break-even ~51.75% (vs 52.38% at -110); at P = 0.20 the taker fee is 1.12c on a 20c contract = 5.6% of stake, so tail strikes are relatively expensive to take.
- Ladder shape: ~19 strikes per game, 1-point steps near the centre (e.g. 40.5 ... 47.5) and 3-point steps in the tails (e.g. 23.5, 26.5, ... 65.5). Liquidity is concentrated at the centre strike (thousands of dollars) and thin on many tail strikes (some zero volume; spreads up to 10-40c on illiquid games like a Thursday game 6 days out).
- **Snapshot comparison (6 games for 2026-10-25, ~2 days before kickoff, mid prices where spread <= 3c) vs the 2015-2025 empirical (skewed) distribution and vs a normal (SD 13.22):** Kalshi mids track the EMPIRICAL distribution closely -- typically within 1-2c at every strike, and closer than the normal model. Example PIT-NO (market median 44.6): strike 65.5 Kalshi 0.065 / empirical 0.074 / normal 0.062; strike 23.5 0.955 / 0.964 / 0.949. Example CIN-BAL (median 48.0): 71.5 0.060 / 0.052 / 0.041. In the two low-total games (CLE-TEN 37.0, MIA-NYJ 39.1) Kalshi's over-tail mids sat 1.5-2.5c BELOW the pooled empirical curve (e.g. MIA-NYJ 47.5: 0.245 vs 0.270) -- but the pooled residual curve ignores that low totals have a slightly smaller spread (SD 13.0 at <= 40), so this is not evidence of mispricing. **Reading: at a glance, Kalshi ladders already embed the real skew (probably derived from sportsbook alt lines); there is no obvious "normal-approximation" mispricing to harvest.** One snapshot, not a test.
- Kalshi-wide evidence: Bürgi, Deng & Whelan (2025), "Makers and Takers: The Economics of the Kalshi Prediction Market" (UCD WP2025_19 / CESifo / CEPR DP20631), 300,000+ contracts: clear favourite-longshot bias -- contracts bought under 10c lose over 60% of money; contracts above 50c earn a small positive return; makers out-earn takers; bias possibly shrinking over time. Grade A- (working paper, large sample). https://www.ucd.ie/economics/t4media/WP2025_19.pdf ; https://cepr.org/voxeu/columns/economics-kalshi-prediction-market . **Implication for ladders:** cheap far-tail YES strikes (e.g. "Over 65.5" at 6c) are the contracts most likely to be overpriced on Kalshi in general -- this pushes the opposite way from the right-skew finding (which says far-over tails are slightly more likely than a normal says). Net effect for NFL totals ladders is an empirical question.
- Kalshi vs sportsbooks on price: RotoWire/Citizens (2026-10-05) measured straight-bet hold Kalshi **4.28%** vs FanDuel 4.41% vs DraftKings 4.47% (Week 4, 28 data points; not totals-specific). https://www.rotowire.com/article/kalshi-tops-sportsbooks-nfl-week-4-pricing-134248
- Derivative markets (team totals, 1H totals): **no peer-reviewed study of mispricing found.** Only anecdotes (e.g. Covers 2020: first-half overs 40-21-2 through week 4 of one season). https://www.covers.com/nfl/nfl-first-half-totals-overs-best-bet

---------------------------------------------------------------------
## 9. NBA game totals (non-box-score factors)

### 9.1 NBA totals market efficiency
- **Moskowitz (2021, J. Finance)**, NBA 18,681 games 1999-2013: O/U open-to-close moves are ~50% reversed by the result (coefficient ~ -0.50), momentum in recent points pushes totals too far, but not profitable after vig. https://conference.nber.org/conf_papers/f92072/f92072.pdf
- **Early-season bias (the one repeated NBA totals finding):**
  - Baryla, Borghesi, Dare & Dennis (2007), "Learning, Price Formation and the Early Season Bias in the NBA", *Finance Research Letters* 4(3):155-164: totals lines (not sides) are significantly biased early each season; open-to-close movement goes the right way but does not remove it; strategy wins **56.72%** vs closing totals. Direction and sample not in abstract. Grade A, old. https://dc.etsu.edu/etsu-works/17780
  - Girdner, Davis, Fodor & Kirch (2013), "Early season NBA over/under bias", *Journal of Prediction Markets* 7(2):1-9, seasons 2009-2012: market efficient overall; **week 1 games went under 58.2%**, +11.1% average return betting every week-1 under. Grade A-, small (one week x 4 seasons). https://ircommons.uwf.edu/esploro/outputs/journalArticle/Early-season-NBA-overunder-bias/99380090347306600
  - Persistence 2015+: not found in literature. Recent seasons have opened with scoring ABOVE expectations in some years (rule/pace changes), so direction may flip; must re-test.
- Long-sample efficiency: The Sport Journal "NBA gambling inefficiencies: a second look" -- 2000-01 to 2007-08, 10,000+ games, betting the over is a fair bet; efficient market. https://thesportjournal.org/article/nba-gambling-inefficiencies-a-second-look/
- Recent (2018+): no peer-reviewed study found. Action Network month-by-month notes swing (e.g. Jan 2024 unders 131-97-2, 57.5%, after an over-heavy November) -- noise-level. https://www.actionnetwork.com/nba/what-we-learned-nba-over-unders-all-star-break
- Woodland & Woodland (2015), NBA season-WIN totals (not game totals): profitable on both sides, bettors overweight prior season. *Int. J. Sport Finance* 10(2):160-174. https://ideas.repec.org/a/jsf/intjsf/v10y2015i2p160-174.html

### 9.2 NBA referees and totals
- Academic work is about BIAS in foul calls, not totals: Price & Wolfers (QJE 2010), 0.18-0.20 more fouls per 48 min for players officiated by more opposite-race referees, using game fixed effects (which absorb crew-level pace). http://users.nber.org/~jwolfers/Papers/NBARace(QJE).pdf ; Deutscher (2015) finds no bias using league assessment data https://journals.sagepub.com/doi/10.3233/JSA-150012 ; Gift & Rodenberg "Napoleon complex": shorter crews call 0.147 more personal fouls per game, affecting free throws but NOT total points (WBUR summary). https://www.wbur.org/onlyagame/2014/11/08/paul-gift-nba-referees-foul
- Betting-site claims (e.g. "a crew 2-3 fouls above average moves the total 3-5 points") have **no data or method** (checked donaghyeffect.com explainer: zero sourced numbers). https://www.donaghyeffect.com/nba/referees/explained
- Donaghy scandal: analyses of line moves in his games are journalism (R.J. Bell, Las Vegas Sun 2008), spreads-focused; Donaghy said he did not bet totals. https://m.lasvegassun.com/news/2008/oct/06/analyst-cites-damning-statistics-against-ref/
- Timing fact: NBA posts referee assignments at about **9:00 a.m. ET each game day** (official.nba.com). So any crew effect would have to survive a full day of market pricing. https://official.nba.com/referee-assignments/
- **Assumption:** NBA crews differ in foul rates (real), but a crew -> total -> over/under edge is unproven; given the NFL null result (section 10.1) and the Gift & Rodenberg "fouls change FTs but not total points" finding, default to "priced or noise" until tested. Free data: officials per game are in NBA stats box-score summaries (nba_api BoxScoreSummaryV2 "Officials" table); closing totals need a separate source.

### 9.3 NBA mid-season coaching changes
- Martínez & Caudill (2013, *Journal of Sports Economics*) and Martínez (2017, *Revista Internacional de Ciencias del Deporte*), full NBA history: only **~12-15% of mid-season replacement coaches significantly outperform their predecessors**; improvement mostly for very bad teams (regression to the mean); earlier changes and ex-player hires do slightly better. Outcome = win%, not pace or scoring. https://portalinvestigacion.upct.es/documentos/6537dcffa97f764c5a12afbf?lang=en ; https://doaj.org/article/df3d048f79ad435fad89ede7b470e358
- Effect of a coaching change on PACE or totals: **not found** in any source. Testable with free data (team pace by game from NBA stats; coach changes from Basketball-Reference).

---------------------------------------------------------------------
## 10. My descriptive checks on free nflverse data (2015-2025) -- persistence test of published "angles"

Data: nflverse games.csv (downloaded 2026-10-23), `total` vs `total_line` (one closing-type line per game). Over% excludes pushes; z = (over% - 50%) / SE. "Beatable" needs the betting side to win >= 52.4% at -110 (i.e. over% <= 47.6% for an under bet, >= 52.4% for an over bet). **I ran ~25 splits, so expect one or two |z| ~ 2 by chance alone.** These are hindsight descriptive numbers, not a backtest. Scripts (run as `python -I <script> games.csv`): research_notes/NFL and NBA totals factors/scripts/ (sit.py, refs.py, qb.py, coach.py, season.py, wind2.py). games.csv = https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv

| Split | 2015-2025 n / over% / mean(total-line) | 2015-2019 over% (n) | 2020-2025 over% (n) |
|---|---|---|---|
| All games | 3,028 / 48.8% / +0.39 | 48.8% (1,335) | 48.8% (1,693) |
| Outdoor observed wind 0-9 mph | 1,313 / 52.5% / +1.00 | 53.2% (661) | 51.8% (652) |
| **Outdoor observed wind 10-14 mph** | **454 / 40.6% (z -4.0) / -1.57** | **40.4% (224)** | **40.8% (230)** |
| Outdoor observed wind 15-19 mph | 179 / 41.8% / -2.64 | 41.4% (87) | 42.2% (92) |
| Outdoor observed wind 20+ mph | 49 / 50.0% / +0.62 | 47.4% (20) | 51.7% (29) |
| Temp <= 32F | 142 / 47.1% / -0.84 | 37.7% (61) | 54.4% (81) |
| Dome / closed roof | 855 / 50.5% / +1.32 | 48.4% | 51.8% |
| Divisional (reg season) | 1,056 / 48.7% | 47.2% | 49.9% |
| Thursday (reg, excl. week 1) | 186 / 50.3% / +1.08 | 50.6% | 50.0% |
| Monday night (reg) | 204 / 43.6% / -1.34 | 43.0% | 44.0% |
| Sunday night (reg, kickoff >= 7pm ET) | 195 / 44.0% / -1.20 | 48.3% | 40.4% |
| **All primetime Thu/Mon/SNF (reg)** | **596 / 45.8% (z -2.0) / -0.59** | 47.1% (261) | 44.8% (335) |
| Sunday daytime (reg) | 2,213 / 49.5% | 49.1% | 49.9% |
| Neutral site (mostly international) | 49 / 46.9% / -2.19 | 45.0% | 48.3% |
| Both teams off a bye | 29 / 37.9% | 45.5% (11) | 33.3% (18) |
| Weeks 1-4 | 698 / 48.9% | 51.0% | 47.2% |
| Weeks 15+ | 607 / 51.1% | 48.5% | 52.7% |
| Closing total <= 40 | 352 / 51.7% | 52.5% | 51.2% |
| Closing total >= 50 | 434 / 48.1% | 47.8% | 48.4% |
| Playoffs | 133 / 48.1% | 47.3% | 48.7% |

Readings:
- **Moderate observed wind (10-19 mph) is the only split that is large, consistent in both halves, and well past break-even (under ~58-59%).** 20+ mph is ~50%: the market fully prices obvious extreme-wind forecasts. Matches the "moderate wind is under-adjusted" idea (https://www.startuphub.ai/news/nfl-betting-does-moderate-wind-create-unders-market-inefficiencies, unsourced) and the Jeff's Edge 10-14 mph ~55% figure. **But it is OBSERVED wind -> partly hindsight. Must be re-tested with archived pre-game forecasts.** Also 2022-2023 wind data are ~45%/20% missing in nflverse.
- **Robustness of the moderate-wind split (observed wind 10-19 mph, 2015-2025):** under% by season 65, 42, 64, 61, 62, 55, 63, 68, 67, **48 (2024, 30-28 O-U), 53 (2025, 22-25)**. So 9 of 11 seasons were >= 53% under, but **the two most recent seasons combined are ~50% (52 overs, 53 unders)** -- possible decay (the angle is widely published) or noise; cannot tell yet. Not driven by one stadium: excluding the three most frequent windy home teams (CLE, MIA, PHI) still 207-292 (58.5% under); PHI home windy games were an outlier at 7-34. By closing total: <= 41: 53.3% under; 41.5-45: 62.6%; >= 45.5: 58.5%.
- Primetime unders (~54% under) are consistent in direction in both halves but only z ~ -2 after many splits; Action Network touts (164-102-3 primetime unders since 2019, https://www.actionnetwork.com/nfl/panthers-vs-bears-primetime-unders-continue-to-crush-it) are the same phenomenon. Plausible mechanism not established. Treat as a hypothesis, not a fact.
- Thursday games: no under edge in 2015-2025 (50%), contrary to the popular "TNF = unders" story.
- Cold: flips sign between halves (37.7% then 54.4%): noise.
- Divisional: 48.7% over; tiny (Shank 2019 claims a divisional under effect 2003-2016, see section 5).

### 10.1 Referees (NFL) -- descriptive test
- 17 referees with >= 80 games 2015-2025. Over rates range 43.0% (Shawn Hochuli, n=128) to 58.1% (Jerome Boger, n=124), BUT a chi-square test of "all referees share the same over rate" gives **chi2 = 10.4 on 16 df** -- *less* spread than pure chance would produce.
- Persistence: odd-season vs even-season over% correlation across referees = **-0.22**; season-to-season (156 referee-season pairs, >= 12 games each) correlation of mean (total - line) = **-0.11**, of over% = -0.09.
- Rule "bet over if the referee's prior-season over% >= 60%, under if <= 40%": **608-631 (49.1%)**. Loses after vig.
- **Conclusion: NFL referee over/under "trends" in public referee tables are noise as far as the closing total is concerned.** (Penalty-rate differences between crews are real -- see section 6 -- but they do not show up as a predictable over/under edge.)

---------------------------------------------------------------------
## 11. Testable hypotheses (pre-registration candidates)

Rules for all: decide the rule BEFORE looking at test data; use the CLOSING total (or the Kalshi price at a fixed time before kickoff) as the benchmark; break-even at -110 is 52.38%, on Kalshi as a taker at 50c ~51.75% (fee 0.07*P*(1-P)); train 2015-2022 / test 2023-2025 / forward-test 2026+. "Prior" = what the literature/my descriptive checks suggest. Ordered by strength of evidence.

| # | Factor and rule | Side | Prior / expected size | Data needed | Free? |
|---|---|---|---|---|---|
| H1 | **Forecast moderate wind:** outdoor/open-roof NFL game where the forecast made ~24-36 h before kickoff has sustained wind 10-19 mph at kickoff hour | Under the closing total (or the Kalshi strike nearest the market median) | Observed-wind version: ~58-60% under 2015-2023, ~50% in 2024-25. If half the effect is hindsight, expect ~54-55%. Pass bar >= 54% on >= 250 test games. Forecast archives limit the sample to 2021+ (GFS day-ahead) or 2018+ (HRRR short-lead) | nflverse games.csv (total_line, roof, stadium) + stadium lat/lon + Open-Meteo Previous Runs API (GFS day-1/day-2 from Mar 2021) or Historical Forecast API (HRRR short-lead from 2018) | Yes |
| H2 | Falsification of H1: split windy games by (observed - forecast) wind | -- | If the under edge exists ONLY where observed wind exceeded the forecast, H1 is dead (edge was hindsight) | Same as H1 | Yes |
| H3 | Negative control: forecast wind >= 20 mph | Under | Expect ~50% (market fully prices it; observed 20+ mph was 50% in 49 games) | Same | Yes |
| H4 | **Primetime unders:** Sunday-night and Monday-night regular-season games | Under closing total | 2015-2025: ~56% under (399 games), both halves; Action Network 164-102-3 since 2019. Expect 53-55%; many-splits risk | nflverse weekday/gametime/total_line | Yes |
| H5 | Thursday-night control | Under | Expect 50% (observed 50.3%, 186 games). Confirms TNF-under folklore is dead | nflverse | Yes |
| H6 | **Kalshi ladder calibration by strike distance:** group KXNFLTOTAL strikes by (strike - ladder median) in 3-pt bins; compare mid/ask at T-2h to realized frequency | Buy NO (or YES) in any bin where price - realized >= taker fee + 2c | Right skew says normal-model ladders overprice "over by 1-8" by ~2 pts and underprice +20 tails by ~1 pt; snapshot suggests Kalshi already prices the skew -> expect no bin clears fees | Kalshi public market/candlestick history for KXNFLTOTAL + nflverse finals | Yes (Kalshi read-only API) |
| H7 | **Kalshi longshot tail:** YES strikes priced <= 10c on NFL total ladders | Sell YES / buy NO as maker | Bürgi et al.: Kalshi sub-10c contracts lose >60% on average; but NFL right skew makes far-over tails slightly fatter than normal. Expect small positive maker return if any; fees 0.0175*P*(1-P) as maker | Kalshi KXNFLTOTAL trade history + settlement | Yes |
| H8 | Key-number spacing on Kalshi 1-point strikes: price gap between adjacent strikes vs empirical P(total = k) given the median (e.g. gap 43.5->44.5 should be ~3.7%, 41.5->42.5 ~1.8%) | Trade the mispriced strike pair | Expect deviations <= 1c (not tradable). Tests whether Kalshi uses a smooth curve | Kalshi ladders + nflverse finals (2015-2025 landing frequencies) | Yes |
| H9 | **Line-move reversal (Moskowitz):** total moves >= 2 pts open->close with no QB change and no wind forecast change | Fade the move at the close | Moskowitz: ~50% of the move reverses, but not past sportsbook vig. Only possibly viable as a Kalshi maker. Expect ~52-53% | Opening + closing totals (Kalshi price history from listing to kickoff as proxy); nflverse has no opening line | Partly (Kalshi only, recent seasons) |
| H10 | High-total under (Paul & Weinbach): closing total >= mean + 7 | Under | Dead since 2010 (47.1%, 48.5% in Kelly & Chen); my 2015-2025 check: lines >= 50 -> 51.9% under. Control only | nflverse | Yes |
| H11 | Interim-coach first game | Over | 23-15 (60.5%) 2002-2025, ~2 games per season -> cannot reach significance; low priority | nflverse coach names | Yes |
| H12 | QB change (team starts a different QB than its previous game) | Under | 2015-2025 47.4% over (515 g), 2020-25 49.8% -> expect no edge; control | nflverse qb ids | Yes |
| H13 | NFL referee trend | Over/under by referee's prior over% | Already tested here: 608-631 (49.1%); chi2 shows no referee effect. REJECTED -- do not use | nflverse referee | Yes |
| H14 | Cold (<= 32F) over | Over | Mixed: SI 2003-15 55.7% over; nflverse 2015-19 37.7%, 2020-25 54.4%. Expect null | nflverse temp | Yes |
| H15 | Divisional under (Shank 2019) | Under | 2015-2025 51.3% under -> below break-even; expect fail | nflverse div_game | Yes |
| H16 | **NBA opening-week under** (first 7 days of regular season) | Under closing total | Girdner et al.: 58.2% under 2009-2012; Baryla et al. early-season totals bias 56.7%. Untested after 2012; direction may have flipped in high-scoring seasons. Expect 52-55% | NBA closing totals by game (Kalshi KXNBATOTAL history for 2025-26+, or an odds archive) + final scores | Partly |
| H17 | NBA referee crew foul-rate | Over when crew's prior-season FTA/foul rate is in top tercile, under in bottom tercile | Price-Wolfers/Gift-Rodenberg: crews differ in fouls, but Gift & Rodenberg found no effect on total points; NFL analogue was null. Expect null. Assignments post ~9:00 ET, so compare price at 9:30 ET vs close | nba_api BoxScoreSummaryV2 Officials + team box scores + closing totals | Officials/box scores free; closing totals partly |
| H18 | NBA mid-season coaching change -> pace | Total vs line in the 10 games after a coach change, in the direction of the new coach's pace history | No published effect on pace or scoring found; NBA replacement coaches beat predecessors only ~12-15% of the time (win%). Expect null | nba_api team game logs (pace), Basketball-Reference coach changes, closing totals | Mostly |

What NOT to assume (already priced or noise per the evidence): high totals go under; TNF unders; referee over/under trends; dome overs; cold-weather unders; backup-QB totals angles; bye-week/rest effects (priced since 2011); early-season NFL scoring trends carrying into the rest of the season.
