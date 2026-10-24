"""Research #59 forward tracking (READ-ONLY; public GETs only).  One run, safe to repeat every 30 minutes:

  a. refresh data/raw/nba_open/team_box_2027.parquet from the hoopR GitHub raw file if the local copy is older than 3 h;
  b. tonight's NBA games (ET "NBA night" = ET date of now - 6 h) from Kalshi's public milestones, exactly as the dashboard does;
  c. LOCK a prediction (frozen E1 and the user's formula B1, reused from reports/output/nba_score_model/main) for every game not yet
     locked whose scheduled start is >= 30 min away.  The first locked record is final: never overwritten, never duplicated;
  d. results from Kalshi live data at status closed/final (once per game); later a "check" record vs hoopR team_box_2027;
  e. logs/score_model/summary.json: cumulative metrics (pre-registered) + tonight's rows.

Nothing here touches orders, portfolio, accounts or keys.  Files: logs/score_model/{predictions,results}.jsonl, summary.json, raw/."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
STUDY = ROOT / "reports" / "output" / "nba_score_model"
MAIN = STUDY / "main"
BOX_DIR = ROOT / "data" / "raw" / "nba_open"
BOX_2027 = BOX_DIR / "team_box_2027.parquet"
HOOPR_URL = "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/team_box/parquet/team_box_2027.parquet"
LOGS = ROOT / "logs" / "score_model"
ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

LOCK_MIN = 30                     # pre-registered: locked prediction is written >= 30 min before the scheduled start
BOX_MAX_AGE_H = 3
STALE_RETRY_H = 0.25              # a stale box is re-downloaded if it is older than this
STALE_FALLBACK_MIN = 45           # amendment: a stale box delays the lock until <= 45 min before start, then locks with stale_box=true
MODELS = ("E1", "B1")
FINAL_STATUSES = {"closed", "complete", "completed", "final", "ended"}      # same set as the dashboard
BOOT_MIN_N = 10
SANE_TEAM = (60.0, 170.0)         # a prediction outside this is a bug, not a prediction: not locked

# Kalshi code -> hoopR abbreviation.  Built from the #57 FRANCHISE map (hoopR -> Kalshi code), current franchises only.
_NEEDED = ("ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN",
           "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS")


# ------------------------------------------------------------------ small utils
def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(10):          # Windows: replace fails with PermissionError while a reader has the file open
        try:
            os.replace(tmp, path)
            return
        except OSError:
            time.sleep(0.05)
    path.write_text(text, encoding="utf-8")        # last resort: never lose data
    try:
        tmp.unlink()
    except OSError:
        pass


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def append_jsonl_atomic(path: Path, records: list[dict]) -> None:
    """Rewrite the whole (small) file through a temp file so a crash never leaves a torn line."""
    if not records:
        return
    path = Path(path)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old and not old.endswith("\n"):
        old += "\n"
    atomic_write_text(path, old + "".join(json.dumps(r, sort_keys=True) + "\n" for r in records))


def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


class RunLock:
    """Single-run guard (exclusive create; a lock older than max_age_s is stale and taken over)."""

    def __init__(self, path: Path, max_age_s: float = 1200):
        self.path, self.max_age_s, self.held = Path(path), max_age_s, False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.max_age_s:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                break
        return self

    def __exit__(self, *a):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass


# ------------------------------------------------------------------ team codes
def kalshi_to_hoopr() -> dict:
    """Kalshi code -> hoopR code (GSW->GS, NYK->NY, SAS->SA, UTA->UTAH, WAS->WSH, NOP->NO, the rest identical)."""
    mods = load_model_modules()
    out = {}
    for hoopr, fr in mods["L"].FRANCHISE.items():
        if hoopr in ("NJ", "SEA"):             # relocated franchises: not current codes
            continue
        out[fr] = hoopr
    missing = [c for c in _NEEDED if c not in out]
    assert not missing, f"no hoopR code for Kalshi codes {missing}"
    return out


# ------------------------------------------------------------------ the frozen #59 code (reused, not re-implemented)
_MODS: dict | None = None


def load_model_modules() -> dict:
    """Import sm_lib / models / run_test from the frozen study.  Name clashes (models, features ...) are resolved in favour of the study."""
    global _MODS
    if _MODS is not None:
        return _MODS
    for name in ("sm_lib", "models", "run_test", "run_select"):
        m = sys.modules.get(name)
        if m is not None and Path(getattr(m, "__file__", "") or "").resolve().parent != MAIN.resolve():
            del sys.modules[name]
    sys.path.insert(0, str(MAIN))
    import sm_lib as S  # noqa: E402
    import models as M  # noqa: E402
    import run_test as RT  # noqa: E402
    _MODS = {"S": S, "M": M, "RT": RT, "L": S.L}
    return _MODS


# ------------------------------------------------------------------ box file refresh
def refresh_box(box_path: Path = BOX_2027, url: str = HOOPR_URL, now: dt.datetime | None = None, max_age_h: float = BOX_MAX_AGE_H,
                getter=None, force: bool = False) -> dict:
    """Download the live hoopR 2026-27 team box when the local copy is older than max_age_h.  Download -> temp file -> validate it parses
    (and is not smaller than the current copy) -> atomic replace.  Only team_box_2027.parquet is ever written."""
    now = now or dt.datetime.now(UTC)
    box_path = Path(box_path)
    rep = {"refreshed": False, "reason": ""}
    if box_path.exists() and not force:
        age_h = (now.timestamp() - box_path.stat().st_mtime) / 3600.0
        rep["age_h"] = round(age_h, 2)
        if age_h < max_age_h:
            rep["reason"] = "fresh"
            return rep
    if getter is None:
        import requests

        def getter(u):
            r = requests.get(u, timeout=60)
            r.raise_for_status()
            return r.content
    tmp_path = None
    try:
        content = getter(url)
        fd, tmp_name = tempfile.mkstemp(suffix=".parquet", prefix="team_box_2027_", dir=str(box_path.parent))
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        df = pd.read_parquet(tmp_path)
        need = {"game_id", "season_type", "game_date_time", "team_abbreviation", "opponent_team_abbreviation", "team_home_away",
                "team_score", "opponent_team_score", "turnovers", "total_turnovers", "field_goals_attempted"}
        if not need.issubset(df.columns):
            raise ValueError(f"missing columns {sorted(need - set(df.columns))}")
        if len(df) == 0:
            raise ValueError("empty file")
        if box_path.exists():
            old_n = len(pd.read_parquet(box_path, columns=["game_id"]))
            if len(df) < old_n:
                raise ValueError(f"new file has {len(df)} rows < current {old_n}")
        for _ in range(10):
            try:
                os.replace(tmp_path, box_path)
                tmp_path = None
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise OSError("could not replace the box file")
        rep.update(refreshed=True, reason="downloaded", rows=int(len(df)))
    except Exception as e:  # noqa: BLE001  (a failed refresh keeps the existing copy)
        rep["reason"] = f"refresh failed: {type(e).__name__}: {e}"
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return rep


# ------------------------------------------------------------------ placeholders + prediction
PH_BASE = -1_000_000            # placeholder game ids are negative, so they can never equal a real hoopR id


def placeholder_rows(template: pd.DataFrame, games: list[dict], fill_scale: float = 1.0) -> pd.DataFrame:
    """Two box rows (away, home) per game for tonight's games so the frozen feature builder / grid can produce their pre-game features.
    Every box-score number is the finite mean of `template` (a past season) x fill_scale; the dated rows are never used for a game's own
    features because every query takes rows strictly before the game's ET date (tests: the prediction is identical for different fills)."""
    num = template.select_dtypes(include=[np.number]).columns
    means = template[num].mean(numeric_only=True)
    rows = []
    for i, g in enumerate(games):
        when = pd.Timestamp(g["start_utc"]).tz_convert("America/New_York").as_unit("us")
        for team, opp, ha in ((g["away_hoopr"], g["home_hoopr"], "away"), (g["home_hoopr"], g["away_hoopr"], "home")):
            r = {c: (means[c] * fill_scale if c in means.index else "") for c in template.columns}
            r.update(game_id=PH_BASE - i, season=2027, season_type=2, game_date=str(when.date()), game_date_time=when,
                     team_abbreviation=team, opponent_team_abbreviation=opp, team_home_away=ha, team_winner=False)
            r["total_turnovers"] = r["turnovers"]
            rows.append(r)
    df = pd.DataFrame(rows, columns=list(template.columns))
    for c in template.columns:
        if c in ("game_date_time",):
            continue
        if pd.api.types.is_numeric_dtype(template[c]) and not pd.api.types.is_bool_dtype(template[c]):
            df[c] = df[c].astype(float)
    df["game_date_time"] = pd.to_datetime(df["game_date_time"])
    return df


