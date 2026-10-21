# Latency measurement (read-only), 2026-10-20 ~19:52 UTC

Run: `KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/latency/measure.py` -> `results.json`.
Only GET /exchange/status, SNTP, local signing timing, and WS subscribe on orderbook_delta (192 KXNBAGAME/SPREAD/TOTAL tickers, 120 s).

**Local clock is ~152.5 ms SLOW** (true = local + 152.5 ms; w32time stopped). time.windows.com -153.7, time.google.com -150.2, pool.ntp.org -151.1 (one garbage pool sample discarded by low-RTT selection). WS delays below are corrected by this offset (uncorrected they would read ~152 ms too low and many would be negative). Offset uncertainty ~ +/-5 ms.

| Item | Result |
|---|---|
| REST warm RTT (30, kept-alive, CloudFront IAD POP, cache hit) | min 15.0 / median 17.8 / p90 26.7 / max 42.8 ms |
| REST cold (5, new TCP+TLS) | min 55 / median 81 / max 280 ms |
| Signing (1000 calls) | mean 0.031 ms, p99 0.045 ms |
| WS feed delay (ts_ms -> receive, offset-corrected, n=89,999) | min 9.9 / p10 16.5 / median 19.6 / p90 47.2 / p99 134 / max 181 ms |
| WS message rate | 752 msg/s (89,999 deltas + 192 snapshots in 120 s) |
| Our processing (json parse + dict update) | mean 0.013 / median 0.009 / p99 0.051 ms |

Delta body keys: delta_fp, market_id, market_ticker, price_dollars, side, ts, ts_ms; envelope also has sending_ts_ms (not used).

## Latency budget (typical = medians)
| Stage | Typical | Basis |
|---|---|---|
| Exchange -> our PC (feed) | ~20 ms (p90 47, p99 134) | measured |
| Our processing + signing | ~0.05 ms | measured |
| Order send -> exchange | ~9 ms | ESTIMATE: half of 17.8 ms REST RTT (CloudFront path, not the order host) |
| Total reaction | ~30 ms typical, ~60 ms p90, ~145 ms p99 | sum, partly estimated |

## Caveats
- REST host api.elections.kalshi.com is CloudFront (108.138.x, IAD55), responses were cache hits, so it measures PC->edge, not matching engine. WS host external-api-ws.kalshi.com resolves to AWS us-east-2 (3.x/18.x IPs); trading-api.kalshi.com also us-east-2. Direct order path RTT was not measured (no order endpoints touched).
- Feed delay depends on the exchange stamping ts_ms accurately and on the 752 msg/s burst load (tail likely queueing/bursts).
- Home-PC RTT of ~15 ms to IAD suggests the PC is already geographically close.

## AWS us-east-2 (ESTIMATE, not measured)
Network legs would drop to roughly 1-3 ms each, so feed delay might fall from ~20 ms to ~2-5 ms median and order send to ~1-2 ms; total reaction ~5-10 ms vs ~30 ms. Tail (p99) gain is the larger win if bursts are network jitter rather than exchange-side batching; unverifiable without a test there.
