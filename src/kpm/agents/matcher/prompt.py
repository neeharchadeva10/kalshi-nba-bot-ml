"""Review prompt and packet construction (identical for session and API backends)."""
from __future__ import annotations

import hashlib
import json

SYSTEM_PROMPT = """You review pairs of prediction-market contracts, one from Kalshi and one from Polymarket.
A trader will use the Polymarket price as a fair-value reference for the Kalshi contract, so the
pair is only usable if both contracts pay $1 on the same real-world outcome of the same game.

Read both contracts' full titles, descriptions and resolution rules. Check: same teams, same game
and date, YES on both sides refers to the same team winning, and how each handles ties,
postponements, cancellations, overtime and settlement sources. List every settlement difference,
even unlikely ones. Lower your confidence below 0.9 for any difference that could plausibly change
which contract pays, or by how much, in a realistic scenario. Answer only with the JSON object."""


def packet(match: dict, kalshi: dict, poly: dict, game: dict) -> dict:
    """Everything the reviewer needs for one pair, with no prices (avoids hindsight)."""
    return {
        "match_id": match["match_id"],
        "rule_score": match["rule_score"],
        "rule_features": json.loads(match["rule_features"] or "{}"),
        "nflverse_game": {k: game.get(k) for k in ("game_id", "game_type", "week", "away_team", "home_team",
                                                   "kickoff_utc")},
        "kalshi": {k: kalshi.get(k) for k in ("contract_key", "event_key", "title", "subtitle", "outcome_label",
                                              "team_abbr", "rules_primary", "rules_secondary", "meta")},
        "polymarket": {k: poly.get(k) for k in ("platform", "contract_key", "event_key", "title", "subtitle",
                                                "outcome_label", "team_abbr", "description", "rules_secondary",
                                                "meta")},
    }


def user_message(pkt: dict) -> str:
    return "Review this contract pair:\n\n" + json.dumps(pkt, indent=2, ensure_ascii=False)


def prompt_hash(pkt: dict) -> str:
    return hashlib.sha256((SYSTEM_PROMPT + user_message(pkt)).encode()).hexdigest()[:16]
