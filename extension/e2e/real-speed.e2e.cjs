// Where the time goes on REAL Vinted search pages: the real extension in Chromium, paired with a
// running FlipFinder (APP_URL), on live vinted.it catalog pages (96 cards each). Reusable: run it
// on the current code and again after a change, same command, and compare the RESULT lines.
//
//   NODE_PATH=/opt/node22/lib/node_modules APP_URL=http://localhost:8100 \
//     DB_URL=postgresql://flipfinder:flipfinder@localhost:5432/flipfinder_baseline \
//     node extension/e2e/real-speed.e2e.cjs
//
// Per page load it measures (times in ms from navigation start, "da DOM" = from DOM interactive):
// - page: TTFB, response end, DOM interactive, DOMContentLoaded, load, first contentful paint;
// - extension: cards read, first quick verdict, quick verdicts on all cards (count and time), best
//   opportunity highlighted, first/all full server verdicts on the badges, its ff:* marks;
// - network: every request from the extension to FlipFinder, through a small timing reverse proxy
//   (PROXY_PORT, default 8190; 0 = none): duration, time to first byte, size;
// - database and analysis: the Server-Timing header of each response (db / analysis / total); code
//   that does not send it is served through backend/tests/bench/db_probe.py, which adds it;
// - images: photo analysis (vision) of the page's best candidates, polled in the database (DB_URL);
// - rendering: badges painted (next frame after the extension marks), long tasks.
// Gentle with Vinted: at most 25 page loads per run (LOADS, default 20), PAUSE_MS apart (default
// 4000), no scrolling, no clicks (consent and country banners stay as they are), no item pages
// beyond what the extension itself opens. SEARCHES="a|b|c" changes the searches. SAVED_PAGE=<file>
// serves a saved Vinted page instead of the live site (development only: labelled in the output).
// OUT=<file> writes every measurement as JSON. Prints a human table and a RESULT JSON line.
"use strict";
const { chromium, request } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");
const { execFile, execFileSync } = require("child_process");
const { performance } = require("perf_hooks");

const MAX_LOADS = 25;
const DEFAULT_SEARCHES = ["ralph lauren polo", "nike air max 90", "the north face nuptse", "levis 501", "carhartt"];
const env = process.env;
const CFG = {
  app: (env.APP_URL || "http://localhost:8100").replace(/\/+$/, ""),
  proxyPort: Number(env.PROXY_PORT ?? 8190),
  dbUrl: (env.DB_URL || "").replace("postgresql+asyncpg://", "postgresql://"),
  searches: (env.SEARCHES ? env.SEARCHES.split("|") : DEFAULT_SEARCHES).map((s) => s.trim()).filter(Boolean),
  loads: Math.max(1, Math.min(MAX_LOADS, Number(env.LOADS || 20))),
  pauseMs: Math.max(2000, Number(env.PAUSE_MS || 4000)),
  settleMs: Number(env.SETTLE_MS || 45000),
  visionTop: Number(env.VISION_TOP || 5),
  visionWaitMs: Number(env.VISION_WAIT_MS || 20000),
  host: (env.VINTED_HOST || "https://www.vinted.it").replace(/\/+$/, ""),
  savedPage: env.SAVED_PAGE || "",
  out: env.OUT || "",
  label: env.LABEL || "",
};
const SRC = path.resolve(__dirname, "..");
const now = () => performance.timeOrigin + performance.now(); // epoch ms, same clock as the pages
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const round = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? null : Math.round(v * 10 ** d) / 10 ** d);

// ------------------------------------------------------------------ timing reverse proxy
// Sits between the extension's service worker and FlipFinder: every request with its duration,
// time to first byte, sizes and Server-Timing (db / analysis / total), on the pages' clock.
function startProxy(port, target) {
  const up = new URL(target);
  const log = [];
  const server = http.createServer((req, res) => {
    const start = now();
    const body = [];
    req.on("data", (c) => body.push(c));
    req.on("end", () => {
      const sent = Buffer.concat(body);
      const entry = { method: req.method, url: req.url, start, ttfb: null, end: null, status: 0, reqBytes: sent.length, resBytes: 0, serverTiming: null, items: null };
      if (sent.length && /json/.test(req.headers["content-type"] || "")) {
        try {
          const b = JSON.parse(sent.toString("utf8"));
          const list = b.items || b.vinted_ids || (b.item ? [b.item] : null);
          if (Array.isArray(list)) entry.items = list.length;
        } catch {
          /* not JSON */
        }
      }
      const fwd = http.request(
        { hostname: up.hostname, port: up.port || 80, method: req.method, path: req.url, headers: { ...req.headers, host: up.host } },
        (r) => {
          entry.ttfb = now();
          entry.status = r.statusCode;
          entry.serverTiming = r.headers["server-timing"] || null;
          res.writeHead(r.statusCode, r.headers);
          r.on("data", (c) => {
            entry.resBytes += c.length;
            res.write(c);
          });
          r.on("end", () => {
            entry.end = now();
            log.push(entry);
            res.end();
          });
        },
      );
      fwd.on("error", (e) => {
        entry.end = now();
        entry.error = e.code || e.message;
        log.push(entry);
        res.writeHead(502);
        res.end();
      });
      fwd.end(sent);
    });
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve({ log, url: `http://127.0.0.1:${port}`, close: () => new Promise((r) => server.close(r)) }));
  });
}

