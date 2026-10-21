"""NFL market discovery for the WebSocket recorder: which open Kalshi tickers belong to which game on a date.

Public GET endpoints only (research client). Output: data/ws/<date>/markets.json, read later by the recorder
(the two programs share nothing but this file)::

    {"date": "26OCT22", "created_utc": "...", "games": {"26OCT22XXXYYY": {"series": [...], "tickers": [...],
        "meta": {ticker: {"yes_sub_title", "floor_strike", "cap_strike", "custom_strike"}},   # optional, simulator inputs
        "kickoff": "<ISO time>"}}}                                                            # optional, from KXNFLGAME

``meta`` (only the series the simulator reads) and ``kickoff`` are optional extras; the recorder reads only ``tickers``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATE_RE = re.compile(r"\d{2}[A-Z]{3}\d{2}")
OPEN_STATUSES = {"active", "open"}
META_SUFFIXES = ("GAME", "TIE", "SPREAD", "WINMARGIN")          # per prefix, e.g. KXNFLGAME, KXNBAWINMARGIN
DEFAULT_PREFIX = "KXNFL"
META_FIELDS = ("yes_sub_title", "floor_strike", "cap_strike", "custom_strike")
# Kickoff is NOT occurrence_datetime / expected_expiration_time: on 26OCT19WASSF both were 03:15Z while kickoff was
# 00:15Z, i.e. they mark the expected end. Rule: kickoff = expected_expiration_time - 3 h, else occurrence_datetime - 3 h.
KICKOFF_FIELDS = ("expected_expiration_time", "occurrence_datetime")
KICKOFF_OFFSET = timedelta(hours=3)


def kickoff_from(markets: list[dict]) -> str | None:
    for m in markets:
        for k in KICKOFF_FIELDS:
            if m.get(k):
                dt = datetime.fromisoformat(str(m[k]).replace("Z", "+00:00"))
                dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                return (dt - KICKOFF_OFFSET).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return None


def _series(client, prefix: str) -> list[str]:
    resp = client.http.get("/series", {"category": "Sports"}, use_cache=False)
    return sorted(s["ticker"] for s in resp.get("series") or [] if str(s.get("ticker", "")).startswith(prefix))


def _events(client, series: str):
    cursor = None
    while True:
        page = client.http.get("/events", {"series_ticker": series, "status": "open", "with_nested_markets": "true",
                                           "limit": 200, "cursor": cursor}, use_cache=False)
        yield from page.get("events") or []
        cursor = page.get("cursor")
        if not cursor:
            return


def discover(client, date_code: str, prefix: str = DEFAULT_PREFIX) -> dict:
    """Group open markets of all <prefix>* series by game suffix (the event ticker after the series, e.g. 26OCT22XXXYYY)."""
    if not DATE_RE.fullmatch(date_code):
        raise ValueError(f"date code must look like 26OCT22, got {date_code!r}")
    games: dict[str, dict] = {}
    meta_series = {prefix + s for s in META_SUFFIXES}
    for series in _series(client, prefix):
        for ev in _events(client, series):
            et = str(ev.get("event_ticker", ""))
            if f"-{date_code}" not in et:
                continue
            suffix = et.split("-", 2)[1]            # drop per-event extras: KXNFLANYTD-26OCT22NECHI-10 -> 26OCT22NECHI
            open_markets = [m for m in ev.get("markets") or [] if m.get("status") in OPEN_STATUSES]
            tickers = [m["ticker"] for m in open_markets]
            if not tickers:
                continue
            g = games.setdefault(suffix, {"series": [], "tickers": []})
            if series in meta_series:
                meta = g.setdefault("meta", {})
                for m in open_markets:
                    got = {k: m[k] for k in META_FIELDS if m.get(k) is not None}
                    if got:
                        meta[m["ticker"]] = got
            if series == prefix + "GAME" and "kickoff" not in g:
                if prefix == DEFAULT_PREFIX:
                    kick = kickoff_from(open_markets)
                    if kick:
                        g["kickoff"] = kick
                else:                                   # other sports: the kickoff rule is not known yet; keep the raw fields
                    g["kickoff"] = None
                    raw = {k: m[k] for m in open_markets[:1] for k in KICKOFF_FIELDS if m.get(k)}
                    if raw:
                        g["kickoff_raw"] = raw
            if series not in g["series"]:
                g["series"].append(series)
            g["tickers"].extend(t for t in tickers if t not in g["tickers"])
    return {"date": date_code, "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "games": dict(sorted(games.items()))}


def out_path(date_code: str, prefix: str = DEFAULT_PREFIX, out_root: str | Path = "data/ws") -> Path:
    """NFL keeps data/ws/<date>/markets.json; other sports go to data/ws/<sport>/<date>/markets.json."""
    base = Path(out_root) if prefix == DEFAULT_PREFIX else Path(out_root) / prefix[2:].lower()
    return base / date_code / "markets.json"


def run(date_code: str, client=None, out_root: str | Path = "data/ws", prefix: str = DEFAULT_PREFIX) -> Path:
    if client is None:
        from kpm.clients.kalshi import KalshiClient
        client = KalshiClient()
    doc = discover(client, date_code, prefix)
    out = out_path(date_code, prefix, out_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    for suffix, g in doc["games"].items():
        print(f"{suffix}: {len(g['tickers'])} markets in {len(g['series'])} series")
    print(f"{len(doc['games'])} games, {sum(len(g['tickers']) for g in doc['games'].values())} markets -> {out}")
    return out
