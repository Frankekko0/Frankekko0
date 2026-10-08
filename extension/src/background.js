/*
 * FlipFinder for Vinted - service worker.
 *
 *  - Sync queue: captures from the content script are kept in chrome.storage.local, one entry
 *    per Vinted ID, sent in batches with retry and backoff; the toolbar badge shows the state.
 *  - FlipFinder API client: authenticates with the extension key the user pasted in the
 *    options (a FlipFinder credential). Vinted cookies or tokens are never read or sent.
 *  - Reads of other pages (deep analysis on command, optional slow automatic ones) go through
 *    one paced queue and are performed by a Vinted tab the user has open, without cookies.
 *  - The shared parser configuration is refreshed from FlipFinder.
 *  - Per-tab browsing sessions feed the live panel.
 */
"use strict";
importScripts("parser-config.js", "parse.js", "core.js", "quick.js");

const P = globalThis.FlipFinderParse;
const K = globalThis.FlipFinderCore;
const Q = globalThis.FlipFinderQuick;
const VERSION = chrome.runtime.getManifest().version;
const local = chrome.storage.local;
const sessionStore = chrome.storage.session;

// ------------------------------------------------------------------ small helpers
let chain = Promise.resolve();
/** Runs state read-modify-write sections one at a time. */
function exclusive(fn) {
  const run = chain.then(fn, fn);
  chain = run.catch(() => {});
  return run;
}

async function getOptions() {
  const { options } = await chrome.storage.sync.get("options");
  return K.normalizeOptions(options);
}

async function getKey() {
  const { apiKey } = await local.get("apiKey");
  return typeof apiKey === "string" && apiKey.startsWith("ff_ext_") ? apiKey : null;
}

async function loadQueue() {
  const { queue } = await local.get("queue");
  return queue && queue.cards && queue.items ? queue : K.emptyQueue();
}

async function logError(where, message) {
  const { errors = [] } = await local.get("errors");
  errors.unshift({ at: Date.now(), where, message: String(message).slice(0, 300) });
  await local.set({ errors: errors.slice(0, 30) });
  console.warn(`[FlipFinder] ${where}: ${message}`);
}

async function sendToTab(tabId, message) {
  if (typeof tabId !== "number") return null;
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch {
    return null; // tab closed or not a Vinted page anymore
  }
}

function sendToPanel(message) {
  chrome.runtime.sendMessage(message).catch(() => {}); // no panel open
}

// ------------------------------------------------------------------ sync state + badge
async function getSync() {
  const { sync } = await sessionStore.get("sync");
  return sync || { state: "idle", pending: 0, lastSyncAt: null, message: "" };
}

async function setSync(patch) {
  const next = { ...(await getSync()), ...patch };
  await sessionStore.set({ sync: next });
  paintBadge(next);
  sendToPanel({ type: "ff:sync", sync: next });
  return next;
}

function paintBadge(s) {
  let text = "";
  let color = "#2a78d6";
  let title = "FlipFinder for Vinted";
  if (s.state === "unpaired") {
    text = "off";
    color = "#85847e";
    title = "FlipFinder: associa l'estensione nelle opzioni";
  } else if (s.state === "offline" || s.state === "error") {
    text = "!";
    color = "#d97706";
    title = `FlipFinder: ${s.message || "sincronizzazione in attesa"}`;
  } else if (s.pending > 0) {
    text = String(Math.min(s.pending, 999));
    title = `FlipFinder: ${s.pending} in attesa di invio`;
  } else if (s.lastSyncAt) {
    title = "FlipFinder: tutto sincronizzato";
  }
  chrome.action.setBadgeText({ text }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color }).catch(() => {});
  chrome.action.setTitle({ title }).catch(() => {});
}

// ------------------------------------------------------------------ FlipFinder API
class ApiError extends Error {
  constructor(status, code, message, retryAfterMs = 0) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfterMs = retryAfterMs;
  }
}

/** "db;dur=12.3, analysis;dur=40, total;dur=55" -> { db: 12, analysis: 40, total: 55 }. */
function parseServerTiming(header) {
  const out = {};
  for (const part of String(header || "").split(",")) {
    const m = /^\s*([\w-]+)\s*;.*?\bdur=([\d.]+)/.exec(part);
    if (m) out[m[1]] = Math.round(Number(m[2]));
  }
  return out;
}

/**
 * A request to FlipFinder. `timing` (optional object) receives the request's duration as seen
 * here (ms, network + server) and the server's own Server-Timing (db, analysis, total).
 */
