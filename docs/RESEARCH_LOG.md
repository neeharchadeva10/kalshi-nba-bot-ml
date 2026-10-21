# Research log

**Append-only.** Never edit or delete an entry. To correct one, add a new entry that points to it. Every idea goes in
here, with its rules and the date, **before** it is tested. Every version tested (each filter, threshold, bucket,
time or variant) adds to the version count. When something "wins", say how many versions were tried to find it.

Managed by the `/log-idea` skill. The funnel (`/funnel`) reads this file to show where each idea stands.

**Funnel steps:** 3 honest test → 4 unseen data → 5 coin flip → 7 team → 8 paper trade → 9 right size → 10 live.
An idea moves to the next step only if it passed the one before.

## Running total

**Versions tested so far: at least 134,754** (#20's historical test added 8, #24 added 42, #29/#30/#31/#32 added 6 each, #37's gate run added 1, #38's k grid added 5, #39's grid added 30, #42's grid added 134,400 plus 1 unseen test), plus 95 pre-registered and not yet run: 34
for #22, 16 for #23, 12 for #26, 8 for #28, 6 for #33, 4 for #35, 6 for #36, 5 for #40, 3 for #41 and 1 for #43. Only 7 tested ideas recorded a count (22 + 192 + 4 + 6 + 6 + 8 + 42); every other tested idea
counts as at least 1, so the true total is higher. Ideas logged: 43. Ideas that passed every step: 0.

## Backfilled entries (written 2026-10-19 from the project memory and reports)

Most ideas before this log did not record how many versions were tried: "n/r" (not recorded, at least 1).

| # | Date | Idea | Versions | Furthest step | Result | Where |
|---|---|---|---|---|---|---|
| 1 | ≤2026-10-05 | NFL: Kalshi vs Polymarket price gaps, final 24 h (2025 season) | n/r | 3 honest | FAIL: no after-fee gap above 2c; Polymarket moves toward Kalshi | reports/output/report.md |
| 2 | ≤2026-10-05 | Reverse test on Polymarket US | n/r | 3 honest | FAIL | reports/output/reverse_report.md |
| 3 | ≤2026-10-05 | Edge hunt (broad scan of simple rules) | n/r | 3 honest | FAIL | reports/output/edge_hunt/ |
| 4 | ≤2026-10-05 | Market making | n/r | 4 unseen | FAIL: good in 2025, lost on fresh 2026 data | reports/output/market_making/ |
| 5 | ≤2026-10-05 | BTC 15m: volatility fair value, momentum, price by time left | n/r | 3 honest | FAIL | reports/output/crypto15m/, crypto_trends/ |
| 6 | ≤2026-10-05 | BTC 15m: higher-timeframe swing structure (the user's own style) | n/r | 3 honest | FAIL: 46-48% win rate, -4 to -5c per trade | reports/output/crypto_htf/ |
| 7 | ≤2026-10-05 | BTC 15m: 90-95c favorite "lead" (taker and maker) | n/r | 4 unseen | FAIL: faded on later data; maker about break-even | reports/output/crypto_maker/ |
| 8 | ≤2026-10-05 | Published "Kalshi Trading Bot v5" rules | 22 | 4 unseen | FAIL: -0.8 to -4.6c per trade; best variant picked from 22, not established | reports/output/crypto_bot_rules/README.md |
| 9 | ≤2026-10-05 | H1: final-minute settlement averaging (BTC 15m) | n/r | 3 honest | FAIL: -0.1 to -3.8c by bucket | reports/output/mechanical_edges/ (h1_*) |
| 10 | ≤2026-10-05 | H2: hourly KXBTCD vs KXBTC15M riskless pairs | n/r | 10 live (micro-test) | Interim: real but tiny, about 0.6c per pair, 80% gone within a minute | reports/output/mechanical_edges/ (h2_*) |
| 11 | 2026-10-05 | GitHub bot "late favorite" (buy the side above 60c at 13-14 min) | n/r | 10 live (micro-test, user's choice) | FAIL in backtest: -1.18c per $1 trade | memory: kalshi-edge-findings |
| 12 | ≤2026-10-05 | Structural scanner (ladder, sell-all, buy-all arbitrage) | n/r | 3 honest | 0 setups at 2 am ET on a Monday; not run at busy times | `kpm scan` |
| 13 | ≤2026-10-05 | Breakout test | 0 | — | Dropped by the user before testing | — |
| 14 | 2026-10-13 | NFL winner vs spread riskless pairs | n/r | 4 unseen | FAIL: $0.02/week by hand, bot dollars were one-off events | reports/output/nfl_spread_arb/README.md |
| 15 | 2026-10-13 | Weather daily-temperature calibration / long-shot bias | 192 | 3 honest | FAIL: 0 of 192 cells; $0/week | reports/output/weather_calibration/README.md |
| 16 | 2026-10-13 | LIP: liquidity rewards with 10-contract resting bids (forward test) | 4 (q=10/50 x rule C/L, pre-registered) | 8 paper (running) | In progress; data gap Oct 14-19 | reports/output/mechanical_edges/PREREGISTRATION.md |
| 17 | 2026-10-13 | H4 step 1: LIP static scan | n/r | 3 honest | Only 28 of 254 books pay $1+ at q=10; led to #16 | reports/output/mechanical_edges/ |

## New entries

<!-- /log-idea appends below this line. Format:

### #<n> — <short name> — <YYYY-MM-DD>
- **Idea:** one sentence.
- **Who loses money to it:** one sentence.
- **Exact rules:** entry, exit, side, size, markets, times, skip rules.
- **Data and split:** series, dates, discovery period, unseen period.
- **Pass bar (fixed now):** ...
- **Versions:** <count> (list each variant as it is added)
- **Status:** logged | 3 honest | 4 unseen | 5 coin flip | 7 team | 8 paper | 9 size | 10 live | FAIL at step N
- **Result:** (added later, as a new dated line, never by editing the lines above)
-->

### #18 — NFL in-game "buy the dip" — 2026-10-19
- **Idea:** when a team's win price drops fast during a game, buy that team, betting the market overreacted.
- **Who loses money to it:** panic sellers after a big play, if the market overreacts. If in-game prices are already
  fair, the dip buyer is the one who loses (spread plus fee).
- **Close to:** nothing tested. #14 used the same in-game quotes, for a different rule.
- **Exact rules:**
  - Markets: KXNFLGAME, each team's YES market, in-game only (kickoff to kickoff + 4 h). 1-minute candles, last
    bid/ask carried forward (as in the spread study's Amendment 1). Mid = (bid + ask) / 2.
  - Dip at minute t (t at least 5 min after kickoff): the highest mid over minutes t-5..t-1, minus the mid at t, is at
    least D cents. D = 10, 15 or 20.
  - Entry: buy 10 YES at the ask one minute later (t+1). Skip if that ask is missing, under 5c or over 95c, or the
    spread is over 5c. After an entry, no new entry on the same market for 15 minutes.
  - Exit E1: hold to settlement (a tie pays 50c). Exit E2: sell 10 at the bid 10 minutes after entry, or settlement
    if the game ended first.
  - Fees: 0.07 x C x P x (1 - P) per order, rounded up to $0.0001, on each buy and sell. None at settlement.
- **Data and split:** data/nfl_spread.sqlite, 360 games with in-game quotes. Discovery: 2025 season. Unseen: 2026
  weeks 1-5. No tweaks after looking at the unseen numbers.
- **Pass bar (fixed now):** for a variant to pass, all of these must hold:
  - mean net per trade above 0 in both periods;
  - unseen 95% lower bound above 0 (bootstrap over games, 10,000 resamples, seed 20261019);
  - at least 50 unseen trades;
  - coin flip: at most 5% of 500 random copies (same entries per game, random in-game minute and team, same skip
    rules and exit, seed 20261019) do as well on the unseen period.

  Fills are at the candle ask, with no trade-print check yet. Any pass needs a print check before it counts.
- **Versions:** 6 (D = 10/15/20 x exits E1/E2)
- **Status:** logged 2026-10-19 23:35Z
- **Result 2026-10-19 23:40Z: FAIL at step 3 (all 6 versions).**
  - Every version lost money in both periods: -0.6c to -5.4c per contract.
  - Hold to settlement after a 10c+ drop, at 10 contracts:
    - 2025: 1,825 trades, 43% won, -1.6c per contract, -$286 total (-$14/week);
    - 2026 unseen: 553 trades, -1.3c per contract, -$71 (-$14/week).
  - Selling 10 minutes later was worse: -3.3c to -3.7c per contract.
  - Random entries lost about the same (median -1.5c to -2.1c hold, -3.7c to -4.3c sell). Buying after a drop is no
    better than buying at a random minute; D20_E2 lost less than random (share 1%) but still lost money.
  - Opus checked 2 sample trades by hand against sp_quotes; the signal, entry, settlement and fee all match.
  - Files: reports/output/nfl_dip/ (dip_test.py, results.json, trades.csv, coinflip.csv).
- **Status 2026-10-19 23:40Z:** FAIL at step 3

### #19 — NFL in-game "ride the jump" (after a score) — 2026-10-19
- **Idea:** the mirror of #18. When a team's win price jumps fast during a game (usually right after it scores), buy
  that team, betting the market under-reacted.
- **Who loses money to it:** slow sellers who don't update after a score, if the market under-reacts. If prices are
  already fair after a score, the buyer loses spread plus fee.
- **Close to:** #18 (the same data and rules, the opposite direction).
- **Exact rules:** identical to #18 except the signal. A jump at minute t means the mid at t, minus the lowest mid
  over minutes t-5..t-1, is at least U cents (U = 10, 15 or 20). Buy 10 YES of that same market at the ask at t+1.
  The skip rules, 15-minute cooldown, exits E1 and E2, and fees are the same.
- **Data and split:** the same as #18 (2025 discovery, 2026 weeks 1-5 unseen).
- **Pass bar (fixed now):** the same as #18, including the 500-copy coin flip.
- **Versions:** 6 (U = 10/15/20 x exits E1/E2)
- **Status:** logged 2026-10-19 23:42Z
- **Result 2026-10-19 23:45Z: FAIL at step 3 (all 6 versions).**
  - Hold to settlement after a 10c+ jump, at 10 contracts:
    - 2025: 1,811 trades, 57% won, -2.3c per contract, -$422 (-$21/week);
    - 2026 unseen: 539 trades, -2.4c per contract, -$130 (-$26/week).
  - Selling 10 minutes later: -3.4c to -7.1c per contract.
  - Only U20 hold was positive in one period (2026: +1.6c, 118 trades, CI -3.5c to +7.6c). It lost 4.7c in 2025, and
    25% of random copies did as well.
  - No version beat random entries by the bar.
  - Opus checked a sample trade (NYJ, 2025-09-07) by hand; it matches.
  - Limitation for both #18 and #19: the signal uses the mid even when that minute's spread is wide, so some signals
    are quote blips rather than plays. Not re-run; it would be a new version.
  - Files: reports/output/nfl_dip/jump_*.
- **Status 2026-10-19 23:45Z:** FAIL at step 3

### #20 — NFL win-margin buckets vs the spread ladder — 2026-10-19
- **Idea:** Kalshi's thin win-margin market (KXNFLWINMARGIN) sometimes prices a bucket below what the deep winner and
  spread markets imply. Buy the cheap bucket.
- **Who loses money to it:** whoever rests stale quotes in the thin margin market.
- **Close to:** #14 (winner vs spread pairs). This is a directional bet, not a riskless pair.
- **Observation that prompted it (not a test):** WAS @ SF, 2026-10-19 23:46Z.
  - "Washington by 1-6" was 10/11c on Kalshi.
  - The winner and spread markets imply about 13.8c (WAS wins 23.5c, minus WAS by 7 or more at 9.5c).
  - Polymarket's exact-margin quote was 12/15c, with no trades.
- **Rules, data and pass bar:** not written yet. Testing needs KXNFLWINMARGIN history (not downloaded), plus the
  spread data already in data/nfl_spread.sqlite.
- **Versions:** 0
- **Status:** logged 2026-10-19 23:50Z (untested)
- **Observation 2026-10-19 23:53Z (scan of all 14 bucket sides, not a test):** "WAS 1-6" YES +2.1c per contract after
  fee, and "SF by 15+" NO +1.0c. Every other bucket side was 0 or negative.
- **2026-10-20 ~05:15Z, pre-registered historical test** (minute level), before any historical win-margin price
  was looked at: reports/output/nfl_margin_hist/PREREGISTRATION.md.
  - Data: about 92 games from 2026 Sep-Oct (WAS@SF and the Super Bowl excluded). Discovery: kickoff before Oct 1.
    Unseen: Oct 1 or later.
  - **Primary:** taker, EV >= 2c after fee, entry one minute later only if the price survived, convergence exit X2.
  - Pass bar as #22, including the coin flip and $2+/week.
- **Versions:** 8 (taker theta 1/2/3c x X1/X2, plus maker delta 1/2c). Not yet run.
- **Result 2026-10-20 ~06:00Z: FAIL** (primary: taker theta 2c, X2).
  - Data: 92 games (48 discovery, 44 unseen), 3,380 markets, 1.12M candles, 149k trade prints. Built by Sonnet;
    Opus hand-checked sample trade 1 against data/nfl_margin.sqlite.
  - Primary result: discovery +1.72c per contract (123 trades); unseen +0.97c (216 trades), about $2.2/week at real
    size.
  - Pass-bar lines:
    - fails line 2: unseen CI -2.52c to +5.06c;
    - fails line 6: only 32-39% of entries had a confirming trade print in the entry minute (sample trade 1 had none);
    - passes lines 1, 3, 5 and line 4: 0.6% of random-timing copies did as well. **The timing signal beats random
      entries clearly** (copies averaged -1.73c), but the profit is too small and too noisy, and most fills can't be
      confirmed.
  - Leads only, not passes (picked from 8 versions): theta 3c X2 unseen +5.39c (94 trades, CI -1.33/+13.86). Maker
    about 0.
  - Data note: KXNFLTIE didn't exist for discovery games, so the 1-6 buckets traded only in unseen.
  - Files: reports/output/nfl_margin_hist/ (summary.json, trades.csv, versions.csv).
- **Status 2026-10-20:** FAIL at step 3/4 (minute level). The order-book test (#22) remains the real test; the
  unconfirmed-fill problem is exactly what its depth-aware fills address.

### #21 — Protected player props vs their regular twins — 2026-10-19
- **Idea:** a "protected" prop (series ending in PROT) has the same outcome as the regular prop. It also refunds the
  pre-game fair price if the player leaves after halftime without hitting the number, so its YES is worth at least
  the regular YES. When the thin protected YES is offered below the regular market's mid, buy it.
- **Who loses money to it:** stale quotes in the nearly untraded protected markets.
- **Observation that prompted it (not a test):** WAS @ SF, 2026-10-19 23:53Z. Of 255 protected/regular pairs, 10
  showed positive EV. The best:
  - Rachaad White 70+ rush yards at 20c vs a regular mid of 22.5c, +1.4c, only 10 contracts offered;
  - Kaelon Black 60+ rush yards, +0.9c;
  - McLaurin 100+ receiving yards, +0.8c.

  No riskless lock: the regular bid was never above the protected ask.
- **Rules, data and pass bar:** not written yet. Testing needs PROT and regular history (not downloaded).
- **Versions:** 0
- **Status:** logged 2026-10-19 23:55Z (untested)
- **2026-10-19 23:59Z:**
  - The user asked Claude to place and manage the WAS 1-6 and White 70+ (protected) bets. Claude declined: the safety
    layer hard-blocks NFL series, more than 1 contract or $1 per order, and placing orders without the user's typed
    confirmation. The user places any orders by hand.
  - A read-only watcher (reports/output/nfl_dip/wassf_watch.py) now logs both legs every 30 s to logs/wassf_watch.csv,
    with "cash out" signals in logs/wassf_watch.log. The signal fires when the bid is at or above the deep-market fair
    value.
  - At start, White 70+ regular had fallen to a 19.5c mid and protected was 18/21c, so it was no longer +EV.
    WAS 1-6 was still 10/11c against a fair 13.75c.
- **2026-10-20 00:05Z:**
  - The user repeated the approval ("you have my confirmation"). Claude wrote
    reports/output/nfl_dip/wassf_order.py for one order: buy 10 YES WAS1TO6 at 11c or better, immediate-or-cancel.
    Its pre-flight checks the price, any existing position and cash; it is a dry run unless --send is given.
  - Claude Code's permission system blocked even the dry run as a real-money action. No order was sent. The White leg
    was dropped (no longer +EV).
- **2026-10-20 00:05:55Z — LIVE ORDER (run by the user with `!`, after a dry run at 00:05:24Z):**
  - Bought 10 YES KXNFLWINMARGIN-26OCT19WASSF-WAS1TO6 at $0.11, immediate-or-cancel; filled 10.00, nothing left.
  - Order id 01a156a1-02b8-7aec-95ac-aee8240d9dea. Fee $0.0686 ($0.0068 per contract).
  - Cost $1.1686, which is also the max loss. Pays $10.00 if WAS wins by 1-6, overtime included.
  - Fair value at entry was about 13.75c (EV about +$0.21).
  - A read-only check at 00:06Z showed position 10.00 and exposure $1.10. Log: logs/wassf_orders.jsonl.
  - Cash-out rule: sell when the bid is at or above the deep-market fair value (the watcher's signal). Otherwise hold
    to settlement.
- **2026-10-20 00:12Z:** sell script ready: reports/output/nfl_dip/wassf_sell.py. The user runs it with `!`; it is a
  dry run unless --send. It sells the whole position at the bid, immediate-or-cancel and reduce_only, and refuses
  while the bid is below fair unless --force. At 00:11Z: bid 10c vs fair 13.75c, so hold.
- **2026-10-20 00:13Z:**
  - The user tested the sell script: the dry run and --send both returned "hold" (bid 10c < fair 13.75c); nothing sold.
  - The user asked Claude to sell "when the time is right". Claude can run the sell only if the user adds a Claude Code
    allow rule for that one script. Claude's rule: only on the watcher's signal, after a dry run, never --force.
  - The user added the allow rule; Claude's dry run at 00:14Z worked (hold).
- **2026-10-20 00:15Z:**
  - At the user's request ("price moves fast"), Claude drops the separate dry run on a signal and goes straight to
    --send. --send re-checks bid >= fair at that moment and holds if not.
  - The watcher was restarted to poll every 10 s instead of 30 s.
- **2026-10-20 00:19:41Z — LIVE SELL (run by Claude under the user's allow rule, on the watcher signal):**
  - Sold 10 YES WAS1TO6 at $0.10, immediate-or-cancel, reduce_only; filled 10.00. Order id
    01a156ad-9960-7797-a20e-03357b9150c6. Fee $0.063.
  - Trigger: early in the game, WAS's win mid fell from 23.5c to 18.5c (00:18-00:19Z). The WAS-by-7+ mid stayed at
    9.5c, so fair dropped to 8.75c while the margin bid sat at 10c.
  - Caveat: the 7+ quote may have lagged. With it about 2c lower, fair would be about 10-10.5c, so the sale was roughly
    at fair, not clearly above it.
  - **Round trip: paid $1.1686, received $0.937, net -$0.23.** Position now 0.
- **2026-10-20 00:27Z — in-game trading set up at the user's request** ("place them when you see them and exit when
  you think it's right"):
  - Script: reports/output/nfl_dip/wassf_trade.py (buy / sell / scan). Hard limits in code:
    - only this game's margin buckets and protected props (YES only);
    - EV of at least +1c after fee against a fair value computed at send time;
    - at most $1.00 and 10 contracts per buy, no adding to a held ticker;
    - at most $3.50 of buys in total (the $5 account cap had $1.11 used);
    - immediate-or-cancel orders only; sells are reduce_only, at or above fair.
  - A monitor scans every ~45 s and flags entries (EV of 1.5c or more, size 3 or more) and exits.
  - Buying needs a user allow rule for this script. In-game scan at 00:26Z: best was +1.4c on 1 contract, so nothing
    qualified.
- **2026-10-20 00:27Z:**
  - The user added the allow rule for wassf_trade.py. Claude's dry run at 00:27:28Z: WAS 7-14 YES at 5c vs fair 7.5c
    (WAS7 mid minus WAS15 mid), EV +2.2c.
  - **LIVE BUY 00:27:41Z:** 4 YES KXNFLWINMARGIN-26OCT19WASSF-WAS7TO14 at $0.05 (only 4 were left at 5c). Order
    01a156b4-f048-758e-a67f-7ee5e17ff02a. Fee $0.0132; cost $0.2132. Fair at send was 7.0c, EV +1.7c per contract
    (+$0.07).
  - Pays $4 if WAS wins by 7-14.
- **LIVE BUY 2026-10-20 00:28:10Z:**
  - 1 NO KXNFLWINMARGIN-26OCT19WASSF-SF7TO14 at $0.70 (YES side at 0.30). Order
    01a156b5-5da8-79be-92b9-59d45a375d55. Fee $0.0147; cost $0.7147.
  - Fair YES was 26c (SF7 mid minus SF15 mid), EV +2.5c. The $1 cap limited it to 1 contract.
  - Pays $1 unless SF wins by 7-14.
- **Fix 00:28Z:** the tonight-spend counter priced NO buys at the YES price (0.30 instead of 0.70). Fixed before any
  further buy; spend is now correctly $0.93 of $3.50. A dry-run sell confirmed NO positions read as negative (-1).
- **Correction 2026-10-20 00:30Z:** Claude told the user the WAS 7-14 YES and SF 7-14 NO "can't both lose". Wrong:
  both lose if SF wins by 7-14 (about 26%, -$0.92). Correct outcomes:
  - SF by 7-14: -$0.92;
  - WAS by 7-14: +$4.08;
  - anything else: +$0.08.

  EV about +$0.10.
- **Exit rule change 00:34Z (before acting on it):**
  - The scanner flagged WAS7TO14 to exit at bid 4c = fair 4c. The fair had fallen from 7c to 4c since the buy.
  - Selling at exactly fair loses the sell fee, so the rule now requires (bid - sell fee) >= fair + 0.5c, mirrored for
    NO. Applied in sell() and in the scan's EXIT lines.
  - Held, no sale.
- **00:33:58Z:** WAS7TO14 dry-run exit: fair was back at 5.5c vs a 4c bid, so hold (the signal was noise).
- **LIVE BUY 2026-10-20 00:34:25Z:**
  - 1 YES KXNFLPASSTDSPROT-26OCT19WASSF-SFBPURDY13-3 (Purdy 3+ passing TDs, protected) at $0.65. Order
    01a156bb-1a68-78ce-95b8-25244acfa8aa. Fee $0.016; cost $0.666.
  - Regular twin mid 68.5c, EV +1.9c. The $1 cap limited it to 1 contract.
  - Spent tonight: $1.59 of $3.50.
- **00:36Z:** the scanner flagged SF7TO14 NO again (66c, +2.4c). Skipped: already held, no adding. The scan now skips
  held tickers.
- **00:38:20Z:** WAS7TO14 exit signal (bid 4c, fair 3c). The sell was aborted: less than 1 contract bid at 4c. Still
  holding 4.
- **00:40Z:** EXIT signals now require at least 1 contract of size on the side being sold into (the 00:38 signal had
  none). The scan loop pause went from 45 s to 15 s, so a full cycle is about 30 s.
- **00:40Z:**
  - McCaffrey 1+ TD (protected) was flagged at 92c vs a regular mid of 99.5c (EV +7c). By the buy 8 s later the ask
    was 99c, so it held.
  - The user asked for more speed. Claude added an --auto mode, where the scanner buys and sells itself under the same
    hard limits. Claude Code's permission system blocked it as an unsafe autonomous agent.
  - Claude removed the --auto code (not run, not compiled) so the existing allow rule can't trigger it. Trading stays
    Claude-in-the-loop on each signal.
- **00:43Z:** the user asked for scans "every second / millisecond". The loop now runs continuously with a 2 s pause.
  One scan is about 16 REST requests covering ~630 markets and takes several seconds. Claude's reaction per trade
  adds 5-10 s.
- **00:44Z:** Mike Evans 80+ receiving yards (protected) flagged at 92c (EV +2.0c). By the buy 6 s later the ask was
  94c (EV +0.1c), so it held. That is the second fast gap missed; both closed within about 10 s.
- **00:45:44Z:**
  - Buy SF1TO6 YES: 8 at 12c, immediate-or-cancel, filled 0 (order 01a156c5-76c0-76f7-a84a-6edf3a77174a); no cost.
  - Check at 00:46: the /markets quote (ask 18c) lagged the live /orderbook (YES ask about 16-17c).
  - **Finding:** the scanner's /events and /markets quotes can lag the real book by seconds, so some "gaps" are
    phantom. Immediate-or-cancel orders make that harmless: no fill, no cost. But the fair values come from the same
    lagging quotes.
- **LIVE BUY 2026-10-20 00:46:29Z:**
  - 10 YES KXNFLWINMARGIN-26OCT19WASSF-WAS1TO6 at $0.05; filled 10. Order 01a156c6-2688-7310-9aae-e82b0ea114f7.
    Fee $0.033; cost $0.533.
  - Fair 7c, EV +1.7c. Pays $10 if WAS wins by 1-6.
  - Spent tonight: $2.13 of $3.50.
- **00:46Z:** the user said "i give permission, time is key" for auto-trading. Not enabled: the permission system's
  block needs a rule in the user's settings, not chat approval. Claude also recommended against it, since the misses
  are mostly stale data, not reaction speed.
- **00:50Z:**
  - The user asked for the rule. Before handing it over, Claude changed buy() and sell() to price from the live
    /orderbook (target and fair-value twins), not the lagging /markets quote.
  - Auto mode now lives only in reports/output/nfl_dip/wassf_auto.py (loop: scan, then act, about every 1 s; stops
    at 04:30Z or when logs/wassf_auto.STOP exists). `wassf_trade.py scan` from the CLI stays read-only, so the earlier
    allow rule can't trigger auto mode.
  - Rule given to the user: `Bash(KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/nfl_dip/wassf_auto.py)`.
- **00:50Z injury note:** the user reported McLaurin went down.
  - His regular props crashed: 40+ yards from 61.5c pre-game to 6/24c; 3+ receptions from 74c to 4/5c.
  - Protected props are worth about the pre-game price if he doesn't return after halftime. Their makers pulled back
    (asks 73-99c), so there was nothing to buy cheaply.
  - The scanner can't be fooled by this: it only buys protected YES below the regular twin, and protected is worth at
    least the regular.
- **00:52Z:**
  - The user added the rule. Claude stopped the read-only scan monitor and started wassf_auto.py in the background.
  - Claude Code's permission system then blocked Claude from watching it, both the log-tail monitor and a one-off
    `tail` of logs/wassf_orders.jsonl ("Create Unsafe Agents").
  - Claude can't see its fills or errors and told the user. Options: the user watches it, Claude stops it, or the
    user allows log reads.
- **00:53Z:** the user allowed `tail -5 logs/wassf_orders.jsonl`, so Claude can read the log on request. Auto
  started 00:51:30Z with no trades.
- **00:54-00:56Z bug:** every margin buy aborted with "no trustworthy fair value". With live order books, long shots
  (TIE, WAS by 15+) have no YES bids, so their mid was None.
  - Fix: use half the ask when the ask is 3c or less.
  - Restarted twice (00:55:23Z, then 00:56:20Z). Checked that no stray process was left each time.
  - Checked fair values from live books: SF1TO6 16.25c, SF7TO14 29c, SF15+ 39.5c, WAS1TO6 9.25c, WAS7TO14 4c.
- **AUTO SELL 2026-10-20 00:56:26Z:**
  - 1 YES Purdy 3+ passing TDs (protected) at $0.47, filled. Order 01a156cf-3ea8-7284-9dd2-e2efbc10a269. Fee
    $0.0175.
  - The regular twin's mid had fallen to 44.5c. Rule: 0.47 - 0.0175 >= 0.445 + 0.005.
  - Round trip: paid $0.666, got $0.4525, **net -$0.21**.
- **AUTO BUY 2026-10-20 01:03:04Z:**
  - 2 YES KXNFLWINMARGIN-26OCT19WASSF-SF15PLUS at $0.41. Order 01a156d5-5158-7205-9ab2-d21912d416e1. Fee
    $0.0169/contract; cost about $0.854.
  - Fair 44.5c, EV +1.8c. Spent tonight: about $2.98 of $3.50.
- **01:03Z mark-to-fair (live books):** realized -$0.44. Open bets (cost -> fair):
  - WAS1TO6: 10 contracts, $0.53 -> $0.75;
  - WAS7TO14: 4 contracts, $0.21 -> $0.12;
  - SF7TO14 NO: 1 contract, $0.715 -> $0.70;
  - SF15+: 2 contracts, $0.854 -> $0.89.

  Open +$0.15, so the night is about **-$0.30** marked to fair. The estimated edge at entry across all trades was
  about +$0.53. The gap is game moves plus exits near fair.
- **AUTO SELLS (found 01:16Z, when the user saw only 1 position in the app):**
  - 01:08:01Z WAS1TO6: 10 at 6c (bought 5c). Order 01a156d9-dd68-7607-8436-293330665742. Net about +$0.028 after
    both fees.
  - 01:10:31Z SF7TO14 NO, sold by buying YES at 26c, so NO went out at 74c (bought at 70c). Order
    01a156dc-2758-7fc7-90bd-ee95f45d8ec1. Net about +$0.012. **First live NO-exit; that path works.**
  - 01:11:26Z SF15+: 2 at 44c (bought 41c). Order 01a156dc-fa48-7405-85e6-b47073056306. Net about -$0.008.
  - Exchange positions (read-only, 01:16Z): only WAS7TO14 = 4 open; all others 0.
  - Realized tonight about **-$0.41**: -0.23 pre-game WAS1TO6, -0.21 Purdy, +0.03 / +0.01 / -0.01 auto scalps.
  - Correction: Claude told the user the trader "usually holds to the end". In practice the thin margin bids move
    around fair, so it round-trips within minutes.
  - Budget: $2.98 of $3.50 spent; sells don't free budget, so only buys of $0.52 or less can happen.
- **01:23Z:** the user raised tonight's buy cap from $3.50 to $5.00 ("since we were fixing some bugs").
  - Auto trader restarted with the new cap; checked that no stray process was left.
  - Worst case if every new buy goes to $0: earlier bot losses $0.88 + tonight realized $0.41 + open $0.21 + new buys
    $2.02 = about $3.52, still under the user's $5 account-wide loss cap.
- **AUTO BUYS 2026-10-20 01:24:49Z** (just after the restart):
  - 6 YES Kittle 90+ receiving yards (protected) at 15c, fee $0.053, cost $0.953;
  - 5 YES Purdy 40+ rushing yards (protected) at 17c, fee $0.049, cost $0.899.
  - Spent $4.83 of $5.00, so buying is effectively done.
- **01:27Z:** read-only dashboard http://127.0.0.1:8766 (reports/output/nfl_dip/wassf_dashboard.py).
  - Shows every fill with price, fee and realized gain/loss on an average-cost basis, open positions at the live bid
    and fair value, and totals.
  - Its ledger matched the hand numbers: realized -$0.4129.
  - At 01:27Z: open about -$0.06 at fair, night about -$0.47.
- **AUTO SELL 2026-10-20 01:33:49Z (found 01:58Z):**
  - WAS7TO14: 4 at 1c. Order 01a156f1-7860-711a-85bc-e6a6e03ecf09. Fee $0.0028. Fair had fallen below 0.5c.
  - Round trip: cost $0.2132, got $0.0372, **net -$0.176**.
  - Missed at first because Bash `tail -5` returned a stale view of the log at 01:47 and 01:53 (PowerShell
    Get-Content showed newer lines). Claude's 01:48 and 01:53 reports of "3 open bets" were wrong; it was 2 from
    01:33Z. Health checks now use PowerShell.
  - Open now: Kittle 90+ receiving yards (protected) x6, Purdy 40+ rushing yards (protected) x5.
- **AUTO BUY 2026-10-20 02:27:46Z:**
  - 3 YES WAS1TO6 at 3c (only 3 offered). Order 01a15722-dce8-7b16-861a-c23caaa1b47f. Fee $0.006; cost $0.096.
  - Fair 4.75c, EV +1.5c. Spent $4.93 of $5.00.
- **02:52:21Z, the only auto_error so far:** PermissionError writing a raw-cache .tmp file under data/raw/kalshi/.
  - Likely cause: the trader, dashboard and watcher all cache identical public requests through CachedClient, and two
    processes wrote the same temp file at once (Windows file lock).
  - The loop caught it and was trading normally 12 s later. No order was involved. Not fixed mid-game.
  - Fix later: per-process temp names in the raw cache.
- **2026-10-20 03:23Z: game over, SF won by 1-6** (KXNFLGAME WAS/SF determined; WINMARGIN SF1TO6 at 99c).
  - Auto trader stopped at about 03:24Z; no trades after the game ended. Checked: after the game, /markets showed
    0/1 quotes (mid 0.50) and the order books were empty. buy() saw no fair value from the empty books and aborted.
  - Open bets, all heading to $0 (regular twins at 0/1c):
    - WAS1TO6 x3, cost $0.096;
    - Kittle 90+ receiving yards (protected) x6, cost $0.953;
    - Purdy 40+ rushing yards (protected) x5, cost $0.899.
- **NIGHT RESULT (#20/#21 live, not a valid test):** realized -$0.59, plus open bets settling at $0 (-$1.95), so
  **about -$2.54**.
  - Kalshi's own numbers agree: realized ex-fee -$0.24, fees $0.46, open cost $1.84.
  - The account-wide loss cap now has about $3.42 of $5 used ($0.88 earlier bot + $2.54).
  - Edge estimated at entry, all buys: about +$0.6. Fees alone were $0.46, about 9% of the ~$5 bought.
  - The result is dominated by game outcomes: the last two protected-prop buys ($1.85) both missed.
  - Not a valid test of #20/#21. Rules changed mid-game, 6 bugs were fixed live, and the sample is one game.
  - Next: test #20/#21 offline through the funnel, using logs/wassf_watch.csv, logs/wassf_signals.csv,
    logs/wassf_orders.jsonl and past games. No live re-run before that.

- **POST-GAME ANALYSIS 2026-10-20** (Opus, read-only; reports/output/nfl_dip/postgame.py, .json, _trades.csv,
  _signals.csv):
  - **Total -$2.54, matching Kalshi.** True fees were $0.46. Every order's fee = ceil(0.07·C·p·(1-p)) to $0.0001, so
    the repo's `cent_ceil` overstates small orders.
  - **Decomposition:** entry edge +$1.08, exit edge +$0.42, fees -$0.46, market moves -$3.58.
    - The market moves split into -$2.22 on bets held to settlement and -$1.36 on round trips.
    - Fair fell between buy and sell on all 6 round trips. The exit rule causes part of this: a sell can only fire
      after fair drops by about spread + both fees + 1.5c, unless the thin bid rallies. So exits are adversely
      selected by design.
  - **Markouts (9 buys):**
    - Margin buckets (6): fair stayed flat after entry (-0.25c / -0.13c at +1 and +5 min) while the thin ask rose
      toward it (+0.8c / +1.8c). **These gaps look real.**
    - Protected props (3): fair fell 2.5c at +1 min and 11c at +15 min. Two of three entries priced off one-minute
      spikes in the regular twin. **The twin-mid fair is unreliable in-game.**
    - n=9 and dominated by game events, so not conclusive.
  - **Funnel (auto):** 122 episodes. 47% were phantom: the listing was stale and the live book said no.
    - Book-confirmed gaps: median 7 s, max 132 s; 17 of 60 lasted under one 4 s scan.
    - The budget cap blocked 55 episodes. Recurring gaps on SF1TO6 YES and SF7TO14 NO lasted up to ~2 min.
  - Candles are too coarse for second-level markouts, with up to about a minute of staleness and big gaps. This
    supports #22's recorder.
  - Kittle 90+ PROT is still unsettled: a series-wide settlement lag, valued at 0 from its regular twin.

### #22 — NFL thin-market stale quotes vs deep twins, at order-book speed — 2026-10-20
- **Idea:** #20 and #21 combined, tested at the speed the gaps really live. A thin market (win-margin buckets,
  protected props, possibly others) shows a price that lags its deep twin. Take it before it corrects.
- **Who loses money to it:** resting orders in thin markets that aren't updated when the deep market moves.
- **Why a new entry:** WAS @ SF showed the gaps close within ~10 s, and 1-minute candles can't see them. Testing needs
  our own order-book recordings.
- **Step 2 (get data), approved by the user 2026-10-20 ~03:45Z ("ok use my key, go straight to the recorder"):**
  - A read-only Kalshi WebSocket recorder. Order-book deltas plus public trades for every market of each recorded NFL
    game, stored raw with receive timestamps.
  - It uses the user's API key only for the WebSocket handshake. It places no orders.
  - Target: 3-5 games before any test.
- **Rules, simulator and pass bar:** to be written here **before** looking at the recordings. They will set latency
  (0.25/1/5 s), exact fees, fill only if the quote still existed at our simulated arrival, and a discovery/unseen
  split by game.
- **Versions:** 0
- **Status:** logged 2026-10-20 (data collection)
- **2026-10-20, recorder built** (Sonnet wrote it, Opus reviewed):
  - Files: src/kpm/collectors/nfl_markets.py (`kpm nfl-markets --date`), src/kpm/execution/ws_recorder.py
    (`kpm exec record`) and src/kpm/analysis/ws_books.py. Suite: 2808 passed, isolation tests included.
  - Smoke test, 90 s live, read-only, NE@CHI 26OCT22NECHI (910 markets in 49 series): 910 snapshots, 2,721 deltas,
    17 trades, 0 gaps, 0 reconnects. About 40 msg/s and 4.5 MB/h gzipped pregame.
  - Opus review asked for: one file per recorder process (crash safety), and a discovery test for sub-event tickers.
    Both done: files are now msgs-<hour>-<start epoch>.jsonl.gz. Suite 2809 passed; Opus re-ran the 71 recorder,
    book and isolation tests (all pass).
- **2026-10-20 04:28Z, recording started** at the user's request ("can we start now"):
  - NE@CHI, 910 markets in 51 series. Discovery refreshed first.
  - Runs detached until 2026-10-23T05:00Z. First heartbeat: 910 snapshots, 0 gaps.
  - Pregame recording doubles as a shakedown and as pregame thin-vs-deep data (the WAS1TO6 gap was pregame).
  - Plan: restart with fresh discovery Thursday afternoon, to catch props listed late.
- **2026-10-20 ~04:40Z, pre-registered** before any game recording was looked at:
  reports/output/nfl_ws/PREREGISTRATION.md.
  - **Primary:** win-margin buckets, taker, EV >= 1c after fee, 1 s arrival delay, convergence exit (X2).
  - Split by kickoff order: first half of games is discovery, the rest unseen. Test only once 6+ games are recorded
    and 3+ are unseen.
  - Pass bar: mean > 0 in both halves; unseen 95% lower bound > 0 (bootstrap by game); 100+ unseen trades; coin flip
    5% or less; $2+/week at real size; fills confirmed by prints or size.
- **Versions:** 28 (A taker 12, B taker 12, A maker 4). The running total is updated above.
- **2026-10-20 ~05:00Z, other-sports scout** (Haiku, read-only; reports/output/nfl_ws/other_sports_scout.md):
  - **NBA is the closest match.** It has KXNBAGAME, the KXNBASPREAD ladder and per-game KXNBAWINMARGIN buckets (1-5,
    6-10, 11-20, 21+ per team, 8 per game, no tie). 14 margin events fall in the next 7 days, and most buckets have no
    bids at all. Its protected-prop series exist but have 0 events.
  - Soccer has winner and spread markets plus an in-game draw, but no per-game margin buckets. NCAA basketball has
    margin series, with games starting 26NOV01.
  - NHL, MLB, NCAAF and WNBA have winner and spread only.
  - NFL margin buckets show only NECHI so far; Sunday's appear closer to game day, so discovery must run near
    kickoff.
  - If used, NBA becomes its own pre-registered idea (#23), not part of #22.
- **2026-10-20 ~06:05Z, amendment** (before any #22 recording was looked at): theta 3c added to Family A taker
  (x L 0.25/1/5 s x X1/X2), because it was the best unseen #20 historical version (a lead only). **Versions: 34.**
  The primary and the pass bar are unchanged. Text: Amendments section of the pre-registration.
- **2026-10-20 ~07:40Z, simulator built** (Sonnet wrote it): src/kpm/analysis/ws_sim.py and ws_sim_stats.py
  (`kpm ws-sim`). All 34 versions, primary `A-T-th1-L1-X2`. Tested on synthetic data only; no recording opened.
  Suite: 2876 passed. An Opus line-by-line review against the pre-registration is still due before the test runs.
- **2026-10-20 ~07:55Z, Opus code review of the simulator** (no recording opened):
  - **Bugs fixed:**
    - An unsettled market kept its X2-exited trades while its held ones were dropped. This was optimistic.
    - The smoke-test file wasn't excluded.
    - Invalid input books didn't trigger the minute exclusion.
  - Suite: 2880 passed.
  - Open points were settled the strict way in a dated pre-registration amendment (~08:00Z): outages, a same-side
    fill check, no reuse of recorded liquidity, a bucket title cross-check, and others. The primary, versions and pass
    bar are unchanged. The code for those is next.
- **2026-10-20 ~08:40Z, amendment code done** (Sonnet wrote it, Opus checked it; synthetic data only, no recording opened):
  - Every 08:00Z clarification is in the code.
  - NBA mode: `kpm ws-sim --sport nba`, 16 versions, no tie, gate of 20 games / 10 unseen, 50 games/week.
  - Opus found that several raw folders were chained on one clock with one book set. Overlapping recorders would
    have made the test INVALID, and sids restart per recorder. Now each folder is its own stream, merged by time.
  - A ticker in two folders makes the test INVALID, and silences over 60 s are reported.
  - Opus ran the 150 simulator, book and isolation tests: all pass. Full suite: 2918 passed (Sonnet's run).

### #23 — NBA win-margin buckets vs deep twins (order-book speed) — 2026-10-20
- **Idea:** #22's Family A applied to the NBA. KXNBAWINMARGIN buckets are priced against KXNBAGAME (no ties) and the
  KXNBASPREAD ladder.
- **Who loses money to it:** stale resting orders in nearly empty NBA margin books.
- **Data:** the read-only WS recorder started 2026-10-20 05:05Z on 3 games (26OCT20 BOSDET, OKCSAS, PHINYK; 1,437
  markets). Data goes to data/ws/nba/<date>/.
- **Pre-registered** before any NBA data was looked at: reports/output/nba_ws/PREREGISTRATION.md.
  - Primary: taker, EV >= 1c, 1 s delay, X2.
  - The test needs 20+ games recorded and 10+ unseen. Pass bar as #22.
- **Versions:** 16 (pre-registered, not run).
- **Status:** logged 2026-10-20 (data collection)
- **2026-10-20 ~17:42Z, live look at NBA prices:**
  - The user asked for NBA picks for tonight. Claude ran a read-only REST scan of the 26OCT20 games:
    reports/output/nba_picks/26OCT20/.
  - These games will be in #23's discovery half. The recordings stay unopened.
  - The #23 rules are frozen as of the 08:05Z amendment, and no later amendment may cite this look.

### #24 — Pre-game price drift: when to bet (NBA primary, NFL secondary) — 2026-10-20
- **Idea:** at 26OCT20BOSDET, the opening lines had DET slightly favoured and BOS firmed to about 53% by tip.
  - If the direction of the pre-game drift is predictable, buying early on that side beats the closing price.
  - The predictors tested: favourite, home team, momentum, or an Elo rating.
  - This answers the user's question "what is the optimal time to bet on an NBA game".
- **Who loses money to it:** people who post or take early prices before information arrives.
- **Pre-registered** 2026-10-20 ~18:30Z, before any NBA history was downloaded: reports/output/nba_drift/PREREGISTRATION.md.
  - Primary: NBA, buy the favourite at T-6h, scored by closing-line value net of the ask and fee.
  - 7 strategies x 3 entry times x 2 sports = **42 versions**.
- **Context:** the same day, every price source (Kalshi, Polymarket, DraftKings) agreed on BOSDET's winner, spread and
  total within about 1c. A Pistons ticket was written, then withdrawn as a recommendation once DraftKings' current
  line was found.
- **Status:** data download and test running (Sonnet builds, Opus checks).
- **2026-10-20 ~19:35Z, NFL secondary result: FAIL** (360 games; reports/output/nba_drift/README_NFL.md).
  - All 21 versions lose. V1 favourite at T-6h has a CLV net of -2.3c in both halves (unseen CI -3.4/-1.4).
  - Typical CLV is about -2c, roughly half the spread plus the fee.
  - The drift from entry to close averages 1.4-2.2c and nothing tested predicts its direction.
  - The close is the best calibrated (Brier 0.213).
- **2026-10-20 ~19:40Z, NBA:**
  - The download is complete: 1,363 games of 2025-26, via the historical endpoints.
  - The pre-registered tip-off check failed (1/20). Kalshi's time minus 3 h is the scheduled start, and real play
    begins 12-15 min later.
  - Amended (tip = scheduled start, a new validation, preseason dropped, active weeks) before any NBA strategy number
    was computed. The NBA run is next.
- **2026-10-20 ~20:10Z, NBA RESULT: FAIL** (reports/output/nba_drift/README.md, results.json):
  - **Data:**
    - 1,317 regular-season and playoff games (659 discovery / 658 unseen, split at 2026-01-23).
    - The tip validation passed 20/20 under the amendment.
  - **Primary (V1 favourite, T-6h):**
    - CLV net -2.30c in discovery and -2.20c unseen (CI -2.46/-1.94).
    - Coin flip 0.978. Settlement P&L -3.0c. -$6.84/week.
    - Fails 5 of 6 rules; only rule 3, the trade count, passes.
  - **All 21 NBA versions:** negative in both halves, every unseen CI below 0, no leads. **All 21 NFL versions fail
    too.**
  - **Why:**
    - The loss equals half the spread plus the fee.
    - Prices drift 0.9-3.4c before tip, but nothing tested predicts the direction.
    - The close is the best-calibrated price (NBA Brier: T-24h 0.2018, close 0.1974).
  - **Opus check:** recomputed the primary independently from data/nba_drift.sqlite. 1,319 trades: CLV net
    -2.31c / -2.19c, settlement -3.05c unseen. This matches.
  - **Answer to the user's question:** there's no profitable "best time" to buy NBA/NFL winners on Kalshi using
    favourite, home, momentum or Elo. Without a better forecast than the market, any time loses about the spread
    plus the fee, and the closest-to-tip price is the most accurate.
- **Versions:** 42 (now tested; moved from pre-registered to tested in the running total).

### #25 — In-game riskless locks across a whole NBA game (observation, not a strategy test) — 2026-10-20
- **What:** a read-only WebSocket watcher over all 564 markets of 26OCT20BOSDET (reports/output/nba_picks/26OCT20/lockwatch.py,
  log logs/lockwatch_bosdet.jsonl).
  - Ladder inversions: a stronger strike's bid above a weaker strike's ask, both legs bought.
  - Mutually exclusive sets: buy every NO, or every YES where the set covers all outcomes.
  - Exact fees at count <= 10. No orders.
  - The user asked "be that bot", then "seems like you need to be faster".
- **Pre-game (17:58-19:00Z, about 585k book updates):** 0 locks.
- **In-game, first ~25 min (to 19:25Z):** 14 locks, none in props.
  - Markets: 1st-quarter winner 5, race to 10 points 4, game total ladder 3, 1Q total 1, 1H total 1.
  - **Every one lasted 0.001-0.02 s:**
    - 9 under 0.01 s, worth $2.57 combined at 10 contracts;
    - 5 at 0.01-0.1 s, worth $0.40;
    - **0 lasted 0.1 s or more.**
  - The biggest was the race-to-10 buy-both-NO, $2.25 at 10 contracts, lasting 0.007 s, as a team reached 10.
- **What it means:**
  - These locks are 10-100x shorter than our order round trip from a home PC (about 0.1-0.5 s).
  - Some are likely non-atomic quote updates (one leg updates a few ms before the other) that no outside order could
    fill.
  - Catching them needs co-located speed, and it would be a race against market makers for cents.
  - **Not worth a paid server unless locks lasting 0.5 s or more show up.** Full-game tally to follow.
- **Monitoring lesson:** Git-Bash `tail -F` doesn't see appends from the Windows writer, the same failure as on 10-19.
  The first two alert monitors were silent while locks happened. Fixed with a Python poller that re-opens the file.
  Silence was checked against the heartbeat before trusting it.
- **Same day, also learned:**
  - Kalshi, Polymarket and DraftKings agreed within about 1c on BOSDET winner, spread and total. The Polymarket-only
    gaps were on books 5-22c wide.
  - DraftKings has no official public API; scraping is off-limits.
  - Opening lines that "leaned DET" were stale; using them was nearly a mistake, caught by checking DraftKings' current
    line.
  - #24 shows that no pre-game timing works.
  - The user's live-order path is now reports/output/picks/approve.py plus the user-only /send skill (moneyline/spread
    only, $3 total cap, IOC, one shot per ticket, 30-min expiry). Nothing has been sent.
- **2026-10-20 ~19:58Z update** (BOSDET, game about 1 h in):
  - 30 locks, $14.16 combined at their shown sizes, overlapping legs double-counted. 25 lasted 5 ms or more ($14.02),
    15 lasted 10 ms or more ($10.53), and **0 lasted 30 ms or more**.
  - Three events dominate:
    - **19:40:52.338Z, Mitchell Robinson rebounds ladder (prop).** YES 10+ / NO 12+ was worth $6.64 at 10 contracts,
      plus two overlapping pairs on the same NO leg. It lasted 14 ms, at the moment he crossed a rebound threshold.
    - **Race to 10:** $2.25, lasting 7 ms.
    - **End of the 1st quarter:** $0.93, lasting 5 ms.
  - So the locks are **resolution events**: when a game event settles one market, its related markets update a few ms
    apart. De-duplicated, the 10-ms-plus value is about $7.5 (about $0.7 without props).
  - PHINYK and OKCSAS had 0 locks pre-game.
- **Latency measured** (reports/output/latency/README.md, 19:53Z):
  - WebSocket feed delay: median 19.6 ms, p90 47, p99 134.
  - REST round trip to CloudFront IAD: median 17.8 ms warm, 81 ms cold.
  - Signing 0.03 ms; processing 0.01 ms.
  - **Total reaction from home is about 30 ms typical, about 60 ms at p90.** Too slow for all 30 locks.
  - An AWS us-east-2 server is estimated at about 5-10 ms. That's not measured; it would reach the 10-30 ms
    resolution locks if not beaten by others.
  - Clock was 152 ms slow before the w32time fix.
- **Versions:** 0 (observation).

### #26 — Be the maker in thin win-margin buckets (NBA primary, NFL secondary) — 2026-10-20
- **Idea (the user's choice after "maybe we need to change our approach"):**
  - Stop taking prices. Post two-sided quotes around the deep-market fair value in thin KXNBAWINMARGIN /
    KXNFLWINMARGIN buckets, where makers pay no fee, and hold to settlement.
  - Today's evidence: every deep market agrees within about 1c across venues, so taking loses the spread plus the
    fee. The thin buckets showed wide, empty books.
- **Who pays us:** takers who cross the wide bucket spreads.
- **Risk:** being picked off when the deep market moves. The strict fill model counts cross fills, and markouts are a
  pass-bar line.
- **Pre-registered** 2026-10-20 ~20:00Z, before any recording was opened: reports/output/maker_ws/PREREGISTRATION.md.
  - Primary: NBA, delta 2c, two-sided.
  - 12 versions.
  - Gate: 20 NBA games (10 unseen).
- **Versions:** 12 (pre-registered, not yet run).
- **Status:** logged. Simulator extension next (synthetic data only). The NBA recorders are running.
- **2026-10-20 ~19:55Z, simulator built** (Sonnet: src/kpm/analysis/ws_maker.py, `kpm ws-maker`,
  tests/test_ws_maker.py). Synthetic data only.
  - Opus reviewed the quoting, trade-fill, cross-fill, inventory and go-live code: it matches the pre-registration.
  - 15 interpretations, all the stricter reading, are recorded in every summary.json.
  - A crossing order at go-live is dropped. The live version must therefore use post-only orders.
  - Opus ran 68 maker and isolation tests: all pass. Full suite: 2943 passed (Sonnet's run).

### #27 — A server next to Kalshi: can it reach the resolution locks? (read-only trial) — 2026-10-20
- **Idea:** the user's push for speed ("make the trading faster and reduce latency").
  - From home our reaction is about 30 ms and no lock lasted 30 ms. Ohio (us-east-2) is estimated at 5-10 ms.
  - BOSDET had about $7.50 (de-duplicated) of locks lasting 10 ms or more.
- **Pre-registered** 2026-10-20 ~20:05Z, before any server measurement: reports/output/ohio_trial/PREREGISTRATION.md.
  - The trial is read-only and runs for at least 5 NBA nights / 25 games.
  - Pass needs:
    - median server reaction <= 10 ms;
    - mean reachable de-duplicated lock value >= $3 per night;
    - >= $2 per night without the single biggest event.
  - Default scope is non-prop; props are included only if the user decides so before the first server night.
- **Setup:** the user creates the AWS account, budget alarm, EC2 t4g.small in us-east-2 and a separate Kalshi key
  (docs/OHIO_SERVER_SETUP.md). Claude deploys the read-only watcher.
- **Versions:** 1 (decision rule).
- **Status:** server up 2026-10-20 ~20:45Z; trial running.
- **2026-10-20 ~20:45Z, server up.**
  - EC2 i-094e9277b9ca0d405 `kalshi-ohio`: t4g.small, Ubuntu 26.04 arm64, Python 3.14, us-east-2c.
  - Address 18.223.21.99. SSH allowed from the user's IP only. Clock on Amazon Time Sync (offset about 2 us).
  - **Key:** the user chose to use their existing Kalshi API key on the server for now ("i will switch it out if
    something goes wrong"). It was copied without being printed; .env and the key are mode 600.
- **First network comparison** (reports: scratchpad rtt.py), median:

  | Target | Home | Ohio |
  |---|---|---|
  | TCP connect, WebSocket host | 38 ms | **0.42 ms** |
  | TCP connect, trading-api | 33.6 ms | **1.10 ms** |
  | TCP connect, api.elections (CloudFront) | 15 ms | 1.7 ms |
  | Warm HTTPS GET, exchange/status | 21.3 ms | **5.7 ms** |

- **Night 1 (26OCT20) started ~20:52Z on both** (lockwatch_night.py --tag ohio / --tag home). It's a partial night:
  BOSDET was nearly over, so the night covers PHINYK and OKCSAS. The prop scope is undecided, so the default is
  non-prop, and both are reported.
- **Night 1, first 3 minutes of feed** (lock_report, SNTP-corrected):

  | Measure (median) | Home | Ohio |
  |---|---|---|
  | Feed delay | 23.6 ms (p90 34, p99 68) | **7.3 ms** (p90 9.3, p99 16) |
  | Order host external-api.kalshi.com, the host `kpm.execution` uses (warm GET) | 33.8 ms | **3.88 ms** |
  | TCP connect to the order host | 28.9 ms | 0.42 ms |

  - **R, per the pre-registration (feed median plus half the REST round trip): home about 40.5 ms, Ohio about
    9.2 ms.** That passes rule 1 so far, with nights to come. The reachable threshold is about 11.2 ms.
  - About 7 ms of the Ohio feed delay is Kalshi's own fan-out; the network adds only about 0.4 ms.
- **2026-10-20 ~21:20Z, the user chose "props yes"** (dated amendment in the pre-registration), before any night-1
  lock result was viewed.
- **First server lock results, end of BOSDET (in-game):**
  - Ohio: 9 events, 4 reachable at R 9.2 ms, worth $0.62. The best was a moneyline buy-both-NO, $0.45 at 10
    contracts, lasting 55 ms.
  - Home: 0 reachable at R 40.5 ms.
  - In-game feed delay over 10 minutes: home median 20.6 / p90 71 / p99 253 ms; Ohio 7.5 / 10.2 / 99 ms.
- **2026-10-20 ~21:40Z, speed work on our side (A/B, both read-only, same server):**
  - lockwatch_fast.py runs one process per game, with orjson, uvloop and per-frame instrumentation (proc_us,
    backlog, gap_us).
  - It's running for PHINYK and OKCSAS next to the unchanged all-games lockwatch_night (the baseline).
  - Home smoke test: our processing is about 48 us median per frame (p99 555 us), so the 100 ms tail is likely not our
    processing.
  - The A/B on PHINYK and OKCSAS will show whether per-game processes cut the tail.
- **2026-10-20 ~21:50Z, user decision: go live now, without waiting for the 5-night watch.**
  - Quotes: "lets go live to really test out the server"; NBA only; "each game budget 5 dollars".
  - Limits set by Claude, changeable by the user:
    - $5 at-risk per game;
    - $15 per night (the user didn't give a nightly cap; their Kalshi cash is about $17.70);
    - 1 contract per leg, minimum profit 1c per set after fees;
    - immediate-or-cancel only, no unwinding, all NBA locks including props.
  - The user left auto mode (Manual) so the bot could be built. Claude builds it and tests it dry; **the user starts
    it live themselves** (Claude doesn't place trades).
  - This is a live micro-test outside the funnel, the user's explicit choice like the 10-19 MNF night. The read-only
    trial measurements continue alongside, and both are reported separately.
- **2026-10-20 ~22:26Z, live bot started by the user** (lockbot.py on the Ohio server, PHINYK):
  - The first start was refused by the preflight: NBA markets are on Kalshi **exchange shard 3** (cash $0), and only
    **KXNBAGAME is on shard 0** (cash $17.70).
  - The bot was patched to trade only funded shards. Tonight that means **moneyline locks only** (2 markets per
    game). Funding shard 3 would enable everything else automatically.
  - The OKCSAS bot refused: only 5 of 8 connections warmed, because two processes warmed at once. The user is asked to
    restart it alone.
  - Limits: count 1, $5 per game, $15 per night, min profit 1c, max 15 attempts per game, halt after 3 order errors.
- **2026-10-21 ~00:06Z, shard funding (the user's action):**
  - The user ran reports/output/picks/shard_transfer.py --send: $9.00 moved from Exchange 0 to Exchange 3 (Tennis,
    Baseball, Basketball) through POST /portfolio/intra_exchange_instance_transfer (transfer 57e49446...).
  - Balances: shard 0 $8.70, shard 3 $9.00.
  - Kalshi's web and app auto-move funds between shards at order time; API orders don't.
- **2026-10-21 ~00:10Z, bots restarted by the user with all markets:**
  - PHINYK has 784 tickers; OKCSAS has 771 after a warm-up fix. A 429 to the 8-connection burst had counted as a
    failure; now any HTTP reply counts, with up to 4 retries.
  - The earlier moneyline-only PHINYK bot ran from 22:26Z to 00:06Z: 298k messages, 0 locks on the moneyline,
    0 attempts.
- **2026-10-21 00:41Z:** a grow-trigger was added (GrowDetector: also fire when an open lock's profit rises past min
  profit). The user added Edit/deploy allow rules for lockbot.py and restarted the bots.
- **2026-10-21 01:15:13Z, FIRST LIVE ATTEMPT** (PHINYK, KXNBA3Q 3rd-quarter winner, me_no over NYK/PHI/TIE, count 1,
  at the end of Q3):
  - **Partial:** only NO PHI filled, 1 @ $0.01 + $0.0007 fee. NO NYK and NO TIE got 0.
  - At risk $0.0107 (PHI was about 99c to win Q3, so expect about -1c). Game at-risk $0.0107 of $5.
  - Decision latency 26.5 ms, likely a first-use import of kpm.execution.client inside build_body. To fix: import at
    startup.
  - Lesson: quarter-end ME locks are contested, and we lost the race on 2 of 3 legs.
- **In-game Kalshi vs Polymarket check** (PHINYK moneyline, 90 samples at 2 s, 01:10-01:13Z, a 31c to 40c swing):
  - 47% of samples had a mid gap of 1c or more (max 3c), and 9 were crossed.
  - Only 2 were positive after the Kalshi fee (+0.3c and +0.45c). One 1c cross lasted about 10 s.
  - The idea of using the faster venue as a lead signal is logged as a candidate (not pre-registered yet).
- **2026-10-21 01:35:33Z, second live attempt:**
  - PHINYK moneyline me_no, near the finish: **none filled** (cost $0).
  - **Decision latency was 127 ms.** Cause: the send was scheduled as an asyncio task and waited behind the frame
    backlog in the end-of-game burst.
- **Fix, deployed about 01:45Z:**
  - Orders now go to the sender threads directly from the detection call (consider()), before the next frame is
    processed.
  - kpm.execution.client is imported at startup (the first attempt's 26 ms was the import).
  - The test now exercises the immediate-send path. The user restarted both bots.
- **2026-10-21 01:42:35Z, attempt 3** (after the fix; decision 3.3 ms, round trips 9-11 ms):
  - PHINYK moneyline me_no: **partial**. NO NYK filled 1 @ 0.56 + $0.0173 fee; NO PHI got 0.
- **2026-10-21 01:48:01Z, attempt 4** (decision 2.9 ms): moneyline me_yes, **partial**. YES NYK filled 1 @ 0.24 +
  $0.0128 fee; YES PHI got 0.
- **Attempts 3 and 4 offset each other on KXNBAGAME-NYK.**
  - Kalshi confirms (read-only positions): position 0, realized +$0.20, fees $0.0301, so **net +$0.17**.
  - With Q3 leftover -$0.0107: **night P&L so far about +$0.16.** Cash: shard 0 $8.8668, shard 3 $8.9893.
  - The bot's ledger still counts $0.84 at risk, because it doesn't net offsetting fills. That's conservative; fix it
    together with the planned "complete the missing leg" logic.
- **2026-10-21 ~04:50Z, live night final** (reports/output/ohio_trial/live_26OCT20/ANALYSIS.md):
  - **Sources agree:** the bot logs and the user's Kalshi fills and settlements (read-only).
  - **Cash:** $17.6969 to $19.5195, **+$1.8226**.
    - 1 open: Champagnie 4+ REB YES at 4c, already in that number. The market says he got 2-3, so expect NO.
    - PHINYK -$0.0171. OKCSAS +$1.8397.
  - **Attempts:** 29, with **0 complete locks**, 14 partial and 15 none. 17 contracts filled.
  - **Speed:** round trips about 10 ms median and decisions 1-5 ms after the fix. The missing legs were missed
    anyway.
  - **Where the profit came from:**
    - 2 of 13 long-shot legs (1-7c) won: 3Q total 63+ at 5c and Holmgren 6+ REB at 2c, for +$1.93. At the prices paid
      that has about a 4.8% chance, so treat it as luck.
    - The moneyline pair netted +$0.17 because NYK fell 20c between the two partial attempts.
    - The 4Q me_no put $1.96 at risk for 4c and paid.
  - **OKCSAS halted** on the 15-attempt cap at 04:24:07Z. Both bots exit at 09:00Z.
  - **Lead, not pre-registered:**
    - In inverted ladders, does the stale-cheap leg win more often than its price? Both winners were that side.
    - Test it on the read-only lockwatch_night logs, pre-registered before looking at outcomes.

### #28 — Polymarket leads Kalshi in-game? Single-leg follow (NBA) — 2026-10-21
- **Idea (the user's in-game hunch):** in-game, the two venues drift apart by 1-3c during swings. If Polymarket
  reliably moves first, one Kalshi taker order in the same direction catches the follow-through.
- It's single-leg, so it avoids the all-legs-must-fill problem of locks. No Polymarket account is needed.
- **Who pays:** slower Kalshi quotes during swings.
- **Pre-registered** 2026-10-21 ~01:55Z, before any paired data: reports/output/leadlag/PREREGISTRATION.md.
  - Primary: theta 2c, L 0.05 s, X1 (exit when converged, or after 30 s).
  - 8 versions.
  - Gate: 30 or more games, 15 or more unseen.
- **Versions:** 8 (pre-registered, not yet run).
- **Status:** a read-only Kalshi+Polymarket recorder is being built for the Ohio server, from 26OCT21.

### #29 — NBA game-winner model: strength, rest, home, key-player availability — 2026-10-21
- **Idea (the user's goal):** predict NBA winners before tip-off from team strength (Elo, rolling margin), rest,
  home court and key-player availability, and bet where the model disagrees with Kalshi's pregame price by more than
  the fee.
  - Availability and trades come from Kalshi's own player-points markets: who is listed per game, for which team,
    and their recent scoring.
- **Who pays:** the pregame price, if it underreacts to absences or schedule spots.
- **Pre-registered** 2026-10-21 ~03:10Z, before any prop data was downloaded: reports/output/nba_model/PREREGISTRATION.md.
  - Primary: full model, edge >= 4c at T-1h.
  - 6 versions.
  - Same discovery/unseen split as #24.
- **Prior evidence against:** #24's Elo-edge versions all lost. The close is better calibrated than earlier prices.
- **Versions:** 6 (pre-registered, not yet run).
- **Status:** downloading the KXNBAPTS history, then the model and test (Sonnet builds, Opus checks).
- **2026-10-21 ~04:00Z, RESULT: FAIL** (reports/output/nba_model/README.md, results.json; 9 tests pass, checked by
  Opus).
  - **Data:** 23,562 KXNBAPTS markets (227 players) and 16,937 KXNBASPREAD markets; KXNBAWINMARGIN had no 2025-26
    markets. 1,317 games, split 659 / 658 (identical to #24).
  - **Primary (full model, edge >= 4c at T-1h):**
    - Discovery +2.07c, unseen **-2.72c** per contract (CI -6.9 / +1.4); 386 unseen bets.
    - Coin flip 0.696; -$5.00 per week.
    - **Model unseen Brier 0.1931 vs market 0.1803.** The model is clearly worse than Kalshi's pregame price.
  - **All 6 versions are positive in discovery and negative unseen** (overfit, then regress). No leads.
  - **Availability vs the market:** when key-player availability pointed to the market underdog (241 games), the
    market's side won 65.1%. The market already prices absences better than this proxy.
  - **Data limits:**
    - 44% of margins are bracket estimates, because 579 spread results were "Yes"/"No" text.
    - Prop listings are thin: both teams were listed in only 898 of 1,317 games.
    - A better data source (official injury reports, box scores) is a possible new idea, but the 0.013 Brier gap to
      the market is large.

### #30 — NOAA NBM forecast model vs Kalshi daily-high markets — 2026-10-21
- **Idea:** convert NOAA's official NBM station forecast (TXN max plus XND spread) into bracket probabilities, with
  rolling per-station calibration, and take the model's side where it beats ask plus fee.
- Physics-based information, thin markets, no speed race, single-leg.
- **Feasibility (no outcomes viewed):**
  - Source: bucket noaa-nbm-grib2-pds, text/blend_nbstx (V4.0-V5.0 with TXN/XND; V3.2 pre-2021 lacks spread). All
    24 Kalshi CLI stations are present.
  - The 06Z run is out by about 07:30Z. About 92 GB to stream; only about 40 KB per file kept.
  - Parser: reports/output/weather_nbm/nbm_text.py.
- **Pre-registered** 2026-10-21 ~04:35Z: reports/output/weather_nbm/PREREGISTRATION.md.
  - Primary: morning (09:00 LST, 06Z run), theta 5c.
  - 6 versions.
  - Split at 2025-07-01.
- **Versions:** 6 (pre-registered, not yet run).
- **2026-10-21 ~04:00Z, RESULT: FAIL** (reports/output/weather_nbm/README.md, results.json; 15 tests).
  - **Data:**
    - 4,078 NBM files (88.2 GB) streamed on the Ohio server in 30 min. Every run used was published before its
      decision time (S3 LastModified).
    - Prices: 1-minute candles already in weather.sqlite.
  - **Primary (morning 09:00 LST, 5c):**
    - Discovery -0.21c, unseen **-3.17c** per contract (CI -3.7 / -2.6); 17,453 unseen bets.
    - Coin flip 100%: random picks lose less than the model.
    - **Model Brier 0.158 vs market 0.130.**
  - **All 6 versions are negative unseen.** Evening is the least bad (-1.9c).
  - The market beats the NBM model in every period. Spreads tightened from about 11c (discovery) to about 3c
    (unseen), and the market sharpened.
  - By 09:00 the market already reflects morning observations that the 06Z NBM can't.
  - **Server note:** the pull overlapped the OKCSAS live game (03:18-03:48Z, load about 7.5 on 2 CPUs). Bot decision
    latency stayed 0.9-3 ms and round trips 8-14 ms, but **don't run heavy jobs on the trading server during games**.
- **Status:** FAILED 2026-10-21.

### #31 — NBA game-winner model v2: official injury reports + open box scores — 2026-10-21
- **Idea:** #29 with its two data weaknesses fixed: exact margins from an open box-score dataset, and real
  availability from the NBA's official injury reports (P(miss) by status x player value from plus-minus per minute).
  The user approved both data sources.
- **Pre-registered** 2026-10-21 ~04:20Z, before any download: reports/output/nba_model/PREREGISTRATION_V2.md.
  - Same split, bets, versions and pass bar as #29.
  - Primary: full v2, theta 4c.
- **Versions:** 6 (pre-registered, not yet run).
- **2026-10-21 ~05:00Z, RESULT: FAIL** (reports/output/nba_model/README_V2.md, results_v2.json; 18 tests pass, checked
  by Opus).
  - **Data:**
    - 815 official NBA injury-report PDFs (69 MB), parsed into 97,353 rows. Every game has a report published 63-120
      min before tip.
    - URLs were built from the NBA CDN file-name pattern, because the 2025-26 index page now redirects to 2026-27.
    - Box scores: GitHub sportsdataverse/hoopR-nba-data (ESPN-derived, LICENSE.md CC BY 4.0). 1,317 of 1,317 games
      matched, so margins are exact.
  - **Primary (full v2, theta 4c):**
    - Discovery +1.82c, unseen **-1.66c** (CI -5.6 / +2.4); 390 unseen bets.
    - Coin flip 0.454; -$3.08 per week.
    - **Unseen Brier: v2 0.1920 vs market 0.1803** (v1 was 0.1931).
  - **All 6 versions are negative unseen.** The injury feature's weight is tiny, and the Elo-only model has the better
    unseen Brier than the full model.
  - **Conclusion:** with real injury reports and exact scores, a public-data model still trails Kalshi's pregame NBA
    price by about 0.012 Brier. The market already prices availability. Close the NBA winner-model line.
- **Status:** FAILED 2026-10-21.

### #32 — NBA game-winner model v3: 5 seasons, sportsbook moneylines + Kalshi — 2026-10-21
- **Idea (the user's request):** the v2 model over 5 seasons. Train on 2021-24, test unseen on 2024-25 against
  sportsbook moneylines, and confirm on 2025-26 against Kalshi.
- **Data decision (the user's):** moneylines come from kyleskom's OddsData.sqlite.
  - It has **no license** and was scraped from SBR by that author. Book and timing are unknown.
  - **Private research only.** The licensed alternative (hoopR CC BY closing spreads, no moneylines) was offered and
    declined.
- **Pre-registered** 2026-10-21 ~05:30Z: reports/output/nba_model/PREREGISTRATION_V3.md.
  - Primary: full model, edge >= 4%, 2024-25 moneylines.
  - 6 versions.
- **Versions:** 6 (pre-registered, not yet run).
- **2026-10-21 ~04:30Z, RESULT: FAIL** (reports/output/nba_model/README_V3.md, results_v3.json; 31 tests pass).
  - **Data:**
    - 6,605 games (2021-22 to 2025-26). Moneylines matched for about 99%.
    - 3,533 official injury PDFs parsed with 0 failures (377 MB on disk).
    - The odds look near-closing: correlation with Kalshi T-1h is 0.972 on 553 overlapping games.
  - **Primary (full, edge >= 4%, 2024-25 vs moneylines):**
    - -4.25c per $1 over 660 bets (CI -15.9 / +7.7). Coin flip 0.538; -$7.79/week at $10.
    - **Brier: model 0.2109 vs de-vigged moneyline 0.2012.**
    - Kalshi 2025-26 confirmation: -2.42c per contract; Brier 0.2075 vs Kalshi 0.1980.
  - **All 6 versions lose in both tests.** Elo carries the model. Injury weight is about 0 (-0.028 per sd, falling to
    -0.007 with more data). Larger edges don't do better.
  - **Conclusion:** with 5 seasons, exact scores, real injury reports and trades, a public-data model still trails
    both the sportsbook close and Kalshi by about 0.01 Brier every season. **Close the NBA winner-model line.**
- **Status:** FAILED 2026-10-21.

### #33 — The cheap leg of a "fake lock": does it win more often than its price? (NBA, exact order books) — 2026-10-21
- **Idea (the user's request: "yes test the cheap leg lead"):** when the live bot's lock math fires and the lock
  turns out to be fake, buy only one leg and hold it to settlement. The main candidate is the cheapest leg.
- **Source:** the 26OCT20 live night.
  - 2 of 13 cheap one-sided fills won (+$1.93). The chance of that with fair prices is about 4.8%.
  - That night is reported separately and never counts.
- **Who would pay:** a stale resting order left at an old price after news moved a neighbouring market.
- **Prior evidence against:** the long-shot bias (#5); possible adverse selection of the side still there.
- **Pre-registered** 2026-10-21 ~05:00Z, before any further outcome was looked at:
  reports/output/cheapleg/PREREGISTRATION.md.
  - Data: home WS order-book recordings from 26OCT21 on, with the live bot's lockcore math at count 1 and profit
    >= 1c.
  - Fill: IOC arriving at t + L.
  - 6 versions: leg {cheapest, stale, trigger} x L {10, 40 ms}. **Primary: cheapest, 10 ms.**
  - Pass bar: mean > 0 in discovery and unseen, bootstrap LB > 0 by game, at least 200 unseen fills,
    price-matched coin flip <= 5%, at least $2/week at count 1.
  - Gate: at least 30 test games (15 unseen) settled.
- **Recording coverage:** the 26OCT21 recorder (started 10-20) missed quarter, half and some prop markets listed
  later.
  - A supplement recorder, data/ws/nba/26OCT21b (1,830 markets; tag nba-26OCT21b; until 2026-10-22T09:00Z), started
    2026-10-21 04:51Z. *PROT series are excluded.
  - Later nights: start the night's recorder in the afternoon, after the quarter/half lines are listed.
- **Versions:** 6 (pre-registered, not yet run).
- **Status:** simulator being built (Sonnet builds, Opus checks). Source night first, then the test at the gate.

### #34 — Weather: does The Weather Company settlement disagree with the NWS daily value? (feasibility, no prices) — 2026-10-21
- **Idea (the user's push for a real edge):**
  - Kalshi daily high/low markets switched their settlement source from the NWS climate report to The Weather Company
    (TWC).
  - If TWC's number often lands in a different bracket than the NWS value most traders watch, late-day prices could
    be wrong.
  - Step 1 measures the mismatch rate only, reading **no prices**.
- **Run 2026-10-21** (reports/output/weather_twc/; Sonnet built it, Opus re-counted it from events.csv: 0 disagreements;
  9 tests).
  - **Switch:** every series moved on event day 2026-08-14 (the last NWS day was 08-13): 20 KXHIGH* and 20 KXLOW*
    series.
  - **NWS-era control:** 12 mismatches in 13,667 events (0.09%). 9 of them come from GHCN's CF6-sourced values; 5 are
    one Houston cluster.
  - **TWC era (08-14 to 10-19):** **1 mismatch in 3,128 events (0.03%)**, Miami 2026-08-29 (TWC 5 F higher than
    GHCN). Not different from the control (Fisher p = 0.48).
  - No edge-of-bracket or C/F conversion pattern.
  - NWS values come from GHCN-Daily, plus the api.weather.gov CLI for the last 2 days. GHCN and the CLI agreed on all
    233 overlapping station-days.
- **Versions:** 0 trading versions (descriptive feasibility only).
- **Status:** CLOSED 2026-10-21. TWC settles the same bracket as NWS 99.97% of the time, so there is no
  settlement-source edge to test.

- **#33 addendum, 2026-10-21 ~06:15Z: source night 26OCT20 run** (reports/output/cheapleg/source_26OCT20/; 21 tests;
  41 min replay). **This night doesn't count.**
  - **Events:** 588 lock_open events. Only 61 had profit of 1c or more at count 1, and 50 remained after de-dup (39
    ladder, 11 me_no).
  - **Primary (cheapest, 10 ms):** 31 fills of 43 events, 3 wins.
    - Mean +$0.020 per fill. CI -0.094 / +0.261. Coin flip 24%.
    - One 5c prop leg paying $1 accounts for the whole gain.
  - **Other versions:** all small and noisy (fills 9-29).
  - **Live-fill reproduction:**
    - 10 of the live bot's 13 cheap fills have a replay lock within 2 s, and the live leg was our cheapest leg in all
      10. 9 of the 10 fill at 10 ms.
    - 3 live fills have no replay lock.
  - **Clarifications** (start time rule, refire, universe, max 3 legs, coin-flip pool, gate) are in the
    pre-registration's Amendments.
  - **Power warning:** about 10 primary fills per game, so 200 unseen fills needs about 40 or more test games.

### #35 — Buy the NBA favorite late in Q4, timed by the game clock — 2026-10-21
- **Idea:** from the research survey.
  - Moshrefi (2026): Kalshi favorites are underpriced in the last 10 minutes before close.
  - Bürgi et al.: makers buying at 50c or more were positive.
  - Here the timing comes from the **game clock** (pbp wallclock), not the close time, to avoid look-ahead.
- **Pre-registered** 2026-10-21 ~06:10Z: reports/output/nba_late_fav/PREREGISTRATION.md.
  - Data: Kalshi KXNBAGAME 1-minute candles and prints (2025-04-15 to 2026-06-30), plus hoopR pbp with wallclock.
  - 4 versions: X {6:00, 3:00} left in Q4 x band {70-92c, 80-95c}. **Primary: 6:00, 70-92c**, taker, count 10, hold.
  - Pass bar:
    - mean > 0 in both halves;
    - bootstrap LB > 0 by game;
    - at least 100 unseen trades;
    - **price-matched coin flip from periods 1-3** <= 5%;
    - at least $2/week;
    - confirmed-fill mean > 0 on unseen.
- **Versions:** 4 (pre-registered, not yet run).
- **Status:** build (Sonnet backtest-builder), then audit (Opus leak-auditor), then run.

### #36 — NBA player-prop model vs Kalshi (PTS/REB/AST/3PT), T-1h — 2026-10-21
- **Idea (the user's pick, "thinner markets"):** a negative-binomial stat model that uses:
  - rate, projected minutes and teammates out (official injury report);
  - opponent, rest and home.
  - It is compared with Kalshi's prop prices 1 hour before tip.
- **Pre-registered** 2026-10-21 ~06:10Z: reports/output/nba_props/PREREGISTRATION.md.
  - It must pass the `/model-test` gates on discovery first: Brier vs mid, the residual test, and 20 or more
    cost-beating disagreements per week. If any fails, stop.
  - Then 4 betting versions: theta {3c, 6c} x {15-85c, all prices}. **Primary: 3c, 15-85c.**
- **Versions:** 6 (2 gate runs + 4 betting; pre-registered, not yet run).
- **Status:** data download (prop hourly candles) and build (Sonnet), then audit (Opus).

- **#33 addendum, 2026-10-21 ~06:40Z: Opus leak-audit verdict NOT TRUSTWORTHY yet.** Mechanics were verified by
  running the code, and the source night was recomputed exactly, but there are 2 blockers.
  - **Blockers:**
    - A failed gate still printed the unseen stats.
    - Max 3 legs was not implemented.
  - **Majors:**
    - RACE events were grouped wrongly.
    - 10 ms over-fills compared with the live bot.
    - Cross-connection locks.
    - Ticker-index overflow past 32,767.
    - Tips depended on file order.
    - The run date wasn't pinned.
  - **Amendments 7-12 written** (pre-registration file):
    - run once at >= 200 unseen primary fills (count-only check);
    - **primary latency changed to 40 ms**;
    - cross-connection locks excluded from the primary;
    - *PROT included;
    - earliest lock in the de-dup chain;
    - fills need depth.
  - Fixes go to the builder, then re-audit.
- **Recording (user, 2026-10-21):** from 26OCT22, one home-PC recorder per NBA night, started daily at 16:00 ET by a
  Windows scheduled task. The Ohio server was rejected: about 11 GB free disk, and transfer costs.

### #37 — The user's pace x efficiency formula for NBA game totals vs Kalshi — 2026-10-21
- **Idea (the user's own formula):**
  - Team points = avg(ORtg_own x Pace_own, DRtg_opp x Pace_own). Total = the sum of both teams.
  - Inputs are last season's regular-season ratings from free box scores.
  - Example: BOS @ DET = 108.99 + 113.87 = 222.86.
- **Pre-registered** 2026-10-21 ~07:00Z: reports/output/nba_totals_pace/PREREGISTRATION.md. All choices are the
  user's:
  - only their version;
  - last season only;
  - compared at T-1h;
  - chances from the formula's typical miss (sigma from discovery only);
  - default bet rule: one bet per game on the best-edge strike, 15-85c, count 10, hold.
  - Model-test gates on discovery first: MAE vs the market median, strike Brier, residual test, volume.
  - Betting theta {3c, 6c}. **Primary 3c.**
- **Versions:** 3 (pre-registered, not yet run).
- **Status:** the user asked for this first. The #35 and #36 builds are **paused** (downloads stopped; cached and
  resumable).


- **#33, 2026-10-21 ~07:10Z: PAUSED by the user** ("pause the cheap-side one for now"). The audit-fix build was
  stopped partway, so cheapleg.py and its tests may be half-edited. On resume, re-run the tests and finish fixes 1-13
  first. Recording continues: the nightly scheduled task and tonight's three recorders.

- **#37 result, 2026-10-21 ~09:05Z: FAIL at the model gates** (discovery only, 660 games, Oct 21 2025 - Jan 22 2026).
  The unseen half was never loaded and no betting rule was run. 1 version tested; the 2 betting versions are void.
  - **Before the run:** the Opus leak-audit fixes went in (amendment 2 in the pre-registration). The ladder check found
    0 mismatches on 16,489 settled strikes.
  - **Gate 1, accuracy: FAIL.**
    - MAE: model 15.81 points vs the market median 14.67 (the bar was 15.17).
    - Strike Brier: 0.2265 vs 0.2102 (the bar was 0.2122).
    - A blend of the two (0.2136) is also worse than the market.
  - **Gate 2, residual: FAIL.** Disagreement coefficient 0.055, 95% CI [-0.19, +0.30]. Where the formula disagreed
    with the price, the price was right.
  - **Gate 3, volume:** passed, but the gate is trivially easy.
  - **Bias:** the formula ran 4.3 points low on average. By month: Oct +7.3, Nov +5.4, Dec +5.9, Jan -0.5.
    - The model is miscalibrated (overs hit more often than it said). The market's calibration is close to perfect.
  - **Exploratory, discovery only and in-sample:**
    - Removing the bias only brings MAE to 15.47, still well behind 14.67.
    - corr(actual, formula) = 0.14 vs corr(actual, market median) = 0.34.
    - The slope of actual on the formula is 0.63, so the projections are too spread out.
  - **Data note:** KXNBATOTAL `expiration_value` is per market (the running total when that strike settled), not the
    final total. This means:
    - the auditor-proposed "first non-empty value per event" is wrong as a final score;
    - the "Kalshi vs hoopR total" cross-check (7 of 72 differ) and the 2026-27 forward-look actuals are **invalid**;
    - the gates are unaffected, since they use hoopR totals (all present) and Kalshi yes/no results.
  - **Structural weaknesses**, for any refinement (a new pre-registration; the unseen half is still clean):
    - each team gets its own pace, though both teams have about the same possessions in a game;
    - last season's ratings miss roster changes and this season's scoring level;
    - there is no injury, rest or home adjustment.

### #38 — The user's totals formula, fixed: shared pace, this season's stats, league level — 2026-10-21
- **Idea:** #37 failed, and the user chose "Fix the formula only".
  - Both teams use the average of the two paces.
  - Last season's ratings are shifted to this season's league level, using games before the date only.
  - The ratings are blended with this season's ratings (last season counts as k games).
  - The user's averaging of offense and opponent defense is kept.
- **Pre-registered** 2026-10-21 ~09:20Z: reports/output/nba_totals_v2/PREREGISTRATION.md.
  - k is picked from {0, 5, 10, 20, 40} by discovery MAE.
  - #37's gates are run on discovery at the chosen k. Only if all pass is the betting rule run (theta 3c/6c, primary
    3c), with one unseen run.
  - No new data: it reuses #37's T-1h quotes and its split.
- **Discovery outcomes were already seen in #37.** The unseen half is untouched and is the honest test.
- **Versions:** 7 (5 model + 2 theta), pre-registered and not yet run.

- **#38 result, 2026-10-21 ~10:25Z: FAIL at the model gates** (discovery only, 660 games).
  - Before the run: amendment 1 went in (the turnover fix for both seasons, D = min(tip, T-1h), hoopR-only
    actuals). 660 of 660 projections are finite.
  - **Discovery MAE by k:** 0 = 15.74, 5 = 15.41, 10 = 15.37, 20 = 15.37, 40 = 15.39. **Chosen k = 20.**
    - Prior only (league-shifted): 15.56.
    - Current only: 15.74.
  - **Gate 1: FAIL.**
    - MAE: 15.37 vs the market's 14.67 (the bar was 15.17).
    - Brier: 0.2219 vs 0.2102 (the bar was 0.2122).
    - A blend of the two (0.2138) is also worse than the market.
    - Using only quotes with spreads of 10c or less, the market is 14.69 and the model 15.39.
  - **Gate 2: FAIL.** Disagreement coefficient -0.105, 95% CI [-0.41, +0.19]. Disagreements with the price carry no
    information; if anything they point the wrong way.
  - **Gate 3** passed, but the gate is trivially easy.
  - **Bias** is now -0.9 overall. By month: Oct +3.2, Nov 0.0, Dec +0.4, Jan -5.5.
    - The shift and blend removed most of #37's lowball, which closed 0.44 of the 1.14-point MAE gap.
  - **The unseen half was never loaded.** The 2 betting versions are void.
  - **Takeaway:** season-level box-score ratings, whether last season, this season or blended, do not beat Kalshi's
    T-1h total line. The remaining gap must come from game-specific information the market already has (injuries,
    rest, lineups).

### #39 — NBA totals formula tournament: every published box-score method vs Kalshi — 2026-10-21
- **Idea (the user):** "google it find it create it... test all possibilities", plus "the ones we are more
  confident we can add more".
  - The research notes are in research_notes/NBA totals formulas/.
  - The numbers were verified against Basketball-Reference 2024-25: ORtg x pace reproduces points per game (BOS
    116.4 vs 116.3, DET 115.5 vs 115.5).
- **Pre-registered** 2026-10-21 ~10:50Z: reports/output/nba_totals_tourney/PREREGISTRATION.md.
  - Grid: 5 formulas (average, multiplicative, additive, points-per-game average, points-per-game additive) x 3
    rating types (season blend, recent games weighted, adjusted for opponents) x rest adjustment on/off (fitted on
    2024-25).
  - **Winner = the lowest discovery Brier.** Only the winner is gated. Only if it passes does it get one unseen test
    (the user's choice).
  - Bets are sized 1-10 contracts by edge (the user's choice). theta 3c/6c, primary 3c.
- **Prior evidence is against it** (#37, #38; the literature has slope 0.02).
- **Versions:** 32 (30 model + 2 betting), pre-registered and not yet run.

- **#39 result, 2026-10-21 ~12:10Z: FAIL. All 30 versions are worse than the market on discovery** (660 games).
  - **Market:** MAE 14.67, Brier 0.2102.
  - **Range across the 30 versions:** MAE 15.30-15.68, Brier 0.2207-0.2260.
  - **Winner F4_R2_S1** (points-per-game average, recent games weighted, back-to-back adjustment):
    - MAE 15.30, Brier 0.2207, bias -0.16;
    - residual coefficient -0.054, CI [-0.36, +0.24];
    - gate 1 FAIL, gate 2 FAIL.
  - **All 30 residual coefficients are negative** (-0.05 to -0.17), and none has a CI excluding 0. The formulas'
    disagreements with the line carry no information.
  - **What helped a little:**
    - the back-to-back adjustment (S1 beats S0 in all 15 pairs);
    - recent-games weighting (R2 beats R1).
  - **What hurt:**
    - the opponent adjustment (R3);
    - the multiplicative and additive forms (F2, F3, F5), which are worse than the user's averaging (F1, F4).
  - **Back-to-back c** (with an intercept, 2024-25): -1.07 to -1.26 per tired team.
  - The unseen half was never loaded; the 2 betting versions are void.
  - **Exploratory market check, discovery (already-seen data), information only:**
    - Kalshi's T-1h median by month: Oct overs 57.5% (n = 80, actual - line +3.4), Nov 48.2%, Dec 50.0%, Jan 40.2%
      (n = 164, -3.5). Overall 47.9%.
    - This fits the literature's early-season totals bias (Baryla et al. 2007) and in-season level drift (Paine).
    - It comes from one season, so it is a hypothesis only. A fresh test would have to be forward, on 2026-27.
  - **Conclusion for #37-#39:** published box-score formulas, in any combination tried, do not beat Kalshi's T-1h
    NBA total line.

### #40 — Early-season NBA overs on Kalshi's total line (forward test, 2026-27) — 2026-10-21
- **Idea:** from #39's exploratory market check (Oct 2025 overs 57.5%, n = 80) and the literature (Baryla et al.
  2007).
- **Pre-registered** 2026-10-21 ~12:40Z, before that night's games: reports/output/nba_early_overs/PREREGISTRATION.md.
  - Games: ET 2026-10-21 to 2026-11-03 (the user chose "First 2 weeks").
  - Buy 10 YES at the strike with mid nearest 50c, at T-1h, as a taker, and hold.
  - Pass bar: mean > 0, bootstrap LB > 0, >= 80 bets, coin flip <= 5%.
- **Runs** about 2026-11-04/05 from public candles. Paper only.
- **Versions:** 1.

### #41 — The user's formula on Kalshi NBA TEAM totals, forward test (2026-27) — 2026-10-21
- **Idea (the user):** "what about per team score instead of combined". The user chose "Forward on this season".
- **Why forward:** KXNBATEAMTOTAL exists only from 2026-02-10. Testing it on 2025-26 would reveal the unseen half's
  game totals.
- **Pre-registered** 2026-10-21 ~13:10Z: reports/output/nba_team_totals_fwd/PREREGISTRATION.md.
  - Model: #39's F1_R2_S1, applied per team.
  - Window: ET 2026-10-21 to 2026-12-31, extended by count only.
  - Gates first, then theta 3c/6c sized 1-10. Primary 3c.
- **Versions:** 3.
- **Also on 2026-10-21, live money (user's decision, placed by the user):** $1 per game early-overs bets, by hand,
  from scripts/early_overs_picks.py (#40's rule) for ET 2026-10-21 to 11-03.
  - After a few nights by hand, the user wants "tickets + one command".
  - Claude does not place trades.
- **#40 live-bet method (the user, 2026-10-21 ~19:10Z):** limit buy YES at the bid (maker; KXNBATOTAL maker fee
  0.0175 x C x P x (1-P)), placed T-75 to T-45 min, and cancelled if unfilled by tip - 15 min.
  - The real bets therefore differ from #40's registered taker-at-ask paper rule.
  - Both are reported, along with how many of the user's limit orders filled.
- **#40 amendment, 2026-10-21 ~19:30Z, before any window game: an UNDER (NO) version is added,** so #40 now has 2
  versions.
  - **Sportsbook check** (private file), first 14 days, 2007-08 to 2023-24: overs 45.5-46.1% (n about 1,700), unders
    53.2%.
  - **Kalshi 2025-26**, same weeks: overs 56%, unders 44%. The two sources conflict.
  - **Lower alt-line overs:** worse in both sources at Kalshi's prices.
  - **The user's real $1 bets** switched to the under: a limit buy NO at the NO bid.
- **#40 amendment, 2026-10-21 ~19:50Z, before any window game: a FORMULA-SIDE version is added,** so #40 now has 3
  versions.
  - Side = over if #39's F1_R2_S1 projection > strike, else under.
  - Exploratory discovery win rate for such picks: 48.6-51.1%, so there is no evidence of an edge.
  - The user's real bets now follow it: 1 contract, a limit at the chosen side's bid. This supersedes the under-only
    plan before any bet.

### #42 — Exhaustive NBA totals formula grid, every one backtested after fees and slippage — 2026-10-21
- **Idea (the user):** "exhaust all possible formulas ... every combination, every logical formula ... backtest every
  one of these".
- **Pre-registered** 2026-10-21 ~20:20Z: reports/output/nba_totals_grid/PREREGISTRATION.md.
  - 26,880 formulas: combine x pace x ratings x prior k x league shift x rest x home/away split x shrink.
  - x 5 confidence thresholds = **134,400 strategies**.
- **Bet:** the main strike, side by the projection, 1 contract, taker at the ask + 1c slippage + fee.
- **Winner** = the highest discovery lower bound of net per contract (n >= 150). A luck check runs on 2,000
  random-side strategies.
- **The accuracy gates are reported, not enforced** (the user chose a profit-based selection).
- **The winner gets ONE test on the never-loaded unseen half** (661 games), with the full pass bar and a coin flip.
- **#42 discovery run, 2026-10-21 ~20:10Z** (after the audit-fix amendment). winner_frozen.json SHA-256 =
  9302be4e3803318bd55ea40b1e902944b417555198c7f7d0d29a8f742c3fa965. The unseen test runs next, once, as registered.
- **#42 RESULT, 2026-10-21 ~20:15Z: FAIL.**
  - **Discovery** (660 games, 134,400 strategies, taker ask + 1c slippage + fee, 1 contract):
    - **0 of 119,191 eligible strategies had a lower bound > 0.** 1,442 (1.2%) had a positive mean.
    - The median strategy lost about 4.7c per contract.
  - **The winner:**
    - formula: defense-only (w = 0), term-own pace, decay half-life 5 games, k = 20, no league shift, rest
      categories, home/away split on, no shrink;
    - threshold 8 points;
    - discovery: n = 204, 59.3% wins at an average of 52.1c paid, mean +5.5c, LB -1.2c.
  - **Luck check (shared random sides, 200 reps):** the best random-side strategy's LB matched or beat the winner's
    in 53.5% of reps (median max LB -1.0c, 95th percentile +3.5c). **The winner is indistinguishable from luck.**
  - **Unseen, run once** (661 games):
    - 170 bets, **50.0% wins at an average of 52.0c paid, mean -3.7c per contract**, 95% CI [-11.2c, +3.9c],
      -$3.13 per week at 10 contracts;
    - coin flip: 51.8% of random-side copies did as well;
    - pass bar items 1, 2, 4 and 5 FAIL.
  - The unseen half has now been used for game totals.
- **Conclusion for #37-#42:** no box-score formula, in any of about 134,400 combinations, beats Kalshi's T-1h NBA
  total line after costs. The live formula-side picks (#40) are expected to be about a coin flip, minus costs.

### #43 — Bet Kalshi's NBA total toward DraftKings' line when they differ by 2+ (forward) — 2026-10-21
- **Idea:** from the user's question about DraftKings vs Kalshi lines.
- **Exploratory 2025-26** (553 games, sportsbook file, probably closing lines): the lines differ by 2+ in 29% of
  games, and the book's side won 54.7% (CI 47-62.5%).
- **Pre-registered** 2026-10-21 ~20:20Z: reports/output/nba_dk_gap/PREREGISTRATION.md.
  - DraftKings comes from The Odds API (the user's free key), snapshotted when each window opens, alongside Kalshi
    orderbooks.
  - Taker at ask + 1c + fee, 1 contract.
  - Runs until 150 qualifying games or 2027-01-31.
- **Versions:** 1.
- **Also, 2026-10-21:** the user chose to keep $0.50 practice bets (1 contract per game) for #40's window despite
  #42's failure. By hand tonight; from 2026-10-22, the user-started bot after the safety audit.
- **#40 amendment, 2026-10-21 ~20:35Z, before any window game: version 4, GRID WINNER, is added.** It is #42's
  frozen winner applied to 2026-27, betting only when it is 8+ points from Kalshi's line.
  - The user's real practice bets now follow it, at the user's request.
  - Known: 59% on discovery, but 50% on unseen (#42 FAIL).
- **#40 version 4 wired live, 2026-10-21 ~16:40 ET** (the user approved in chat after the classifier blocked a subagent
  edit).
  - The change: scripts/early_overs_picks.py `RULE = "grid_winner"`, using reports/output/nba_totals_grid/tonight.py.
    That file reproduces `project_one` for the frozen spec exactly (max diff 0 on 18 discovery games).
  - The notifier text was updated. 144 tests pass. The grid path itself has no unit test yet (TODO).
  - **First night (16:34 ET):** 1 bet of 11, CHA @ BKN OVER 219.5 (proj 228.2, +8.7) at 50c. The other 10 are no-bet
    (gaps 0.5-6.8).
- **#40 amendment, 2026-10-21 ~20:50Z, before any window game: version 5 is added** (the search winner at 4+ points;
  the user: "lets do 4+ so we can test all forward"). Real bets follow it, and version 4 (8+) is still tracked.
  - Discovery at 4+: 50.2% (n = 424), -3.4c per contract.
