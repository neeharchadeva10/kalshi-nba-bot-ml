# Pre-registration — research log #44: do Kalshi NBA game totals react late to official injury reports?

Written by Opus on 2026-10-22 at about 05:25Z (01:25 ET), before any Kalshi price was compared with any injury report
time. The idea and the choice to test it first are the user's ("Injury-news speed"). Changes go in "Amendments"
with a date and are never made silently.

## Question

The NBA publishes official injury reports as PDFs at fixed times on game days. When a report first shows an
important player newly **Out** (or newly back), Kalshi's game-total line (KXNBATOTAL) should move.

- **If the line has already moved before the report,** the news was out earlier (social media, beat reporters).
  Free official data cannot be first, so the idea dies.
- **If the line moves only minutes after the report,** someone reading the report quickly could buy the stale price.

**Who would pay:** resting orders on Kalshi left at pre-news prices.

## Known before writing

- **#31 and #32:** injury-report features added almost nothing to pregame NBA *winner* models at T-1h. The market
  had already priced injuries by then. That says nothing about how quickly it priced them.
- **#42** used the 2025-26 unseen half (2026-01-23 on) for game-total *formulas* at T-1h, so settlement outcomes of
  those games have been seen. The primary measure here is price-based (an exit 30 minutes later), not
  outcome-based, so that exposure matters little. It is still disclosed.
- **Report times:**
  - Report times come from the PDF /CreationDate; the file-name stamp is not the publish time.
  - Hourly names exist for every season. 15-minute names exist from about 2025-12-22 (README_V3; README_V2 says
    12-28; the builder checks).
- **No Kalshi minute-level price has been looked at** next to any report.

## Data (official, free, GET only)

1. **Official NBA injury report PDFs**, 2025-26 season. Source: ak-static.cms.nba.com/referee/injury/, by the
   file-name pattern only, HTTP GET, 0.25 s between requests.
   - Cached files are reused.
   - Missing slots are probed on each game day from 11:00 AM ET to the last tip: hourly before the 15-minute era,
     every 15 minutes in it.
   - The publish time P is the PDF /CreationDate, in UTC.
   - The existing parser (reports/output/nba_model/injury_parse_v3.py) gives player, team, matchup and status.
2. **Kalshi 1-minute candles** for every KXNBATOTAL market, from T-6 h to tip, 2025-26. Source: the public
   candlestick endpoint (event-level if available), with yes_bid / yes_ask close per minute.
   - Raw responses are cached; this is the #37 downloader pattern with period_interval = 1.
   - Tip time T = KXNBAGAME occurrence - 3 h (the #37 rule), with hoopR's tip time used if the game was moved.
3. **Player importance:** hoopR player box scores 2025-26 (data/raw/nba_open/player_box_2026.parquet).
   - A player is "material" if his average minutes over the team's games strictly before that date are >= 24, with
     at least 5 games played.

**Step 0, feasibility gate:** if 1-minute candles are not available for settled 2025-26 KXNBATOTAL markets, stop and
report. There is no fallback to hourly candles, which cannot measure speed.

## Definitions

- **Status change.** For each game, compare each report with the previous report that lists that game.
  - **DOWN event:** a material player is newly Out. Either his previous status was not Out (Available, Probable,
    Questionable, Doubtful or not listed), or he is newly listed as Out.
  - **UP event:** a material player goes from Out or Doubtful to Available or Probable, or is removed from a report
    that still lists his game.
  - Questionable changes are ignored.
- **One event per (game, report).** If the same report has both DOWN and UP players, the direction comes from the
  sign of (sum of UP players' minutes - sum of DOWN players' minutes). A tie is skipped.
- **Only events with P between tip - 6 h and tip - 10 min** count.
- **Implied total M(t):** from 1-minute candles, using strikes with 0 < bid <= ask < 1 and ask - bid <= 0.15. It is
  the point where the YES mid crosses 0.50, linearly interpolated between the bracketing strikes (the dashboard's
  `implied_total`). If there's no crossing, it's missing.

## Primary measurements (descriptive, always reported)

For each event, M at P-30, P-1, P+1, P+2, P+5, P+10, P+30 and P+60 minutes. M(x) is the last candle ending at or
before x.

- **Total move** = M(P+60) - M(P-30), in the event's expected direction (negative for DOWN).
- **Pre-move share** = (M(P-1) - M(P-30)) / total move. The median is reported, overall and for 15-minute-era
  reports alone.
- **Late share** = (M(P+60) - M(P+2)) / total move. This is the part a reader at P+2 could still catch.
- Only events with |total move| >= 1 point count in the share medians. All events count in the trading test.

**Kill rule (descriptive):** if the median pre-move share is >= 0.5, the news is usually priced before the official
report. The verdict is then "official reports are too slow", whatever the trading test says.

## Trading rule (the test; 1 version)

- **Entry:** at the close of the first 1-minute candle ending at or after P + 120 s.
  - Use the main strike: mid nearest 0.50 among strikes with mid 15-85c and ask - bid <= 0.10.
  - DOWN → buy NO at 1 - yes_bid. UP → buy YES at yes_ask.
  - 1 contract, taker fee 0.07 x P x (1 - P), rounded up to $0.0001.
- **Exit:** sell at the bid of the same contract at the close of the first candle ending at or after P + 30 min, or
  the last candle ending before tip if that comes first. Taker fee on the exit too.
- **Net per event** = exit bid - entry ask - both fees.
- **Skip** an event if either candle is missing or the book fails the spread filter, and report the number skipped.

## Split

- **Discovery:** games with ET date 2025-10-21 to 2026-01-22.
- **Unseen:** 2026-01-23 to the end of the 2025-26 regular season.
- The unseen half is run **once**, with the rule frozen after discovery.
- The 26OCT20 and 26OCT21 second-level WS recordings are reported separately (descriptive only), using the official
  reports for those dates fetched the same way.

## Pass bar (discovery, then unseen; all must hold in both)

1. Mean net per event > 0.
2. Bootstrap 95% lower bound of the mean > 0 (resampling games, 10,000 draws, seed 20261022).
3. At least 50 events.
4. Coin flip: at most 5% of 500 copies do as well. Each copy takes a random side at the same entry and exit times.
   Seed 20261022.
5. The kill rule does not fire.

## Information only (never selectable; a pass found only here is not a pass)

- Entry delay 5 min.
- Exit at P+10, P+60 or tip.
- Hold to settlement.
- Material threshold 30 minutes.
- DOWN events only, and UP events only.
- Hourly-era vs 15-minute-era reports.

## Versions

1.

## Amendments

(none yet)