async function api(path, { method = "GET", body, appUrl, key, timing } = {}) {
  const opts = await getOptions();
  const base = appUrl || opts.appUrl;
  const token = key || (await getKey());
  if (!token) throw new ApiError(0, "unpaired", "Estensione non associata a FlipFinder.");
  const t0 = performance.now();
  let res;
  try {
    res = await fetch(`${base}/api/v1${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      credentials: "omit",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "offline", "FlipFinder non raggiungibile: controlla che sia avviato e l'indirizzo nelle opzioni.");
  }
  if (res.ok) {
    const data = res.status === 204 ? null : await res.json();
    if (timing) Object.assign(timing, { ms: Math.round(performance.now() - t0) }, parseServerTiming(res.headers.get("server-timing")));
    return data;
  }
  let err = {};
  try {
    err = (await res.json()).error || {};
  } catch {
    /* not JSON */
  }
  const retry = Number(res.headers.get("retry-after"));
  throw new ApiError(res.status, err.code || `http_${res.status}`, err.message || `FlipFinder ha risposto ${res.status}.`, Number.isFinite(retry) ? retry * 1000 : 0);
}

// ------------------------------------------------------------------ evaluations cache
// Server evaluations by Vinted ID, kept across browser restarts (12 h) so a page opened again
// shows them at once. Mirrored in memory: reading 3000 entries from storage on every page
// would cost more than the whole instant verdict.
const EVAL_CACHE_MAX = 3000;
const EVAL_TTL_MS = 12 * 3600 * 1000;
let evalMem = null;

async function evalStore() {
  if (!evalMem) {
    const { evalCache = {} } = await local.get("evalCache");
    evalMem = evalCache;
  }
  return evalMem;
}

const persistEvals = (() => {
  let t = 0;
  return () => {
    clearTimeout(t);
    t = setTimeout(() => local.set({ evalCache: evalMem }).catch(() => {}), 1500);
  };
})();

async function cacheEvaluations(evals) {
  if (!evals.length) return;
  const cache = await evalStore();
  const now = Date.now();
  for (const ev of evals) cache[ev.vinted_id] = { ...ev, cachedAt: now };
  const ids = Object.keys(cache);
  if (ids.length > EVAL_CACHE_MAX) {
    ids.sort((a, b) => cache[a].cachedAt - cache[b].cachedAt);
    for (const id of ids.slice(0, ids.length - EVAL_CACHE_MAX)) delete cache[id];
  }
  persistEvals();
}

async function cachedEvaluations(vids) {
  const cache = await evalStore();
  const fresh = Date.now() - EVAL_TTL_MS;
  return vids.map((v) => cache[v]).filter((e) => e && e.cachedAt >= fresh);
}

// ------------------------------------------------------------------ market summary (instant verdict)
// Downloaded here, read and used by the pages themselves (stored in chrome.storage.local).
let marketVersion = null;

async function updateMarketCache() {
  if (!(await getKey())) return;
  try {
    const raw = await api("/extension/market-cache");
    if (!Q.compileMarket(raw)) return; // refuse a summary that cannot be used
    if (!marketVersion) marketVersion = ((await local.get("marketCache")).marketCache || {}).version || null;
    if (marketVersion !== raw.version) {
      marketVersion = raw.version;
      await local.set({ marketCache: raw });
    }
  } catch (err) {
    await logError("mercato", `Riepilogo di mercato non aggiornato: ${err.message}`);
  }
}

// What a Vinted page needs at its very start (pairing flag - never the key -, options, parser
// configuration, market summary), mirrored in the memory-backed session storage: read there it
// never waits behind the large writes of the local storage (queue, evaluations cache), which
// can hold a page's first look at its cards for half a second.
async function publishBoot() {
  try {
    const [{ paired, parserConfig, marketCache }, { options }] = await Promise.all([local.get(["paired", "parserConfig", "marketCache"]), chrome.storage.sync.get("options")]);
    await sessionStore.set({ boot: { paired: Boolean(paired), parserConfig: parserConfig || null, marketCache: marketCache || null, options: options || null } });
  } catch (err) {
    await logError("avvio", err.message);
  }
}

// ------------------------------------------------------------------ browsing sessions (live panel)
// One session per tab and search: it resets when the search changes (unless locked).
function searchKey(pageUrl) {
  try {
    const u = new URL(pageUrl);
    for (const p of ["page", "time", "referrer", "search_id", "search_by_image_uuid"]) u.searchParams.delete(p);
    u.searchParams.sort();
    return u.hostname + u.pathname + "?" + u.searchParams.toString();
  } catch {
    return "";
  }
}

function emptySession(key, pageType) {
  return { key, pageType, startedAt: Date.now(), locked: false, seen: 0, saved: 0, bestMargin: null, alerted: [], deepAsked: [], evals: {} };
}

// Kept in memory and saved shortly after each change: rewriting every session (up to 1500
// evaluations per tab) on each card or page view kept the service worker busy while a page
// waited for its instant verdict.
let sessionsMem = null;

async function loadSessions() {
  if (!sessionsMem) {
    const { sessions = {} } = await sessionStore.get("sessions");
    sessionsMem = sessions;
  }
  return sessionsMem;
}

const persistSessions = (() => {
  let t = 0;
  return () => {
    clearTimeout(t);
    t = setTimeout(() => sessionStore.set({ sessions: sessionsMem }).catch(() => {}), 1000);
  };
})();

async function withSession(tabId, fn) {
  return exclusive(async () => {
    const sessions = await loadSessions();
    const s = sessions[tabId];
    const out = await fn(s, sessions);
    persistSessions();
    return out;
  });
}

async function touchSession(tabId, pageUrl, pageType, seenCount) {
  if (typeof tabId !== "number") return;
  await withSession(tabId, (s, sessions) => {
    const key = pageType === "item" ? (s && s.key) || searchKey(pageUrl) : searchKey(pageUrl);
    let cur = s;
    if (!cur || (cur.key !== key && !cur.locked && pageType !== "item")) {
      cur = emptySession(key, pageType);
      sessions[tabId] = cur;
      sendToPanel({ type: "ff:session-reset", tabId });
    }
    cur.seen += seenCount || 0;
  });
}

async function addToSession(tabId, evals, fresh) {
  if (typeof tabId !== "number" || !evals.length) return [];
  const opts = await getOptions();
  return withSession(tabId, (s) => {
    if (!s) return [];
    const hot = [];
    const now = Date.now();
    for (const ev of evals) {
      const prev = s.evals[ev.vinted_id];
      s.evals[ev.vinted_id] = { ...ev, seenAt: prev ? prev.seenAt : now, saved: Boolean((prev && prev.saved) || fresh) };
      if (ev.net_margin !== null && ev.net_margin !== undefined && ev.flip_score !== null) {
        s.bestMargin = s.bestMargin === null ? ev.net_margin : Math.max(s.bestMargin, ev.net_margin);
      }
      if (K.isHot(ev, opts) && K.passesFilters(ev, opts) && !s.alerted.includes(ev.vinted_id)) {
        s.alerted.push(ev.vinted_id);
        hot.push(ev);
      }
    }
    // Items of this session stored in FlipFinder (each counted once, however often re-analysed).
    s.saved = Object.values(s.evals).filter((e) => e.saved).length;
    // Bound the session (a very long scroll): keep the most recent 1500 items.
    const ids = Object.keys(s.evals);
    if (ids.length > 1500) {
      ids.sort((a, b) => s.evals[a].seenAt - s.evals[b].seenAt);
      for (const id of ids.slice(0, ids.length - 1500)) delete s.evals[id];
    }
    return hot;
  });
}

/** Evaluations arrived: cache, badges in the tab, live panel, alerts, automatic deep reads. */
// Favourite states read on a listing page before FlipFinder had recorded that listing.
const favouriteSeenLater = new Map();

async function onEvaluations(evals, { tabIds = [], fresh = true, deep = null, timing = null } = {}) {
  await cacheEvaluations(evals);
  await scanNotify(evals).catch((err) => logError("scanner", err.message));
  for (const ev of evals) {
    if (!favouriteSeenLater.has(ev.vinted_id)) continue;
    const value = favouriteSeenLater.get(ev.vinted_id);
    favouriteSeenLater.delete(ev.vinted_id);
    recordVinted(ev.vinted_id, { kind: "favourite", value, source: "page" });
  }
  const opts = await getOptions();
  for (const tabId of new Set(tabIds.filter((t) => typeof t === "number"))) {
    await sendToTab(tabId, { type: "ff:evals", evals, deep, timing });
    const hot = await addToSession(tabId, evals, fresh);
    sendToPanel({ type: "ff:session-update", tabId });
    if (hot.length) {
      sendToPanel({ type: "ff:hot", tabId, items: hot });
      if (opts.alertsVisual) await sendToTab(tabId, { type: "ff:hot", vids: hot.map((h) => h.vinted_id) });
    }
    if (opts.autoDeep) await planAutoDeep(tabId, evals, opts);
  }
}

// ------------------------------------------------------------------ sync queue
let flushTimer = null;

function scheduleFlush(delayMs) {
  clearTimeout(flushTimer);
  if (delayMs <= 25000) flushTimer = setTimeout(() => flush().catch((e) => logError("flush", e.message)), Math.max(0, delayMs));
  else chrome.alarms.create("ff-flush", { when: Date.now() + delayMs });
}

async function enqueue(kind, entries, delayMs) {
  const size = await exclusive(async () => {
    const q = await loadQueue();
    K.queueAdd(q, kind, entries, Date.now());
    await local.set({ queue: q });
    return K.queueSize(q);
  });
  const key = await getKey();
  await setSync(key ? { pending: size } : { state: "unpaired", pending: size });
  if (key) scheduleFlush(delayMs);
}

async function sendBatch(kind, batch) {
  const timing = { ep: kind === "items" ? "item" : "cards", n: batch.entries.length };
  if (kind === "items") {
    const e = batch.entries[0];
    const res = await api("/capture/item", {
      method: "POST",
      body: { item: e.payload, mode: e.mode || "extension_item", track: e.track ?? null, extension_version: VERSION },
      timing,
    });
    return { evaluations: res.evaluation ? [res.evaluation] : [], analysis: res.analysis, timing };
  }
  const res = await api("/capture/cards", {
    method: "POST",
    body: { page_type: batch.pageType.split("#")[0], page_url: batch.entries[0].pageUrl || "", items: batch.entries.map((e) => e.payload), extension_version: VERSION },
    timing,
  });
  return { ...res, timing };
}

// Cards of a page go best first (the page sends them ordered by its instant verdict, visible
// ones first): a small first request so the first full verdicts show at once, then larger ones.
const FIRST_CHUNK = 12;
const NEXT_CHUNK = 48;

let flushing = false;
let flushAgain = false;

async function flush() {
  if (flushing) {
    flushAgain = true;
    return;
  }
  flushing = true;
  try {
    do {
      flushAgain = false;
      await flushOnce();
    } while (flushAgain);
  } finally {
    flushing = false;
  }
}

async function flushOnce() {
  const key = await getKey();
  if (!key) {
    const q = await loadQueue();
    await setSync({ state: "unpaired", pending: K.queueSize(q) });
    return;
  }
  for (let round = 0; round < 25; round += 1) {
    const q = await loadQueue();
    K.queuePrune(q, Date.now());
    const items = K.queueTake(q, "items", Date.now(), 1);
    const kind = items ? "items" : "cards";
    const batch = items || K.queueTake(q, "cards", Date.now(), NEXT_CHUNK);
    if (!batch) break;
    if (kind === "cards" && batch.entries[0].first) batch.entries = batch.entries.filter((e) => e.first).slice(0, FIRST_CHUNK);
    try {
      const res = await sendBatch(kind, batch);
      await exclusive(async () => {
        const cur = await loadQueue();
        K.queueDone(cur, kind, batch.entries);
        await local.set({ queue: cur });
      });
      const after = await loadQueue();
      await setSync({ state: "ok", lastSyncAt: Date.now(), pending: K.queueSize(after), message: "" });
      const e = batch.entries[0];
      await onEvaluations(res.evaluations || [], {
        tabIds: batch.entries.map((x) => x.tabId),
        deep: kind === "items" ? { vid: e.vid, mode: e.mode || "extension_item", analysis: res.analysis || null } : null,
        timing: res.timing || null,
      });
    } catch (err) {
      const status = err.status || 0;
      if (status === 401) {
        await setSync({ state: "unpaired", message: err.message });
        await logError("sync", err.message);
        return;
      }
      if ((status === 400 || status === 422) && batch.entries.length > 1) {
        // One invalid record must not block the others: send them one by one.
        await exclusive(async () => {
          const cur = await loadQueue();
          for (const [i, e] of batch.entries.entries()) if (cur[kind][e.vid]) cur[kind][e.vid].pageType = `${batch.pageType}#${i}`;
          await local.set({ queue: cur });
        });
        continue;
      }
      if (status === 400 || status === 422) {
        await exclusive(async () => {
          const cur = await loadQueue();
          K.queueDone(cur, kind, batch.entries);
          await local.set({ queue: cur });
        });
        await logError("sync", `Scartato ${batch.entries[0].vid}: ${err.message}`);
        continue;
      }
      await exclusive(async () => {
        const cur = await loadQueue();
        K.queueFail(cur, kind, batch.entries, Date.now(), err.retryAfterMs || 0);
        await local.set({ queue: cur });
      });
      const q2 = await loadQueue();
      await setSync({ state: status === 0 ? "offline" : "error", pending: K.queueSize(q2), message: err.message });
      await logError("sync", err.message);
      break;
    }
  }
  const q = await loadQueue();
  const next = K.queueNextAt(q);
  if (next !== null) scheduleFlush(Math.max(1000, next - Date.now()));
}

