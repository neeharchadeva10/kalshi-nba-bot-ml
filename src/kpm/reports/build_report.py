"""Step 9: assemble reports/output/report.md from analysis results + charts.

The data sections are generated. The interpretation (summary, conclusions, recommendation)
lives in reports/narrative.md, written after reading the results, and is placed on top.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from kpm.core import config, db
from kpm.core.config import PROJECT_ROOT, settings
from kpm.reports import charts

log = logging.getLogger(__name__)


def _fmt(v, nd=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    if isinstance(v, (int, np.integer)):
        return f"{v:,d}"
    return str(v).replace("|", "\\|")  # a raw pipe would split the markdown table cell


def md_table(df: pd.DataFrame, cols: list[str], headers: list[str] | None = None, nd: int = 2) -> str:
    headers = headers or cols
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(cols)]
    for r in df[cols].itertuples(index=False):
        lines.append("| " + " | ".join(_fmt(v, nd) for v in r) + " |")
    return "\n".join(lines)


def _ci(v) -> str:
    if isinstance(v, str):
        v = tuple(float(x) for x in v.strip("()").split(","))
    return f"[{v[0]:.2f}, {v[1]:.2f}]" if v and not any(np.isnan(v)) else "–"


def reverse_report() -> str:
    """reports/output/reverse_report.md from the `kpm reverse` results."""
    out = config.path("report_dir")
    res = out / "reverse"
    summary = json.loads((res / "summary.json").read_text(encoding="utf-8"))
    freq = pd.read_csv(res / "frequency.csv")
    conv = pd.read_csv(res / "convergence.csv")
    results = pd.read_csv(res / "results.csv").assign(ci=lambda d: d["ci95_cents"].map(_ci))
    narrative = PROJECT_ROOT / "reports" / "reverse_narrative.md"
    parts = [narrative.read_text(encoding="utf-8") if narrative.exists() else "# Reverse test\n"]
    parts.append(f"""
## Appendix R1. How often was Polymarket US cheap vs Kalshi?

Percent of valid minutes. *Poly edge* is Kalshi fair minus the Polymarket US ask minus its fee. *Arb* is the
locked profit of buying on Polymarket US plus the opposite side on Kalshi, after both fees.
Games: {summary['games_by_split']}.

{md_table(freq, ['split', 'ttk_bucket', 'games', 'valid_minutes', 'median_poly_spread_c', 'pct_poly_edge>1c',
                 'pct_poly_edge>2c', 'pct_poly_edge>5c', 'pct_arb>0c', 'pct_arb>1c', 'pct_arb>2c'],
          ['Split', 'Time to kickoff', 'Games', 'Valid minutes', 'Median Poly US spread (c)', 'Poly edge > 1c',
           'Poly edge > 2c', 'Poly edge > 5c', 'Arb > 0', 'Arb > 1c', 'Arb > 2c'], nd=3)}

## Appendix R2. When Polymarket US was > 2c cheap, who moved?

{md_table(conv, ['split', 'horizon', 'n_episodes', 'n_games', 'pct_kalshi_toward_poly', 'pct_poly_toward_kalshi',
                 'pct_both', 'pct_neither', 'mean_kalshi_move_c', 'mean_poly_move_c'],
          ['Split', 'Horizon', 'Episodes', 'Games', '% Kalshi→Poly US', '% Poly US→Kalshi', '% both', '% neither',
           'Mean Kalshi move (c)', 'Mean Poly US move (c)'])}

## Appendix R3. Strategy results (100 contracts, after both venues' fees)

Parameters chosen on weeks 9–13: {', '.join(f"{k}: `{v['params']}`" for k, v in summary['chosen'].items())}.

{md_table(results, ['strategy', 'split', 'params', 'n_trades', 'n_games', 'mean_pnl_cents', 'ci', 'total_pnl_usd',
                    'win_rate', 'trust'],
          ['Strategy', 'Split', 'Parameters', 'Trades', 'Games', 'Mean PnL/contract (c)', '95% CI (c, by game)',
           'Total PnL ($)', 'Win rate', 'Sample'])}

