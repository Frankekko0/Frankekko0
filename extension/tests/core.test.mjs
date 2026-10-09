// Run with: node --test extension/tests
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const K = createRequire(import.meta.url)("../src/core.js");

test("options are normalized and clamped", () => {
  const o = K.normalizeOptions({ minScore: 140, budget: "", brands: 5, enabled: "yes" });
  assert.equal(o.minScore, 100);
  assert.equal(o.budget, null);
  assert.equal(o.brands, "");
  assert.equal(o.enabled, true);
  assert.equal(K.normalizeAppUrl("flip.example.com/"), "https://flip.example.com");
  assert.equal(K.normalizeAppUrl("http://192.168.1.5:3000/"), "http://192.168.1.5:3000");
  assert.throws(() => K.normalizeAppUrl("javascript:alert(1)"));
});

test("there are no options for automatic reads: nothing to switch on", () => {
  const keys = Object.keys(K.DEFAULT_OPTIONS).concat(Object.keys(K.normalizeOptions({})));
  assert.deepEqual(keys.filter((k) => /scan|deep|slow|refresh/i.test(k)), []);
  // Old saved options with those keys are simply ignored.
  const old = K.normalizeOptions({ scanEnabled: true, autoDeep: true, slowRefresh: true });
  assert.equal("scanEnabled" in old || "autoDeep" in old || "slowRefresh" in old, false);
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


// ------------------------------------------------------------------ photos (decision Q3-B)
test("photo jobs: only what the server asked for, that the opened page shows, on Vinted's photo host", () => {
  const PAGE = [
    "https://images1.vinted.net/t/01_aaa/f800/1.jpeg?s=sig1",
    "https://images1.vinted.net/t/01_aaa/f800/2.jpeg?s=sig2",
    "https://images1.vinted.net/t/01_aaa/f800/3.jpeg",
  ];
  const wanted = [
    { image_key: "bbbbbbbbbbbbbbbb", position: 1, url: "https://images2.vinted.net/t/01_aaa/f800/2.jpeg" }, // another host: not the file the page shows
    { image_key: "aaaaaaaaaaaaaaaa", position: 0, url: "https://images1.vinted.net/t/01_aaa/f800/1.jpeg?s=other" },
    { image_key: "cccccccccccccccc", position: 2, url: "https://evil.example/t/01_aaa/f800/3.jpeg" }, // not the photo host
    { image_key: "dddddddddddddddd", position: 3, url: "https://images1.vinted.net/t/other/f800/9.jpeg" }, // not on the page
    { image_key: "aaaaaaaaaaaaaaaa", position: 0, url: "https://images1.vinted.net/t/01_aaa/f800/1.jpeg" }, // repeated
    { image_key: "short", position: 4, url: "https://images1.vinted.net/t/01_aaa/f800/3.jpeg" }, // malformed key
    { image_key: "eeeeeeeeeeeeeeee", position: 5, url: "http://images1.vinted.net/t/01_aaa/f800/3.jpeg" }, // not https
  ];
  // Matching is by file (host + path): the signature changes, the file does not.
  const jobs = K.photoJobs(wanted, PAGE);
  assert.deepEqual(jobs.map((j) => j.key), ["aaaaaaaaaaaaaaaa"]);
  assert.deepEqual(K.photoJobs([], PAGE), []);
  assert.deepEqual(K.photoJobs(null, PAGE), []);
  assert.deepEqual(K.photoJobs(wanted, []), []); // a page that shows no photo authorises none
});

test("the photo host check refuses look-alikes and credentials in the address", () => {
  assert.equal(K.isPhotoUrl("https://images1.vinted.net/t/a.jpeg"), true);
  assert.equal(K.isPhotoUrl("https://vinted.net.evil.example/t/a.jpeg"), false);
  assert.equal(K.isPhotoUrl("https://evilvinted.net/t/a.jpeg"), false);
  assert.equal(K.isPhotoUrl("https://user:pw@images1.vinted.net/t/a.jpeg"), false);
  assert.equal(K.isPhotoUrl("http://images1.vinted.net/t/a.jpeg"), false);
  assert.equal(K.isPhotoUrl("not a url"), false);
});

test("sending photos is off until the user turns it on", () => {
  assert.equal(K.normalizeOptions({}).uploadPhotos, false);
  assert.equal(K.normalizeOptions({ uploadPhotos: true }).uploadPhotos, true);
  assert.equal(K.normalizeOptions({ uploadPhotos: "yes" }).uploadPhotos, false);
});
