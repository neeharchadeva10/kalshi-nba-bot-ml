# Kalshi daily temperature markets: settlement mechanics, data pipeline, trader edges

## 1. How ASOS measures/reports temperature; rounding artifacts; CLI vs METAR

### Takeaway
ASOS stores whole-degF values internally (rolling 5-min average); the CLI daily high comes from that internal record, while public 5-min METARs (whole degC) and hourly METARs (tenths degC) are lossy views of it. The CLI high can legitimately exceed every published METAR, but no source found quantifies how often.

### Cited Findings
- Sensor reads ~every 10 s in tenths degF; stored as whole degF each minute plus a rolling 5-minute average in whole degF. Daily highs/lows are based on the rolling 5-min average, not raw 1-min readings (example: raw 82F at 12:41 PM averaged to 80F; if 80F was the day's peak, official high is 80F). — [NWS Chicago FAQ](https://www.weather.gov/lot/weather_observations_faq)
- 5-minute METARs round to whole degC then convert (80F -> 26.7C -> 27C -> 80.6F, shown by websites as 81F). Hourly/special METARs carry tenths degC (T-group), which preserves the original degF on conversion. — [NWS Chicago FAQ](https://www.weather.gov/lot/weather_observations_faq)
- NWS LA: the "1 degree or 2" gaps between 5-min reports and internal values come from rounding to whole C before converting to F; highs/lows can occur between 5-min observations and are tracked internally but not published; 1-minute data is not public. Full hourly obs report tenths C (LAX at :53 past the hour). Station reports previous day's high/low around 1 am; those feed the CLI. — [NWS LOX](https://www.weather.gov/lox/asostemperature)
- Iowa Environmental Mesonet: stations store whole degF; 78F sent as 26C converts back to 78.8F (->79F). Hourly METARs with T-group convert reliably to whole F; 5-minute MADIS data is whole C only and cannot be reliably converted, so IEM stores it as missing. — [IEM METAR dataset notes](https://mesonet.agron.iastate.edu/info/datasets/metar.html)
- 6-hour max/min groups are in the 00Z, 06Z, 12Z, 18Z hourly METARs (max/min of prior 6 hours). — [NWS Chicago FAQ](https://www.weather.gov/lot/weather_observations_faq)
- Reasons METAR-derived values differ from CLI: 5-min rounding (often 1-2F), highs/lows between reports, standard-time climate day, 0600Z cutoff quirks, rare ASOS malfunctions corrected before CLI. NWS says almost all perceived discrepancies are these quirks, and the CLI is the reference. — [NWS Chicago FAQ](https://www.weather.gov/lot/weather_observations_faq)
- CLI issued ~4:30 PM local (intermediate) and ~12:30 AM CST / 1:30 AM CDT (full day); preliminary until NCEI QC 3+ months later. — [NWS Chicago FAQ](https://www.weather.gov/lot/weather_observations_faq)
- Kalshi delay rule: determination may be delayed if a reported high is inconsistent with 6-hr or 24-hr METAR highs, or if the final NWS high is lower than the preliminary. — [Kalshi Help: Weather Markets](https://help.kalshi.com/markets/popular-markets/weather-markets)
- A third-party skill (unverified) says raw METAR is not the settlement value, recommends fitting per-city seasonal bias (CLI extreme minus ASOS extreme), and claims IEM daily data matches CLI 100%. — [claude-trading-skills kalshi-weather-markets](https://claudeskills.info/skills/agiprolabs/claude-trading-skills/kalshi-weather-markets/)

### Inferences
- The hourly METAR T-group max is a floor, not the answer; CLI high >= highest hourly METAR (converted) is expected, with a gap of 0-2F possible when the peak falls between :53 reports. Free 5-min whole-C data cannot pin down the integer F, so a tracking bot has +/-1F ambiguity; 6-hr max groups (tenths C) and IEM T-group data are the tighter free proxies.
- Whole-C conversions land on a ~1.8F grid, so some integer F values never appear from 5-min data (arithmetic inference from the sources above).

### Gaps
- No source quantified how often CLI max exceeds the highest hourly METAR. Needs own analysis (IEM archives of METAR vs CLI). The Interactive Brokers article "Nuances in the calculations of daily temperature market values" returned HTTP 403, so its worked example is seen only via search snippet.

## 2. Kalshi's switch to The Weather Company (TWC)

### Takeaway
Credible sources date the daily high/low switch to mid/late August 2026 (Aug 14 or Aug 27 reported), and Kalshi's own help pages (July 2026) still said NWS CLI for daily. No documentation found of how TWC derives the value or of any NWS-vs-TWC discrepancies or disputes.

### Cited Findings
- Kalshi-TWC partnership announced ~28 Aug 2026: TWC "provides the authoritative observation data used to settle these markets"; Kalshi retains contract design and resolution; TWC will show Kalshi probabilities in its app/site. Kalshi says climate/weather volume up 500% YoY, ~$1.1B annualized. — [Artemis](https://artemis.bm/news/prediction-market-kalshi-partners-with-the-weather-company-for-weather-hedge-settlements)
- "As of August 27, 2026" daily high/low series settle on TWC (weather.com/kalshi) not NWS; stations still named by CLI IDs (CLINYC, CLIMDW, CLILAX). KXHIGHNY rulebook PDF still names NWS as Source Agency while live market rules say "according to The Weather Company"; "Final" on weather.com/kalshi "is provided for convenience only"; rulebooks reserve the right to change source after trading begins. Precipitation/snow still NWS. — [TurbineFi](https://www.turbinefi.com/blog/how-to-trade-kalshi-weather-markets-2026)
- Another guide summary (via search result, unverified) says Aug 14; conflicts with Aug 27. — WebSearch summary
- Kalshi help (dated July 22, 2026, pre-switch): daily = NWS CLI; hourly temperature markets = TWC, "The Weather Company's reported value is official and final"; "Preliminary Weather Company readings may differ from the final reported value due to rounding and conversion." Hourly markets settle 25-35 min after close. — [Kalshi Help: Weather Markets](https://help.kalshi.com/en/articles/13823837-weather-markets)
- Action Network (Aug 2026): Kalshi, Polymarket and TWC reject claims of improper practice; no specific settlement disputes reported. — [Action Network](https://www.actionnetwork.com/news/inside-the-boom-weather-prediction-markets)
- Pre-switch KXHIGHNY rules (Mar 2026): highest temperature in Central Park "as reported by the National Weather Service's Climatological Report (Daily)". — [Solflare mirror of Kalshi rules](https://www.solflare.com/prediction/climate-and-weather/event/KXHIGHNY-26MAR11)

### Inferences
- The "rounding and conversion" warning originated on TWC hourly markets and was apparently carried to daily rules. Pre-switch NWS-CLI/IEM history may no longer reproduce settlement; TWC "Final" is not guaranteed error-free.

### Gaps
- Why Kalshi switched, TWC's derivation method (feed, rounding, climate-day window, standard vs daylight time), and any trader-reported mismatches: not found. No Reddit/Discord threads surfaced. Check weather.com/kalshi and live KXHIGH rules directly.
- Task brief says daily markets already moved to TWC; Kalshi help pages in July said NWS (consistent with an August switch).

## 3. Observation timing and automation

### Takeaway
Only generic claims found; no sourced data on when markets reprice after new observations or on bot latency in temperature markets.

### Cited Findings
- Climate day is local standard time year-round; in DST it runs 1:00 AM to 12:59 AM next day local daylight time, so readings 12-1 AM DST belong to the prior day. — [Kalshi Help](https://help.kalshi.com/markets/popular-markets/weather-markets); [NWS Chicago FAQ](https://www.weather.gov/lot/weather_observations_faq)
- Blog opinion: same-day trading once temperatures track toward a bracket is "closer to arbitrage than forecasting"; 06z/12z model runs can shift a projected high a full bracket; biggest mispricings claimed 3-5 days out. — [PillarLab](https://pillarlabai.com/blog/how-to-trade-temperature-markets-kalshi/)
- Anecdote: a bot polling NWS every 15-60 min lost 0-32 (Feb 2026); author claims arbitrage bots reprice within seconds of model cycles. — [MAXIMUS postmortem](https://www.northlakelabs.com/max/blog/kalshi-weather-postmortem-and-pivot/)
- Third-party skill nowcast: clamp mu toward observations with ~3.0F/hr drift, shrink sigma with sqrt(hours remaining), floor ~0.5F. — [claude-trading-skills](https://claudeskills.info/skills/agiprolabs/claude-trading-skills/kalshi-weather-markets/)

### Gaps
- Typical time of daily high by city/season (usually mid-late afternoon; no source found, compute from IEM). No measured repricing latency.

## 4. Documented trader edges, models, liquidity, fees, incentives

### Takeaway
No credible, verified after-fee profit writeup found. Evidence is mostly negative or marketing; liquid NYC markets reportedly track the public NWS forecast. Plausible niches: maker-side fading of longshot brackets, station/settlement mismatch, late-day near-certain situations.

### Cited Findings
- Failed trader: 0 wins/32 losses, "a few hundred dollars" lost; Gaussian errors underestimated tails (2-sigma ~10-12% vs 5%); flat fees kill cheap contracts (1c fee on 5c contract = 20%; avoid below ~15c). Anecdote, Feb 2026. — [MAXIMUS](https://www.northlakelabs.com/max/blog/kalshi-weather-postmortem-and-pivot/)
- GFS-ensemble bot repo trades only when edge >8% (Open-Meteo 31-member); no verified results. A "$1,800 peak profit" claim appeared only in a search summary, unverified. — [suislanchez repo](https://github.com/suislanchez/polymarket-kalshi-weather-bot)
- Skill page: out-of-sample Brier 0.07-0.14 vs climatology 0.25 but "forecast skill is not trading edge"; practical edge described as fading mispriced longshot brackets on the maker side. Pitfalls: treating 2F inclusive brackets as 1F half-open gave +1640% phantom edge; phantom 1c asks overstated PnL ~23x; UTC vs LST day aggregation cost ~14pp accuracy. — [claude-trading-skills](https://claudeskills.info/skills/agiprolabs/claude-trading-skills/kalshi-weather-markets/)
- Bracket structure: B = 2F inclusive window (B74.5 = 74-75F); T tails ("greater" YES if >= strike+1, "less" YES if <= strike-1); sum of YES prices >1.0 is normal overround; join on ticker date, not close_time. — same
- BotForKalshi: on liquid NYC high markets prices usually reflect the public NWS forecast so easy mispricings are gone; weather is thinner than crypto/sports; daily-high major-city markets are most liquid. Vendor, no measured edge. — [BotForKalshi](https://www.botforkalshi.com/blog/can-you-bet-on-the-weather)
- TurbineFi backtest (May 2026, LaGuardia feed, pre-switch, vendor): "Hot Forecast Breakout" 22 of 50 variants profitable, best +117.75%; contrarian families 0/50. NBM beat GFS MOS in a 2018 Colorado study; 30-day bias correction worth ~0.7F. Daily lows thinner than highs. Self-described "directional, not production-grade". — [TurbineFi](https://www.turbinefi.com/blog/how-to-trade-kalshi-weather-markets-2026)
- Vendor win-rate claims (weatheredge 67% at $25/mo; PillarLab 68.5%, graded calls shown were sports) are unverified marketing. — [Weather Edge](https://weatheredge.it.com/); [PillarLab](https://pillarlabai.com/blog/how-to-trade-temperature-markets-kalshi/)
- Fees: general taker formula 0.07 x C x P x (1-P) rounded up (max ~1.75c at 50c); maker 0.0175 where maker fees apply; no source confirmed the weather-series rate. — [Rotowire fee guide](https://www.rotowire.com/prediction-markets/trading-fees). Your memory notes say makers are free on BTC series; weather unverified.
- Kalshi Liquidity Provider Program lists "Daily High/Low for All Listed Cities" as an incentivized series (one provider); no amounts or requirements shown, page shows an invalid "6/31/26" date. — [Kalshi LP program](https://help.kalshi.com/en/articles/15410219-liquidity-provider-program)

### Inferences
- A free-data trader likely cannot beat model consensus on 1-2 day forecasts; the more defensible tests are same-day late-afternoon pricing vs observed running max, and maker longshot fading, each with exact fees and real fills.

### Gaps
- No verified after-fee P&L, no spread/depth numbers, no weather-specific fee confirmation, no Reddit/Discord data found.

## 5. Climate day, stations, gotchas

### Takeaway
Stations are fixed (NYC = Central Park), the day is standard-time, and bracket/threshold semantics are error-prone.

### Cited Findings
- Stations: NYC Central Park (CLINYC), not LGA/JFK; Chicago Midway, not O'Hare; LA LAX; Austin Bergstrom; Houston Hobby; Denver DEN; Philly PHL. — [TurbineFi](https://www.turbinefi.com/blog/how-to-trade-kalshi-weather-markets-2026); [vweatherstation](https://vweatherstation.com/prediction-markets/)
- Polymarket differs (Weather Underground / airport METAR, local clock, NYC = LaGuardia), so cross-venue comparison misleads. — [claude-trading-skills](https://claudeskills.info/skills/agiprolabs/claude-trading-skills/kalshi-weather-markets/)
- Contracts resolve on the listed station only; AccuWeather/Google are not authoritative. — [Kalshi Help](https://help.kalshi.com/markets/popular-markets/weather-markets)

### Gaps
- Unverified whether TWC's Central Park value uses the same sensor and day window as the CLI.
