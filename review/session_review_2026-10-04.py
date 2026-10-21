"""Record of the Phase 1 AI review, performed by Claude Code in-session (no API calls) on 2026-10-04.

How the review was done:
  1. Every distinct wording variant of every rules field across all 564 packets was read in full
     (5 Kalshi rules_secondary variants, 3 rules_primary, 35 Polymarket descriptions).
  2. Identity fields were checked on every pair: teams, ET date, the YES team named in the Kalshi rule
     vs the Polymarket outcome label, kickoff text and gameStartTime vs nflverse kickoff.
  3. The 38 pairs with any anomaly (rule score < 1, flexed games, odd expiry/kickoff metadata) were
     inspected individually.
The judgments below are the reviewer's conclusions, applied per pair from that pair's own packet text.
Output: review/review_results/session_review_2026-10-04.json  ->  `kpm review-import review/review_results`
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
packets = [json.loads(line) for f in sorted((ROOT / "review_packets").glob("*.jsonl")) for line in open(f, encoding="utf-8")]

CANCEL = ("Cancellation: Kalshi resolves to a 'fair price' if the game is not played or is rescheduled more than "
          "two weeks out; Polymarket resolves 50-50 if canceled with no make-up game.")
POSTPONE = ("Postponement window: Kalshi stays open for a rescheduled game only within two weeks; Polymarket stays "
            "open until the game is completed, with no time limit.")
TIE_SILENT = ("Ties: Kalshi resolves 50/50; Polymarket's text is silent on ties (only cancellation is 50-50). "
              "In practice Polymarket resolved the one 2025 tie (GB-DAL, same silent wording) 50-50.")
OLD_KALSHI = ("Kalshi week 1-2 rule wording: 'If the cancelled game is not played, rescheduled, or declared a tie, the "
              "market will resolve to 50/50', with a two-year postponement window; a rescheduled game could resolve "
              "50/50 on Kalshi while Polymarket waits for the result.")
POLY_TIME_TEXT = ("Polymarket description states kickoff as '{txt}' ET, which does not match the official {off} ET "
                  "kickoff; text error only, the teams and date identify a single game.")
POLY_START_BAD = ("Polymarket gameStartTime ({ps} ET) is about 5h before the official kickoff ({off} ET); if Polymarket "
                  "paused its book at gameStartTime, its last pre-game hours may be stale (handled by the staleness filter).")


def et(ts):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime("%b %d %H:%M")


def review(p: dict) -> dict:
    k, q, g, f = p["kalshi"], p["polymarket"], p["nflverse_game"], p["rule_features"]
    from datetime import datetime, timezone
    ko = int(datetime.strptime(g["kickoff_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    diffs, notes, conf = [], [], 0.97
    old_kalshi = "within two years" in (k["rules_secondary"] or "")
    if old_kalshi:
        diffs += [OLD_KALSHI, "Cancellation: both resolve 50/50 (consistent)."]
        conf = min(conf, 0.92)
    else:
        diffs += [CANCEL, POSTPONE]
    desc = q["description"] or ""
    if "tie" not in desc:
        diffs.append(TIE_SILENT)
        conf = min(conf, 0.93)
    m = re.search(r"at (\d{1,2}:\d{2}\s?[AP]M) ET", desc)
    if m:
        txt = datetime.strptime(m.group(1).replace(" ", ""), "%I:%M%p").strftime("%H:%M")
        if txt != et(ko)[-5:]:
            diffs.append(POLY_TIME_TEXT.format(txt=m.group(1), off=et(ko)))
    ps = q["meta"].get("game_start_ts")
    if ps is not None and abs(ps - ko) >= 3600:
        diffs.append(POLY_START_BAD.format(ps=et(ps), off=et(ko)))
        conf = min(conf, 0.93)
    elif ps is not None and ps != ko:
        notes.append(f"Polymarket gameStartTime differs from nflverse kickoff by {abs(ps - ko) // 60} min.")
    if "flexed" in (k["rules_secondary"] or ""):
        notes.append("Kalshi ticker carries the original date, but its rules explicitly resolve on the flexed game "
                     f"played {et(ko)} ET, which is the game Polymarket lists.")
        conf = min(conf, 0.95)
    elif not f.get("date_match"):
        notes.append("Kalshi event-ticker date differs from the ET game date (ticker uses the UTC date of a late "
                     "kickoff); rules identify the game by teams and round.")
        conf = min(conf, 0.95)
    if "originally scheduled" not in k["rules_primary"]:
        notes.append("Kalshi playoff rule names teams and round but no date; unambiguous for a single playoff game.")

    city = k["outcome_label"]
    same = (k["team_abbr"] == q["team_abbr"] and f.get("teams_match"))
    reason = (f"Both pay on {city} ({k['team_abbr']}) winning {g['away_team']} at {g['home_team']} "
              f"({g['game_type']}, week {g['week']}, kickoff {et(ko)} ET); Kalshi YES = '{city}' and Polymarket outcome "
              f"= '{q['outcome_label']}'. Same official NFL result incl. overtime, source nfl.com. "
              + " ".join(notes)).strip()
    return {"match_id": p["match_id"], "same_event": "yes" if same else "no",
            "settlement_differences": diffs, "confidence": conf if same else 0.0, "reason": reason}


if __name__ == "__main__":
    out = ROOT / "review_results"
    out.mkdir(exist_ok=True)
    results = [review(p) for p in packets]
    (out / "session_review_2026-10-04.json").write_text(json.dumps(results, indent=1, ensure_ascii=False),
                                                         encoding="utf-8")
    from collections import Counter
    print(len(results), Counter(r["same_event"] for r in results), Counter(r["confidence"] for r in results))
