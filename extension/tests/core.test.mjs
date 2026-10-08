// Run with: node --test extension/tests
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const K = createRequire(import.meta.url)("../src/core.js");

test("options are normalized and clamped", () => {
  const o = K.normalizeOptions({ minScore: 140, autoDeepPerHour: 99, budget: "", brands: 5, enabled: "yes" });
  assert.equal(o.minScore, 100);
  assert.equal(o.autoDeepPerHour, 20); // never above the hard cap
  assert.equal(o.budget, null);
  assert.equal(o.brands, "");
  assert.equal(o.enabled, true);
  assert.equal(o.autoDeep, false); // slow automatic reads are off unless chosen
  assert.equal(o.slowRefresh, false);
  assert.equal(K.normalizeAppUrl("flip.example.com/"), "https://flip.example.com");
  assert.equal(K.normalizeAppUrl("http://192.168.1.5:3000/"), "http://192.168.1.5:3000");
  assert.throws(() => K.normalizeAppUrl("javascript:alert(1)"));
});

test("queue: one entry per Vinted ID, latest capture wins, batches by page type", () => {
  const q = K.emptyQueue();
  K.queueAdd(q, "cards", [{ vid: "1", payload: { price: 10 }, pageType: "catalog" }, { vid: "2", payload: { price: 20 }, pageType: "closet" }], 1000);
  K.queueAdd(q, "cards", [{ vid: "1", payload: { price: 9 }, pageType: "catalog" }], 1100);
  assert.equal(K.queueSize(q), 2);
  assert.equal(q.cards["1"].payload.price, 9);
  assert.equal(q.cards["1"].addedAt, 1000);
  const batch = K.queueTake(q, "cards", 2000, 40);
  assert.equal(batch.pageType, "closet"); // oldest sequence first: vid 2
  assert.deepEqual(batch.entries.map((e) => e.vid), ["2"]);
  // A newer capture arriving while a batch is in flight is not lost when that batch completes.
  const inflight = K.queueTake(q, "cards", 2000, 40);
  K.queueDone(q, "cards", batch.entries);
  K.queueAdd(q, "cards", [{ vid: "1", payload: { price: 8 }, pageType: "catalog" }], 2100);
  const next = K.queueTake(q, "cards", 2200, 40);
  K.queueDone(q, "cards", [inflight.entries[0]].filter((e) => e.vid === "1"));
  assert.equal(q.cards["1"].payload.price, 8);
  assert.ok(next.entries.length >= 1);
});

test("queue: retry with exponential backoff, capped; stale entries pruned", () => {
  const q = K.emptyQueue();
  K.queueAdd(q, "items", [{ vid: "7", payload: {}, pageType: "item" }], 0);
  const b = K.queueTake(q, "items", 0, 1);
  K.queueFail(q, "items", b.entries, 0);
  assert.equal(q.items["7"].nextAt, 5000);
  assert.equal(K.queueTake(q, "items", 4000, 1), null); // not due yet
  K.queueFail(q, "items", K.queueTake(q, "items", 5000, 1).entries, 5000);
  assert.equal(q.items["7"].nextAt, 15000);
  assert.equal(K.backoffMs(30), 10 * 60 * 1000);
  K.queueFail(q, "items", K.queueTake(q, "items", 15000, 1).entries, 15000, 60000); // Retry-After wins
  assert.equal(q.items["7"].nextAt, 75000);
  assert.equal(K.queueNextAt(q), 75000);
  assert.equal(K.queuePrune(q, 25 * 3600 * 1000), 1);
  assert.equal(K.queueSize(q), 0);
});

