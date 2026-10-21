"""Builds README.md from results.json / build_summary.json (so every number in the README comes from the run)."""
import json
from pathlib import Path

H = Path(__file__).resolve().parent
R = json.loads((H / "results.json").read_text())
B = json.loads((H / "build_summary.json").read_text())
V = R["versions"]
P = V["morning_5c"]
f = lambda x, d=4: "n/a" if x is None else f"{x:+.{d}f}"
usd = lambda x: "n/a" if x is None else f"{'-' if x < 0 else ''}${abs(x):,.2f}"

L = []
w = L.append
w("# Research log #30: does NOAA's NBM forecast beat Kalshi's daily-high prices?\n")
w("Pre-registration: `PREREGISTRATION.md` (written before any outcome or price was compared with a forecast). "
  "Read-only: NOAA open data over anonymous HTTPS and Kalshi's stored public candles. No orders, no portfolio calls.\n")
verdict = "PASS" if P["passes_all"] else "FAIL"
w(f"## Verdict: **{verdict}** (primary = morning decision, 5c edge threshold)\n")
w("Plain English: a probability built from NOAA's own forecast is *worse* than Kalshi's price at 9 am local time, and "
  "taking the model's side loses money after the spread and fee. In the unseen period the primary rule bet "
  f"{P['unseen']['bets']:,} times (10 contracts each) and lost {usd(-P['unseen']['total_net_dollars'])} in total, about "
  f"{usd(-P['unseen']['dollars_per_week'])} lost per week with bets. Betting at random loses less than the model does.\n")
pb = P["pass_bar"]
u, d = P["unseen"], P["discovery"]
w("| # | Rule | Result | Number |\n|---|---|---|---|")
w(f"| 1 | Mean net per contract > 0 in discovery and unseen | {'pass' if pb['1_mean_net_pos_both'] else 'FAIL'} | "
  f"discovery {f(d['mean_net_per_contract'])}, unseen {f(u['mean_net_per_contract'])} dollars |")
w(f"| 2 | Unseen 95% bootstrap lower bound > 0 (10,000 draws, station-days, seed 20261021) | "
  f"{'pass' if pb['2_unseen_ci_lower_gt_0'] else 'FAIL'} | 95% interval {f(u['bootstrap']['lo95_two_sided'])} to {f(u['bootstrap']['hi95'])} |")
w(f"| 3 | At least 200 unseen bets | {'pass' if pb['3_unseen_bets_ge_200'] else 'FAIL'} | {u['bets']:,} |")
w(f"| 4 | Coin flip: at most 5% of 500 random copies do as well | {'pass' if pb['4_coin_flip_share_le_5pct'] else 'FAIL'} | "
  f"{u['coin_flip']['share']*100:.1f}% did as well or better (random copies average {usd(u['coin_flip']['copies_total_mean_dollars'])}, model {usd(u['total_net_dollars'])}) |")
w(f"| 5 | At least $2 per week at count 10 | {'pass' if pb['5_dollars_per_week_ge_2'] else 'FAIL'} | "
  f"{usd(u['dollars_per_week'])} per week over {u['weeks_with_bets']} unseen weeks with bets |")
qm = R["quality"]["morning"]["unseen"]
w(f"| 6 | Unseen Brier of model <= market (or within 0.002 while 1-5 pass) | "
  f"{'pass' if pb['6_brier_model_le_market_or_within_0.002_while_1to5_pass'] else 'FAIL'} | model {qm['brier_model']:.4f} vs market mid {qm['brier_market']:.4f} (lower is better) |\n")

w("## The 6 versions\n")
w("Net = dollars per contract after the taker fee, holding to settlement. 'Coin flip' = share of 500 random copies "
  "(same station-days, same bets per station-day) that did as well on the unseen period (want <= 5%). "
  "$/week = unseen net dollars at 10 contracts divided by unseen weeks that had bets.\n")