// ------------------------------------------------------------------ reads of other pages
const MAX_DEEP_JOBS = 30;

async function loadDeep() {
  const { deep } = await local.get("deep");
  return deep && Array.isArray(deep.jobs) ? deep : { jobs: [], pacing: K.emptyPacing() };
}

async function deepEnqueue(job) {
  const res = await exclusive(async () => {
    const d = await loadDeep();
    const existing = d.jobs.find((j) => j.vid === job.vid);
    if (existing) {
      if (job.kind === "manual") Object.assign(existing, { kind: "manual", tabId: job.tabId });
    } else {
      d.jobs.push({ ...job, addedAt: Date.now() });
    }
    // Manual requests first, then the oldest; beyond the cap the newest automatic ones wait out.
    d.jobs.sort((a, b) => (b.kind === "manual") - (a.kind === "manual") || a.addedAt - b.addedAt);
    while (d.jobs.length > MAX_DEEP_JOBS && d.jobs[d.jobs.length - 1].kind !== "manual") d.jobs.pop();
    await local.set({ deep: d });
    return { position: d.jobs.findIndex((j) => j.vid === job.vid) + 1, pacing: d.pacing };
  });
  scheduleDeep(0);
  return res;
}

let deepTimer = null;
function scheduleDeep(delayMs) {
  clearTimeout(deepTimer);
  if (delayMs <= 25000) deepTimer = setTimeout(() => deepPump().catch((e) => logError("deep", e.message)), Math.max(0, delayMs));
  else chrome.alarms.create("ff-deep", { when: Date.now() + delayMs });
}

async function registeredTabs() {
  const { tabs = {} } = await sessionStore.get("tabs");
  return tabs;
}

/** A Vinted tab the user has open, to read the page from (the preferred one when still open). */
async function pickTab(preferred) {
  const tabs = await registeredTabs();
  const ids = Object.keys(tabs)
    .map(Number)
    .sort((a, b) => (tabs[b].at || 0) - (tabs[a].at || 0));
  if (typeof preferred === "number") ids.unshift(preferred);
  for (const id of ids) {
    try {
      await chrome.tabs.get(id);
      return id;
    } catch {
      /* closed */
    }
  }
  return null;
}

let pumping = false;

async function deepPump() {
  if (pumping) return;
  pumping = true;
  try {
    await deepStep();
  } finally {
    pumping = false;
  }
}

async function deepStep() {
  const opts = await getOptions();
  const step = await exclusive(async () => {
    const d = await loadDeep();
    if (!d.jobs.length) return null;
    const job = d.jobs[0];
    const check = K.pacingCheck(d.pacing, job.kind, Date.now(), opts);
    if (!check.ok) return { wait: check.waitMs, reason: check.reason, job };
    d.jobs.shift();
    d.pacing = K.pacingRecord(d.pacing, job.kind, Date.now());
    await local.set({ deep: d });
    return { job };
  });
  if (!step) return;
  if (step.wait) {
    if (step.reason === "paused" && step.job.kind === "manual") {
      await sendToTab(step.job.tabId, { type: "ff:deep-status", vid: step.job.vid, state: "paused", waitMs: step.wait });
    }
    scheduleDeep(step.wait);
    return;
  }
  const job = step.job;
  const tabId = await pickTab(job.tabId);
  if (tabId === null) {
    // No Vinted tab open: automatic reads wait for one (they only run while you browse).
    if (job.kind !== "manual") await deepEnqueue({ ...job, tabId: null });
    return;
  }
  await sendToTab(job.tabId ?? tabId, { type: "ff:deep-status", vid: job.vid, state: "reading" });
  const r = (await sendToTab(tabId, { type: "ff:read-page", url: job.url, vid: job.vid })) || { outcome: "error", message: "Scheda non disponibile." };
  await handleRead(job, tabId, r);
  const d = await loadDeep();
  if (d.jobs.length) scheduleDeep(1000);
}

async function handleRead(job, tabId, r) {
  const notifyTab = job.tabId ?? tabId;
  if (r.outcome === "ok" && r.payload) {
    const mode = job.kind === "refresh" ? "extension_refresh" : "extension_deep";
    // Deep analyses on command are tracked (explicit interest); automatic ones are not.
    const track = job.kind === "manual" ? null : false;
    await enqueue("items", [{ vid: job.vid, payload: r.payload, mode, track, tabId: notifyTab, pageType: "item" }], 0);
    return;
  }
  if (r.outcome === "blocked") {
    await exclusive(async () => {
      const d = await loadDeep();
      d.pacing = K.pacingPause(d.pacing, Date.now(), r.message || "rifiuto di Vinted");
      await local.set({ deep: d });
    });
    await logError("lettura", `Vinted ha rifiutato la lettura (${r.status || "?"}): letture sospese per 6 ore.`);
    sendToPanel({ type: "ff:reads-paused" });
  }
  if (job.kind === "refresh" || r.outcome === "not_found") {
    try {
      await api("/capture/refresh-result", {
        method: "POST",
        body: { vinted_id: job.vid, outcome: r.outcome === "ok" ? "error" : r.outcome, http_status: r.status || null, message: r.message || null },
      });
    } catch (err) {
      if (err.status !== 404) await logError("lettura", err.message);
    }
  }
  if (r.outcome !== "ok") await logError("lettura", `${job.vid}: ${r.message || r.outcome}`);
  await sendToTab(notifyTab, { type: "ff:deep-status", vid: job.vid, state: r.outcome, message: r.message || "" });
}

/** Slow automatic deep reads: the best few quick evaluations of what you are scrolling. */
async function planAutoDeep(tabId, evals, opts) {
  const candidates = evals.filter(
    (e) => e.analysis_depth === "quick" && e.status === "active" && K.isHot(e, opts) && K.passesFilters(e, opts),
  );
  if (!candidates.length) return;
  const picked = await withSession(tabId, (s) => {
    if (!s) return [];
    const out = [];
    for (const e of candidates) {
      if (s.deepAsked.includes(e.vinted_id) || s.deepAsked.length >= 6) continue;
      s.deepAsked.push(e.vinted_id);
      out.push(e);
    }
    return out;
  });
  for (const e of picked.slice(0, 3)) await deepEnqueue({ vid: e.vinted_id, url: e.url, kind: "auto", tabId });
}

/** Optional slow status checks of tracked items (only while a Vinted tab is open). */
async function pollRefreshQueue() {
  const opts = await getOptions();
  if (!opts.slowRefresh || !(await getKey()) || (await pickTab(null)) === null) return;
  const d = await loadDeep();
  if (d.jobs.some((j) => j.kind === "refresh") || d.pacing.pausedUntil > Date.now()) return;
  try {
    const res = await api("/capture/refresh-queue?limit=2");
    for (const it of res.items || []) await deepEnqueue({ vid: it.vinted_id, url: it.url, kind: "refresh", tabId: null });
  } catch (err) {
    await logError("aggiornamento", err.message);
  }
}

// ------------------------------------------------------------------ automatic scanner (opt-in)
// Re-reads page 1 (newest first) of the Vinted searches you saved, while the browser is open.
// Cautious by construction: one read at a time, at least a minute apart, 30 an hour and 300 a
// day, without cookies, and a 6-hour pause at the first refusal or anti-bot page (for every
// automatic read). New items go through the normal capture queue; the ones that are new AND above
// your thresholds raise a notification. Nothing is bought, offered or sent to a seller.
const SCAN_STATUS_MAX = 160;

async function loadScanner() {
  const { scanner } = await local.get("scanner");
  const ok = scanner && Array.isArray(scanner.searches);
  return { searches: ok ? scanner.searches : [], pacing: ok && scanner.pacing ? scanner.pacing : K.emptyScanPacing(), watch: ok && scanner.watch ? scanner.watch : {} };
}