test("pacing: on command at most one read every 4 s; automatic ones slow and capped", () => {
  const opts = { autoDeepPerHour: 3 };
  let s = K.emptyPacing();
  const t0 = 10_000_000;
  assert.equal(K.pacingCheck(s, "manual", t0, opts).ok, true);
  s = K.pacingRecord(s, "manual", t0);
  assert.equal(K.pacingCheck(s, "manual", t0 + 1000, opts).ok, false);
  assert.equal(K.pacingCheck(s, "manual", t0 + 4000, opts).ok, true);
  assert.equal(K.pacingCheck(s, "auto", t0 + 10_000, opts).reason, "gap"); // 30 s between automatic reads
  let t = t0;
  for (let i = 0; i < 3; i += 1) {
    t += 31_000;
    assert.equal(K.pacingCheck(s, "auto", t, opts).ok, true, `auto ${i}`);
    s = K.pacingRecord(s, "auto", t);
  }
  assert.equal(K.pacingCheck(s, "auto", t + 31_000, opts).reason, "hourly"); // the user's hourly cap
  assert.equal(K.pacingCheck(s, "refresh", t + 61_000, opts).ok, true); // refresh has its own (shared) cap
  assert.equal(K.pacingCheck(s, "refresh", t + 30_000, opts).reason, "gap"); // 60 s between status checks
  assert.equal(K.pacingCheck(s, "auto", t0 + 3_700_000, opts).ok, true); // a new hour
  const paused = K.pacingPause(s, t, "HTTP 403");
  const check = K.pacingCheck(paused, "manual", t + 60_000, opts);
  assert.deepEqual([check.ok, check.reason], [false, "paused"]); // even on command: no workaround
  assert.ok(check.waitMs > 5 * 3600 * 1000);
});

test("refusals are recognized, never retried around", () => {
  assert.equal(K.isRefusal(403, ""), true);
  assert.equal(K.isRefusal(429, ""), true);
  assert.equal(K.isRefusal(200, "<html><script src='https://geo.captcha-delivery.com/x.js'>"), true);
  assert.equal(K.isRefusal(200, "<html><h1>Felpa</h1>"), false);
  assert.equal(K.isRefusal(404, "datadome"), false);
});

const ev = (id, over = {}) => ({
  vinted_id: id,
  title: `Polo ${id}`,
  brand: "Ralph Lauren",
  size: "M",
  status: "active",
  price: 10,
  total_cost: 13,
  net_margin: 12,
  flip_score: 70,
  fake_risk: "none",
  seenAt: Number(id),
  ...over,
});

test("live ranking: filters, best first, insufficient data never outranks a score", () => {
  const evals = [
    ev("1", { flip_score: 60 }),
    ev("2", { flip_score: 90, net_margin: 5 }),
    ev("3", { flip_score: 90, net_margin: 20 }),
    ev("4", { flip_score: null, net_margin: null }),
    ev("5", { flip_score: 95, fake_risk: "high" }),
    ev("6", { flip_score: 99, total_cost: 80 }),
    ev("7", { flip_score: 99, size: "XL" }),
    ev("8", { flip_score: 99, status: "sold" }),
    ev("9", { flip_score: 99, brand: "Nike", title: "Nike Air" }),
  ];
  const f = { budget: 50, minMargin: 0, brands: "ralph lauren", sizes: "M, L", excludeFakeRisk: true };
  assert.deepEqual(K.rank(evals, f, 5).map((e) => e.vinted_id), ["3", "2", "1", "4"]);
  assert.deepEqual(K.rank(evals, { ...f, minMargin: 10 }, 5).map((e) => e.vinted_id), ["3", "1", "4"]);
  assert.equal(K.isHot(ev("3", { flip_score: 90, net_margin: 20 }), { minScore: 80, minMargin: 15 }), true);
  assert.equal(K.isHot(ev("4", { flip_score: null }), { minScore: 0, minMargin: -100 }), false);
});

test("live ranking: risk-adjusted profit decides among scored items", () => {
  const evals = [
    ev("1", { flip_score: 95, net_margin: 40, risk_adjusted_profit: 6 }), // likely fake: low adjusted profit
    ev("2", { flip_score: 70, net_margin: 15, risk_adjusted_profit: 12 }),
    ev("3", { flip_score: 99, net_margin: 30 }), // no probabilities yet: after the ones that have them
    ev("4", { flip_score: 60, net_margin: -5, risk_adjusted_profit: -5 }),
  ];
  const f = { minMargin: null };
  assert.deepEqual(K.rank(evals, f, 5).map((e) => e.vinted_id), ["2", "1", "4", "3"]);
});

