"""Tests for #36 JOB 1: bot pause window and the 3PT selection's no-look-ahead rule."""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nba_teammate_out"))
import threes_download as D  # noqa: E402
import threes_selection as S  # noqa: E402

ET = ZoneInfo("America/New_York")


def ts(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET).timestamp()


def test_pause_window_edges():
    assert D.pause_seconds(ts(2026, 10, 22, 17, 29, 59)) == 0
    assert D.pause_seconds(ts(2026, 10, 22, 17, 30)) == 4 * 3600
    assert D.pause_seconds(ts(2026, 10, 22, 19, 0)) == 2.5 * 3600
    assert D.pause_seconds(ts(2026, 10, 22, 21, 29, 59)) == 1
    assert D.pause_seconds(ts(2026, 10, 22, 21, 30)) == 0
    assert D.pause_seconds(ts(2026, 10, 22, 3, 0)) == 0


def test_pause_window_dst_days():
    assert D.pause_seconds(ts(2026, 11, 2, 18, 0)) == 3.5 * 3600
    assert D.pause_seconds(ts(2026, 3, 9, 18, 0)) == 3.5 * 3600


def test_wait_if_paused_sleeps_through_and_never_returns_inside():
    clock = [ts(2026, 10, 22, 17, 31)]
    logs = []

    def sleep(d):
        clock[0] += d
    slept = D.wait_if_paused(clock=lambda: clock[0], sleep=sleep, logger=logs.append)
    assert D.pause_seconds(clock[0]) == 0
    assert clock[0] >= ts(2026, 10, 22, 21, 30)
    assert slept >= 3.9 * 3600 - 120
    assert logs and "paused for bot window" in logs[0]


def test_wait_if_paused_noop_outside():
    called = []
    assert D.wait_if_paused(clock=lambda: ts(2026, 10, 22, 12, 0), sleep=called.append) == 0
    assert not called


def test_ceiling_is_1_5():
    assert D.AIMD(ceil=D.CEIL).ceil == 1.5 and D.CEIL == 1.5


def test_path_is_public_historical_only():
    assert D.path_for("KXNBA3PT-X-Y-3") == "/historical/markets/KXNBA3PT-X-Y-3/candlesticks"


class FakeBox:
    def __init__(self, rows, threes):
        self.played = {1: [(d, g) for d, g in rows]}
        self.threes = threes


def _defs(date, strikes, team="NYK", pk="P1"):
    return [(f"K-{date}-{pk}-{int(s + 0.5)}", "3PT", date, "NYKSAS", team, pk, "Name", s, 1000, 1000) for s in strikes]


def test_selection_no_lookahead_and_two_nearest():
    rows = [(f"2026-01-{d:02d}", f"g{d}") for d in range(1, 11)] + [("2026-01-11", "g11")]
    threes = {(1, f"g{d}"): float(d) for d in range(1, 11)}
    threes[(1, "g11")] = 99.0                       # game on the game date itself: must not count
    box = FakeBox(rows, threes)
    defs = _defs("2026-01-11", [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5])
    out, skips, n = S.select_rows(defs, box, {("2026-01-11", "NYKSAS"): 5000}, {("NYK", "P1"): 1})
    assert out[0]["mean10"] == 5.5 and out[0]["n_prior"] == 10
    assert [r["floor_strike"] for r in out] == [5.5, 4.5]            # 4.5/6.5 tie -> lower strike
    assert [r["rank"] for r in out] == [1, 2]
    assert n == 1 and skips == {}


def test_selection_ignores_same_day_and_future_games():
    rows = [(f"2026-01-{d:02d}", f"g{d}") for d in range(1, 8)] + [("2026-01-08", "g8"), ("2026-01-09", "g9")]
    threes = {(1, r[1]): 2.0 for r in rows[:7]}
    threes[(1, "g8")] = 50.0
    threes[(1, "g9")] = 50.0
    box = FakeBox(rows, threes)
    out, _, _ = S.select_rows(_defs("2026-01-08", [1.5, 2.5, 3.5]), box, {("2026-01-08", "NYKSAS"): 5000}, {("NYK", "P1"): 1})
    assert out[0]["mean10"] == 2.0 and out[0]["floor_strike"] == 1.5


def test_selection_skips_counted():
    rows = [(f"2026-01-{d:02d}", f"g{d}") for d in range(1, 4)]
    box = FakeBox(rows, {(1, r[1]): 1.0 for r in rows})
    out, skips, _ = S.select_rows(_defs("2026-01-09", [1.5]), box, {("2026-01-09", "NYKSAS"): 5000}, {("NYK", "P1"): 1})
    assert out == [] and skips == {"fewer_than_5_prior_games": 1}
    out, skips, _ = S.select_rows(_defs("2026-01-09", [1.5]), box, {}, {("NYK", "P1"): None})
    assert skips == {"unmapped_player": 1}
    nod = [d[:8] + (None, None) for d in _defs("2026-01-09", [1.5])]
    out, skips, _ = S.select_rows(nod, box, {}, {("NYK", "P1"): 1})
    assert skips == {"no_tip_time": 1}