def make_box_with_placeholders(real_box: pd.DataFrame, template: pd.DataFrame, games: list[dict], fill_scale: float = 1.0) -> pd.DataFrame:
    if not games:
        return real_box.copy()
    ph = placeholder_rows(template, games, fill_scale)
    return pd.concat([real_box, ph], ignore_index=True)


def predict_games(frozen: dict, real_box: pd.DataFrame, games: list[dict], fill_scale: float = 1.0) -> dict:
    """{key: {e1_away, e1_home, b1_away, b1_home, inputs...}} for `games` (dicts with key/away_hoopr/home_hoopr/start_utc).
    Mirrors run_test.forward_block, with the games appended as placeholder rows."""
    mods = load_model_modules()
    S, RT, L = mods["S"], mods["RT"], mods["L"]
    template = L.read_year(2026)
    template = template[template.season_type == 2]
    box = make_box_with_placeholders(real_box, template, games, fill_scale)
    L27 = S.FORWARD
    tg27 = L.team_games_from_box(box, L27)
    tg27["fr"], tg27["fr_opp"] = tg27.team.map(L.FRANCHISE), tg27.opp.map(L.FRANCHISE)
    tg26 = L.team_games_from_box(L.read_year(2026), "2025-26")
    tg26["fr"], tg26["fr_opp"] = tg26.team.map(L.FRANCHISE), tg26.opp.map(L.FRANCHISE)
    tg = pd.concat([tg26, tg27], ignore_index=True)
    F = S.build_features(tg, [S.FORWARD])
    ph_ids = {PH_BASE - i: g for i, g in enumerate(games)}
    Fp = F[F.game_id.isin(ph_ids)].reset_index(drop=True)
    assert len(Fp) == 2 * len(games), "placeholder games missing from the feature table"
    assert np.isfinite(Fp[S.FEATS].values.astype(float)).all(), "non-finite pre-game feature"

    def reader(year):
        if int(year) != S.FORWARD_YEAR:
            return L.read_year_fixed(year)
        tb = box.copy()
        if L.tov_diagnostics(real_box)["doubled_total_turnovers"]:
            tb["total_turnovers"] = tb["turnovers"]
        return tb

    pr = RT.season_predictions(frozen, Fp, S.FORWARD, list(MODELS), reader=reader)
    out = {}
    for gid, g in ph_ids.items():
        m = (Fp.game_id == gid).values
        home = Fp.home.values[m] > 0
        rec = {"key": g["key"]}
        for mod in MODELS:
            p = np.asarray(pr[mod])[m]
            rec[mod.lower() + "_away"] = float(p[~home][0])
            rec[mod.lower() + "_home"] = float(p[home][0])
        out[g["key"]] = rec
    return out