/** "db;dur=12.5, analysis;dur=300, total;dur=420" -> { db: 12.5, analysis: 300, total: 420 } */
function parseServerTiming(value) {
  const out = {};
  for (const part of String(value || "").split(",")) {
    const m = /^\s*([\w-]+)\s*(?:;.*?dur=([\d.]+))?/.exec(part);
    if (m && m[2] !== undefined) out[m[1]] = Number(m[2]);
  }
  return out;
}

/** Endpoint name without ids and query: /api/v1/capture/items/123 -> /capture/items/:id */
function endpoint(method, url) {
  const p = url.split("?")[0].replace(/^\/api\/v1/, "").replace(/\/\d+(?=\/|$)/g, "/:id").replace(/\/[0-9a-f-]{36}(?=\/|$)/g, "/:uuid");
  return `${method} ${p}`;
}

// ------------------------------------------------------------------ database (psql)
function psql(sql) {
  return new Promise((resolve) => {
    if (!CFG.dbUrl) return resolve(null);
    execFile("psql", [CFG.dbUrl, "-At", "-F", "\t", "-c", sql], { timeout: 10000 }, (err, stdout) => resolve(err ? null : stdout.trim().split("\n").filter(Boolean).map((l) => l.split("\t"))));
  });
}

/** Page listings known to FlipFinder: risk-adjusted profit, flip score, photo analysis done. */
async function listingState(vids) {
  const ids = vids.filter((v) => /^\d+$/.test(v));
  if (!ids.length) return null;
  const rows = await psql(
    `SELECT l.external_id, (l.identification ? 'vision')::int, coalesce(o.risk_adjusted_profit::text, ''), coalesce(o.flip_score::text, '')
     FROM listings l LEFT JOIN opportunities o ON o.listing_id = l.id AND o.is_active
     WHERE l.provider = 'vinted' AND l.external_id IN (${ids.map((v) => `'${v}'`).join(",")})`,
  );
  if (!rows) return null;
  const out = {};
  for (const [vid, vision, rap, flip] of rows) out[vid] = { vision: vision === "1", rap: rap === "" ? null : Number(rap), flip: flip === "" ? null : Number(flip) };
  return out;
}

