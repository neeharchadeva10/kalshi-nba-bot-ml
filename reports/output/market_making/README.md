# Market-making backtest: results

*Passive quotes on Kalshi NFL game-winner markets, 100 contracts per quote, maker fees, positions flattened
before kickoff with the taker fee. `kpm market-making` is the main grid; `kpm market-making --robustness` is
the sweep below. Settings were chosen on 2025 weeks 1–9 and then frozen. 2025 later weeks and the first 62
games of 2026 were never used for choosing.*

## Chosen setting (improve the spread by 1¢ when it is ≥ 2¢ wide; stay ≥ 1¢ from fair value)

| Data | Games | Contracts filled | Profit | Per contract | Markets profitable |
|---|---|---|---|---|---|
| 2025 weeks 1–9 (chosen here) | 135 | 122,869 | +$1,295 | +1.05¢ | 39% |
| 2025 weeks 10+ and playoffs | 147 | 123,345 | +$569 | +0.46¢ | 45% |
| **2026 weeks 1–4 (fresh)** | **62** | **42,705** | **−$283** | **−0.66¢** | **25%** |

The strict fill rule (fills only when the price trades through) gives the same picture: +$1,211, +$532, −$255.

## Robustness (2025 profit was not a lucky point, but 2026 erased it)

| Safety margin | 2025 weeks 1–9 | 2025 weeks 10+ | 2026 (fresh) |
|---|---|---|---|
| 0.75¢ | +$108 | −$498 | −$791 |
| 1.0¢ (chosen) | +$1,295 | +$569 | −$283 |
| 1.25¢ | +$2,438 | +$1,081 | −$145 |
| 1.5¢ | +$2,839 | +$1,209 | +$22 |
| 2.0¢ | +$3,001 | +$1,307 | −$11 |

Inventory caps of 500 and 2,000 behave like the 1,000 cap.

## Why it stopped working

- **Fills got worse.** After a fill, the fair price had moved about 3¢ in our favor 15 minutes later in 2025.
  In 2026 that dropped to under 1¢.
- **There were fewer chances.** About 20 fills per game in 2026, against 27–29 per game in 2025.
- **Likely cause:** more competition early in the week. This is consistent with Kalshi's NFL markets maturing.

**Verdict:** no edge. The 2025 profit did not carry over to current conditions, so a live system isn't
justified.