test("ranking export is spreadsheet-safe", () => {
  const csv = K.rankingCsv([ev("1", { title: '=HYPERLINK("x")', reason: 'a; "b"' }), ev("2", { flip_score: null })]);
  assert.ok(csv.startsWith("﻿posizione;vinted_id"));
  const lines = csv.split("\r\n");
  assert.ok(lines[1].includes(`"'=HYPERLINK(""x"")"`));
  assert.ok(lines[1].includes('"a; ""b"""'));
  assert.ok(lines[2].includes("dati insufficienti"));
});

test("scanner options are off by default and clamped", () => {
  const o = K.normalizeOptions({});
  assert.equal(o.scanEnabled, false);
  assert.equal(o.scanIntervalMin, 15);
  assert.equal(o.scanNotify, true);
  assert.equal(K.normalizeOptions({ scanIntervalMin: 1 }).scanIntervalMin, 10); // never faster than every 10 minutes
  assert.equal(K.normalizeOptions({ scanIntervalMin: 9999 }).scanIntervalMin, 240);
  assert.equal(K.normalizeOptions({ scanEnabled: "yes" }).scanEnabled, false);
});

test("scanner: only Vinted search pages are accepted, cleaned and sorted newest first", () => {
  const ok = K.normalizeSearchUrl("https://www.vinted.it/catalog?search_text=polo+ralph+lauren&page=3&time=17&search_id=9&referrer=catalog#x");
  assert.equal(ok.error, undefined);
  assert.equal(ok.origin, "https://www.vinted.it");
  assert.equal(ok.name, "polo ralph lauren");
  const u = new URL(ok.url);
  assert.equal(u.searchParams.get("order"), "newest_first");
  assert.equal(u.searchParams.get("search_text"), "polo ralph lauren");
  for (const p of ["page", "time", "search_id", "referrer"]) assert.equal(u.searchParams.has(p), false);
  assert.equal(u.hash, "");
  // The same search typed differently is the same search; an order chosen by the user is kept.
  assert.equal(K.normalizeSearchUrl("https://www.vinted.it/catalog?search_text=polo+ralph+lauren&page=1").key, ok.key);
  assert.equal(new URL(K.normalizeSearchUrl("https://www.vinted.it/catalog?search_text=a&order=price_low_to_high").url).searchParams.get("order"), "price_low_to_high");
  assert.equal(K.normalizeSearchUrl("https://www.vinted.it/catalog/1904-t-shirts").name, "t shirts");
  for (const bad of ["", "not a url", "http://www.vinted.it/catalog?q=1", "https://example.com/catalog?q=1", "https://www.vinted.it.evil.com/catalog", "https://www.vinted.it/items/123-x", "https://www.vinted.it/member/1-x", "javascript:alert(1)"]) {
    assert.ok(K.normalizeSearchUrl(bad).error, `must reject ${bad}`);
  }
});

test("scanner pacing: a minute apart, 30 an hour, 300 a day, and a 6-hour pause", () => {
  let s = K.emptyScanPacing();
  const t0 = 10_000_000;
  assert.equal(K.scanPacingCheck(s, t0).ok, true);
  s = K.scanPacingRecord(s, t0);
  const soon = K.scanPacingCheck(s, t0 + 30_000);
  assert.deepEqual([soon.ok, soon.reason, soon.waitMs], [false, "gap", 30_000]);
  assert.equal(K.scanPacingCheck(s, t0 + 60_000).ok, true);
  // 30 reads in the hour, a minute apart: the 31st waits for the hour to end.
  let t = t0;
  for (let i = 1; i < 30; i += 1) {
    t += 60_000;
    assert.equal(K.scanPacingCheck(s, t).ok, true, `read ${i + 1}`);
    s = K.scanPacingRecord(s, t);
  }
  const hourly = K.scanPacingCheck(s, t + 60_000);
  assert.equal(hourly.ok, false);
  assert.equal(hourly.reason, "hourly");
  assert.equal(K.scanPacingCheck(s, t0 + 3_600_000 + 1).ok, true);
  // The daily cap holds across hours.
  let d = { ...K.emptyScanPacing(), dayStart: t0, dayCount: K.SCAN.perDay, lastAt: t0, hourStart: t0, hourCount: 0 };
  const daily = K.scanPacingCheck(d, t0 + 7_200_000);
  assert.deepEqual([daily.ok, daily.reason], [false, "daily"]);
  assert.equal(K.scanPacingCheck(d, t0 + 86_400_000 + 1).ok, true);
  // A refusal pauses for 6 hours.
  const paused = K.scanPacingPause(s, t0, "403");
  const check = K.scanPacingCheck(paused, t0 + 3_600_000);
  assert.deepEqual([check.ok, check.reason], [false, "paused"]);
  assert.equal(check.waitMs, 5 * 3_600_000);
  assert.equal(K.scanPacingCheck(paused, t0 + 6 * 3_600_000 + 1).ok, true);
});

