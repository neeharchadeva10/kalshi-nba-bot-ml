"""Matcher pipeline: rules -> AI review -> gate -> manual review. Every decision is audited."""
from __future__ import annotations

import csv
import json
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path

from kpm.agents.matcher import rules
from kpm.agents.matcher.backends import ClaudeAPIBackend, SessionBackend
from kpm.agents.matcher.models import ReviewRecord
from kpm.agents.matcher.prompt import packet, prompt_hash
from kpm.core import audit, config, db
from kpm.core.config import settings
from kpm.core.teams import poly_slug_teams
from kpm.core.timeutil import now_ts, to_iso

log = logging.getLogger(__name__)
COMPONENT = "matcher"
AI_REVIEWERS_EXCLUDE = ("human",)


def _games(conn) -> dict[str, rules.GameRef]:
    return {g.game_id: g for g in (rules.GameRef(r["game_id"], r["home_team"], r["away_team"], r["kickoff_ts"])
                                   for r in conn.execute("SELECT * FROM games"))}


def _contracts(conn) -> list[rules.Contract]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM markets")]
    by_event: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r["platform"] == "kalshi":
            by_event[r["event_key"]].append(r["contract_key"])
    out = []
    for r in rows:
        meta = json.loads(r["meta_json"] or "{}")
        if r["platform"] == "kalshi":
            out.append(rules.Contract(
                platform="kalshi", market_id=r["market_id"], contract_key=r["contract_key"], event_key=r["event_key"],
                team=r["team_abbr"], event_teams=rules.kalshi_event_teams(r["event_key"], by_event[r["event_key"]]),
                event_date=rules.parse_kalshi_event_date(r["event_key"]),
                kickoff_hint_ts=meta.get("occurrence_ts"), expiry_hint_ts=meta.get("expected_expiration_ts"),
                market_type_ok=meta.get("market_type") == "binary"))
        else:  # polymarket (international) and polymarket_us share slug conventions
            away, home, date = poly_slug_teams(r["event_key"])
            out.append(rules.Contract(
                platform=r["platform"], market_id=r["market_id"], contract_key=r["contract_key"],
                event_key=r["event_key"], team=r["team_abbr"], event_teams=rules.norm_team_set(away, home),
                event_date=date, kickoff_hint_ts=meta.get("game_start_ts"),
                market_type_ok=(meta.get("sports_market_type") or "moneyline").lower() == "moneyline"))
    return out


POLY_PLATFORMS = ("polymarket", "polymarket_us")


def run_rules() -> None:
    cfg = settings()["matcher"]
    with db.session() as conn:
        games = _games(conn)
        glist = list(games.values())
        contracts = _contracts(conn)
        by_game: dict[str, dict[str, list[rules.Contract]]] = defaultdict(
            lambda: {"kalshi": [], **{p: [] for p in POLY_PLATFORMS}})
        for c in contracts:
            g = rules.find_game(glist, c.event_teams, c.event_date, c.kickoff_hint_ts)
            conn.execute("UPDATE markets SET game_id=? WHERE market_id=?", (g.game_id if g else None, c.market_id))
            if g is None:
                audit.log(conn, f"{COMPONENT}.rules", "no_game", "market", c.contract_key,
                          {"platform": c.platform, "teams": sorted(c.event_teams), "date": c.event_date})
                continue
            by_game[g.game_id][c.platform].append(c)

        n_pairs = 0
        for gid, sides in by_game.items():
            g = games[gid]
            for k, plat in ((k, plat) for plat in POLY_PLATFORMS for k in sides["kalshi"]):
                partners = [p for p in sides[plat] if p.team == k.team] or sides[plat]
                for p in partners:
                    score, feats = rules.score_pair(k, p, g, cfg["kickoff_tolerance_s"])
                    same = int(bool(k.team and k.team == p.team))
                    if not same:
                        continue  # cross-team pairs are just complements of the same-team pair
                    existing = conn.execute("SELECT match_id, status FROM market_matches WHERE kalshi_market_id=? AND "
                                            "poly_market_id=?", (k.market_id, p.market_id)).fetchone()
                    ts = now_ts()
                    if existing:
                        conn.execute("UPDATE market_matches SET game_id=?, rule_score=?, rule_features=?, "
                                     "matcher_version=?, updated_ts=? WHERE match_id=?",
                                     (gid, score, json.dumps(feats), cfg["version"], ts, existing["match_id"]))
                        mid = existing["match_id"]
                    else:
                        cur = conn.execute(
                            "INSERT INTO market_matches (game_id, kalshi_market_id, poly_market_id, same_outcome, "
                            "rule_score, rule_features, status, matcher_version, created_ts, updated_ts) "
                            "VALUES (?,?,?,?,?,?, 'pending', ?,?,?)",
                            (gid, k.market_id, p.market_id, same, score, json.dumps(feats), cfg["version"], ts, ts))
                        mid = cur.lastrowid
                    audit.log(conn, f"{COMPONENT}.rules", "scored_pair", "match", mid,
                              {"game_id": gid, "kalshi": k.contract_key, "venue": plat, "other": p.contract_key,
                               "team": k.team, "score": score, "features": feats})
                    n_pairs += 1
            kalshi_teams = {c.team for c in sides["kalshi"]}
            for plat in POLY_PLATFORMS:
                if not sides[plat]:
                    continue
                poly_teams = {c.team for c in sides[plat]}
                for t in kalshi_teams ^ poly_teams:
                    audit.log(conn, f"{COMPONENT}.rules", "unpaired_contract", "game", gid,
                              {"venue": plat, "team": t, "missing_on": plat if t in kalshi_teams else "kalshi"})
        log.info("rules: %d same-outcome pairs across %d games", n_pairs, len(by_game))


