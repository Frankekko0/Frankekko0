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

test("ranking export is spreadsheet-safe", () => {
  const csv = K.rankingCsv([ev("1", { title: '=HYPERLINK("x")', reason: 'a; "b"' }), ev("2", { flip_score: null })]);
  assert.ok(csv.startsWith("﻿posizione;vinted_id"));
  const lines = csv.split("\r\n");
  assert.ok(lines[1].includes(`"'=HYPERLINK(""x"")"`));
  assert.ok(lines[1].includes('"a; ""b"""'));
  assert.ok(lines[2].includes("dati insufficienti"));
});
