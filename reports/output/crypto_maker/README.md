# BTC 15-minute markets: resting limit bids on 90–95¢ favorites

*Produced by `kpm crypto-maker --sample 500`. The signal is the first minute a favorite is priced 90–95¢ with
4–10 minutes left. 500 random signals per period (seeded); fills were simulated from Kalshi trade prints after
the order was placed, until 2 minutes before close; 100 contracts; no maker fee (a sensitivity version with
Kalshi's maker rate changes results by about 0.1¢).*

| Period | Taker at ask | Resting bid, realistic fills | Generous | Strict | Fill rate (realistic) | Favorite win rate: all vs filled |
|---|---|---|---|---|---|---|
| June | −0.3¢ | −0.8¢ | −0.3¢ | −0.7¢ | 85% | 92.6% vs 91.3% |
| July | +1.7¢ | +1.6¢ | +1.9¢ | +1.7¢ | 89% | 94.6% vs 93.9% |
| Aug–Oct | +0.8¢ | +0.5¢ | +0.9¢ | +0.6¢ | 88% | 93.6% vs 92.7% |

All 95% intervals include zero.

- **Fills are adversely selected.** The favorite wins 0.7–1.3 points less often when a resting bid fills.
- **Net effect of limit orders.** They beat taking the ask by about 0.7¢ on the same filled signals. They
  don't beat simply taking every signal.

**Verdict:** no reliable edge. 90–95¢ favorites with 4–10 minutes left are about fairly priced.