Trade-by-trade files: `reports/output/reverse/trades_*.csv`. Full parameter grid: `search_early.csv`.
""")
    path = out / "reverse_report.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    log.info("reverse report written: %s", path)
    return str(path)


def run() -> str:
    cfg = settings()
    out = config.path("report_dir")
    res = out / "results"
    chart_dir = out / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    summary = json.loads((res / "summary.json").read_text(encoding="utf-8"))
    freq = pd.read_csv(res / "frequency_by_split.csv")
    freq_ttk = pd.read_csv(res / "frequency_by_ttk.csv")
    freq_close = pd.read_csv(res / "frequency_by_closeness.csv")
    conv = pd.read_csv(res / "convergence_by_split.csv")
    eps = pd.read_csv(res / "episodes.csv")
    strat = pd.read_csv(res / "strategy_results.csv")
    search = pd.read_csv(res / "strategy_search_dev.csv")
    cal = pd.read_csv(res / "calibration_games.csv")
    cal_scores = pd.read_csv(res / "calibration_scores.csv")
    reso = pd.read_csv(res / "price_resolution_by_market.csv")
    N = cfg["fees"]["order_size"]

    trades = {}
    for s in ("gap_close", "hold"):
        parts = [pd.read_csv(res / f"trades_{s}_{sp}.csv") for sp in ("dev", "holdout")
                 if (res / f"trades_{s}_{sp}.csv").exists() and (res / f"trades_{s}_{sp}.csv").stat().st_size > 2]
        trades[s] = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    c = {
        "profile": charts.gap_profile(pd.read_csv(res / "gap_profile_by_hour.csv"), chart_dir / "gap_profile.png"),
        "freq": charts.gap_frequency(freq, chart_dir / "gap_frequency.png"),
        "dur": charts.duration_ecdf(eps, 0.02, chart_dir / "gap_duration.png"),
        "conv": charts.convergence_stacks(conv, 0.02, chart_dir / "convergence.png"),
        "pnl": charts.cumulative_pnl(trades, N, chart_dir / "cumulative_pnl.png"),
        "cal": charts.reliability(cal, chart_dir / "calibration.png"),
    }

    with db.session() as conn:
        n_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        status = dict(conn.execute("SELECT status, COUNT(*) FROM market_matches GROUP BY status").fetchall())
        no_kalshi = json.loads(conn.execute(
            "SELECT details FROM audit_log WHERE component='collector.kalshi' AND action='discover' "
            "ORDER BY id DESC LIMIT 1").fetchone()[0]).get("games_without_market", [])
        run_cfg = conn.execute("SELECT config_json FROM analysis_runs WHERE run_id=?",
                               (summary["run_id"],)).fetchone()[0]

    q = summary["quality"]
    narrative = PROJECT_ROOT / "reports" / "narrative.md"
    parts = [narrative.read_text(encoding="utf-8") if narrative.exists() else
             "# Kalshi vs Polymarket NFL gap study\n\n*(narrative not written yet)*\n"]

    # ---------- data & coverage ----------
    kal = reso[reso.platform == "kalshi"]["price_resolution_s"]
    pol = reso[reso.platform == "polymarket"]["price_resolution_s"]
    parts.append(f"""
## Appendix A. Data and coverage

- Season: **{cfg['season']}** ({n_games} regular-season and playoff games in nflverse).
- Games with no usable Kalshi `KXNFLGAME` market (excluded): {', '.join(no_kalshi) or 'none'}.
- Matched pairs by status: {', '.join(f'{k}: {v}' for k, v in sorted(status.items()))}. Only `approved` pairs are analyzed.
- Games in analysis: early weeks (dev, weeks 1–{cfg['split']['dev_last_week']}) **{q['games_in_analysis']['dev']}**,
  later weeks + playoffs (holdout) **{q['games_in_analysis']['holdout']}**.