def _packets(conn, only_unreviewed: bool) -> list[dict]:
    q = """SELECT mm.*, g.game_type, g.week, g.away_team, g.home_team, g.kickoff_ts
           FROM market_matches mm JOIN games g USING (game_id)"""
    if only_unreviewed:
        q += " WHERE NOT EXISTS (SELECT 1 FROM match_reviews r WHERE r.match_id = mm.match_id AND r.reviewer != 'human')"
    out = []
    for m in conn.execute(q + " ORDER BY g.kickoff_ts, mm.match_id"):
        m = dict(m)
        k = dict(conn.execute("SELECT * FROM markets WHERE market_id=?", (m["kalshi_market_id"],)).fetchone())
        p = dict(conn.execute("SELECT * FROM markets WHERE market_id=?", (m["poly_market_id"],)).fetchone())
        for x in (k, p):
            x["meta"] = json.loads(x.pop("meta_json") or "{}")
        game = {"game_id": m["game_id"], "game_type": m["game_type"], "week": m["week"], "away_team": m["away_team"],
                "home_team": m["home_team"], "kickoff_utc": to_iso(m["kickoff_ts"])}
        out.append(packet(m, k, p, game))
    return out


def export_review_packets(batch_size: int = 50, only_unreviewed: bool = True) -> list[Path]:
    with db.session() as conn:
        pkts = _packets(conn, only_unreviewed)
        backend = SessionBackend(config.path("review_dir") / "review_packets")
        paths = backend.export(pkts, batch_size)
        audit.log(conn, f"{COMPONENT}.review", "export_packets", None, None,
                  {"n": len(pkts), "files": [str(p) for p in paths]})
    log.info("exported %d review packets to %d files", len(pkts), len(paths))
    return paths


def _save_review(conn, rec: ReviewRecord, reviewer: str, phash: str | None) -> None:
    conn.execute(
        "INSERT INTO match_reviews (match_id, reviewer, same_event, settlement_differences, confidence, reason, "
        "raw_json, prompt_hash, created_ts) VALUES (?,?,?,?,?,?,?,?,?)",
        (rec.match_id, reviewer, 1 if rec.same_event == "yes" else 0, json.dumps(rec.settlement_differences),
         rec.confidence, rec.reason, rec.model_dump_json(), phash, now_ts()))
    audit.log(conn, f"{COMPONENT}.review", "review_saved", "match", rec.match_id,
              {"reviewer": reviewer, **rec.model_dump()})


def import_reviews(path: str, reviewer: str = "claude-code-session") -> int:
    p = Path(path)
    files = sorted(p.glob("*.json")) if p.is_dir() else [p]
    n = 0
    with db.session() as conn:
        pk = {x["match_id"]: x for x in _packets(conn, only_unreviewed=False)}
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            for item in data:
                rec = ReviewRecord.model_validate(item)
                if rec.match_id not in pk:
                    raise ValueError(f"{f}: unknown match_id {rec.match_id}")
                _save_review(conn, rec, reviewer, prompt_hash(pk[rec.match_id]))
                n += 1
    log.info("imported %d reviews from %d files", n, len(files))
    gate()
    return n


def run_api_reviews(limit: int | None = None) -> int:
    cfg = settings()["matcher"]
    backend = ClaudeAPIBackend(cfg["claude_model"], cfg["allow_paid_api"])  # raises unless explicitly allowed
    with db.session() as conn:
        pkts = _packets(conn, only_unreviewed=True)[:limit]
        for pkt in pkts:
            res = backend.review(pkt)
            _save_review(conn, ReviewRecord(match_id=pkt["match_id"], **res.model_dump()), backend.name, prompt_hash(pkt))
            conn.commit()
    gate()
    return len(pkts)