// ------------------------------------------------------------------ page instrumentation
// Installed before any page script: long tasks, paints, LCP, the moment the extension's attributes
// appear (and the next frame, i.e. painted), and a light poll (100 ms) of the badges' state.
function instrument() {
  const M = (window.__ffSpeed = {
    longtasks: [], lcp: null, attrs: {}, painted: {}, timeline: [], firstBadge: null, firstServer: null, lastServerChange: null, maxServer: 0,
    firstAllVerdicts: null, atQuick: null, quickSeen: new Set(), quickOtherSeen: new Set(),
  });
  try {
    new PerformanceObserver((l) => {
      for (const e of l.getEntries()) M.longtasks.push([Math.round(e.startTime), Math.round(e.duration)]);
    }).observe({ type: "longtask", buffered: true });
    new PerformanceObserver((l) => {
      const e = l.getEntries().at(-1);
      if (e) M.lcp = Math.round(e.startTime);
    }).observe({ type: "largest-contentful-paint", buffered: true });
  } catch {
    /* unsupported */
  }
  const watched = ["data-ff-quick-ms", "data-ff-best-ms", "data-ff-stats-ms", "data-ff-server-first-ms", "data-ff-server-all-ms"];
  // The document element may not exist yet: watch the document (subtree) for those attributes.
  new MutationObserver((muts) => {
    for (const m of muts) {
      const name = m.attributeName;
      if (m.target !== document.documentElement || M.attrs[name] !== undefined || !m.target.hasAttribute(name)) continue;
      M.attrs[name] = performance.now();
      requestAnimationFrame(() => requestAnimationFrame(() => (M.painted[name] = performance.now())));
    }
  }).observe(document, { attributes: true, subtree: true, attributeFilter: watched });

  const kind = (root) => {
    const host = root.querySelector("ff-badge");
    const b = host && host.shadowRoot && host.shadowRoot.querySelector(".b");
    if (!b) return "none";
    const src = host.getAttribute("data-ff-src") || root.getAttribute("data-ff-src");
    const text = (b.textContent || "").trim();
    const aria = b.getAttribute("aria-label") || "";
    if (b.querySelector(".dot")) return "pending";
    if (src === "server") return "server";
    if (src === "local" || src === "stats") return text.startsWith("≈") ? "quick" : "quick_other";
    if (/stima rapida/.test(aria) || text.startsWith("≈")) return "quick";
    if (/dati insufficienti \(/.test(aria) || aria === "FlipFinder: non in vendita") return "quick_other";
    if (/score \d+/.test(aria) || /\d+\s·\s/.test(text)) return "server";
    if (/dati insufficienti per una stima/.test(aria) || /^FlipFinder: (venduto|riservato|rimosso)/.test(aria)) return "server_other";
    if (text === "n/d") return "unreadable";
    return "other";
  };
  // The page's own cards (Vinted's product test ids), each with the badge the extension put on it:
  // a card the page re-created and the extension has not registered again counts as "none".
  const poll = () => {
    const roots = new Map();
    for (const r of document.querySelectorAll("[data-ff-vid]")) roots.set(r.getAttribute("data-ff-vid"), r);
    const ids = new Set();
    for (const el of document.querySelectorAll("[data-testid^='product-item-id-']")) {
      const m = /^product-item-id-(\d+)$/.exec(el.getAttribute("data-testid"));
      if (m) ids.add(m[1]);
    }
    const all = ids.size ? ids : new Set(roots.keys());
    if (!all.size || !roots.size) return;
    const c = { cards: all.size, none: 0, pending: 0, quick: 0, quick_other: 0, server: 0, server_other: 0, unreadable: 0, other: 0 };
    for (const vid of all) {
      const r = roots.get(vid);
      const k = r ? kind(r) : "none";
      c[k] += 1;
      if (k === "quick") M.quickSeen.add(vid);
      else if (k === "quick_other") M.quickOtherSeen.add(vid);
    }
    const t = performance.now();
    const shown = c.quick + c.quick_other + c.server + c.server_other + c.unreadable + c.other;
    if (M.prevShown !== undefined && M.prevShown - shown > (M.lostMax || 0)) {
      M.lostMax = M.prevShown - shown; // badges that disappeared (cards re-created by the page)
      M.lostAt = t;
    }
    M.prevShown = shown;
    if (M.atQuick === null && document.documentElement.hasAttribute("data-ff-quick-ms")) M.atQuick = c;
    const badged = c.cards - c.none;
    const server = c.server + c.server_other;
    if (badged && M.firstBadge === null) M.firstBadge = t;
    if (server && M.firstServer === null) M.firstServer = t;
    if (server > M.maxServer) {
      M.maxServer = server;
      M.lastServerChange = t;
    }
    if (M.firstAllVerdicts === null && c.pending + c.none === 0) M.firstAllVerdicts = t;
    M.last = c;
    const prev = M.timeline.at(-1);
    if (!prev || prev[1] !== c.quick + c.quick_other || prev[2] !== server) M.timeline.push([Math.round(t), c.quick + c.quick_other, server, c.cards]);
  };
  const start = () => setInterval(poll, 100);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
}

/** Everything the page knows about this load (called at the end of the observation). */
function collectPage() {
  const nav = performance.getEntriesByType("navigation")[0] || {};
  const paint = Object.fromEntries(performance.getEntriesByType("paint").map((p) => [p.name, Math.round(p.startTime)]));
  const marks = {};
  for (const m of performance.getEntriesByType("mark")) {
    if (!m.name.startsWith("ff:")) continue;
    const k = m.name.slice(3);
    if (!marks[k]) marks[k] = { first: m.startTime, last: m.startTime, n: 0 };
    marks[k].last = m.startTime;
    marks[k].n += 1;
  }
  const attrs = {};
  for (const a of document.documentElement.attributes) if (a.name.startsWith("data-ff-")) attrs[a.name] = a.value;
  const ids = new Set();
  for (const el of document.querySelectorAll("[data-testid^='product-item-id-']")) {
    const m = /^product-item-id-(\d+)$/.exec(el.getAttribute("data-testid"));
    if (m) ids.add(m[1]);
  }
  if (!ids.size) for (const a of document.querySelectorAll("a[href*='/items/']")) {
    const m = /\/items\/(\d+)/.exec(a.getAttribute("href") || "");
    if (m) ids.add(m[1]);
  }
  const res = performance.getEntriesByType("resource");
  const imgs = res.filter((r) => r.initiatorType === "img" || /\.(webp|jpe?g|png)(\?|$)/.test(r.name));
  const S = window.__ffSpeed || {};
  return {
    url: location.href,
    timeOrigin: performance.timeOrigin,
    nav: {
      ttfb: nav.responseStart, responseEnd: nav.responseEnd, domInteractive: nav.domInteractive, dcl: nav.domContentLoadedEventEnd,
      load: nav.loadEventEnd || null, htmlBytes: nav.decodedBodySize, transferBytes: nav.transferSize,
    },
    paint, lcp: S.lcp, marks, attrs,
    timingJson: attrs["data-ff-timing"] || null,
    vids: [...ids],
    extCards: document.querySelectorAll("[data-ff-vid]").length,
    badges: S.last || null,
    atQuick: S.atQuick || null,
    quickSeen: S.quickSeen ? S.quickSeen.size : null,
    quickOtherSeen: S.quickOtherSeen ? [...S.quickOtherSeen].filter((v) => !S.quickSeen.has(v)).length : null,
    timeline: S.timeline || [],
    firstBadge: S.firstBadge, firstServer: S.firstServer, lastServerChange: S.lastServerChange, maxServer: S.maxServer, firstAllVerdicts: S.firstAllVerdicts,
    attrSeen: S.attrs || {}, painted: S.painted || {}, lostMax: S.lostMax || 0, lostAt: S.lostAt ?? null,
    longtasks: S.longtasks || [],
    resources: { count: res.length, images: imgs.length, lastImageEnd: imgs.length ? Math.max(...imgs.map((r) => r.responseEnd)) : null },
    best: document.getElementById("flipfinder-best")?.shadowRoot?.querySelector(".box")?.textContent.replace(/\s+/g, " ").trim().slice(0, 160) || null,
  };
}

// ------------------------------------------------------------------ one page load
async function measureLoad(page, search, index, round_, proxy) {
  const url = CFG.savedPage ? `${CFG.host}/catalog?search_text=${encodeURIComponent(search)}&saved=1` : `${CFG.host}/catalog?search_text=${encodeURIComponent(search)}`;
  const load = { index, search, round: round_, visit: round_ === 0 ? "prima visita" : "visita ripetuta", loadavg: os.loadavg().map((v) => round(v, 2)) };
  const before = now();
  const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }).catch((e) => ({ error: e.message }));
  load.http = resp && resp.status ? resp.status() : resp && resp.error;
  // Vinted refused the page (anti-bot check, rate limit): the run stops here, never works around it.
  const title = await page.title().catch(() => "");
  if (load.http === 403 || load.http === 429 || /just a moment|attention required|un momento/i.test(title)) {
    load.refused = `HTTP ${load.http}${title ? `, "${title.slice(0, 60)}"` : ""}`;
    return load;
  }
  // Quick verdicts and the best opportunity: the extension sets these attributes when done.
  const seen = (name) => (window.__ffSpeed && window.__ffSpeed.attrs[name] !== undefined) || document.documentElement.hasAttribute(name);
  await page.waitForFunction(seen, "data-ff-quick-ms", { timeout: 20000 }).catch(() => null);
  await page.waitForFunction(seen, "data-ff-best-ms", { timeout: 3000 }).catch(() => null);
  const vids = await page.evaluate(() => [...document.querySelectorAll("[data-ff-vid]")].map((e) => e.getAttribute("data-ff-vid"))).catch(() => []);
  // Full server verdicts and photo analysis, until every card has a server verdict (stable) or SETTLE_MS.
  const vision = {}; // vid -> ms from navigation start when first seen done
  let origin = null;
  const deadline = Date.now() + CFG.settleMs;
  let lastPoll = 0;
  while (Date.now() < deadline) {
    const s = await page.evaluate(() => ({ o: performance.timeOrigin, S: window.__ffSpeed && { last: window.__ffSpeed.last, change: window.__ffSpeed.lastServerChange, now: performance.now() } })).catch(() => null);
    if (s) origin = s.o;
    if (CFG.dbUrl && Date.now() - lastPoll > 1000 && origin) {
      lastPoll = Date.now();
      const st = await listingState(vids);
      for (const [vid, v] of Object.entries(st || {})) if (v.vision && vision[vid] === undefined) vision[vid] = now() - origin;
    }
    const c = s && s.S && s.S.last;
    if (c && c.server + c.server_other >= c.cards - c.unreadable && s.S.now - (s.S.change || 0) > 1500) break;
    await sleep(250);
  }
  // Best candidates of the page (server ranking) and their photo analysis.
  let top = [];
  if (CFG.dbUrl && origin) {
    const waitUntil = Date.now() + CFG.visionWaitMs;
    for (;;) {
      const st = (await listingState(vids)) || {};
      for (const [vid, v] of Object.entries(st)) if (v.vision && vision[vid] === undefined) vision[vid] = now() - origin;
      top = Object.entries(st)
        .filter(([, v]) => v.rap !== null)
        .sort((a, b) => b[1].rap - a[1].rap || (b[1].flip || 0) - (a[1].flip || 0))
        .slice(0, CFG.visionTop)
        .map(([vid, v]) => ({ vid, rap: v.rap, flip: v.flip, vision_ms: vision[vid] === undefined ? null : round(vision[vid]) }));
      if ((top.length && top.every((t) => t.vision_ms !== null)) || Date.now() > waitUntil) break;
      await sleep(1000);
    }
  }
  const p = await page.evaluate(collectPage).catch((e) => ({ error: e.message }));
  load.page = p;
  load.vision = { top, done: top.filter((t) => t.vision_ms !== null).length, page_done: Object.keys(vision).length };
  const t0 = p.timeOrigin || before;
  load.requests = proxy
    ? proxy.log
        .filter((r) => r.start >= t0 - 50 && r.start <= now())
        .map((r) => ({ ep: endpoint(r.method, r.url), status: r.status, at: round(r.start - t0), ms: round(r.end - r.start), ttfb: round(r.ttfb - r.start), resKB: round(r.resBytes / 1024, 1), items: r.items, st: parseServerTiming(r.serverTiming), error: r.error }))
    : [];
  return load;
}