- Window: the {cfg['collection']['window_before_kickoff_s'] // 86400} days before scheduled kickoff, strictly pre-game.
- Simulated order size: {N} contracts. Fee model: `{cfg['fees']['taker_multiplier']} × C × P × (1−P)`, rounding `{cfg['fees']['rounding']}`, taker only.

### Actual time resolution per market

| Venue | Markets | Median spacing (s) | 90th pct spacing (s) | Max spacing (s) |
|---|---|---|---|---|
| Kalshi (1-min candles) | {kal.notna().sum()} | {_fmt(float(kal.median()), 0)} | {_fmt(float(kal.quantile(0.9)), 0)} | {_fmt(float(kal.max()), 0)} |
| Polymarket (CLOB prices-history) | {pol.notna().sum()} | {_fmt(float(pol.median()), 0)} | {_fmt(float(pol.quantile(0.9)), 0)} | {_fmt(float(pol.max()), 0)} |

Polymarket series source counts: {q['poly_sources']}. The per-market list is in `results/price_resolution_by_market.csv`.
Polymarket's two outcome series sum to 1. Mean absolute deviation is {_fmt(q['poly_token_sum_abs_dev_c']['mean'], 4)}c,
95th pct {_fmt(q['poly_token_sum_abs_dev_c']['p95'], 4)}c. They are exact complements, so `prices-history` publishes one
series per market (a midpoint-style price). It carries **no bid/ask and no volume**, and it prints every minute even
when nothing changes.
""")

    # ---------- gap frequency ----------
    parts.append(f"""
## Appendix B. How often after-fee gaps exceeded 1, 2, 3 and 5 cents

An **episode** is a run of consecutive minutes where buying YES (or NO) at the Kalshi ask beats the
Polymarket price by more than the threshold **after** Kalshi's fee. **All minutes** uses every minute with
fresh data on both venues. **Polymarket active** keeps only minutes where the Polymarket price changed in
the previous {cfg['analysis']['poly_active_max_unchanged_s'] // 60} minutes. `prices-history` prints a point every minute even when nothing trades,
so the first view includes minutes where Polymarket's book was dormant.

![gap profile by hour](charts/{c['profile']})

![gap frequency](charts/{c['freq']})

The "Polymarket active" bars are taller on the left because their denominator is smaller: only active minutes
count. The number of episodes (right) barely changes, so most gaps happen while Polymarket's price is moving.

{md_table(freq.sort_values(['data', 'split', 'threshold_c']),
          ['data', 'split', 'threshold_c', 'n_games', 'pct_minutes_above', 'n_episodes', 'games_with_episode',
           'episodes_per_game', 'median_duration_min', 'p90_duration_min', 'pct_lasting_5m+'],
          ['Data', 'Split', 'Threshold (c)', 'Games', '% minutes above', 'Episodes', 'Games with ≥1', 'Episodes/game',
           'Median duration (min)', 'P90 duration (min)', '% lasting ≥5 min'])}

![gap duration](charts/{c['dur']})

### By expected closeness of the game

The game type comes from the de-vigged closing moneyline: toss-up = favorite under 60%, moderate = 60–75%,
heavy = favorite at 75% or more. The closing line is only known at kickoff, so this split describes what
happened by game type. It is not a filter you could have traded on.

{md_table(freq_close[freq_close.threshold_c.isin([1, 2])].sort_values(['data', 'split', 'closeness', 'threshold_c']),
          ['data', 'split', 'closeness', 'threshold_c', 'n_games', 'pct_minutes_above', 'n_episodes',
           'games_with_episode', 'episodes_per_game', 'median_duration_min'],
          ['Data', 'Split', 'Game type', 'Threshold (c)', 'Games', '% minutes above', 'Episodes', 'Games with ≥1',
           'Episodes/game', 'Median duration (min)'])}

### By time before kickoff (threshold 2c)

{md_table(freq_ttk[freq_ttk.threshold_c == 2].sort_values(['data', 'split', 'ttk_bucket']),
          ['data', 'split', 'ttk_bucket', 'valid_minutes', 'pct_minutes_above', 'n_episodes', 'games_with_episode',
           'median_duration_min'],
          ['Data', 'Split', 'Time to kickoff', 'Valid minutes', '% minutes above 2c', 'Episodes', 'Games with ≥1',
           'Median duration (min)'])}
""")

    # ---------- convergence ----------
    conv2 = conv[conv.threshold == 0.02].sort_values(["data", "split"])
    parts.append(f"""
## Appendix C. After a gap opened, who moved?

At the first minute of each episode, we measure how far the Kalshi mid moved toward the Polymarket price and
how far Polymarket moved toward Kalshi, after 5, 15 and 60 minutes and at kickoff. A move counts if it is at
least {cfg['analysis']['converge_tolerance'] * 100:.1f}c. Episodes whose horizon falls after kickoff are left out of that horizon.

![convergence](charts/{c['conv']})

{md_table(conv2, ['data', 'split', 'horizon', 'n_episodes', 'n_games', 'pct_kalshi_toward_poly',
                  'pct_poly_toward_kalshi', 'pct_both', 'pct_neither', 'mean_kalshi_move_c', 'mean_poly_move_c',
                  'median_closed_frac', 'trust'],
          ['Data', 'Split', 'Horizon', 'Episodes', 'Games', '% Kalshi→Poly', '% Poly→Kalshi', '% both', '% neither',
           'Mean Kalshi move (c)', 'Mean Poly move (c)', 'Median share of gap closed', 'Sample'])}
""")
    for dim, title in (("ttk_bucket", "time before kickoff"),
                       ("listing_bucket", "time since Polymarket opened the market"),
                       ("spread_bucket", "Kalshi bid-ask spread at gap open"),
                       ("volume_bucket", "Kalshi volume in the prior hour (dev terciles)"),
                       ("favorite", "favorite vs underdog (team being bought, closing line)"),
                       ("closeness", "expected closeness of the game (closing line)")):
        p = res / f"convergence_by_{dim}.csv"
        if p.exists():
            d = pd.read_csv(p)
            d = d[d.horizon.isin(["15m", "kickoff"])]
            parts.append(f"\n### Convergence by {title} (2c episodes)\n\n" + md_table(
                d, ['data', 'split', dim, 'horizon', 'n_episodes', 'n_games', 'pct_kalshi_toward_poly',
                    'pct_poly_toward_kalshi', 'pct_neither', 'median_closed_frac', 'trust'],
                ['Data', 'Split', 'Bucket', 'Horizon', 'Episodes', 'Games', '% Kalshi→Poly', '% Poly→Kalshi',
                 '% neither', 'Median share closed', 'Sample']) + "\n")
    parts.append(f"\nVolume tercile cut points (contracts in prior hour): {', '.join(f'{x:,.0f}' for x in q['volume_bucket_cuts'])}.\n")

    # ---------- strategies ----------
    main = strat[strat["breakdown"].isna()] if "breakdown" in strat else strat
    main = main.assign(ci=main["ci95_cents"].map(_ci))
    parts.append(f"""
## Appendix D. Strategy simulation (Kalshi only, after fees)

Entries are at the Kalshi ask, {N} contracts, and only count when Kalshi prints within ±{cfg['analysis']['fill_window_s']}s
show at least {N} contracts traded at our price or better. There is at most one open position per game.
**(a)** sells at the Kalshi bid when the Kalshi mid reaches the Polymarket price, or at the last pre-kickoff minute.
**(b)** holds the same entries to settlement. Parameters were chosen on the early weeks only:

- (a) gap close: `{summary['chosen']['gap_close']['params']}`. {summary['chosen']['gap_close']['note']}.
- (b) hold: `{summary['chosen']['hold']['params']}`. {summary['chosen']['hold']['note']}.

![cumulative pnl](charts/{c['pnl']})

Each line uses its own parameters chosen on the early weeks (above), so the two lines are different trade sets.

{md_table(main, ['strategy', 'split', 'params', 'n_trades', 'n_games', 'mean_pnl_cents', 'ci', 'total_pnl_usd',
                 'win_rate', 'max_drawdown_usd', 'trust'],
          ['Strategy', 'Split', 'Parameters', 'Trades', 'Games', 'Mean PnL/contract (c)', '95% CI (c, by game)',
           'Total PnL ($)', 'Win rate', 'Max drawdown ($)', 'Sample'])}
""")
    if "breakdown" in strat:
        bd = strat[strat["breakdown"].notna()]
        parts.append("\n### Chosen strategies broken down\n\n" + md_table(
            bd, ['strategy', 'split', 'breakdown', 'bucket', 'n_trades', 'n_games', 'mean_pnl_cents', 'win_rate',
                 'trust'],
            ['Strategy', 'Split', 'By', 'Bucket', 'Trades', 'Games', 'Mean PnL/contract (c)', 'Win rate', 'Sample']) + "\n")
    top = search.sort_values(["strategy", "mean_pnl_cents"], ascending=[True, False]).groupby("strategy").head(6)
    parts.append("\n### Parameter search on the early weeks (top 6 per strategy)\n\n" + md_table(
        top, ['strategy', 'params', 'signals', 'skipped_no_fill', 'n_trades', 'n_games', 'mean_pnl_cents', 'win_rate',
              'trust'],
        ['Strategy', 'Parameters', 'Signals', 'Skipped (no fill)', 'Trades', 'Games', 'Mean PnL/contract (c)',
         'Win rate', 'Sample']) + "\n\nThe full grid is in `results/strategy_search_dev.csv`.\n")

    # ---------- calibration ----------
    parts.append(f"""
## Appendix E. Is Polymarket a trustworthy fair price?

Home-team probability from each source vs the result (ties excluded). Lower is better for both scores.
"Closing line" is the de-vigged nflverse closing moneyline.

{md_table(cal_scores, ['horizon', 'source', 'n_games', 'brier', 'log_loss'],
          ['Horizon', 'Source', 'Games', 'Brier', 'Log loss'], nd=4)}

![calibration](charts/{c['cal']})
""")

    parts.append(f"\n## Appendix F. Reproducibility\n\nAnalysis run {summary['run_id']} config:\n\n```json\n"
                 f"{json.dumps(json.loads(run_cfg), indent=2)}\n```\n")
    report = "\n".join(parts)
    path = out / "report.md"
    path.write_text(report, encoding="utf-8")
    log.info("report written: %s", path)
    return str(path)