MANUAL_FIELDS = ["match_id", "game_id", "kickoff_utc", "kalshi_ticker", "poly_event", "poly_outcome", "team",
                 "rule_score", "ai_same_event", "ai_confidence", "ai_settlement_differences", "ai_reason",
                 "flag_reasons", "approve", "reviewer_note"]


def _latest(conn, match_id: int, human: bool) -> sqlite3.Row | None:
    op = "=" if human else "!="
    return conn.execute(f"SELECT * FROM match_reviews WHERE match_id=? AND reviewer {op} 'human' "
                        "ORDER BY review_id DESC LIMIT 1", (match_id,)).fetchone()


def gate() -> dict:
    """Approve pairs that pass rules and AI review; everything else goes to the manual CSV."""
    thr = settings()["matcher"]["approve_threshold"]
    counts: dict[str, int] = defaultdict(int)
    manual_rows = []
    with db.session() as conn:
        q = """SELECT mm.*, g.kickoff_ts, k.contract_key kt, p.event_key pe, p.outcome_label po
               FROM market_matches mm JOIN games g USING (game_id)
               JOIN markets k ON k.market_id = mm.kalshi_market_id JOIN markets p ON p.market_id = mm.poly_market_id
               ORDER BY g.kickoff_ts"""
        for m in conn.execute(q).fetchall():
            human, ai = _latest(conn, m["match_id"], True), _latest(conn, m["match_id"], False)
            flags = []
            if m["rule_score"] < thr:
                flags.append(f"rule_score {m['rule_score']:.2f} < {thr}")
            if ai is None:
                flags.append("no AI review yet")
            else:
                if not ai["same_event"]:
                    flags.append("AI: not the same event")
                if ai["confidence"] < thr:
                    flags.append(f"AI confidence {ai['confidence']:.2f} < {thr}")
            if human is not None:
                status, why = ("approved" if human["same_event"] else "rejected"), "human decision"
            elif ai is None:
                status, why = "pending", "awaiting AI review"
            elif flags:
                status, why = "manual_review", "; ".join(flags)
            else:
                status, why = "approved", "rules and AI review passed"
            if status != m["status"]:
                conn.execute("UPDATE market_matches SET status=?, updated_ts=? WHERE match_id=?",
                             (status, now_ts(), m["match_id"]))
            audit.log(conn, f"{COMPONENT}.gate", "status", "match", m["match_id"],
                      {"status": status, "previous": m["status"], "why": why, "flags": flags})
            counts[status] += 1
            if status == "manual_review":
                manual_rows.append({
                    "match_id": m["match_id"], "game_id": m["game_id"], "kickoff_utc": to_iso(m["kickoff_ts"]),
                    "kalshi_ticker": m["kt"], "poly_event": m["pe"], "poly_outcome": m["po"],
                    "team": m["kt"].rsplit("-", 1)[-1], "rule_score": m["rule_score"],
                    "ai_same_event": None if ai is None else ("yes" if ai["same_event"] else "no"),
                    "ai_confidence": None if ai is None else ai["confidence"],
                    "ai_settlement_differences": None if ai is None else ai["settlement_differences"],
                    "ai_reason": None if ai is None else ai["reason"],
                    "flag_reasons": "; ".join(flags), "approve": "", "reviewer_note": ""})
    out = config.path("review_dir") / "manual_review.csv"
    if out.exists():  # never discard decisions typed into the CSV but not yet imported
        with open(out, newline="", encoding="utf-8") as fh:
            prior = {r["match_id"]: r for r in csv.DictReader(fh)}
        for r in manual_rows:
            old = prior.get(str(r["match_id"]))
            if old:
                r["approve"], r["reviewer_note"] = old.get("approve", ""), old.get("reviewer_note", "")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANUAL_FIELDS)
        w.writeheader()
        w.writerows(manual_rows)
    log.info("gate: %s; manual review CSV: %s (%d rows)", dict(counts), out, len(manual_rows))
    return dict(counts)


def import_manual_csv(path: str | None = None) -> int:
    """Apply your decisions: approve column 'yes' approves, 'no' rejects, blank leaves it pending."""
    p = Path(path) if path else config.path("review_dir") / "manual_review.csv"
    n = 0
    with db.session() as conn, open(p, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            v = (row.get("approve") or "").strip().lower()
            if v not in ("yes", "y", "no", "n", "true", "false", "1", "0"):
                continue
            ok = v in ("yes", "y", "true", "1")
            rec = ReviewRecord(match_id=int(row["match_id"]), same_event="yes" if ok else "no",
                               settlement_differences=[], confidence=1.0,
                               reason=row.get("reviewer_note") or ("approved by human" if ok else "rejected by human"))
            _save_review(conn, rec, "human", None)
            n += 1
    log.info("applied %d human decisions from %s", n, p)
    gate()
    return n