// ------------------------------------------------------------------ metrics and summary
function metricsOf(l) {
  const p = l.page || {};
  const n = p.nav || {};
  const mk = (k) => (p.marks && p.marks[k] ? p.marks[k].first : null);
  // First time the extension set the attribute (seen by the page's observer); the value left at
  // the end can be a later one: a page re-render can drop the attributes and the extension sets them again.
  const attr = (k) => (p.attrSeen && p.attrSeen[k] !== undefined ? p.attrSeen[k] : p.attrs && p.attrs[k] !== undefined ? Number(p.attrs[k]) : null);
  const attrLast = (k) => (p.attrs && p.attrs[k] !== undefined ? Number(p.attrs[k]) : null);
  const di = n.domInteractive;
  const quick = attr("data-ff-quick-ms");
  const best = attr("data-ff-best-ms");
  const firstQuick = [mk("best"), mk("first"), p.firstBadge].filter((v) => v !== null && v !== undefined).reduce((a, b) => Math.min(a, b), Infinity);
  const cardsReadEnd = mk("cards-read") ?? mk("parse-end");
  const cardsReadStart = mk("scan-start") ?? mk("parse-start");
  const serverFirst = attr("data-ff-server-first-ms") ?? mk("server-first") ?? p.firstServer;
  const serverAll = attr("data-ff-server-all-ms") ?? mk("server-all") ?? (p.maxServer ? p.lastServerChange : null);
  const reqs = l.requests || [];
  const caps = reqs.filter((r) => /capture\/cards|extension\/page-stats|capture\/evaluations|capture\/item/.test(r.ep));
  const sum = (arr, f) => (arr.length ? arr.reduce((a, r) => a + (f(r) || 0), 0) : null);
  const extStart = mk("dom");
  const extEnd = mk("badges") ?? mk("quick") ?? quick;
  const lt = p.longtasks || [];
  const ltDuring = (a, b) => (a === null || b === null ? [] : lt.filter(([t, d]) => t + d > a && t < b).map(([, d]) => d));
  const q = p.atQuick || {};
  let tj = null; // the extension's own timing (data-ff-timing): CPU spent reading cards, requests
  try {
    tj = p.timingJson ? JSON.parse(p.timingJson) : null;
  } catch {
    tj = null;
  }
  return {
    "page.ttfb": n.ttfb,
    "page.response_end": n.responseEnd,
    "page.dom_interactive": di,
    "page.dcl": n.dcl,
    "page.load": n.load,
    "page.fcp": p.paint ? p.paint["first-contentful-paint"] : null,
    "page.html_kb": n.htmlBytes ? n.htmlBytes / 1024 : null,
    "page.cards": p.vids ? p.vids.length : null,
    "ext.start_dom": extStart,
    "ext.cards_read_ms": cardsReadEnd !== null && cardsReadStart !== null ? cardsReadEnd - cardsReadStart : null,
    "ext.cards_read_at": cardsReadEnd,
    "ext.read_cpu_ms": tj ? (tj.read_ms || 0) + (tj.scan_ms || 0) : null,
    "ext.first_quick": Number.isFinite(firstQuick) ? firstQuick : null,
    "ext.first_quick_from_di": Number.isFinite(firstQuick) && di ? firstQuick - di : null,
    "ext.best": best,
    "ext.best_from_di": best !== null && di ? best - di : null,
    "ext.quick_all": quick,
    "ext.quick_all_from_di": quick !== null && di ? quick - di : null,
    "ext.quick_cards": p.quickSeen ?? null,
    "ext.quick_other_cards": p.quickOtherSeen ?? null,
    "ext.verdicts_at_quick": q.cards !== undefined ? q.cards - q.none - q.pending : null,
    "ext.work_ms": extStart !== null && extEnd !== null ? extEnd - extStart : null,
    "ext.stats_at": attr("data-ff-stats-ms") ?? mk("stats"),
    "ext.redrawn_at": attrLast("data-ff-quick-ms") !== null && quick !== null && attrLast("data-ff-quick-ms") - quick > 100 ? attrLast("data-ff-quick-ms") : null,
    "ext.badges_lost": p.lostMax ?? null,
    "server.first": serverFirst,
    "server.all": serverAll,
    "server.cards": p.maxServer ?? null,
    "net.requests": reqs.length,
    "net.capture_requests": caps.length,
    "net.capture_ms_sum": sum(caps, (r) => r.ms),
    "net.cards_sent": sum(reqs.filter((r) => /capture\/cards/.test(r.ep)), (r) => r.items),
    "net.first_capture_done": caps.length ? Math.min(...caps.map((r) => r.at + r.ms)) : null,
    "net.last_capture_done": caps.length ? Math.max(...caps.map((r) => r.at + r.ms)) : null,
    "db.ms_page": sum(reqs, (r) => r.st.db),
    "db.share_of_server": (() => {
      const tot = sum(reqs, (r) => r.st.total);
      return tot ? (100 * sum(reqs, (r) => r.st.db)) / tot : null;
    })(),
    "analysis.ms_page": sum(reqs, (r) => r.st.analysis),
    "images.top_done": l.vision ? l.vision.done : null,
    "images.top_all_ms": l.vision && l.vision.top.length && l.vision.done === l.vision.top.length ? Math.max(...l.vision.top.map((t) => t.vision_ms)) : null,
    "images.page_done": l.vision ? l.vision.page_done : null,
    "render.quick_painted": p.painted ? p.painted["data-ff-quick-ms"] ?? null : null,
    "render.best_painted": p.painted ? p.painted["data-ff-best-ms"] ?? null : null,
    "render.longtasks_ext_ms": extStart !== null && extEnd !== null ? ltDuring(extStart, extEnd).reduce((a, d) => a + d, 0) : null,
    "render.longtasks_to_quick_ms": quick !== null ? ltDuring(0, quick).reduce((a, d) => a + d, 0) : null,
    "render.longtask_max_ms": lt.length ? Math.max(...lt.map(([, d]) => d)) : 0,
  };
}