/** The parser configuration in use as plain data (the offscreen reader compiles it itself). */
async function rawParserConfig() {
  const { parserConfig } = await local.get("parserConfig");
  return parserConfig && parserConfig.version ? parserConfig : globalThis.FF_PARSER_CONFIG;
}

async function ensureOffscreen() {
  if (!chrome.offscreen) throw new Error("Questa versione del browser non supporta lo scanner (serve Chrome 120 o successivo).");
  const open = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (open.length) return;
  await chrome.offscreen.createDocument({
    url: "src/scan.html",
    reasons: ["DOM_PARSER"],
    justification: "Legge l'HTML delle ricerche salvate di Vinted per lo scanner automatico (analisi della pagina, senza eseguirla).",
  });
}

async function readSearchPage(search) {
  await ensureOffscreen();
  try {
    const config = await rawParserConfig();
    const timeout = new Promise((resolve) => setTimeout(() => resolve({ outcome: "error", message: "Lettura troppo lenta." }), 40000));
    return await Promise.race([chrome.runtime.sendMessage({ type: "ff:scan-read", url: search.url, config }), timeout]).then((r) => r || { outcome: "error", message: "Nessuna risposta dal lettore." });
  } finally {
    chrome.offscreen.closeDocument().catch(() => {});
  }
}

let scanning = false;
async function scanTick() {
  if (scanning) return;
  scanning = true;
  try {
    await scanStep();
  } catch (err) {
    await logError("scanner", err.message);
  } finally {
    scanning = false;
  }
}

async function scanStep() {
  const opts = await getOptions();
  if (!opts.scanEnabled || !(await getKey())) return;
  const now = Date.now();
  if ((await loadDeep()).pacing.pausedUntil > now) return; // a refusal anywhere pauses every automatic read
  const sc = await loadScanner();
  if (K.scanPacingCheck(sc.pacing, now).ok === false) return;
  const due = K.scanPickDue(sc.searches, now, opts.scanIntervalMin);
  if (!due.length) return;

  // The first due search whose site you allowed. Without the permission nothing is read.
  let search = null;
  for (const s of due) {
    const origin = new URL(s.url).origin;
    if (await chrome.permissions.contains({ origins: [`${origin}/*`] })) {
      search = s;
      break;
    }
    await scanMark(s.id, { lastError: `Serve il permesso di leggere ${new URL(s.url).hostname}: aggiungila di nuovo dalle opzioni.` });
  }
  if (!search) return;

  // Reserve the slot before reading: a crash in the middle never makes the same read repeat at once.
  await exclusive(async () => {
    const cur = await loadScanner();
    cur.pacing = K.scanPacingRecord(cur.pacing, now);
    const s = cur.searches.find((x) => x.id === search.id);
    if (s) s.lastScanAt = now;
    await local.set({ scanner: cur });
  });

  const result = await readSearchPage(search);
  await scanResult(search, result, opts);
}

/** Updates one saved search under the lock. */
function scanMark(id, patch) {
  return exclusive(async () => {
    const sc = await loadScanner();
    const s = sc.searches.find((x) => x.id === id);
    if (!s) return null;
    Object.assign(s, patch);
    await local.set({ scanner: sc });
    return s;
  });
}

async function scanResult(search, r, opts) {
  const now = Date.now();
  if (r.outcome === "blocked") {
    await exclusive(async () => {
      const sc = await loadScanner();
      sc.pacing = K.scanPacingPause(sc.pacing, now, r.message || "rifiuto di Vinted");
      const s = sc.searches.find((x) => x.id === search.id);
      if (s) s.lastError = r.message || "Vinted ha rifiutato la lettura.";
      await local.set({ scanner: sc });
      const d = await loadDeep();
      d.pacing = K.pacingPause(d.pacing, now, "scanner: rifiuto di Vinted");
      await local.set({ deep: d });
    });
    await logError("scanner", `Vinted ha rifiutato la lettura (${r.status || "?"}): tutte le letture automatiche sono sospese per 6 ore.`);
    sendToPanel({ type: "ff:reads-paused" });
    if (opts.scanNotify) notify("ff-scan-paused", "Scanner in pausa per 6 ore", "Vinted ha rifiutato una lettura. FlipFinder si ferma e non ritenta: riparte da solo dopo la pausa.");
    return;
  }
  if (r.outcome !== "ok") {
    const updated = await exclusive(async () => {
      const sc = await loadScanner();
      const s = sc.searches.find((x) => x.id === search.id);
      if (!s) return null;
      s.failures = (s.failures || 0) + 1;
      s.lastError = r.message || "Lettura non riuscita.";
      if (s.failures >= K.SCAN.maxFailures) {
        s.enabled = false;
        s.lastError = `${s.lastError} Ricerca sospesa dopo ${K.SCAN.maxFailures} tentativi falliti: riattivala dalle opzioni.`.slice(0, SCAN_STATUS_MAX + 60);
      }
      await local.set({ scanner: sc });
      return s;
    });
    await logError("scanner", `${search.name}: ${(updated && updated.lastError) || r.message || r.outcome}`);
    return;
  }

  // Read: what is new against what this search showed before. The first read of a search only
  // builds the baseline (everything on it counts as already seen, no notification).
  const vids = r.cards.map((c) => c.vid);
  const fresh = await exclusive(async () => {
    const sc = await loadScanner();
    const s = sc.searches.find((x) => x.id === search.id);
    if (!s) return [];
    const diff = K.scanDiff(s.seen, vids);
    const wasPrimed = Boolean(s.primed);
    s.seen = diff.seen;
    s.primed = true;
    s.failures = 0;
    s.lastError = "";
    s.lastOkAt = now;
    s.lastCount = vids.length;
    s.lastNew = wasPrimed ? diff.fresh.length : 0;
    s.totalNew = (s.totalNew || 0) + s.lastNew;
    if (wasPrimed && opts.scanNotify) {
      for (const vid of diff.fresh) sc.watch[vid] = { name: s.name, at: now };
    }
    for (const [vid, w] of Object.entries(sc.watch)) if (now - w.at > K.SCAN.watchTtlMs) delete sc.watch[vid];
    await local.set({ scanner: sc });
    return wasPrimed ? diff.fresh : [];
  });
  const entries = r.cards.map((c) => ({ vid: c.vid, payload: c.payload, pageType: "scan", pageUrl: search.url.slice(0, 1000), tabId: null }));
  await enqueue("cards", entries, 0);
  if (fresh.length) sendToPanel({ type: "ff:scan-new", name: search.name, count: fresh.length });
}

// ------------------------------------------------------------------ scanner notifications
// notification id -> the Vinted item page it opens (session storage: survives the worker being stopped)
async function scanLinksUpdate(fn) {
  return exclusive(async () => {
    const { scanLinks = {} } = await sessionStore.get("scanLinks");
    const out = fn(scanLinks);
    await sessionStore.set({ scanLinks });
    return out;
  });
}

function notify(id, title, message) {
  chrome.notifications.create(id, { type: "basic", iconUrl: chrome.runtime.getURL("icons/icon-128.png"), title: title.slice(0, 80), message: message.slice(0, 240), priority: 1 }).catch(() => {});
}

/** Evaluations arrived: the items that were new in a scan and pass your thresholds raise a notification. */
async function scanNotify(evals) {
  if (!evals.length) return;
  const opts = await getOptions();
  const hits = await exclusive(async () => {
    const sc = await loadScanner();
    const watched = evals.filter((e) => sc.watch[e.vinted_id]);
    if (!watched.length) return [];
    const picked = opts.scanNotify ? K.scanHotPick(watched, sc.watch, opts) : [];
    const out = picked.map((e) => ({ ev: e, name: sc.watch[e.vinted_id].name }));
    for (const e of watched) delete sc.watch[e.vinted_id]; // answered, hot or not
    await local.set({ scanner: sc });
    return out;
  });
  for (const { ev, name } of hits) {
    const id = `ff-scan-${ev.vinted_id}`;
    await scanLinksUpdate((links) => {
      links[id] = ev.url;
      const ids = Object.keys(links);
      for (const old of ids.slice(0, Math.max(0, ids.length - 50))) delete links[old];
    });
    const profit = ev.net_margin !== null && ev.net_margin !== undefined ? ` · margine ${K.eur(ev.net_margin, true)}` : "";
    notify(id, `Nuova occasione${ev.brand ? ` · ${ev.brand}` : ""}`, `${ev.title}\n${K.eur(ev.price)}${profit} · score ${ev.flip_score} · ${name}`);
  }
}