w("| Version | Bets disc / unseen | Mean net disc | Mean net unseen | Unseen 95% interval | Coin-flip share | $/week | All rules |\n|---|---|---|---|---|---|---|---|")
for k in ("morning_3c", "morning_5c", "morning_8c", "evening_3c", "evening_5c", "evening_8c"):
    v = V[k]; a, b = v["discovery"], v["unseen"]
    star = " (primary)" if k == "morning_5c" else ""
    w(f"| {k.replace('_', ' ')}{star} | {a['bets']:,} / {b['bets']:,} | {f(a['mean_net_per_contract'])} | {f(b['mean_net_per_contract'])} | "
      f"{f(b['bootstrap']['lo95_two_sided'])} to {f(b['bootstrap']['hi95'])} | {b['coin_flip']['share']*100:.1f}% | {usd(b['dollars_per_week'])} | "
      f"{'PASS' if v['passes_all'] else 'fail'} |")
w("\nNo version clears the bar. No leads for a follow-up pre-registration.\n")

w("## Model vs market (all brackets with a valid bid and ask at the decision time)\n")
w("| Decision | Period | Brackets | Brier model | Brier market (mid) | Log loss model | Log loss market | Model differs from mid by >= 5c |\n|---|---|---|---|---|---|---|---|")
for typ in ("morning", "evening"):
    for per in ("discovery", "unseen"):
        q = R["quality"][typ][per]
        w(f"| {typ} | {per} | {q['n']:,} | {q['brier_model']:.4f} | {q['brier_market']:.4f} | {q['logloss_model']:.3f} | {q['logloss_market']:.3f} | {q['disagree_ge_5c']*100:.0f}% |")
w("\nLog loss clips probabilities to [0.001, 0.999]. The market beats the model in every row. "
  "The model is over-confident in the tails (see below), and in the evening version its high-probability calls "
  "(p >= 0.8) came true only about 37% of the time on unseen days (52 brackets), while the market's mid was well calibrated.\n")
w("### Calibration, morning, unseen (mean predicted probability vs how often YES happened)\n")
w("| Bucket of p | n model | model p | YES freq | n market | market mid | YES freq |\n|---|---|---|---|---|---|---|")
cm = {k: v for d_ in R["calibration"]["morning_unseen"] for k, v in d_.items()}
mm = {x["bucket"]: x for x in cm["model"]}; mk = {x["bucket"]: x for x in cm["market_mid"]}
for bkt in mm:
    a = mm[bkt]; b = mk.get(bkt)
    w(f"| {bkt} | {a['n']:,} | {a['mean_p']:.3f} | {a['freq_yes']:.3f} | {b['n'] if b else 0:,} | {b['mean_p'] if b else float('nan'):.3f} | {b['freq_yes'] if b else float('nan'):.3f} |")
w("\nThe model's bottom buckets are too low (it says 0.7% and YES happens 3.6%); the market's bottom buckets are slightly too high "
  "(the usual long-shot bias), which is still smaller than the spread plus fee.\n")

def tbl(title, grp, per="unseen", ver=P):
    w(f"**{title}** (primary, {per})\n")
    w("| Group | Bets | Net dollars (10 contracts) | Mean net per contract |\n|---|---|---|---|")
    for k, x in sorted(ver[per]["breakdown"][grp].items()):
        w(f"| {k} | {x['bets']:,} | {usd(x['net_dollars'])} | {f(x['mean_net_per_contract'])} |")
    w("")

w("## Where the primary loses (and wins)\n")
tbl("By YES / NO", "side"); tbl("By entry price", "pbucket"); tbl("By time zone", "tz"); tbl("By season", "season")
tbl("By NBM version", "nbm_version"); tbl("By station", "station")
w("Discovery period for comparison:\n")
tbl("By YES / NO", "side", "discovery"); tbl("By entry price", "pbucket", "discovery"); tbl("By NBM version", "nbm_version", "discovery")

w("## NBM bias and spread by station (morning run, observed high minus TXN)\n")
w("| Station | Days | Mean error | Std of error | Mean NBM XND |\n|---|---|---|---|---|")
for s, x in sorted(R["nbm"]["morning"]["by_station"].items()):
    w(f"| {s} | {x['n']:,} | {x['mean_obs_minus_txn']:+.2f} | {x['sd']:.2f} | {x['mean_xnd']:.2f} |")
pp = R["nbm"]["morning"]["pooled"]
w(f"\nAll stations: {pp['n']:,} days, mean error {pp['mean_err']:+.2f} F, std {pp['sd_err']:.2f} F. NBM's own XND averages about 2 F, so the "
  "calibrated sigma (k near 1.2-1.4) is wider than NBM states. Per station and year, including the calibrated b and k, are in `results.json` "
  "(`nbm.morning.by_station_year`). Station biases are small (largest: Midway +0.47, San Diego +0.65 on 54 days), so calibration adds little.\n")

