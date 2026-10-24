"""Static PNG charts for the markdown report (matplotlib, light theme).

Palette: validated reference categorical slots, assigned in fixed order; thin marks,
recessive grid; every multi-series chart has a legend; text stays in ink colors.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # slots 1-4, fixed order
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"], "font.size": 10,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK, "axes.titlesize": 11,
    "axes.titleweight": "semibold", "axes.titlelocation": "left",
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.axisbelow": True,
    "legend.frameon": False, "legend.labelcolor": INK2, "lines.linewidth": 2,
})


def _save(fig, path: Path) -> str:
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path.name


COMBOS = [("dev", "all"), ("dev", "active"), ("holdout", "all"), ("holdout", "active")]


def _combo_label(split: str, data: str) -> str:
    return f"{split}, {'all minutes' if data == 'all' else 'Polymarket active'}"


def gap_frequency(freq: pd.DataFrame, path: Path) -> str:
    """Grouped bars: share of pre-game minutes with an after-fee edge above each threshold."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    thr = sorted(freq["threshold_c"].unique())
    x = np.arange(len(thr))
    w = 0.2
    for ax, col, title in ((axes[0], "pct_minutes_above", "Share of pre-game minutes with edge above threshold (%)"),
                           (axes[1], "episodes_per_game", "Gap episodes per game")):
        for i, (s, d) in enumerate(COMBOS):
            sub = freq[(freq["split"] == s) & (freq["data"] == d)].set_index("threshold_c").reindex(thr)
            ax.bar(x + (i - 1.5) * w, sub[col], width=w - 0.02, color=SERIES[i], label=_combo_label(s, d))
        ax.set_xticks(x, [f">{t}c" for t in thr])
        ax.set_title(title)
        ax.grid(axis="x", visible=False)
    axes[1].legend(loc="upper right", fontsize=8)
    return _save(fig, path)


