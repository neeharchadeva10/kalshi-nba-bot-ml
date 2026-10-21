# Kalshi BTC 15-minute up/down markets (KXBTC15M): results

*Produced by `kpm crypto-collect` + `kpm crypto-backtest`. Covers 11,835 markets: June 2026 was used to choose
settings, July 2026 was the test, and Aug 5 – Oct 3, 2026 was fresh confirmation. Fair value comes from Coinbase
1-minute prices and trailing 2-hour volatility, accounts for the 60-second settlement average, and adjusts for
the Coinbase-vs-index gap measured on June (median 1.5 basis points). Trades are 100 contracts with Kalshi's
taker fee.*

| Check | June (choose) | July (test) | Aug–Oct (fresh) |
|---|---|---|---|
| Accuracy 2 min before close (error score, model vs Kalshi; lower is better) | 0.058 vs **0.053** | 0.060 vs **0.053** | 0.087 vs **0.078** |
| Next-minute Kalshi move toward the model (slope) | 0.05 | 0.02 | 0.02 |
| Hold to settlement, chosen setting (gap > 5¢, at least 8 min left) | −0.2¢ (967 trades) | −1.2¢ (1,535) | −0.9¢ (2,998) |
| Scalp: sell after 1 minute, chosen setting (gap > 8¢) | −1.4¢ (330) | −3.0¢ (678) | −3.5¢ (1,466) |

- All 15 hold settings and all 15 scalp settings lost money on June.
- Kalshi's own price was more accurate than the model at every horizon and in every period.

**Verdict:** no edge. These markets (about 1.5M contracts each, spreads under 1¢) are priced faster and better
than a public-data model can manage. Scalping loses about 3¢ per contract after paying the taker fee twice plus
the spread.