def box_stats(real_box: pd.DataFrame, game_date: dt.date) -> dict:
    """What the box contained when a game was locked: games dated before the game's ET date, newest date, and games on the day before."""
    L = load_model_modules()["L"]
    tg = L.team_games_from_box(real_box, "2026-27") if len(real_box) else pd.DataFrame(columns=["game_id", "date"])
    if not len(tg):
        return {"n_box_games": 0, "box_max_date": None, "box_games_prev_day": 0}
    d = pd.to_datetime(tg.date).dt.date
    prior = tg[d < game_date]
    prev = tg[d == game_date - dt.timedelta(days=1)]
    return {"n_box_games": int(prior.game_id.nunique()), "box_max_date": str(max(d)),
            "box_games_prev_day": int(prev.game_id.nunique())}


# ------------------------------------------------------------------ integrity of the frozen model
def check_integrity(study: Path = STUDY) -> str:
    """frozen.json must match frozen.sha256 and the audited code/inputs must be unchanged.  Returns the frozen.json SHA-256."""
    mods = load_model_modules()
    S = mods["S"]
    S.check_frozen_hash(study)
    S.check_audit_ok(study, MAIN, S.AUDIT_FILES)
    return sha256_file(Path(study) / "frozen.json")


# ------------------------------------------------------------------ schedule / live data (public GETs, same as the dashboard)
def default_api():
    import live_scores_dashboard as D
    return D.Kalshi()