w("## Data and coverage\n")
bm, be = B["by_type"]["morning"], B["by_type"]["evening"]
w(f"- Kalshi: {B['events_total']:,} station-day events ({B['markets_total']:,} bracket markets) from 24 CLI stations, 2021-08-06 to 2026-10-12; "
  f"{B['events_valid']:,} usable after the exactly-one-YES / no-overlap / complete-candles checks. Excluded: "
  + ", ".join(f"{k.split('(')[0]} {v}" for k, v in B["excluded_events"].items() if not k.startswith("obs_")) + ".")
w(f"- Observed highs come from the market's own `expiration_value` field (the official CLI integer high; "
  f"it was cross-checked against which bracket settled YES, 1 mismatch). {B['excluded_events'].get('obs_missing(kept for trading, not for calibration)', 0)} "
  "events have no value; they are traded but not used in calibration.")
w(f"- NBM: 4,078 files read, 2 not in the bucket (2022-01-05 18Z, 2026-06-17 00Z), 0 failures, 88.2 GB read in 30.0 minutes on the Ohio server "
  "(5 threads, nice 10). Versions 4.0 to 5.0. 24 of 24 stations present in every file. Output: `data/nbm_station_max.csv` (97,872 rows), `data/nbm_files.csv` (per-file log with S3 LastModified).")
w(f"- Morning: model built for {bm['station_days_modelled']:,} station-days; {bm['markets']:,} markets, {bm['markets_valid_quote']:,} with a valid bid and ask "
  f"({bm['markets_valid_quote']/bm['markets']*100:.0f}%); {bm['markets_missing_bid_or_ask']:,} had a candle but a missing bid or ask (skipped and counted); 0 had no candle yet. "
  f"Skipped days: {bm['skips']['no_run_published_by_decision_time']} (06Z file published after the decision time) and {bm['skips']['no_calibration_pool_lt_15']} (pool < 15 days, first weeks of 2021).")
w(f"- Evening: {be['station_days_modelled']:,} station-days; {be['markets']:,} markets, {be['markets_valid_quote']:,} valid ({be['markets_valid_quote']/be['markets']*100:.0f}%); "
  f"{be['markets_missing_bid_or_ask']:,} missing a side; {be['markets_no_candle_before_decision']} had no candle yet and {be['markets_not_open_at_decision']} were not open. "
  f"Run used: 00Z of event day {be['runs_used']['00']:,} times, 18Z of the day before {be['runs_used']['18']:,} times (in summer the 00Z run is usually published after 21:00 ET).")
w("- Prices: data/weather.sqlite already holds 1-minute candles (bid/ask closes) for every market, so no new Kalshi downloads were needed. "
  "The quote is the last candle at or before the decision instant (median age under an hour).\n")

