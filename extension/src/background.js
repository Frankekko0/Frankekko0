/*
 * FlipFinder for Vinted - service worker.
 *
 *  - Sync queue: captures from the content script are kept in chrome.storage.local, one entry
 *    per Vinted ID, sent in batches with retry and backoff; the toolbar badge shows the state.
 *  - FlipFinder API client: authenticates with the extension key the user pasted in the
 *    options (a FlipFinder credential). Vinted cookies or tokens are never read or sent.
 *  - It reads only the pages the user opens and scrolls: no scanner, no background reads, no
 *    clicks on Vinted. Buying, favouriting and messaging stay the user's own actions.
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
  return { key, pageType, startedAt: Date.now(), locked: false, seen: 0, saved: 0, bestMargin: null, alerted: [], evals: {} };
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

/** Evaluations arrived: cache, badges in the tab, live panel, alerts. */
async function onEvaluations(evals, { tabIds = [], fresh = true, deep = null, timing = null } = {}) {
  await cacheEvaluations(evals);
  const opts = await getOptions();
  for (const tabId of new Set(tabIds.filter((t) => typeof t === "number"))) {
    await sendToTab(tabId, { type: "ff:evals", evals, deep, timing });
    const hot = await addToSession(tabId, evals, fresh);
    sendToPanel({ type: "ff:session-update", tabId });
    if (hot.length) {
      sendToPanel({ type: "ff:hot", tabId, items: hot });
      if (opts.alertsVisual) await sendToTab(tabId, { type: "ff:hot", vids: hot.map((h) => h.vinted_id) });
    }
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
  const parser_version = String((await rawParserConfig()).version || "").slice(0, 24) || null;
  const timing = { ep: kind === "items" ? "item" : "cards", n: batch.entries.length };
  if (kind === "items") {
    const e = batch.entries[0];
    const res = await api("/capture/item", {
      method: "POST",
      body: { item: e.payload, mode: e.mode || "extension_item", track: e.track ?? null, extension_version: VERSION, parser_version },
      timing,
    });
    return { evaluations: res.evaluation ? [res.evaluation] : [], analysis: res.analysis, timing };
  }
  const res = await api("/capture/cards", {
    method: "POST",
    body: { page_type: batch.pageType.split("#")[0], page_url: batch.entries[0].pageUrl || "", items: batch.entries.map((e) => e.payload), extension_version: VERSION, parser_version },
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

// ------------------------------------------------------------------ tabs
async function registeredTabs() {
  const { tabs = {} } = await sessionStore.get("tabs");
  return tabs;
}

/** The parser configuration in use as plain data. */
async function rawParserConfig() {
  const { parserConfig } = await local.get("parserConfig");
  return parserConfig && parserConfig.version ? parserConfig : globalThis.FF_PARSER_CONFIG;
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
  async "ff:hello"(msg, sender) {
    const tabId = sender.tab && sender.tab.id;
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
    const [sync, q, key, opts, store] = await Promise.all([getSync(), loadQueue(), getKey(), getOptions(), local.get(["errors", "parserConfig", "account"])]);
    return {
      version: VERSION,
      paired: Boolean(key),
      account: store.account || null,
      appUrl: opts.appUrl,
      sync: { ...sync, pending: K.queueSize(q) },
      parserVersion: (store.parserConfig && store.parserConfig.version) || globalThis.FF_PARSER_CONFIG.version,
      errors: (store.errors || []).slice(0, 10),
    };
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
  else if (alarm.name === "ff-config") updateParserConfig();
  else if (alarm.name === "ff-market") updateMarketCache();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if ((area === "sync" && changes.options) || (area === "local" && (changes.paired || changes.parserConfig || changes.marketCache))) publishBoot();
  if (area === "sync" && changes.options) {
    scheduleFlush(0);
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
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => {});
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