test("scanner: the interval stretches so that all the searches stay within the caps", () => {
  assert.equal(K.scanIntervalMin(1, 15), 15);
  assert.equal(K.scanIntervalMin(3, 15), 15); // 3 x 96 reads a day = 288 <= 300
  assert.equal(K.scanIntervalMin(4, 15), 20); // 4 x 96 = 384 > 300 -> every 20 min (288 a day)
  assert.equal(K.scanIntervalMin(10, 10), 48); // 10 searches: 300 a day at most
  for (let n = 1; n <= K.SCAN.maxSearches; n += 1) {
    const every = K.scanIntervalMin(n, 10);
    assert.ok((n * 1440) / every <= K.SCAN.perDay + 1e-9, `daily cap with ${n} searches`);
    assert.ok((n * 60) / every <= K.SCAN.perHour + 1e-9, `hourly cap with ${n} searches`);
  }
});

test("scanner: the longest unread search goes first; suspended and failing ones wait", () => {
  const now = 100 * 60_000;
  const searches = [
    { id: "a", lastScanAt: now - 20 * 60_000 },
    { id: "b", lastScanAt: now - 50 * 60_000 },
    { id: "c", lastScanAt: 0, enabled: false },
    { id: "d", lastScanAt: now - 5 * 60_000 },
    { id: "e", lastScanAt: now - 40 * 60_000, failures: 2 }, // waits 3 intervals (45 min)
  ];
  assert.deepEqual(K.scanPickDue(searches, now, 15).map((s) => s.id), ["b", "a"]);
  assert.deepEqual(K.scanPickDue([{ id: "x" }], now, 15).map((s) => s.id), ["x"]); // never read: due at once
  assert.deepEqual(K.scanPickDue([], now, 15), []);
});

test("scanner: new items are the ones not seen before, remembered newest first and bounded", () => {
  const first = K.scanDiff([], ["3", "2", "1", "2"]);
  assert.deepEqual(first.fresh, ["3", "2", "1"]);
  const next = K.scanDiff(first.seen, ["5", "4", "3", "2"]);
  assert.deepEqual(next.fresh, ["5", "4"]);
  assert.deepEqual(next.seen, ["5", "4", "3", "2", "1"]);
  const many = K.scanDiff([], Array.from({ length: 1000 }, (_, i) => String(i)));
  assert.equal(many.seen.length, K.SCAN.maxSeen);
});

test("scanner: only new items above the thresholds raise a notification, best first, at most three", () => {
  const opts = K.normalizeOptions({ minScore: 70, minMargin: 10 });
  const watch = { 1: {}, 2: {}, 3: {}, 4: {}, 5: {}, 6: {} };
  const evals = [
    ev("1", { flip_score: 90, net_margin: 30, risk_adjusted_profit: 20 }),
    ev("2", { flip_score: 90, net_margin: 40, risk_adjusted_profit: 35 }),
    ev("3", { flip_score: 50, net_margin: 40 }), // score too low
    ev("4", { flip_score: 95, net_margin: 3 }), // margin too low
    ev("5", { flip_score: 80, net_margin: 25, risk_adjusted_profit: 10 }),
    ev("6", { flip_score: 80, net_margin: 22, risk_adjusted_profit: 9 }),
    ev("7", { flip_score: 99, net_margin: 99, risk_adjusted_profit: 99 }), // not new: not in the watch list
    ev("8", { flip_score: null }), // insufficient data
  ];
  assert.deepEqual(K.scanHotPick(evals, watch, opts).map((e) => e.vinted_id), ["2", "1", "5"]);
  assert.deepEqual(K.scanHotPick(evals, {}, opts), []);
  assert.deepEqual(K.scanHotPick([ev("1", { flip_score: 90, net_margin: 30, fake_risk: "high" })], watch, opts), []); // fake risk excluded
});
