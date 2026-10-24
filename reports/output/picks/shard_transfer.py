"""Move the user's own cash between Kalshi exchange shards (e.g. Exchange 0 -> Exchange 3, where NBA spreads/props
trade). Money stays in the same account; only the shard balance changes.

  KALSHI_ENV=prod .venv/Scripts/python.exe reports/output/picks/shard_transfer.py --amount 9.00 [--from 0] [--to 3] [--send]

Without --send it is a preview: it only reads the balance of each shard. With --send it makes ONE
POST /portfolio/intra_exchange_instance_transfer (Kalshi docs, IntraExchangeInstanceTransfer; amount in centicents)
and then reads the balances again. Every step is appended to logs/shard_transfers.jsonl. No key material is printed.
The user runs --send themselves.
"""
import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOG = ROOT / "logs" / "shard_transfers.jsonl"
ENDPOINT = "/portfolio/intra_exchange_instance_transfer"
MAX_AMOUNT = Decimal("10.00")           # hard cap per transfer (dollars)
SHARDS = (0, 1, 2, 3)


def log(event, **kw):
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **kw}
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str))


def balances(ro):
    return {s: ro.shard_cash(s) for s in SHARDS}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", type=Decimal, required=True, help="dollars, e.g. 9.00")
    ap.add_argument("--from", dest="src", type=int, default=0)
    ap.add_argument("--to", dest="dst", type=int, default=3)
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    if not (Decimal("0.01") <= a.amount <= MAX_AMOUNT):
        sys.exit(f"amount must be between 0.01 and {MAX_AMOUNT}")
    if a.src == a.dst or a.src not in SHARDS or a.dst not in SHARDS:
        sys.exit(f"--from and --to must be different shards in {SHARDS}")

    from kpm.execution.client import ExecClient
    from kpm.execution.env import load_env
    env = load_env(require_keys=True, expected="prod")
    ro = ExecClient(env, live=False)                       # read-only: balance reads
    before = balances(ro)
    log("preview", amount=a.amount, src=a.src, dst=a.dst, balances_before=before)
    if before.get(a.src) is None or before[a.src] < a.amount:
        return log("abort", reason=f"shard {a.src} balance {before.get(a.src)} is below {a.amount}")
    centicents = int(a.amount * 10000)
    body = {"source": "event_contract", "destination": "event_contract", "amount": centicents,
            "source_exchange_shard": a.src, "destination_exchange_shard": a.dst,
            "client_transfer_id": str(uuid.uuid4())}
    print(f"\nMove ${a.amount} from Exchange {a.src} (${before[a.src]}) to Exchange {a.dst} (${before[a.dst]}).")
    if not a.send:
        print("PREVIEW - nothing sent. To do it, run the same command with --send.")
        return

    import requests
    from kpm.execution.auth import auth_headers, sign_path
    path = sign_path(env.rest_base, ENDPOINT)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    headers.update(auth_headers(env.key, env.key_id, "POST", path))
    log("send", body=body)
    r = requests.post(env.rest_base + ENDPOINT, data=json.dumps(body, separators=(",", ":")), headers=headers,
                      timeout=15)
    log("result", status=r.status_code, response=(r.text or "")[:400])
    if r.status_code != 200:
        return
    for _ in range(10):                                   # the transfer is processed asynchronously
        time.sleep(2)
        after = balances(ro)
        if after.get(a.dst) is not None and before.get(a.dst) is not None and after[a.dst] > before[a.dst]:
            break
    log("balances_after", balances_after=after)


if __name__ == "__main__":
    main()
