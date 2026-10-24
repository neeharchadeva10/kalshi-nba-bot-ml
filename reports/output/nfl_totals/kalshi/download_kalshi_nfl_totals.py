"""Read-only Kalshi NFL totals data download + integrity checks (research log #66).

Public market-data GETs only, through kpm.clients.kalshi.KalshiClient / CachedClient (forbid_trading_paths on,
every response cached on disk under data/raw/kalshi).  No auth, no kpm.execution.  No betting-outcome statistics
(no line-vs-final hit rates, no P&L): only data-integrity checks.

Stages (all resumable; candles keep a done-table, done.jsonl):
  series  -> series_meta.json            GET /series/<S> (fee type / multiplier / settlement source)
  markets -> markets.parquet, events.parquet (raw pages in raw/markets/<utc stamp>/)
  candles -> candles_1h.parquet, candles_1m.parquet (raw per-market json in raw/candles_*/)
  check   -> CHECK.md, check.json, coverage.parquet

Examples
  smoke test (<=50 requests):  python download_kalshi_nfl_totals.py --smoke
  full run:                    python download_kalshi_nfl_totals.py --not-before-utc 2026-10-24T02:00:00Z
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from kpm.clients.http import HttpError  # noqa: E402
from kpm.clients.kalshi import KalshiClient  # noqa: E402
from kpm.core.timeutil import et_to_utc_ts, parse_iso  # noqa: E402

log = logging.getLogger("nfl_totals_dl")

HERE = Path(__file__).resolve().parent
SERIES_ALL = ["KXNFLTOTAL", "KXNFLTEAMTOTAL", "KXNFL1HTOTAL", "KXNFL2HTOTAL"]
PRIMARY = "KXNFLTOTAL"
GAMES_CSV = ROOT / "data" / "raw" / "nflverse" / "games" / "games.csv"
HOURLY_LEAD_CAP_S = 10 * 86400        # hourly candles start at max(market open, kickoff - 10 days)
MIN_LEAD_S = 6 * 3600                 # 1-minute candles start at kickoff - 6h
POST_KICK_S = 15 * 60                 # both end at kickoff + 15 min
EXPECTED_FEE = ("quadratic", 1)       # the task's reference: quadratic with 0.07 multiplier (multiplier field is 1 -> x0.07)

# Kalshi event-ticker team codes that differ from nflverse team codes (derived from the data and listed in README).
KALSHI_TO_NFLVERSE = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS", "STL": "LA", "SD": "LAC", "OAK": "LV"}
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_EVENT_RE = re.compile(r"^(KX[A-Z0-9]+)-(\d{2})([A-Z]{3})(\d{2})(\d{4})?([A-Z]+)$")


# ----------------------------------------------------------------------------------------------- budget / time
class BudgetExhausted(RuntimeError):
    pass


class RequestBudget:
    """Counts real HTTP requests (cache hits do not reach the session) and refuses once the cap is reached."""

    def __init__(self, cap: int | None):
        self.cap, self.n = cap, 0

    def install(self, client: KalshiClient) -> None:
        sess = client.http._session
        orig = sess.get

        def counted(*a, **kw):
            if self.cap is not None and self.n >= self.cap:
                raise BudgetExhausted(f"request cap {self.cap} reached")
            self.n += 1
            return orig(*a, **kw)

        sess.get = counted


def wait_until(iso: str | None, now_fn=time.time, sleep_fn=time.sleep, chunk_s: float = 60.0) -> float:
    """Sleep until the ISO UTC time (before any network work). Returns seconds slept (0 if already past/None)."""
    if not iso:
        return 0.0
    target = parse_iso(iso)
    slept = 0.0
    while True:
        remaining = target - now_fn()
        if remaining <= 0:
            return slept
        step = min(remaining, chunk_s)
        log.info("waiting for %s: %.0f s remaining", iso, remaining)
        sleep_fn(step)
        slept += step


# ----------------------------------------------------------------------------------------------- team codes / events
def build_team_map(observed: set[str], nfl_codes: set[str]) -> tuple[dict[str, str], list[str]]:
    """Kalshi code -> nflverse code. Identity when the code exists in nflverse, else the explicit alias."""
    out, bad = {}, []
    for c in sorted(observed):
        if c in nfl_codes:
            out[c] = c
        elif KALSHI_TO_NFLVERSE.get(c) in nfl_codes:
            out[c] = KALSHI_TO_NFLVERSE[c]
        else:
            bad.append(c)
    return out, bad


def parse_event(event_ticker: str) -> dict | None:
    """'KXNFLTOTAL-26OCT22NECHI' -> series, ET gameday '2026-10-22', block 'NECHI', game_key '26OCT22NECHI'."""
    m = _EVENT_RE.match(event_ticker or "")
    if not m or m.group(3) not in MONTHS:
        return None
    series, yy, mon, dd, hhmm, block = m.groups()
    return {"series": series, "gameday": f"20{yy}-{MONTHS[mon]:02d}-{dd}", "block": block,
            "game_key": f"{yy}{mon}{dd}{block}", "hhmm": hhmm}


def split_block(block: str, valid: set[str]) -> list[tuple[str, str]]:
    """All ways to read the team block as <away><home> with both parts in `valid` (Kalshi codes)."""
    return [(block[:i], block[i:]) for i in range(2, len(block) - 1)
            if block[:i] in valid and block[i:] in valid and block[:i] != block[i:]]


def kickoff_utc(gameday: str, gametime) -> int | None:
    """nflverse gameday + gametime are US Eastern wall-clock; DST handled by zoneinfo."""
    if gametime is None or (isinstance(gametime, float) and gametime != gametime) or str(gametime).strip() == "":
        return None
    return et_to_utc_ts(gameday, str(gametime).strip())


def load_games(path: Path = GAMES_CSV) -> pd.DataFrame:
    g = pd.read_csv(path, dtype={"gametime": str})
    g = g[g["season"] >= 2024].copy()
    g["kickoff_utc"] = [kickoff_utc(d, t) for d, t in zip(g["gameday"], g["gametime"])]
    return g.reset_index(drop=True)


def match_event(event_ticker: str, games: pd.DataFrame, valid_kalshi: set[str], tmap: dict[str, str]) -> dict:
    """Match a Kalshi event to an nflverse game: same ET date (then +-1 day) and the same unordered team pair."""
    pe = parse_event(event_ticker)
    res = {"event_ticker": event_ticker, "status": "unparsed", "game_id": None}
    if not pe:
        return res
    res.update(game_key=pe["game_key"], gameday_ticker=pe["gameday"])
    splits = split_block(pe["block"], valid_kalshi)
    if not splits:
        res["status"] = "bad_team_block"
        return res
    day = datetime.strptime(pe["gameday"], "%Y-%m-%d")
    for off, method in ((0, "exact_date"), (-1, "date-1"), (1, "date+1")):
        d = (day + pd.Timedelta(days=off)).strftime("%Y-%m-%d")
        sub = games[games["gameday"] == d]
        hits = []
        for a, h in splits:
            pair = {tmap[a], tmap[h]}
            for _, r in sub.iterrows():
                if {r["away_team"], r["home_team"]} == pair:
                    hits.append((a, h, r))
        if len(hits) == 1:
            a, h, r = hits[0]
            res.update(status="matched", method=method, game_id=r["game_id"], season=int(r["season"]),
                       week=int(r["week"]), game_type=r["game_type"], gameday=r["gameday"],
                       kickoff_utc=None if r["kickoff_utc"] is None or pd.isna(r["kickoff_utc"]) else int(r["kickoff_utc"]),
                       k_away=a, k_home=h, away_team=r["away_team"], home_team=r["home_team"],
                       order_ok=(tmap[a] == r["away_team"] and tmap[h] == r["home_team"]),
                       away_score=r["away_score"], home_score=r["home_score"], overtime=r["overtime"])
            return res
        if len(hits) > 1:
            res["status"] = "ambiguous"
            return res
    res["status"] = "no_nflverse_game"
    res["k_away"], res["k_home"] = splits[0]
    return res


# ----------------------------------------------------------------------------------------------- settlement check
def bracket_check(strikes: list[tuple[float, str]], total: float | None) -> dict:
    """strikes: (floor_strike, result) for 'greater' markets (YES iff total > floor). Result must bracket `total`:
    total > max floor settled YES  and  total <= min floor settled NO.  Pure data-integrity check."""
    yes = [s for s, r in strikes if r == "yes"]
    no = [s for s, r in strikes if r == "no"]
    hi_yes = max(yes) if yes else None
    lo_no = min(no) if no else None
    out = {"max_yes": hi_yes, "min_no": lo_no, "n_yes": len(yes), "n_no": len(no),
           "monotonic": not (yes and no and hi_yes >= lo_no), "bracket_ok": None, "implied_range": [hi_yes, lo_no]}
    if total is not None:
        out["bracket_ok"] = bool((hi_yes is None or total > hi_yes) and (lo_no is None or total <= lo_no))
    return out


def exp_value_flags(rows: list[tuple[float, str, object]], total: float) -> list[dict]:
    """expiration_value is NOT always the final total: markets that close early on a YES carry the running total at
    that moment.  Consistency rule used here: numeric value; result yes -> floor < value <= final total;
    result no -> value == final total.  Returns the markets that break the rule (data-integrity only)."""
    bad = []
    for floor, res, ev in rows:
        if ev in (None, ""):
            bad.append({"floor": floor, "result": res, "expiration_value": ev, "why": "empty"})
            continue
        try:
            v = float(ev)
        except (TypeError, ValueError):
            bad.append({"floor": floor, "result": res, "expiration_value": ev, "why": "non-numeric"})
            continue
        if res == "yes" and not (floor < v <= total):
            bad.append({"floor": floor, "result": res, "expiration_value": ev, "why": "yes: not in (floor, total]"})
        elif res == "no" and v != total:
            bad.append({"floor": floor, "result": res, "expiration_value": ev, "why": "no: != total"})
    return bad


# ----------------------------------------------------------------------------------------------- io helpers
def save_gz(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh)
    tmp.replace(path)


def load_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def first(d: dict, name: str):
    for k in (name, f"{name}_dollars", f"{name}_fp"):
        if d.get(k) is not None:
            return d[k]
    return None


# ----------------------------------------------------------------------------------------------- stage: series
def stage_series(client: KalshiClient, out: Path, series: list[str]) -> dict:
    meta = {}
    for s in series:
        try:
            d = client.series(s)
        except HttpError as e:
            meta[s] = {"error": str(e)}
            continue
        meta[s] = d
    save_gz(out / "raw" / "series_meta.json.gz", meta)      # untouched API responses
    flags = {}
    for s, d in meta.items():
        if "error" in d:
            flags[s] = d["error"]
            continue
        notes = []
        if (d.get("fee_type"), d.get("fee_multiplier")) != EXPECTED_FEE:
            notes.append(f"fee_type={d.get('fee_type')!r} multiplier={d.get('fee_multiplier')!r} "
                         f"differs from plain 'quadratic' x1 (0.07 coefficient)")
        info = ((d.get("product_metadata") or {}).get("important_info") or {}).get("markdown", "")
        d["_includes_overtime_text"] = info or "(no overtime statement in series metadata; see per-market rules_primary)"
        flags[s] = notes
    (out / "series_meta.json").write_text(json.dumps({"series": meta, "fee_flags": flags}, indent=1), encoding="utf-8")
    return {"series": meta, "fee_flags": flags}


# ----------------------------------------------------------------------------------------------- stage: markets
def fetch_markets(client: KalshiClient, series: str, historical: bool, rawdir: Path, first_page_only=False) -> list[dict]:
    path = "/historical/markets" if historical else "/markets"
    tier = "historical" if historical else "live"
    rows, cursor, n = [], None, 0
    while True:
        page = client.http.get(path, {"series_ticker": series, "limit": 1000, "cursor": cursor}, use_cache=historical)
        save_gz(rawdir / f"{series}_{tier}_p{n:03d}.json.gz", page)
        for m in page.get("markets", []):
            m["_tier"] = tier
            rows.append(m)
        n += 1
        cursor = page.get("cursor")
        if not cursor or first_page_only:
            return rows


MARKET_COLS = ["ticker", "series", "event_ticker", "game_key", "ticker_suffix", "tier", "away_code", "home_code",
               "floor_strike", "cap_strike", "strike_type", "open_time", "close_time", "expected_expiration_time",
               "occurrence_datetime", "expiration_time", "latest_expiration_time", "created_time", "settlement_ts",
               "status", "result", "expiration_value", "settlement_value_dollars", "settlement_value",
               "volume_fp", "volume", "open_interest_fp", "open_interest", "rules_primary"]


def market_row(m: dict, series: str) -> dict:
    pe = parse_event(m.get("event_ticker", "")) or {}
    row = {c: m.get(c) for c in MARKET_COLS if c in m}
    row.update(ticker=m["ticker"], series=series, game_key=pe.get("game_key"), tier=m.get("_tier"),
               ticker_suffix=m["ticker"].rsplit("-", 1)[-1],
               floor_strike=num(m.get("floor_strike")), cap_strike=num(m.get("cap_strike")),
               settlement_value=num(m.get("settlement_value_dollars", m.get("settlement_value"))),
               volume=num(m.get("volume_fp", m.get("volume"))),
               open_interest=num(m.get("open_interest_fp", m.get("open_interest"))))
    # any other occurrence-like field the API returns
    for k, v in m.items():
        if "occ" in k.lower() and k not in row:
            row[k] = v
    row["raw_json"] = json.dumps({k: v for k, v in m.items() if k != "_tier"}, sort_keys=True)
    return row


def build_tables(markets_by_series: dict[str, list[dict]], games: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    rows, dups = {}, []
    for s, ms in markets_by_series.items():
        for m in ms:
            if m["ticker"] in rows:
                dups.append({"ticker": m["ticker"], "tiers": [rows[m["ticker"]]["tier"], m.get("_tier")]})
                if m.get("_tier") != "live":        # keep the live copy (fresher); historical is the fallback
                    continue
            rows[m["ticker"]] = market_row(m, s)
    mk = pd.DataFrame(list(rows.values()))
    if mk.empty:
        return mk, pd.DataFrame(), {"map": {}, "unmapped": [], "duplicates": dups}
    # Kalshi codes seen in event tickers -> nflverse
    nfl_codes = set(games["away_team"]) | set(games["home_team"])
    candidates = set(KALSHI_TO_NFLVERSE) | nfl_codes
    tmap, _ = build_team_map(candidates, nfl_codes)
    valid = set(tmap)
    evs = []
    for et, grp in mk.groupby("event_ticker"):
        r = match_event(et, games, valid, tmap)
        r["series"] = grp["series"].iloc[0]
        r["n_markets"] = len(grp)
        evs.append(r)
    ev = pd.DataFrame(evs)
    mk = mk.merge(ev[["event_ticker", "game_id", "status"]].rename(columns={"status": "match_status"}),
                  on="event_ticker", how="left")
    away = ev.set_index("event_ticker")
    mk["away_code"] = mk["event_ticker"].map(away.get("k_away"))
    mk["home_code"] = mk["event_ticker"].map(away.get("k_home"))
    used = set(ev.get("k_away", pd.Series(dtype=str)).dropna()) | set(ev.get("k_home", pd.Series(dtype=str)).dropna())
    observed_map = {k: tmap[k] for k in sorted(used)}
    return mk, ev, {"map": observed_map, "unmapped": [], "duplicates": dups}


def stage_markets(client, out: Path, series: list[str], games: pd.DataFrame, first_page_only=False) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rawdir = out / "raw" / "markets" / stamp
    cut = client.cutoff()
    save_gz(out / "raw" / "cutoff.json.gz", cut)
    by_series = {}
    for s in series:
        ms = []
        for hist in (False, True):       # live listing + historical tier (immutable pages are cached by CachedClient)
            try:
                ms += fetch_markets(client, s, hist, rawdir, first_page_only)
            except HttpError as e:
                log.warning("%s historical=%s: %s", s, hist, e)
        by_series[s] = ms
    mk, ev, info = build_tables(by_series, games)
    mk.to_parquet(out / "markets.parquet", index=False)
    ev.to_parquet(out / "events.parquet", index=False)
    (out / "team_map.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return {"n_markets": len(mk), "n_events": len(ev), "cutoff": cut, **info}


# ----------------------------------------------------------------------------------------------- stage: candles
def candle_windows(open_ts: int | None, kickoff: int) -> dict[str, tuple[int, int] | None]:
    """Hourly: [max(open, kickoff-10d), kickoff+15m]; 1-minute: [kickoff-6h, kickoff+15m]. None if empty."""
    end = kickoff + POST_KICK_S
    out = {}
    h0 = kickoff - HOURLY_LEAD_CAP_S if open_ts is None else max(open_ts, kickoff - HOURLY_LEAD_CAP_S)
    out["1h"] = (h0, end) if h0 < end else None
    out["1m"] = (kickoff - MIN_LEAD_S, end)
    if open_ts is not None and open_ts >= end:
        out["1m"] = None
    return out


def flatten_candle(c: dict) -> dict:
    row = {}
    for k, v in c.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                row[f"{k}_{k2}"] = v2
        else:
            row[k] = v
    for grp in ("yes_bid", "yes_ask", "price"):
        d = c.get(grp) or {}
        for stat in ("open", "high", "low", "close", "mean", "previous", "min", "max"):
            row[f"{grp}_{stat}_f"] = num(first(d, stat))
    row["volume_f"] = num(first(c, "volume"))
    row["open_interest_f"] = num(first(c, "open_interest"))
    return row


def fetch_candles(client: KalshiClient, series: str, ticker: str, start: int, end: int, interval: int,
                  tier: str) -> tuple[list[dict], str]:
    hist = tier == "historical"
    try:
        return client.candlesticks(series, ticker, start, end, interval, hist), tier
    except HttpError as e:
        if e.status != 404:
            raise
        other = not hist           # tier drift around the cutoff: try the other endpoint
        return client.candlesticks(series, ticker, start, end, interval, other), ("historical" if other else "live")


def load_done(out: Path) -> dict[str, dict]:
    p = out / "done.jsonl"
    done = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[f"{r['ticker']}|{r['kind']}"] = r
    return done


def mark_done(out: Path, rec: dict) -> None:
    with open(out / "done.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def stage_candles(client, out: Path, markets: pd.DataFrame, events: pd.DataFrame, now: int,
                  only_tickers: list[str] | None = None, passes: int = 3, pass_sleep_s: float = 60.0,
                  sleep_fn=time.sleep) -> dict:
    ev = events.set_index("event_ticker")
    done = load_done(out)
    stats = Counter()
    todo = []
    for r in markets.itertuples():
        if only_tickers is not None and r.ticker not in only_tickers:
            continue
        e = ev.loc[r.event_ticker] if r.event_ticker in ev.index else None
        ko = None if e is None or e.get("status") != "matched" or pd.isna(e.get("kickoff_utc")) else int(e["kickoff_utc"])
        if ko is None:
            for kind in ("1h", "1m"):
                if f"{r.ticker}|{kind}" not in done:
                    mark_done(out, {"ticker": r.ticker, "kind": kind, "status": "no_kickoff"})
            stats["no_kickoff"] += 1
            continue
        if ko + POST_KICK_S + 3600 > now:
            stats["not_yet_played"] += 1
            continue
        w = candle_windows(parse_iso(r.open_time), ko)
        for kind in ("1h", "1m"):
            if f"{r.ticker}|{kind}" in done:
                stats["already_done"] += 1
                continue
            todo.append((r.ticker, r.series, r.tier, kind, w[kind], ko))
    for p in range(passes):
        failed = []
        for ticker, s, tier, kind, win, ko in todo:
            if win is None:
                mark_done(out, {"ticker": ticker, "kind": kind, "status": "empty_window", "kickoff": ko})
                stats["empty_window"] += 1
                continue
            try:
                cs, used = fetch_candles(client, s, ticker, win[0], win[1], 60 if kind == "1h" else 1, tier)
            except BudgetExhausted:
                raise
            except Exception as exc:        # network/HTTP error after CachedClient's own retries: retry next pass
                log.warning("candles %s %s failed: %s", ticker, kind, exc)
                failed.append((ticker, s, tier, kind, win, ko))
                continue
            save_gz(out / "raw" / f"candles_{kind}" / f"{ticker}.json.gz",
                    {"ticker": ticker, "tier": used, "start": win[0], "end": win[1], "kickoff": ko,
                     "interval_min": 60 if kind == "1h" else 1, "candlesticks": cs})
            mark_done(out, {"ticker": ticker, "kind": kind, "status": "ok", "n": len(cs), "tier": used})
            stats["fetched"] += 1
        todo = failed
        if not todo:
            break
        log.warning("pass %d: %d candle requests failed; sleeping %.0fs", p + 1, len(todo), pass_sleep_s)
        sleep_fn(pass_sleep_s)
    stats["still_failed"] = len(todo)
    return dict(stats)


def build_candle_parquets(out: Path) -> dict:
    n = {}
    for kind in ("1h", "1m"):
        rows = []
        for f in sorted((out / "raw" / f"candles_{kind}").glob("*.json.gz")):
            d = load_gz(f)
            for c in d["candlesticks"]:
                r = {"ticker": d["ticker"], "end_ts": int(c["end_period_ts"]), "tier": d["tier"]}
                r.update(flatten_candle(c))
                rows.append(r)
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values(["ticker", "end_ts"]).reset_index(drop=True)
        df.to_parquet(out / f"candles_{kind}.parquet", index=False)
        n[kind] = len(df)
    return n


# ----------------------------------------------------------------------------------------------- stage: check
def two_sided(bid, ask) -> bool:
    return bid is not None and ask is not None and not pd.isna(bid) and not pd.isna(ask) and 0 < bid < 1 and 0 < ask < 1 and bid <= ask


def coverage_table(markets: pd.DataFrame, events: pd.DataFrame, c1h: pd.DataFrame) -> pd.DataFrame:
    ev = events.set_index("event_ticker")
    rows = []
    mk = markets[markets["series"] == PRIMARY]
    for et, grp in mk.groupby("event_ticker"):
        e = ev.loc[et]
        if e.get("status") != "matched" or pd.isna(e.get("kickoff_utc")):
            continue
        ko = int(e["kickoff_utc"])
        cs = c1h[c1h["ticker"].isin(grp["ticker"])] if not c1h.empty else c1h
        r = {"event_ticker": et, "game_id": e["game_id"], "kickoff_utc": ko, "n_strikes": len(grp),
             "strike_min": grp["floor_strike"].min(), "strike_max": grp["floor_strike"].max(),
             "first_candle_ts": int(cs["end_ts"].min()) if len(cs) else None,
             "last_candle_ts": int(cs["end_ts"].max()) if len(cs) else None,
             "n_strikes_with_candles": int(cs["ticker"].nunique()) if len(cs) else 0}
        for lab, sec in (("24h", 86400), ("3h", 10800), ("1h", 3600)):
            t = ko - sec
            ex = ff = 0
            for tk, g in (cs.groupby("ticker") if len(cs) else []):
                hit = g[(g["end_ts"] <= t) & (g["end_ts"] > t - 3600)]
                if len(hit) and two_sided(hit["yes_bid_close_f"].iloc[-1], hit["yes_ask_close_f"].iloc[-1]):
                    ex += 1
                prior = g[g["end_ts"] <= t]
                if len(prior) and two_sided(prior["yes_bid_close_f"].iloc[-1], prior["yes_ask_close_f"].iloc[-1]):
                    ff += 1
            r[f"share_2sided_exact_{lab}"] = ex / len(grp)     # a candle in the hour ending at/before T-x is two-sided
            r[f"share_2sided_ffill_{lab}"] = ff / len(grp)     # last candle at/before T-x is two-sided
        rows.append(r)
    return pd.DataFrame(rows)


def stage_check(out: Path, games: pd.DataFrame, series_meta: dict | None, now: int) -> dict:
    mk = pd.read_parquet(out / "markets.parquet")
    ev = pd.read_parquet(out / "events.parquet")
    chk: dict = {"generated_utc": datetime.fromtimestamp(now, timezone.utc).isoformat()}
    chk["series_meta"] = {s: {"fee_type": d.get("fee_type"), "fee_multiplier": d.get("fee_multiplier"),
                              "overtime_text": d.get("_includes_overtime_text")}
                          for s, d in (series_meta or {}).items() if isinstance(d, dict)}
    chk["n_markets_by_series"] = mk["series"].value_counts().to_dict()
    chk["strike_types"] = mk.groupby("series")["strike_type"].value_counts().rename("n").reset_index().to_dict("records")
    chk["match_status_by_series"] = ev.groupby(["series", "status"]).size().rename("n").reset_index().to_dict("records")
    chk["unmatched_events"] = ev[ev["status"] != "matched"][["event_ticker", "series", "status", "n_markets"]].to_dict("records")
    m = ev[ev["status"] == "matched"]
    chk["order_mismatch_events"] = m[~m["order_ok"].astype(bool)][["event_ticker", "away_team", "home_team", "k_away", "k_home"]].to_dict("records")
    chk["match_method_counts"] = m["method"].value_counts().to_dict()
    # counts by season/week (events, primary series) and games per season vs nflverse
    prim = m[m["series"] == PRIMARY]
    chk["events_by_season_week"] = prim.groupby(["season", "week"]).size().rename("n_events").reset_index().to_dict("records")
    chk["markets_by_season_week"] = (mk.merge(prim[["event_ticker", "season", "week"]], on="event_ticker")
                                     .groupby(["season", "week"]).size().rename("n_markets").reset_index().to_dict("records"))
    gps = {}
    if len(prim):
        lo, hi = prim["gameday"].min(), prim["gameday"].max()
        for season, g in games.groupby("season"):
            gg = g[(g["gameday"] >= lo) & (g["gameday"] <= hi)]
            if gg.empty:
                continue
            have = set(prim["game_id"])
            gps[int(season)] = {"nflverse_games_in_kalshi_date_range": len(gg),
                                "with_primary_event": int(gg["game_id"].isin(have).sum()),
                                "missing_game_ids": gg[~gg["game_id"].isin(have)]["game_id"].tolist()}
    chk["games_per_season_vs_nflverse"] = gps
    spg = mk[mk["series"] == PRIMARY].groupby("event_ticker").size()
    chk["strikes_per_game_primary"] = ({"min": int(spg.min()), "median": float(median(spg)), "max": int(spg.max()),
                                        "n_games": int(len(spg))} if len(spg) else {})
    # duplicates
    chk["duplicate_tickers_in_table"] = int(mk["ticker"].duplicated().sum())
    d2 = mk[mk.duplicated(["event_ticker", "floor_strike", "strike_type", "ticker_suffix"], keep=False) & mk["floor_strike"].notna()]
    chk["duplicate_event_strike_rows"] = int(len(d2))
    d3 = mk[mk.duplicated(["event_ticker", "floor_strike", "strike_type"], keep=False)
            & (mk["series"] == PRIMARY) & mk["floor_strike"].notna()]
    chk["duplicate_primary_event_strike_rows"] = int(len(d3))
    chk["cross_tier_duplicates"] = (json.loads((out / "team_map.json").read_text())["duplicates"]
                                    if (out / "team_map.json").exists() else [])
    # settlement bracket vs nflverse final (primary series, SETTLED games only)
    dis, ev_dis, checked, ot_checked, n_ev_bad, n_empty = [], [], 0, 0, 0, 0
    evi = m.set_index("event_ticker")
    for et, grp in mk[mk["series"] == PRIMARY].groupby("event_ticker"):
        if et not in evi.index:
            continue
        e = evi.loc[et]
        st = grp[grp["result"].isin(["yes", "no"]) & (grp["strike_type"] == "greater")]
        if len(st) == 0 or pd.isna(e["home_score"]) or pd.isna(e["away_score"]) or len(st) != len(grp):
            continue            # not (fully) settled or no final score yet
        total = float(e["home_score"] + e["away_score"])
        b = bracket_check(list(zip(st["floor_strike"], st["result"])), total)
        evf_all = exp_value_flags(list(zip(st["floor_strike"], st["result"], st["expiration_value"])), total)
        n_empty += sum(1 for x in evf_all if x["why"] == "empty")
        evf = [x for x in evf_all if x["why"] != "empty"]
        checked += 1
        ot_checked += int(e["overtime"] == 1)
        n_ev_bad += len(evf)
        if (not b["bracket_ok"]) or (not b["monotonic"]):
            dis.append({"event_ticker": et, "game_id": e["game_id"], "nflverse_total": total,
                        "overtime": int(e["overtime"]), **b})
        if evf:
            ev_dis.append({"event_ticker": et, "game_id": e["game_id"], "nflverse_total": total,
                           "overtime": int(e["overtime"]), "bad_markets": evf[:6], "n_bad": len(evf)})
    chk["settlement_bracket"] = {"events_checked": checked, "of_which_overtime": ot_checked,
                                 "n_disagreements": len(dis), "disagreements": dis,
                                 "expiration_value_empty_markets": n_empty,
                                 "expiration_value_rule_violating_markets": n_ev_bad,
                                 "expiration_value_violating_events": ev_dis}
    # candle coverage
    c1h = pd.read_parquet(out / "candles_1h.parquet") if (out / "candles_1h.parquet").exists() else pd.DataFrame()
    c1m = pd.read_parquet(out / "candles_1m.parquet") if (out / "candles_1m.parquet").exists() else pd.DataFrame()
    done = load_done(out)
    elig = {k.split("|")[0] for k, v in done.items() if v["status"] == "ok"}
    have1h = set(c1h["ticker"]) if len(c1h) else set()
    have1m = set(c1m["ticker"]) if len(c1m) else set()
    fetched = {t for t in elig}
    empty = {k.split("|")[0] for k, v in done.items() if v["status"] == "empty_window"}
    chk["candles"] = {"markets_fetched_ok": len(fetched), "markets_empty_window(opened after kickoff+15m)": len(empty),
                      "markets_fetched_but_no_1h_candles": sorted(fetched - have1h)[:200],
                      "markets_fetched_but_no_1m_candles": sorted(fetched - have1m)[:200],
                      "n_1h_rows": int(len(c1h)), "n_1m_rows": int(len(c1m)),
                      "markets_never_attempted_or_no_kickoff": int(len(set(mk["ticker"]) - {k.split('|')[0] for k in done}))}
    cov = coverage_table(mk, ev, c1h)
    cov.to_parquet(out / "coverage.parquet", index=False)
    chk["coverage_summary"] = ({c: float(cov[c].mean()) for c in cov.columns if c.startswith("share_")} if len(cov) else {})
    (out / "check.json").write_text(json.dumps(chk, indent=1, default=str), encoding="utf-8")
    (out / "CHECK.md").write_text(render_check_md(chk), encoding="utf-8")
    return chk


def render_check_md(c: dict) -> str:
    L = ["# KXNFL totals data integrity check", f"generated {c['generated_utc']}", "",
         "Data-integrity only. No line-vs-outcome statistics are computed here.", "", "## Series metadata"]
    for s, d in c["series_meta"].items():
        L.append(f"- {s}: fee_type={d['fee_type']} fee_multiplier={d['fee_multiplier']}; overtime text: {d['overtime_text']}")
    L += ["", "## Markets by series", *(f"- {k}: {v}" for k, v in c["n_markets_by_series"].items()), "",
          "## Event to nflverse match", *(f"- {r['series']} {r['status']}: {r['n']}" for r in c["match_status_by_series"]),
          f"- unmatched events: {len(c['unmatched_events'])}"]
    L += [f"  - {r['event_ticker']} ({r['status']}, {r['n_markets']} markets)" for r in c["unmatched_events"][:60]]
    L += [f"- away/home order differs from nflverse: {len(c['order_mismatch_events'])}",
          *(f"  - {r['event_ticker']}" for r in c["order_mismatch_events"][:30]), "",
          "## Games per season vs nflverse (within the Kalshi date range)"]
    for s, d in c["games_per_season_vs_nflverse"].items():
        L.append(f"- {s}: nflverse {d['nflverse_games_in_kalshi_date_range']}, with {PRIMARY} event "
                 f"{d['with_primary_event']}; missing: {d['missing_game_ids']}")
    L += ["", f"## Strikes per game ({PRIMARY})", str(c["strikes_per_game_primary"]), "",
          "## Events by season/week (primary)", *(f"- {r['season']} wk{r['week']}: {r['n_events']}" for r in c["events_by_season_week"]),
          "", "## Duplicates", f"- duplicate tickers: {c['duplicate_tickers_in_table']}",
          f"- duplicate (event, strike) rows: {c['duplicate_event_strike_rows']} (primary {c['duplicate_primary_event_strike_rows']})",
          f"- tickers present in both tiers: {len(c['cross_tier_duplicates'])}", "",
          "## Settlement bracket vs nflverse final (settled primary games)"]
    sb = c["settlement_bracket"]
    L += [f"- events checked: {sb['events_checked']} (overtime games among them: {sb['of_which_overtime']})",
          f"- disagreements: {sb['n_disagreements']}"]
    for d in sb["disagreements"]:
        L.append(f"  - {d['event_ticker']} nflverse total {d['nflverse_total']} OT={d['overtime']} implied "
                 f"{d['implied_range']} bracket_ok={d['bracket_ok']} monotonic={d['monotonic']}")
    L += [f"- markets with empty expiration_value: {sb['expiration_value_empty_markets']}",
          f"- expiration_value rule violations (markets): {sb['expiration_value_rule_violating_markets']} "
          f"in {len(sb['expiration_value_violating_events'])} events (rule: yes -> floor<v<=total, no -> v==total)"]
    for d in sb["expiration_value_violating_events"][:60]:
        L.append(f"  - {d['event_ticker']} total {d['nflverse_total']} OT={d['overtime']} n_bad={d['n_bad']} e.g. {d['bad_markets'][:2]}")
    cd = c["candles"]
    L += ["", "## Candles", *(f"- {k}: {v}" for k, v in cd.items()), "", "## Coverage (mean over games)",
          *(f"- {k}: {v:.3f}" for k, v in c["coverage_summary"].items())]
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------------------------- smoke
def run_smoke(client, out: Path, games: pd.DataFrame, now: int) -> None:
    sm = stage_series(client, out, SERIES_ALL)
    print("SERIES META")
    for s, d in sm["series"].items():
        print(f"  {s}: fee_type={d.get('fee_type')} fee_multiplier={d.get('fee_multiplier')} "
              f"settlement_sources={d.get('settlement_sources')} scope={(d.get('product_metadata') or {}).get('scope')}")
        print(f"     overtime text: {d.get('_includes_overtime_text')}")
        print(f"     flags: {sm['fee_flags'][s]}")
    info = stage_markets(client, out, [PRIMARY], games, first_page_only=True)
    print("MARKETS", info["n_markets"], "events", info["n_events"], "cutoff", info["cutoff"])
    print("TEAM MAP (observed in smoke page)", info["map"])
    mk = pd.read_parquet(out / "markets.parquet")
    ev = pd.read_parquet(out / "events.parquet")
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_colwidth", 60)
    print(mk.drop(columns=["raw_json", "rules_primary"]).head(3).T)
    print(ev.head(5).T)
    played = ev[(ev["status"] == "matched") & (ev["season"] == 2026) & (ev["kickoff_utc"] < now - 86400)]
    games_sorted = played.sort_values("kickoff_utc")
    pick = games_sorted.iloc[len(games_sorted) // 2]["event_ticker"]
    g = mk[mk["event_ticker"] == pick].sort_values("floor_strike")
    tick = g["ticker"].iloc[len(g) // 2: len(g) // 2 + 2].tolist()
    print("candles for", pick, tick)
    st = stage_candles(client, out, mk, ev, now, only_tickers=tick)
    print("candle stage", st, "parquet rows", build_candle_parquets(out))
    c1h = pd.read_parquet(out / "candles_1h.parquet")
    c1m = pd.read_parquet(out / "candles_1m.parquet")
    print("1h rows", len(c1h)); print(c1h.drop(columns=[c for c in c1h.columns if c.endswith("_f")]).tail(2).T)
    print("1m rows", len(c1m)); print(c1m.drop(columns=[c for c in c1m.columns if c.endswith("_f")]).tail(2).T)
    chk = stage_check(out, games, sm["series"], now)
    print("check: matched/unmatched", chk["match_status_by_series"], "bracket", {k: v for k, v in chk["settlement_bracket"].items() if k != "disagreements"})


# ----------------------------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(HERE))
    ap.add_argument("--stages", default="series,markets,candles,check")
    ap.add_argument("--series", default=",".join(SERIES_ALL))
    ap.add_argument("--smoke", action="store_true", help="tiny test: series meta, first market pages, 2 markets' candles")
    ap.add_argument("--not-before-utc", default=None, help="ISO time; sleep until then before any network work")
    ap.add_argument("--max-requests", type=int, default=None, help="hard cap on real HTTP requests (cache hits are free)")
    ap.add_argument("--passes", type=int, default=3, help="retry passes over failed candle fetches")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = Path(a.out_dir) / ("smoke" if a.smoke else "")
    out.mkdir(parents=True, exist_ok=True)
    wait_until(a.not_before_utc)
    budget = RequestBudget(a.max_requests if a.max_requests is not None else (50 if a.smoke else None))
    client = KalshiClient()
    budget.install(client)
    games = load_games()
    now = int(time.time())
    code = 0
    try:
        if a.smoke:
            run_smoke(client, out, games, now)
        else:
            stages = a.stages.split(",")
            series = a.series.split(",")
            sm = None
            if "series" in stages:
                sm = stage_series(client, out, series)
                for s, f in sm["fee_flags"].items():
                    if f:
                        log.warning("FEE FLAG %s: %s", s, f)
            if "markets" in stages:
                log.info("markets: %s", {k: v for k, v in stage_markets(client, out, series, games).items() if k != "map"})
            if "candles" in stages:
                mk = pd.read_parquet(out / "markets.parquet")
                ev = pd.read_parquet(out / "events.parquet")
                log.info("candles: %s", stage_candles(client, out, mk, ev, now, passes=a.passes))
                log.info("candle parquet rows: %s", build_candle_parquets(out))
            if "check" in stages:
                if sm is None and (out / "series_meta.json").exists():
                    sm = json.loads((out / "series_meta.json").read_text())
                stage_check(out, games, (sm or {}).get("series"), now)
    except BudgetExhausted as e:
        log.error("%s -- stopping cleanly; re-run to resume", e)
        code = 3
    log.info("real HTTP requests this run: %d; cache stats %s", budget.n, client.http.stats)
    return code


if __name__ == "__main__":
    sys.exit(main())
