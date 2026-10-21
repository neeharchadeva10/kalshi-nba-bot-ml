import pytest

from kpm.agents.matcher import rules
from kpm.agents.matcher.models import ReviewResult
from kpm.core import teams

DALPHI = rules.GameRef("2025_01_DAL_PHI", home="PHI", away="DAL", kickoff_ts=1757031600)  # 2025-09-04 20:20 ET
GAMES = [DALPHI, rules.GameRef("2025_15_PHI_DAL", home="DAL", away="PHI", kickoff_ts=1765746000)]


def _k(team="DAL", date="2025-09-04", expiry=1757031600 + 3 * 3600):
    return rules.Contract("kalshi", 1, f"KXNFLGAME-25SEP04DALPHI-{team}", "KXNFLGAME-25SEP04DALPHI", team,
                          frozenset({"DAL", "PHI"}), date, expiry_hint_ts=expiry)


def _p(team="DAL", date="2025-09-04", kickoff=1757031600):
    return rules.Contract("polymarket", 2, "tok", "nfl-dal-phi-2025-09-04", team, frozenset({"DAL", "PHI"}), date,
                          kickoff_hint_ts=kickoff)


def test_team_normalization():
    assert teams.norm_code("JAC") == "JAX"
    assert teams.norm_code("wsh") == "WAS"
    assert teams.norm_code("XYZ") is None
    assert teams.from_name("Los Angeles R") == "LA"
    assert teams.from_name("Chargers") == "LAC"
    assert teams.from_name("New York Jets") == "NYJ"
    assert teams.kalshi_ticker_team("KXNFLGAME-25SEP07CARJAC-JAC") == "JAX"
    assert teams.poly_slug_teams("nfl-hou-la-2025-09-07") == ("HOU", "LA", "2025-09-07")
    assert teams.poly_slug_teams("cowboys-vs-eagles-anytime-td") == (None, None, None)


def test_parse_kalshi_event():
    assert rules.parse_kalshi_event_date("KXNFLGAME-25SEP04DALPHI") == "2025-09-04"
    assert rules.parse_kalshi_event_date("KXNFLGAME-26JAN25LASEA") == "2026-01-25"
    assert rules.parse_kalshi_event_date("garbage") is None
    assert rules.kalshi_event_teams("X", ["KXNFLGAME-26JAN25LASEA-LA", "KXNFLGAME-26JAN25LASEA-SEA"]) == {"LA", "SEA"}


def test_find_game_uses_date_to_separate_rematches():
    teams_ = frozenset({"DAL", "PHI"})
    assert rules.find_game(GAMES, teams_, "2025-09-04").game_id == "2025_01_DAL_PHI"
    assert rules.find_game(GAMES, teams_, "2025-12-14").game_id == "2025_15_PHI_DAL"
    assert rules.find_game(GAMES, teams_, "2025-09-05").game_id == "2025_01_DAL_PHI"  # off-by-one tolerated
    assert rules.find_game(GAMES, teams_, "2025-10-20") is None
    assert rules.find_game(GAMES, frozenset({"DAL", "NYG"}), "2025-09-04") is None


def test_game_et_date_for_late_kickoff():
    # 20:20 ET on Sep 4 is 00:20 UTC Sep 5; the ET date must be used.
    assert DALPHI.et_date == "2025-09-04"


def test_perfect_pair_scores_one():
    score, f = rules.score_pair(_k(), _p(), DALPHI)
    assert score == pytest.approx(1.0)
    assert f["teams_match"] and f["outcome_team_match"] and f["date_match"]


def test_wrong_outcome_team_scores_below_threshold():
    score, f = rules.score_pair(_k("DAL"), _p("PHI"), DALPHI)
    assert not f["outcome_team_match"]
    assert score < 0.9


def test_kickoff_mismatch_and_date_mismatch_lower_score():
    s1, _ = rules.score_pair(_k(), _p(kickoff=1757031600 + 5 * 3600), DALPHI)
    assert s1 == pytest.approx(0.9)
    s2, _ = rules.score_pair(_k(date="2025-09-05"), _p(), DALPHI)
    assert s2 < 0.9


def test_missing_game_scores_low():
    score, _ = rules.score_pair(_k(), _p(), None)
    assert score < 0.5


def test_review_schema_validates():
    r = ReviewResult(same_event="yes", settlement_differences=["tie handling"], confidence=0.95, reason="ok")
    assert r.confidence == 0.95
    with pytest.raises(Exception):
        ReviewResult(same_event="maybe", confidence=0.5, reason="x")
    with pytest.raises(Exception):
        ReviewResult(same_event="yes", confidence=1.5, reason="x")