function stats(values) {
  const v = values.filter((x) => x !== null && x !== undefined && Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return { median: null, min: null, max: null, n: 0 };
  const mid = Math.floor(v.length / 2);
  const median = v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
  return { median: round(median, 1), min: round(v[0], 1), max: round(v.at(-1), 1), n: v.length };
}

function endpointStats(loads) {
  const by = {};
  for (const l of loads) for (const r of l.requests || []) (by[r.ep] = by[r.ep] || []).push(r);
  const out = {};
  for (const [ep, rs] of Object.entries(by)) {
    out[ep] = {
      count: rs.length,
      ms: stats(rs.map((r) => r.ms)),
      ttfb: stats(rs.map((r) => r.ttfb)),
      db: stats(rs.map((r) => r.st.db)),
      analysis: stats(rs.map((r) => r.st.analysis)),
      server_total: stats(rs.map((r) => r.st.total)),
      kb: stats(rs.map((r) => r.resKB)),
      errors: rs.filter((r) => r.error || r.status >= 400).length,
    };
  }
  return out;
}

const LABELS = {
  "page.ttfb": "Pagina: primo byte (TTFB)",
  "page.response_end": "Pagina: HTML scaricato",
  "page.dom_interactive": "Pagina: DOM interattivo (HTML letto)",
  "page.dcl": "Pagina: DOMContentLoaded",
  "page.load": "Pagina: load",
  "page.fcp": "Pagina: primo contenuto disegnato",
  "page.html_kb": "Pagina: HTML (KB decompressi)",
  "page.cards": "Pagina: schede articolo",
  "ext.start_dom": "Estensione: avvio sul documento",
  "ext.cards_read_ms": "Estensione: lettura schede (durata)",
  "ext.cards_read_at": "Estensione: schede lette",
  "ext.read_cpu_ms": "Estensione: CPU per leggere le schede",
  "ext.first_quick": "Primo verdetto rapido",
  "ext.first_quick_from_di": "Primo verdetto rapido (da DOM)",
  "ext.best": "Migliore evidenziata",
  "ext.best_from_di": "Migliore evidenziata (da DOM)",
  "ext.quick_all": "Verdetto rapido su tutte",
  "ext.quick_all_from_di": "Verdetto rapido su tutte (da DOM)",
  "ext.quick_cards": "Schede con stima rapida (numero)",
  "ext.quick_other_cards": "Schede rapide senza stima (dati insuff./stato)",
  "ext.verdicts_at_quick": "Schede con verdetto al 'rapido su tutte'",
  "ext.work_ms": "Estensione: lavoro avvio-badge",
  "ext.stats_at": "Statistiche di pagina applicate",
  "ext.redrawn_at": "Verdetti ridisegnati dopo un re-render",
  "ext.badges_lost": "Badge persi in un re-render (max)",
  "server.first": "Primo verdetto completo dal server",
  "server.all": "Tutti i verdetti completi dal server",
  "server.cards": "Schede con verdetto del server",
  "net.requests": "Richieste a FlipFinder",
  "net.capture_requests": "Richieste di cattura/statistiche",
  "net.capture_ms_sum": "Rete: durata somma catture",
  "net.cards_sent": "Schede inviate al server (con doppioni)",
  "net.first_capture_done": "Rete: prima cattura completata",
  "net.last_capture_done": "Rete: ultima cattura completata",
  "db.ms_page": "Database: SQL per pagina (somma)",
  "db.share_of_server": "Database: % del tempo server",
  "analysis.ms_page": "Analisi server per pagina (somma)",
  "images.top_done": `Foto analizzate dei migliori ${CFG.visionTop}`,
  "images.top_all_ms": `Foto dei migliori ${CFG.visionTop} analizzate (tempo)`,
  "images.page_done": "Foto analizzate (tutta la pagina)",
  "render.quick_painted": "Badge rapidi disegnati",
  "render.best_painted": "Migliore disegnata",
  "render.longtasks_ext_ms": "Task lunghi durante l'estensione (ms)",
  "render.longtasks_to_quick_ms": "Task lunghi fino ai verdetti (ms)",
  "render.longtask_max_ms": "Task lungo massimo (ms)",
};

function table(rows) {
  const w = Math.max(...rows.map((r) => r[0].length));
  return rows.map((r) => `${r[0].padEnd(w)}  ${String(r[1]).padStart(9)}  ${String(r[2]).padStart(15)}  ${String(r[3]).padStart(4)}`).join("\n");
}

// ------------------------------------------------------------------ main
(async () => {
  const started = new Date();
  if (!CFG.searches.length) throw new Error("SEARCHES is empty");
  const proxy = CFG.proxyPort ? await startProxy(CFG.proxyPort, CFG.app) : null;
  const extApp = proxy ? proxy.url : CFG.app;
  const gitHead = (() => {
    try {
      return execFileSync("git", ["rev-parse", "--short", "HEAD"], { cwd: SRC, encoding: "utf8" }).trim();
    } catch {
      return null;
    }
  })();

  // A fresh FlipFinder account and extension key (as Settings -> Browser extension would create).
  const api = await request.newContext({ baseURL: CFG.app });
  const reg = await api.post("/api/v1/auth/register", { data: { email: `real-speed-${Date.now()}@example.com`, password: "Real-speed-2026!" } });
  if (!reg.ok()) throw new Error("register: " + (await reg.text()));
  const session = await reg.json();
  const created = await api.post("/api/v1/extension/keys", { data: { name: "real-speed" }, headers: { "X-CSRF-Token": session.csrf_token } });
  const { key, id: keyId } = await created.json();
  if (!key) throw new Error("no key: " + (await created.text()));

  // Test copy of the extension with FlipFinder's address pre-granted (no permission prompt headless).
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ffext-"));
  fs.cpSync(SRC, dir, { recursive: true, filter: (p) => !p.includes(`${path.sep}node_modules`) });
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json")));
  manifest.host_permissions = [...new Set([`${extApp}/*`, `${CFG.app}/*`])];
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest));

  // Outbound proxy of this machine, if any (Chromium's own flag: loopback, i.e. FlipFinder and the
  // timing proxy, stays direct; Playwright's proxy option would force loopback through it).
  const browserProxy = CFG.savedPage ? "" : env.BROWSER_PROXY || env.HTTPS_PROXY || env.https_proxy || "";
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ffprof-"));
  const ctx = await chromium.launchPersistentContext(profile, {
    headless: false,
    executablePath: env.CHROME_PATH || undefined,
    args: ["--headless=new", `--disable-extensions-except=${dir}`, `--load-extension=${dir}`, "--no-sandbox", ...(browserProxy ? [`--proxy-server=${browserProxy}`] : [])],
    viewport: { width: 1366, height: 900 },
    locale: "it-IT",
  });
  if (CFG.savedPage) {
    const html = fs.readFileSync(CFG.savedPage, "utf8");
    await ctx.route(`${CFG.host}/**`, (r) => (r.request().resourceType() === "document" ? r.fulfill({ contentType: "text/html", body: html }) : r.abort()));
    await ctx.route("https://images1.vinted.net/**", (r) => r.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="310" height="430"/>' }));
  }
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent("serviceworker");
  const extId = new URL(sw.url()).host;

  // Pairing exactly as the options page does it.
  const opt = await ctx.newPage();
  await opt.goto(`chrome-extension://${extId}/src/options.html`);
  const paired = await opt.evaluate(({ appUrl, key }) => chrome.runtime.sendMessage({ type: "ff:pair", appUrl, key }), { appUrl: extApp, key });
  if (!paired || !paired.ok) throw new Error("pairing failed: " + JSON.stringify(paired));
  await opt.close();
  let summary = null;
  for (let i = 0; i < 60 && !summary; i += 1) {
    summary = await sw.evaluate(async () => {
      const m = (await chrome.storage.local.get("marketCache")).marketCache;
      return m ? { version: m.version, segments: Object.keys(m.segments || {}).length, models: Object.keys(m.models || {}).length, kb: Math.round(JSON.stringify(m).length / 1024) } : null;
    });
    if (!summary) await sleep(500);
  }
  if (!summary) throw new Error("market summary not downloaded");
  const extVersion = await sw.evaluate(() => chrome.runtime.getManifest().version);
  const browserVersion = ctx.browser() ? ctx.browser().version() : null;
  console.log(`FlipFinder ${CFG.app} (estensione via ${extApp}), commit ${gitHead || "?"}; riepilogo di mercato ${JSON.stringify(summary)}`);
  console.log(`${CFG.loads} caricamenti: ${CFG.searches.join(" | ")}${CFG.savedPage ? ` - PAGINA SALVATA ${CFG.savedPage}, non Vinted reale` : ""}`);

  // Requests before the first page (pairing, parser configuration, market summary): one-off.
  const setup = proxy ? proxy.log.map((r) => ({ ep: endpoint(r.method, r.url), status: r.status, ms: round(r.end - r.start), resKB: round(r.resBytes / 1024, 1), st: parseServerTiming(r.serverTiming) })) : [];
  await ctx.addInitScript(instrument);
  const page = await ctx.newPage();
  const loads = [];
  let refused = null;
  for (let i = 0; i < CFG.loads; i += 1) {
    const search = CFG.searches[i % CFG.searches.length];
    const l = await measureLoad(page, search, i, Math.floor(i / CFG.searches.length), proxy);
    if (l.refused) {
      refused = `caricamento ${i + 1} ("${search}"): ${l.refused}`;
      console.log(`#${i + 1} "${search}": Vinted ha rifiutato la pagina (${l.refused}). Misura interrotta: nessun tentativo di aggiramento.`);
      await page.goto("about:blank").catch(() => null); // stop the refused page's own retries
      break;
    }
    loads.push(l);
    const m = metricsOf(l);
    const f = (k) => (m[k] === null || m[k] === undefined ? "-" : Math.round(m[k]));
    console.log(
      `#${i + 1} "${search}" (${l.visit}, http ${l.http}, carico ${l.loadavg[0]}): schede ${f("page.cards")}, DOM ${f("page.dom_interactive")}, ` +
        `migliore ${f("ext.best")} (da DOM ${f("ext.best_from_di")}), rapido su ${f("ext.quick_cards")}+${f("ext.quick_other_cards")} in ${f("ext.quick_all")} (da DOM ${f("ext.quick_all_from_di")}), ` +
        `server ${f("server.cards")} in ${f("server.all")}, catture ${f("net.capture_requests")} (${f("net.capture_ms_sum")} ms, db ${f("db.ms_page")} ms, analisi ${f("analysis.ms_page")} ms), ` +
        `foto ${l.vision ? `${l.vision.done}/${l.vision.top.length}` : "n/m"}`,
    );
    if (i + 1 < CFG.loads) await sleep(CFG.pauseMs);
  }

  // ---------------------------------------------------------------- summary
  const per = loads.map(metricsOf);
  const keys = Object.keys(LABELS);
  const summaryAll = Object.fromEntries(keys.map((k) => [k, stats(per.map((m) => m[k]))]));
  const split = (visit) => {
    const sel = per.filter((_, i) => loads[i].visit === visit);
    return Object.fromEntries(["ext.best", "ext.best_from_di", "ext.quick_all", "ext.quick_all_from_di", "server.first", "server.all", "db.ms_page", "net.capture_ms_sum"].map((k) => [k, stats(sel.map((m) => m[k]))]));
  };
  const endpoints = endpointStats(loads);
  let dbState = null;
  if (CFG.dbUrl) {
    const rows = await psql(
      `SELECT count(*) FILTER (WHERE raw->>'synthetic' IS NULL AND provider = 'vinted'), count(*) FILTER (WHERE raw->>'synthetic' IS NULL AND identification ? 'vision'), count(*) FILTER (WHERE raw->>'synthetic' = 'true') FROM listings`,
    );
    if (rows && rows[0]) dbState = { real_listings: Number(rows[0][0]), real_with_vision: Number(rows[0][1]), synthetic_listings: Number(rows[0][2]) };
  }
  const result = {
    label: CFG.label || null,
    started: started.toISOString(),
    source: CFG.savedPage ? `saved page ${CFG.savedPage}` : CFG.host,
    searches: CFG.searches,
    loads: loads.length,
    refused,
    git: gitHead,
    extension: extVersion,
    browser: browserVersion,
    market_summary: summary,
    setup_requests: setup,
    server_timing_seen: loads.some((l) => (l.requests || []).some((r) => r.st && r.st.total !== undefined)),
    metrics: summaryAll,
    by_visit: { first: split("prima visita"), repeat: split("visita ripetuta") },
    endpoints,
    vision_top: loads.map((l) => l.vision && { done: l.vision.done, of: l.vision.top.length }),
    db_state: dbState,
    machine: { cpus: os.cpus().length, loadavg_runs: stats(loads.map((l) => l.loadavg[0])) },
  };

  const fmt = (s) => (s.n ? [s.median, `${s.min}–${s.max}`, s.n] : ["-", "-", 0]);
  console.log("\n=== Mediana e intervallo su tutti i caricamenti (ms da inizio navigazione, salvo indicato) ===");
  console.log(table([["metrica", "mediana", "min–max", "n"], ...keys.map((k) => [LABELS[k], ...fmt(summaryAll[k])])]));
  for (const [name, part] of [["prima visita di una ricerca", result.by_visit.first], ["visite ripetute", result.by_visit.repeat]]) {
    console.log(`\n--- ${name} ---`);
    console.log(table(Object.entries(part).map(([k, s]) => [LABELS[k], ...fmt(s)])));
  }
  console.log("\n=== Richieste dell'estensione a FlipFinder (per endpoint, ms) ===");
  console.log(
    table([
      ["endpoint (n)", "durata", "db", "analisi"],
      ...Object.entries(endpoints).map(([ep, e]) => [`${ep} (${e.count}${e.errors ? `, ${e.errors} errori` : ""})`, e.ms.median ?? "-", e.db.median ?? "-", e.analysis.median ?? "-"]),
    ]),
  );
  if (setup.length) console.log("\nRichieste una tantum (associazione, configurazione, riepilogo di mercato):", setup.map((r) => `${r.ep} ${r.ms} ms (db ${r.st.db ?? "-"})`).join(", "));
  if (refused) console.log(`\nMisura interrotta da Vinted al ${refused}: i numeri valgono per i ${loads.length} caricamenti completati.`);
  if (dbState) console.log("\nDatabase:", JSON.stringify(dbState));
  if (CFG.out) {
    fs.writeFileSync(CFG.out, JSON.stringify({ result, loads }, null, 1));
    console.log("dettagli:", CFG.out);
  }
  console.log("RESULT", JSON.stringify(result));

  await ctx.close();
  await api.delete(`/api/v1/extension/keys/${keyId}`, { headers: { "X-CSRF-Token": session.csrf_token } }).catch(() => null);
  await api.dispose();
  if (proxy) await proxy.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
