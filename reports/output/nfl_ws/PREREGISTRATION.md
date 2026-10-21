# Pre-registration — research log #22: thin-market stale quotes vs deep twins (NFL, order-book speed)

Written 2026-10-20 ~04:40Z by Opus, **before any game recording was looked at**. The only data seen so far is the
90-second pregame smoke test of 2026-10-20 (`data/ws/26OCT22/raw/msgs-<hour>.jsonl.gz` without a start-epoch suffix).
That file is excluded from every result below. Changes after this point go in an "Amendments" section with a date
and a reason. The original text stays.

## Question

Do Kalshi's thin NFL markets show prices that lag their deep twin markets, by enough to profit after exact fees, at
reaction delays we can achieve (WebSocket feed plus automatic IOC orders)? Is the result better than random entries?

Evidence that prompted it (WAS @ SF, 2026-10-19; see `reports/output/nfl_dip/postgame.*`):
- Win-margin buckets showed real staleness: the thin ask moved toward a flat fair value.
- Protected-prop "fair" values were spiky.
- Gaps lasted a median of 7 s, and 47% of REST-listing signals were phantom.

## Data

- **Source:** read-only WebSocket recordings, `data/ws/<date>/raw/msgs-<YYYYMMDDHH>-<epoch>.jsonl.gz` (orderbook
  snapshots and deltas, public trades, receive timestamps). Books are rebuilt with `kpm.analysis.ws_books`.
- **Games:** every NFL game recorded from 2026-10-22 on, ordered by kickoff.
  - **Discovery** = the first half of games (rounded down).
  - **Unseen** = the rest.
  - The test runs only once at least **6 games** are recorded and at least **3 are unseen**.
- **Windows:** each game from recording start to final settlement. Results are reported for pregame and in-game
  separately, but the pass bar uses the whole window.
- **Exclusions:**
  - Any market from its first seq gap or reconnect until its next snapshot.
  - Any minute in which a fair-value input book is missing or invalid (rules below).
  - Markets that never got a snapshot.
- **Settlement:** the public market result (GET /markets/{ticker}). A market not settled within 48 h of game end is
  dropped and reported.

## Markets and fair value

**Family A, primary: win-margin buckets** (series KXNFLWINMARGIN). Bucket "team by a to b" is read from the market's
own title or strike fields.
- fair = P(team wins by > a-0.5) - P(team wins by > b+0.5).
- These come from the deep game-winner market (KXNFLGAME; tie-adjusted: win = mid - TIE/2) and the spread ladder
  (KXNFLSPREAD at the matching strikes). "15 or more" = P(by > 14.5). TIE = KXNFLTIE.
- If a needed strike is not listed, that bucket is skipped.
- Input validity: every input book has both sides and a spread of 3c or less. A long shot with an empty bid side and
  an ask of 3c or less counts as ask/2.

**Family B, secondary: protected props** (series ending in PROT), YES side only.
- fair = the **median over the trailing 30 s** of the regular twin's mid. The twin must have a spread of 3c or less
  at every sampled update.
- Protected YES is worth at least regular YES, so this fair is a lower bound.

Fair values use only book states received **at or before** the decision time.

## Strategies (every variant counts as a version)

**Taker (T):**
- At decision time t, signal when EV = fair - ask - fee(ask) >= theta, with theta in {1c, 2c}.
- Family A also trades the NO side: EV = bid - fair - fee(1-bid).
- The order arrives at t + L, with L in {0.25 s, 1 s, 5 s}. It is an IOC at the signal-time price. It fills only if
  the thin book at t + L still offers that price or better, at the best available price there.
- Size: count = min(10, contracts offered at arrival, floor($1 / price)).
- One open position per market and side; no adding.

