# kpm execution module: design for a safe micro live test

Draft 0.1, 2026-10-05. This design uses only the verified research pass: docs.kalshi.com, openapi.yaml 3.32.0, asyncapi.yaml, the changelog through Oct 8 2026, and public GETs. No authenticated, order, or portfolio endpoint has been called.

**Goal.** Prove the whole order path with at most **$0.25 at risk**, plus at most $0.05 of fees. The path is: sign, place, amend, cancel, fill, reconcile, settle. An optional demo run comes first.

**Out of scope: LIP income and edge.**
- A position this small is far below the Target Size. That is 300 contracts on the FX 15-minute programs and 1,000 on Miami hourly.
- Rewards under $1.00 per period are not paid, so this test earns $0.
- Fill rates and adverse selection at strategy size stay with the planned fill study and paper tracking.

**"25*0.01" can be read two ways.**
- 25 contracts at $0.01, which is $0.25 of collateral.
- 25 × 0.01 = 0.25 contracts. Fractional contracts trade in 0.01 steps.
- The caps are in dollars, so either reading stops at $0.25. The user must confirm which one they meant (Q1).

**Where it lives.**
- **Package.** `src/kpm/execution/` holds `units.py`, `auth.py`, `env.py`, `client.py`, `safety.py`, `ledger.py`, `reconcile.py` and the CLI `kpm exec check|place|cancel|kill|reconcile|microtest`. `place` takes the outcome you are buying and that outcome's price (`--buy no --price 0.01`), never a raw side. That rules out wrong-side orders typed by hand.
- **No reuse of `kpm.clients.http.CachedClient`.** It has a permanent disk cache and automatically retries 429 and 5xx. That would serve stale portfolio data and could place an order twice. Its read-only path guard stays in place for the research code.
- **Config.** The module reads a new `execution:` block in `config/settings.yaml`. It ignores the existing `trading:` block, which points at the shared host and has looser caps.
- **Ledger.** New tables `exec_requests`, `exec_orders`, `exec_fills` and `exec_settlements` store prices and counts as TEXT decimals. The legacy `bot_orders` table (REAL prices, yes/no plus buy/sell) is not used.
- **Dependencies (checked locally today).** `.venv` (Python 3.12.10) now has `cryptography 50.0.2`, `websockets 17.2` and `tzdata 2026.5`. The research note that "cryptography is missing" is out of date.

---

## 1. Units cheat sheet

### 1.1 Traps that cause 100x or wrong-side orders

1. **Prices are in dollars.** `price: "0.0100"` means 1¢. The legacy API used integer cents (1–99), so `"1"` or `"50"` is either rejected or 100× off. Always send exactly 4 decimals.
2. **`side` is the YES book side, and `price` is always the YES price.**
   - `bid` buys YES.
   - `ask` sells YES, which is the same as buying NO at 1 − price.
   - **To buy NO at 1¢, send `side:"ask"` with `price:"0.9900"`.**
   - Sending `ask` at `"0.0100"` buys NO at 99¢. For 25 contracts that locks **$24.75 instead of $0.25 (99×)**.
3. **Collateral depends on the side.** Starting flat, a bid locks price × count and an ask locks (1 − price) × count. Every cap must use `collateral()`.
4. **`count` is in contracts, in steps of 0.01.** `"25"` and `"0.25"` differ by 100×.
5. **Money fields mix units.**
   - GetBalance `balance` and `portfolio_value` are integer **cents**. `balance_dollars` in the same object is a dollar string.
   - Settlement `revenue` and `value` are integer **cents**. Its `*_dollars` and `fee_cost` fields are dollars.
   - The intra-shard transfer `amount` is in **centicents**, so $1 = 10000. The subaccount transfer `amount_cents` is in cents.
   - A subaccount-balance `balance` is a dollar *string*. It has the same name as GetBalance's integer-cent field.
   - Incentive `period_reward` is in centicents.
6. **Seconds vs milliseconds.**
   - The `expiration_time` request field is Unix **seconds**. A millisecond value pushes the expiry millennia out, or gets rejected, which silently disables the dead-man switch.
   - `min_ts` and `max_ts` are seconds. A millisecond value returned HTTP 400.
   - The `KALSHI-ACCESS-TIMESTAMP` header is **milliseconds**. Sending seconds makes the signature fail.
7. **REST orderbook arrays are ascending, so the best bid is the LAST element.** `[0]` is the worst level shown. Best YES ask = 1 − last `no_dollars` price. The API reference says "best to worst", but live data and the orderbook guide show ascending order.
8. **WebSocket orderbook NO levels use NO-leg prices** unless you subscribe with `use_yes_price: true`.
9. **Placeholders are not quotes.**
   - `/markets` shows an empty side as `yes_bid_dollars "0.0000"` with size `"0.00"`, or `yes_ask_dollars "1.0000"` with size `"0.00"`.
   - `/markets` is server-cached for up to about 15 s, so price orders only from `/markets/{t}/orderbook`.
   - The orderbook of a ticker that doesn't exist returns HTTP 200 with empty arrays.
10. **`balance` covers every shard** unless you pass `?exchange_index=N`.
    - KXBTC15M is on shard 2. FX and weather are on shard 0.
    - API orders never move cash between shards.
11. **Status names differ.** The `/markets` filter uses `status=open`, but market objects report `"active"`.

### 1.2 Field reference

| Field | Where | Unit and type | Example |
|---|---|---|---|
| `price` | create, amend (request) | YES price in USD, string; send 4 dp; must be on the grid, strictly between 0 and 1 | `"0.5600"` |
| `count` | create, amend (request) | contracts, string, 0–2 dp, step 0.01; on amend it is the new total (filled + resting) | `"25"`, `"0.25"` |
| `reduce_by`, `reduce_to` | decrease (request) | contracts string | `"2.00"` |
| `fill_count`, `remaining_count`, `reduced_by` | V2 write responses | contracts string, 2 dp; same type as the `_fp` fields despite the missing suffix | `"10.00"` |
| `*_fp` | Order, Fill, Position, queue, market data | contracts string, 2 dp; `position_fp` is signed (+YES, −NO) | `"-25.00"` |
| `*_dollars`, `fee_cost`, `average_fill_price`, `balance_dollars`, `balance_breakdown[].balance` | responses | USD string, **up to 6 dp**; parse with `Decimal` | `"0.010000"` |
| `average_fee_paid` | create, amend responses | USD **per contract**; only present when `fill_count` > 0 | `"0.0012"` |
| `taker_fees_dollars`, `maker_fees_dollars` | Order | USD **total** for the order | `"0.000000"` |
| `balance`, `portfolio_value` | GET /portfolio/balance | int **cents**, sub-cent truncated, all shards by default | `2500` = $25 |
| `revenue`, `value` | Settlement | int **cents**; `value` is the payout of one YES contract, nullable | `2500` |
| Settlement `fee_cost` | Settlement | USD; **sum of the trade fees already on the fills** | count once |
| `amount` | POST intra_exchange_instance_transfer | int **centicents** | $1 = `10000` |
| `amount_cents`, `fee_cents` | subaccount transfer, deposits, withdrawals | int cents | $5 = `500` |
| `period_reward`, `max_reward_per_account` | GET /incentive_programs | int centicents | `200000` = $20 |
| `discount_factor_bps` | GET /incentive_programs | basis points | `5000` = 0.50 |
| `dollar_volume`, `dollar_open_interest` | WS ticker | int whole dollars | `16948` |
| `expiration_time` | create, amend (request) | int Unix **seconds**; GTC only; must be in the future | `1791223380` |
| `expiration_time`, `created_time`, `last_update_time`, `settled_time`, position `last_updated_ts` | REST objects | RFC3339 string with 0–6 fractional digits; `last_updated_ts` is a string | `"2026-10-05T17:10:41.988526Z"` |
| `ts_ms`, `created_ts_ms`, `last_updated_ts_ms`, `expiration_ts_ms`, `sending_ts_ms` | write responses, WS | int Unix **ms** | `1715793600123` |
| `min_ts`, `max_ts` | query params | int Unix **seconds**; a ms value returns 400 | |
| `KALSHI-ACCESS-TIMESTAMP` | request header | Unix **ms**, 13-digit string | `"1703123456789"` |
| REST `Fill.ts`, balance `updated_ts` | | int, **unit undocumented**, so don't use them | |
| `orderbook_fp.yes_dollars`, `no_dollars` | REST orderbook | `[[price_usd, contracts], ...]`, bids only, **ascending** | `[["0.5600","1658.88"], ...]` |
| `yes_dollars_fp`, `no_dollars_fp`, delta `price_dollars`, `delta_fp` | WS orderbook | same pairs; NO side in NO-leg prices by default; `delta_fp` is signed | `"-54.00"` |
| `price_ranges` | Market | `[{start, end, step}]` USD strings; the authoritative tick grid | see below |
| `exchange_index` | Market, Order, Fill, Balance | int shard: 0 Default, 1 Combos, 2 Crypto & Commodities, 3 Tennis/Baseball/Basketball | read it per market |
| `outcome_side`, `book_side` | Order, Fill, WS | `yes`/`no` and `bid`/`ask` (bid = yes, ask = no); the deprecated `side`/`action` fields can disappear | |