def fetch_schedule(api, night: dt.date) -> dict:
    import live_scores_dashboard as D
    return D.scan_games(api, night)


def fetch_live_details(api, milestone_id: str) -> tuple[dict, dict]:
    j = api.get("/live_data/basketball_game/milestone/%s" % milestone_id, prio=True)
    return ((j.get("live_data") or {}).get("details") or {}), j


def is_final(det: dict) -> bool:
    return str(det.get("status") or "").lower() in FINAL_STATUSES


# ------------------------------------------------------------------ locking
def lockable(game: dict, now: dt.datetime, locked_keys: set) -> tuple[bool, str]:
    if game["key"] in locked_keys:
        return False, "already locked"
    start = parse_utc(game["start_utc"])
    if start - now < dt.timedelta(minutes=LOCK_MIN):
        mins = (start - now).total_seconds() / 60.0
        return False, ("started" if mins <= 0 else f"only {mins:.0f} min to start (< {LOCK_MIN})")
    return True, "ok"


def game_record(key: str, g: dict, k2h: dict) -> tuple[dict | None, str]:
    a, h = g.get("away"), g.get("home")
    if a not in k2h or h not in k2h:
        return None, f"unmapped team code {a}/{h}"
    return {"key": key, "away": a, "home": h, "away_hoopr": k2h[a], "home_hoopr": k2h[h], "start_utc": iso(parse_utc(g["start"])),
            "milestone_id": g.get("id")}, "ok"


def box_is_fresh(real_box: pd.DataFrame, game_date: dt.date, prev_games: dict | None, k2h: dict) -> tuple[bool, str]:
    """Amendment: the box must include every league game dated before the game's ET date.  Known from the Kalshi schedule of the
    previous night (prev_games: key -> game dict); no games on D-1 -> fresh.  prev_games None (schedule unavailable) -> fresh only
    if the box itself has a game dated D-1 or later."""
    tab = box_game_table(real_box)
    dates = set(tab.date) if len(tab) else set()
    prev = game_date - dt.timedelta(days=1)
    if prev_games is None:
        return (max(dates, default=dt.date.min) >= prev), "schedule unavailable; judged from box dates"
    if not prev_games:
        return True, "no league games on D-1"
    missing = []
    for key, g in prev_games.items():
        a, h = k2h.get(g.get("away")), k2h.get(g.get("home"))
        hit = a is not None and h is not None and len(tab) > 0 and bool(
            ((tab.away == a) & (tab.home == h) & tab.date.apply(lambda d: abs((d - prev).days) <= 1)).any())
        if not hit:
            missing.append(key)
    return (not missing), ("all D-1 games in box" if not missing else f"box missing {len(missing)} D-1 game(s): {','.join(sorted(missing))}")


