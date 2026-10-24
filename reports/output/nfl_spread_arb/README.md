# NFL winner vs spread arbitrage: results

*Pre-registered test (see PREREGISTRATION.md, including Amendment 1). The data comes from `kpm spread-collect` and the
results from `kpm spread-arb`. Run on 2026-10-13. Covers 364 games: all 285 games of 2025 (22 weeks, playoffs
included) and 79 games of 2026 (weeks 1–5). That is 8,730 markets and every 1-minute quote Kalshi recorded.*

## Verdict: FAIL for both uses

| Use | Pass bar | 2025 | 2026 | Verdict |
|---|---|---|---|---|
| By hand (10 contracts, lasting at least 2 minutes, strict fills, pre-game) | $2.00/week in both seasons | **$0.02/week** | **$0.00/week** | FAIL |
| Bot (up to 100 contracts, strict fills, pre-game) | $5.00/week in both seasons | $2.59/week | $4.50/week | FAIL |

Under the looser fill rule the bot would show $5.53 and $11.07 per week. That rule isn't the criterion, and the money
comes from the same few events described below.

## What the market looks like

- **Prices almost never break the logic.** Pre-game, a guaranteed pair costing less than $1 after fees came up in fewer
  than 2 of every million pair-minutes checked (context_stats.csv). The single winner-pair check (R4) was never
  positive after fees in 2026.
- **75 pre-game opportunities in 27 weeks** (50 in 2025, 25 in 2026). 28 of them lasted only one minute snapshot.
- **The pre-game dollars come from two one-off events:**
  - 2025-10-10, 9:17 pm ET: a single 98¢ bid for "Bengals win by more than 7.5" while "Bengals win" was offered at
    10¢. It lasted about a minute. Someone sold 50 contracts into it 55 seconds later, and then it was gone. That
    one event is $43.62 of the bot's $57.03 for 2025.
  - 2026-09-27, about 8:28 pm ET: the minute Kalshi opened the week-4 spread markets, a few ladders were seeded
    inconsistently, for example "Cardinals by more than 7.5" offered at 6¢ while "Cardinals by more than 10.5" was bid
    at 19¢. Other traders took 11,000 to 18,000 contracts within 13 seconds. This one minute is all $22.48 of 2026.
- **Mispricings that stood for minutes had no trades.** Eight pre-game violations of at least 1¢ lasted 2 or more
  minutes. Only one had trades in the 60 seconds after it appeared: Bengals–Broncos, 70 minutes before kickoff,
  5.4¢ per pair. That is $0.54 at 10 contracts. The other seven are quotes nobody traded, so their size is unknown.
  Even if all seven could have been filled for 10 contracts, they add up to $7.50 over 27 weeks.
- **In-game mispricings happen during the deciding plays.** The median is 157 minutes after kickoff. 313 of 369 lasted
  a single minute snapshot. In the minute a game is decided, one market reprices within seconds while another lags
  (for example PHI–LAC on 2025-12-08: "Eagles win" went from 61¢ to 2¢ inside one minute). That is a race between
  fast bots, not something to catch by hand.

## Data quality

- D1: 4 markets were excluded as duplicates. Kalshi listed two copies of the same strike in two 2025 games: PHI 17.5
  (DET–PHI) and BUF 22.5 (NO–BUF).
- D2: 1 of 8,734 settled results disagrees with the final score. "New England wins by more than 22.5" (CAR–NE,
  2025-09-28) settled NO, although nflverse has New England winning 42–13 at home and Kalshi settled "by more than
  12.5" YES. Kalshi's rules text for that game also has the venue wrong ("New England at Carolina"). This is unexplained and could be a
  settlement error. A "riskless" pair is only riskless if Kalshi settles correctly.
- D3: a median of 100% of minutes have a known (carried) quote. The Amendment 1 re-check was run on all 29,798 raw
  candle files: a candle after a gap opened at exactly the previous close in 6,911,956 of 6,911,977 cases (99.9997%),
  above the 99% bar. Carrying the last quote forward is therefore accurate.
- D4: no crossed book (bid above ask) in any market. D5: every market's rules text matches the expected wording and
  strike.
- D6: the largest opportunities were re-fetched fresh from the Kalshi API. The stored bid and ask matched the API
  candles in every case, and the trade prints confirm the fills described above.

## Bottom line

Kalshi's NFL winner and spread markets are kept consistent with each other. When they do break, it is a fat-finger
order or a market opening that lasts seconds, and fast bots already take it. There is nothing here for a manual
trader, and not enough for a small bot either.
