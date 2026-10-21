"""Command line entry point: `kpm <step>` (or `python -m kpm <step>`)."""
from __future__ import annotations

import argparse
import logging
import sys


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kpm", description="Kalshi vs Polymarket NFL gap research (read-only), plus "
                                                         "opt-in live execution (kpm exec, kpm bot)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="create the SQLite schema")
    p = sub.add_parser("nfl", help="step 4: load nflverse schedule/scores/lines")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--season", type=int, help="another season, stored as an untouched 'fresh' test set")
    p = sub.add_parser("kalshi", help="step 2: collect Kalshi markets, candles, trades")
    p.add_argument("--no-trades", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int)
    p = sub.add_parser("polymarket", help="step 3: collect Polymarket markets and price history")
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int)

    p = sub.add_parser("polymarket-us", help="collect Polymarket US NFL markets and 1-min bid/ask history")
    p.add_argument("--force", action="store_true")

    sub.add_parser("match", help="step 5a: rule-based matching + confidence scores")
    p = sub.add_parser("review-export", help="step 5b: write review packets for the AI reviewer")
    p.add_argument("--batch-size", type=int, default=50)
    p = sub.add_parser("review-import", help="step 5b: import AI review JSON results")
    p.add_argument("path", help="JSON file or directory of JSON files")
    p.add_argument("--reviewer", default="claude-code-session")
    p = sub.add_parser("review-api", help="step 5b (live system): run reviews via Claude API [paid]")
    p.add_argument("--limit", type=int)
    sub.add_parser("gate", help="step 5c: approve / route to manual review CSV")
    p = sub.add_parser("manual-import", help="step 5c: apply your approvals from the manual review CSV")
    p.add_argument("path", nargs="?")

    sub.add_parser("gaps", help="step 6: align series and compute after-fee edges")
    sub.add_parser("analyze", help="steps 7-8: analysis + dev/holdout split")
    sub.add_parser("report", help="step 9: write the markdown report and charts")
    sub.add_parser("reverse", help="reverse test: Kalshi as fair value, trade on Polymarket US (+ two-leg arb)")
    sub.add_parser("edge-hunt", help="Kalshi-only arbitrage, drift, price buckets, venue choice, maker vs taker")
    sub.add_parser("crypto-collect", help="collect Kalshi crypto series markets/candles + Coinbase reference prices")
    sub.add_parser("crypto-backtest", help="BTC 15-minute markets vs volatility fair value (hold and scalp)")
    sub.add_parser("crypto-trends", help="trend scan on BTC 15-minute markets (price x time left, momentum, sessions)")
    sub.add_parser("crypto-htf", help="higher-timeframe trend following on BTC 15-minute markets")
    sub.add_parser("crypto-breakout", help="1h/15m swing-high/low breakouts on BTC 15-minute markets")
    p = sub.add_parser("crypto-bot-rules", help="backtest published 'late favorite' bot rules (entry range, stop, take-profit)")
    p.add_argument("--fee-rounding", choices=["cent_ceil", "cent_half_even", "micro_ceil", "none"],
                   help="override the config fee rounding (Kalshi's fills API currently charges exact fees: micro_ceil)")
    p = sub.add_parser("scan", help="live structural mispricing scan of all open Kalshi events (read-only)")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--every", type=int, default=60, help="seconds between scans")
    p = sub.add_parser("final-minute", help="H1: final-minute settlement averaging on BTC 15-minute markets")
    p.add_argument("--collect", action="store_true", help="download final-90-second trade prints first (resumable)")
    p = sub.add_parser("cross-bounds", help="H2: riskless bounds between hourly BTC (KXBTCD) and 15-minute markets")
    p.add_argument("--collect", action="store_true", help="download the nearest hourly strikes' candles first")
    p = sub.add_parser("dashboard", help="localhost dashboard: bot status, live pair pricing, trades, research progress")
    p.add_argument("--port", type=int, help="default: dashboard.port in config")
    p = sub.add_parser("lip-scan", help="H4: liquidity-reward economics of active programs vs today's order books")
    p.add_argument("--top", type=int, default=200, help="programs to check, by reward per day")
    p = sub.add_parser("lip-collect", help="H4 step 2: forward LIP collector (runs until stopped; data/lip_forward.sqlite)")
    p.add_argument("--max-minutes", type=float, help="stop after N minutes (smoke test); default runs forever")
    p = sub.add_parser("crypto-maker", help="resting limit bids on 90-95c favorites (downloads trade prints)")
    p.add_argument("--sample", type=int, default=500, help="signals per period")
    p = sub.add_parser("market-making", help="passive quoting backtest on Kalshi (three fill rules)")
    p.add_argument("--robustness", action="store_true", help="sweep around the chosen setting instead")
    p = sub.add_parser("spread-collect", help="NFL winner-vs-spread study: collect KXNFLSPREAD/KXNFLGAME markets and "
                                              "1-min quotes into data/nfl_spread.sqlite (resumable)")
    p.add_argument("--limit", type=int, help="only this many games (evenly spaced sample)")
    p.add_argument("--force", action="store_true", help="redo quotes already collected")
    p.add_argument("--season", type=int, choices=(2025, 2026))
    p = sub.add_parser("spread-arb", help="NFL winner vs spread arbitrage: pre-registered analysis (data/nfl_spread.sqlite)")
    p.add_argument("--no-fills", action="store_true", help="skip the trade-print fill checks (no verdict)")
    p.add_argument("--db", help="spread database (default data/nfl_spread.sqlite)")
    p.add_argument("--games-db", help="database holding the nflverse games table (default data/kpm.sqlite; read-only)")
    p.add_argument("--out", help="output directory (default reports/output/nfl_spread_arb)")
    p = sub.add_parser("weather-collect", help="weather calibration study: collect settled daily high/low temperature "
                                               "markets + 1-min quotes into data/weather.sqlite (resumable)")
    p.add_argument("--limit", type=int, help="candles for only this many markets (evenly spaced sample)")
    p.add_argument("--series", help="only this series ticker, e.g. KXHIGHNY")
    p.add_argument("--force", action="store_true", help="redo candles already collected")
    p.add_argument("--discover-only", action="store_true", help="series and market metadata only, no candles")
    p = sub.add_parser("weather-calib", help="weather calibration study: pre-registered analysis of data/weather.sqlite "
                                             "(read-only)")
    p.add_argument("--db", help="weather database (default data/weather.sqlite)")
    p.add_argument("--no-fills", action="store_true", help="skip the trade-print size check (no final verdict)")
    p.add_argument("--max-fetch", type=int, default=20000, help="cap on trade-print windows fetched (default 20000)")
    p.add_argument("--out", help="output directory (default reports/output/weather_calibration)")
    sub.add_parser("status", help="row counts and pipeline status")

    # Live execution (kpm.execution). Everything sent to Kalshi goes through kpm.execution.safety's gates; without
    # --live the bots are watch-only and need no keys.
    p = sub.add_parser("exec", help="execution tools: check (read-only), kill (stop everything), reconcile (read-only)")
    esub = p.add_subparsers(dest="exec_cmd", required=True)
    q = esub.add_parser("check", help="read-only health check: config, clock, shards, cash, resting orders; sends no write")
    q.add_argument("--env", choices=("demo", "prod"), help="must equal KALSHI_ENV")
    esub.add_parser("kill", help="write the kill file (every bot stops) and cancel whatever is resting")
    q = esub.add_parser("reconcile", help="reconcile the ledger with the exchange and record settlements (reads only)")
    q.add_argument("--env", choices=("demo", "prod"), help="must equal KALSHI_ENV")
    q = esub.add_parser("record", help="READ-ONLY WebSocket order-book recorder (needs KALSHI_ENV=prod and a key)")
    q.add_argument("--markets", required=True, help="data/ws/<date>/markets.json from `kpm nfl-markets`")
    q.add_argument("--games", nargs="+", help="only these game suffixes (default: all games in the file)")
    q.add_argument("--until", help="stop at this ISO UTC time, e.g. 2026-10-22T03:30:00Z")
    q.add_argument("--trades", action=argparse.BooleanOptionalAction, default=True, help="also record the trade channel")
    q.add_argument("--tag", help="name for logs/ws_recorder-<tag>.heartbeat/.STOP (default: markets file folder under data/ws, '/' -> '-'). Raw data goes in raw/ next to the markets file. Processes started before this change use the old logs/ws_recorder.STOP, which still stops them")
    p = sub.add_parser("nfl-markets", help="list open NFL tickers per game for a date -> data/ws/<date>/markets.json")
    p.add_argument("--date", required=True, help="date code as in event tickers, e.g. 26OCT22")
    p.add_argument("--prefix", default="KXNFL", help="series prefix, e.g. KXNBA (default KXNFL); non-NFL writes data/ws/<sport>/<date>/markets.json")
    p = sub.add_parser("ws-sim", help="thin-market order-book simulator (research log #22 NFL, #23 NBA): raw WebSocket "
                                      "recordings -> reports/output/<sport>_ws/run_<name>")
    p.add_argument("--raw", nargs="+", required=True, help="raw directories (data/ws/<date>/raw), processed in this order")
    p.add_argument("--markets", nargs="+", required=True, help="markets.json files from `kpm nfl-markets`")
    p.add_argument("--sport", choices=("nfl", "nba"), default="nfl", help="study: nfl (#22) or nba (#23, Family A only, no tie)")
    p.add_argument("--out", help="output folder (default reports/output/<sport>_ws/run_default)")
    p.add_argument("--versions", choices=("primary", "all"), default="primary")
    p.add_argument("--no-fetch", action="store_true", help="do not fetch public settlement results (a market without a cached result is dropped whole, X2 exits included)")
    p.add_argument("--no-coin", action="store_true", help="skip the coin-flip second pass")
    p = sub.add_parser("ws-maker", help="maker simulator (research log #26): quote thin win-margin buckets around the deep fair "
                                        "value, strict queue/cross fill model -> reports/output/maker_ws/<sport>_<name>")
    p.add_argument("--sport", choices=("nfl", "nba"), required=True)
    p.add_argument("--raw", nargs="+", required=True, help="raw directories, processed in this order")
    p.add_argument("--markets", nargs="+", required=True, help="markets.json files from `kpm nfl-markets`")
    p.add_argument("--out", help="output folder (default reports/output/maker_ws/<sport>_default)")
    p.add_argument("--versions", choices=("primary", "all"), default="primary")
    p.add_argument("--no-fetch", action="store_true", help="do not fetch public settlement results (unsettled markets are dropped whole)")
    p = sub.add_parser("bot", help="trading bots: watch-only unless every live gate passes")
    bsub = p.add_subparsers(dest="bot_cmd", required=True)
    q = bsub.add_parser("run", help="run a strategy; watch-only (paper trades, no keys, nothing sent) unless --live")
    q.add_argument("--strategy", required=True, choices=("late_favorite", "pair_h2"))
    q.add_argument("--live", action="store_true",
                   help="send real orders. Also needs trading.enabled in settings.yaml, --env prod, KALSHI_ENV=prod "
                        "and the typed confirmation")
    q.add_argument("--env", choices=("demo", "prod"), help="must equal KALSHI_ENV; live trading needs --env prod")
    q.add_argument("--hours", type=float, help="stop after this many hours (pair_h2: hourly windows); default: until stopped")
    q.add_argument("--smoke", action="store_true", help="one public discovery + decision pass, then exit; never trades")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.cmd == "init-db":
        from kpm.core import db
        with db.session():
            pass
    elif a.cmd == "nfl":
        from kpm.collectors import nfl_reference
        nfl_reference.run(refresh=a.refresh, season=a.season)
    elif a.cmd == "kalshi":
        from kpm.collectors import kalshi_history
        kalshi_history.run(with_trades=not a.no_trades, force=a.force, limit=a.limit)
    elif a.cmd == "polymarket":
        from kpm.collectors import polymarket_history
        polymarket_history.run(force=a.force, limit=a.limit)
    elif a.cmd == "polymarket-us":
        from kpm.collectors import polymarket_us_history
        polymarket_us_history.run(force=a.force)
    elif a.cmd == "reverse":
        from kpm.analysis import reverse
        from kpm.reports import build_report
        reverse.run()
        build_report.reverse_report()
    elif a.cmd == "crypto-collect":
        from kpm.collectors import kalshi_series
        kalshi_series.run()
    elif a.cmd == "crypto-backtest":
        from kpm.analysis import crypto15m
        crypto15m.run()
    elif a.cmd == "crypto-trends":
        from kpm.analysis import crypto_trends
        crypto_trends.run()
    elif a.cmd == "scan":
        from kpm.scanner import structural
        res = structural.run(repeat=a.repeat, every_s=a.every)
        if len(res):
            cols = [c for c in ("scan_ts", "kind", "riskless", "event_key", "profit_per_set", "max_sets", "profit_usd",
                                "verified", "verify_note") if c in res.columns]
            print(res[cols].head(40).to_string(index=False))
    elif a.cmd == "crypto-breakout":
        from kpm.analysis import crypto_breakout
        crypto_breakout.run()
    elif a.cmd == "final-minute":
        from kpm.analysis import crypto_final_minute
        if a.collect:
            crypto_final_minute.collect()
        crypto_final_minute.run()
    elif a.cmd == "cross-bounds":
        from kpm.analysis import crypto_cross_bounds
        if a.collect:
            crypto_cross_bounds.collect()
        crypto_cross_bounds.run()
    elif a.cmd == "dashboard":
        from kpm.dashboard import server
        server.serve(port=a.port)
    elif a.cmd == "lip-scan":
        from kpm.analysis import lip_rewards
        lip_rewards.run(top=a.top)
    elif a.cmd == "lip-collect":
        from kpm.collectors import lip_forward
        lip_forward.run(max_minutes=a.max_minutes)
    elif a.cmd == "crypto-bot-rules":
        from kpm.analysis import crypto_bot_rules
        crypto_bot_rules.run(fee_rounding=a.fee_rounding)
    elif a.cmd == "crypto-htf":
        from kpm.analysis import crypto_htf
        crypto_htf.run()
    elif a.cmd == "crypto-maker":
        from kpm.analysis import crypto_maker
        crypto_maker.run(sample_per_split=a.sample)
    elif a.cmd == "market-making":
        from kpm.analysis import market_making
        market_making.robustness() if a.robustness else market_making.run()
    elif a.cmd == "spread-arb":
        from kpm.analysis import spread_arb
        spread_arb.run(db_path=a.db, fills=not a.no_fills, games_db=a.games_db, out_dir=a.out)
    elif a.cmd == "edge-hunt":
        from kpm.analysis import edge_hunt
        edge_hunt.run()
    elif a.cmd == "match":
        from kpm.agents.matcher import pipeline
        pipeline.run_rules()
    elif a.cmd == "review-export":
        from kpm.agents.matcher import pipeline
        pipeline.export_review_packets(batch_size=a.batch_size)
    elif a.cmd == "review-import":
        from kpm.agents.matcher import pipeline
        pipeline.import_reviews(a.path, reviewer=a.reviewer)
    elif a.cmd == "review-api":
        from kpm.agents.matcher import pipeline
        pipeline.run_api_reviews(limit=a.limit)
    elif a.cmd == "gate":
        from kpm.agents.matcher import pipeline
        pipeline.gate()
    elif a.cmd == "manual-import":
        from kpm.agents.matcher import pipeline
        pipeline.import_manual_csv(a.path)
    elif a.cmd == "gaps":
        from kpm.analysis import gaps
        gaps.run()
    elif a.cmd == "analyze":
        from kpm.analysis import run_all
        run_all.run()
    elif a.cmd == "report":
        from kpm.reports import build_report
        build_report.run()
    elif a.cmd in ("exec", "bot"):
        try:
            from kpm.execution import cli as exec_cli
        except ImportError as exc:      # the signing library is an optional extra; research commands never need it
            print(f"kpm {a.cmd} needs the trading extras ({exc.name or exc}): "
                  f"pip install -e \".[trading]\"", file=sys.stderr)
            return 2
        return exec_cli.run_exec(a) if a.cmd == "exec" else exec_cli.run_bot(a)
    elif a.cmd == "nfl-markets":
        from kpm.collectors import nfl_markets
        nfl_markets.run(a.date, prefix=a.prefix)
    elif a.cmd == "ws-sim":
        from kpm.analysis import ws_sim
        a.out = a.out or f"reports/output/{a.sport}_ws/run_default"
        s = ws_sim.run(a.raw, a.markets, a.out, versions=a.versions, fetch=not a.no_fetch, coin=not a.no_coin, sport=a.sport)
        print(f"{s['lines']} lines, {len(s['games'])} games, verdict {s['verdict']['verdict']}, {s['runtime_s']}s -> {a.out}")
        print(s["maker_caveat"])
    elif a.cmd == "ws-maker":
        from kpm.analysis import ws_maker
        a.out = a.out or f"reports/output/maker_ws/{a.sport}_default"
        s = ws_maker.run(a.raw, a.markets, a.out, versions=a.versions, fetch=not a.no_fetch, sport=a.sport)
        print(f"{s['lines']} lines, {len(s['games'])} games, verdict {s['verdict']['verdict']}, {s['runtime_s']}s -> {a.out}")
    elif a.cmd == "spread-collect":
        from kpm.collectors import kalshi_spread
        kalshi_spread.run(limit=a.limit, force=a.force, season=a.season)
    elif a.cmd == "weather-collect":
        from kpm.collectors import kalshi_weather
        kalshi_weather.run(limit=a.limit, series=a.series, force=a.force, discover_only=a.discover_only)
    elif a.cmd == "weather-calib":
        from kpm.analysis import weather_calib
        weather_calib.run(db_path=a.db, fills=not a.no_fills, max_fetch=a.max_fetch, out_dir=a.out)
    elif a.cmd == "status":
        from kpm.core import db
        with db.session() as conn:
            for t in ("games", "markets", "price_history", "trades_1m", "market_matches", "match_reviews",
                      "gaps", "audit_log", "raw_responses"):
                print(f"{t:16s} {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>12,d}")
            for r in conn.execute("SELECT status, COUNT(*) n FROM market_matches GROUP BY status"):
                print(f"  matches[{r['status']}] = {r['n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