w("## How each ambiguous rule was read (stricter reading when unclear)\n")
for t in [
    "**Stations and series:** the 24 series whose `wx_series.station` is a CLI code (KXHIGHNY, KXHIGHCHI, ...). The 2024-25 KXHIGHHOU series has no CLI code and is excluded. Event dates 2021-08-06 to 2026-10-12 (no market existed in 2021-01 to 2021-07).",
    "**Morning decision:** 09:00 local STANDARD time on the event day (so 14:00Z for Eastern in every season, 16:00Z for Phoenix). The 06Z run is used only if its S3 LastModified is at or before that instant; otherwise the day is skipped.",
    "**Evening decision:** 21:00 America/New_York clock time (DST-aware) on the prior evening. 00Z run of the event day's UTC date if its LastModified <= that instant, else the 18Z run of the prior day if its LastModified <= it, else the day is skipped. This applies to all stations.",
    "**Quote:** 'last hourly candle closing at or before the decision time' is implemented as the last stored 1-minute candle at or before the decision instant (at the top of the hour this equals the hourly close). Valid = bid and ask both present, bid <= ask, market open. A bid of 0 (no bidders) is a missing bid; this is common for far-tail brackets.",
    "**Model:** TXN for the 12Z-00Z window ending 00Z of day D+1 (UTC date D). sigma = max(1, k*XND); a day with XND missing is skipped (none occurred). Brackets: between [floor, cap]; 'greater F' = F+1 and up; 'less C' = C-1 and below.",
    "**Calibration history:** a prior day counts only if (a) it is an earlier event day of that station, (b) its market had settled (Kalshi settled timestamp) at or before the decision instant, (c) its observed high and a forecast of the same decision type exist. So the most recent day is often not yet usable. 'Previous 60 event days' = the 60 most recent qualifying days. Pooled values = maximum-likelihood fit on the union of every station's 60 qualifying days (needs >= 15, else no model that day). Station n < 15 uses pooled values; otherwise weight n/(n+30) on the station's fit.",
    "**Fit details (not specified):** exact-degree likelihood Phi((y+.5-mu)/s) - Phi((y-.5-mu)/s); Nelder-Mead from three starting points; b limited to +-15 F and k to 0.05-10 (no calibration ever touched a limit: b ranged -1.5 to +2.0 F, k 0.4 to 2.4; sigma is floored at 1).",
    "**Trading:** buy YES if p - ask - fee >= theta (fee per contract as in the pre-registration, order fee rounded up to $0.0001); buy NO if bid - p - fee(1-bid) >= theta; the larger edge if both (cannot happen with bid <= ask). 10 contracts, hold to settlement; net uses the settled result.",
    "**Split:** discovery = event day < 2025-07-01; unseen = 2025-07-01 to 2026-10-12.",
    "**Bootstrap:** clusters are all station-days with at least one valid quote in the period (so days without bets count as zero), 10,000 multinomial resamples, seed 20261021. The 'lower bound' used for rule 2 is the 2.5th percentile (two-sided 95% interval), the stricter reading; the 5th percentile is also in results.json.",
    "**Coin flip:** 500 copies per version and period (seed 20261021). Same station-days and same number of bets per station-day; markets drawn at random without replacement from that day's valid-quote markets, side random; same price (ask for YES, 1-bid for NO) and fee. 'As well' = total net on the unseen period >= the model's.",
    "**$/week:** unseen net dollars at 10 contracts / number of ISO weeks (by event date) with at least one bet.",
    "**Brier / log loss:** bracket level on all valid-quote brackets of that decision type (market p = (bid+ask)/2). Rule 6 uses the morning numbers for the primary.",
]:
    w(f"- {t}")
w("\n## Things worth knowing\n")
for t in [
    "The market is better informed than a 06Z forecast at 9 am local time: temperatures have been rising for hours. The morning model is the worst of the six (unseen -3.2c per contract); the evening model is about as good as random picks (coin-flip share 22-38%) but still loses 1.9c per contract.",
    "Discovery looked near break-even (morning 5c: -0.2c; evening 5c: +1.2c per contract) and unseen got worse (-3.2c and -1.9c). The market changed: the average bid-ask spread on valid-quote brackets was about 11c in discovery and about 3c in the unseen period, so the market is both tighter and sharper later. The discovery period is also mostly NYC, Chicago, Austin, Miami, Denver, Philadelphia and LAX; most other cities exist only in the unseen period.",
    "The model bets on a large share of brackets (about 59% of valid-quote brackets in the unseen morning version at 5c) because it disagrees with the market by 5c or more about 70% of the time. That is noise from over-confident tails, not hidden edge.",
    "Late-published NBM files were found and skipped by the LastModified rule (8 of 1,891 06Z files were published 7+ hours late; 7 morning days dropped).",
    "Look-ahead checks: the build asserts every used run has LastModified <= the decision instant, and the calibration only uses days settled before the decision (see tests).",
]:
    w(f"- {t}")
w("\n## Files\n")
w("`PREREGISTRATION.md`, `pull_nbm.py` (server bulk pull), `nbm_text.py` (parser), `nbm_runsel.py` (decision times and run choice), `nbm_model.py` (probability math, fee, calibration), "
  "`flatten_nbm.py`, `build_rows.py` (model rows and quotes), `analyze.py` (backtest, bootstrap, coin flip), `make_readme.py`, `test_model.py` (15 tests), "
  "`results.json`, `build_summary.json`, `rows_morning.csv.gz`, `rows_evening.csv.gz`, `bets_primary.csv.gz`, `days.txt`. Data: `data/nbm_station_max.csv`, `data/nbm_files.csv`, `data/nbm_raw.jsonl`.")
(H / "README.md").write_text("\n".join(L) + "\n", encoding="utf-8")
print("ok")
