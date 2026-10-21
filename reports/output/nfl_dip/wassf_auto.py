"""Automatic in-game trader for WAS @ SF. The user approved it explicitly on 2026-10-20 ~00:48Z with a permission rule
for exactly this command. It repeats wassf_trade.scan(auto=True) about once a second. Every buy and sell goes through
wassf_trade.buy()/sell(), which re-price from the live order book and enforce the hard limits there: this game only;
margin buckets and protected-prop YES only; EV >= +1c; at most $1 and 10 contracts per buy; no adding; $3.50 of buys
in total tonight; immediate-or-cancel; sells are reduce_only and only above fair.

Stops: at 04:30Z, when logs/wassf_auto.STOP exists (create that file to stop it), or when the process is killed.
"""
import time
from datetime import datetime, timezone

import wassf_trade as w

END = datetime(2026, 10, 20, 4, 30, tzinfo=timezone.utc).timestamp()
STOP = w.ROOT / "logs" / "wassf_auto.STOP"

w.log("auto_started", end="04:30Z", stop_file=str(STOP))
while time.time() < END and not STOP.exists():
    try:
        w.scan(auto=True)
    except Exception as e:      # keep going on transient API errors; every error is logged
        w.log("auto_error", error=f"{type(e).__name__}: {e}")
    time.sleep(1)
w.log("auto_stopped", stop_file_present=STOP.exists())
