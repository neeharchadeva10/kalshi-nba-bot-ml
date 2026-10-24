# BTC 15-minute markets: following the higher-timeframe trend

*Produced by `kpm crypto-htf`. Uses 11,816 markets. At each window's first minute, a trend signal from Coinbase
closes (past data only) picks a side: buy "Up" at the ask in an uptrend, "Down" in a downtrend. Positions are
held to settlement with Kalshi's taker fee, 100 contracts, one trade per market. June was used for choosing,
July for testing, Aug 5 – Oct 3 for fresh confirmation.*

| Follow signal | Win rate (Jun / Jul / Aug–Oct) | Break-even win rate | Profit per contract (Jun / Jul / Aug–Oct) |
|---|---|---|---|
| 1h trend | 49.8% / 50.2% / 50.1% | ~53% | −3.1¢ / −2.3¢ / −2.6¢ |
| 4h trend | 48.6% / 49.3% / 49.7% | ~52.5% | −3.5¢ / −3.2¢ / −2.7¢ |
| 24h trend | 51.1% / 49.0% / 49.4% | ~52% | −0.9¢ / −2.9¢ / −2.5¢ |
| 1h and 4h agree | 48.9% / 49.6% / 49.9% | ~53% | −3.8¢ / −3.1¢ / −2.9¢ |
| 1h, 4h and 24h agree | 49.6% / 48.9% / 49.0% | ~52.5% | −3.1¢ / −3.6¢ / −3.5¢ |
| Above 50 EMA (15m) | 48.5% / 48.8% / 49.4% | ~52.5% | −3.7¢ / −3.4¢ / −2.9¢ |
| Above 20 EMA (1h) | 49.9% / 49.3% / 49.3% | ~52% | −2.3¢ / −2.7¢ / −2.7¢ |
| Strong 1h trend (>0.5%) | 47.8% / 49.9% / 49.6% | ~53.5% | −6.0¢ / −2.9¢ / −3.8¢ |
| Strong 4h trend (>1%) | 49.6% / 48.3% / 49.6% | ~52.5% | −2.1¢ / −4.3¢ / −2.9¢ |

All 9 trend-following signals lost money in every period. The best June variant (fading strong 1h trends)
went +1.7¢ → −1.4¢ → −0.5¢.

**Why:** the next 15 minutes are essentially a coin flip whatever the bigger trend (win rates of 48–51%), and
Kalshi already prices the trend side slightly above 50¢. You'd need about a 52–53% win rate just to cover the
fee and spread.

## Swing highs and lows (market structure) on 5m / 15m / 1h line charts

A chart is in an uptrend when its last two confirmed swings show higher highs and higher lows (downtrend: lower
highs and lower lows). A swing is a close above (or below) the 2 bars on each side, counted only once those
bars have printed.

| Follow signal (entry at window start) | Win rate (Jun / Jul / Aug–Oct) | Profit per contract |
|---|---|---|
| 5m + 15m + 1h all agree | 44.7% / 42.2% / 50.0% | −6.0¢ / −8.0¢ / −1.9¢ |
| 5m + 15m agree | 47.0% / 47.8% / 47.4% | −3.2¢ / −3.4¢ / −4.2¢ |
| 15m + 1h agree | 48.0% / 47.1% / 48.3% | −3.9¢ / −4.3¢ / −3.7¢ |
| 1h only | 50.5% / 48.8% / 48.9% | −1.7¢ / −3.0¢ / −3.4¢ |

Every swing-based follow rule lost money in every period. Fading them came closer to break-even but still
didn't hold up out of sample.

## Win rate isn't the goal: price paid matters

"5m, 15m and 1h all up, enter 5 minutes into the window" **won 73–74% of the time**. But the trend side then
cost about 72–74¢, so the break-even win rate was 74–76% and it still lost (−3.2¢ / −0.3¢ / −0.2¢). The
market already prices the trend in.