**Price grids seen live.**
- KXBTC15M and KXGOLD15M use `tapered_deci_cent`: $0.001 steps in [0, 0.10] and [0.90, 1.00], $0.01 steps in between.
- KXUSDJPY15M and KXTEMPMIAH use `linear_cent`: $0.01 everywhere.
- $0 and $1 are invalid. Off-grid prices are rejected. Whole-cent prices are valid on every grid.

**Fees.** All three target series are `quadratic` with multiplier 1.
- Taker fee = 0.07 × C × P × (1 − P), rounded up to $0.000001. A rounding fee is then added to bring the balance back onto the $0.0001 grid. The 0.07 rate is confirmed by a 2022 filing.
- Plain `quadratic` has no maker fee, so maker fills should cost $0. Confirm this on the first fill (Q16).
- Budget with the taker formula.

### 1.3 Conversion helpers (`execution/units.py`)

```python
from decimal import Decimal, ROUND_CEILING

D0, D1 = Decimal("0"), Decimal("1")
CENT, CENTICENT = Decimal("0.01"), Decimal("0.0001")

def dec(s: str) -> Decimal:
    """Any *_dollars / *_fp / fee_cost / price / count string. Floats are refused."""
    if not isinstance(s, str):
        raise TypeError(f"expected a decimal string, got {type(s).__name__}")
    return Decimal(s)

def fmt_price(p: Decimal) -> str:            # request 'price': YES leg, dollars, 4 dp
    if not (D0 < p < D1) or p != p.quantize(CENTICENT):
        raise ValueError(f"bad price {p}")
    return f"{p:.4f}"

def fmt_count(c: Decimal) -> str:            # request 'count': contracts, 2 dp
    if c <= 0 or c != c.quantize(CENT):
        raise ValueError(f"bad count {c}")
    return f"{c:.2f}"

def to_book(outcome: str, outcome_price: Decimal) -> tuple[str, Decimal]:
    """The ONLY place an outcome price becomes (side, YES price). buy NO @0.01 -> ('ask', 0.99)."""
    if outcome == "yes":
        return "bid", outcome_price
    if outcome == "no":
        return "ask", D1 - outcome_price
    raise ValueError(outcome)

def collateral(side: str, yes_price: Decimal, count: Decimal) -> Decimal:
    """Max loss of a fill from flat, before fees: bid p*count, ask (= NO buy) (1-p)*count."""
    return (yes_price if side == "bid" else D1 - yes_price) * count

def worst_fee(count: Decimal, yes_price: Decimal, multiplier: Decimal = D1) -> Decimal:
    """Taker-fee bound for budgeting, plus one $0.0001 rounding step."""
    raw = Decimal("0.07") * multiplier * count * yes_price * (D1 - yes_price)
    return raw.quantize(CENTICENT, rounding=ROUND_CEILING) + CENTICENT

def on_grid(p: Decimal, price_ranges: list[dict]) -> bool:
    """Exact grid membership. The safety layer never snaps silently."""
    for band in price_ranges:
        start, end, step = dec(band["start"]), dec(band["end"]), dec(band["step"])
        if start <= p <= end and (p - start) % step == 0:
            return True
    return False

def best_bid(levels: list[list[str]]) -> Decimal | None:     # REST book: ascending, best LAST
    return dec(levels[-1][0]) if levels else None

def best_yes_ask(no_levels: list[list[str]]) -> Decimal | None:
    b = best_bid(no_levels)
    return None if b is None else D1 - b

def from_cents(v: int) -> Decimal: return Decimal(v) * CENT
def from_centicents(v: int) -> Decimal: return Decimal(v) * CENTICENT

def to_centicents(usd: Decimal) -> int:      # intra-shard transfer 'amount' (not used by this module)
    n = usd / CENTICENT
    if n != n.to_integral_value():
        raise ValueError("not a multiple of $0.0001")
    return int(n)

def unix_seconds(ts: int) -> int:            # guard for expiration_time / min_ts / max_ts
    if not (1_000_000_000 <= ts < 10_000_000_000):
        raise ValueError(f"{ts} is not Unix seconds")
    return ts
```

---

## 2. Authentication

**Algorithm.** Verified against the docs, the official quick starts, and SDK 3.31.0 `auth.py`.

1. **Timestamp.** `ts = str(int(time.time() * 1000))`. That is milliseconds, 13 digits, fresh for every request and every retry.
2. **Signed path.** `urlparse(base_url + endpoint).path`.
   - It includes `/trade-api/v2` and excludes the host and the query string.
   - It is the same on every host.
   - No trailing slash. A trailing slash triggers a 301, which changes the path.
3. **Pre-sign text.** `ts + METHOD.upper() + path`, with no separators, UTF-8 encoded.
   - Example: `1703123456789GET/trade-api/v2/portfolio/balance`.
   - The JSON body is **not** signed.
4. **Signature.**
   - Ed25519 keys sign the text directly: 64 bytes, deterministic.
   - RSA keys use RSA-PSS with SHA-256, MGF1(SHA-256) and `salt_length=PSS.DIGEST_LENGTH` (32 bytes, **not** `MAX_LENGTH`): 256 bytes, random each time.
5. **Encoding.** Standard base64 (`b64encode`), not URL-safe.
6. **Headers.** `KALSHI-ACCESS-KEY` (key id, a UUID), `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE`. Add `Content-Type: application/json` when there is a body.
7. **WebSocket.** Send the same three headers on the handshake, signed over `ts + "GET" + "/trade-api/ws/v2"`. This is required even for public channels. Re-sign on every reconnect.