def gap_profile(prof: pd.DataFrame, path: Path) -> str:
    """Left: share of minutes with an after-fee edge, by hours before kickoff. Right: Kalshi activity."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    p = prof.groupby("hour").apply(
        lambda d: pd.Series({
            "pct_above_1c": np.average(d["pct_above_1c"], weights=d["minutes"]),
            "pct_above_2c": np.average(d["pct_above_2c"], weights=d["minutes"]),
            "vol": float(d["median_k_volume_1h"].median()),
        }), include_groups=False).reset_index().sort_values("hour")
    axes[0].plot(p["hour"], p["pct_above_1c"], color=SERIES[0], label="edge > 1c after fee")
    axes[0].plot(p["hour"], p["pct_above_2c"], color=SERIES[1], label="edge > 2c after fee")
    axes[0].axvline(24, color=AXIS, linewidth=1, linestyle="--")
    axes[0].text(24, axes[0].get_ylim()[1] * 0.95, " 24h", color=MUTED, fontsize=8, va="top")
    axes[0].set_title("Share of minutes with a tradable gap (%)")
    axes[0].legend(loc="upper left", fontsize=8)
    axes[1].plot(p["hour"], p["vol"].clip(lower=1), color=SERIES[0])
    axes[1].set_yscale("log")
    axes[1].set_title("Kalshi contracts traded in the prior hour (median)")
    for ax in axes:
        ax.set_xlim(p["hour"].max(), 0)
        ax.set_xlabel("Hours before kickoff")
    return _save(fig, path)


def duration_ecdf(eps: pd.DataFrame, threshold: float, path: Path) -> str:
    fig, ax = plt.subplots(figsize=(7, 3.8))
    e = eps[eps["threshold"] == threshold]
    for i, (s, dv) in enumerate(COMBOS):
        d = np.sort(e.loc[(e["split"] == s) & (e["data"] == dv), "duration_min"].to_numpy())
        if len(d):
            ax.step(d, np.arange(1, len(d) + 1) / len(d), where="post", color=SERIES[i],
                    label=f"{_combo_label(s, dv)} (n={len(d):,})")
    ax.set_xscale("log")
    ax.set_xlabel("Episode duration (minutes, log scale)")
    ax.set_ylabel("Share of episodes lasting at most x")
    ax.set_title(f"How long after-fee gaps above {round(threshold * 100)}c last")
    ax.legend(loc="lower right")
    return _save(fig, path)


def convergence_stacks(conv: pd.DataFrame, threshold: float, path: Path) -> str:
    classes = [("pct_kalshi_toward_poly", "Kalshi moved toward Polymarket"),
               ("pct_poly_toward_kalshi", "Polymarket moved toward Kalshi"),
               ("pct_both", "Both moved"), ("pct_neither", "Neither / gap widened")]
    c = conv[conv["threshold"] == threshold]
    fig, axs = plt.subplots(2, 2, figsize=(10, 7), sharey=True)
    axes = axs.ravel()
    for ax, (s, dv) in zip(axes, COMBOS):
        sub = c[(c["split"] == s) & (c["data"] == dv)]
        labels = list(sub["horizon"])
        bottom = np.zeros(len(sub))
        for i, (col, lab) in enumerate(classes):
            vals = sub[col].fillna(0).to_numpy()
            ax.bar(labels, vals, bottom=bottom, color=SERIES[i], width=0.6, label=lab,
                   edgecolor=SURFACE, linewidth=1.5)
            bottom += vals
        for j, n in enumerate(sub["n_episodes"]):
            ax.text(j, 101, f"n={n:,}", ha="center", va="bottom", color=MUTED, fontsize=8)
        ax.set_ylim(0, 110)
        ax.set_title(f"{_combo_label(s, dv)}: episodes >{round(threshold * 100)}c", fontsize=10)
        ax.grid(axis="x", visible=False)
    axs[0, 0].set_ylabel("% of episodes")
    axs[1, 0].set_ylabel("% of episodes")
    axes[1].legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=8)
    return _save(fig, path)


def cumulative_pnl(trades: dict[str, pd.DataFrame], order_size: float, path: Path) -> str:
    """trades: {'gap_close': df, 'hold': df} each with split, t0 and pnl_<strategy> columns."""
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    for ax, s in zip(axes, ["dev", "holdout"]):
        for i, (strat, df) in enumerate(trades.items()):
            d = df[df["split"] == s].sort_values("t0") if not df.empty else df
            if d.empty:
                continue
            y = np.cumsum(d[f"pnl_{strat}"].fillna(0).to_numpy() * order_size)
            ax.plot(np.arange(1, len(y) + 1), y, color=SERIES[i],
                    label=f"{'Sell when gap closes' if strat == 'gap_close' else 'Hold to settlement'} ({len(y)})")
        ax.axhline(0, color=AXIS, linewidth=1)
        ax.set_title(f"{s}: cumulative PnL after fees ($, {int(order_size)} contracts/trade)")
        ax.set_xlabel("Trade number")
        ax.legend(loc="best", fontsize=8)
    return _save(fig, path)


def reliability(cal: pd.DataFrame, path: Path) -> str:
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], color=AXIS, linewidth=1, linestyle="--", label="Perfect calibration")
    t = cal[cal["y"] != 0.5]
    bins = np.linspace(0, 1, 11)
    for i, (col, lab) in enumerate((("poly_kickoff", "Polymarket"), ("kalshi_kickoff", "Kalshi mid"))):
        sub = t.dropna(subset=[col])
        idx = np.clip(np.digitize(sub[col], bins) - 1, 0, 9)
        xs, ys, ns = [], [], []
        for b in range(10):
            m = idx == b
            if m.sum() >= 5:
                xs.append(sub[col][m].mean())
                ys.append(sub["y"][m].mean())
                ns.append(m.sum())
        ax.plot(xs, ys, marker="o", markersize=6, color=SERIES[i], label=f"{lab} (n={len(sub)})",
                markeredgecolor=SURFACE, markeredgewidth=1.5)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Price just before kickoff (home team)")
    ax.set_ylabel("Share of games the home team won")
    ax.set_title("Calibration at kickoff (bins with >=5 games)")
    ax.legend(loc="upper left", fontsize=8)
    return _save(fig, path)
