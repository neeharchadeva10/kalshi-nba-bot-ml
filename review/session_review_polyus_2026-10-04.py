"""Record of the AI review of Kalshi <-> Polymarket US pairs, performed by Claude Code in-session
(no API calls) on 2026-10-04, using the same method as session_review_2026-10-04.py:
  1. Every distinct wording variant across all 322 packets was read in full (4 Kalshi rules_secondary
     variants; 2 Polymarket US description templates in 19 time variants).
  2. Identity fields were checked on every pair: teams, ET date, the YES team named in the Kalshi rule vs the
     Polymarket US side, kickoff text and gameStartTime vs nflverse kickoff.
  3. Pairs with any anomaly (rule score < 1, flexed games, odd metadata) were inspected individually.
Output: review/review_results/session_review_polyus_2026-10-04.json -> `kpm review-import review/review_results`
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
packets = [json.loads(line) for f in sorted((ROOT / "review_packets").glob("*.jsonl")) for line in open(f, encoding="utf-8")]
packets = [p for p in packets if p["polymarket"].get("platform") == "polymarket_us"]

CANCEL = ("Cancellation: Kalshi resolves to a 'fair price' if the game is not played or is rescheduled more than "
          "two weeks out; Polymarket US resolves 50-50 if canceled with no make-up game.")
POSTPONE = ("Postponement window: Kalshi stays open for a rescheduled game only within two weeks; Polymarket US stays "
            "open until the game is completed, with no time limit.")
TIE_SILENT = ("Ties: Kalshi resolves 50/50; the Polymarket US description is silent on ties. No tie occurred in the "
              "games Polymarket US listed (weeks 9+); international Polymarket resolved the one 2025 tie 50-50.")
CITY_ONLY = ("Polymarket US description names teams by city only ('{city}'); the market's sides carry the full team "
             "name ('{name}'), which identifies the team unambiguously.")
TIME_TEXT = ("Polymarket US description states kickoff as '{txt}' ET, not the official {off} ET; text error only, the "
             "teams and date identify a single game.")
START_BAD = "Polymarket US gameStartTime ({ps} ET) is more than 1h from the official kickoff ({off} ET)."


def et(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET).strftime("%b %d %H:%M")


def review(p: dict) -> dict:
    k, q, g, f = p["kalshi"], p["polymarket"], p["nflverse_game"], p["rule_features"]
    ko = int(datetime.strptime(g["kickoff_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    diffs, notes, conf = [CANCEL, POSTPONE], [], 0.97
    desc = q["description"] or ""
    if "tie" not in desc.lower():
        diffs.append(TIE_SILENT)
        conf = min(conf, 0.93)
    team_name = json.loads(q["rules_secondary"] or "{}").get("team_name") or q["subtitle"] or ""
    for city in ("Los Angeles", "New York"):
        if team_name.startswith(city) and re.search(rf"{city}(?! [A-Z][a-z])", desc):
            diffs.append(CITY_ONLY.format(city=city, name=team_name))
    m = re.search(r"at (\d{1,2}:\d{2}\s?[AP]M) ET", desc)
    if m:
        txt = datetime.strptime(m.group(1).replace(" ", ""), "%I:%M%p").strftime("%H:%M")
        if txt != et(ko)[-5:]:
            diffs.append(TIME_TEXT.format(txt=m.group(1), off=et(ko)))
    ps = q["meta"].get("game_start_ts")
    if ps is not None and abs(ps - ko) >= 3600:
        diffs.append(START_BAD.format(ps=et(ps), off=et(ko)))
        conf = min(conf, 0.93)
    elif ps is not None and ps != ko:
        notes.append(f"Polymarket US gameStartTime differs from nflverse kickoff by {abs(ps - ko) // 60} min.")
    if "flexed" in (k["rules_secondary"] or ""):
        notes.append(f"Kalshi ticker carries the original date, but its rules resolve on the flexed game played {et(ko)} "
                     "ET, which is the date of this Polymarket US market.")
        conf = min(conf, 0.95)
    elif not f.get("date_match"):
        notes.append("Kalshi event-ticker date differs from the ET game date (UTC date of a late kickoff); rules "
                     "identify the game by teams and round.")
        conf = min(conf, 0.95)
    if "originally scheduled" not in k["rules_primary"]:
        notes.append("Kalshi playoff rule names teams and round but no date; unambiguous for a single playoff game.")
    same = k["team_abbr"] == q["team_abbr"] and bool(f.get("teams_match"))
    reason = (f"Both pay on {k['outcome_label']} ({k['team_abbr']}) winning {g['away_team']} at {g['home_team']} "
              f"({g['game_type']}, week {g['week']}, kickoff {et(ko)} ET); Kalshi YES = '{k['outcome_label']}', "
              f"Polymarket US side = '{q['outcome_label']}' ({team_name}). Same official NFL result incl. overtime, "
              "source nfl.com. " + " ".join(notes)).strip()
    return {"match_id": p["match_id"], "same_event": "yes" if same else "no", "settlement_differences": diffs,
            "confidence": conf if same else 0.0, "reason": reason}


if __name__ == "__main__":
    out = ROOT / "review_results"
    out.mkdir(exist_ok=True)
    results = [review(p) for p in packets]
    (out / "session_review_polyus_2026-10-04.json").write_text(json.dumps(results, indent=1, ensure_ascii=False),
                                                                encoding="utf-8")
    print(len(results), Counter(r["same_event"] for r in results), Counter(r["confidence"] for r in results))
    print("time-text mismatches:", sum(any(d.startswith("Polymarket US description states") for d in r["settlement_differences"]) for r in results),
          "| gameStartTime >1h off:", sum(any(d.startswith("Polymarket US gameStartTime") for d in r["settlement_differences"]) for r in results),
          "| city-only names:", sum(any(d.startswith("Polymarket US description names") for d in r["settlement_differences"]) for r in results))
