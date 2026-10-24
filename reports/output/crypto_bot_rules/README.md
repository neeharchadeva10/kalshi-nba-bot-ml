# Published bot rules on Kalshi BTC 15-minute markets (`kpm crypto-bot-rules`)

## Source and rules

Source: "Kalshi Trading Bot v5", ProcessOverProfit (code.processoverprofit.blog, video 14). Its YouTube video was
watched with the `watch` skill.

The rules are taken from the published code:
- In the last 8 minutes of each window, buy the side (UP or DOWN) whose ask is in the entry range, at most once per
  window.
- Sell if that side's ask falls below 40c (stop-loss) or reaches the fill price + 10c (take-profit).
- Otherwise hold to settlement.

## Data and fills

Data: 11,835 KXBTC15M markets, 2026-06-01 to 2026-10-04, from 1-minute candles.

Two fill models bracket the bot's sub-second polling:
- `close`: rules checked on minute-close quotes.
- `intraminute`: also uses each minute's extreme quotes.

## Fees

The bot's own logs in the video (Aug 11, 2026) show Kalshi's fills API charging the exact fee formula. For example:
- $0.1078 for 10 contracts at 81c.
- $0.0551 for 10 contracts at about 91c.

These are not rounded up to the cent per order. The table below therefore uses exact fees (`micro_ceil`). The earlier
per-order cent rounding (`cent_ceil`, the project default) overstated the cost of 1-contract orders: 2c instead of 1.1c
at 80c. `exact_fee_check.csv` has both versions.

## Results

1 contract per trade, exact fees, all periods. Each cell is `close / intraminute`.

| Rule set | Trades | Win rate (needed) | Mean per trade | June / Jul / Aug-Oct (close) |
|---|---|---|---|---|
| v5 live default: 80-93c, stop 40c, tp +10c | 9,898 | 85.6% (86.6%) / 84.8% (89.3%) | -0.8c / -3.1c | -0.9 / -0.1 / -1.1 |
| v5 paper default: 75-85c, stop 40c, tp +10c | 7,828 | 81.1% (83.3%) / 81.6% (89.2%) | -1.6c / -4.6c | -2.2 / -0.9 / -1.6 |
| Video demo's BTC setting: 80-93c, stop 40c, hold | 9,898 | 83.5% (84.1%) / 80.1% (82.6%) | -0.5c / -1.8c | -0.4 / +0.2 / -1.0 |
| Favorites only: 85-95c, no stop, hold | 9,776 | 90.2% (90.1%) | +0.05c (CI -0.55, +0.66) | +0.9 / +0.3 / -0.5 |

Findings:
- Both published defaults lose in every period under both fill models.
- The stop-loss and the take-profit make results worse, because each early exit pays another fee plus the spread.
  - With 80-93c entries held to settlement: -0.3c per trade.
  - Adding the 40c stop: -0.5c / -1.8c.
  - Adding the +10c take-profit: -0.6c / -1.8c.
- Holding 85-95c favorites to settlement is break-even after fees, and its result fades over time.

Delta filter (the bot's `Delta`, off by default), applied to 80-93c entries with the 40c stop, held to settlement:
- **$25 or $50:** no help.
- **$100:** 2,612 trades, +0.7c / -0.1c per trade (CI -0.1 to +1.6 / -1.0 to +0.8).
  - By period (close / intraminute): June +1.2c / 0.0c, Jul 1 - Aug 4 -1.7c / -2.3c, Aug 5 - Oct 4 +1.1c / +0.5c.
  - It loses in one of the three periods, its confidence intervals include zero, and it was chosen after looking at 22
    variants. It is not established. It is only worth testing forward on new data.

The video's live demo was one 15-minute window (2:15-2:30 AM ET, Aug 11, 2026):
- BTC: 10 DOWN at 91c, held to settlement, +$0.80.
- ETH: 10 DOWN at 81c, sold at 98c, +$1.55.

In the backtest, about 60% of one-hour stretches contain only winning trades, so a short winning demo is the normal
case for this strategy.

## Files

- `results.csv` and `trades_v5_defaults.csv`: the full grid of 22 rule sets at exact fees. They were produced by
  `kpm crypto-bot-rules --fee-rounding micro_ceil`, and the `fee_rounding` column records the setting.
- `exact_fee_check.csv`: exact vs cent-rounded fees at 1 and 10 contracts.
- `summary.json`
