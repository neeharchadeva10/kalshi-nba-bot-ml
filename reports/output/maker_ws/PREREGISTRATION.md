# Pre-registration — research log #26: be the maker in thin win-margin buckets (NBA primary, NFL secondary)

Written 2026-10-20 ~20:00Z by Opus at the user's direction ("i like maker one"), **before any WebSocket recording
was opened**. The recordings for #22/#23 are still unseen. This test uses the same recordings, so its versions are
added to the count alongside theirs. Changes go in "Amendments" with a date.

## Question

Can we earn money by **posting** prices instead of taking them? We would quote on both sides of thin Kalshi
win-margin buckets around a fair value taken from the deep winner and spread markets. Makers pay no fee there
(fee_type `quadratic`; re-check at test time). The test must use a fill model that counts the bad fills as well
as the good ones.

Lessons carried in:
- **Market making #4:** deep NFL winner markets were +1c per contract in 2025 and -0.66c in 2026 as competition grew,
  and post-fill markouts shrank. So this test targets thin markets and measures markouts.
- **#20:** the maker version was about 0 at minute level, but candles can't show queue position. The order-book
  recordings can.

## Data

- **Recordings:** read-only WebSocket recordings (`data/ws/nba/<date>/raw/`, `data/ws/<date>/raw/` for NFL),
  replayed by `kpm.analysis.ws_books` / `kpm ws-sim`, with every rule of the #22 08:00Z amendment:
  - outages invalidate books;
  - the smoke-test file is skipped;
  - unsettled markets are dropped whole;
  - each raw folder is its own stream;
  - line order must be 0, or the run is invalid.
- **Games and split:**
  - NBA: every game recorded from 2026-10-20 on, ordered by scheduled start (occurrence or expected expiration
    minus 3 h).
  - Discovery = the first half, unseen = the rest.
  - The NBA test runs at **20 or more games with 10 or more unseen**. The NFL test runs at 6 or more games with 3 or
    more unseen.
- **Fair value:** exactly as #22 (NFL, with the tie adjustment) and #23 (NBA, no tie). Fair uses only book states at
  or before the decision time. Input validity, the long-shot rule, skips and invalid minutes are as in those
  pre-registrations.

## Strategy (per bucket market)

- **Quote:**
  - a YES bid at floor-to-cent(fair - delta);
  - with two-sided quoting, also a NO bid at floor-to-cent((1 - fair) - delta). That is a YES ask at
    ceil-to-cent(fair + delta).
  - Size 10 per quote.
- **No crossing:** a quote that would cross the opposite best price is not placed; we never take.
- **Requote** when fair moves by 1c or more, or after a fill. Placements and cancels take effect L = 1 s after the
  decision.
- **Inventory:** the net YES position in a market stays between -10 and +10. A quote that could breach it is cut to
  fit.
- **Exit:** hold everything to settlement. A YES fill plus a NO fill in the same market pays exactly $1 per pair.
- **No quotes** while any fair input is invalid. Live quotes are cancelled at once (after L) when fair turns
  invalid, and in the last 60 s before a market closes.

## Fill model (strict)

- Our order joins the **back of the queue** at its price when it goes live. The queue ahead is the resting size at
  that price at that moment.
- **Trade fill:** public trades at our price, or through it on our side, after we are live reduce the queue ahead.
  We fill with what is left.
- **Cross fill:** if the opposite best price moves to or through our price while we are live, we count as filled at
  our price for the full remaining size. This catches the bad fills that leave no trade print, closing #22's
  known maker gap.
- Maker fee = 0. No exit fee, since we hold to settlement.

## Versions

- delta in {1c, 2c, 3c} x {two-sided, bid-only (YES bids only)} = 6 per sport, **12 in total**.
- **Primary (named now):** NBA, delta = 2c, two-sided.

## Pass bar (primary; all must hold)

1. Mean net P&L per filled contract > 0 in discovery **and** unseen.
2. Unseen 95% bootstrap lower bound > 0. Resample whole games, 10,000 draws, seed 20261020; unseen games without
   fills count as 0.
3. At least 100 unseen filled contracts.
4. **Information check:** the same strategy quoting around the thin market's own mid instead of the deep fair
   (same delta, the same rules) must do worse on unseen. Point estimate of primary minus naive > 0, with its
   per-game bootstrap CI reported.
5. At least **$2 per week** projected at size 10, using unseen per-game P&L and about 50 NBA games per week.
6. **Markouts reported and not fatal:** the mean fair-value markout 60 s after fills must not be worse than
   -2c per contract. Worse than that means our quotes are being picked off.

The other 11 versions are reported. A non-primary version that clears the bar is a lead for a new
pre-registration, never a pass.

## Reported regardless

- Fills per game.
- Share of trade fills vs cross fills.
- Markouts at +10, +60 and +300 s and at settlement.
- Inventory over time.
- Pairs completed (a YES and a NO both filled).
- Settlement P&L by bucket type.
- NBA vs NFL.
- Comparison with #22/#23's maker versions.

## What happens after a pass

- **Paper trading:** the same code runs live, read-only, with simulated orders, for 1 week.
- **Then a live micro-test:** 1-2 contracts per quote, with a hard loss cap.
- The live step needs the user's explicit setup. A resting-order bot is autonomous, and Claude Code's safety check
  requires the user to allow it.

## Amendments

(none)
