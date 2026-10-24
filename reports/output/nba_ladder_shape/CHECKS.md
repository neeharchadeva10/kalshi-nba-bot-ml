# CHECKS (research log #61) - discovery dates only

Events in window 2025-10-21..2026-01-22: 664

## 1. T-1h quotes: 1-minute table vs hourly table

Hourly candle = latest hourly candle ending in [D-30min, D], D = T - 60 min (ends exactly at D for T on the hour, at D-30min for T on the half hour). The 1-minute quote is the last 1-minute candle ending at or before THAT hourly end_ts.

- Strikes compared: 7268 in 664 games
- Median |mid diff|: 0.0000
- Share within 1c: 1.0000
- Share within 3c: 1.0000
- Strikes more than 3c apart: 0; games excluded from both tables: 0

## 2. Strike-result consistency with hoopR finals

- consistent: 7281; inconsistent: 2; strikes without a hoopR final (game unmatched): 22; strikes without a yes/no result: 0

| event | ticker | strike | hoopR final | mkts.result |
|---|---|---|---|---|
| KXNBATOTAL-25NOV17OKCNOP | KXNBATOTAL-25NOV17OKCNOP-232 | 232.5 | 229 | yes |
| KXNBATOTAL-25NOV17OKCNOP | KXNBATOTAL-25NOV17OKCNOP-229 | 229.5 | 229 | yes |
