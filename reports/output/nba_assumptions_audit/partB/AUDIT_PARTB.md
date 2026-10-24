# Part B of research #63: judgment calls in the E1/E2 score model (DESCRIPTIVE)

Built by the Part B builder, 2026-10-22. Nothing here selects or changes a model. ~11 items were examined, so a "difference" the size of
sampling noise is expected somewhere; none of this is a pre-registered finding. Numbers come from `results_partB.json` (one run of
`run_partB.py`; J9 was re-run once alone after a NaN bug fix). E1 reproduction gate: 2022-26 total MAE = 14.79634 (4,923 games) - matches #59.

Verdict rule (declared in `pb_lib.py` before the numbers were read): largest swing in MAE across the alternatives <0.05 pt = DOESN'T MATTER,
0.05-0.15 = A LITTLE, >0.15 = MATTERS. J5-J9 and J11 use their own stated rules (in the JSON).

## One row per item

| Id | We assumed | Alternatives tried | The numbers | Verdict |
|---|---|---|---|---|
| J1 | Key player = 28+ mpg over his own last 10 games played | 24/28/32 mpg x {own last 10 played, team's last 10 games (DNP = 0 min, needs 5 team games)} x {#62 roster rule, active-roster rule}; 2022-26, 4,859 games with a close | Key players per team-game: 7.0 (24, own) / 4.8 (28, own) / 2.6 (32, own) / 1.5 (32, team). A game's 0/1/2+ group changes for 20-83% of games vs the 28-own definition. E1-minus-close MAE gap (pts): all-games about +0.5; in the 2+ group it ranges +0.54 to +1.13 across variants (CI half-width about 0.4); the 2+ group holds 3,242 games under 28-own but 170 under 32-team. **Finding: #62's roster rule keeps players who left the league after last season for the whole year** (e.g. Kevin Porter Jr. sits on HOU's 2024-01-15 roster with only last-season minutes), so "key players out" per team-game is 1.23 under #62 vs 0.82 with an active-roster rule. #62's own test split (1,730 of 2,462 games in "2+") is inflated the same way | **MATTERS** (0.59 pt swing in the 2+ gap; group membership very definition-dependent). The direction (E1 is worse than the close, more so with more key players out) is stable only in the active-roster definitions: 0.38 / 0.43 / 0.70 for 28-own-active |
| J2 | E2 rotation threshold 15 mpg | 10 / 15 / 20 | Variant-B ridge refit on 2021-23, scored on 2023-24 (1,231 games). Total MAE 14.8087 / 14.8066 / 14.8099; team MAE 9.2877 / 9.2874 / 9.2959. Paired CI of each vs 15: includes 0. E1 itself: 14.8263 / 9.3901. Descriptive, not a selection | **CHOICE DOESN'T MATTER** (swing 0.003 total, 0.009 team) |
| J3 | Player window = last 10 games | 5 / 10 / 20 | Total MAE 14.8097 / 14.8066 / 14.8021; team MAE 9.2861 / 9.2874 / 9.2862. 3x3 grid with J2: all 9 cells 14.802-14.811 | **CHOICE DOESN'T MATTER** (swing 0.008 total, 0.001 team) |
| J4 | Questionable kept separate (own feature) | separate / 0.5 weight / ignored (variant A) / counted as Out | Total MAE 14.8066 / 14.8367 / 14.8201 / 14.8467; team MAE 9.2874 / 9.2962 / 9.2781 / 9.3154. Paired CI vs separate includes 0 for all (counted-as-Out: -0.0007 to +0.082). Note E1 alone is 14.8263, so every E2-style layer is within about 0.02 of E1 on total MAE | **CHOICE DOESN'T MATTER** (swing 0.040 total, 0.037 team; the "counted as Out" version is the worst) |
| J5 | Player value = recent PPG | PPG vs minutes share vs box plus_minus per game, 2021-24, 7,382 team-games, residual = actual - E1 team points vs sum over Out/Doubtful rotation players | Own-team single regressor R^2: PPG 1.42% (r = -0.119), minutes share 1.18% (r = -0.108), plus_minus 0.08% (r = +0.028, wrong sign; it is outcome-driven). All three together 1.51%. Mean residual: -0.56 when an own rotation player is out, +1.32 otherwise | **CHOICE DOESN'T MATTER between PPG and minutes** (R^2 gap 0.25 percentage points). plus_minus is much worse. Every measure explains under 2% of the team-score residual |
| J6 | Unmatched listed names count as 0 | Who they are; residual when present | 5,229 unmatched Out/Doubtful/Questionable names = 9.2% of all listed names (97% are Out). By type: traded in / last box row for another team 55.0%; first-ever box row is this season (rookie or new) 38.1%; no NBA box row ever (two-way/G-League) 5.7%; same team, stale or unexplained 1.2%. 1,557 (30%) had a rotation-level history (mpg >= 15), all of them traded-in. Mean team residual, listed games: none unmatched -0.44 (n=8,111); >=1 unmatched -0.28 (n=4,066); >=1 rotation-likely unmatched -1.03 (n=1,399). Difference vs none for the rotation-likely group: -0.59 pt, CI -1.23 to +0.06 | **CHOICE DOESN'T MATTER (not distinguishable from 0)**. The point estimate is the right sign (E1 too high when a real rotation player is Out and unmatched, about 11% of listed team-games). Worth fixing in any future version: match traded-in players by name across teams |
| J7 | The T-60 report is the one that matters | Last report before tip vs the T-60 report (2021-26, 6,152 games, 4,042 with a later report, 43,206 listed players) | Out stays Out 99.7%; Doubtful -> Out 82.6% (still Doubtful 15%); Questionable -> Available 46.1%, Out 38.4%, still Questionable 15.2%; Probable -> Available 81%. Among Out/Doubtful/Questionable names at T-60 12.0% change status. 0.29 new Out/Doubtful names per team-game appear after T-60; 20.8% of team-games get at least one. Not weighted by player quality; 34% of games have no later report | **MATTERS A LITTLE** (Out is reliable; Questionable is a coin flip and a fifth of team-games get a late scratch) |
| J8 | League average settles after 50 games | 25 / 50 / 100 games vs the season's final value, 2019-20..2025-26 | Mean absolute error of the to-date league game total vs final: 3.86 pts at 25, 2.91 at 50, 2.55 at 100 games; E1's fallback (last season's final value) is 2.84. Ortg error 3.6 / 3.6 / 2.8; pace error 3.3 / 2.6 / 1.7. Sign changes by season (mean signed +0.3 / -1.2 / -1.7), so it is noise, not drift. Effect on E1 itself NOT measured (E1 is not refit, per the plan) | **MATTERS A LITTLE** by the stated rule (a more than 1 pt league-total error at 25 games), but 50 is not worse than the prior-season fallback and 100 is only 0.4 pt better. The league term is a shared level shift, not a per-game error source |
| J9 | +1c slippage vs the price seen | Every real fill with a recorded quote at order time | 21 taker fills paired, all 1-contract: 19 NBA fills vs the order book rebuilt from the ws recorder just before the fill (Oct 20-21: 15 lockbot, 4 manual early-overs) and 2 bot IOC fills vs the ask the bot read before sending. **Slippage was exactly 0.00c on all 21.** Manual early-overs taker fills vs the ping-time ask (minutes-hours earlier): +1, 0, -1, -1c (mean -0.25c, n=4). 3 maker fills filled at their limit (0c). Unpaired: 346 KXBTC15M, NFL and MVE fills (no cached quote), 3 lockbot fills with no snapshot, the 2 bot fills have no recorded book (bot's own quote only) | **MATTERS A LITTLE**: 1c/contract of assumed cost vs 0.0c realised, but n=21, one contract each, quote taken at the instant of the order (no decision-to-order delay), so it cannot justify dropping the 1c |
| J10 | Kalshi T-1h line = 0.50 crossing (spread <= 0.15) | spread <= 0.10; main-strike (closest to 0.50); volume-weighted crossing; 2025-26, 1,230 events, 1,214 common games | MAE vs the actual total: crossing-0.15 14.592, crossing-0.10 14.592, main-strike 14.609, volume-weighted 14.605 (common games). Paired differences vs baseline: -0.0001, +0.017, +0.014; all CIs include 0. Reference on the same games: sportsbook close 14.593, E1 15.169 | **CHOICE DOESN'T MATTER** (max swing 0.017 pt) |
| J11 | Fee = 0.07 C P (1-P) rounded up to $0.0001 | All 400 real fills in the latest ledger pull (`fee_cost`) | Taker fills (396): 90.7% match to the $0.0001; total fee $6.1507 real vs $6.1537 formula (0.05% apart); largest taker difference $0.00016. Integer counts (313): 99.7% exact. Fractional counts (87): 57% exact, the real fee is a few 1e-5 to 1e-4 off (e.g. 166.66 @ 0.028: real 0.31752 vs formula 0.3176; 0.01-contract fills round to 0 or $0.0001). Maker fills (4): the 3 NBA makers match the 0.0175 maker formula; one KXBTC15M maker fill paid 0 (formula 0.0492) | **CHOICE DOESN'T MATTER** for 1-contract NBA bets (all 10 KXNBATOTAL fills exact). Fractional-count rounding differs slightly from the formula but is below $0.001 per fill |

## Things that look odd (read before relying on any row)

1. **J1 roster inflation.** #62's "key player out" (and its 0/1/2+ test split) counts players whose last box row is last season, including
   those no longer in the league. This is not an error in Part B; it is a property of the #62 definition, shown here by comparing two roster
   rules. #62's headline claims (the total is unaffected; team scores improve) do not rest on that split, but the split should not be quoted.
2. **J9 zeros.** 21 of 21 exact zeros looks too clean. It is expected for 1-contract takes at the best ask on liquid books, and the
   reconstruction itself is validated by the 19 book-based fills all matching the fill price (the book is built independently of the fill).
   It says nothing about latency or larger sizes.
3. **J2-J4 are all within 0.04 pt** of each other and within 0.02 pt of E1 itself on 2023-24 total MAE: the injury layer barely moves the
   total whichever way it is defined. That is consistent with #62, not new.
4. **J5 plus_minus** is a result of the game, not a pre-game quality measure; its sign is positive (the opposite of what a quality measure should
   give). Do not read it as a usable feature.

## Method notes and ambiguities (most literal reading used)

- J1 "team's last 10 games (min 5)": mean minutes of the player over the team's last 10 games of this season, a missed game counting 0,
  requiring the team to have played at least 5 games; with fewer, E2's own-window value is used (6.1% of team-rows).
- J1 "key-out" is judged after the fact from the box (as #62's run_test does), never an input. Key-status itself uses only data before the game
  date (a toy look-ahead guard test covers it).
- J2/J3/J4: `e2_lib.build_injury_features` was called with `ROT_MPG`/`WINDOW` patched in memory (file untouched, values restored). The
  baseline rebuild equals the frozen `injury_features.parquet` exactly (asserted). Ridge lambda is chosen by leave-one-season-out CV as in #62.
  J4 "0.5 weight" = one feature out_pts + 0.5 q_pts (own and opp).
- J6 types: the unmatched name is looked up in all box rows (2020-21 onward) by the same normalisation. "Rookie or new" = his first box row in
  the data is this season (includes true rookies and anyone absent from the 2020-21+ files).
- J7 uses raw report names and statuses; "not listed" = dropped from the later report. Only games with a different, later report are
  compared.
- J10 volume-weighted crossing: candle-volume-weighted mid over the hour before T-1h, spread filter on the T-1h quote; zero-volume hours fall
  back to the last mid. Main strike = strike value itself (not interpolated). Quotes carried forward from the last candle at or before T-1h.
- J9 fills: the ledger's NBA fills were matched to `ledger_raw` fills by fill_id; position = `outcome_side`, quoted ask = 1 - best opposite bid.
  The recorder jsonl copy (1.4 GB) was deleted after the run; `pb_ws_extract.py` rebuilds it (about 15 min).

## Files

`partB/run_partB.py` (runner), `pb_lib.py`, `pb_j9_j10.py`, `pb_common.py`, `pb_ws_extract.py`, `test_partB.py` (34 tests), `results_partB.json`,
`_cache/e1_2021_26.parquet` (cached E1 predictions), logs.
