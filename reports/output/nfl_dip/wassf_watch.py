"""Read-only in-game watcher for the two WAS @ SF bets (log #20 and #21). Sends no orders and reads no account data.

Every 10 s it logs both legs' bid/ask next to the fair value implied by the deep markets, and flags CASH OUT when a
leg's bid is at or above that fair value, i.e. when someone will pay more than the bet is worth.
  Leg 1  KXNFLWINMARGIN WAS1TO6 (YES)   fair = P(WAS wins) - P(WAS by 7+) = (WAS mid - tie mid / 2) - WAS7 spread mid
  Leg 2  KXNFLRSHYDSPROT White 70+ (YES) fair >= regular KXNFLRSHYDS White 70+ mid (protection only adds value)
Output: logs/wassf_watch.csv (every poll) and logs/wassf_watch.log (start, signals, errors, end).
"""
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

from kpm.clients.kalshi import KalshiClient

ROOT = Path(__file__).resolve().parents[3]
CSV = ROOT / "logs" / "wassf_watch.csv"
LOG = ROOT / "logs" / "wassf_watch.log"
E = "26OCT19WASSF"
T = {"margin": f"KXNFLWINMARGIN-{E}-WAS1TO6", "was": f"KXNFLGAME-{E}-WAS", "tie": f"KXNFLTIE-{E}-Y",
     "was7": f"KXNFLSPREAD-{E}-WAS7", "white_prot": f"KXNFLRSHYDSPROT-{E}-WASRWHITE1-70",
     "white_reg": f"KXNFLRSHYDS-{E}-WASRWHITE1-70"}
END = datetime(2026, 10, 20, 4, 45, tzinfo=timezone.utc).timestamp()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def say(msg):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{now()} {msg}\n")


def quote(c, t):
    m = c.http.get(f"/markets/{t}", use_cache=False)["market"]
    b, a = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
    b = float(b) if b not in (None, "") else None
    a = float(a) if a not in (None, "") else None
    mid = (b + a) / 2 if b is not None and a is not None else None
    return dict(bid=b, ask=a, mid=mid, status=m.get("status"), result=m.get("result"))


def main():
    c = KalshiClient()
    new = not CSV.exists()
    say("watcher started (read-only; no orders)")
    flags = {"margin": False, "white": False}
    while time.time() < END:
        try:
            q = {k: quote(c, t) for k, t in T.items()}
            fm = None
            if None not in (q["was"]["mid"], q["was7"]["mid"]):
                fm = q["was"]["mid"] - (q["tie"]["mid"] or 0) / 2 - q["was7"]["mid"]
            fw = q["white_reg"]["mid"]
            sig_m = fm is not None and q["margin"]["bid"] is not None and q["margin"]["bid"] >= fm
            sig_w = fw is not None and q["white_prot"]["bid"] is not None and q["white_prot"]["bid"] >= fw + 0.01
            row = dict(ts=now(), margin_bid=q["margin"]["bid"], margin_ask=q["margin"]["ask"],
                       margin_fair=None if fm is None else round(fm, 4), margin_cashout=sig_m,
                       white_prot_bid=q["white_prot"]["bid"], white_prot_ask=q["white_prot"]["ask"],
                       white_reg_mid=fw, white_cashout=sig_w, was_mid=q["was"]["mid"], was7_mid=q["was7"]["mid"],
                       margin_status=q["margin"]["status"], white_status=q["white_prot"]["status"],
                       margin_result=q["margin"]["result"], white_result=q["white_prot"]["result"])
            with CSV.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(row))
                if new:
                    w.writeheader()
                    new = False
                w.writerow(row)
            for k, s, msg in (("margin", sig_m, f"WAS 1-6: bid {row['margin_bid']} >= fair {row['margin_fair']}"),
                              ("white", sig_w, f"White 70+ prot: bid {row['white_prot_bid']} >= reg mid {fw} + 1c")):
                if s and not flags[k]:
                    say(f"CASH OUT signal ON  {msg}")
                elif not s and flags[k]:
                    say(f"cash-out signal off ({k})")
                flags[k] = s
            if q["margin"]["result"] and q["white_prot"]["result"]:
                say(f"settled: WAS1TO6={q['margin']['result']} White70prot={q['white_prot']['result']}")
                break
        except Exception as e:  # keep watching through transient errors
            say(f"error: {type(e).__name__}: {e}")
        time.sleep(10)
    say("watcher stopped")


if __name__ == "__main__":
    main()
