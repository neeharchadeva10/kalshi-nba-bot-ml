# Pre-registration — research log #28: does Polymarket lead Kalshi in-game? (NBA, single-leg)

Written 2026-10-21 ~01:55Z by Opus, at the user's direction ("lets do that for the nba"). **No paired recording exists
yet.** The only prior look was a 3-minute manual sample of 26OCT20PHINYK (see research log #25/#27 notes):
- 47% of 2-s samples had a mid gap of 1c or more (max 3c);
- 9 crossed moments, 2 positive after the Kalshi fee (+0.3c, +0.45c).

That sample is excluded from every result. Changes go in "Amendments" with a date.

## Question

During NBA games, when Polymarket's price for a team moves first, does Kalshi follow often and far enough that ONE
Kalshi taker order in the same direction is profitable after Kalshi's fees? This is single-leg only: no Polymarket
account and no multi-leg fills.

## Data

- **Recorder:** a read-only recorder on the Ohio server (us-east-2) logs, on ONE clock (Amazon Time Sync):
  - Kalshi WebSocket top-of-book changes for KXNBAGAME and KXNBASPREAD;
  - Polymarket CLOB WebSocket (public market channel) top-of-book changes for the matching NBA moneyline and spread
    markets.
- **Games:** every NBA game recorded from 26OCT21 on, ordered by scheduled start. In-game window = scheduled start +
  10 min until the Kalshi moneyline reaches 97c or more (or 3c or less) on either side.
- **Split:** discovery = the first half of games, unseen = the rest. The test runs at **30 or more games with 15 or
  more unseen**.
- **Exclusions:**
  - seconds where either venue's book is one-sided, crossed, or has had no update for more than 10 s;
  - Polymarket markets that can't be matched to a Kalshi market at the same line.

## Signal and trades

- **Mid:** (best bid + best ask) / 2 per venue, in the same team's YES terms. Polymarket outcome prices are mapped to
  that team.
- **Signal at time t:** Polymarket mid rose by theta or more over the last 2 s, while Kalshi mid rose by less than
  theta/2 over the same 2 s. The mirror for falls.
- **Trade:**
  - Buy YES on Kalshi (falls: buy NO) at the Kalshi ask in force at t + L.
  - Count = min(10, ask size at t + L).
  - IOC at the ask seen at t; it fills only if the ask at t + L is that price or better.
- **Exits:**
  - X1: sell at the Kalshi bid when Kalshi mid is within 1c of Polymarket mid, or after 30 s, whichever comes first.
    Sells use the same arrival delay L.
  - X2: hold to settlement.
- **Fees:** Kalshi taker fee per order, ceil(0.07 * C * P * (1-P), to $0.0001), at entry and at an X1 exit.
- **Concurrency:** one open position per market; no re-entry for 30 s after an exit.

## Versions

- theta in {2c, 3c} x L in {0.05 s, 0.5 s} x exit in {X1, X2} = **8 versions**.
- **Primary (named now):** theta 2c, L 0.05 s, X1.
- Reported but not traded: the reverse case (Kalshi leads Polymarket).

## Pass bar (primary; all must hold)

1. Mean net P&L per contract > 0 in discovery **and** unseen.
2. Unseen 95% bootstrap lower bound > 0 (resample whole games, 10,000 draws, seed 20261021).
3. At least 100 unseen trades.
4. **Coin flip:** at most 5% of 500 copies do as well on unseen. Each copy uses the same games, the same number of
   entries per game and the same exits; entry times are uniform within the in-game windows and the side is random.
   Seed 20261021.
5. At least **$2 per week** at count 10, using about 50 NBA games per week.
6. Fill realism: at least 80% of simulated entries had ask size of 10 or more at arrival, or the count was cut to the
   size shown.

The other 7 versions are reported. Any that clear the bar are leads for a new pre-registration only.

## Reported regardless

- Lead-lag: cross-correlation of 1-s mid changes; the share of moves of 2c or more that each venue led; the lag
  distribution.
- Gap lifetime.
- Moneyline vs spread.
- By quarter.

## Amendments

(none)
