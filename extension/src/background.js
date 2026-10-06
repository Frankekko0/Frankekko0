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
importScripts("parser-config.js", "parse.js", "core.js");

const P = globalThis.FlipFinderParse;
const K = globalThis.FlipFinderCore;
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

async function api(path, { method = "GET", body, appUrl, key } = {}) {
  const opts = await getOptions();
  const base = appUrl || opts.appUrl;
  const token = key || (await getKey());
  if (!token) throw new ApiError(0, "unpaired", "Estensione non associata a FlipFinder.");
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
  if (res.ok) return res.status === 204 ? null : res.json();
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
const EVAL_CACHE_MAX = 3000;

async function cacheEvaluations(evals) {
  if (!evals.length) return;
  const { evals: cache = {} } = await sessionStore.get("evals");
  const now = Date.now();
  for (const ev of evals) cache[ev.vinted_id] = { ...ev, cachedAt: now };
  const ids = Object.keys(cache);
  if (ids.length > EVAL_CACHE_MAX) {
    ids.sort((a, b) => cache[a].cachedAt - cache[b].cachedAt);
    for (const id of ids.slice(0, ids.length - EVAL_CACHE_MAX)) delete cache[id];
  }
  await sessionStore.set({ evals: cache });
}

async function cachedEvaluations(vids) {
  const { evals: cache = {} } = await sessionStore.get("evals");
  return vids.map((v) => cache[v]).filter(Boolean);
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

async function loadSessions() {
  const { sessions = {} } = await sessionStore.get("sessions");
  return sessions;
}

async function withSession(tabId, fn) {
  return exclusive(async () => {
    const sessions = await loadSessions();
    const s = sessions[tabId];
    const out = await fn(s, sessions);
    await sessionStore.set({ sessions });
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
async function onEvaluations(evals, { tabIds = [], fresh = true, deep = null } = {}) {
  await cacheEvaluations(evals);
  const opts = await getOptions();
  for (const tabId of new Set(tabIds.filter((t) => typeof t === "number"))) {
    await sendToTab(tabId, { type: "ff:evals", evals, deep });
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
  if (kind === "items") {
    const e = batch.entries[0];
    const res = await api("/capture/item", {
      method: "POST",
      body: { item: e.payload, mode: e.mode || "extension_item", track: e.track ?? null, extension_version: VERSION },
    });
    return { evaluations: res.evaluation ? [res.evaluation] : [], analysis: res.analysis };
  }
  return api("/capture/cards", {
    method: "POST",
    body: { page_type: batch.pageType.split("#")[0], page_url: batch.entries[0].pageUrl || "", items: batch.entries.map((e) => e.payload), extension_version: VERSION },
  });
}

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
    const batch = items || K.queueTake(q, "cards", Date.now(), 40);
    if (!batch) break;
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
      const evals = await api("/capture/evaluations", { method: "POST", body: { vinted_ids: vids } });
      const tabs = new Set([...wanted.values()].flatMap((s) => [...s]));
      await onEvaluations(evals, { tabIds: [...tabs], fresh: false });
    } catch (err) {
      await logError("valutazioni", err.message);
    }
  }, 400);
}

const HANDLERS = {
  async "ff:hello"(msg, sender) {
    const tabId = sender.tab && sender.tab.id;
    if (typeof tabId === "number") {
      await exclusive(async () => {
        const tabs = await registeredTabs();
        tabs[tabId] = { pageType: msg.pageType, url: String(msg.url || "").slice(0, 300), at: Date.now() };
        await sessionStore.set({ tabs });
      });
      await touchSession(tabId, msg.url, msg.pageType, 0);
      sendToPanel({ type: "ff:tab-update", tabId });
    }
    const { parserConfig } = await local.get("parserConfig");
    return { options: await getOptions(), paired: Boolean(await getKey()), parserConfig: parserConfig || null, tabId };
  },

  async "ff:cards"(msg, sender) {
    const tabId = sender.tab && sender.tab.id;
    await touchSession(tabId, msg.pageUrl, msg.pageType, (msg.cards || []).length);
    const entries = (msg.cards || []).slice(0, 200).map((c) => ({ vid: c.vid, payload: c.payload, pageType: msg.pageType, pageUrl: String(msg.pageUrl || "").slice(0, 1000), tabId }));
    await enqueue("cards", entries, 800);
    return { queued: entries.length };
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
    for (const v of vids) {
      if (known.has(v)) continue;
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

  async "ff:pair"(msg) {
    const appUrl = K.normalizeAppUrl(msg.appUrl);
    const key = String(msg.key || "").trim();
    if (!key.startsWith("ff_ext_")) return { error: "La chiave deve iniziare con ff_ext_ (la trovi in FlipFinder → Impostazioni → Browser extension)." };
    try {
      const res = await api("/extension/ping", { appUrl, key });
      const { options } = await chrome.storage.sync.get("options");
      await chrome.storage.sync.set({ options: { ...(options || {}), appUrl } });
      await local.set({ apiKey: key, account: res.account || null });
      await setSync({ state: "ok", message: "" });
      updateParserConfig();
      scheduleFlush(0);
      return { ok: true, account: res.account, parserVersion: res.parser_version };
    } catch (err) {
      return { error: err.status === 401 ? "Chiave non valida o revocata." : err.message };
    }
  },

  async "ff:unpair"() {
    await local.remove(["apiKey", "account"]);
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
  else if (alarm.name === "ff-config") updateParserConfig();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "sync" && changes.options) scheduleFlush(0);
});

async function startup() {
  chrome.alarms.create("ff-config", { periodInMinutes: 360, delayInMinutes: 1 });
  chrome.alarms.create("ff-refresh", { periodInMinutes: 10, delayInMinutes: 2 });
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => {});
  const key = await getKey();
  const q = await loadQueue();
  paintBadge(key ? { ...(await getSync()), pending: K.queueSize(q) } : { state: "unpaired" });
  if (key) {
    updateParserConfig();
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