def lock_predictions(now, night, games_in: dict, locked: dict, real_box: pd.DataFrame, box_mtime: str | None, predictor=None,
                     integrity=None, k2h=None, prev_sched=None) -> tuple[list[dict], dict]:
    """New locked records for tonight's lockable games + {key: reason} for the ones not locked."""
    k2h = k2h or kalshi_to_hoopr()
    skipped, todo = {}, []
    for key, g in sorted(games_in.items(), key=lambda kv: kv[1].get("start") or ""):
        rec, why = game_record(key, g, k2h)
        if rec is None:
            skipped[key] = why
            continue
        ok, why = lockable(rec, now, set(locked))
        if not ok:
            skipped[key] = why
            continue
        gd = parse_utc(rec["start_utc"]).astimezone(ET).date()
        fresh, fwhy = box_is_fresh(real_box, gd, prev_sched(gd) if prev_sched else {}, k2h)
        mins = (parse_utc(rec["start_utc"]) - now).total_seconds() / 60.0
        if not fresh and mins > STALE_FALLBACK_MIN:
            skipped[key] = f"stale box, waiting ({fwhy})"
            continue
        rec["stale_box"], rec["fresh_note"] = (not fresh), fwhy
        todo.append(rec)
    if not todo:
        return [], skipped
    try:
        sha = (integrity or check_integrity)()
    except SystemExit as e:
        for r in todo:
            skipped[r["key"]] = f"integrity check failed: {e}"
        return [], skipped
    L = load_model_modules()["L"]
    # a game whose two teams already have a box row on the game's ET date is not a pre-game: skip
    try:
        preds = (predictor or (lambda frozen, box, gs: predict_games(frozen, box, gs)))(
            json.loads((STUDY / "frozen.json").read_text()), real_box, todo)
    except Exception as e:  # noqa: BLE001
        for r in todo:
            skipped[r["key"]] = f"prediction error {type(e).__name__}: {e}"
        return [], skipped
    new = []
    for r in todo:
        p = preds.get(r["key"])
        if p is None:
            skipped[r["key"]] = "no prediction returned"
            continue
        vals = [p[k] for k in ("e1_away", "e1_home", "b1_away", "b1_home")]
        if not all(np.isfinite(v) and SANE_TEAM[0] <= v <= SANE_TEAM[1] for v in vals):
            skipped[r["key"]] = f"implausible prediction {vals}"
            continue
        gd = parse_utc(r["start_utc"]).astimezone(ET).date()
        st = box_stats(real_box, gd)
        new.append({"ts_utc": iso(now), "night": night.isoformat(), "key": r["key"], "start_utc": r["start_utc"], "away": r["away"],
                    "home": r["home"], "e1_away": p["e1_away"], "e1_home": p["e1_home"], "e1_total": p["e1_away"] + p["e1_home"],
                    "b1_away": p["b1_away"], "b1_home": p["b1_home"], "b1_total": p["b1_away"] + p["b1_home"], "frozen_sha": sha,
                    "milestone_id": r["milestone_id"], "stale_box": r["stale_box"],
                    "inputs": dict(st, fresh_note=r["fresh_note"], n_2026_27_box_games=st["n_box_games"], box_mtime_utc=box_mtime)})
    return new, skipped


# ------------------------------------------------------------------ results and checks
def box_game_table(real_box: pd.DataFrame) -> pd.DataFrame:
    """One row per game in the box: date, away, home (hoopR codes), away_pts, home_pts."""
    if not len(real_box):
        return pd.DataFrame(columns=["date", "away", "home", "away_pts", "home_pts", "game_id"])
    L = load_model_modules()["L"]
    tg = L.team_games_from_box(real_box, "2026-27")
    h = tg[tg.venue == "home"]
    a = tg[tg.venue == "away"].set_index("game_id")
    g = pd.DataFrame({"game_id": h.game_id.values, "date": pd.to_datetime(h.date.values).date, "home": h.team.values,
                      "home_pts": h.pts.values})
    g["away"] = a.loc[g.game_id.values, "team"].values
    g["away_pts"] = a.loc[g.game_id.values, "pts"].values
    return g


