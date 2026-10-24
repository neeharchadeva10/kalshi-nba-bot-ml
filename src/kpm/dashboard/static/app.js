/* KPM bot monitor (localhost only).
   Polls the local JSON API every dashboard.refresh_s seconds and renders, top to bottom: the bot status for the strategy
   of the latest run (late_favorite or pair_h2), the money (summary tiles, a cumulative profit-and-loss chart, a result
   bar per trade and the loss-cap gauge, all hand-drawn SVG), a chart of the pair edge per strike (pair runs) and the
   tables. Every trade is described by its own status in words: settled and won or lost, open until its market settles,
   not filled, skipped and why, or paper only. No external dependencies. Data from the API is always inserted with
   textContent / createTextNode, never parsed as HTML. */
"use strict";

(function () {
  const WINDOW_MIN = 30;                 // chart window, minutes
  const RESEARCH_EVERY_MS = 15000;
  const PERF_EVERY_MS = 15000;           // the money figures are fetched again when the numbers change, or this often
  const MAX_BARS = 60;                   // result bars shown (the newest settled trades)
  const STALE_HEARTBEAT_S = 15;          // the bot beats at least every 5 s in every state (see server.py)
  const NOT_RESPONDING_S = 60;           // beyond this the headline stops saying LIVE / WATCH-ONLY
  const NO_HEARTBEAT_GRACE_S = 30;       // a run that has not beaten once after this long probably failed to start
  const SLOTS = 8;                       // categorical colour slots; extra strikes are drawn in grey
  const SCOPE_KEY = "kpm.dashboard.scope";   // the one thing remembered between visits: which runs the page shows
  const MINUS = "−";
  const DASH = "—";
  const SVGNS = "http://www.w3.org/2000/svg";

  const state = {
    refreshS: 2,
    skew: 0,                             // server clock minus browser clock, seconds
    status: null,
    opps: null,
    orders: null,
    events: null,
    research: null,
    lastResearchAt: 0,
    perf: null,                          // /api/performance: summary, settled trades with running totals, open trades
    perfSig: "",                         // what the last fetch was for (scope + the figures that change when a trade settles)
    perfNext: 0,
    perfError: "",
    pnl: null,                           // geometry of the profit-and-loss chart (for the hover layer)
    pnlHover: null,                      // index of the hovered trade
    pnlKeyboard: false,                  // the hover was set with the arrow keys: read the values aloud
    bars: null,
    barsHover: null,
    barsKeyboard: false,
    quotes: new Map(),                   // "ts|strike_ticker" -> quote row
    quotesRun: undefined,
    lastQuoteTs: null,
    slotOf: new Map(),                   // strike_ticker -> colour slot; sticky, so a colour follows its strike
    hidden: new Set(),
    zoom: "all",
    scope: null,                         // "latest" | "all": what the person chose (and the browser remembered), else decided at the first poll
    oppFilter: "all",                   // trades table: every signal, or only the ones that put real money to work
    hoverTs: null,
    hoverKeyboard: false,                // the hover was set with the arrow keys: read the values aloud
    chart: null,
    bannerKey: null,                     // what the banners show now; they are rebuilt only when it changes
    connected: null,
    connError: "",
    openEvents: new Set(),
    h1Open: false,
    timer: null,
  };

  // Keys the bot may use inside bot_heartbeat.detail_json (first match wins). A bare "balance" is NOT accepted as dollars:
  // Kalshi's own balance field of that name is an integer number of CENTS.
  const DETAIL_KEYS = {
    hour: ["market_ticker", "hour_ticker", "current_hour", "hour_market", "market_15m", "current_market"],
    loss: ["loss_used_usd", "loss_used", "loss_usd", "cum_loss_usd", "cumulative_loss_usd"],
    balance: ["balance_usd", "balance_dollars", "kalshi_balance_usd"],
    balanceCents: ["balance_cents"],
    error: ["last_error", "error"],
    heartbeatEvery: ["heartbeat_s", "heartbeat_interval_s"],
  };
  // Shown elsewhere on the page, so left out of the "More from the bot's heartbeat" list.
  const KNOWN_DETAIL = new Set(Object.values(DETAIL_KEYS).flat().concat(["strategy", "mode", "status", "max_loss_usd", "kill_switch"]));
  const STRATEGIES = {
    late_favorite: { kind: "late", name: "Late favorite", title: "Late favorite bot", sub: "Kalshi BTC 15-minute · localhost monitor" },
    pair_h2: { kind: "pair", name: "Pair H2", title: "BTC pair bot", sub: "Kalshi hourly vs 15-minute · localhost monitor" },
  };

  // ------------------------------------------------------------------ small helpers
  const $ = (id) => document.getElementById(id);
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const serverNow = () => Date.now() / 1000 + state.skew;

  function numOrNull(v) {
    if (isNum(v)) return v;
    if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
    return null;
  }
  function pick(obj, keys) {
    if (!obj || typeof obj !== "object") return undefined;
    for (const k of keys) if (obj[k] !== undefined && obj[k] !== null && obj[k] !== "") return obj[k];
    return undefined;
  }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function S(tag, attrs, text) {
    const e = document.createElementNS(SVGNS, tag);
    if (attrs) for (const k of Object.keys(attrs)) e.setAttribute(k, String(attrs[k]));
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function td(text, cls, title) {
    const c = el("td", cls, text);
    if (title) c.title = title;
    return c;
  }
  const GLYPH = { good: "✓", info: "i", warn: "!", serious: "!", crit: "×", none: "•" };
  function icon(tone) { return el("span", "ico " + (tone || ""), GLYPH[tone] || GLYPH.none); }
  function emptyRow(cols, text) {
    const tr = el("tr");
    const c = td(text, "empty");
    c.colSpan = cols;
    tr.appendChild(c);
    return tr;
  }
  function clip(s, n) { s = String(s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  // ------------------------------------------------------------------ formatting
  function fixed(v, dp) {
    return Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  function fmtInt(v) { return isNum(v) ? Math.round(v).toLocaleString("en-US") : DASH; }
  function fmtCount(v) {
    if (!isNum(v)) return DASH;
    return Number.isInteger(v) ? v.toLocaleString("en-US") : fixed(v, 2);
  }
  // Money: 2 decimals from $1 up, up to 4 below $1 so fees and sub-cent amounts stay visible.
  function fmtUsd(v, sign) {
    if (!isNum(v)) return DASH;
    const a = Math.abs(v);
    let s = a >= 1 || a === 0 ? fixed(a, 2) : fixed(a, 4).replace(/(\.\d{2}\d*?)0+$/, "$1");
    s = "$" + s;
    if (v < 0 && s !== "$0.00") return MINUS + s;
    return sign && v > 0 ? "+" + s : s;
  }
  function fmtCentsSigned(c) {
    if (!isNum(c)) return DASH;
    if (Math.abs(c) < 0.005) return "0.00¢";
    return (c < 0 ? MINUS : "+") + fixed(c, Math.abs(c) < 10 ? 2 : 1) + "¢";
  }
  function fmtEdge(dollars) { return isNum(dollars) ? fmtCentsSigned(dollars * 100) : DASH; }
  function fmtPrice(dollars) {             // a price or cost in dollars -> cents
    if (!isNum(dollars)) return DASH;
    return String(Number((dollars * 100).toFixed(2))) + "¢";
  }
  function fmtTime(ts) {
    if (!isNum(ts)) return DASH;
    const d = new Date(ts * 1000);
    const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    if (d.toDateString() === new Date(serverNow() * 1000).toDateString()) return t;
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + t;
  }
  function fmtHM(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  // HH:MM, with the date in front when it is not today: for a market close, which is always on the minute.
  function fmtHMDay(ts) {
    if (!isNum(ts)) return DASH;
    const d = new Date(ts * 1000);
    const hm = fmtHM(ts);
    return d.toDateString() === new Date(serverNow() * 1000).toDateString() ? hm : fmtShortDateOf(d) + " " + hm;
  }
  function fmtShortDateOf(d) { return d.toLocaleDateString([], { month: "short", day: "numeric" }); }
  function fmtClock(s) {
    s = Math.max(0, Math.floor(s));
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }
  function fmtAge(s) {
    if (!isNum(s)) return DASH;
    if (s < 1) return "just now";
    if (s < 90) return Math.round(s) + " s ago";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 172800) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " days ago";
  }
  function strikeValue(ticker) {
    const m = /-T(\d+(?:\.\d+)?)$/.exec(ticker || "");
    return m ? Number(m[1]) : null;
  }
  function strikeLabel(ticker) {
    const v = strikeValue(ticker);
    if (v === null) return ticker || DASH;
    return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  // Money for the tiles, labels and graphs: always to the cent, signed on request ("+$0.25", "−$0.74", "$0.00").
  // (fmtUsd above keeps up to four decimals for the tables.)
  function fmtMoney(v, sign) {
    if (!isNum(v)) return DASH;
    const cents = Math.round(Math.abs(v) * 100);
    const s = "$" + (cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (cents === 0) return s;
    return (v < 0 ? MINUS : sign ? "+" : "") + s;
  }
  // A dollar axis label at a tick step: "$0", "$0.50", "−$2".
  function fmtMoneyTick(v, step) {
    if (Math.abs(v) < step / 1000) return "$0";
    const dp = step >= 1 ? 0 : 2;
    return (v < 0 ? MINUS : "") + "$" + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  // Round dollar ticks that include the whole range, at most maxN intervals.
  function moneyTicks(lo, hi, maxN) {
    const steps = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 25, 50, 100, 250, 500, 1000, 5000];
    let step = steps[steps.length - 1];
    for (const s of steps) if ((hi - lo) / s <= maxN) { step = s; break; }
    const a = Math.floor(lo / step + 1e-9) * step;
    const b = Math.ceil(hi / step - 1e-9) * step;
    const vals = [];
    for (let v = a; v <= b + step * 1e-6; v += step) vals.push(Math.round(v / step) * step);
    return { lo: a, hi: b, step: step, vals: vals };
  }
  const TIME_STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];
  // Clock ticks (local time) that fit in [x0, x1] with at most maxN intervals.
  function timeTicks(x0, x1, maxN) {
    const span = Math.max(1, x1 - x0);
    let step = TIME_STEPS[TIME_STEPS.length - 1];
    for (const s of TIME_STEPS) if (span / s <= maxN) { step = s; break; }
    const off = -new Date(x0 * 1000).getTimezoneOffset() * 60;          // line the ticks up with local midnight
    const ticks = [];
    for (let t = Math.ceil((x0 + off) / step) * step - off; t <= x1; t += step) ticks.push(t);
    return { step: step, ticks: ticks, multiDay: new Date(x0 * 1000).toDateString() !== new Date(x1 * 1000).toDateString() };
  }
  function fmtShortDate(ts) { return fmtShortDateOf(new Date(ts * 1000)); }
  function fmtTickTime(t, tk) {
    if (tk.step >= 86400) return fmtShortDate(t);
    return tk.multiDay ? fmtShortDate(t) + " " + fmtHM(t) : fmtHM(t);
  }

  // ------------------------------------------------------------------ status, tiles, banners
  function detailOf(st) {
    const d = st && st.heartbeat && st.heartbeat.detail;
    return d && typeof d === "object" && !Array.isArray(d) ? d : {};
  }
  function runEnded(st) { return !!(st && st.run && isNum(st.run.ended_ts)); }
  function heartbeatAge(st) {
    const hb = st && st.heartbeat;
    return hb && isNum(hb.ts) ? serverNow() - hb.ts : null;
  }
  // Seconds without a heartbeat after which the bot is called silent. The bots beat at least every 5 s; a bot that
  // reports its own interval (heartbeat_s) gets three of them.
  function staleLimit(st) {
    const every = numOrNull(pick(detailOf(st), DETAIL_KEYS.heartbeatEvery));
    return every !== null && every > 0 ? Math.max(STALE_HEARTBEAT_S, 3 * every) : STALE_HEARTBEAT_S;
  }
  function sinceStart(st) {
    return st && st.run && isNum(st.run.started_ts) ? serverNow() - st.run.started_ts : null;
  }
  function strategyOf(st) {
    const id = st && st.run ? String(st.run.strategy || "") : "";
    const known = STRATEGIES[id];
    if (known) return Object.assign({ id: id }, known);
    return { id: id, kind: "other", name: id || "unknown", title: id ? id + " bot" : "KPM bot monitor",
      sub: "Kalshi · localhost monitor" };
  }
  // Limits: numbers (the server turns "5.00" into 5; numOrNull also copes with a string).
  function capsOf(st) { return (st && st.caps) || {}; }
  function blockOf(st, name) {
    const b = capsOf(st)[name];
    return b && typeof b === "object" && !Array.isArray(b) ? b : {};
  }
  function windowMin() {
    const v = numOrNull(blockOf(state.status, "pair").window_min);
    return v !== null && v > 0 ? v : 15;
  }
  function pollInterval() {
    const v = numOrNull(blockOf(state.status, "pair").poll_interval_s);
    return v !== null && v > 0 ? v : 1;
  }
  // An "opportunity" is a quote the bot would act on: edge at or above trading.pair.min_edge_usd (above 0 when unset).
  function minEdge() {
    const v = numOrNull(blockOf(state.status, "pair").min_edge_usd);
    return v !== null && v > 0 ? v : 0;
  }
  function isOpportunity(edge) {
    if (!isNum(edge)) return false;
    const m = minEdge();
    return m > 0 ? edge >= m : edge > 0;
  }
  function liveOrders(st) {
    const s = st && st.stats && st.stats.run;
    return s && isNum(s.live_orders) ? s.live_orders : 0;
  }
  function otherLive(st) { return st && Array.isArray(st.other_live_runs) ? st.other_live_runs : []; }

  // The big badge. Only the exact words 'live' and 'watch' are believed; anything else is shown as unknown rather
  // than as "no orders sent". A stopped or silent run never keeps the LIVE / WATCH-ONLY headline.
  function modeInfo(st) {
    const run = st && st.run;
    if (!run) return { cls: "none", text: "NO BOT RUN", sub: "start the bot to see live data" };
    const raw = String(run.mode == null ? "" : run.mode);
    const word = raw === "live" ? "LIVE" : raw === "watch" ? "WATCH-ONLY" : "mode " + JSON.stringify(raw);
    if (runEnded(st)) {
      return { cls: "none", text: "STOPPED", sub: "last run was " + word + " · ended " + fmtTime(run.ended_ts) +
        (run.end_reason ? " (" + run.end_reason + ")" : "") };
    }
    const age = heartbeatAge(st);
    const since = sinceStart(st);
    if (isNum(age) && age > NOT_RESPONDING_S) {
      return { cls: "unknown", text: "NOT RESPONDING", sub: "last run was " + word + " · no heartbeat for " + fmtAge(age).replace(" ago", "") };
    }
    if (!st.heartbeat && isNum(since) && since > NO_HEARTBEAT_GRACE_S) {
      return { cls: "unknown", text: "NOT RESPONDING", sub: "last run was " + word + " · no heartbeat since it started" };
    }
    // The badge describes the BOT (it is running and can trade), never a trade: each trade has its own status below.
    if (raw === "live") return { cls: "live", text: "LIVE bot running", sub: "real orders, real money · each trade shows its own status" };
    if (raw === "watch") {
      if (liveOrders(st) > 0) {
        return { cls: "unknown", text: "MODE MISMATCH", sub: "labelled watch-only, but " + liveOrders(st) + " real order(s) were sent" };
      }
      return { cls: "watch", text: "WATCH-ONLY bot running", sub: "paper trades only, no orders sent" };
    }
    return { cls: "unknown", text: "MODE UNKNOWN", sub: word + " is neither 'live' nor 'watch'" };
  }

  function botStatus(st) {
    if (!st) return { text: "connecting", tone: "" };
    const run = st.run, hb = st.heartbeat;
    if (!run) return { text: "not running", tone: "" };
    if (runEnded(st)) return { text: "stopped" + (run.end_reason ? " (" + run.end_reason + ")" : ""), tone: "" };
    if (!hb) {
      const since = sinceStart(st);
      if (isNum(since) && since > NO_HEARTBEAT_GRACE_S) {
        return { text: "no heartbeat since start (" + fmtAge(since).replace(" ago", "") + "), the bot may have failed to start", tone: "warn" };
      }
      return { text: "starting, no heartbeat yet", tone: "warn" };
    }
    const age = heartbeatAge(st);
    const s = String(hb.status || "unknown");
    if (isNum(age) && age > staleLimit(st)) return { text: s + " (not responding?)", tone: "warn" };
    const tones = { monitoring: "info", trading: "good", error: "crit", waiting: "", stopped: "" };
    return { text: s, tone: tones[s] || "" };
  }

  function renderStrategy(st) {
    const s = strategyOf(st);
    $("title-main").textContent = s.title;
    $("title-sub").textContent = s.sub;
    $("strategy-chip").hidden = !s.id;
    $("strategy-name").textContent = s.id || DASH;
    $("chart-card").hidden = !(s.kind === "pair" || (s.kind !== "late" && state.quotes.size > 0));
    renderRuleCard(st, s);
  }

  // late_favorite has no pricing chart (one order book per window); its card says what the rule is, from the values
  // the bot recorded for this run.
  function renderRuleCard(st, s) {
    const card = $("rule-card");
    card.hidden = s.kind !== "late";
    if (s.kind !== "late") return;
    const lf = blockOf(st, "late_favorite");
    const up = numOrNull(lf.up_threshold), down = numOrNull(lf.down_threshold);
    const a = numOrNull(lf.window_start_s), b = numOrNull(lf.window_end_s);
    const tries = numOrNull(lf.max_attempts);
    $("rule-text").textContent =
      "In each 15-minute KXBTC15M market, between " + (a !== null ? Math.round(900 - a) : 120) + " and " +
      (b !== null ? Math.round(900 - b) : 60) + " seconds before it closes, take the first fresh order book. If the YES " +
      "mid is above " + (up !== null ? up.toFixed(2) : "0.60") + ", buy 1 YES at the current ask. If it is below " +
      (down !== null ? down.toFixed(2) : "0.40") + ", buy 1 NO at the current ask. Otherwise skip the window. Each order " +
      "is immediate-or-cancel at the ask (up to " + (tries !== null ? tries : 3) + " attempts), and a filled trade is held to " +
      "settlement. A paper twin at the detection price is tracked next to every live trade.";
  }

  // The badge is a polite live region: write only what changed, so a screen reader is not told again at every poll.
  function setText(node, text) { if (node.textContent !== text) node.textContent = text; }
  function renderMode(st) {
    const m = modeInfo(st);
    const cls = "mode " + m.cls;
    if ($("mode").className !== cls) $("mode").className = cls;
    setText($("mode-text"), m.text);
    setText($("mode-sub"), m.sub);
  }

  function renderBotStatus(st) {
    const b = botStatus(st);
    const chip = $("bot-status");
    clear(chip);
    chip.appendChild(document.createTextNode("Bot: "));
    chip.appendChild(el("span", "dot-status " + b.tone));
    chip.appendChild(el("b", null, b.text));

    const hbChip = $("hb");
    clear(hbChip);
    hbChip.appendChild(document.createTextNode("Heartbeat: "));
    const age = heartbeatAge(st);
    if (!isNum(age)) {
      hbChip.appendChild(el("b", null, DASH));
    } else if (runEnded(st)) {
      hbChip.appendChild(el("b", null, "last " + fmtTime(st.heartbeat.ts)));
    } else if (age > staleLimit(st)) {
      hbChip.appendChild(icon("warn"));
      hbChip.appendChild(el("b", null, fmtAge(age) + " (stale)"));
    } else {
      hbChip.appendChild(el("b", "tnum", fmtAge(age)));
    }
  }

  // When the bot trades, as text first (open or closed, and when it changes). late_favorite decides once per 15-minute
  // market, 120-60 s before its close; the pair bot prices the last window_min minutes of each hour.
  function windowModel(st) {
    const w = windowTimes(st);
    if (st && st.run && runEnded(st)) {      // a stopped run does not trade: say so instead of counting down to a trade
      return { label: w.label, state: "NOT TRADING", text: w.text, sub: "the bot is stopped · " + w.sub };
    }
    return w;
  }

  function windowTimes(st) {
    const now = serverNow();
    const end15 = (Math.floor(now / 900) + 1) * 900;
    const s = strategyOf(st);
    if (s.kind === "late") {
      const lf = blockOf(st, "late_favorite");
      const a = numOrNull(lf.window_start_s), b = numOrNull(lf.window_end_s);
      const opens = end15 - (900 - (a !== null ? a : 780)), ends = end15 - (900 - (b !== null ? b : 840));
      const mkt = "market closes at " + fmtHM(end15) + " (in " + fmtClock(end15 - now) + ")";
      if (now < opens) {
        return { label: "Decision window", state: "CLOSED", text: "opens in " + fmtClock(opens - now),
          sub: "decides " + fmtHM(opens) + "–" + fmtHM(ends) + " · " + mkt };
      }
      if (now <= ends) return { label: "Decision window", state: "OPEN", text: fmtClock(ends - now) + " left", sub: mkt };
      return { label: "Decision window", state: "CLOSED", text: "opens in " + fmtClock(opens + 900 - now),
        sub: "next decision " + fmtHM(opens + 900) + "–" + fmtHM(ends + 900) + " · " + mkt };
    }
    return pairWindow(st, now, end15);
  }

  function pairWindow(st, now, end15) {
    const hourEnd = (Math.floor(now / 3600) + 1) * 3600;
    const stop = numOrNull(blockOf(st, "pair").stop_before_close_s);
    const winStart = hourEnd - windowMin() * 60, winEnd = hourEnd - (stop !== null && stop >= 0 ? stop : 60);
    const mkt = "15-minute market ends at " + fmtHM(end15) + " (in " + fmtClock(end15 - now) + ")";
    if (now < winStart) {
      return { label: "Trading window", state: "CLOSED", text: "opens in " + fmtClock(winStart - now),
        sub: "next window " + fmtHM(winStart) + "–" + fmtHM(hourEnd) + " · " + mkt };
    }
    if (now <= winEnd) {
      return { label: "Trading window", state: "OPEN", text: fmtClock(winEnd - now) + " left", sub: "until " + fmtHM(winEnd) + " · " + mkt };
    }
    return { label: "Trading window", state: "CLOSED", text: "opens in " + fmtClock(winStart + 3600 - now), sub: mkt };
  }

  function renderClock() {
    const now = serverNow();
    const w = windowModel(state.status);
    const label = $("window-label");                    // "Trading window: CLOSED" in words, not only in colour
    clear(label);
    label.appendChild(document.createTextNode(w.label + ": "));
    label.appendChild(el("b", null, w.state));
    $("countdown").textContent = w.text;
    $("window-sub").textContent = w.sub;
    $("foot-time").textContent = "Server time " + fmtTime(now) + " · all times in your local time zone";
    if (state.status) renderBotStatus(state.status);
  }

  function renderTiles(st) {
    const det = detailOf(st);
    const strat = strategyOf(st);
    // market tile: the market being watched now; the newest quote's market only while it is recent
    $("hour-label").textContent = strat.kind === "late" ? "15-minute market" : "Hour market";
    let hour = pick(det, DETAIL_KEYS.hour);
    let recent = !runEnded(st);                 // the last heartbeat of a stopped run is history, not "now"
    if (!hour && st.latest_quote && st.latest_quote.hour_ticker) {
      hour = st.latest_quote.hour_ticker;
      recent = isNum(st.latest_quote.ts) && serverNow() - st.latest_quote.ts <= Math.max(30, 5 * pollInterval());
    }
    $("hour-market").textContent = hour ? (recent ? "" : "last watched: ") + String(hour) : DASH;
    const nStrikes = state.chart ? state.chart.nSeries : 0;
    $("hour-sub").textContent = !hour
      ? "shown once the bot watches a market"
      : strat.kind === "late" ? "KXBTC15M market, decided once in its last 2 minutes"
        : "KXBTC15M market ending on the hour" + (nStrikes ? " · " + nStrikes + " strike" + (nStrikes === 1 ? "" : "s") + " on the chart" : "");

    // (the loss used vs cap figure has its own card, renderGauge)

    // balance
    let bal = numOrNull(pick(det, DETAIL_KEYS.balance));
    if (bal === null) {
      const cents = numOrNull(pick(det, DETAIL_KEYS.balanceCents));
      if (cents !== null) bal = cents / 100;
    }
    $("balance").textContent = bal === null ? DASH : fmtUsd(bal);
    $("balance-sub").textContent = bal === null
      ? "not reported by the bot yet"
      : "reported with the last heartbeat (" + fmtAge(heartbeatAge(st)) + ")";

    // trades this run: the answer to "did anything happen?" without scrolling
    const sr = st.stats && st.stats.run;
    if (!sr) {
      $("trades-value").textContent = DASH;
      $("trades-sub").textContent = "no bot run yet";
    } else {
      const noun = strat.kind === "late" ? "signal" : "opportunit";
      const n = sr.opportunities || 0;
      $("trades-value").textContent = n + " " + (strat.kind === "late" ? noun + (n === 1 ? "" : "s") : noun + (n === 1 ? "y" : "ies")) +
        (isNum(sr.filled) && sr.filled ? " · " + sr.filled + " filled" : "");
      const parts = [isNum(sr.last_ts) ? "last " + fmtTime(sr.last_ts) : "none yet"];
      if (sr.paired_n) parts.push("settled live " + fmtUsd(sr.paired_realized, true) + " vs paper " + fmtUsd(sr.paired_paper, true) +
        " (" + sr.paired_n + " trade" + (sr.paired_n === 1 ? "" : "s") + ")");
      if (sr.pending_n) parts.push(sr.pending_n + " awaiting settlement");
      $("trades-sub").textContent = parts.join(" · ");
    }
  }

  function renderCaps(st) {
    const c = capsOf(st);
    const p = blockOf(st, "pair");
    const lf = blockOf(st, "late_favorite");
    const strat = strategyOf(st);
    const num = (v) => numOrNull(v);
    const items = [
      ["Live trading in config", c.enabled === true ? "ON" : "OFF"],
      ["Max loss", fmtUsd(num(c.max_loss_usd))],
      ["Max cost per order", fmtUsd(num(c.max_cost_per_order_usd))],
      ["Max contracts per order", fmtCount(num(c.max_contracts_per_order))],
      ["Max open exposure", fmtUsd(num(c.max_open_exposure_usd))],
      ["Max orders per minute", fmtCount(num(c.max_orders_per_minute))],
      ["Max orders per run", isNum(num(c.max_orders_per_session)) ? fmtCount(num(c.max_orders_per_session)) : "none"],
      ["Series", Array.isArray(c.allowed_series) && c.allowed_series.length ? c.allowed_series.join(", ") : DASH],
    ];
    if (strat.kind === "pair") {
      const me = num(p.min_edge_usd), wm = num(p.window_min), se = num(p.strikes_each_side), pi = num(p.poll_interval_s);
      const mp = num(p.max_live_pairs_per_hour);
      items.push(["Min edge", isNum(me) ? String(Number((me * 100).toFixed(3))) + "¢" : DASH],
        ["Trading window", isNum(wm) ? "last " + wm + " min of each hour" : DASH],
        ["Strikes", isNum(se) ? se + " each side" : DASH],
        ["Poll", isNum(pi) ? pi + " s" : DASH],
        ["Live pairs per hour", isNum(mp) ? String(mp) : DASH]);
    } else if (strat.kind === "late") {
      const up = num(lf.up_threshold), dn = num(lf.down_threshold), tries = num(lf.max_attempts), pi = num(lf.poll_interval_s);
      const a = num(lf.window_start_s), b = num(lf.window_end_s);
      items.push(["Rule", "YES mid > " + (isNum(up) ? up.toFixed(2) : "0.60") + " buys YES · < " + (isNum(dn) ? dn.toFixed(2) : "0.40") + " buys NO"],
        ["Decision window", Math.round(900 - (isNum(a) ? a : 780)) + "–" + Math.round(900 - (isNum(b) ? b : 840)) + " s before close"],
        ["Attempts per window", isNum(tries) ? String(tries) : "3"],
        ["Poll", isNum(pi) ? pi + " s" : DASH]);
    }
    const kpath = st.kill_switch_path || st.kill_switch_file || "data/KILL";
    items.push(["Kill switch", kpath + (st.kill_switch_active ? " (present)" : " (not present)")]);
    const ul = $("caps");
    clear(ul);
    for (const [k, v] of items) {
      const li = el("li");
      li.appendChild(document.createTextNode(k + " "));
      li.appendChild(el("b", null, v));
      ul.appendChild(li);
    }
    // where the limits come from, and any difference between this run and settings.yaml today
    const note = $("caps-note");
    const drift = Array.isArray(st.caps_drift) ? st.caps_drift : [];
    const bits = [];
    if (st.caps_source === "run") bits.push("Limits shown are the ones in force for this run (recorded when it started).");
    if (drift.length) {
      bits.push("settings.yaml now differs from this run: " + drift.map((d) => d.key + " is " + shortVal(d.config) + " in the file but " +
        shortVal(d.run) + " in the run").join("; ") + ". A change takes effect only in a new run" +
        (drift.some((d) => d.key === "kill_switch_file") ? ", and the STOP button writes the file named in settings.yaml" : "") + ".");
    }
    note.textContent = bits.join(" ");
    note.hidden = bits.length === 0;
  }
  function shortVal(v) { return typeof v === "string" ? clip(v, 60) : JSON.stringify(v); }

  function renderDetail(st) {
    const det = detailOf(st);
    const keys = Object.keys(det).filter((k) => !KNOWN_DETAIL.has(k));
    const box = $("detail-more");
    box.hidden = keys.length === 0;
    const dl = $("detail-list");
    clear(dl);
    for (const k of keys) {
      const v = det[k];
      let text = typeof v === "object" ? JSON.stringify(v) : String(v);
      if (/_ts$/.test(k) && isNum(v) && v > 1e9) text = fmtTime(v);     // epoch seconds -> local time
      dl.appendChild(el("dt", null, k));
      dl.appendChild(el("dd", null, text));
    }
  }

  // Banners are rebuilt only when their content changes, and their text never carries a ticking age, so a screen reader
  // does not hear the same alert again at every poll.
  function renderBanners() {
    const st = state.status;
    const items = [];
    const add = (tone, text) => items.push({ tone: tone, text: text });
    if (state.connected === false) {
      add("warn", "Lost contact with the dashboard server (" + state.connError + "). Showing the last data received; retrying every " +
        state.refreshS + " s. If you closed the dashboard window, start it again with: kpm dashboard");
    }
    if (st) {
      if (st.kill_switch_active) {
        add("crit", "Kill switch is active (" + (st.kill_switch_path || st.kill_switch_file) + " exists). The bot must not place new orders " +
          "and should cancel resting orders and stop. To trade again: delete that file, then restart the bot.");
      }
      if (st.db_available === false) {
        add("warn", "The bot database was not found yet. It appears once the bot (or any kpm command) has created it.");
      }
      const run = st.run;
      const age = heartbeatAge(st);
      if (run && !runEnded(st)) {
        if (isNum(age) && age > staleLimit(st)) {
          add("warn", "No heartbeat for more than " + staleLimit(st) + " seconds. The bot may have crashed, frozen or lost its " +
            "connection. Check its console window. While it is not running it cannot place or cancel orders.");
        } else if (!st.heartbeat && isNum(sinceStart(st)) && sinceStart(st) > NO_HEARTBEAT_GRACE_S) {
          add("warn", "This run has not sent a single heartbeat since it started. It may have failed to start (check the key " +
            "settings and the bot's console window), and it cannot place or cancel orders while it is not running.");
        }
        const raw = String(run.mode == null ? "" : run.mode);
        if (raw !== "live" && raw !== "watch") {
          add("warn", "The latest run reports mode " + JSON.stringify(raw) + ", which is neither 'live' nor 'watch', so the page cannot " +
            "say whether real orders are being sent.");
        }
        if (raw === "watch" && liveOrders(st) > 0) {
          add("crit", "This run is labelled WATCH-ONLY but " + liveOrders(st) + " real order(s) were sent. Treat it as live and check the bot.");
        }
      }
      for (const o of otherLive(st)) {
        add("crit", "Another live run is active: " + o.strategy + " (" + o.run_id + "). Two live bots would double every trade. " +
          "The headline describes only the newest run.");
      }
      const err = pick(detailOf(st), DETAIL_KEYS.error);
      if (err && !runEnded(st)) add("warn", "Bot reported an error: " + clip(typeof err === "string" ? err : JSON.stringify(err), 400));
    }
    const key = JSON.stringify(items);
    if (key === state.bannerKey) return;
    state.bannerKey = key;
    const box = $("banners");
    clear(box);
    for (const it of items) {
      const b = el("div", "banner " + it.tone);
      b.setAttribute("role", it.tone === "crit" ? "alert" : "status");
      b.appendChild(icon(it.tone));
      b.appendChild(el("div", null, it.text));
      box.appendChild(b);
    }
  }

  function renderKill(st) {
    const btn = $("stop");
    const file = st && (st.kill_switch_path || st.kill_switch_file);
    if (st && st.kill_switch_active) {
      btn.disabled = true;
      btn.textContent = "KILL SWITCH ACTIVE";
      btn.title = "The kill-switch file exists. Delete " + file + " and restart the bot to trade again.";
    } else if (!btn.dataset.busy) {
      btn.disabled = false;
      btn.textContent = "STOP BOT";
      btn.title = "Creates the kill-switch file" + (file ? " (" + file + ")" : "") + ": the bot stops placing orders, cancels resting orders and exits.";
    }
  }

  function updateTitle() {
    const st = state.status;
    const m = modeInfo(st);
    const b = botStatus(st);
    const s = strategyOf(st);
    document.title = (st && st.kill_switch_active ? "KILLED · " : "") + m.text + " · " + b.text + " — " + (s.id ? s.title : "KPM bot");
  }

  // ------------------------------------------------------------------ trade status: what became of each signal
  // The server derives a status for every row (server.trade_status) and says it in words. These two functions only put the
  // clock time into this browser's format, and move a trade that was "open" to "market closed, waiting for the result" once
  // its market has closed, between two polls.
  function tradeLabel(t) {
    if (!t) return "";
    const hm = isNum(t.close_ts) ? fmtHM(t.close_ts) : null;
    if (t.state === "open" && hm) {
      const lead = /one leg held/.test(t.label || "") ? "Open · one leg held, " : "Open · ";
      return lead + (serverNow() >= t.close_ts ? "market closed " + hm + ", waiting for the result" : "settles at " + hm);
    }
    if (t.state === "overdue" && hm) return "Overdue · no result recorded since " + hm;
    return t.label || "";
  }
  function tradeDetail(t) {
    if (!t) return "";
    if (t.state === "paper_only" && !isNum(t.paper_pnl) && isNum(t.close_ts)) {
      return serverNow() >= t.close_ts ? "paper twin: market closed " + fmtHM(t.close_ts) + ", waiting for the result"
        : "paper twin settles at " + fmtHM(t.close_ts);
    }
    return t.detail || "";
  }
  // One settled trade of /api/performance as a sentence: "bought NO at 74¢ · WON +$0.25".
  function tradeSentence(t) {
    const bought = (t.side ? "bought " + t.side + (isNum(t.entry) ? " at " + fmtPrice(t.entry) : "") : isNum(t.entry) ? "pair at " + fmtPrice(t.entry) : "");
    return (bought ? bought + " · " : "") + (t.result === "won" ? "WON " : "LOST ") + fmtMoney(t.pnl, true);
  }
  function announceTo(id, text) {
    const live = $(id);
    if (live && live.textContent !== text) live.textContent = text;
  }

  // ------------------------------------------------------------------ money: summary tiles
  function perfOk() { return !!(state.perf && Array.isArray(state.perf.trades) && state.perf.summary); }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  function statTile(label, value, sub, cls) {
    const d = el("div", "stat" + (cls ? " " + cls : ""));
    d.appendChild(el("div", "stat-label", label));
    d.appendChild(el("div", "stat-value", value));
    if (sub) d.appendChild(el("div", "stat-sub", sub));
    return d;
  }

  function renderScopeNote() {
    const st = state.status;
    const note = $("scope-note");
    if (state.scope === "all") {
      const paper = perfOk() && state.perf.view === "paper";
      note.textContent = paper ? "every run so far · no real orders yet, so the money figures are paper twins"
        : "every run so far · the money figures and graphs count real-money trades only";
    } else if (st && st.run) {
      note.textContent = st.run.run_id + " · started " + fmtTime(st.run.started_ts) + (runEnded(st) ? " · ended " + fmtTime(st.run.ended_ts) : "");
    } else {
      note.textContent = "";
    }
  }

  // The cost of order delay, in words and without a sign to misread. ``diff`` is what the server calls delay_cost: the real P&L minus
  // the paper P&L of the same trades, so a positive number means the real fills did BETTER than their paper twins (a cost of
  // $0.00, not a "+$0.01 cost"). The trades table says the same with the price: a fill below the detection price is "paid less".
  function delayCostText(diff) {
    if (!isNum(diff)) return DASH;
    if (Math.round(Math.abs(diff) * 100) === 0) return "Delay cost: none · real matched paper";
    return diff > 0 ? "Delay cost: none · real beat paper by " + fmtMoney(diff) : "Delay cost: " + fmtMoney(-diff) + " · real fell behind paper";
  }

  // Where the open trades stand: when the next one settles, how many have closed and wait for their result, how many are overdue.
  function openTradesText(open, overdue) {
    const now = serverNow();
    const live = open.filter((o) => o.state === "open");
    const ahead = live.filter((o) => isNum(o.close_ts) && o.close_ts > now).sort((a, b) => a.close_ts - b.close_ts);
    const bits = [];
    if (ahead.length) bits.push("next settles at " + fmtHM(ahead[0].close_ts));
    if (live.length > ahead.length) bits.push(plural(live.length - ahead.length, "market") + " closed, waiting for the result");
    if (overdue) bits.push(overdue + " with no result recorded");
    return bits.join(" · ");
  }

  function renderMoney() {
    const box = $("money-tiles");
    const note = $("money-note");
    clear(box);
    renderScopeNote();
    if (!perfOk()) {
      note.hidden = true;
      box.appendChild(el("p", "muted", state.perfError
        ? "The money figures could not be loaded (" + state.perfError + "). If the dashboard was started before this version, restart it with: kpm dashboard"
        : "Loading the money figures…"));
      return;
    }
    const P = state.perf;
    const s = P.summary;
    const paper = P.view === "paper";
    const late = strategyOf(state.status).kind === "late";
    const n = s.settled || 0;
    const sideTime = (b) => (b ? [b.side, isNum(b.close_ts) ? fmtHMDay(b.close_ts) : ""].filter(Boolean).join(" · ") : "");
    const openSub = openTradesText(P.open || [], s.overdue || 0) || (s.open ? "waiting for settlement" : "none open");
    $("money-sub").textContent = paper
      ? "No real orders in this view, so these are the paper twins: what each trade would have made at the price the bot saw when it decided. A trade counts once its market has closed and paid out."
      : "Real-money trades only. A trade counts once its market has closed and paid out; until then it is open. The paper twin is the same trade at the price the bot saw when it decided.";
    const tiles = [];
    tiles.push([paper ? "Paper P&L (no real orders)" : "Real P&L (settled)", n ? fmtMoney(s.pnl, true) : fmtMoney(0),
      n ? plural(n, "settled " + (paper ? "paper trade" : "trade")) + (paper ? " · paper twins" : " · real money") : "no settled trades yet", "hero"]);
    if (!paper) {
      const known = n && isNum(s.paper_pnl);
      tiles.push(["Paper P&L (same trades)", known ? fmtMoney(s.paper_pnl, true) : DASH,
        known ? delayCostText(s.delay_cost) : "no settled trades yet"]);
    }
    tiles.push(["Average per trade", n ? fmtMoney(s.avg_pnl, true) : DASH, "P&L ÷ settled trades"],
      ["Best trade", s.best ? fmtMoney(s.best.pnl, true) : DASH, sideTime(s.best) || "none yet"],
      ["Worst trade", s.worst ? fmtMoney(s.worst.pnl, true) : DASH, sideTime(s.worst) || "none yet"],
      [paper ? "Settled paper trades" : "Settled live trades", String(n), "market closed and paid out"],
      ["Open trades", String(s.open || 0), openSub],
      ["Wins", String(s.wins || 0), late ? "the contract paid out" : "the pair made money"],
      ["Losses", String(s.losses || 0), late ? "the contract expired worthless" : "the pair lost money"],
      ["Win rate", isNum(s.win_rate) ? Math.round(s.win_rate * 100) + "%" : DASH, n ? (s.wins || 0) + " of " + n + " settled" : "no settled trades yet"]);
    if (!paper) {
      // These three are about the fills, not the settlement, so they cover every real fill, open trades included (the tiles
      // above count settled trades only). Fees are shown to the 4 decimals the orders table uses: 0.09 cents is not "$0.00".
      tiles.push(["Fees paid", isNum(s.fees) ? fmtUsd(s.fees) : DASH,
        "on every real fill, open ones too" + (s.fees_pending ? " · " + s.fees_pending + " not recorded yet" : "")],
      ["Avg fill delay", isNum(s.avg_latency_ms) ? Math.round(s.avg_latency_ms).toLocaleString("en-US") + " ms" : DASH,
        "detection to the order's answer · all fills, open ones too"],
      ["Avg fill vs paper", isNum(s.avg_fill_vs_paper_cents) ? fmtCentsSigned(s.avg_fill_vs_paper_cents) : DASH,
        "per contract, negative = paid less than paper · all fills, open ones too"]);
    }
    for (const t of tiles) box.appendChild(statTile(t[0], t[1], t[2], t[3]));

    const bits = [];
    if (s.skipped) {
      const why = Object.keys(s.skipped_reasons || {}).map((k) => k + " ×" + s.skipped_reasons[k]).join(", ");
      bits.push(plural(s.skipped, "signal") + " skipped" + (why ? " (" + why + ")" : ""));
    }
    if (s.not_filled) bits.push(s.not_filled + " not filled");
    if (!paper && s.paper_only) bits.push(s.paper_only + " paper only (watch-only runs)");
    if (s.unknown) bits.push(s.unknown + " with an unknown result: check Kalshi");
    const parts = [];
    if (bits.length) parts.push("Not counted above: " + bits.join(", ") + ".");
    if (P.truncated) parts.push("Long history: the graphs show the newest trades only; the tiles count all of them.");
    note.textContent = parts.join(" ");
    note.hidden = parts.length === 0;
  }

  // ------------------------------------------------------------------ money: loss used vs cap (gauge)
  // The cap is account-wide (every run), so the bot's figure is too.
  function lossModel(st) {
    const det = detailOf(st);
    const cap = numOrNull(capsOf(st).max_loss_usd);
    let used = numOrNull(pick(det, DETAIL_KEYS.loss));
    let source;
    if (used !== null) {
      const age = heartbeatAge(st);
      source = "account-wide, reported by the bot" + (isNum(age) && !runEnded(st) ? " " + fmtAge(age) : " at its last heartbeat");
    } else {
      const realized = st.stats && st.stats.all ? st.stats.all.realized_pnl : null;
      if (isNum(realized)) {
        used = Math.max(0, -realized);
        source = "from realized P&L over all runs";
      } else {
        used = 0;
        source = "no settled live trades yet";
      }
    }
    used = Math.max(0, used);                // a net gain is not "loss used"
    const perOrder = numOrNull(capsOf(st).max_cost_per_order_usd);
    return { cap: cap, used: used, pct: cap ? Math.min(1, used / cap) : 0, source: source, perOrder: perOrder };
  }

  function renderGauge(st) {
    const L = lossModel(st);
    const tone = L.pct >= 0.8 ? "crit" : L.pct >= 0.5 ? "warn" : "";
    const pctText = Math.round(L.pct * 100) + "% used";
    $("loss-value").textContent = L.cap !== null ? fmtMoney(L.used) + " of " + fmtMoney(L.cap) : fmtMoney(L.used);
    const holder = $("gauge-chart");
    holder.setAttribute("aria-valuenow", String(Math.round(L.pct * 100)));
    holder.setAttribute("aria-valuetext", $("loss-value").textContent + " (" + pctText + ")");
    const status = $("loss-status");
    clear(status);
    status.appendChild(icon(tone === "crit" ? "crit" : tone === "warn" ? "warn" : "good"));
    status.appendChild(document.createTextNode((tone === "crit" ? "Near the cap" : tone === "warn" ? "Getting high" : "OK") + " · " + pctText));
    const allRealized = st.stats && st.stats.all ? st.stats.all.realized_pnl : null;
    $("loss-sub").textContent = (L.cap !== null ? fmtMoney(Math.max(0, L.cap - L.used)) + " left · the bot stops itself at " + fmtMoney(L.cap) + " · " : "") +
      L.source + (isNum(allRealized) ? " · all runs realized P&L " + fmtMoney(allRealized, true) : "");
    $("gauge-note").textContent = L.cap !== null
      ? "Loss used counts realized losses plus the most the open trades could still lose. The bot checks every order before it sends it: the loss used, plus that order's full cost and fees, must still fit under " +
        fmtMoney(L.cap) + ". So it stops before an order could take the loss past the cap, not after."
      : "No loss cap is configured.";

    const svg = $("gauge-svg");
    const W = Math.max(260, Math.floor(holder.clientWidth));
    const narrow = W < 520;
    const M = { l: 12, r: 16 };
    const pw = W - M.l - M.r;
    const barY = 8, barH = 16;
    const H = 86;
    clear(svg);
    svg.setAttribute("class", "chart-svg gauge" + (tone ? " " + tone : ""));
    svg.setAttribute("width", String(W));
    svg.setAttribute("height", String(H));
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    if (!(L.cap > 0)) return;
    const X = (v) => M.l + Math.min(1, Math.max(0, v / L.cap)) * pw;
    svg.appendChild(S("rect", { class: "gauge-track", x: M.l, y: barY, width: pw, height: barH, rx: 8 }));
    if (L.used > 0) {
      const w = Math.max(8, X(L.used) - M.l);
      svg.appendChild(S("rect", { class: "gauge-fill", x: M.l, y: barY, width: w, height: barH, rx: Math.min(8, w / 2) }));
    }
    svg.appendChild(S("line", { class: "gauge-cap", x1: M.l + pw, x2: M.l + pw, y1: barY - 3, y2: barY + barH + 3 }));
    const tk = moneyTicks(0, L.cap, narrow ? 4 : 6);
    for (const v of tk.vals) {                                            // dollar ticks; the cap has its own label at the end
      if (v < 0 || X(L.cap) - X(v) < 64) continue;
      svg.appendChild(S("text", { class: "tick", x: X(v), y: barY + barH + 18, "text-anchor": v === 0 ? "start" : "middle" }, fmtMoneyTick(v, tk.step)));
    }
    svg.appendChild(S("text", { class: "tick zero-tick", x: M.l + pw, y: barY + barH + 18, "text-anchor": "end" }, "cap " + fmtMoney(L.cap)));
    if (isNum(L.perOrder) && L.perOrder > 0 && L.perOrder < L.cap) {      // the room a single order may need
      const xa = X(L.cap - L.perOrder), xb = X(L.cap), y = barY + barH + 30;
      svg.appendChild(S("path", { class: "gauge-bracket", d: "M" + xa + "," + (y - 4) + "V" + y + "H" + xb + "V" + (y - 4) }));
      svg.appendChild(S("text", { class: "tick", x: xb, y: y + 14, "text-anchor": "end" }, "one order can risk up to " + fmtMoney(L.perOrder)));
    }
  }

  // What an empty chart says while its data is on the way, or when the server cannot give it (an older dashboard server).
  function unavailable() {
    return state.perfError ? ["The money figures are not available", "Restart the dashboard (kpm dashboard) to load them."] : ["Loading…"];
  }

  // A centred message inside an empty plot, on a surface-coloured patch so the grid lines do not run through the words.
  function plotMessage(svg, cx, cy, lines) {
    const g = S("g");
    lines.forEach((t, i) => g.appendChild(S("text", { class: "empty-msg", x: cx, y: cy + i * 18, "text-anchor": "middle" }, t)));
    svg.appendChild(g);
    try {
      const b = g.getBBox();
      if (b.width > 0) svg.insertBefore(S("rect", { class: "empty-bg", x: b.x - 12, y: b.y - 6, width: b.width + 24, height: b.height + 12, rx: 6 }), g);
    } catch (e) { /* not laid out yet: the words simply have no patch behind them */ }
  }

  // ------------------------------------------------------------------ money: profit and loss over time
  // Two step lines (the running total after each settled trade): real money and the paper twin of the same trades. Real is
  // drawn last, on top. A watch-only scope has no real orders: then the paper twin is the only line.
  function pnlSeries(P) {
    const paperView = P.view === "paper";
    const base = P.base || { pnl: 0, paper_pnl: 0 };
    const last = P.trades.length ? P.trades[P.trades.length - 1] : null;
    const mk = (key, name, cls, first, get) => ({ key: key, name: name, cls: cls, first: first, get: get, last: last ? get(last) : first });
    const out = [mk("paper", paperView ? "Paper twin (no real orders)" : "Paper twin, same trades", "s2", paperView ? base.pnl : base.paper_pnl,
      (t) => (paperView ? t.cum : t.cum_paper))];
    if (!paperView) out.push(mk("real", "Real money", "s1", base.pnl, (t) => t.cum));
    return out;
  }
  const EMPTY_PERF = { trades: [], open: [], view: "real", base: { pnl: 0, paper_pnl: 0 }, since: null };

  function legendItem(keyEl, name, value) {
    const li = el("li");
    li.appendChild(keyEl);
    li.appendChild(el("span", "lg-name", name));
    if (value) li.appendChild(el("span", "lg-val", value));
    return li;
  }

  function renderPnl() {
    const wrap = $("pnl-chart");
    const svg = $("pnl-svg");
    const ok = perfOk();
    const P = ok ? state.perf : EMPTY_PERF;
    const trades = P.trades;
    const W = Math.max(280, Math.floor(wrap.clientWidth));
    const narrow = W < 520;
    const H = narrow ? 250 : 290;
    const M = { l: 56, r: narrow ? 14 : 118, t: 14, b: 30 };
    const pw = W - M.l - M.r;
    const ph = H - M.t - M.b;
    const now = serverNow();
    const series = pnlSeries(P);
    const paperView = P.view === "paper";

    // x: from the start of the run (just before the first trade in "all runs") to now, or to the end of a run that has stopped
    const firstClose = trades.length ? trades[0].close_ts : null;
    let x0 = isNum(P.since) ? P.since : firstClose !== null ? firstClose - 900 : now - 3600;
    if (firstClose !== null) x0 = P.truncated ? firstClose - 900 : Math.min(x0, firstClose - 300);   // older trades left out: start at the oldest one drawn
    const run = state.status && state.status.run;
    const stoppedAt = state.scope !== "all" && run && isNum(run.ended_ts) ? run.ended_ts : null;
    const x1 = Math.max(stoppedAt !== null ? stoppedAt : now, trades.length ? trades[trades.length - 1].close_ts + 60 : 0);
    if (x1 - x0 < 1800) x0 = x1 - 1800;
    // y: always includes $0
    let lo = 0, hi = 0;
    for (const s of series) { lo = Math.min(lo, s.first); hi = Math.max(hi, s.first); }
    for (const t of trades) for (const s of series) { const v = s.get(t); if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
    if (hi - lo < 0.2) hi = lo + 0.2;
    const pad = (hi - lo) * 0.08;
    const tk = moneyTicks(lo - pad, hi + pad, narrow ? 4 : 6);
    lo = tk.lo;
    hi = tk.hi;
    const X = (t) => M.l + ((t - x0) / (x1 - x0)) * pw;
    const Y = (v) => M.t + ((hi - v) / (hi - lo)) * ph;

    clear(svg);
    svg.setAttribute("width", String(W));
    svg.setAttribute("height", String(H));
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    for (const v of tk.vals) {                                            // dollar grid, hairlines
      const y = Y(v);
      const zero = Math.abs(v) < tk.step / 1000;
      if (!zero) svg.appendChild(S("line", { class: "grid", x1: M.l, x2: M.l + pw, y1: y, y2: y }));
      svg.appendChild(S("text", { class: "tick" + (zero ? " zero-tick" : ""), x: M.l - 8, y: y + 4, "text-anchor": "end" }, fmtMoneyTick(v, tk.step)));
    }
    const multiDay = new Date(x0 * 1000).toDateString() !== new Date(x1 * 1000).toDateString();
    const tt = timeTicks(x0, x1, Math.max(2, Math.floor(pw / (multiDay ? 112 : 76))));       // time axis (a label with a date is wider)
    svg.appendChild(S("line", { class: "axis", x1: M.l, x2: M.l + pw, y1: M.t + ph, y2: M.t + ph }));
    for (const t of tt.ticks) {
      const x = X(t);
      svg.appendChild(S("line", { class: "axis", x1: x, x2: x, y1: M.t + ph, y2: M.t + ph + 4 }));
      svg.appendChild(S("text", { class: "tick", x: x, y: M.t + ph + 18, "text-anchor": x > W - 36 ? "end" : x < M.l + 20 ? "start" : "middle" }, fmtTickTime(t, tt)));
    }
    const y0 = Y(0);                                                      // the $0 line: bold, labelled "$0" on the axis
    svg.appendChild(S("line", { class: "zero", x1: M.l, x2: M.l + pw, y1: y0, y2: y0 }));

    for (const s of series) {                                             // step lines: the total holds until the next trade settles
      let d = "M" + X(x0).toFixed(1) + "," + Y(s.first).toFixed(1);
      for (const t of trades) d += "H" + X(t.close_ts).toFixed(1) + "V" + Y(s.get(t)).toFixed(1);
      d += "H" + X(x1).toFixed(1);
      svg.appendChild(S("path", { class: "ln " + s.cls, d: d }));
    }
    // One marker per settled trade: a filled dot for real money, a ring for the paper twin (a ring, because the two nearly
    // coincide). With very many trades only the newest is marked, and the paper rings drop out first to keep the chart readable.
    for (const s of series) {
      const limit = s.key === "paper" && series.length > 1 ? 24 : 80;
      const marked = trades.length <= limit ? trades : trades.slice(-1);
      for (const t of marked) {
        const at = { cx: X(t.close_ts).toFixed(1), cy: Y(s.get(t)).toFixed(1) };
        svg.appendChild(S("circle", Object.assign(s.key === "paper" ? { class: "pnl-ring", r: 5.5 } : { class: "dot " + s.cls, r: 4.5 }, at)));
      }
    }

    if (!narrow && trades.length) {                                       // direct end labels, pushed apart with leader lines
      const labs = series.map((s) => ({ s: s, yTrue: Y(s.last), y: Y(s.last), v: fmtMoney(s.last, true), name: s.key === "real" ? "Real" : "Paper" }));
      labs.sort((a, b) => a.yTrue - b.yTrue || (a.s.key === "real" ? -1 : 1));
      const GAP = 15;
      for (let i = 0; i < labs.length; i++) labs[i].y = Math.max(labs[i].yTrue, i ? labs[i - 1].y + GAP : M.t + 4);
      for (let i = labs.length - 1; i >= 0; i--) {
        const maxY = i === labs.length - 1 ? M.t + ph : labs[i + 1].y - GAP;
        if (labs[i].y > maxY) labs[i].y = maxY;
      }
      const ex = X(x1);
      for (const L of labs) {
        if (Math.abs(L.y - L.yTrue) > 1.5) svg.appendChild(S("line", { class: "leader", x1: ex + 2, y1: L.yTrue, x2: ex + 9, y2: L.y }));
        const t = S("text", { class: "endlab", x: ex + 12, y: L.y + 4 });
        t.appendChild(S("tspan", { class: "v" }, L.v));
        t.appendChild(S("tspan", { dx: 5 }, L.name));
        svg.appendChild(t);
      }
    }
    if (!trades.length) {
      plotMessage(svg, M.l + pw / 2, M.t + ph / 2 - 14, ok
        ? ["No settled trades yet", (P.open || []).length ? "The first one is counted here once its market settles." : "A trade is counted here once its market has settled."]
        : unavailable());
    }
    const hov = S("g", { class: "hover" });
    svg.appendChild(hov);
    svg.appendChild(S("rect", { class: "hit", x: M.l, y: M.t, width: pw, height: ph }));
    state.pnl = { W: W, H: H, M: M, pw: pw, ph: ph, X: X, Y: Y, trades: trades, series: series, hov: hov };

    // legend (two lines or more) and the note about trades that are not counted yet
    const lg = $("pnl-legend");
    clear(lg);
    lg.hidden = series.length < 2;
    for (const s of series.slice().reverse()) lg.appendChild(legendItem(el("span", "key " + s.cls), s.name, trades.length ? fmtMoney(s.last, true) : ""));
    const openTrades = P.open || [];
    const notes = [];
    if (openTrades.length) {
      const where = openTradesText(openTrades, openTrades.filter((o) => o.state === "overdue").length);
      notes.push(plural(openTrades.length, "open trade") + (where ? " (" + where + ")" : "") + " not counted until " +
        (openTrades.length === 1 ? "it settles." : "they settle."));
    }
    if (P.truncated && trades.length) {
      const b = P.base || { pnl: 0, paper_pnl: 0 };
      notes.push("Older trades are left out of the chart: the lines start from their running total (" +
        (paperView ? fmtMoney(b.pnl, true) + " paper" : fmtMoney(b.pnl, true) + " real, " + fmtMoney(b.paper_pnl, true) + " paper") + ").");
    }
    if (trades.length > 80) notes.push("Markers are shown for the newest trade only; hover the chart to read the others.");
    const note = $("pnl-note");
    note.textContent = notes.join(" ");
    note.hidden = notes.length === 0;
    $("pnl-title").textContent = paperView ? "Paper profit and loss over time" : "Profit and loss over time";
    $("pnl-sub").textContent = paperView
      ? "Running total of the paper twins after each settled trade. This view sent no real orders."
      : "Running total after each settled trade: real money against the paper twin of the same trades.";
    drawPnlHover();
  }

  function pnlNearest(px) {
    const m = state.pnl;
    if (!m || !m.trades.length) return null;
    let best = null, bd = Infinity;
    for (let i = 0; i < m.trades.length; i++) {
      const d = Math.abs(m.X(m.trades[i].close_ts) - px);
      if (d < bd) { bd = d; best = i; }
    }
    return bd <= 48 ? best : null;
  }

  function tipPlace(tip, m, x) {
    tip.hidden = false;
    const tw = tip.offsetWidth;
    let left = x + 14;
    if (left + tw > m.W - 4) left = x - 14 - tw;
    if (left < 4) left = 4;
    tip.style.left = left + "px";
    tip.style.top = (m.M.t + 4) + "px";
  }
  function tipRow(cls, name, value, sub) {
    const row = el("div", "tip-row");
    row.appendChild(el("span", cls));
    const body = el("div");
    const top = el("div", "tip-top");
    top.appendChild(el("strong", "tip-val", value));
    top.appendChild(el("span", "tip-name", name));
    body.appendChild(top);
    if (sub) body.appendChild(el("div", "tip-sub", sub));
    row.appendChild(body);
    return row;
  }

  function drawPnlHover() {
    const m = state.pnl;
    const tip = $("pnl-tip");
    if (!m) return;
    clear(m.hov);
    const i = state.pnlHover;
    if (i === null || i < 0 || i >= m.trades.length) {
      tip.hidden = true;
      announceTo("pnl-live", "");
      return;
    }
    const t = m.trades[i];
    const x = m.X(t.close_ts);
    m.hov.appendChild(S("line", { class: "xhair", x1: x, x2: x, y1: m.M.t, y2: m.M.t + m.ph }));
    for (const s of m.series) {
      m.hov.appendChild(S("circle", Object.assign(s.key === "paper" ? { class: "pnl-ring", r: 6.5 } : { class: "dot " + s.cls, r: 5.5 },
        { cx: x, cy: m.Y(s.get(t)) })));
    }
    clear(tip);
    tip.appendChild(el("div", "tip-time", fmtHMDay(t.close_ts) + " · trade " + (i + 1) + " of " + m.trades.length));
    for (const s of m.series.slice().reverse()) tip.appendChild(tipRow("key " + s.cls, s.name + " (running total)", fmtMoney(s.get(t), true), null));
    tip.appendChild(el("div", "tip-sub tip-trade", (t.ticker || "") + ""));
    tip.appendChild(el("div", "tip-sub", tradeSentence(t) + (isNum(t.paper_pnl) && state.perf.view !== "paper" ? " (paper " + fmtMoney(t.paper_pnl, true) + ")" : "")));
    tipPlace(tip, m, x);
    announceTo("pnl-live", state.pnlKeyboard ? fmtHMDay(t.close_ts) + ": " + m.series.slice().reverse().map((s) => s.name + " " + fmtMoney(s.get(t), true)).join(", ") + ". " + tradeSentence(t) : "");
  }
  function setPnlHover(i, fromKeyboard) {
    state.pnlHover = i;
    state.pnlKeyboard = !!fromKeyboard && i !== null;
    drawPnlHover();
  }

  // ------------------------------------------------------------------ money: result of each trade (bars)
  // A bar from the baseline to the tip: 4 px rounded at the tip, square at the baseline.
  function barPath(x, w, y0, y1) {
    const f = (v) => v.toFixed(1);
    const h = Math.abs(y1 - y0);
    if (h < 0.5) return "M" + f(x) + "," + f(y0 - 1) + "h" + f(w) + "v2h" + f(-w) + "z";
    const r = Math.min(4, h / 2, w / 2);
    const dir = y1 < y0 ? 1 : -1;               // 1: the tip is above the baseline
    return "M" + f(x) + "," + f(y0) + "V" + f(y1 + dir * r) + "Q" + f(x) + "," + f(y1) + " " + f(x + r) + "," + f(y1) + "H" + f(x + w - r) +
      "Q" + f(x + w) + "," + f(y1) + " " + f(x + w) + "," + f(y1 + dir * r) + "V" + f(y0) + "Z";
  }

  function renderBars() {
    const wrap = $("bars-chart");
    const svg = $("bars-svg");
    const ok = perfOk();
    const all = ok ? state.perf.trades : [];
    const trades = all.slice(-MAX_BARS);
    const n = trades.length;
    const W = Math.max(280, Math.floor(wrap.clientWidth));
    const narrow = W < 520;
    const H = narrow ? 240 : 270;
    const M = { l: 56, r: 14, t: 14, b: 30 };
    const pw = W - M.l - M.r;
    const ph = H - M.t - M.b;
    let lo = 0, hi = 0;
    for (const t of trades) { lo = Math.min(lo, t.pnl); hi = Math.max(hi, t.pnl); }
    if (hi - lo < 0.2) hi = lo + 0.2;
    const pad = (hi - lo) * 0.12;
    const tk = moneyTicks(lo < 0 ? lo - pad : lo, hi > 0 ? hi + pad : hi, narrow ? 4 : 6);
    lo = tk.lo;
    hi = tk.hi;
    const Y = (v) => M.t + ((hi - v) / (hi - lo)) * ph;
    const slot = n ? Math.min(56, pw / n) : pw;
    const bw = Math.max(3, Math.min(24, slot - 4));                       // at most 24 px thick, always a gap between neighbours
    const xc = (i) => M.l + slot * (i + 0.5);

    clear(svg);
    svg.setAttribute("width", String(W));
    svg.setAttribute("height", String(H));
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    for (const v of tk.vals) {
      const y = Y(v);
      const zero = Math.abs(v) < tk.step / 1000;
      if (!zero) svg.appendChild(S("line", { class: "grid", x1: M.l, x2: M.l + pw, y1: y, y2: y }));
      svg.appendChild(S("text", { class: "tick" + (zero ? " zero-tick" : ""), x: M.l - 8, y: y + 4, "text-anchor": "end" }, fmtMoneyTick(v, tk.step)));
    }
    svg.appendChild(S("line", { class: "axis", x1: M.l, x2: M.l + pw, y1: M.t + ph, y2: M.t + ph }));
    const y0 = Y(0);
    svg.appendChild(S("line", { class: "zero", x1: M.l, x2: M.l + pw, y1: y0, y2: y0 }));

    const els = [];
    let iBest = -1, iWorst = -1;
    trades.forEach((t, i) => {
      if (iBest < 0 || t.pnl > trades[iBest].pnl) iBest = i;
      if (iWorst < 0 || t.pnl < trades[iWorst].pnl) iWorst = i;
    });
    trades.forEach((t, i) => {
      const cls = t.result === "won" ? "bar bar-win" : "bar bar-loss";
      const p = S("path", { class: cls, d: barPath(xc(i) - bw / 2, bw, y0, Y(t.pnl)) });
      svg.appendChild(p);
      els.push(p);
    });
    if (slot >= 40) {                                                     // value labels on the extremes and the newest bar only
      const want = new Set([iBest, iWorst, n - 1]);
      for (const i of want) {
        if (i < 0 || Math.abs(trades[i].pnl) < 0.005) continue;
        const up = trades[i].pnl >= 0;
        svg.appendChild(S("text", { class: "val-label", x: xc(i), y: Y(trades[i].pnl) + (up ? -6 : 14), "text-anchor": "middle" }, fmtMoney(trades[i].pnl, true)));
      }
    }
    const multi = n > 1 && new Date(trades[0].close_ts * 1000).toDateString() !== new Date(trades[n - 1].close_ts * 1000).toDateString();
    const every = n ? Math.max(1, Math.ceil(n / Math.max(2, Math.floor(pw / (multi ? 104 : 66))))) : 1;
    for (let i = 0; i < n; i += every) {
      const x = xc(i);
      svg.appendChild(S("line", { class: "axis", x1: x, x2: x, y1: M.t + ph, y2: M.t + ph + 4 }));
      svg.appendChild(S("text", { class: "tick", x: x, y: M.t + ph + 18, "text-anchor": x > W - 40 ? "end" : x < M.l + 24 ? "start" : "middle" },
        (multi ? fmtShortDate(trades[i].close_ts) + " " : "") + fmtHM(trades[i].close_ts)));
    }
    if (!n) {
      plotMessage(svg, M.l + pw / 2, M.t + ph / 2 - 14, ok ? ["No settled trades yet", "Each trade gets a bar here once its market has settled."] : unavailable());
    }
    const hov = S("g", { class: "hover" });
    svg.appendChild(hov);
    svg.appendChild(S("rect", { class: "hit", x: M.l, y: M.t, width: pw, height: ph }));
    state.bars = { W: W, H: H, M: M, pw: pw, ph: ph, slot: slot, xc: xc, Y: Y, trades: trades, els: els, hov: hov };

    const lg = $("bars-legend");
    clear(lg);
    lg.appendChild(legendItem(el("span", "key-rect win"), "Win (bar goes up)", ""));
    lg.appendChild(legendItem(el("span", "key-rect loss"), "Loss (bar goes down)", ""));
    const note = $("bars-note");
    const text = all.length > n ? "Showing the newest " + n + " of " + all.length + " settled trades." : "";
    note.textContent = text;
    note.hidden = !text;
    drawBarsHover();
  }

  function barIndexAt(px) {
    const m = state.bars;
    if (!m || !m.trades.length) return null;
    const i = Math.floor((px - m.M.l) / m.slot);
    return i >= 0 && i < m.trades.length ? i : null;
  }

  function drawBarsHover() {
    const m = state.bars;
    const tip = $("bars-tip");
    if (!m) return;
    m.els.forEach((p, k) => p.classList.toggle("hl", k === state.barsHover));
    const i = state.barsHover;
    if (i === null || i < 0 || i >= m.trades.length) {
      tip.hidden = true;
      announceTo("bars-live", "");
      return;
    }
    const t = m.trades[i];
    clear(tip);
    tip.appendChild(el("div", "tip-time", fmtHMDay(t.close_ts) + " · trade " + (i + 1) + " of " + m.trades.length));
    tip.appendChild(tipRow("key-rect " + (t.result === "won" ? "win" : "loss"), t.result === "won" ? "WON" : "LOST", fmtMoney(t.pnl, true), null));
    tip.appendChild(el("div", "tip-sub tip-trade", t.ticker || ""));
    tip.appendChild(el("div", "tip-sub", tradeSentence(t).replace(/ · (WON|LOST) .*$/, "")));
    // fees to 4 decimals, as the tables show them: to the cent a fee of 0.09 cents would read "fees paid $0.00"
    if (isNum(t.paper_pnl) && state.perf.view !== "paper") tip.appendChild(el("div", "tip-sub", "paper twin " + fmtMoney(t.paper_pnl, true) + (isNum(t.fees) ? " · fees paid " + fmtUsd(t.fees) : "")));
    tipPlace(tip, m, m.xc(i));
    announceTo("bars-live", state.barsKeyboard ? fmtHMDay(t.close_ts) + ": " + (t.result === "won" ? "won " : "lost ") + fmtMoney(t.pnl, true) + ". " + tradeSentence(t) : "");
  }
  function setBarsHover(i, fromKeyboard) {
    state.barsHover = i;
    state.barsKeyboard = !!fromKeyboard && i !== null;
    drawBarsHover();
  }

  // The table twin of both graphs: every settled trade, newest first, with its running totals.
  function renderTableView() {
    const tb = $("tv-body");
    clear(tb);
    const trades = perfOk() ? state.perf.trades : [];
    if (!trades.length) {
      tb.appendChild(emptyRow(8, "No settled trades yet."));
      return;
    }
    const paper = state.perf.view === "paper";
    for (let i = trades.length - 1; i >= 0; i--) {
      const t = trades[i];
      const tr = el("tr");
      tr.appendChild(td(fmtHMDay(t.close_ts)));
      tr.appendChild(td(t.ticker || DASH, "mono"));
      tr.appendChild(td(t.side ? t.side + (isNum(t.entry) ? " at " + fmtPrice(t.entry) : "") : isNum(t.entry) ? fmtPrice(t.entry) : DASH));
      const rc = el("td");
      rc.appendChild(icon(t.result === "won" ? "good" : "crit"));
      rc.appendChild(document.createTextNode(t.result === "won" ? "WON" : "LOST"));
      tr.appendChild(rc);
      tr.appendChild(td(paper ? DASH : fmtMoney(t.pnl, true), "num"));
      tr.appendChild(td(isNum(t.paper_pnl) ? fmtMoney(t.paper_pnl, true) : DASH, "num"));
      tr.appendChild(td(paper ? DASH : fmtMoney(t.cum, true), "num"));
      tr.appendChild(td(fmtMoney(paper ? t.cum : t.cum_paper, true), "num"));
      tb.appendChild(tr);
    }
  }

  function initMoneyEvents() {
    const wire = (svgId, nearest, setHover, listLen, getHover) => {
      const svg = $(svgId);
      const onPointer = (ev) => setHover(nearest(ev.clientX - svg.getBoundingClientRect().left));
      svg.addEventListener("pointermove", onPointer);
      svg.addEventListener("pointerdown", onPointer);
      // A finger that lifts leaves the chart too: keep the tooltip then, until the next tap somewhere else.
      svg.addEventListener("pointerleave", (ev) => { if (ev.pointerType !== "touch") setHover(null); });
      document.addEventListener("pointerdown", (ev) => { if (getHover() !== null && !svg.contains(ev.target)) setHover(null); });
      svg.addEventListener("blur", () => setHover(null));
      svg.addEventListener("focus", () => { const n = listLen(); if (n && getHover() === null) setHover(n - 1, true); });
      svg.addEventListener("keydown", (ev) => {
        const n = listLen();
        if (!n) return;
        const cur = getHover();
        let i = cur === null ? n - 1 : cur;
        if (ev.key === "ArrowLeft") i = Math.max(0, i - 1);
        else if (ev.key === "ArrowRight") i = Math.min(n - 1, i + 1);
        else if (ev.key === "Home") i = 0;
        else if (ev.key === "End") i = n - 1;
        else if (ev.key === "Escape") { setHover(null); return; }
        else return;
        ev.preventDefault();
        setHover(i, true);
      });
    };
    wire("pnl-svg", pnlNearest, setPnlHover, () => (state.pnl ? state.pnl.trades.length : 0), () => state.pnlHover);
    wire("bars-svg", barIndexAt, setBarsHover, () => (state.bars ? state.bars.trades.length : 0), () => state.barsHover);
    const later = (fn) => {
      let pending = false;
      return () => {
        if (pending) return;
        pending = true;
        requestAnimationFrame(() => { pending = false; fn(); });
      };
    };
    const targets = [["pnl-chart", renderPnl], ["bars-chart", renderBars], ["gauge-chart", () => { if (state.status) renderGauge(state.status); }]];
    for (const [id, fn] of targets) {
      if (window.ResizeObserver) new ResizeObserver(later(fn)).observe($(id));
      else window.addEventListener("resize", later(fn));
    }
  }

  // ------------------------------------------------------------------ chart
  function byStrike(a, b) {
    const x = a.strike === null ? Infinity : a.strike;
    const y = b.strike === null ? Infinity : b.strike;
    return x - y || String(a.ticker).localeCompare(String(b.ticker));
  }
  function slotClass(s) { return s.slot >= 0 ? "s" + (s.slot + 1) : "sx"; }

  function buildSeries(x0) {
    const by = new Map();
    for (const r of state.quotes.values()) {
      if (!isNum(r.ts) || r.ts < x0) continue;
      let s = by.get(r.strike_ticker);
      if (!s) {
        s = { ticker: r.strike_ticker, strike: strikeValue(r.strike_ticker), pts: [] };
        by.set(r.strike_ticker, s);
      }
      s.pts.push(r);
    }
    const series = Array.from(by.values());
    for (const s of series) {
      s.pts.sort((a, b) => a.ts - b.ts);
      const last = s.pts[s.pts.length - 1];
      s.kind = last.kind;
      s.hour = last.hour_ticker;
    }
    // Sticky colour slots: a strike keeps its colour while it is on screen; new strikes take the lowest free slot
    // in strike order. Past 8 strikes the rest are grey ("other"), never a generated 9th hue.
    const present = new Set(series.map((s) => s.ticker));
    for (const t of Array.from(state.slotOf.keys())) if (!present.has(t)) state.slotOf.delete(t);
    const used = new Set(state.slotOf.values());
    for (const s of series.filter((x) => !state.slotOf.has(x.ticker)).sort(byStrike)) {
      for (let i = 0; i < SLOTS; i++) {
        if (!used.has(i)) { used.add(i); state.slotOf.set(s.ticker, i); break; }
      }
    }
    for (const s of series) s.slot = state.slotOf.has(s.ticker) ? state.slotOf.get(s.ticker) : -1;
    for (const t of Array.from(state.hidden)) if (!present.has(t)) state.hidden.delete(t);
    return series.sort(byStrike);
  }

  function niceTicks(lo, hi, n) {
    const raw = (hi - lo) / Math.max(1, n);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / mag;
    const step = (f >= 7.5 ? 10 : f >= 3.5 ? 5 : f >= 1.5 ? 2 : 1) * mag;
    const a = Math.floor(lo / step) * step;
    const b = Math.ceil(hi / step) * step;
    const vals = [];
    for (let v = a; v <= b + step * 1e-6; v += step) vals.push(Math.round(v / step) * step);
    return { lo: a, hi: b, step, vals };
  }
  function fmtTick(v, step) {
    const dp = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
    if (Math.abs(v) < step / 1000) return "0";
    return (v < 0 ? MINUS : "+") + Math.abs(v).toFixed(dp);
  }
  function lowerBound(arr, t, key) {
    let lo = 0, hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (key(arr[mid]) < t) lo = mid + 1; else hi = mid;
    }
    return lo;
  }
  function nearestTs(list, t) {
    if (!list.length) return null;
    const i = lowerBound(list, t, (v) => v);
    if (i <= 0) return list[0];
    if (i >= list.length) return list[list.length - 1];
    return t - list[i - 1] <= list[i] - t ? list[i - 1] : list[i];
  }
  function nearestPoint(pts, t, tol) {
    const i = lowerBound(pts, t, (p) => p.ts);
    let best = null;
    for (const j of [i - 1, i]) {
      const p = pts[j];
      if (p && isNum(p.edge) && Math.abs(p.ts - t) <= tol && (!best || Math.abs(p.ts - t) < Math.abs(best.ts - t))) best = p;
    }
    return best;
  }
  function lastWithEdge(pts) {
    for (let i = pts.length - 1; i >= 0; i--) if (isNum(pts[i].edge)) return pts[i];
    return null;
  }

  // Detections of the latest run inside the chart window (rows come from /api/opportunities, newest first).
  function chartDetections(x0, x1) {
    const run = state.status && state.status.run;
    const rows = (state.opps && state.opps.rows) || [];
    // The chart is about the latest run; with "all runs" selected the list holds the other runs' rows too, which are left out.
    return rows.filter((r) => isNum(r.detected_ts) && r.detected_ts >= x0 && r.detected_ts <= x1 &&
      (state.scope !== "all" || (run && r.run_id === run.run_id)));
  }

  function renderChart() {
    const wrap = $("chart");
    const svg = $("chart-svg");
    const W = Math.max(300, Math.floor(wrap.clientWidth));
    const narrow = W < 640;
    const H = narrow ? 260 : 330;
    const M = { l: 46, r: narrow ? 16 : 140, t: 22, b: 28 };
    const pw = W - M.l - M.r;
    const ph = H - M.t - M.b;
    const now = serverNow();
    const x1 = now;
    const x0 = now - WINDOW_MIN * 60;
    const series = buildSeries(x0);
    const shown = series.filter((s) => !state.hidden.has(s.ticker));

    // y domain in cents; always includes break-even
    let lo = Infinity, hi = -Infinity;
    for (const s of shown) for (const p of s.pts) {
      if (!isNum(p.edge)) continue;
      const c = p.edge * 100;
      if (c < lo) lo = c;
      if (c > hi) hi = c;
    }
    const hasData = lo <= hi;
    const dataLo = hasData ? lo : 0;
    if (!hasData) { lo = -5; hi = 2; }
    if (state.zoom === "near") lo = Math.max(lo, -10);
    lo = Math.min(lo, -1);
    hi = Math.max(hi, 1);
    const pad = (hi - lo) * 0.06;
    const tk = niceTicks(lo - pad, hi + pad, narrow ? 4 : 6);
    lo = tk.lo;
    hi = tk.hi;
    const X = (t) => M.l + ((t - x0) / (x1 - x0)) * pw;
    const Y = (c) => M.t + ((hi - c) / (hi - lo)) * ph;
    const clampY = (y) => Math.min(M.t + ph, Math.max(M.t, y));

    clear(svg);
    svg.setAttribute("width", String(W));
    svg.setAttribute("height", String(H));
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    const defs = S("defs");
    const cp = S("clipPath", { id: "plot-clip" });
    cp.appendChild(S("rect", { x: M.l, y: M.t - 5, width: pw + 6, height: ph + 10 }));
    defs.appendChild(cp);
    svg.appendChild(defs);

    // trading windows (shaded) and hour closes
    const winS = windowMin() * 60;
    for (let h = Math.ceil(x0 / 3600) * 3600; h < x1 + winS; h += 3600) {
      const a = Math.max(x0, h - winS), b = Math.min(x1, h);
      if (b > a) svg.appendChild(S("rect", { class: "band", x: X(a), y: M.t, width: X(b) - X(a), height: ph }));
      if (h >= x0 && h <= x1) {
        const x = X(h);
        svg.appendChild(S("line", { class: "hour-line", x1: x, x2: x, y1: M.t, y2: M.t + ph }));
        svg.appendChild(S("text", { class: "hour-label", x: x, y: M.t - 7, "text-anchor": x > W - M.r - 40 ? "end" : "middle" },
          fmtHM(h) + " close"));
      }
    }

    // y grid and labels
    for (const v of tk.vals) {
      const y = Y(v);
      if (Math.abs(v) > tk.step / 1000) svg.appendChild(S("line", { class: "grid", x1: M.l, x2: M.l + pw, y1: y, y2: y }));
      svg.appendChild(S("text", { class: "tick", x: M.l - 8, y: y + 4, "text-anchor": "end" }, fmtTick(v, tk.step)));
    }
    svg.appendChild(S("text", { class: "axis-title", x: 2, y: 11 }, "¢ per pair"));

    // x axis
    const stepS = narrow ? 600 : 300;
    svg.appendChild(S("line", { class: "axis", x1: M.l, x2: M.l + pw, y1: M.t + ph, y2: M.t + ph }));
    for (let t = Math.ceil(x0 / stepS) * stepS; t <= x1; t += stepS) {
      const x = X(t);
      svg.appendChild(S("line", { class: "axis", x1: x, x2: x, y1: M.t + ph, y2: M.t + ph + 4 }));
      const anchor = x > W - 28 ? "end" : x < M.l + 20 ? "start" : "middle";
      svg.appendChild(S("text", { class: "tick", x: x, y: M.t + ph + 18, "text-anchor": anchor }, fmtHM(t)));
    }

    // break-even line (bold, labelled)
    const y0 = Y(0);
    svg.appendChild(S("line", { class: "zero", x1: M.l, x2: M.l + pw, y1: y0, y2: y0 }));
    svg.appendChild(S("rect", { class: "zero-label-bg", x: M.l + 4, y: y0 - 17, width: 64, height: 14, rx: 3 }));
    svg.appendChild(S("text", { class: "zero-label", x: M.l + 8, y: y0 - 6 }, "break-even"));

    // lines (broken across gaps) and opportunity markers
    const gapS = Math.max(10, 5 * pollInterval());
    const plot = S("g", { "clip-path": "url(#plot-clip)" });
    for (const s of shown) {
      let d = "";
      let prev = null;
      for (const p of s.pts) {
        if (!isNum(p.edge)) { prev = null; continue; }
        const xy = X(p.ts).toFixed(1) + "," + Y(p.edge * 100).toFixed(1);
        d += prev === null || p.ts - prev > gapS ? "M" + xy + "h0" : "L" + xy;
        prev = p.ts;
      }
      if (d) plot.appendChild(S("path", { class: "ln " + slotClass(s), d: d }));
    }
    // Opportunity markers: all surface-coloured halos first, then the fills, so a run of consecutive points
    // above break-even reads as one solid highlight with a clean outline instead of a pile of rings.
    const halos = S("g", { class: "halo" });
    const fills = S("g");
    for (const s of shown) for (const p of s.pts) {
      if (isOpportunity(p.edge)) {
        const cx = X(p.ts).toFixed(1), cy = Y(p.edge * 100).toFixed(1);
        halos.appendChild(S("circle", { cx: cx, cy: cy, r: 6 }));
        fills.appendChild(S("circle", { class: "dot opp " + slotClass(s), cx: cx, cy: cy, r: 4 }));
      }
    }
    plot.appendChild(halos);
    plot.appendChild(fills);
    svg.appendChild(plot);

    // What the bot actually detected and traded (bot_opportunities), not only quotes above the line: a grey tick for a
    // detection (the paper twin), a thick red one for a live attempt.
    const dets = chartDetections(x0, x1);
    for (const d of dets) {
      const x = X(d.detected_ts).toFixed(1);
      const t = S("line", { class: "det" + (d.live ? " live" : ""), x1: x, x2: x, y1: M.t + 1, y2: M.t + 13 });
      t.appendChild(S("title", null, fmtTime(d.detected_ts) + " · " + (d.live ? "live attempt" : "detected (paper)") +
        (d.strike_ticker ? " · above " + strikeLabel(d.strike_ticker) : "")));
      plot.appendChild(t);
    }

    // direct end labels (value + strike), pushed apart with leader lines when they would collide
    if (!narrow) {
      const labs = [];
      for (const s of shown) {
        const p = lastWithEdge(s.pts);
        if (!p) continue;
        const yTrue = clampY(Y(p.edge * 100));
        labs.push({ s: s, x: X(p.ts), yTrue: yTrue, y: yTrue, v: fmtEdge(p.edge), name: strikeLabel(s.ticker) });
      }
      labs.sort((a, b) => a.yTrue - b.yTrue);
      const GAP = 14;
      for (let i = 0; i < labs.length; i++) labs[i].y = Math.max(labs[i].yTrue, i ? labs[i - 1].y + GAP : M.t + 4);
      for (let i = labs.length - 1; i >= 0; i--) {
        const maxY = i === labs.length - 1 ? M.t + ph : labs[i + 1].y - GAP;
        if (labs[i].y > maxY) labs[i].y = maxY;
      }
      for (const L of labs) {
        const lx = L.x + 12;
        if (Math.abs(L.y - L.yTrue) > 1.5) {
          svg.appendChild(S("line", { class: "leader", x1: L.x + 5, y1: L.yTrue, x2: lx - 3, y2: L.y }));
        }
        const t = S("text", { class: "endlab", x: lx, y: L.y + 4 });
        t.appendChild(S("tspan", { class: "v" }, L.v));
        t.appendChild(S("tspan", { dx: 5 }, L.name));
        svg.appendChild(t);
      }
    }

    if (!hasData) {
      const cx = M.l + pw / 2;
      const allHidden = series.length > 0 && shown.length === 0;           // quotes exist; the legend hid every strike
      svg.appendChild(S("text", { class: "empty-msg", x: cx, y: M.t + ph / 2 - 6, "text-anchor": "middle" },
        allHidden ? "All strikes are hidden" : "No pricing in the last " + WINDOW_MIN + " minutes"));
      svg.appendChild(S("text", { class: "empty-msg", x: cx, y: M.t + ph / 2 + 14, "text-anchor": "middle" },
        allHidden ? "Click a strike in the legend to show it."
          : "The bot prices pairs only during the trading window (last " + windowMin() + " min of each hour)."));
    }

    // hover layer on top
    const hov = S("g", { class: "hover" });
    svg.appendChild(hov);
    svg.appendChild(S("rect", { class: "hit", x: M.l, y: M.t, width: pw, height: ph }));

    const tsSet = new Set();
    for (const s of shown) for (const p of s.pts) if (isNum(p.edge)) tsSet.add(p.ts);
    state.chart = {
      W: W, H: H, M: M, pw: pw, ph: ph, x0: x0, x1: x1, X: X, Y: Y, clampY: clampY, hov: hov, shown: shown,
      tsList: Array.from(tsSet).sort((a, b) => a - b), nSeries: series.length,
    };

    renderLegend(series);
    renderLatest(series);
    const notes = [];
    if (hasData && dataLo < lo) notes.push("Some values are below " + fmtTick(lo, tk.step) + "¢ and are cut off. Choose “All values” to see them.");
    if (series.some((s) => s.slot < 0)) notes.push("Strikes beyond the first eight are drawn in grey.");
    const note = $("chart-note");
    note.textContent = notes.join(" ");
    note.hidden = notes.length === 0;
    drawHover();
  }

  function renderLegend(series) {
    const ul = $("legend");
    clear(ul);
    ul.hidden = series.length === 0;
    for (const s of series) {
      const li = el("li");
      const b = el("button");
      b.type = "button";
      b.dataset.ticker = s.ticker;
      b.setAttribute("aria-pressed", state.hidden.has(s.ticker) ? "false" : "true");
      b.title = s.ticker + " (click to show or hide)";
      b.appendChild(el("span", "key " + slotClass(s)));
      b.appendChild(el("span", "lg-name", strikeLabel(s.ticker)));
      b.appendChild(el("span", "lg-kind", s.kind || ""));
      const p = lastWithEdge(s.pts);
      b.appendChild(el("span", "lg-val", p ? fmtEdge(p.edge) : DASH));
      li.appendChild(b);
      ul.appendChild(li);
    }
  }

  function renderLatest(series) {
    const tb = $("latest-body");
    clear(tb);
    if (!series.length) {
      tb.appendChild(emptyRow(8, "No quotes in the last " + WINDOW_MIN + " minutes."));
      return;
    }
    const now = serverNow();
    for (const s of series) {
      const p = s.pts[s.pts.length - 1];
      const tr = el("tr");
      if (isOpportunity(p.edge)) tr.className = "opp";
      const c = el("td");
      c.appendChild(el("span", "key " + slotClass(s)));
      c.appendChild(document.createTextNode("above " + strikeLabel(s.ticker)));
      c.title = s.ticker + (p.hour_ticker ? " vs " + p.hour_ticker : "");
      tr.appendChild(c);
      tr.appendChild(td(p.kind || DASH));
      tr.appendChild(td(fmtPrice(p.leg_a_ask) + " × " + fmtCount(p.leg_a_size), "num"));
      tr.appendChild(td(fmtPrice(p.leg_b_ask) + " × " + fmtCount(p.leg_b_size), "num"));
      tr.appendChild(td(fmtPrice(p.cost), "num"));
      tr.appendChild(td(fmtPrice(p.fees), "num"));
      const e = el("td", "num");
      if (isOpportunity(p.edge)) e.appendChild(icon("good"));
      e.appendChild(el("b", null, fmtEdge(p.edge)));
      tr.appendChild(e);
      tr.appendChild(td(fmtAge(now - p.ts), "num", fmtTime(p.ts)));
      tb.appendChild(tr);
    }
  }

  function drawHover() {
    const m = state.chart;
    const tip = $("tooltip");
    if (!m) return;
    clear(m.hov);
    const ts = state.hoverTs;
    if (ts === null || ts < m.x0 || ts > m.x1 || !m.tsList.length) {
      tip.hidden = true;
      announce("");
      return;
    }
    const x = m.X(ts);
    m.hov.appendChild(S("line", { class: "xhair", x1: x, x2: x, y1: m.M.t, y2: m.M.t + m.ph }));
    const tol = Math.max(2.5, 1.5 * pollInterval());
    const rows = [];
    for (const s of m.shown) {
      const p = nearestPoint(s.pts, ts, tol);
      if (!p) continue;
      m.hov.appendChild(S("circle", { class: "dot " + slotClass(s), cx: m.X(p.ts), cy: m.clampY(m.Y(p.edge * 100)), r: 4.5 }));
      rows.push({ s: s, p: p });
    }
    rows.sort((a, b) => b.p.edge - a.p.edge);
    if (state.hoverKeyboard) {            // keyboard reading: say the values aloud (the tooltip itself is not announced)
      announce(fmtTime(ts) + ": " + (rows.length
        ? rows.map((r) => fmtEdge(r.p.edge) + " above " + strikeLabel(r.s.ticker)).join(", ") : "no quote at this moment"));
    } else {
      announce("");
    }
    clear(tip);
    tip.appendChild(el("div", "tip-time", fmtTime(ts)));
    for (const r of rows) {
      const row = el("div", "tip-row");
      row.appendChild(el("span", "key " + slotClass(r.s)));
      const body = el("div");
      const top = el("div", "tip-top");
      top.appendChild(el("strong", "tip-val", fmtEdge(r.p.edge)));
      top.appendChild(el("span", "tip-name", "above " + strikeLabel(r.s.ticker)));
      body.appendChild(top);
      body.appendChild(el("div", "tip-sub", "cost " + fmtPrice(r.p.cost) + " · fees " + fmtPrice(r.p.fees)));
      body.appendChild(el("div", "tip-sub", r.p.kind || ""));
      row.appendChild(body);
      tip.appendChild(row);
    }
    if (!rows.length) tip.appendChild(el("div", "tip-sub", "No quote at this moment."));
    tip.hidden = false;
    const tw = tip.offsetWidth;
    let left = x + 14;
    if (left + tw > m.W - 4) left = x - 14 - tw;
    if (left < 4) left = 4;
    tip.style.left = left + "px";
    tip.style.top = (m.M.t + 4) + "px";
  }

  function setHover(ts, fromKeyboard) {
    state.hoverTs = ts;
    state.hoverKeyboard = !!fromKeyboard && ts !== null;
    drawHover();
  }
  // The polite live region next to the chart: only changed text is written, so nothing is announced twice.
  function announce(text) {
    const live = $("chart-live");
    if (live && live.textContent !== text) live.textContent = text;
  }

  function initChartEvents() {
    const svg = $("chart-svg");
    const onPointer = (ev) => {
      const m = state.chart;
      if (!m || !m.tsList.length) return;
      const r = svg.getBoundingClientRect();
      const px = ev.clientX - r.left;
      if (px < m.M.l - 12 || px > m.M.l + m.pw + 12) { setHover(null); return; }
      const t = m.x0 + ((px - m.M.l) / m.pw) * (m.x1 - m.x0);
      const ts = nearestTs(m.tsList, t);
      if (ts === null || Math.abs(m.X(ts) - px) > 40) { setHover(null); return; }
      setHover(ts);
    };
    svg.addEventListener("pointermove", onPointer);
    svg.addEventListener("pointerdown", onPointer);
    svg.addEventListener("pointerleave", () => setHover(null));
    svg.addEventListener("blur", () => setHover(null));
    svg.addEventListener("focus", () => {
      const m = state.chart;
      if (m && m.tsList.length && state.hoverTs === null) setHover(m.tsList[m.tsList.length - 1], true);
    });
    svg.addEventListener("keydown", (ev) => {
      const m = state.chart;
      if (!m || !m.tsList.length) return;
      const list = m.tsList;
      let i = state.hoverTs === null ? list.length - 1 : lowerBound(list, state.hoverTs, (v) => v);
      if (ev.key === "ArrowLeft") i = Math.max(0, i - 1);
      else if (ev.key === "ArrowRight") i = Math.min(list.length - 1, i + 1);
      else if (ev.key === "Home") i = 0;
      else if (ev.key === "End") i = list.length - 1;
      else if (ev.key === "Escape") { setHover(null); return; }
      else return;
      ev.preventDefault();
      setHover(list[i], true);
    });
    $("legend").addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-ticker]");
      if (!b) return;
      const t = b.dataset.ticker;
      if (state.hidden.has(t)) state.hidden.delete(t); else state.hidden.add(t);
      renderChart();
    });
    for (const b of document.querySelectorAll("[data-zoom]")) {
      b.addEventListener("click", () => {
        state.zoom = b.dataset.zoom;
        for (const o of document.querySelectorAll("[data-zoom]")) o.setAttribute("aria-pressed", String(o === b));
        renderChart();
      });
    }
    let pending = false;
    const onResize = () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => { pending = false; renderChart(); });
    };
    if (window.ResizeObserver) new ResizeObserver(onResize).observe($("chart"));
    else window.addEventListener("resize", onResize);
  }

  // ------------------------------------------------------------------ tables
  const OUTCOME = {
    paper_only: ["paper only", ""],
    both_filled: ["both legs filled", "good"],
    filled: ["filled", "good"],
    one_leg_unwound: ["one leg, unwound", "serious"],
    one_leg_held: ["one leg held", "crit"],
    none_filled: ["nothing filled", ""],
    blocked_by_cap: ["blocked by a cap", "warn"],
    unknown: ["outcome unknown", "crit"],
  };

  // What the bot did with the signals (a count of each step). The money itself, compared like with like (real trades that
  // settled against the paper twin of the same trades), is in the "Money so far" section at the top of the page.
  function renderKpis(st) {
    const box = $("kpis");
    clear(box);
    const note = $("kpi-note");
    note.hidden = true;
    const s = st && st.stats ? (state.scope === "all" ? st.stats.all : st.stats.run) : null;
    if (!s) {
      box.appendChild(el("p", "muted", "No bot run yet."));
      return;
    }
    const late = strategyOf(st).kind === "late";
    const items = [
      [late ? "Signals" : "Opportunities", fmtInt(s.opportunities)],
      ["Live attempts", fmtInt(s.live_attempts)],
      [late ? "Filled" : "Both legs filled", fmtInt(late ? s.filled : s.both_filled)],
    ];
    if (!late) items.push(["One leg unwound", fmtInt(s.unwound)]);
    items.push(["Live orders sent", fmtInt(s.live_orders)]);
    if (perfOk()) items.push(["Skipped", fmtInt(state.perf.summary.skipped)], ["Not filled", fmtInt(state.perf.summary.not_filled)]);
    for (const [k, v] of items) {
      const d = el("div", "kpi");
      d.appendChild(el("div", "kpi-label", k));
      d.appendChild(el("div", "kpi-value", v));
      box.appendChild(d);
    }
  }

  const PAIR_COLS = [["Time"], ["Strike"], ["Pair"], ["Status"], ["Paper edge", 1], ["Live edge", 1], ["Δ vs paper", 1],
    ["Latency", 1], ["Realized P&L", 1], ["Paper P&L", 1]];
  const LATE_COLS = [["Time"], ["Market"], ["Bought"], ["Status"], ["Detected at", 1], ["Filled at", 1],
    ["Δ vs detection", 1], ["Latency", 1], ["Realized P&L", 1], ["Paper P&L", 1]];

  function setOppHead(cols) {
    const head = $("opp-head");
    const key = cols === LATE_COLS ? "late" : "pair";
    if (head.dataset.kind === key) return;
    head.dataset.kind = key;
    clear(head);
    const tr = el("tr");
    for (const [label, num] of cols) tr.appendChild(el("th", num ? "num" : null, label));
    head.appendChild(tr);
  }

  // Old wording, only for an answer from a server that does not derive the trade status yet.
  function outcomeCell(r) {
    const key = r.outcome || (r.live ? "pending" : "paper_only");
    const o = OUTCOME[key] || [String(key).replace(/_/g, " "), key === "pending" ? "info" : ""];
    const oc = el("td");
    if (o[1]) oc.appendChild(icon(o[1]));
    oc.appendChild(document.createTextNode(o[0]));
    return oc;
  }
  // The status of a trade in words, with an icon that repeats the tone: "Settled · WON +$0.25 · paper +$0.24",
  // "Open · settles at 03:00 PM", "Skipped · loss cap reached", "Not filled", "Paper only". A settled trade shows the paper
  // twin's P&L beside it (not in the orders table, which is wide enough already); a second muted line explains the rest.
  function statusCell(t, row, compact) {
    if (!t) return row ? outcomeCell(row) : td(DASH);
    const c = el("td", "status");
    c.appendChild(icon(t.tone));
    c.appendChild(el("span", "st-label", tradeLabel(t)));
    if (t.state === "settled" && isNum(t.paper_pnl)) {
      c.title = "Paper twin (the same trade at the detection price): " + fmtMoney(t.paper_pnl, true);
      if (!compact) c.appendChild(el("span", "st-paper", " · paper " + fmtMoney(t.paper_pnl, true)));
    }
    const d = tradeDetail(t);
    if (d) c.appendChild(el("span", "st-detail", d));
    return c;
  }
  function pnlCells(tr, r) {
    const settledLive = r.live && r.outcome && r.outcome !== "none_filled" && r.outcome !== "blocked_by_cap";
    tr.appendChild(td(isNum(r.realized_pnl) ? fmtUsd(r.realized_pnl, true) : settledLive ? "pending" : DASH, "num"));
    tr.appendChild(td(isNum(r.paper_realized_pnl) ? fmtUsd(r.paper_realized_pnl, true) : "pending", "num"));
  }
  function latencyCell(r) {
    return td(isNum(r.latency_ms) ? Math.round(r.latency_ms).toLocaleString("en-US") + " ms" : DASH, "num");
  }

  function renderOpps() {
    const tb = $("opp-body");
    clear(tb);
    const late = strategyOf(state.status).kind === "late";
    setOppHead(late ? LATE_COLS : PAIR_COLS);
    $("opp-sub").textContent = late
      ? "Every time the rule fired, and what became of it (Status). Detected is the ask at detection (the paper twin); Filled is what the live order paid; " +
        "Δ is filled minus detected, the cost of order delay (positive = paid more). Realized P&L is booked when the market settles."
      : "Every pair the bot detected, and what became of it (Status). Paper edge is at detection prices (the paper twin); live edge is what the fills actually gave. " +
        "Δ is live minus paper, the cost of order delay.";
    const all = (state.opps && state.opps.rows) || [];
    const realOnly = state.oppFilter === "real";
    const rows = realOnly ? all.filter((r) => !r.trade || r.trade.real) : all;
    if (!rows.length) {
      tb.appendChild(emptyRow(10, all.length ? "No real-money trades in this list. Choose “All signals” to see the others."
        : late ? "No signals yet. One is logged when the first fresh book of a decision window has a YES mid outside the 0.40-0.60 band."
          : "No opportunities yet. One is logged whenever both legs together cost less than $1 after fees."));
      return;
    }
    for (const r of rows) {
      const tr = el("tr");
      if (r.trade && (r.trade.state === "open" || r.trade.state === "overdue")) tr.className = "open";     // a trade still waiting for its market
      tr.appendChild(td(fmtTime(r.detected_ts)));
      if (late) {
        const per = isNum(r.contracts) && r.contracts > 0 ? r.contracts : 1;
        tr.appendChild(td(r.hour_ticker || DASH, "mono", r.strike_ticker || ""));
        tr.appendChild(td(/\bNO\b/.test(r.kind || "") ? "NO" : /\bYES\b/.test(r.kind || "") ? "YES" : (r.kind || DASH)));
        tr.appendChild(statusCell(r.trade, r));
        tr.appendChild(td(isNum(r.paper_cost) ? fmtPrice(r.paper_cost / per) : DASH, "num"));
        tr.appendChild(td(isNum(r.live_cost) ? fmtPrice(r.live_cost / per) : DASH, "num"));
        tr.appendChild(td(isNum(r.live_cost) && isNum(r.paper_cost) ? fmtCentsSigned((r.live_cost - r.paper_cost) / per * 100) : DASH, "num"));
      } else {
        tr.appendChild(td(strikeLabel(r.strike_ticker), null, (r.strike_ticker || "") + (r.hour_ticker ? " vs " + r.hour_ticker : "")));
        tr.appendChild(td(r.kind || DASH));
        tr.appendChild(statusCell(r.trade, r));
        tr.appendChild(td(fmtEdge(r.paper_edge), "num"));
        tr.appendChild(td(r.fees_pending ? "fees pending" : fmtEdge(r.live_edge), "num"));
        tr.appendChild(td(r.fees_pending ? "fees pending" : fmtEdge(r.edge_change), "num"));
      }
      tr.appendChild(latencyCell(r));
      pnlCells(tr, r);
      tb.appendChild(tr);
    }
  }

  function renderOrders() {
    const tb = $("orders-body");
    clear(tb);
    const rows = (state.orders && state.orders.rows) || [];
    if (!rows.length) {
      tb.appendChild(emptyRow(12, "No orders yet."));
      return;
    }
    for (const r of rows) {
      const tr = el("tr");
      if (r.trade && (r.trade.state === "open" || r.trade.state === "overdue")) tr.className = "open";
      tr.appendChild(td(fmtTime(isNum(r.ts_sent) ? r.ts_sent : r.ts_ack)));
      tr.appendChild(td(r.ticker || DASH, "mono", r.client_order_id ? "client order id " + r.client_order_id : null));
      tr.appendChild(td((r.action || "?") + " " + String(r.side || "?").toUpperCase()));
      tr.appendChild(td(fmtCount(r.count), "num"));
      tr.appendChild(td(fmtPrice(r.limit_price), "num"));
      const resp = r.response === null || r.response === undefined ? null
        : clip(typeof r.response === "string" ? r.response : JSON.stringify(r.response), 600);
      tr.appendChild(td(r.status || DASH, null, resp));
      tr.appendChild(td(fmtCount(r.filled), "num"));
      tr.appendChild(td(fmtPrice(r.avg_fill_price), "num"));
      tr.appendChild(td(fmtUsd(r.fees), "num"));
      tr.appendChild(td(isNum(r.ts_sent) && isNum(r.ts_ack) ? Math.round((r.ts_ack - r.ts_sent) * 1000).toLocaleString("en-US") + " ms" : DASH, "num"));
      // "real money" or "paper" (never the ambiguous LIVE): whether the order went to Kalshi. What became of the trade is
      // the next column, so an order that settled long ago no longer looks live.
      const real = r.dry_run === 0 || r.dry_run === false;
      const money = typeof r.money === "string" && r.money ? r.money : real ? "real money" : "paper";
      const mc = el("td");
      mc.appendChild(el("span", money === "real money" ? "tag real" : "tag", money));
      tr.appendChild(mc);
      tr.appendChild(r.trade ? statusCell(r.trade, null, true) : td(DASH));
      tb.appendChild(tr);
    }
  }

  function renderEvents() {
    const tb = $("events-body");
    clear(tb);
    const rows = (state.events && state.events.rows) || [];
    if (!rows.length) {
      tb.appendChild(emptyRow(4, "No events yet."));
      return;
    }
    for (const r of rows) {
      const tr = el("tr");
      tr.appendChild(td(fmtTime(r.ts)));
      const lv = String(r.level || "info").toLowerCase();
      const tone = lv === "error" ? "crit" : lv === "warn" || lv === "warning" ? "warn" : "info";
      const lc = el("td");
      lc.appendChild(icon(tone));
      lc.appendChild(document.createTextNode(lv));
      tr.appendChild(lc);
      tr.appendChild(td(r.kind || DASH));
      const mc = el("td", "msg");
      const hasData = r.data !== null && r.data !== undefined &&
        !(typeof r.data === "object" && Object.keys(r.data).length === 0);
      if (hasData) {
        const d = el("details");
        const id = String(r.id);
        d.open = state.openEvents.has(id);
        d.addEventListener("toggle", () => { if (d.open) state.openEvents.add(id); else state.openEvents.delete(id); });
        d.appendChild(el("summary", null, r.message || "(no message)"));
        d.appendChild(el("pre", null, typeof r.data === "string" ? r.data : JSON.stringify(r.data, null, 2)));
        mc.appendChild(d);
      } else {
        mc.textContent = r.message || "";
      }
      tr.appendChild(mc);
      tb.appendChild(tr);
    }
  }

  // ------------------------------------------------------------------ research
  function researchBlock(title, updatedTs) {
    const b = el("div", "research-block");
    const h = el("h3", null, title);
    if (isNum(updatedTs)) h.appendChild(el("span", "updated", "updated " + fmtTime(updatedTs)));
    b.appendChild(h);
    return b;
  }
  function facts(pairs) {
    const ul = el("ul", "facts");
    for (const [k, v] of pairs) {
      const li = el("li");
      li.appendChild(document.createTextNode(k + " "));
      li.appendChild(el("b", null, v));
      ul.appendChild(li);
    }
    return ul;
  }
  function table(headers, rows, numCols) {
    const wrap = el("div", "table-wrap");
    const t = el("table");
    const tr = el("tr");
    headers.forEach((h, i) => tr.appendChild(el("th", numCols.has(i) ? "num" : null, h)));
    const thead = el("thead");
    thead.appendChild(tr);
    t.appendChild(thead);
    const tb = el("tbody");
    for (const r of rows) {
      const row = el("tr");
      r.forEach((v, i) => {
        if (v instanceof Node) { const c = el("td", numCols.has(i) ? "num" : null); c.appendChild(v); row.appendChild(c); }
        else row.appendChild(td(v, numCols.has(i) ? "num" : null));
      });
      tb.appendChild(row);
    }
    t.appendChild(tb);
    wrap.appendChild(t);
    return wrap;
  }
  function verdictLine(tone, text) {
    const p = el("div", "verdict");
    p.appendChild(icon(tone));
    p.appendChild(el("span", null, text));
    return p;
  }
  function yesNo(v) { return v === true ? "yes" : v === false ? "no" : DASH; }
  function fmtC(v) { return isNum(v) ? fmtCentsSigned(v) : DASH; }

  function renderResearch() {
    const r = state.research;
    const box = $("research");
    clear(box);
    if (!r) { box.appendChild(el("p", "muted", "Loading…")); return; }
    const up = r.updated || {};
    const grid = el("div", "research-grid");

    // download progress
    const prog = researchBlock("Download progress");
    const PROG = [
      ["h1_final_minute_trades", "H1 final-minute trade prints", "markets"],
      ["h2_cross_bounds_pairs", "H2 hourly-strike candles", "hours"],
    ];
    for (const [key, label, unit] of PROG) {
      const p = (r.progress || {})[key] || {};
      const row = el("div", "progress-row");
      row.appendChild(el("div", null, label));
      const meter = el("div", "meter");
      const fill = el("div", "meter-fill");
      const frac = isNum(p.done) && isNum(p.total) && p.total > 0 ? Math.min(1, p.done / p.total) : 0;
      fill.style.width = (frac * 100).toFixed(1) + "%";
      meter.setAttribute("role", "meter");
      meter.setAttribute("aria-label", label);
      meter.setAttribute("aria-valuemin", "0");
      meter.setAttribute("aria-valuemax", "100");
      meter.setAttribute("aria-valuenow", String(Math.round(frac * 100)));
      meter.appendChild(fill);
      row.appendChild(meter);
      row.appendChild(el("div", "num", isNum(p.done)
        ? fmtInt(p.done) + " / " + fmtInt(p.total) + " " + unit + " (" + (frac * 100).toFixed(1) + "%)"
        : "not available"));
      prog.appendChild(row);
    }
    grid.appendChild(prog);

    // H1
    const h1 = researchBlock("H1 · final-minute settlement averaging (KXBTC15M)", up.h1_verdict || up.h1_summary);
    const s1 = r.h1_summary && typeof r.h1_summary === "object" ? r.h1_summary : null;
    const v1 = Array.isArray(r.h1_verdict) ? r.h1_verdict : [];
    if (!s1 && !v1.length) {
      h1.appendChild(el("p", "muted", "No results yet (h1_summary.json not found)."));
    } else {
      const passing = v1.length ? v1.filter((x) => x.PASS === true) : (Array.isArray(s1 && s1.buckets_passing) ? s1.buckets_passing : []);
      const n = v1.length;
      if (passing.length) {
        h1.appendChild(verdictLine("good", passing.length + (n ? " of " + n : "") + " buckets pass all four pre-registered criteria: " +
          passing.map((x) => x.sec_b + " left at " + x.price_b).join("; ") + ". Next step per the plan: a forward test."));
      } else {
        h1.appendChild(verdictLine("none", "No bucket passes all four pre-registered criteria" + (n ? " (0 of " + n + ")." : ".")));
      }
      if (s1) {
        const f = [];
        if (isNum(s1.markets_with_data)) f.push(["Markets with data", fmtInt(s1.markets_with_data)]);
        if (isNum(s1.markets_downloaded)) f.push(["Markets downloaded", fmtInt(s1.markets_downloaded)]);
        if (isNum(s1.observations)) f.push(["Observations", fmtInt(s1.observations)]);
        const bs = s1.markets_by_split;
        if (bs && typeof bs === "object") for (const k of ["early", "later", "fresh"]) if (isNum(bs[k])) f.push([k[0].toUpperCase() + k.slice(1), fmtInt(bs[k])]);
        h1.appendChild(facts(f));
      }
      if (v1.length) {
        const det = el("details");
        det.open = state.h1Open;
        det.addEventListener("toggle", () => { state.h1Open = det.open; });
        det.appendChild(el("summary", null, "All " + v1.length + " buckets (edge in cents per contract after fees)"));
        const rows = v1.map((x) => {
          let pass;
          if (x.PASS === true) { pass = el("span", "tag good", "PASS"); }
          else { pass = document.createTextNode("fail"); }
          return [x.sec_b ?? DASH, x.price_b ?? DASH, fmtC(x.early_c), fmtC(x.later_c), fmtC(x.fresh_c), fmtC(x.oos_mean_c),
            fmtC(x.oos_bonferroni_lo_c), fmtInt(x.oos_markets), fmtInt(x.median_size), fmtC(x.control_oos_mean_c),
            yesNo(x.pass_all_periods), yesNo(x.pass_bonferroni), yesNo(x.pass_size), yesNo(x.pass_mechanism), pass];
        });
        det.appendChild(table(["Seconds left", "Favorite price", "Early", "Later", "Fresh", "Later+fresh", "Bonferroni low",
          "Markets", "Median size", "Control", "Every period > 0", "CI > 0", "Enough size", "Mechanism", "Result"],
        rows, new Set([2, 3, 4, 5, 6, 7, 8, 9])));
        h1.appendChild(det);
      }
    }
    grid.appendChild(h1);

    // H2
    const h2 = researchBlock("H2 · hourly (KXBTCD) vs 15-minute bounds, the pair the bot trades", up.h2_by_split || up.h2_summary);
    const v2 = Array.isArray(r.h2_by_split) ? r.h2_by_split : [];
    if (!v2.length) {
      h2.appendChild(el("p", "muted", "No results yet (h2_cross_bounds_summary.csv not found)."));
    } else {
      const all = v2.find((x) => x.split === "all");
      if (all) {
        h2.appendChild(verdictLine("info", "All periods: " + fmtInt(all.violations) + " violations in " + fmtInt(all.hours_with_violation) +
          " of " + fmtInt(all.hours) + " hours; mean profit at detection " + fmtC(all.mean_profit_c) + ", realized " +
          fmtC(all.realized_mean_c) + " per trade; " + (isNum(all.persist_next_min) ? Math.round(all.persist_next_min * 100) + "%" : DASH) +
          " still there one minute later; about " + fmtUsd(all.usd_per_day_1_contract) + " per day at 1 contract."));
      }
      const order = { early: 0, later: 1, fresh: 2, all: 3 };
      const rows = v2.slice().sort((a, b) => (order[a.split] ?? 9) - (order[b.split] ?? 9)).map((x) => [
        x.split ?? DASH, fmtInt(x.hours), fmtInt(x.snapshots), fmtInt(x.violations), fmtInt(x.hours_with_violation),
        fmtC(x.mean_profit_c), fmtC(x.realized_mean_c), isNum(x.persist_next_min) ? (x.persist_next_min * 100).toFixed(0) + "%" : DASH,
        fmtUsd(x.usd_per_day_1_contract), fmtInt(x.payout_below_1)]);
      h2.appendChild(table(["Period", "Hours", "Snapshots", "Violations", "Hours with one", "Profit at detection",
        "Realized", "Still there +1 min", "$/day, 1 contract", "Payout < $1"], rows, new Set([1, 2, 3, 4, 5, 6, 7, 8, 9])));
    }
    grid.appendChild(h2);

    // H4
    const h4 = researchBlock("H4 · liquidity-reward programs", up.h4_summary);
    const s4 = r.h4_summary && typeof r.h4_summary === "object" ? r.h4_summary : null;
    if (!s4) {
      h4.appendChild(el("p", "muted", "No results yet (h4_lip_summary.json not found)."));
    } else {
      const LABELS = {
        active_programs: ["Active programs", fmtInt],
        reward_per_day_total_usd: ["Rewards per day, all programs", (v) => isNum(v) ? "$" + fixed(v, 0) : DASH],
        checked_books: ["Order books checked", fmtInt],
        meeting_target_now: ["Books meeting target now", fmtInt],
        "shortlist_q50_ge_0.5pct": ["Shortlist (median ≥ 0.5% of collateral per day)", fmtInt],
      };
      const f = [];
      for (const k of Object.keys(s4)) {
        const v = s4[k];
        if (LABELS[k]) f.push([LABELS[k][0], LABELS[k][1](v)]);
        else f.push([k.replace(/_/g, " "), isNum(v) ? fmtCount(v) : typeof v === "object" ? JSON.stringify(v) : String(v)]);
      }
      h4.appendChild(facts(f));
    }
    grid.appendChild(h4);
    box.appendChild(grid);
  }

  // ------------------------------------------------------------------ data loading
  async function getJSON(path) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 10000);
    try {
      const r = await fetch(path, { cache: "no-store", signal: ctl.signal, headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } finally {
      clearTimeout(timer);
    }
  }

  async function pollQuotes() {
    const base = "/api/quotes?minutes=" + WINDOW_MIN;
    const incremental = state.lastQuoteTs !== null;
    // Ask again for the last 15 s and de-duplicate, so rows committed slightly out of order are not missed.
    let res = await getJSON(incremental ? base + "&since=" + (state.lastQuoteTs - 15).toFixed(3) : base);
    if (res.run_id !== state.quotesRun) {
      state.quotes.clear();
      state.slotOf.clear();
      state.lastQuoteTs = null;
      state.quotesRun = res.run_id;
      if (incremental) res = await getJSON(base);
    }
    for (const r of res.rows || []) {
      if (!isNum(r.ts)) continue;
      state.quotes.set(r.ts + "|" + r.strike_ticker, r);
      if (state.lastQuoteTs === null || r.ts > state.lastQuoteTs) state.lastQuoteTs = r.ts;
    }
    const cutoff = serverNow() - WINDOW_MIN * 60 - 5;
    for (const [k, r] of state.quotes) if (r.ts < cutoff) state.quotes.delete(k);
  }

  // The money section is drawn from its own data. A drawing bug there must not turn into the "lost contact" banner or stop the
  // tables from updating: it is reported in the browser console and the rest of the page carries on.
  function safely(fn, arg) {
    try {
      fn(arg);
    } catch (err) {
      console.error("dashboard: " + fn.name + " failed", err);
    }
  }
  function renderMoneyAll() {
    safely(renderMoney);
    safely(renderPnl);
    safely(renderBars);
    safely(renderTableView);
  }

  // The money figures (summary, settled trades with running totals, open trades) have their own endpoint. They are fetched
  // again when a number that changes with a trade changes, or every PERF_EVERY_MS, and a failure never breaks the rest of the page.
  async function pollPerf(st) {
    const scope = state.scope === "all" ? "all" : "latest";
    const s = st && st.stats ? (scope === "all" ? st.stats.all : st.stats.run) : null;
    const sig = [scope, st && st.run ? st.run.run_id : "",
      s ? [s.opportunities, s.live_attempts, s.filled, s.paired_n, s.pending_n, s.realized_pnl, s.paper_realized_pnl, s.last_ts].join(",") : ""].join("|");
    if (sig === state.perfSig && Date.now() < state.perfNext) return;
    state.perfSig = sig;
    state.perfNext = Date.now() + PERF_EVERY_MS;
    try {
      state.perf = await getJSON("/api/performance?limit=300" + (scope === "all" ? "&run=all" : ""));
      state.perfError = "";
    } catch (err) {
      state.perfError = err && err.name === "AbortError" ? "timed out" : String((err && err.message) || err);
    }
  }

  function renderAll() {
    const st = state.status;
    if (st) {
      renderStrategy(st);
      renderMode(st);
      renderBotStatus(st);
      if (!$("chart-card").hidden) renderChart();     // before the tiles: the hour tile reports the number of strikes
      renderTiles(st);
      safely(renderGauge, st);
      renderCaps(st);
      renderDetail(st);
      renderKill(st);
      renderKpis(st);
    }
    renderMoneyAll();
    renderBanners();
    renderOpps();
    renderOrders();
    renderEvents();
    renderClock();
    updateTitle();
  }

  let inFlight = false;
  let again = false;
  async function poll() {
    if (inFlight) { again = true; return; }
    inFlight = true;
    clearTimeout(state.timer);
    try {
      let first = null;
      if (state.scope === null) {            // nothing chosen or remembered: the default depends on the latest run, so ask for it first
        first = await getJSON("/api/status");
        if (state.scope === null) {          // (a click on a button while the answer was on its way wins)
          state.scope = defaultScope(first);
          showScope();
        }
      }
      const scopeQ = state.scope === "all" ? "&run=all" : "";
      const [st, opps, orders, events] = await Promise.all([
        first || getJSON("/api/status"),
        getJSON("/api/opportunities?limit=100" + scopeQ),
        getJSON("/api/orders?limit=100" + scopeQ),
        getJSON("/api/events?limit=200" + scopeQ),
      ]);
      if (isNum(st.server_time)) state.skew = st.server_time - Date.now() / 1000;
      if (st.dashboard && isNum(st.dashboard.refresh_s)) state.refreshS = st.dashboard.refresh_s;
      state.status = st;
      state.opps = opps;
      state.orders = orders;
      state.events = events;
      await Promise.all([pollQuotes(), pollPerf(st)]);
      state.connected = true;
      $("main").classList.remove("stale");
      renderAll();
    } catch (err) {
      state.connected = false;
      state.connError = err && err.name === "AbortError" ? "timed out" : String((err && err.message) || err);
      $("main").classList.add("stale");
      renderBanners();
    } finally {
      inFlight = false;
    }
    if (Date.now() - state.lastResearchAt > RESEARCH_EVERY_MS) {
      state.lastResearchAt = Date.now();
      getJSON("/api/research").then((r) => { state.research = r; renderResearch(); }).catch(() => {});
    }
    if (again) {
      again = false;
      poll();
    } else {
      state.timer = setTimeout(poll, Math.max(500, state.refreshS * 1000));
    }
  }

  // ------------------------------------------------------------------ STOP button
  function initStop() {
    const btn = $("stop");
    btn.addEventListener("click", async () => {
      const st = state.status;
      if (st && st.kill_switch_active) return;
      const file = (st && (st.kill_switch_path || st.kill_switch_file)) || "data/KILL";      // the full path, so it can be found
      const running = !!(st && st.run && !runEnded(st));
      const ok = window.confirm(
        (running ? "Stop the bot now?\n\n" : "No bot run is active right now. Create the kill switch anyway?\n\n") +
        "This creates the kill-switch file (" + file + "). " +
        (running ? "The bot stops placing orders, cancels any resting orders and shuts down."
          : "While it exists, the bot will refuse to trade when it starts.") +
        "\n\nTo trade again you must delete that file and restart the bot.");
      if (!ok) return;
      btn.dataset.busy = "1";
      btn.disabled = true;
      btn.textContent = "STOPPING…";
      try {
        const r = await fetch("/api/kill", { method: "POST", headers: { "X-KPM-Kill": "1" }, cache: "no-store" });
        let body = {};
        try { body = await r.json(); } catch (e) { /* non-JSON error page */ }
        if (!r.ok || !body.ok) throw new Error(body.error || "HTTP " + r.status);
        if (state.status) {
          state.status.kill_switch_active = true;
          state.status.kill_switch_file = body.kill_switch_file || state.status.kill_switch_file;
          state.status.kill_switch_path = body.kill_switch_path || state.status.kill_switch_path;
        }
      } catch (err) {
        window.alert("Could not create the kill-switch file: " + ((err && err.message) || err) +
          "\n\nStop the bot by hand: press Ctrl+C in its window, or create the file " + file + " in the project folder.");
      } finally {
        delete btn.dataset.busy;
        if (state.status) { renderKill(state.status); renderBanners(); renderCaps(state.status); updateTitle(); }
        poll();
      }
    });
  }

  // Which runs the money figures, graphs and tables cover. A restart of the bot is a new run, so "latest run" would blank every
  // figure and graph the moment the bot restarts; while the bot trades real money the default is therefore every run (the loss
  // cap and the balance are account-wide too), and "latest run" is one click away. A watch-only latest run keeps its own paper
  // twins on screen. What the person picks is remembered by the browser (when it lets us; the page works without).
  function defaultScope(st) { return st && st.run && st.run.mode === "live" ? "all" : "latest"; }
  function savedScope() {
    try {
      const v = window.localStorage.getItem(SCOPE_KEY);
      return v === "all" || v === "latest" ? v : null;
    } catch (e) { return null; }             // blocked storage, a private window: no memory, the default applies
  }
  function saveScope(v) {
    try { window.localStorage.setItem(SCOPE_KEY, v); } catch (e) { /* the choice just does not stick */ }
  }
  function showScope() {
    for (const o of document.querySelectorAll("[data-scope]")) o.setAttribute("aria-pressed", String(o.dataset.scope === state.scope));
  }

  function initScope() {
    state.scope = savedScope();
    if (state.scope !== null) showScope();
    for (const b of document.querySelectorAll("[data-scope]")) {
      b.addEventListener("click", () => {
        state.scope = b.dataset.scope;
        saveScope(state.scope);
        showScope();
        state.perf = null;                       // do not show the other scope's money while the new figures load
        state.perfSig = "";
        state.perfError = "";
        renderKpis(state.status);
        renderMoneyAll();
        poll();
      });
    }
  }

  function initOppFilter() {
    for (const b of document.querySelectorAll("[data-opps]")) {
      b.addEventListener("click", () => {
        state.oppFilter = b.dataset.opps;
        for (const o of document.querySelectorAll("[data-opps]")) o.setAttribute("aria-pressed", String(o === b));
        renderOpps();
      });
    }
  }

  function start() {
    initChartEvents();
    initMoneyEvents();
    initStop();
    initScope();
    initOppFilter();
    renderResearch();
    renderClock();
    setInterval(renderClock, 1000);
    poll();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
