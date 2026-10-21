# cheapleg - research log #33 simulator

Does the cheap leg of a "fake lock" win more often than its price? Pre-registration: `PREREGISTRATION.md`.
Read-only replay of the WebSocket recordings; public GET market data only; no order code.

## Run

    .venv/Scripts/python.exe reports/output/cheapleg/cheapleg.py --raw <dir> [<dir> ...] --markets <markets.json> [...]         --out <outdir> [--label source|test] [--no-fetch] [--no-coin] [--live-events <events.jsonl>]

- `--raw` dirs and `--markets` files: one per recorder folder (e.g. 26OCT21 and 26OCT21b); streams are merged by recording time as in `ws_sim`.
- Metadata (strike types, mutually_exclusive, results) via cached public GETs in `cache/`. An event is refetched on each run until all its markets are settled/finalized; `--no-fetch` uses the cache only.
- `--label test` adds the discovery/unseen split (first ceil(N/2) games by scheduled start = discovery), the gate check and the rule 1-5 verdict. `--label source` only reports.
- Tests: `.venv/Scripts/python.exe -m pytest reports/output/cheapleg -q` (21 pass).

Outputs: `results.json` (config, input files and sizes, replay counters, every version's stats, bootstrap CIs, coin flips, breakdowns by price bucket / market type / kind / phase, fills per game, coverage, live-fill reproduction), `fills.csv` (one row per deduped lock event per version, including refire-skipped rows and misses), `run.log`.

## Implementation notes

- Books: `ws_books.BookSet` per recorder folder (sequence gap or connect/disconnect/stop invalidates until next snapshot; synthetic stop at each folder's last line, as in `ws_sim`). `lockcore.Detector` is driven on every touched ticker (top changes and newly dropped books), exactly as `lockwatch_night` does. The doc contains only the recorded tickers (what the recorder subscribed to, like the bot's discovery).
- Lock event: `lock_open` only, count-1 profit (payout - sum(price + fee)) >= $0.01, then `lockcore.dedupe` (50 ms), then a 60 s refire guard per (ticker, side) per version where "already bought" means a previous filled attempt on that leg. No max-legs limit (the live bot used 3).
- IOC: pending checks at t+L run before any message with time > t+L (a message at exactly t+L is applied first).
- Scheduled start = (occurrence_datetime, else expected_expiration_time) - 3 h. See ambiguity note below.
- Coin flip: per-second reservoir sample (cap 300 per game/phase/price tick/side) of every valid-book ticker side priced 1-99c; a copy replaces each real fill by a candidate within 1c (either side), weighted by true sample counts; unsettled candidates dropped.

## Source night 26OCT20 (does NOT count toward the pass bar)

3 games (BOSDET, PHINYK, OKCSAS), 1,437 recorded tickers (API lists 1,780 for these events; the rest were listed after the recorder started), 89.6M lines, runtime 41 min.
588 lock_open events, 527 below $0.01 at count 1, 61 candidates, 50 after dedupe (ladder 39, me_no 11, me_yes 0). All locks fell in-game; 0 unsettled, 0 void.

| version | lock events | fills | fill rate | mean P&L/fill | win rate vs mean price | 95% CI (games) | coin share >= real | total P&L |
|---|---|---|---|---|---|---|---|---|
| cheapest-L10 | 43 | 31 | 0.72 | +0.0204 | 0.097 vs 0.073 (+fee 0.0038) | [-0.094, +0.261] | 0.240 | +0.63 |
| cheapest-L40 | 44 | 21 | 0.48 | +0.0697 | 0.143 vs 0.070 (+fee 0.0036) | [-0.074, +0.476] | 0.054 | +1.46 |
| stale-L10 | 47 | 20 | 0.43 | +0.0329 | 0.600 vs 0.561 (+fee 0.0061) | [+0.010, +0.053] | 0.134 | +0.66 |
| stale-L40 | 49 | 9 | 0.18 | +0.0659 | 0.444 vs 0.373 (+fee 0.0052) | [-0.017, +0.132] | 0.202 | +0.59 |
| trigger-L10 | 46 | 29 | 0.63 | +0.0502 | 0.241 vs 0.187 (+fee 0.0043) | [-0.018, +0.261] | 0.052 | +1.46 |
| trigger-L40 | 47 | 20 | 0.43 | +0.0662 | 0.250 vs 0.179 (+fee 0.0048) | [-0.052, +0.476] | 0.052 | +1.32 |

(lock events = deduped events not blocked by the refire guard; the CI resamples 3 games, so it is nearly meaningless here. $/week for the primary at 50 games/week = $10.52, from 3 games.)

Primary (cheapest, 10 ms): 31 fills, 3 wins, mean price 0.073, win rate 0.097. By price bucket: 1-2c 17 fills 1 win; 3-5c 6 fills 1 win; 6-10c 2 fills 0; 11-25c 3 fills 0; 26-50c 3 fills 1. The gain comes from one 5c prop leg paying $1.

## Reproduction of the live bot's 13 cheap fills (26OCT20, attempt legs with fill_count 1.00 and price <= 0.10)

Matching window +-2 s. Negative dt = our lock_open precedes the live attempt (decision plus send latency, clock offset).

| attempt time | ticker | side | live px | lock events in window | nearest dt (ms) | our cheapest leg | sim fill 10ms | sim fill 40ms |
|---|---|---|---|---|---|---|---|---|
| 01:15:13.906 | KXNBA3Q...CT20PHINYK-PHI | NO | 0.01 | 1 | -64.3 | True | True | True |
| 01:56:45.049 | KXNBATOTAL...CT20PHINYK-217 | YES | 0.04 | 1 | -1196.8 | True | False | True |
| 01:57:21.123 | KXNBATEAMTOTAL...0PHINYK-PHI110 | YES | 0.02 | 1 | -60.6 | True | True | True |
| 02:00:06.171 | KXNBATOTAL...CT20PHINYK-207 | YES | 0.07 | 7 | -57.2 | True | True | True |
| 02:00:09.587 | KXNBATEAMTOTAL...0PHINYK-NYK101 | YES | 0.01 | 1 | -13.4 | True | True | True |
| 02:00:09.859 | KXNBA2HTOTAL...OCT20PHINYK-95 | YES | 0.05 | 1 | -34.0 | True | True | True |
| 02:00:15.248 | KXNBA4QSPREAD...T20PHINYK-NYK2 | YES | 0.01 | 1 | -65.4 | True | True | True |
| 02:03:46.977 | KXNBARACE...0OKCSAS-10-SAS | NO | 0.02 | 0 | None | None | None | None |
| 02:55:47.286 | KXNBA1HTOTAL...CT20OKCSAS-111 | YES | 0.01 | 2 | -58.2 | True | True | True |
| 03:48:04.308 | KXNBA3QTOTAL...OCT20OKCSAS-63 | YES | 0.05 | 1 | -108.3 | True | True | True |
| 03:52:54.606 | KXNBAREB...CHAMPAGNIE30-4 | YES | 0.04 | 0 | None | None | None | None |
| 03:53:04.407 | KXNBAREB...KCCHOLMGREN7-6 | YES | 0.02 | 1 | -77.5 | True | True | True |
| 04:21:24.495 | KXNBAPTS...SDVASSELL24-10 | YES | 0.01 | 0 | None | None | None | None |

10 of 13 have a matching lock event (dt -13 to -108 ms except one at -1197 ms); in all 10 the leg was our cheapest leg; simulated fills: 9 at 10 ms, 10 at 40 ms. 3 have no lock event in our replay (RACE NO 0.02, REB 0.04, PTS 0.01).

## Coverage, exclusions, caveats

- Coverage per game in `results.json` (`coverage`): recorded 463 / 457 / 517 tickers, all with book events; API lists 585 / 589 / 606. 2 recorded tickers per game are not returned by the API (KXNBARACE events; /events/ returned an HTTP error for them), so they count as unsettled but no fill used them.
- Unsettled exclusions: 0 fills. Void: 0.
- Scheduled start sanity check: settlement minus inferred start is 2.6 / 3.0 / 3.1 h for the three games.
- Only 50 deduped events in the night; every rate above rests on tens of fills.