chrome.notifications?.onClicked.addListener(async (id) => {
  const url = await scanLinksUpdate((links) => {
    const found = links[id];
    delete links[id];
    return found;
  });
  chrome.notifications.clear(id).catch(() => {});
  // Opens the item page on Vinted; buying, offering and messaging stay your decision.
  if (url && /^https:\/\/www\.vinted\.[a-z.]+\/items\//.test(url)) chrome.tabs.create({ url });
});

/** The scanner as shown in the options and the popup (no private data: only the search list and counters). */
async function scanSummary() {
  const [sc, opts, key] = await Promise.all([loadScanner(), getOptions(), getKey()]);
  const now = Date.now();
  const active = sc.searches.filter((s) => s.enabled !== false);
  const permitted = {};
  for (const s of sc.searches) {
    const origin = new URL(s.url).origin;
    if (!(origin in permitted)) permitted[origin] = await chrome.permissions.contains({ origins: [`${origin}/*`] });
  }
  const effective = K.scanIntervalMin(active.length, opts.scanIntervalMin);
  return {
    enabled: opts.scanEnabled,
    paired: Boolean(key),
    intervalMin: opts.scanIntervalMin,
    effectiveIntervalMin: effective,
    requestsPerHour: active.length ? Math.round((active.length * 60) / effective) : 0,
    pausedUntil: sc.pacing.pausedUntil > now ? sc.pacing.pausedUntil : 0,
    pauseReason: sc.pacing.pausedUntil > now ? sc.pacing.pauseReason : "",
    usedToday: now - sc.pacing.dayStart < 86400000 ? sc.pacing.dayCount : 0,
    limits: { perHour: K.SCAN.perHour, perDay: K.SCAN.perDay, maxSearches: K.SCAN.maxSearches, gapSeconds: K.SCAN.gapMs / 1000 },
    searches: sc.searches.map((s) => ({
      id: s.id,
      name: s.name,
      url: s.url,
      enabled: s.enabled !== false,
      allowed: permitted[new URL(s.url).origin] === true,
      lastScanAt: s.lastScanAt || 0,
      lastOkAt: s.lastOkAt || 0,
      lastCount: s.lastCount ?? null,
      lastNew: s.lastNew ?? 0,
      totalNew: s.totalNew || 0,
      lastError: s.lastError || "",
    })),
  };
}

// ------------------------------------------------------------------ shared parser configuration
async function updateParserConfig() {
  if (!(await getKey())) return;
  try {
    const raw = await api("/extension/parser-config");
    P.compileConfig(raw); // refuse a file that doesn't compile
    const { parserConfig } = await local.get("parserConfig");
    if (!parserConfig || parserConfig.version !== raw.version) await local.set({ parserConfig: raw });
  } catch (err) {
    await logError("config", `Configurazione del parser non aggiornata: ${err.message}`);
  }
}

// The parser configuration in use: FlipFinder's latest copy, else the one shipped with the extension.
let compiledConf = null;
async function parserConf() {
  const { parserConfig } = await local.get("parserConfig");
  const raw = parserConfig && parserConfig.version ? parserConfig : globalThis.FF_PARSER_CONFIG;
  if (!compiledConf || compiledConf.version !== raw.version) {
    try {
      compiledConf = { version: raw.version, C: P.compileConfig(raw) };
    } catch {
      compiledConf = { version: raw.version, C: P.compileConfig(globalThis.FF_PARSER_CONFIG) };
    }
  }
  return compiledConf.C;
}

// ------------------------------------------------------------------ actions on Vinted (favourite, buy)
// Only on your click in FlipFinder (or in the panel): the item page opens in your browser, in your
// Vinted session, and the content script clicks Vinted's own button once. Nothing is paid here:
// the checkout waits for your confirmation. FlipFinder only receives what happened.
const BUY_TTL_MS = 3 * 3600 * 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// vid -> tabId of the item page opened for a purchase. In session storage: the service worker
// may be stopped between the check and the checkout, the tab must still be found.
async function buyTabOf(vid) {
  const { buyTabs = {} } = await sessionStore.get("buyTabs");
  const tabId = buyTabs[String(vid)];
  if (typeof tabId !== "number") return null;
  return chrome.tabs.get(tabId).then(() => tabId, () => null);
}

function setBuyTab(vid, tabId) {
  // Read and written in one step: two purchases started at once never overwrite each other.
  return exclusive(async () => {
    const { buyTabs = {} } = await sessionStore.get("buyTabs");
    if (tabId === null) delete buyTabs[String(vid)];
    else buyTabs[String(vid)] = tabId;
    await sessionStore.set({ buyTabs });
  });
}

async function appOrigin() {
  try {
    return new URL((await getOptions()).appUrl).origin;
  } catch {
    return null;
  }
}

/** Requests come from FlipFinder's own pages (through the bridge) or from this extension's pages. */
async function trustedSender(sender) {
  if (sender.id !== chrome.runtime.id) return false;
  if (String(sender.url || "").startsWith(chrome.runtime.getURL(""))) return true; // panel, popup, options
  const origin = sender.origin || (sender.url ? new URL(sender.url).origin : null);
  return Boolean(origin) && origin === (await appOrigin());
}

async function itemUrlOf(url, vid) {
  const C = await parserConf();
  try {
    const u = new URL(String(url));
    if (!P.isVintedUrl(u.href, C) || P.itemId(u.pathname, C) !== String(vid)) return null;
    return u.origin + u.pathname;
  } catch {
    return null;
  }
}

// Checkouts being opened: tab id -> resolve(true when the tab shows Vinted's checkout).
// Listening starts before the click; the time limit counts from the click.
const checkoutWaits = new Map();
function watchCheckout(tabId) {
  let resolveSeen;
  const seen = new Promise((resolve) => {
    resolveSeen = resolve;
  });
  const settle = (v) => {
    if (checkoutWaits.get(tabId) === onSeen) checkoutWaits.delete(tabId);
    resolveSeen(v);
  };
  const onSeen = () => settle(true);
  checkoutWaits.set(tabId, onSeen);
  return {
    wait: (timeoutMs = 15000) => Promise.race([seen, sleep(timeoutMs).then(() => false)]).then((v) => (settle(v), v)),
    cancel: () => settle(false),
  };
}

/**
 * Asks the content script of a tab, waiting for it to be there: a tab just opened (even in the
 * background, even on a slow page) answers once its document exists. Time-bound, not count-bound.
 */
async function askTab(tabId, message, timeoutMs = 30000) {
  const until = Date.now() + timeoutMs;
  for (;;) {
    try {
      const r = await chrome.tabs.sendMessage(tabId, message);
      if (r) return r;
    } catch (err) {
      if (/No tab with id/i.test(String(err && err.message))) return { ok: false, code: "no_page", message: "La scheda di Vinted è stata chiusa." };
      /* content script not there yet */
    }
    if (Date.now() > until) return { ok: false, code: "no_page", message: "La pagina di Vinted non risponde: riprova." };
    await sleep(250);
  }
}

/**
 * The item page in a tab for an action: the tab already opened for that purchase (sent again to
 * the listing, so the page is fresh) or a new one. `after`: answers only from the page loaded now.
 */
async function itemTab(vid, url, { active, reuse }) {
  const after = Date.now();
  const known = reuse ? await buyTabOf(vid) : null;
  if (known !== null) {
    const ok = await chrome.tabs.update(known, { url, ...(active ? { active: true } : {}) }).then(() => true, () => false);
    if (ok) return { tabId: known, created: false, after };
  }
  const tab = await chrome.tabs.create({ url, active });
  return { tabId: tab.id, created: true, after };
}

async function recordVinted(vid, body) {
  try {
    return await api("/capture/vinted-actions", { method: "POST", body: { vinted_id: String(vid), ...body } });
  } catch (err) {
    await logError("vinted", err.message);
    return null;
  }
}

// Tabs opened to change a favourite: their own page reading must not race the click's result.
const favouriteTabs = new Set();

async function vintedFavourite(msg) {
  const url = await itemUrlOf(msg.url, msg.vid);
  if (!url) return { ok: false, code: "bad_url", message: "Annuncio non valido." };
  const tab = await chrome.tabs.create({ url, active: false });
  favouriteTabs.add(tab.id);
  try {
    const want = Boolean(msg.want);
    let r = await askTab(tab.id, { type: "ff:vinted-act", action: "favourite", vid: String(msg.vid), want });
    if (r.code === "verify" && r.clicked) {
      // Clicked once; the page didn't show the result: give Vinted a moment, then read it again.
      await sleep(1500);
      const reloadedAt = Date.now();
      const reloaded = await chrome.tabs.reload(tab.id, { bypassCache: true }).then(() => true, () => false);
      let s = reloaded ? await askTab(tab.id, { type: "ff:vinted-act", action: "state", vid: String(msg.vid), after: reloadedAt }) : { ok: false };
      if (s.ok && typeof s.favourite === "boolean" && s.favourite !== want) {
        // Vinted didn't get the click (the page was not ready for it): one more, on this fresh
        // page, after reading the state again - never a click that could undo the first one.
        const again = await askTab(tab.id, { type: "ff:vinted-act", action: "favourite", vid: String(msg.vid), want, after: reloadedAt });
        if (again.ok) s = { ...again, favourite: want };
      }
      if (s.ok && s.favourite === want) r = { ...s, ok: true, changed: true };
      else {
        if (s.ok && typeof s.favourite === "boolean") await recordVinted(msg.vid, { kind: "favourite", value: s.favourite, source: "page" });
        return { ok: false, code: "not_confirmed", message: "Vinted non ha confermato il cambio: controlla l'annuncio su Vinted.", favourite: s.ok ? s.favourite : null };
      }
    }
    if (r.ok) await recordVinted(msg.vid, { kind: "favourite", value: Boolean(r.favourite), source: "click" });
    return r;
  } finally {
    favouriteTabs.delete(tab.id);
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function vintedBuyCheck(msg) {
  const url = await itemUrlOf(msg.url, msg.vid);
  if (!url) return { ok: false, code: "bad_url", message: "Annuncio non valido." };
  // In front: if you go ahead, the checkout continues in this tab.
  const { tabId, after } = await itemTab(String(msg.vid), url, { active: true, reuse: true });
  await setBuyTab(msg.vid, tabId);
  return askTab(tabId, { type: "ff:vinted-act", action: "state", vid: String(msg.vid), after });
}

/** One click on Acquista in the item's tab, then the checkout in front (confirmed by you on Vinted). */
async function clickBuy(vid, tabId, extra) {
  const checkout = watchCheckout(tabId); // listening before the click
  const r = await askTab(tabId, { type: "ff:vinted-act", action: "buy", vid, ...extra });
  if (!r.ok) {
    checkout.cancel();
    return r;
  }
  // A purchase completed later in this tab belongs to this listing.
  const { pendingBuys = {} } = await local.get("pendingBuys");
  pendingBuys[vid] = { at: Date.now(), price: r.price, tabId };
  await local.set({ pendingBuys });
  await chrome.tabs.update(tabId, { active: true }).catch(() => {});
  if (!(await checkout.wait(15000))) return { ok: false, code: "checkout_not_seen", message: "Ho premuto Acquista su Vinted ma il checkout non si è aperto: controlla la scheda di Vinted.", price: r.price };
  await recordVinted(vid, { kind: "checkout_opened", price: r.price, source: "click" });
  return r;
}

async function vintedBuyOpen(msg) {
  const vid = String(msg.vid);
  let tabId = await buyTabOf(vid);
  if (tabId === null) {
    const check = await vintedBuyCheck(msg);
    if (!check.ok) return check;
    tabId = await buyTabOf(vid);
    if (tabId === null) return { ok: false, code: "no_page", message: "La scheda di Vinted è stata chiusa." };
  }
  const expect = Number(msg.expect_price);
  return clickBuy(vid, tabId, expect > 0 ? { expect_price: expect } : {});
}

/**
 * Buy at the price you saw, in one click: the listing opens (in the background while it is read),
 * Acquista is clicked only if it is still on sale at exactly that price, then the checkout comes
 * to the front. Otherwise nothing is clicked and the reason comes back (price_changed with the
 * new price, sold, reserved, removed, signed_out...).
 */
async function vintedBuy(msg) {
  const vid = String(msg.vid);
  const url = await itemUrlOf(msg.url, vid);
  if (!url) return { ok: false, code: "bad_url", message: "Annuncio non valido." };
  const expect = Number(msg.expect_price);
  if (!(expect > 0)) return { ok: false, code: "bad_price", message: "Prezzo dell'analisi mancante." };
  const { tabId, created, after } = await itemTab(vid, url, { active: false, reuse: true });
  await setBuyTab(vid, tabId);
  const r = await clickBuy(vid, tabId, { expect_price: expect, after });
  if (!r.ok && r.code !== "price_changed" && r.code !== "checkout_not_seen" && created) {
    // Nothing to do on that page: the tab you didn't open yourself goes away. With a new price it
    // stays, ready for "open the checkout at the new price".
    await setBuyTab(vid, null);
    chrome.tabs.remove(tabId).catch(() => {});
  }
  return r;
}

/** The tab of a started checkout moved on to another listing: that checkout is abandoned. */
async function forgetBuyIfLeft(tabId, url) {
  const { pendingBuys = {} } = await local.get("pendingBuys");
  let vid = null;
  try {
    vid = P.itemId(new URL(String(url)).pathname, await parserConf());
  } catch {
    return;
  }
  const stale = Object.keys(pendingBuys).filter((k) => pendingBuys[k].tabId === tabId && k !== vid);
  if (!stale.length) return;
  for (const k of stale) delete pendingBuys[k];
  await local.set({ pendingBuys });
}

async function onPurchaseDone(msg, sender) {
  const { pendingBuys = {} } = await local.get("pendingBuys");
  const now = Date.now();
  // Only the checkout started from FlipFinder in this very tab: a purchase is never attributed
  // to another item (record it by hand with "I bought it" when it was completed elsewhere).
  const match = Object.entries(pendingBuys).find(([, p]) => now - p.at < BUY_TTL_MS && sender.tab && p.tabId === sender.tab.id);
  if (!match) return { ok: false };
  const [vid, pending] = match;
  delete pendingBuys[vid];
  await local.set({ pendingBuys });
  const paid = typeof msg.total === "number" && msg.total > 0 ? msg.total : pending.price;
  await recordVinted(vid, { kind: "purchased", price: paid, source: "checkout", detail: { item_price: pending.price, total_read: msg.total ?? null } });
  return { ok: true, vid };
}

// ------------------------------------------------------------------ bridge to FlipFinder's pages
// One registration at a time: the worker start-up, a permission just granted and a changed address
// can all ask at once, and two concurrent runs both try to register the same script ID
// ("Duplicate script ID 'ff-app-bridge'").
let bridgeRun = Promise.resolve();
function registerAppBridge() {
  bridgeRun = bridgeRun.then(registerAppBridgeNow, registerAppBridgeNow);
  return bridgeRun;
}

async function registerAppBridgeNow() {
  const origin = await appOrigin();
  const allowed = Boolean(origin) && (await chrome.permissions.contains({ origins: [`${origin}/*`] }).catch(() => false));
  const want = allowed ? [`${origin}/*`] : null;
  const [current] = await chrome.scripting.getRegisteredContentScripts({ ids: ["ff-app-bridge"] }).catch(() => []);
  // Left as it is when nothing changed: the service worker starts often (every wake-up), and a
  // FlipFinder page loading while the script is re-registered would miss its bridge.
  if (!(current && want && JSON.stringify(current.matches) === JSON.stringify(want))) {
    if (current) await chrome.scripting.unregisterContentScripts({ ids: ["ff-app-bridge"] }).catch(() => {});
    if (!want) return;
    await chrome.scripting
      .registerContentScripts([{ id: "ff-app-bridge", matches: want, js: ["src/app-bridge.js"], runAt: "document_start", persistAcrossSessions: true }])
      .catch((err) => logError("bridge", err.message));
  }
  // FlipFinder pages already open (address just set, extension just installed or updated, a
  // page loaded while the worker was restarting) get the bridge now, without a reload. Once per
  // page: the script checks it itself.
  const open = await chrome.tabs.query({ url: want[0] }).catch(() => []);
  for (const tab of open) chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["src/app-bridge.js"] }).catch(() => {});
}

// ------------------------------------------------------------------ messages
const OPENABLE = [/^\/items\/[\w-]+$/, /^\/deals\/[\w-]+$/, /^\/analyze(#import=[\w-]+)?$/, /^\/import(#batch=[\w-]+)?$/, /^\/settings(#[\w-]+)?$/, /^\/items$/];

let pageStatsOffUntil = 0;
let lookupTimer = null;
const lookupWanted = new Map(); // vid -> Set(tabId)

function scheduleLookup() {
  clearTimeout(lookupTimer);
  lookupTimer = setTimeout(async () => {
    const wanted = new Map(lookupWanted);
    lookupWanted.clear();
    const vids = [...wanted.keys()].slice(0, 200);
    if (!vids.length || !(await getKey())) return;
    try {
      const timing = { ep: "evaluations", n: vids.length };
      const evals = await api("/capture/evaluations", { method: "POST", body: { vinted_ids: vids }, timing });
      const tabs = new Set([...wanted.values()].flatMap((s) => [...s]));
      await onEvaluations(evals, { tabIds: [...tabs], fresh: false, timing });
    } catch (err) {
      await logError("valutazioni", err.message);
    }
  }, 400);
}

const HANDLERS = {
  async "ff:bridge-hello"(msg, sender) {
    if (!(await trustedSender(sender))) return { ok: false };
    return { ok: true, version: VERSION, paired: Boolean(await getKey()) };
  },

  async "ff:vinted-favourite"(msg, sender) {
    if (!(await trustedSender(sender))) return { ok: false, code: "forbidden", message: "Richiesta non autorizzata." };
    if (!(await getKey())) return { ok: false, code: "unpaired", message: "Estensione non associata a FlipFinder." };
    return vintedFavourite(msg);
  },

  async "ff:vinted-buy-check"(msg, sender) {
    if (!(await trustedSender(sender))) return { ok: false, code: "forbidden", message: "Richiesta non autorizzata." };
    if (!(await getKey())) return { ok: false, code: "unpaired", message: "Estensione non associata a FlipFinder." };
    return vintedBuyCheck(msg);
  },

  async "ff:vinted-buy-open"(msg, sender) {
    if (!(await trustedSender(sender))) return { ok: false, code: "forbidden", message: "Richiesta non autorizzata." };
    return vintedBuyOpen(msg);
  },

  async "ff:vinted-buy"(msg, sender) {
    if (!(await trustedSender(sender))) return { ok: false, code: "forbidden", message: "Richiesta non autorizzata." };
    if (!(await getKey())) return { ok: false, code: "unpaired", message: "Estensione non associata a FlipFinder." };
    return vintedBuy(msg);
  },

  async "ff:purchase-done"(msg, sender) {
    return onPurchaseDone(msg, sender);
  },

  async "ff:favourite-seen"(msg, sender) {
    if (sender.tab && favouriteTabs.has(sender.tab.id)) return { ok: true };
    if (!/^\d+$/.test(String(msg.vid)) || typeof msg.value !== "boolean") return { ok: false };
    try {
      await api("/capture/vinted-actions", { method: "POST", body: { vinted_id: String(msg.vid), kind: "favourite", value: msg.value, source: "page" } });
    } catch (err) {
      if (err.status === 404) favouriteSeenLater.set(String(msg.vid), msg.value); // listing still being recorded
      else await logError("vinted", err.message);
    }
    return { ok: true };
  },

  async "ff:hello"(msg, sender) {
    const tabId = sender.tab && sender.tab.id;
    if (typeof tabId === "number" && msg.pageType === "item") forgetBuyIfLeft(tabId, msg.url);
    if (typeof tabId === "number" && checkoutWaits.has(tabId)) {
      const C = await parserConf();
      let path = "";
      try {
        path = new URL(String(msg.url)).pathname;
      } catch {
        /* not a URL */
      }
      if (C.patterns.page_checkout && C.patterns.page_checkout.test(path)) checkoutWaits.get(tabId)();
    }
    if (typeof tabId === "number") {
      // Bookkeeping for the live panel, without making the page wait for it.
      exclusive(async () => {
        const tabs = await registeredTabs();
        tabs[tabId] = { pageType: msg.pageType, url: String(msg.url || "").slice(0, 300), at: Date.now() };
        await sessionStore.set({ tabs });
      })
        .then(() => touchSession(tabId, msg.url, msg.pageType, 0))
        .then(() => sendToPanel({ type: "ff:tab-update", tabId }))
        .catch(() => {});
    }
    const [{ parserConfig }, options, key] = await Promise.all([local.get("parserConfig"), getOptions(), getKey()]);
    return { options, paired: Boolean(key), parserConfig: parserConfig || null, tabId };
  },

  async "ff:cards"(msg, sender) {
    const tabId = sender.tab && sender.tab.id;
    await touchSession(tabId, msg.pageUrl, msg.pageType, (msg.cards || []).length);
    const first = Boolean(msg.first);
    const entries = (msg.cards || []).slice(0, 200).map((c) => ({ vid: c.vid, payload: c.payload, pageType: msg.pageType, pageUrl: String(msg.pageUrl || "").slice(0, 1000), tabId, ...(first ? { first } : {}) }));
    // At once: the page groups its cards itself (best first, then the rest of the document,
    // then cards read later in small groups).
    await enqueue("cards", entries, 0);
    return { queued: entries.length };
  },

  async "ff:page-stats"(msg) {
    // One request per page (and per new batch of cards): pre-computed statistics, no analysis.
    if (Date.now() < pageStatsOffUntil || !(await getKey())) return { ok: false, off: true };
    const items = (msg.items || []).filter((i) => i && /^\d{1,20}$/.test(String(i.vinted_id))).slice(0, 120);
    if (!items.length) return { ok: false };
    const timing = { ep: "page-stats", n: items.length };
    try {
      const res = await api("/extension/page-stats", { method: "POST", body: { items }, timing });
      return { ok: true, stats: (res && res.stats) || {}, timing };
    } catch (err) {
      // An older FlipFinder without it, or switched off: not asked again for a while.
      if (err.status === 404 || err.status === 403 || err.status === 405) pageStatsOffUntil = Date.now() + 3600 * 1000;
      else if (err.status === 429) pageStatsOffUntil = Date.now() + (err.retryAfterMs || 60000);
      return { ok: false, status: err.status || 0 };
    }
  },

  async "ff:item"(msg, sender) {
    const tabId = sender.tab && sender.tab.id;
    await touchSession(tabId, msg.pageUrl, "item", 0);
    await enqueue("items", [{ vid: msg.vid, payload: msg.payload, mode: "extension_item", tabId, pageType: "item" }], 150);
    return { queued: 1 };
  },

  async "ff:get-evals"(msg, sender) {
    const vids = (msg.vids || []).filter((v) => /^\d+$/.test(v)).slice(0, 300);
    const found = await cachedEvaluations(vids);
    const known = new Set(found.map((e) => e.vinted_id));
    const tabId = sender.tab && sender.tab.id;
    // Cards sent for capture get their verdict from it: no second request for them.
    const remote = Array.isArray(msg.remote) ? new Set(msg.remote) : null;
    for (const v of vids) {
      if (known.has(v) || (remote && !remote.has(v))) continue;
      if (!lookupWanted.has(v)) lookupWanted.set(v, new Set());
      if (typeof tabId === "number") lookupWanted.get(v).add(tabId);
    }
    if (lookupWanted.size) scheduleLookup();
    if (typeof tabId === "number" && found.length) await addToSession(tabId, found, false);
    return { evals: found };
  },

  async "ff:track"(msg) {
    const res = await api("/capture/track", { method: "POST", body: { url: msg.url, track: msg.track !== false } });
    if (res.evaluation) await cacheEvaluations([res.evaluation]);
    return res;
  },

  async "ff:deep"(msg, sender) {
    const tabId = sender.tab ? sender.tab.id : msg.tabId;
    if (!/^\d+$/.test(String(msg.vid)) || !msg.url) return { error: "Annuncio non valido." };
    if (!(await getKey())) return { error: "Associa prima l'estensione a FlipFinder (opzioni)." };
    return deepEnqueue({ vid: String(msg.vid), url: msg.url, kind: "manual", tabId });
  },

  async "ff:open"(msg) {
    const opts = await getOptions();
    const path = String(msg.path || "");
    if (!OPENABLE.some((rx) => rx.test(path))) return { ok: false };
    await chrome.tabs.create({ url: `${opts.appUrl}${path}` });
    return { ok: true };
  },

  async "ff:item-detail"(msg) {
    if (!/^\d+$/.test(String(msg.vid))) return { error: "Annuncio non valido." };
    try {
      return await api(`/capture/items/${msg.vid}`);
    } catch (err) {
      return { error: err.status === 404 ? "Articolo non trovato." : err.message };
    }
  },

  async "ff:open-options"() {
    await chrome.runtime.openOptionsPage();
    return { ok: true };
  },

  async "ff:open-panel"(msg, sender) {
    const tabId = sender.tab ? sender.tab.id : msg.tabId;
    if (!chrome.sidePanel || typeof tabId !== "number") return { error: "Pannello non disponibile in questo browser." };
    try {
      await chrome.sidePanel.open({ tabId });
      return { ok: true };
    } catch {
      return { error: "Apri il pannello dall'icona dell'estensione o con Alt+Shift+F." };
    }
  },

  async "ff:status"() {
    const [sync, q, d, key, opts, store] = await Promise.all([getSync(), loadQueue(), loadDeep(), getKey(), getOptions(), local.get(["errors", "parserConfig", "account"])]);
    return {
      version: VERSION,
      paired: Boolean(key),
      account: store.account || null,
      appUrl: opts.appUrl,
      sync: { ...sync, pending: K.queueSize(q) },
      deep: { queued: d.jobs.length, pausedUntil: d.pacing.pausedUntil, pauseReason: d.pacing.pauseReason },
      parserVersion: (store.parserConfig && store.parserConfig.version) || globalThis.FF_PARSER_CONFIG.version,
      errors: (store.errors || []).slice(0, 10),
    };
  },

  // ---- automatic scanner (the page asking has just obtained the site permission)
  async "ff:scan-state"() {
    return scanSummary();
  },

  async "ff:scan-add"(msg) {
    const n = K.normalizeSearchUrl(msg.url);
    if (n.error) return { error: n.error };
    if (!(await chrome.permissions.contains({ origins: [`${n.origin}/*`] }))) return { error: `Serve il permesso di leggere ${new URL(n.origin).hostname}.` };
    const name = String(msg.name || "").trim().slice(0, 60) || n.name;
    const res = await exclusive(async () => {
      const sc = await loadScanner();
      if (sc.searches.some((s) => K.normalizeSearchUrl(s.url).key === n.key)) return { error: "Questa ricerca è già nello scanner." };
      if (sc.searches.length >= K.SCAN.maxSearches) return { error: `Al massimo ${K.SCAN.maxSearches} ricerche: toglierne una prima di aggiungerne un'altra.` };
      sc.searches.push({ id: crypto.randomUUID(), name, url: n.url, enabled: true, addedAt: Date.now(), lastScanAt: 0, failures: 0, seen: [], primed: false });
      await local.set({ scanner: sc });
      return { ok: true };
    });
    if (res.ok) scanTick();
    return res.ok ? { ok: true, summary: await scanSummary() } : res;
  },

  async "ff:scan-remove"(msg) {
    await exclusive(async () => {
      const sc = await loadScanner();
      sc.searches = sc.searches.filter((s) => s.id !== msg.id);
      await local.set({ scanner: sc });
    });
    return { ok: true, summary: await scanSummary() };
  },

  async "ff:scan-toggle"(msg) {
    await scanMark(String(msg.id), msg.enabled ? { enabled: true, failures: 0, lastError: "" } : { enabled: false });
    if (msg.enabled) scanTick();
    return { ok: true, summary: await scanSummary() };
  },

  async "ff:pair"(msg) {
    const appUrl = K.normalizeAppUrl(msg.appUrl);
    const key = String(msg.key || "").trim();
    if (!key.startsWith("ff_ext_")) return { error: "La chiave deve iniziare con ff_ext_ (la trovi in FlipFinder → Impostazioni → Browser extension)." };
    try {
      const res = await api("/extension/ping", { appUrl, key });
      const { options } = await chrome.storage.sync.get("options");
      await chrome.storage.sync.set({ options: { ...(options || {}), appUrl } });
      await local.set({ apiKey: key, account: res.account || null, paired: true });
      await setSync({ state: "ok", message: "" });
      updateParserConfig();
      updateMarketCache();
      scheduleFlush(0);
      return { ok: true, account: res.account, parserVersion: res.parser_version };
    } catch (err) {
      return { error: err.status === 401 ? "Chiave non valida o revocata." : err.message };
    }
  },

  async "ff:unpair"() {
    // The market summary and evaluations belong to that account (its costs): dropped too.
    await local.remove(["apiKey", "account", "paired", "marketCache", "evalCache"]);
    marketVersion = null;
    evalMem = null;
    await setSync({ state: "unpaired" });
    return { ok: true };
  },

  async "ff:flush"() {
    await flush();
    return { ok: true };
  },

  async "ff:clear-queue"() {
    await exclusive(() => local.set({ queue: K.emptyQueue() }));
    await setSync({ pending: 0 });
    return { ok: true };
  },

  // ---- live panel
  async "ff:panel-state"(msg) {
    const sessions = await loadSessions();
    const tabs = await registeredTabs();
    return { session: sessions[msg.tabId] || null, tab: tabs[msg.tabId] || null, options: await getOptions(), sync: await getSync() };
  },

  async "ff:session-reset"(msg) {
    await withSession(msg.tabId, (s, sessions) => {
      sessions[msg.tabId] = emptySession(s ? s.key : "", s ? s.pageType : "catalog");
    });
    return { ok: true };
  },

  async "ff:session-lock"(msg) {
    await withSession(msg.tabId, (s) => {
      if (s) s.locked = Boolean(msg.locked);
    });
    return { ok: true };
  },

  async "ff:relay"(msg) {
    // Panel -> page: scroll to / highlight a card. Only these two commands are relayed.
    if (!["ff:scroll-to", "ff:highlight"].includes(msg.message && msg.message.type)) return { ok: false };
    return (await sendToTab(msg.tabId, msg.message)) || { ok: false };
  },
};

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Messages come from this extension only (content scripts and pages); never from websites.
  if (sender.id !== chrome.runtime.id) return false;
  const handler = msg && typeof msg.type === "string" && Object.hasOwn(HANDLERS, msg.type) ? HANDLERS[msg.type] : null;
  if (!handler) return false;
  Promise.resolve()
    .then(() => handler(msg, sender))
    .then(sendResponse, (err) => sendResponse({ error: (err && err.message) || "Errore inatteso." }));
  return true;
});

// ------------------------------------------------------------------ lifecycle
chrome.tabs.onRemoved.addListener((tabId) => {
  exclusive(async () => {
    const tabs = await registeredTabs();
    const sessions = await loadSessions();
    delete tabs[tabId];
    delete sessions[tabId];
    await sessionStore.set({ tabs, sessions });
  });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "ff-flush") flush().catch((e) => logError("flush", e.message));
  else if (alarm.name === "ff-deep") deepPump().catch((e) => logError("deep", e.message));
  else if (alarm.name === "ff-refresh") pollRefreshQueue();
  else if (alarm.name === "ff-scan") scanTick();
  else if (alarm.name === "ff-config") updateParserConfig();
  else if (alarm.name === "ff-market") updateMarketCache();
});