def new_results(now, preds: dict, results: list[dict], api, raw_dir: Path, live: dict, fetch=None) -> list[dict]:
    """Result records for locked games that have started, are not recorded yet, and whose Kalshi live data says closed/final.
    `live` is filled with {key: details} for started games (used by the summary)."""
    done = {r["key"] for r in results if r.get("type") == "result"}
    out = []
    for key, p in preds.items():
        if key in done or not p.get("milestone_id"):
            continue
        start = parse_utc(p["start_utc"])
        if start > now or now - start > dt.timedelta(days=3):
            continue
        try:
            det, raw = (fetch or fetch_live_details)(api, p["milestone_id"])
        except Exception as e:  # noqa: BLE001
            live[key] = {"error": type(e).__name__}
            continue
        live[key] = det
        a, h = det.get("away_points"), det.get("home_points")
        if is_final(det) and isinstance(a, (int, float)) and isinstance(h, (int, float)) and a > 0 and h > 0:
            try:
                atomic_write_text(Path(raw_dir) / f"final_{key}.json", json.dumps(raw, sort_keys=True))
            except OSError:
                pass
            out.append({"type": "result", "key": key, "away_pts": int(a), "home_pts": int(h), "total": int(a) + int(h),
                        "period": det.get("period"), "status": det.get("status"), "source": "kalshi_live", "ts": iso(now)})
    return out


def new_checks(now, results: list[dict], preds: dict, real_box: pd.DataFrame) -> list[dict]:
    """One check record per game once hoopR's team_box_2027 has it: agree / disagree with the Kalshi final."""
    have = {r["key"] for r in results if r.get("type") == "check"}
    res = {r["key"]: r for r in results if r.get("type") == "result"}
    todo = [k for k in res if k not in have and k in preds]
    if not todo or not len(real_box):
        return []
    tab = box_game_table(real_box)
    out = []
    for k in todo:
        p = preds[k]
        d0 = parse_utc(p["start_utc"]).astimezone(ET).date()
        m = tab[(tab.away == _hoopr(p["away"])) & (tab.home == _hoopr(p["home"]))
                & tab.date.apply(lambda d: abs((d - d0).days) <= 1)]
        if not len(m):
            continue
        r = res[k]
        ba, bh = int(m.away_pts.iloc[0]), int(m.home_pts.iloc[0])
        out.append({"type": "check", "key": k, "box_away_pts": ba, "box_home_pts": bh, "kalshi_away_pts": r["away_pts"],
                    "kalshi_home_pts": r["home_pts"], "agree": bool(ba == r["away_pts"] and bh == r["home_pts"]),
                    "source": "hoopR team_box_2027", "ts": iso(now)})
    return out


def _hoopr(code: str) -> str:
    return kalshi_to_hoopr()[code]


# ------------------------------------------------------------------ metrics
def per_game_arrays(preds: dict, results: list[dict], model: str):
    """Rows (two per game) for models.game_metrics, only for games with both a locked prediction and a result."""
    res = {r["key"]: r for r in results if r.get("type") == "result"}
    keys = sorted(k for k in res if k in preds)
    gid, home, y, p = [], [], [], []
    for i, k in enumerate(keys):
        pr, r = preds[k], res[k]
        m = model.lower()
        gid += [i, i]
        home += [0.0, 1.0]
        y += [float(r["away_pts"]), float(r["home_pts"])]
        p += [pr[m + "_away"], pr[m + "_home"]]
    return keys, np.array(gid), np.array(home), np.array(y), np.array(p)


def compute_metrics(preds: dict, results: list[dict]) -> dict:
    M = load_model_modules()["M"]
    out = {"n_games": 0}
    errs = {}
    for model in MODELS:
        keys, gid, home, y, p = per_game_arrays(preds, results, model)
        out["n_games"] = len(keys)
        if not len(keys):
            out[model] = None
            continue
        m = M.game_metrics(gid, home, y, p)
        out[model] = {k: m[k] for k in ("n_games", "total_mae", "pct_within_10", "total_rmse", "bias", "team_mae", "margin_mae",
                                        "winner_pct")}
        errs[model] = np.abs(m["err"])
    n = out["n_games"]
    if "E1" in errs and "B1" in errs:
        d = errs["E1"] - errs["B1"]
        pd_ = {"n_games": n, "diff_point": float(d.mean()), "definition": "total MAE of E1 minus total MAE of B1 (negative = E1 better)"}
        if n >= BOOT_MIN_N:
            point, lo, hi = M.paired_boot_ci(d)
            pd_.update(diff=point, lo=lo, hi=hi, draws=M.BOOT_N, seed=M.BOOT_SEED, ci_upper_below_zero=bool(hi < 0))
        else:
            pd_["note"] = f"CI needs n >= {BOOT_MIN_N}"
        out["paired_E1_minus_B1"] = pd_
    else:
        out["paired_E1_minus_B1"] = None
    return out


