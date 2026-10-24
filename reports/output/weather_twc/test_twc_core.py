import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from twc_core import *

def test_c10_roundtrip_all_whole_f():
    for f in range(-80, 140):
        assert c10_to_f(f_to_c10(f)) == f

def test_c10_known():
    assert c10_to_f(194) == 67   # 19.4 C = 66.9 F -> 67
    assert c10_to_f(0) == 32 and c10_to_f(-178) == 0 and c10_to_f(100) == 50

def test_market_interval():
    assert market_interval("between", 70, 71) == (70, 71)
    assert market_interval("greater", 92, None) == (93, POS)
    assert market_interval("less", None, 85) == (NEG, 84)

def mk(t, f, c, r): return dict(strike_type=t, floor=f, cap=c, result=r)

def test_event_between():
    ms = [mk("less", None, 60, "no"), mk("between", 60, 61, "no"), mk("between", 62, 63, "yes"),
          mk("between", 64, 65, "no"), mk("greater", 65, None, "no")]
    assert event_payout_interval(ms) == (62, 63, "ok")

def test_event_tails_and_gaps():
    assert event_payout_interval([mk("greater", 92, None, "yes"), mk("between", 90, 91, "no")]) == (93, None, "ok")
    assert event_payout_interval([mk("less", None, 85, "yes"), mk("between", 85, 86, "no")]) == (None, 84, "ok")
    # missing bracket in ladder: the paid set is wider than one bracket but still determined
    assert event_payout_interval([mk("between", 70, 71, "no"), mk("between", 74, 75, "no"),
                                  mk("between", 72, 73, "yes")]) == (72, 73, "ok")

def test_event_inconsistent_and_unresolved():
    assert event_payout_interval([mk("between", 70, 71, "yes"), mk("between", 72, 73, "yes")])[2] == "inconsistent"
    assert event_payout_interval([mk("between", 70, 71, "scalar")])[2] == "unresolved"
    assert event_payout_interval([mk("between", 70, 71, "no")])[2] == "no_yes"

def test_mismatch():
    assert mismatch(62, 62, 63) == 0 and mismatch(63, 62, 63) == 0
    assert mismatch(61, 62, 63) == 1      # TWC higher
    assert mismatch(65, 62, 63) == -2     # TWC lower
    assert mismatch(50, 93, None) == 43 and mismatch(95, None, 84) == -11

def test_parse_cli():
    t = ("CLIMATE REPORT\n...THE CENTRAL PARK NY CLIMATE SUMMARY FOR OCTOBER 20 2026...\nVALID TODAY AS OF 0400 PM LOCAL TIME.\n"
         "TEMPERATURE (F)\n TODAY\n  MAXIMUM         67    255 PM  80    1947  63      4       68\n  MINIMUM         50    302 AM  31\n")
    r = parse_cli(t)
    assert r == {"date": "2026-10-20", "max": 67, "min": 50, "final": False}
    t2 = t.replace("0400 PM LOCAL TIME", "1159 PM LOCAL STANDARD TIME").replace("MINIMUM         50", "MINIMUM         MM")
    r2 = parse_cli(t2)
    assert r2["final"] is True and r2["min"] is None


def test_parse_cli_morning_final():
    t = ("CLIMATE SUMMARY FOR OCTOBER 19 2026...\n\nCLIMATE NORMAL\n  MAXIMUM         61    255 PM  80\n"
         "  MINIMUM         50    302 AM  31\n")
    assert parse_cli(t) == {"date": "2026-10-19", "max": 61, "min": 50, "final": True}