chrome.permissions.onAdded.addListener(() => registerAppBridge());

chrome.storage.onChanged.addListener((changes, area) => {
  if ((area === "sync" && changes.options) || (area === "local" && (changes.paired || changes.parserConfig || changes.marketCache))) publishBoot();
  if (area === "sync" && changes.options) {
    scheduleFlush(0);
    registerAppBridge();
    // Switched on: the first read starts at once (the pacing still applies).
    const before = changes.options.oldValue && changes.options.oldValue.scanEnabled;
    if (changes.options.newValue && changes.options.newValue.scanEnabled && !before) scanTick();
  }
  if (area === "local" && changes.apiKey) {
    // Pages only see whether the extension is paired, never the key.
    local.set({ paired: Boolean(changes.apiKey.newValue) });
    if (changes.apiKey.newValue) updateMarketCache();
  }
});

async function startup() {
  // Vinted pages read their start-up data (see publishBoot) from the session storage.
  await sessionStore.setAccessLevel?.({ accessLevel: "TRUSTED_AND_UNTRUSTED_CONTEXTS" }).catch(() => {});
  chrome.alarms.create("ff-config", { periodInMinutes: 360, delayInMinutes: 1 });
  chrome.alarms.create("ff-market", { periodInMinutes: 180, delayInMinutes: 180 });
  chrome.alarms.create("ff-refresh", { periodInMinutes: 10, delayInMinutes: 2 });
  chrome.alarms.create("ff-scan", { periodInMinutes: 1, delayInMinutes: 1 }); // does nothing unless the scanner is on
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => {});
  registerAppBridge();
  const key = await getKey();
  // Pages read this flag (never the key) to know whether to score their cards.
  if (Boolean(key) !== Boolean((await local.get("paired")).paired)) await local.set({ paired: Boolean(key) });
  const { boot } = await sessionStore.get("boot");
  if (!boot) publishBoot();
  const q = await loadQueue();
  paintBadge(key ? { ...(await getSync()), pending: K.queueSize(q) } : { state: "unpaired" });
  if (key) {
    updateParserConfig();
    updateMarketCache();
    scheduleFlush(500);
    scheduleDeep(2000);
  }
}

chrome.runtime.onInstalled.addListener((details) => {
  startup();
  if (details.reason === "install") chrome.runtime.openOptionsPage().catch(() => {});
});
chrome.runtime.onStartup.addListener(startup);
startup();

chrome.commands?.onCommand.addListener(async (command, tab) => {
  if (command === "open-panel" && tab && chrome.sidePanel) await chrome.sidePanel.open({ tabId: tab.id }).catch(() => {});
});
