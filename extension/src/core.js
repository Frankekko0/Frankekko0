/*
 * FlipFinder for Vinted - pure logic shared by the service worker, the content script and the
 * live panel: options, the sync queue (dedupe by Vinted ID, retry with backoff), the pacing of
 * page reads, and the live ranking. No browser APIs: unit-tested in Node.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FlipFinderCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // ------------------------------------------------------------------ options
  const DEFAULT_OPTIONS = Object.freeze({
    appUrl: "http://localhost:3000",
    enabled: true,
    captureCards: true,
    captureItems: true,
    badges: true,
    // Highlight / alert thresholds.
    minScore: 70,
    minMargin: 10,
    // Default filters of the live panel.
    budget: null,
    brands: "",
    sizes: "",
    excludeFakeRisk: true,
    alertsVisual: true,
    alertsSound: false,
    // Slow, optional reads of other pages (never on by default).
    autoDeep: false,
    autoDeepPerHour: 10,
    slowRefresh: false,
  });

  const clampNum = (v, lo, hi, dflt) => {
    const n = Number(v);
    return v === "" || v === null || v === undefined || !Number.isFinite(n) ? dflt : Math.min(hi, Math.max(lo, n));
  };

  function normalizeOptions(o) {
    const src = { ...DEFAULT_OPTIONS, ...(o || {}) };
    const bool = (k) => (typeof src[k] === "boolean" ? src[k] : DEFAULT_OPTIONS[k]);
    return {
      appUrl: typeof src.appUrl === "string" && src.appUrl ? src.appUrl : DEFAULT_OPTIONS.appUrl,
      enabled: bool("enabled"),
      captureCards: bool("captureCards"),
      captureItems: bool("captureItems"),
      badges: bool("badges"),
      minScore: clampNum(src.minScore, 0, 100, DEFAULT_OPTIONS.minScore),
      minMargin: clampNum(src.minMargin, -1000, 10000, DEFAULT_OPTIONS.minMargin),
      budget: clampNum(src.budget, 1, 100000, null),
      brands: typeof src.brands === "string" ? src.brands.slice(0, 500) : "",
      sizes: typeof src.sizes === "string" ? src.sizes.slice(0, 200) : "",
      excludeFakeRisk: bool("excludeFakeRisk"),
      alertsVisual: bool("alertsVisual"),
      alertsSound: bool("alertsSound"),
      autoDeep: bool("autoDeep"),
      autoDeepPerHour: clampNum(src.autoDeepPerHour, 1, 20, DEFAULT_OPTIONS.autoDeepPerHour),
      slowRefresh: bool("slowRefresh"),
    };
  }

  /** "flip.example.com/" -> "https://flip.example.com"; throws on anything but http(s). */
  function normalizeAppUrl(value) {
    let v = String(value || "").trim();
    if (!v) return DEFAULT_OPTIONS.appUrl;
    if (!/^[a-z]+:\/\//i.test(v)) v = `https://${v}`;
    const url = new URL(v);
    if (!/^https?:$/.test(url.protocol)) throw new Error("protocol");
    return (url.origin + url.pathname).replace(/\/+$/, "");
  }

  const splitList = (s) =>
    String(s || "")
      .split(",")
      .map((x) => x.trim().toLowerCase())
      .filter(Boolean);

  // ------------------------------------------------------------------ sync queue
  const MAX_BACKOFF_MS = 10 * 60 * 1000;

  function emptyQueue() {
    return { seq: 0, cards: {}, items: {} };
  }

  /** 5 s, 10 s, 20 s ... capped at 10 minutes. */
  function backoffMs(attempts) {
    return Math.min(MAX_BACKOFF_MS, 5000 * 2 ** Math.max(0, attempts - 1));
  }

  /**
   * Adds captures to the queue, one entry per Vinted ID (the latest capture wins and is sent
   * as soon as possible). kind: "cards" | "items". Returns the number of entries touched.
   */
  function queueAdd(queue, kind, entries, now) {
    const box = queue[kind];
    let n = 0;
    for (const e of entries) {
      if (!e || !e.vid || !e.payload) continue;
      queue.seq += 1;
      box[e.vid] = { ...e, seq: queue.seq, attempts: 0, nextAt: now, addedAt: (box[e.vid] && box[e.vid].addedAt) || now };
      n += 1;
    }
    return n;
  }

  /** Due entries of one kind, grouped so one request carries one page type. */
  function queueTake(queue, kind, now, max) {
    const due = Object.values(queue[kind])
      .filter((e) => e.nextAt <= now)
      .sort((a, b) => a.seq - b.seq);
    if (!due.length) return null;
    const pageType = due[0].pageType || "other";
    const entries = due.filter((e) => (e.pageType || "other") === pageType).slice(0, max);
    return { pageType, entries };
  }

  /** Removes sent entries, unless a newer capture of the same item arrived meanwhile. */
  function queueDone(queue, kind, entries) {
    for (const e of entries) {
      const cur = queue[kind][e.vid];
      if (cur && cur.seq === e.seq) delete queue[kind][e.vid];
    }
  }

  function queueFail(queue, kind, entries, now, retryAfterMs = 0) {
    for (const e of entries) {
      const cur = queue[kind][e.vid];
      if (!cur || cur.seq !== e.seq) continue;
      cur.attempts += 1;
      cur.nextAt = now + Math.max(retryAfterMs, backoffMs(cur.attempts));
    }
  }

  function queueSize(queue) {
    return Object.keys(queue.cards).length + Object.keys(queue.items).length;
  }

  /** Earliest time something is due (null when empty). */
  function queueNextAt(queue) {
    let next = null;
    for (const kind of ["cards", "items"]) {
      for (const e of Object.values(queue[kind])) if (next === null || e.nextAt < next) next = e.nextAt;
    }
    return next;
  }

  /** Drops entries older than a day (stale observations are worth less than a clean queue). */
  function queuePrune(queue, now, maxAgeMs = 24 * 3600 * 1000) {
    let dropped = 0;
    for (const kind of ["cards", "items"]) {
      for (const [vid, e] of Object.entries(queue[kind])) {
        if (now - e.addedAt > maxAgeMs) {
          delete queue[kind][vid];
          dropped += 1;
        }
      }
    }
    return dropped;
  }

  // ------------------------------------------------------------------ pacing of page reads
  // Reads of other pages (deep analysis, status checks) are few and slow. On command they
  // start right away (at most one every 4 s); automatic ones wait 30-60 s between reads and
  // stop for 6 hours at the first sign of a refusal.
  const PACING = Object.freeze({
    manualGapMs: 4000,
    autoGapMs: 30000,
    refreshGapMs: 60000,
    manualPerHour: 60,
    backgroundPerHour: 20,
    pauseMs: 6 * 3600 * 1000,
  });

  function emptyPacing() {
    return { lastAt: 0, hourStart: 0, counts: { manual: 0, auto: 0, refresh: 0 }, pausedUntil: 0, pauseReason: "" };
  }

  function pacingCheck(state, kind, now, opts) {
    const s = state || emptyPacing();
    if (s.pausedUntil > now) return { ok: false, waitMs: s.pausedUntil - now, reason: "paused" };
    const counts = now - s.hourStart >= 3600000 ? { manual: 0, auto: 0, refresh: 0 } : s.counts;
    const gap = kind === "manual" ? PACING.manualGapMs : kind === "refresh" ? PACING.refreshGapMs : PACING.autoGapMs;
    const since = now - s.lastAt;
    if (since < gap) return { ok: false, waitMs: gap - since, reason: "gap" };
    const hourLeft = Math.max(1000, 3600000 - (now - s.hourStart));
    if (kind === "manual" && counts.manual >= PACING.manualPerHour) return { ok: false, waitMs: hourLeft, reason: "hourly" };
    if (kind !== "manual") {
      const cap = Math.min(PACING.backgroundPerHour, kind === "auto" ? (opts && opts.autoDeepPerHour) || 10 : PACING.backgroundPerHour);
      const used = kind === "auto" ? counts.auto : counts.auto + counts.refresh;
      if (used >= cap || counts.auto + counts.refresh >= PACING.backgroundPerHour) return { ok: false, waitMs: hourLeft, reason: "hourly" };
    }
    return { ok: true, waitMs: 0, reason: "" };
  }

  function pacingRecord(state, kind, now) {
    const s = { ...emptyPacing(), ...(state || {}) };
    if (now - s.hourStart >= 3600000) {
      s.hourStart = now;
      s.counts = { manual: 0, auto: 0, refresh: 0 };
    } else {
      s.counts = { ...s.counts };
    }
    s.counts[kind] = (s.counts[kind] || 0) + 1;
    s.lastAt = now;
    return s;
  }

  function pacingPause(state, now, reason) {
    return { ...emptyPacing(), ...(state || {}), pausedUntil: now + PACING.pauseMs, pauseReason: reason };
  }

  const CHALLENGE_MARKERS = [
    "datadome",
    "captcha-delivery",
    "geo.captcha",
    "cf-chl",
    "challenge-platform",
    "just a moment",
    "attention required",
    "please enable js",
    "verify you are human",
  ];

  /** A refusal (403/429) or an anti-bot page: never retried around, the reads pause instead. */
  function isRefusal(status, body) {
    if (status === 403 || status === 429) return true;
    if (status !== 200 && status !== 503) return false;
    const head = String(body || "").slice(0, 20000).toLowerCase();
    return CHALLENGE_MARKERS.some((m) => head.includes(m));
  }

  // ------------------------------------------------------------------ live ranking
  /** Filters of the live panel: budget (total cost), minimum net margin, brands, sizes, fakes. */
  function passesFilters(ev, f) {
    if (!ev || ev.status !== "active") return false;
    if (f.budget && (ev.total_cost ?? ev.price) > f.budget) return false;
    const brands = splitList(f.brands);
    if (brands.length && !brands.some((b) => String(ev.brand || ev.title || "").toLowerCase().includes(b))) return false;
    const sizes = splitList(f.sizes);
    if (sizes.length && !sizes.includes(String(ev.size || "").toLowerCase())) return false;
    if (f.excludeFakeRisk && (ev.fake_risk === "medium" || ev.fake_risk === "high")) return false;
    if (ev.flip_score === null || ev.flip_score === undefined) return true; // shown as "dati insufficienti"
    if (f.minMargin !== null && f.minMargin !== undefined && f.minMargin !== "" && (ev.net_margin ?? -Infinity) < Number(f.minMargin)) return false;
    return true;
  }

  const scored = (ev) => ev.flip_score !== null && ev.flip_score !== undefined;

  const hasRap = (ev) => ev.risk_adjusted_profit !== null && ev.risk_adjusted_profit !== undefined;

  /** Best first: risk-adjusted profit (margin x P(sale) x P(authentic)), then score and margin;
   * items without enough data never outrank scored ones. */
  function compare(a, b) {
    if (scored(a) !== scored(b)) return scored(a) ? -1 : 1;
    if (!scored(a)) return (b.seenAt || 0) - (a.seenAt || 0);
    if (hasRap(a) !== hasRap(b)) return hasRap(a) ? -1 : 1;
    if (hasRap(a) && b.risk_adjusted_profit !== a.risk_adjusted_profit) return b.risk_adjusted_profit - a.risk_adjusted_profit;
    return b.flip_score - a.flip_score || (b.net_margin ?? 0) - (a.net_margin ?? 0);
  }

  function rank(evals, filters, n = 5) {
    const list = evals.filter((e) => passesFilters(e, filters));
    list.sort(compare);
    return list.slice(0, n);
  }

  /** Worth an alert: a scored item above both thresholds. */
  function isHot(ev, opts) {
    return scored(ev) && ev.status === "active" && ev.flip_score >= opts.minScore && (ev.net_margin ?? -Infinity) >= opts.minMargin;
  }

  // ------------------------------------------------------------------ formatting
  function eur(v, sign = false) {
    if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
    const n = Number(v);
    const s = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR", maximumFractionDigits: Math.abs(n) >= 100 ? 0 : 2 }).format(Math.abs(n));
    return (n < 0 ? "−" : sign && n > 0 ? "+" : "") + s;
  }

  function csvCell(v) {
    const s = v === null || v === undefined ? "" : String(v);
    const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
    return /[";\n,]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  }

  /** Session ranking -> CSV (Excel-friendly: BOM, semicolons). */
  function rankingCsv(rows) {
    const head = ["posizione", "vinted_id", "titolo", "brand", "taglia", "prezzo", "costo_totale", "rivendita_stimata", "margine_netto", "profitto_corretto_rischio", "prob_vendita", "prob_autentico", "roi", "score", "confidenza", "motivo", "url", "visto_alle"];
    const lines = rows.map((r, i) =>
      [i + 1, r.vinted_id, r.title, r.brand, r.size, r.price, r.total_cost, r.resale_expected, r.net_margin, r.risk_adjusted_profit, r.sale_probability, r.authenticity_probability, r.roi, r.flip_score ?? "dati insufficienti", r.confidence, r.reason, r.url, r.seenAt ? new Date(r.seenAt).toISOString() : ""]
        .map(csvCell)
        .join(";"),
    );
    return "﻿" + [head.join(";"), ...lines].join("\r\n");
  }

  return {
    DEFAULT_OPTIONS,
    PACING,
    normalizeOptions,
    normalizeAppUrl,
    splitList,
    emptyQueue,
    backoffMs,
    queueAdd,
    queueTake,
    queueDone,
    queueFail,
    queueSize,
    queueNextAt,
    queuePrune,
    emptyPacing,
    pacingCheck,
    pacingRecord,
    pacingPause,
    isRefusal,
    passesFilters,
    rank,
    compare,
    isHot,
    eur,
    csvCell,
    rankingCsv,
  };
});
