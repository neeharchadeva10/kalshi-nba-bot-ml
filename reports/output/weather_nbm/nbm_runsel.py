"""Decision-time and NBM-run selection (pre-registration #30). Pure functions, no I/O.

Morning decision: 09:00 local STANDARD time on the event day D, run = 06Z of D, valid only if its S3 LastModified
is <= the decision instant.
Evening decision: 21:00 America/New_York (clock time, DST-aware) on D-1; run = 00Z of D if its S3 LastModified is
<= the decision instant, else 18Z of D-1 (if its LastModified is <= the decision instant, else no run).
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
# standard (non-DST) UTC offsets in hours of the station's market time zone (CLI day = local standard time)
STD_OFFSET = {"America/New_York": -5, "America/Chicago": -6, "America/Denver": -7,
              "America/Phoenix": -7, "America/Los_Angeles": -8}


def morning_decision_utc(day, tz):
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return datetime(d.year, d.month, d.day, 9, tzinfo=timezone.utc) - timedelta(hours=STD_OFFSET[tz])


def evening_decision_utc(day):
    d = date.fromisoformat(day) if isinstance(day, str) else day
    prev = d - timedelta(days=1)
    return datetime(prev.year, prev.month, prev.day, 21, tzinfo=ET).astimezone(timezone.utc)


def choose_evening(day, lm00, lm18):
    """lm00/lm18: aware datetimes or None (missing). Returns '00', '18' or None."""
    dec = evening_decision_utc(day)
    if lm00 is not None and lm00 <= dec:
        return "00"
    if lm18 is not None and lm18 <= dec:
        return "18"
    return None


def choose_morning(day, tz, lm06):
    if lm06 is not None and lm06 <= morning_decision_utc(day, tz):
        return "06"
    return None