**Key type.** Since 2026-10-01 the web dialog creates Ed25519 keys by default (PKCS#8, `BEGIN PRIVATE KEY`). RSA keys come as PKCS#1 (`BEGIN RSA PRIVATE KEY`), but a PKCS#8 RSA key also says `BEGIN PRIVATE KEY`. So detect the type from the parsed key object, never from the header.

**Reference implementation.** `sign_text` is the official sample. The path handling follows the official "Complete Working Example".

```python
"""execution/auth.py: Kalshi request signing (docs.kalshi.com api_keys + quick start samples)."""
from __future__ import annotations

import base64
import time
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

WS_SIGN_PATH = "/trade-api/ws/v2"

def load_private_key(pem_path: str, password: bytes | None = None):
    key = serialization.load_pem_private_key(Path(pem_path).read_bytes(), password=password)
    if not isinstance(key, (Ed25519PrivateKey, rsa.RSAPrivateKey)):   # type from the object, not the header
        raise ValueError("Kalshi API keys are Ed25519 or RSA")
    return key

def sign_text(key, text: str) -> str:
    msg = text.encode("utf-8")
    if isinstance(key, Ed25519PrivateKey):
        sig = key.sign(msg)                                   # 64 bytes, deterministic
    else:
        sig = key.sign(msg,
                       padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                   salt_length=padding.PSS.DIGEST_LENGTH),   # 32; NOT MAX_LENGTH
                       hashes.SHA256())                       # 256 bytes, randomized
    return base64.b64encode(sig).decode("ascii")             # standard alphabet

def sign_path(base_url: str, endpoint: str) -> str:
    """'/portfolio/balance' -> '/trade-api/v2/portfolio/balance'. Query goes in params, never here."""
    if endpoint.startswith("/trade-api/") or "?" in endpoint or endpoint.endswith("/"):
        raise ValueError(f"bad endpoint {endpoint!r}")       # double prefix / inline query / trailing slash
    return urlparse(base_url + endpoint).path

def auth_headers(key, key_id: str, method: str, path: str) -> dict[str, str]:
    ts = str(int(time.time() * 1000))                         # ms, new for every attempt
    return {"KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sign_text(key, ts + method.upper() + path.split("?")[0])}
```

```python
# execution/client.py (transport core)
import time, requests
from kpm.execution.auth import auth_headers, sign_path

class ExecHttp:
    def __init__(self, base_url: str, key, key_id: str, audit, live: bool):
        self.base_url, self._key, self._key_id = base_url, key, key_id
        self._audit, self.live, self._s = audit, live, requests.Session()

    def send(self, method: str, endpoint: str, *, params=None, body=None, timeout_s=10.0):
        if method != "GET" and not self.live:
            raise PermissionError("write attempted in dry-run")          # second line of defense
        path = sign_path(self.base_url, endpoint)
        headers = auth_headers(self._key, self._key_id, method, path)
        if body is not None:
            headers["Content-Type"] = "application/json"
        t0 = time.monotonic()
        try:
            r = self._s.request(method, self.base_url + endpoint, params=params, json=body,
                                headers=headers, timeout=timeout_s, allow_redirects=False)
        except requests.RequestException as e:
            self._audit(method=method, path=path, params=params, body=body, error=type(e).__name__)
            raise
        self._audit(method=method, path=path, params=params, body=body, status=r.status_code,
                    response=r.text[:65536], latency_ms=int((time.monotonic() - t0) * 1000))
        if 300 <= r.status_code < 400:     # requests would turn a 301 POST into a GET
            raise RuntimeError(f"redirect {r.status_code} on {path}; not followed")
        return r

# WebSocket (websockets 17.2): websockets.connect(ws_url,
#     additional_headers=auth_headers(key, key_id, "GET", "/trade-api/ws/v2"))
```

**Test vectors.** From the offline self-test with throwaway keys.
- DELETE `https://external-api.kalshi.com/trade-api/v2/portfolio/events/orders/abc123?market_ticker=X&exchange_index=2` at ts 1703123456789 pre-signs as `1703123456789DELETE/trade-api/v2/portfolio/events/orders/abc123`.
- Ed25519: 64 bytes, 88 base64 characters, deterministic, verifies.
- RSA-2048: 256 bytes, 344 base64 characters, different every time, verifies with salt length 32. A `MAX_LENGTH` signature fails that check.
- A PKCS#8 RSA key does not load as Ed25519.

**Operational rules.**
- **On 401, stop.** Don't loop. The official troubleshooting rows map errors to causes:

  | Symptom | Likely cause |
  |---|---|
  | 401 | Wrong key id, wrong key, or wrong algorithm |
  | Signature error | Timestamp sent in seconds, or the query string was signed |
  | Path not found | `/trade-api/v2` prefix doubled |

- **Clock.** The allowed clock skew is undocumented for REST and WS. FIX allows 30 s. Keep Windows time synced.
- **Don't use the official SDK.**
  - It requires Python 3.13 or later; the venv is 3.12.10.
  - It defaults to the **production** host.
  - `set_kalshi_auth(key_id, path)` passes the path where PEM text is expected.
  - It cannot load encrypted PEMs.
- **Secrets.**
  - The key id, PEM path and optional PEM password come only from `.env`, and the PEM file lives outside the repo.
  - Logs and banners show `key_fp = sha256(key_id)[:8]`, never the id, the headers, or the PEM path or contents.
  - Add the new env names to `config.SECRET_KEYS`.

---

## 3. Environments

| | Demo (mock money) | Production |
|---|---|---|
| Web | https://demo.kalshi.co (sign-up at /sign-up; the `.com` domain is not Kalshi) | https://kalshi.com |
| REST (allowlisted) | https://external-api.demo.kalshi.co/trade-api/v2 | https://external-api.kalshi.com/trade-api/v2 |
| REST (alternate, not used) | https://demo-api.kalshi.co/trade-api/v2 | https://api.elections.kalshi.com/trade-api/v2 (shared with the collectors; drew a 429) |
| WS (allowlisted) | wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2 | wss://external-api-ws.kalshi.com/trade-api/ws/v2 |
| WS (alternate) | wss://demo-api.kalshi.co/trade-api/ws/v2 | wss://api.elections.kalshi.com/trade-api/ws/v2 |
| Account and keys | Separate account and keys, which work only on demo hosts | Production keys work only on production hosts |
| Books | Nearly empty and one-sided; `status=open` returned a stale closed market | Real |

**About demo.**
- It starts with no funds. Fund it with a test card (for example Visa Debit 4000 0566 5566 5556, any future expiry and CVV), Plaid sandbox (`user_good` / `pass_good`), or Solana devnet USDC.
- It is sometimes down for up to about 24 h.
- Demo can prove auth, order mechanics, error codes and state changes. It cannot prove fills or edge.

**Switching safely (`execution/env.py`).**
- **`KALSHI_ENV` is required** and must be exactly `demo` or `prod`, with no default. `.env.example` currently defaults to `prod` and should be changed to blank. Any other value makes the module exit.
- **Hosts are a constant table in code** with one allowlisted host per environment. `settings.yaml` cannot override them, and `trading.base_url` / `ws_url` are ignored.
- **Credentials are per environment.**
  - Variables: `KALSHI_DEMO_KEY_ID`, `KALSHI_DEMO_KEY_PATH`, `KALSHI_PROD_KEY_ID`, `KALSHI_PROD_KEY_PATH`, plus an optional `_KEY_PASSWORD` for each.
  - Only the selected environment's pair is loaded.
  - The old single pair `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY_PATH` is not read.
- **Startup probe.** An authenticated `GET /portfolio/balance`. A 401 means the key and environment don't match, so the module aborts.
- **Banner** (printed and audited): environment, REST host, `key_fp`, DRY-RUN or LIVE, caps, whitelist.
- **One process uses one environment.** Every ledger row stores `env`, and reconciliation never compares across environments.
- **Production needs extra gates.** `--env prod` must be on the command line, must match `KALSHI_ENV`, and there is a typed confirmation (see 5.1). Demo also needs `--live` to send anything.

---

## 4. Client API surface

Paths are relative to the environment's REST base and are signed as `/trade-api/v2` + path.

**Rate limits (Basic tier).**
- Read: 200 tokens/s. Write: 100 tokens/s, and the Write bucket holds only 1 s.
- Default cost is 10 tokens.
- A 429 has no `Retry-After` header and no penalty.
- Reads are retried with bounded exponential backoff (0.5, 1, 2, 4 s). Writes are never retried blindly (see 5.4).

**Public endpoints.** No auth needed, always fetched fresh.

| Function | HTTP | Fields used |
|---|---|---|
| `exchange_status()` | GET /exchange/status | `exchange_index_statuses[{exchange_index, description, exchange_active, trading_active, intra_exchange_transfers_active}]`, `exchange_estimated_resume_time`. Top-level flags describe shard 0 only. If the breakdown is missing, treat the shard as closed. |
| `series(t)` | GET /series/{t} | `exchange_index`, `fee_type` (treat as an open string), `fee_multiplier` |
| `open_markets(series)` | GET /markets?series_ticker=&status=open | `markets[]`, `cursor`. Candidate tickers come only from here (series whitelist; never parse tickers). Cached for up to 15 s. |
| `market(t)` | GET /markets/{t} | `market{ticker, event_ticker, status, close_time, can_close_early, exchange_index, price_ranges}`. The wrapper shape comes from OpenAPI and wasn't captured in research (Q28). |
| `orderbook(t, depth)` | GET /markets/{t}/orderbook | `orderbook_fp{yes_dollars, no_dollars}`: ascending, best bid last, not cached |
| `user_data_timestamp()` | GET /exchange/user_data_timestamp | `as_of_time`: how fresh the REST portfolio data is |
| `historical_cutoff()` | GET /historical/cutoff | `orders_updated_ts` and others; older orders are only in /historical/orders |

**Authenticated reads.** These use the Read bucket.

| Function | HTTP and params | Response |
|---|---|---|
| `account_limits()` | GET /account/limits | `usage_tier`, `read{refill_rate, bucket_capacity}`, `write{...}`, `grants[{exchange_instance, level, expires_ts, source}]` |
| `balance(ei)` | GET /portfolio/balance `?exchange_index` (`subaccount` is always omitted) | `balance` (¢), `balance_dollars`, `portfolio_value` (¢), `updated_ts`, `balance_breakdown[{exchange_index, balance}]` |
| `positions(...)` | GET /portfolio/positions `?ticker, event_ticker, settlement_status (unsettled, settled, all), count_filter, exchange_index, subaccount, limit<=1000, cursor` | `market_positions[{ticker, exchange_index, position_fp, market_exposure_dollars, realized_pnl_dollars, fees_paid_dollars, total_traded_dollars, last_updated_ts}]`, `event_positions[...]`, `cursor` |
| `orders(...)` | GET /portfolio/orders `?ticker, event_ticker (<=10), min_ts, max_ts, status (resting, canceled, executed), limit<=1000, cursor, subaccount, exchange_index` | `orders[Order]`, `cursor`. There is **no client_order_id filter**, so query by ticker and match locally. |
| `order(id)` | GET /portfolio/orders/{order_id} (2 tokens) | `{order: Order}`. It takes no shard parameter and is unverified for shard-2 orders, so use `orders(ticker=...)` instead. |
| `queue_position(id)` | GET /portfolio/orders/{order_id}/queue_position | `queue_position_fp`: contracts ahead of you; `"0.00"` means first in line |
| `fills(...)` | GET /portfolio/fills `?ticker (one), order_id, min_ts, max_ts, limit, cursor, subaccount, exchange_index` | `fills[{fill_id, trade_id (= fill_id), order_id, ticker, exchange_index, outcome_side, book_side, count_fp, yes_price_dollars, no_price_dollars, is_taker, fee_cost, created_time}]`. There is no `client_order_id`, so join on `order_id` and dedupe on `fill_id`. |
| `settlements(...)` | GET /portfolio/settlements `?ticker, event_ticker, min_ts, max_ts, limit, cursor, subaccount` | `settlements[{ticker, event_ticker, exchange_index, market_result, yes_count_fp, yes_total_cost_dollars, no_count_fp, no_total_cost_dollars, revenue (¢), value (¢ or null), fee_cost, settled_time}]`. Persist these locally; there is no historical equivalent. |

**Order object.**
- Fields: `order_id, user_id, client_order_id, ticker, outcome_side, book_side, type, status (resting, canceled, executed), yes_price_dollars, no_price_dollars, fill_count_fp, remaining_count_fp, initial_count_fp, taker_fees_dollars, maker_fees_dollars, taker_fill_cost_dollars, maker_fill_cost_dollars, expiration_time, created_time, last_update_time, self_trade_prevention_type, order_group_id, cancel_order_on_pause, subaccount_number, exchange_index`.
- The WS `user_order` message names the market `ticker`, while `fill` and `market_position` use `market_ticker`.

### Writes

Writes go to the Write bucket and use V2 paths only. The legacy POST/DELETE `/portfolio/orders` endpoints are deprecated.

**`create_order`**: POST /portfolio/events/orders, 10 tokens.
- Required: `ticker`, `side` (`bid`/`ask`), `count`, `price`, `time_in_force` (`fill_or_kill`, `good_till_canceled`, `immediate_or_cancel`), `self_trade_prevention_type` (`taker_at_cross`, `maker`).
- Optional:
  - `client_order_id`.
  - `expiration_time`: seconds, GTC only, must be in the future.
  - `post_only`, `cancel_order_on_pause`.
  - `reduce_only`: rejected unless the order is IOC.
  - `order_group_id`, `exchange_index`, `subaccount` (in the body).
- 201 response: `order_id`, `fill_count`, `remaining_count`, `ts_ms`, plus optional `client_order_id`, `average_fill_price` and `average_fee_paid`. There is no status field; `remaining_count "0.00"` means nothing is resting.
- Error codes: 400, 401, 403, 409 (duplicate `client_order_id`), 429, 500, 503.
- **Module defaults:**
  - `good_till_canceled` with `expiration_time` = min(now + ttl, close_time − buffer).
  - `post_only: true`, `cancel_order_on_pause: true`, `self_trade_prevention_type: "taker_at_cross"`.
  - The market's own `exchange_index`. Auto-routing would bill every shard's bucket.
  - Always a `client_order_id`.

**`cancel_order(order_id, exchange_index)`**: DELETE /portfolio/events/orders/{order_id}?exchange_index=N, 2 tokens.
- **If neither `exchange_index` nor `market_ticker` is passed, the cancel goes to shard 0** and misses a shard-2 order.
  - Pass the order's own index.
  - `market_ticker` with no index auto-routes.
  - Never send `exchange_index=0` together with `market_ticker`.
- 200 response: `order_id`, `client_order_id`, `reduced_by`, `ts_ms`.
- The only documented responses are 200, 401, 404 and 500. A 404 does not prove the order is gone; reconcile.
- Known issue from 2026-05-21, with no recorded fix: the response can describe a different order. Check that `order_id` matches, then confirm with `orders(ticker)`.
- After `close_time`, every operation, including cancel, is rejected with `MARKET_INACTIVE`.

**`amend_order`**: POST /portfolio/events/orders/{order_id}/amend (`?subaccount`), 10 tokens (derived from the changelog).
- Required: `ticker`, `side`, `price`, and `count` as the **new total** (filled + desired resting).
- Optional: `client_order_id`, `updated_client_order_id`, `expiration_time` (omit to keep it, 0 to clear it), `exchange_index`.
- 200 response: always `order_id` and `ts_ms`. `remaining_count`, `fill_count` and the fill averages appear only when relevant.
- A price change or size increase sends the order to the back of the queue. Changing only the expiry, or decreasing the size, keeps its place.
- **An amend that crosses fills immediately, and amend has no `post_only` field.** So the cross check runs again before every amend. For a price move toward the touch, the module cancels and places a new post-only order instead of amending.

**`decrease_order`**: POST /portfolio/events/orders/{order_id}/decrease (`?subaccount`), 10 tokens (derived).
- Body: exactly one of `reduce_by` or `reduce_to`, plus `exchange_index` or `market_ticker`.
- 200 response: `order_id`, `client_order_id`, `remaining_count`, `ts_ms`.

**`batch_create`**: POST /portfolio/events/orders/batched, 10 tokens per order, billed to the unscoped bucket.
- Body: `{orders: [...]}`.
- 201 response: `{orders: [{order_id, client_order_id, fill_count, remaining_count, average_fill_price, average_fee_paid, ts_ms, error}]}`. Check the `error` on every item.
- No Advanced tier is needed, but at Basic a batch is capped at about 10 orders because the whole batch must fit in the bucket.
- Disabled for the micro-test.

**`batch_cancel`**: DELETE /portfolio/events/orders/batched with a JSON body, 2 tokens per order.
- Body: `{orders: [{order_id, exchange_index or market_ticker}]}`.
- 200 response: per-item `reduced_by` (`"0.00"` on error), `ts_ms`, `error`.

**`cancel_all`**: DELETE /portfolio/events/orders, 2 tokens, returns 204.
- It cancels **every** resting order on the account, on all shards, including orders placed by hand in the app.
- Orders placed in the following minute may also be cancelled.
- Use it only as the last-resort kill.

**Order groups**: POST /portfolio/order_groups/create, with reset at PUT .../{id}/reset?exchange_index=N.
- Limits are whole contracts (at least 1), and a group works on one shard only.
- Not used in the micro-test.

**Endpoint allowlist.** The client refuses any path not listed in this section. That excludes transfers, API-key, subaccount, `target_balance_allocation` and tier-upgrade endpoints.

**Error parsing (`client.parse_error`).**
- **Three body shapes:**
  - `{"error": {"code", "message", "details"}}`, seen live as `not_found` and `bad_request`.
  - `{"error": "too many requests"}`, the documented 429 body. A public 429 actually came back nested, so detect 429 by status code, not body.
  - `{"msg": "Parameter validation failed ..."}`, with no code.
- **Known codes.** These are from the legacy-era changelog and the V2 strings may differ: `invalid_order`, `invalid_order_size`, `available_balance_too_low`, `order_id_and_client_order_id_mismatch`, `order_side_mismatch`, `order_ticker_mismatch`, and `invalid order` with details `post only cross`. A crossing post-only order may instead return 201 with `remaining_count "0.00"`.
- **Outcome classes:**

  | Class | Responses | Handling |
  |---|---|---|
  | `rejected` | Other 4xx | No retry |
  | `duplicate` | 409 | Look the order up |
  | `throttled` | 429 | Resend the same body and `client_order_id` at most twice, after 1 s and 2 s |
  | `unknown` | Timeout, connection error, 5xx | Reconcile; never resend automatically |
  | `auth` | 401, 403 | Halt |

---

## 5. Safety layer (mandatory)

### 5.1 Dry-run by default; live gates
A write leaves the process only if **all** of these hold. They are checked at startup and again before every write.
1. `--live` is on the command line. Without it, the run is a dry-run.
2. `execution.enabled: true` is set in `settings.yaml`.
3. `KALSHI_ENV` is `demo` or `prod`, the host matches that environment's table, and the environment's key loads and passes the balance probe.
4. **Production only:** `--env prod` is on the command line, plus a typed confirmation of `LIVE PROD 0.25` at an interactive prompt that shows the caps. With no TTY, the module refuses.
5. The kill file is absent and the session is not halted.

**What dry-run does.**
- It runs the whole pipeline: it reads public data, checks the caps, builds the request JSON, and writes an audit row with `dry_run=1`.
- With keys present, it also makes the authenticated reads. Without keys, it uses a simulated balance.
- The transport itself raises on any write while `live` is false.

### 5.2 Hard caps
```yaml
execution:                        # read by kpm.execution only; trading: is ignored
  enabled: false
  max_contracts_per_order: "25"
  max_dollars_per_order: "0.25"   # collateral(side, price, count): max loss before fees
  max_open_exposure_usd: "0.25"   # resting-order collateral + open-position worst case
  max_daily_loss_usd: "0.30"      # realized loss incl. fees, rolling 24 h (0.25 + 0.05 fee allowance)
  max_orders_per_minute: 6        # creates + amends; cancels/decreases exempt so a kill is never blocked
  max_orders_per_session: 20
  max_resting_orders: 2
  allowed_series: [KXUSDJPY15M]   # add KXTEMPMIAH / KXBTC15M explicitly, later
  price_min: "0.01"
  price_max: "0.99"
  order_ttl_s: 180                # every resting order carries expiration_time
  min_seconds_to_close: 300
  kill_switch_file: data/KILL
```
- **Strings, not numbers.** Values are written as strings so YAML never turns them into floats.
- **Code ceilings.** `safety.py` hard-codes ceilings equal to these values (`order_ttl_s` up to 900). A config value above a ceiling refuses to start. Raising a ceiling is a code change plus a test change, never a config edit.
- **Combined loss rule.** This rule bounds the worst case at $0.25 + $0.05:

  `realized_loss_24h + Σ worst(open) + worst(new) ≤ max_daily_loss`, where `worst(x) = collateral(x) + worst_fee(x)`.

  Each new order also needs `collateral ≤ max_dollars_per_order` and `count ≤ max_contracts_per_order`. Twenty-five contracts at $0.01 fits: collateral $0.25, worst case $0.2675.
- **Price checks:**
  - **Bounds.** 0.01 ≤ YES price ≤ 0.99, which keeps the NO price inside the same bounds.
  - **Grid, including sub-cent ticks.** `on_grid(price, market.price_ranges)` must hold exactly. Sub-cent prices such as 0.0950 or 0.9550 are allowed only where that band's step is $0.001. Linear-cent series accept whole cents only. Strategy code may snap prices (bids down, asks up), but the safety layer rejects anything off-grid.
  - **Source of (side, price).** It must come from `to_book()`.
  - **Post-only cross check against a fresh orderbook.** A bid must be below the best YES ask, and an ask must be above the best YES bid. If either side of the book is empty or a placeholder, refuse.
  - **IOC exits** (the only taker orders allowed) must be priced within 2 ticks of the touch. This also guards against Rulebook 5.11's $3,000 fee for causing an erroneous trade.

### 5.3 Pre-trade checks
They run in this order and fail closed.
1. Kill file, halt flag, rate limiter, session order cap.
2. **Trading hours.** Refuse from Thursday 02:55 to 05:05 ET (`kpm.core.timeutil.ET`). In `exchange_status()`, the entry for the market's `exchange_index` must show `trading_active` and `exchange_active`.
3. **Market.**
   - The ticker is in the fresh `open_markets()` list for a whitelisted series.
   - `status == "active"`.
   - `close_time − now ≥ min_seconds_to_close`, with an extra buffer if `can_close_early`.
   - `exchange_index` and `price_ranges` are read from this market object.
4. Price checks from 5.2, using a fresh orderbook.
5. **Size and caps.** The order count is a multiple of 0.01, and all caps and the combined loss rule hold. The total number of resting orders stays at or below `max_resting_orders`.
6. **Cash.**
   - `balance(exchange_index=N).balance_dollars ≥ worst(order) + $0.05`. Never use the top-level integer `balance`.
   - The module makes no shard transfers. The user funds shard 2 in the UI, and `target_balance_allocation` stays off.
7. **Exposure.** `positions(ticker)` and `orders(ticker, status=resting)` must match the ledger. Any unknown resting order or position in the test market makes the module refuse; it could be a crashed earlier run or manual trading.
8. Only one in-flight order intent per (ticker, side).

### 5.4 Idempotent `client_order_id`
1. `client_order_id = str(uuid4())`, which is 36 characters (FIX allows 64).
2. Insert and **commit** the intent row (status `intent`, full body, `env`, `run_id`) **before** sending.
3. Send once and classify the result (see section 4).
   - On 201, the status becomes `acked` and the module stores `order_id`, `fill_count`, `remaining_count` and `ts_ms`.
4. On `unknown`:
   - Wait until `user_data_timestamp().as_of_time` passes the send time (at most about 10 s).
   - Then call `orders(ticker, min_ts=sent−60)` and match `client_order_id`.
   - If found, adopt the order.
   - If not found, mark it `presumed_not_placed`, halt new orders, and report. **Never resend automatically.** Duplicate detection is only guaranteed against open orders, so a resent IOC that already executed would fill twice.
5. A retry always reuses the same id. A new id means a new intent, which must pass every check again.
6. The ledger maps `client_order_id` to `order_id`, because REST fills don't carry `client_order_id`.

### 5.5 Post-trade reconciliation
Reconciliation runs after every write, every 5 s while anything is open, at settlement, and at exit.
1. **Write responses first.** Check that the returned `order_id` matches the request.
2. **Orders.** `orders(ticker)` gives the status, `fill_count_fp`, `remaining_count_fp` and fees.
3. **Fills.** `fills(order_id)` rows are upserted by `fill_id`.
   - Σ `count_fp` must equal `fill_count_fp`.
   - On a post-only order, `is_taker` must be false; if it isn't, halt.
4. **Position.** Σ(+count when `outcome_side` is `yes`, −count when `no`) must equal `positions(ticker).position_fp`. For WS fills, which lack a NO price, the NO price is 1 − YES price.
5. **Cash.** Compare `balance_dollars` before and after, at the same `exchange_index`, against expected cost, fees and settlement revenue. Whether resting collateral reduces `balance_dollars` is unknown, so the first run records what actually happens before this check becomes strict.
6. **Settlement.** Persist each `settlements(ticker)` row. Realized P&L = `revenue` / 100 − cost − fill fees. Don't add the settlement `fee_cost` on top of the fill fees.
7. **Lag.** Declare a mismatch only after `as_of_time` is past the event and two reads 5 s apart agree. A real mismatch halts the session, runs the cancel path, and writes a report.
8. **WebSocket (optional).** `user_orders`, `fill` and `market_positions` arrive faster but carry no `seq`. After every reconnect, rerun the REST reconciliation.

### 5.6 Kill switch, and cancel on exit or exception
- **Kill file.** `data/KILL` is checked before every write and at least once per second. `kpm exec kill` creates the file and runs the cancel path. Deleting the file does not resume trading; a new run must be started.
- **Cancel path.** It is triggered by the kill file, a cap breach, a reconciliation mismatch, any exception, Ctrl+C or SIGBREAK, and normal exit (via `try/finally` plus `atexit`).
  1. Run `cancel_order(id, exchange_index)` for every order in the ledger that isn't final, and for every resting order on the tickers this run touched.
  2. Verify with `orders(ticker, status=resting)`, using bounded backoff for up to 30 s.
  3. If anything is still resting, or the reads fail, call `cancel_all()`. Log it, because it also cancels manual orders. Then block new orders for 60 s.
- **Backstops when cancels can't run.** A hard kill, a power loss, an exchange pause, or a market past `close_time` all stop cancels from working.
  - Every order carries `expiration_time` (at most the ttl) and `cancel_order_on_pause: true`.
  - The 15-minute markets close by themselves.

### 5.7 Audit logging without secrets
- **What is logged.** Every request and response, live or dry-run, goes to SQLite `exec_requests` and to `logs/exec.jsonl` via `kpm.core.audit`. Fields: run_id, env, live, ts_ms, method, signed path, params, body, client_order_id, status, response (cut at 64 KB), latency_ms, exception class.
- **How rows are built.** Each row is built from an explicit list of fields, never from `requests` objects. Never logged: header values, the key id (use `key_fp` instead), the PEM path or contents, passwords, and anything from `.env`.
- **Leak test.** A unit test runs a fake session, then searches the logs and the DB for the key id, signature, `PRIVATE KEY` and the PEM path.

### 5.8 Rules that shape the design
- **Least privilege.** Default keys get full `read` + `write`, which includes transfers. If the key dialog offers scopes, create the production key with `["read", "write::trade"]`. The endpoint allowlist enforces the same limit locally.
- **Developer Agreement** (S3 copy, v1.1):
  - The API may only be used for your own trading.
  - No spoofing or wash trades, so test orders must be genuinely executable. Self-trade prevention is mandatory anyway.
  - Retries need bounded exponential backoff.
  - No monitoring or benchmarking of Kalshi's availability or performance.
  - Kalshi's liability is capped at $50.
- **Collectors.** Pause the research collectors, or let them finish, before a live run. A public 429 was observed on the shared host, and it's unknown whether that traffic can throttle authenticated calls from the same IP.

---

## 6. Test plan

### 6.1 Unit tests with recorded fixtures (no network), in `tests/execution/`
- **Network guard.** An autouse fixture patches `requests.Session.request` and `socket.socket.connect` to raise. The client takes an injectable transport that replays fixtures keyed by (method, path, params).
- **Fixtures** live in `tests/fixtures/kalshi_exec/`.
  - **Real public captures from 2026-10-05.** Copy them now from the session scratchpad, which is temporary: `C:\Users\Jarvis\AppData\Local\Temp\claude\c--Users-Jarvis-Documents-Kalshi-PM\5805aad9-f58e-4981-a45a-fa35e3d20f67\scratchpad\` (`kdocs_auth\`, `kdocs_rt\live\`, `verify_auth2\pub\`).
    - KXBTC15M markets (tapered grid, shard 2).
    - Series files for KXUSDJPY15M and KXTEMPMIAH.
    - Full and depth-3 orderbooks.
    - Production and demo exchange status.
    - The 404 `not_found` body and the nested 429 body.
    - Incentive programs.
  - **Spec examples, marked synthetic until replaced by redacted demo recordings:**
    - Create 201, with and without fills.
    - Batch create with an error on one item.
    - Cancel 200, amend 200 without `remaining_count`, decrease 200, batch cancel, cancel-all 204.
    - Orders, fills (including a NO fill), positions, settlements, balance with breakdown, account limits.
    - WS fill, user_order and market_position messages.
    - A `{"msg"}` 400 and a `{"error": "too many requests"}` 429.
  - **Throwaway keys.** Ed25519, RSA PKCS#1 and RSA PKCS#8 keys are generated in `tmp_path` and never committed.
- **Cases:**
  1. **Signing.**
     - The vectors from section 2, including the MAX_LENGTH case that must fail.
     - Unsupported key types are rejected.
     - The method is uppercased.
     - The timestamp is 13 digits and fresh on every call.
     - Double prefix, inline query and trailing slash are rejected.
     - The WS signing path is correct.
  2. **Units.**
     - `fmt_price(0.01)` gives `"0.0100"` and rejects 0, 1, 50, 0.12345 and floats.
     - `fmt_count` keeps `"25"` and `"0.25"` distinct and rejects 0 and 0.001.
     - `to_book("no", 0.01)` gives `("ask", 0.99)`.
     - `collateral("ask", 0.99, 25) == 0.25`, and `collateral("ask", 0.01, 25) == 24.75`, which the caps must refuse.
     - `to_centicents(1) == 10000`, and 1.00005 is rejected.
     - `from_cents(2500) == 25`.
     - `unix_seconds` rejects milliseconds.
     - RFC3339 parsing works with 0, 4, 5 and 6 fractional digits.
  3. **Grid.**
     - On a tapered grid, 0.0950, 0.10, 0.55 and 0.9550 are accepted; 0.5550 and 0.0005 are rejected.
     - On a linear grid, 0.555 is rejected.
     - The 0.01–0.99 bounds reject 0.0050 and 0.9950, even though both are on the tapered grid.
  4. **Orderbook.** The best bid is the last element. YES ask = 1 − last NO bid. An empty side means refuse. `/markets` placeholders are ignored.
  5. **Errors.** All three body shapes, a non-JSON body, the classification table, and 3xx as a hard error that is never followed.
  6. **Gates.** Every combination of missing `--live`, `enabled: false`, missing or misspelled `KALSHI_ENV`, wrong host, missing production confirmation, and kill file present. In every case no write is sent; the transport raises if one is tried.
  7. **Caps.**
     - Each cap refuses with its own reason.
     - A config value above the code ceiling stops startup.
     - Non-whitelisted series.
     - `close_time` too close.
     - Missing shard status.
     - The Thursday window, with time mocked and DST handled.
     - Low cash on the target shard while the all-shard total looks large.
  8. **Idempotency.**
     - The intent is committed before the send; simulate a crash right after the commit.
     - Timeout: the order is found by `client_order_id`, and no second POST is sent.
     - 409: a lookup.
     - 429: at most 2 resends, same id.
     - 5xx: no resend.
  9. **Cancel routing.**
     - The query carries the order's `exchange_index`.
     - `exchange_index=0&market_ticker=` is never sent for a shard-2 order.
     - A mismatched response `order_id` is flagged.
     - A 404 triggers reconciliation.
  10. **Reconciliation.**
      - Fills give the right position sign, cost and fees, including WS NO fills.
      - Σ fills = `fill_count_fp`.
      - Settlement cents are converted to dollars without double-counting fees.
      - A taker fill on a post-only order halts the session.
  11. **Kill and exit.**
      - Kill file, exception and KeyboardInterrupt all run the cancel path.
      - `cancel_all` is used only after targeted cancels fail, followed by a 60 s lockout.
  12. **Audit.** No secrets leak, every request has a row, and dry-run rows are flagged.
  13. **Isolation.** Research modules never import `kpm.execution`, and `CachedClient` still refuses portfolio paths.

### 6.2 Demo end-to-end checklist (optional, mock money)

**Setup**
- [ ] Sign up at https://demo.kalshi.co/sign-up and fund the account with the test card.
- [ ] Create a demo API key and note its type.
  - Keep the PEM outside the repo.
  - In `.env`, set `KALSHI_ENV=demo`, `KALSHI_DEMO_KEY_ID` and `KALSHI_DEMO_KEY_PATH`.
- [ ] Sync the clock and run the unit tests.
- [ ] Run `kpm exec check`, which is read-only.
  - Check the banner, the balance and its breakdown, and per-shard exchange status.
  - Call `account_limits()` and record the tier and bucket capacity.
  - Confirm there are no resting orders.

**Mechanics** (each step dry-run first, then `--live`; check the audit rows)
- [ ] Choose an active whitelisted demo market with at least 10 minutes to close. Check status and `close_time` yourself, because demo lists stale markets.
- [ ] **Place.** Send a post-only GTC bid, count `"1"`, price `"0.0100"`, expiring in 120 s. Expect 201 with `remaining_count "1.00"`. Record `balance_dollars` before and after.
- [ ] **Check state.** `order(id)` and `orders(ticker)` should show `resting`. Read the queue position.
- [ ] **Duplicate id.** Resend the same body and `client_order_id`. Expect 409 and record the body.
- [ ] **Amend** the price to `"0.0200"`, with count = filled + resting. Expect 200, then confirm with `orders`.
- [ ] **Decrease.** `reduce_by "0.50"` should leave `remaining_count "0.50"`.
- [ ] **Cancel** with `?exchange_index`. Expect 200 with a matching `order_id` and a status of `canceled`.
- [ ] **Minimum size.** Place `count "0.01"`, record whether it is accepted, then cancel.
- [ ] **Expiry.** Place an order expiring in 60 s and wait. It should end `canceled`.
- [ ] **Reject cases.** Send these and record the status and body for each:
  - an off-grid price
  - a past `expiration_time`
  - `reduce_only` on a GTC order
  - a post-only order that crosses, if the demo book has any orders
- [ ] **Shard 2 (optional).**
  - Place an order on a demo KXBTC15M market with no cash on shard 2, and record the error.
  - Fund shard 2 in the demo UI and place it again.
  - Cancel once *without* routing to record the misroute response, then cancel properly.
- [ ] **Kill file.** Create it while an order is resting. Everything should be cancelled and the process should exit.
- [ ] **Crash recovery.**
  - Press Ctrl+C with an order resting; the cancel path should run.
  - Hard-kill the process (`taskkill /F`) with a 60 s order resting; the exchange should expire it.
- [ ] **Cancel-all.** Expect 204. Place a new order right after and record whether it gets cancelled within the minute.
- [ ] **Fills (best effort; demo books are thin).**
  - Send an IOC against an existing demo order.
  - Check fills, position, balance and settlement.
  - `kpm exec reconcile` should come back clean.
- [ ] **WS (optional).** `user_orders` and `fill` should match REST. After a reconnect, the REST reconciliation should run.
- [ ] **Fixtures.** Save the redacted demo responses as fixtures, replacing the synthetic ones, and rerun the unit tests.

**Exit criteria:** every step passes, there are no unexplained mismatches, and no secrets appear in the logs.

### 6.3 Live micro-test checklist (real money, at most $0.25 at risk)

**Before**
- [ ] The user confirms Q1 and the $0.25 budget, and lifts the project's read-only rule for this test only.
- [ ] The demo checklist has passed, or the user explicitly skips it.
- [ ] The user creates the production key, with `read` + `write::trade` if the dialog offers scopes, and sets `KALSHI_ENV=prod` and `KALSHI_PROD_KEY_*`.
- [ ] Caps are as in 5.2 with `allowed_series: [KXUSDJPY15M]`.
  - KXUSDJPY15M is on shard 0, uses a whole-cent grid and quadratic fees, has one market per event and no collateral netting. No shard transfer is needed.
  - KXBTC15M only after the user moves no more than $1 to shard 2 in the UI.
- [ ] It is not Thursday 03:00–05:00 ET, and the collectors are paused or finished.
- [ ] `kpm exec check --env prod`:
  - shard-0 `balance_dollars` ≥ $1
  - no resting orders
  - no position in the test market
  - shard 0 `trading_active`

**Steps** (each dry-run first, then `--live`)
1. [ ] **Mechanics, no fill expected.**
   - Place a post-only YES bid: 25 contracts at $0.01, or 0.25 contracts if Q1 says so, with a ttl of 180 s.
   - Expect 201, then `resting`, then a queue position.
   - Amend the expiry only, which keeps the queue spot, then cancel and confirm `canceled`.
   - Expected cost: $0.
2. [ ] **Maker fill.**
   - Place a post-only YES bid at the best bid, with collateral of at most $0.25 (for example 0.62 contracts at $0.40).
   - Use a ttl of at most 300 s, placed at least 5 minutes before close.
   - If it fills, check that `is_taker` is false, `fee_cost` (expected $0), `position_fp` and the balance change.
   - Hold to settlement. The maximum loss equals the collateral. Then reconcile `settlements`.
3. [ ] **Two-sided quote (the LIP-style shape).** Run this after step 2 has settled or expired.
   - Place a YES bid and a NO bid (side `ask`), each with collateral of about $0.12 or less.
   - Both should rest, and the kill file should cancel both.
   - If both fill, the position nets to zero.
4. [ ] **Kill drill.** Create `data/KILL` while orders rest. Every order should be cancelled and verified.
5. [ ] **Report.** `kpm exec reconcile` covers orders, fills, fees, positions, settlements, cash change against expected, and audit rows. There should be zero mismatches.

**Stop immediately and report** on any 401 or 403, any taker fill, an order not visible within 10 s, an unexplained mismatch, or any cap being hit.

**What this proves:** signing, routing, placing, amending, cancelling, expiry, the kill switch, fills, fees, settlement, and reconciliation with real money.

**What it does not prove:** LIP rewards, fill rates or adverse selection at strategy size, or edge.

---

## 7. Open questions and unverifiable facts (confirm before going live)

**User decisions**
1. **Q1.** Does "25*0.01" mean 25 contracts at $0.01, or 0.25 contracts? The caps hold either way.
2. Lift the read-only rule for this test only. The user creates the keys in the web UI.
3. **Key scopes.** It's undocumented whether the key dialog offers scopes or a subaccount lock. If it doesn't, either:
   - accept a full-access key and rely on the local allowlist, or
   - generate a narrow key with POST /api_keys/generate. That is an API-key write, so it's the user's call.
4. Stay on shard 0 (KXUSDJPY15M), or fund shard 2 in the UI for KXBTC15M.
5. Read the current Developer Agreement. The kalshi.com page returned 429; the copy that could be read is S3 v1.1 from 2023.
6. **Later hardening.**
   - Upgrade to the Advanced tier, which requires at least one API order.
   - Then create a funded subaccount with a key restricted to it, so the exchange itself caps the capital.

**Measure in demo or the first live step**

7. Is `count "0.01"` accepted, and is there any minimum notional?
8. What are the exact V2 error codes and bodies for:
   - a duplicate `client_order_id` (409)
   - an off-grid price, $0 or $1
   - a past expiry
   - `reduce_only` without IOC
   - low cash on a shard
   - `MARKET_INACTIVE`
   - a crossing post-only order (4xx, or 201 with `remaining_count "0.00"`)
9. What does a misrouted cancel return (probably 404)? Does `GET /portfolio/orders/{id}` find shard-2 orders?
10. Does `balance_dollars` drop while an order is resting?
11. Can cancel or amend responses still describe the wrong order (the 2026-05-21 issue)?
12. Does the REST Order include `last_update_reason`? The changelog says yes; the spec says no.
13. What are the length and character limits for `client_order_id`, and how long does duplicate detection last? uuid4 is assumed safe. FIX allows 64 characters and only checks against open orders.
14. Does an amend keep the original `post_only`? The design assumes not.
15. **WS.** Is `seq` counted per subscription or per market, and in what order do snapshot levels arrive?

**Confirm with the first real fills**

16. Do maker fills cost $0 on plain `quadratic` series? Is `Fill.fee_cost` net of the rounding fee and rebate? Is a retail account a "direct member" ($0.0001 grid, and covered by cancel-all's "Direct member" wording)?
17. How is settlement rounded? The overview says whole cents; the FIX example uses $0.0001.
18. What are the units of REST `Fill.ts` and balance `updated_ts`? Both are undocumented and not used.
19. How long is the lag before REST reads reflect a write?

**Unknown, designed around**

20. The allowed clock skew for signed timestamps: undocumented for REST and WS, 30 s for FIX.
21. The body shape of an authenticated 429. Public (CloudFront) limits, and whether same-IP collector traffic can throttle order calls.
22. Write-bucket capacity above Basic: the guide says 3 s, the spec says 2 s. Read it from `GET /account/limits`.
23. Does the help-center rule that a resting order blocks closing a position apply to the API? The design cancels before exiting.
24. The maker fee rate 0.0175 is unverified, because the fee schedule PDF returned 429. The taker rate 0.07 is confirmed.
25. Location attestation names only Sports, Elections and Entertainment, so crypto, FX and weather are presumably unaffected.
26. `GET /account/endpoint_costs` appears public in the spec and would give authoritative token costs. It wasn't called because it sits under /account.
27. **LIP.** How ticks are counted on sub-cent grids, and whether API orders are treated like app orders (implied, not stated). A micro-test earns $0 because of the $1 floor.
28. The `GET /markets/{ticker}` wrapper `{market}` comes from OpenAPI and was not captured. Check it on first use.

**Corrections applied (refuted or stale; don't reopen)**
- Batch endpoints need no Advanced tier. At Basic, the bucket size caps a batch at about 10 creates or 50 cancels.
- Error bodies are wrapped under `error`, plus the string and `msg` shapes. They are not flat.
- The research's `fill_row` helper crashes on WS NO fills. Use NO price = 1 − YES price.
- `?exchange_index=0&market_ticker=` does **not** auto-route. An explicit 0 sends the request to shard 0.
- Use `/markets?series_ticker=` to map markets to series. Don't parse tickers.
- The current LIP terms (effective 2026-07-30) exclude only IB/FCM customers, so designated market makers compete for the same pools.
- WebSocket limits are published: 500k market subscriptions per session and 10k commands/s. Neither matters here.
- The project `.venv` now has `cryptography` and `websockets`; the research said they were missing.