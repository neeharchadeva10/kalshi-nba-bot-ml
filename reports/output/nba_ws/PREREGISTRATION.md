# Pre-registration — research log #23: NBA win-margin buckets vs deep twins (order-book speed)

Written 2026-10-20 ~05:10Z by Opus. The NBA recorder started at 05:05Z; **no NBA recording or NBA price has been
looked at**. The only NBA facts used come from the read-only scout (reports/output/nfl_ws/other_sports_scout.md),
which saw quotes on a few buckets:
- 8 buckets per game (1-5, 6-10, 11-20, 21+ per team), no tie market;
- most buckets had no YES bid;
- KXNBAWINMARGIN fee_type is `quadratic` (maker fee 0), KXNBAGAME/KXNBASPREAD are `quadratic_with_maker_fees`.

This mirrors #22 (reports/output/nfl_ws/PREREGISTRATION.md), including its 2026-10-20 clarifications. Only the
differences are listed. Amendments go at the bottom.

## Differences from #22

- **Markets:** KXNBAWINMARGIN buckets only (Family A). There are no protected props: the NBA PROT series have no
  events.
- **Fair value:** for bucket "team by a to b": fair = P(team by > a-0.5) - P(team by > b+0.5).
  - P(team by > 0.5) = the KXNBAGAME mid for that team. NBA games can't tie, so there is no tie adjustment.
  - The other legs are KXNBASPREAD at the matching floor_strike (5.5, 10.5, 20.5). "21 or more" = P(by > 20.5).
  - A bucket with a missing strike is skipped.
  - Validity rules are as in #22: both sides, spread of 3c or less, the long-shot ask/2 rule, and the
    outside-[0,1]-before-clamping rule.
- **Games:** every NBA game recorded from 2026-10-20 on, ordered by tip-off (from occurrence_datetime; unverified, and
  used only for ordering and the pregame/in-game report).
  - Discovery = the first half (rounded down); unseen = the rest.
  - The test runs only once at least **20 games** are recorded and at least **10 are unseen**.
- **Projected $/week:** uses the number of NBA games on Kalshi in the unseen weeks (about 50 per week per the
  scout), not the NFL 15.
- **Versions:**
  - Taker: theta {1c, 2c} x L {0.25, 1, 5 s} x exits {X1, X2} = 12.
  - Maker: delta {1c, 2c} x L {0.25, 1 s} = 4.
  - **16 versions.** Primary: taker, theta = 1c, L = 1 s, X2.
- **Pass bar:** identical to #22, rules 1-6 (seed 20261020, 10,000 bootstrap draws by game, 500 coin-flip copies,
  at least 100 unseen trades, $2/week, fill check).
- **Code:** `kpm ws-sim` must be generalized to the KXNBA series and the no-tie rule before the test. Any such change
  is made and unit-tested on synthetic data only, before the NBA recordings are opened.

## Amendments

**2026-10-20 ~08:05Z — adopt #22's 08:00Z clarifications, before any NBA recording was looked at.**
- The clarifications from the Opus code review in reports/output/nfl_ws/PREREGISTRATION.md apply here as well:
  - unsettled markets are dropped whole;
  - outages invalidate books;
  - the fill check needs a same-side taker;
  - recorded liquidity can't be reused;
  - the bucket title cross-check;
  - line order must be 0;
  - the k-unseen-games CI report;
  - maker versions can never pass.
- The Family B items don't apply, because #23 has no Family B.
- The versions, primary and pass bar are unchanged.