def tonight_rows(night: dt.date, games_in: dict, preds: dict, results: list[dict], skipped: dict, live: dict) -> list[dict]:
    res = {r["key"]: r for r in results if r.get("type") == "result"}
    chk = {r["key"]: r for r in results if r.get("type") == "check"}
    rows = []
    keys = set(games_in) | {k for k, p in preds.items() if p.get("night") == night.isoformat()}
    for key in sorted(keys, key=lambda k: (games_in.get(k, {}).get("start") or preds.get(k, {}).get("start_utc") or "", k)):
        g, p = games_in.get(key, {}), preds.get(key)
        row = {"key": key, "stale_box": bool((p or {}).get("stale_box")), "away": g.get("away") or (p or {}).get("away"), "home": g.get("home") or (p or {}).get("home"),
               "start_utc": iso(parse_utc(g["start"])) if g.get("start") else (p or {}).get("start_utc"),
               "locked": p is not None, "reason_not_locked": None if p is not None else skipped.get(key)}
        if p:
            row.update({k: p[k] for k in ("e1_away", "e1_home", "e1_total", "b1_away", "b1_home", "b1_total", "ts_utc")})
        r = res.get(key)
        if r:
            row.update(final_away=r["away_pts"], final_home=r["home_pts"], final_total=r["total"])
            if p:
                row.update(e1_error=p["e1_total"] - r["total"], b1_error=p["b1_total"] - r["total"])
            if key in chk:
                row["box_check"] = "agree" if chk[key]["agree"] else "DISAGREE"
        elif key in live and isinstance(live[key], dict) and "away_points" in live[key]:
            d = live[key]
            row.update(live_status=d.get("status"), live_period=d.get("period"), live_away=d.get("away_points"),
                       live_home=d.get("home_points"),
                       live_total=(d.get("away_points") or 0) + (d.get("home_points") or 0))
        rows.append(row)
    return rows


def build_summary(now, night, games_in, preds, results, skipped, live, frozen_sha, box_info) -> dict:
    disagree = [r for r in results if r.get("type") == "check" and not r.get("agree")]
    return {"updated_utc": iso(now), "night": night.isoformat(), "frozen_sha": frozen_sha, "n_locked": len(preds),
            "n_results": sum(1 for r in results if r.get("type") == "result"),
            "n_checks": sum(1 for r in results if r.get("type") == "check"), "box": box_info,
            "metrics": compute_metrics(preds, results),
            "metrics_by_freshness": {
                "fresh": compute_metrics({k: p for k, p in preds.items() if not p.get("stale_box")}, results),
                "stale_box": compute_metrics({k: p for k, p in preds.items() if p.get("stale_box")}, results)},
            "check_disagreements": disagree,
            "tonight": {"night": night.isoformat(), "rows": tonight_rows(night, games_in, preds, results, skipped, live)}}