**Exits:**
- **X1:** hold to settlement.
- **X2 (convergence):** the first time t' after entry when thin bid - fee(bid) >= fair(t') - 1c, sell at that bid
  (arrival delay L applies, and the bid must still be there at t' + L). Otherwise hold to settlement. The no-side
  mirror applies.
- No exit ever triggers on fair falling.

**Maker (M), Family A only, exploratory:**
- Rest a YES bid at fair - delta (delta in {1c, 2c}) when it sits inside the spread. Cancel/replace when fair moves
  1c or more; cancels and placements take effect after L in {0.25 s, 1 s}.
- Fill only when public trades at or through our price, after our order is live, exceed the size that was resting
  ahead of us (back of queue).
- Maker fee 0 (KXNFLWINMARGIN fee_type `quadratic`; re-check at test time). Exit X2 with a taker sell.

**Fees:** taker fee per order = ceil(0.07 * C * P * (1-P), to $0.0001), as observed on 15 of 15 real orders on
2026-10-19. Per-contract figures use C = the trade's count.

## Versions

- Family A taker: 2 thetas x 3 latencies x 2 exits = 12.
- Family B taker: 12.
- Family A maker: 2 deltas x 2 latencies = 4.
- **Total: 28 versions** (added to the research log count).

**Primary (named now):** Family A, taker, theta = 1c, L = 1 s, exit X2.

## Pass bar (primary version, all must hold)

1. Mean net P&L per contract > 0 in discovery **and** unseen.
2. Unseen 95% bootstrap lower bound > 0 (resample whole games, 10,000 draws, seed 20261020).
3. At least 100 unseen trades.
4. **Coin flip:** at most 5% of 500 random copies do as well on unseen. Each copy keeps the same markets, the same
   number of entries per game, uniformly random decision times within the same windows, the same side split, and
   the same fill and exit rules. Seed 20261020.
5. At least **$2 per week** projected at real size, using the 2026 NFL slate (about 15 games per week) and the
   unseen per-game average.
6. Fill check: at least 50% of simulated fills also had a public trade at that price or better within the arrival
   window, or the quoted size was 5 contracts or more.

The other 27 versions are reported. A non-primary version that clears the bar counts only as a lead for a new
pre-registration, never as a pass.

## Reported regardless

Markouts at +10 s, +60 s and +300 s (fair and thin mid vs entry); gap lifetimes; phantom rate (signals whose arrival
book no longer qualifies); P&L by latency; pregame vs in-game; and the effect of the 6-game minimum on the CIs.

## Amendments

**2026-10-20 ~05:00Z — clarifications, made while building the simulator, before any game recording was looked
at.** They don't change the rules; they pin down implementation details.
- **Kickoff** = KXNFLGAME expected_expiration_time - 3 h. Kalshi's occurrence/expiration times are about 3 h after
  kickoff (26OCT19WASSF: 03:15Z vs a 00:15Z kickoff). Kickoff only splits pregame from in-game in the reports.
- **Fill check window** (rule 6) = [decision time, arrival time].
- **Coin flip:**
  - It runs in a second pass.
  - Each copy reuses the real unseen primary entries' market, side and count.
  - The decision time is uniform within that market's own first-to-last top-change window, then L = 1 s and X2.
  - "As well" means net per contract >= the real unseen net per contract. A copy with no fills counts as 0.
- **Multi-level fills** pay the fee at the volume-weighted price. This is conservative, since p(1-p) is concave.
- **Signal EV** uses the per-contract fee (C = 1).
- **Invalid fair:** a Family A fair below 0 or above 1 before clamping is invalid and skipped. The TIE bucket is not
  traded.
- **Spread strikes** come from each market's floor_strike, saved by discovery. Legs without one are skipped and
  counted.
- **Bootstrap:** the lower bound is the 2.5th percentile of pooled net per contract, resampling whole unseen games.
  Unseen games with no trades count as 0.

**2026-10-20 ~06:05Z — one added version, before any #22 recording was looked at.**
- The minute-level historical test (#20, FAILED) showed theta = 3c with X2 as its best unseen version. That was
  picked from 8, so it is a lead only.
- Added to Family A taker: theta 3c x L {0.25, 1, 5 s} x exits {X1, X2} = **6 more versions (34 total)**.
- The primary version and the pass bar are **unchanged**.

**2026-10-20 ~08:00Z — clarifications from the Opus code review, before any #22 recording was looked at.**
- The review of `kpm ws-sim` found three bugs (fixed) and several loose spots. Each choice below takes the stricter,
  less optimistic reading.
- The primary version, the 34 versions and the pass bar are **unchanged**.

The clarifications:
- **Unsettled market:** dropped whole, including trades that already exited under X2. This was a bug: only held
  positions were dropped, which favoured X2.
- **Smoke-test file:** the 90-s smoke-test file (no start-epoch suffix) is skipped and listed in the run summary.
- **Invalid input book:** excludes the rest of that minute, from the moment it goes invalid. This is forward only,
  the same as drops.
- **Recorder outages:** a disconnect, a stop, or a gap between recorder processes invalidates every book until its
  next snapshot.
  - Arrivals, exits and coin-flip decisions that fall inside an outage, or after the recording ends, don't fill.
  - A position whose exit didn't fill stays held. Settlement still applies.
- **Fill check (rule 6):** a public trade confirms a simulated fill only if its taker traded the same way we did,
  at our price or better, within [decision, arrival]. A trade on the other side of the spread no longer counts.
  Quoted size of 5 or more still counts.
- **No reuse of recorded liquidity:** after a version's entry fill in a market and side, that version may not enter
  the same market and side again until the thin book's best level on that side has changed (price or size) after
  the fill.
- **Coin flip "count":** means the number of entries. Each random entry is sized by the normal fill rule at its
  random time ("same fill and exit rules", rule 4). The comparison is per contract.
- **Bucket cross-check:** the range read from the ticker must agree with the market's title (yes_sub_title in
  markets.json). If not, the bucket is skipped and counted.
- **Family B twins:** they don't get the long-shot ask/2 rule. Both sides must be present.
- **Settlement timing:** the test runs at least 48 h after the last included game ends. Anything unsettled then is
  dropped whole and reported.
- **Line order:** lines read out of time order must be 0. Otherwise the run is invalid until the cause is fixed.
- **6-game minimum (reported regardless):** shown as the primary's unseen bootstrap lower bound using the first k
  unseen games, for k = 3..all.
- **Maker versions:** they can never pass, and they keep a known optimistic bias. Resting asks that would have
  crossed our bid leave no trade print, and an order that would cross at go-live is dropped instead of filled as a
  taker. The report must say this.