# ------------------------------------------------------------------ one run
def run(now: dt.datetime | None = None, logs: Path = LOGS, box_path: Path = BOX_2027, api=None, schedule_fn=None, predictor=None,
        integrity=None, refresh=True, box_getter=None, fetch_live=None, log=print) -> dict:
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    logs = Path(logs)
    logs.mkdir(parents=True, exist_ok=True)
    night = (now.astimezone(ET) - dt.timedelta(hours=6)).date()          # = live_scores_dashboard.nba_night
    with RunLock(logs / ".run.lock") as lk:
        if not lk.held:
            log("another run holds the lock; exiting")
            return {"skipped": "locked"}
        box_rep = refresh_box(box_path, now=now, getter=box_getter) if refresh else {"refreshed": False, "reason": "disabled"}
        log("box:", box_rep)
        real_box = pd.read_parquet(box_path) if Path(box_path).exists() else pd.DataFrame()
        box_mtime = iso(dt.datetime.fromtimestamp(Path(box_path).stat().st_mtime, UTC)) if Path(box_path).exists() else None
        api = api or (None if schedule_fn else default_api())
        try:
            games_in = (schedule_fn or (lambda a, n: fetch_schedule(a, n)))(api, night)
        except Exception as e:  # noqa: BLE001
            log("schedule fetch failed:", type(e).__name__, e)
            games_in = {}
        log(f"night {night}: {len(games_in)} games on Kalshi")
        pred_path, res_path = logs / "predictions.jsonl", logs / "results.jsonl"
        preds = {}
        for r in read_jsonl(pred_path):
            preds.setdefault(r["key"], r)                     # first record wins, always
        results = read_jsonl(res_path)
        live: dict = {}
        # d. results first (needs only earlier locks)
        nr = new_results(now, preds, results, api, logs / "raw", live, fetch_live) if (api or fetch_live) else []
        append_jsonl_atomic(res_path, nr)
        results += nr
        # c. lock (amendment: only against a box that has every earlier league game, unless <= 45 min to start)
        cache: dict = {}

        def prev_sched(d):
            if d not in cache:
                try:
                    cache[d] = (schedule_fn or (lambda a, n: fetch_schedule(a, n)))(api, d - dt.timedelta(days=1))
                except Exception as e:  # noqa: BLE001
                    log("previous-night schedule failed:", type(e).__name__, e)
                    cache[d] = None
            return cache[d]
        new, skipped = lock_predictions(now, night, games_in, preds, real_box, box_mtime, predictor, integrity, prev_sched=prev_sched)
        if refresh and any(str(v).startswith("stale box") for v in skipped.values()):
            rep2 = refresh_box(box_path, now=now, getter=box_getter, max_age_h=STALE_RETRY_H)     # stale: re-download if > 15 min old
            if rep2.get("refreshed"):
                log("box re-downloaded because it was stale:", rep2)
                real_box = pd.read_parquet(box_path)
                box_mtime = iso(dt.datetime.fromtimestamp(Path(box_path).stat().st_mtime, UTC))
                box_rep = rep2
                new, skipped = lock_predictions(now, night, games_in, preds, real_box, box_mtime, predictor, integrity,
                                                prev_sched=prev_sched)
        keep = [r for r in new if r["key"] not in preds]
        append_jsonl_atomic(pred_path, keep)
        for r in keep:
            preds[r["key"]] = r
        for k, why in sorted(skipped.items()):
            log(f"not locked {k}: {why}")
        for r in keep:
            log(f"LOCKED{' (STALE BOX)' if r.get('stale_box') else ''} {r['key']}: E1 {r['e1_away']:.1f}-{r['e1_home']:.1f} ({r['e1_total']:.1f})  B1 ({r['b1_total']:.1f})")
        # d. checks vs hoopR
        nc = new_checks(now, results, preds, real_box)
        append_jsonl_atomic(res_path, nc)
        results += nc
        for r in nr:
            log(f"RESULT {r['key']}: {r['away_pts']}-{r['home_pts']} ({r['total']})")
        for r in nc:
            log(f"CHECK {r['key']}: {'agree' if r['agree'] else 'DISAGREE'}")
        # e. summary
        sha = next(iter(preds.values()))["frozen_sha"] if preds else None
        summ = build_summary(now, night, games_in, preds, results, skipped, live, sha,
                             {"mtime_utc": box_mtime, "refresh": box_rep, "rows": int(len(real_box))})
        atomic_write_text(logs / "summary.json", json.dumps(summ, indent=1, sort_keys=True))
        return summ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-refresh", action="store_true", help="do not download the hoopR file")
    a = ap.parse_args()
    s = run(refresh=not a.no_refresh)
    if "metrics" in s:
        print(json.dumps(s["metrics"], indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
