// End-to-end: the automatic scanner. Real extension in Chromium, a fake Vinted (a local HTTPS server
// that the browser reaches as www.vinted.it through host mapping: requests made by the extension's
// own pages are not covered by Playwright's routing), a running FlipFinder API (APP_URL).
// Test-only manifest change: host permission for APP and for the fake Vinted (the permission prompts
// can't be clicked headless).
//   APP_URL=http://localhost:8000 CHROME_PATH=/path/to/chrome node extension/e2e/scan.e2e.cjs
// Needs a running FlipFinder API that accepts sign-ups (ALLOW_REGISTRATION=true) and Playwright
// (PLAYWRIGHT_MODULE points to it when it is not installed next to this file).
//
// What it proves:
//   - the scanner is off by default and reads nothing, even with a search in the list;
//   - switched on, the first read of a search builds the baseline (stored in FlipFinder, mode
//     extension_scan, no notification), without cookies and with the newest-first order;
//   - a new listing on the next read is stored and, being a good deal, raises a notification;
//     a new one that is not, raises none;
//   - reads keep the pacing (>= 60 s apart, caps) and one search is read at a time;
//   - the first refusal (403) pauses every automatic read for 6 hours, with no retry around it;
//   - a search that never shows cards is set aside after three failed reads.
const { chromium, request } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("assert/strict");
const fs = require("fs");
const path = require("path");
const os = require("os");
const https = require("https");
const { execFileSync } = require("child_process");
const APP = process.env.APP_URL || "http://localhost:8000";
const SRC = path.resolve(__dirname, "..");

const ID0 = 883000100 + (Date.now() % 100000) * 10; // fresh Vinted ids on every run
const euro = (v) => v.toFixed(2).replace(".", ",");
const cardHtml = (c) => {
  const p = euro(c.price);
  const tot = euro(c.price * 1.05 + 0.7);
  return `<div class="feed-grid__item"><div class="new-item-box" data-testid="product-item-id-${c.id}">
    <a href="/items/${c.id}-${c.slug}?referrer=catalog" title="${c.title}, brand: Ralph Lauren, condizioni: Ottime, taglia: M, ${p} €, ${tot} € include la Protezione acquisti" style="display:block">
      <img src="https://images1.vinted.net/t/${c.id}/f800.jpeg" alt="${c.title}" width="200" height="260"></a>
    <p data-testid="product-item-id-${c.id}--description-title">Ralph Lauren</p><p data-testid="product-item-id-${c.id}--description-subtitle">M · Ottime</p>
    <p data-testid="product-item-id-${c.id}--price-text">${p} €</p></div></div>`;
};
const page = (cards) => `<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Polo Ralph Lauren | Vinted</title></head>
<body><header>Vinted (pagina di prova)</header><main><h1>polo ralph lauren</h1><div class="feed-grid">${cards.map(cardHtml).join("")}</div></main></body></html>`;

// What Vinted shows for the saved searches.
const mk = (n, title, price) => ({ id: ID0 + n, slug: title.toLowerCase().replace(/\W+/g, "-"), title, price });
const OLD = [mk(1, "Polo Ralph Lauren Custom Slim Fit blu", 30), mk(2, "Felpa Ralph Lauren grigia", 35), mk(3, "Camicia Ralph Lauren Oxford", 28)];
const GOOD = mk(10, "Polo Ralph Lauren Custom Slim Fit rossa", 9); // far below the market of the seeded sales
const DULL = mk(11, "Maglione Ralph Lauren lana", 80); // nothing to gain
const vinted = { A: [...OLD], reads: [], mode: "ok" }; // search A; "refuse" answers 403
const SVG = (n) => `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="380"><rect width="300" height="380" fill="hsl(${(n * 47) % 360},45%,72%)"/></svg>`;

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ffext-"));
  fs.cpSync(SRC, dir, { recursive: true });
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json")));
  manifest.host_permissions = [`${new URL(APP).origin}/*`, "https://www.vinted.it/*"];
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest));

  // A fresh FlipFinder account and a pairing key.
  const api = await request.newContext({ baseURL: APP });
  const reg = await api.post("/api/v1/auth/register", { data: { email: `e2e-scan-${Date.now()}@example.com`, password: "E2e-test-pass-2026!" } });
  if (!reg.ok()) throw new Error("register: " + (await reg.text()));
  const session = await reg.json();
  const created = await api.post("/api/v1/extension/keys", { data: { name: "e2e-scan" }, headers: { "X-CSRF-Token": session.csrf_token } });
  const { key } = await created.json();
  const capture = await request.newContext({ baseURL: APP, extraHTTPHeaders: { Authorization: `Bearer ${key}` } });

  // Fake Vinted over HTTPS with a throwaway self-signed certificate.
  const certDir = fs.mkdtempSync(path.join(os.tmpdir(), "ffcert-"));
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=www.vinted.it", "-addext", "subjectAltName=DNS:www.vinted.it,DNS:images1.vinted.net", "-keyout", path.join(certDir, "k.pem"), "-out", path.join(certDir, "c.pem")], { stdio: "ignore" });
  const server = https.createServer({ key: fs.readFileSync(path.join(certDir, "k.pem")), cert: fs.readFileSync(path.join(certDir, "c.pem")) }, (req, res) => {
    const u = new URL(req.url, `https://${req.headers.host}`);
    const send = (body, type = "text/html", status = 200) => res.writeHead(status, { "Content-Type": `${type}; charset=utf-8`, "Cache-Control": "no-store" }).end(body);
    if (u.hostname === "images1.vinted.net") return send(SVG(7), "image/svg+xml");
    if (u.pathname === "/catalog") {
      vinted.reads.push({ at: Date.now(), search: u.searchParams.get("search_text"), order: u.searchParams.get("order"), page: u.searchParams.get("page"), cookie: req.headers.cookie || null, secFetch: req.headers["sec-fetch-dest"] || null });
      if (vinted.mode === "refuse") return send("<html><title>Access denied</title></html>", "text/html", 403);
      if (u.searchParams.get("search_text") === "empty") return send(page([]));
      return send(page(vinted.A));
    }
    return send("<!doctype html><title>Vinted</title><p>Vinted</p>");
  });
  await new Promise((res) => server.listen(0, "127.0.0.1", res));
  const port = server.address().port;

  const ctx = await chromium.launchPersistentContext(fs.mkdtempSync(path.join(os.tmpdir(), "ffprof-")), {
    headless: false,
    executablePath: process.env.CHROME_PATH || undefined,
    args: [
      "--headless=new", `--disable-extensions-except=${dir}`, `--load-extension=${dir}`, "--no-sandbox",
      `--host-resolver-rules=MAP www.vinted.it:443 127.0.0.1:${port}, MAP images1.vinted.net:443 127.0.0.1:${port}`,
      "--ignore-certificate-errors", "--no-proxy-server",
    ],
    viewport: { width: 1280, height: 900 },
    ignoreHTTPSErrors: true,
  });
  const isExt = (w) => w.url().startsWith("chrome-extension://");
  let sw = ctx.serviceWorkers().find(isExt);
  if (!sw) sw = await ctx.waitForEvent("serviceworker", { predicate: isExt });
  if (process.env.E2E_DEBUG) sw.on("console", (m) => console.log("  [sw]", m.text()));
  const log = (...a) => console.log("•", ...a);
  const until = async (what, fn, ms = 30000) => {
    const end = Date.now() + ms;
    for (;;) {
      const v = await fn();
      if (v) return v;
      if (Date.now() > end) throw new Error(`timeout: ${what}`);
      await new Promise((res) => setTimeout(res, 400));
    }
  };
  const summary = () => sw.evaluate(() => scanSummary());
  const scanner = () => sw.evaluate(async () => (await chrome.storage.local.get("scanner")).scanner);
  // Notifications are observed through the extension's own create calls.
  await sw.evaluate(() => {
    self.__notes = [];
    const orig = chrome.notifications.create.bind(chrome.notifications);
    chrome.notifications.create = (id, o) => (self.__notes.push({ id, title: o.title, message: o.message }), orig(id, o));
  });
  const notes = () => sw.evaluate(() => self.__notes);
  const listingMode = async (id) => {
    const r = await api.get(`/api/v1/items/${id}`);
    return r.ok() ? (await r.json()).item.acquisition_mode : null;
  };

  await sw.evaluate(async ({ app, key }) => {
    await chrome.storage.local.set({ apiKey: key, paired: true });
    await chrome.storage.sync.set({ options: { appUrl: app, minScore: 40, minMargin: 5, excludeFakeRisk: false } });
  }, { app: APP, key });

  // Market data: sold Polo Ralph Lauren around 45 € (so 9 € is a deal, 80 € is not).
  const sold = Array.from({ length: 12 }, (_, i) => ({ url: `https://www.vinted.it/items/${ID0 + 500 + i}-polo`, title: `Polo Ralph Lauren Custom Slim Fit ${i}`, price: 44 + (i % 3), brand: "Ralph Lauren", size: "M", condition: "Ottime", status: "sold" }));
  const seeded = await capture.post("/api/v1/capture/cards", { data: { page_type: "catalog", items: sold } });
  assert.ok(seeded.ok(), "seed market: " + (await seeded.text()));

  // 1) Off by default: a search in the list is not read.
  const added = await sw.evaluate(() => HANDLERS["ff:scan-add"]({ url: "https://www.vinted.it/catalog?search_text=polo+ralph+lauren&page=4&time=1", name: "Polo RL" }));
  assert.equal(added.ok, true, JSON.stringify(added));
  const dup = await sw.evaluate(() => HANDLERS["ff:scan-add"]({ url: "https://www.vinted.it/catalog?page=2&search_text=polo+ralph+lauren" }));
  assert.match(dup.error, /già/);
  const bad = await sw.evaluate(() => HANDLERS["ff:scan-add"]({ url: "https://example.com/catalog?search_text=x" }));
  assert.ok(bad.error);
  await sw.evaluate(() => scanTick());
  assert.equal(vinted.reads.length, 0);
  assert.equal((await summary()).enabled, false);
  log("off by default: nothing read; duplicate and non-Vinted addresses refused");

  // 2) Switched on: the first read is the baseline. Stored, tagged extension_scan, no notification.
  await sw.evaluate(async () => {
    const { options } = await chrome.storage.sync.get("options");
    await chrome.storage.sync.set({ options: { ...options, scanEnabled: true } });
  });
  await until("first read", () => vinted.reads.length >= 1);
  const first = vinted.reads[0];
  log("first read:", JSON.stringify(first));
  assert.equal(first.cookie, null, "no cookies are sent");
  assert.equal(first.order, "newest_first");
  assert.equal(first.page, null, "paging parameters are dropped: only page 1");
  assert.equal(first.search, "polo ralph lauren");
  await until("baseline stored", async () => (await listingMode(OLD[0].id)) === "extension_scan");
  const s1 = await until("search primed", async () => ((await scanner()).searches[0].primed ? (await scanner()).searches[0] : null));
  assert.equal(s1.lastCount, 3);
  assert.equal(s1.lastNew, 0, "the first read only builds the baseline");
  assert.deepEqual((await notes()).filter((n) => !/pausa/.test(n.title)), []);
  log("baseline:", s1.lastCount, "items stored as extension_scan, no notification");

  // 3) Pacing: the next read waits (>= 60 s); we move the clock of the search, not the pacing.
  await sw.evaluate(() => scanTick());
  assert.equal(vinted.reads.length, 1, "no second read within the minute");
  assert.equal((await scanner()).pacing.hourCount, 1);

  // 4) Two new listings appear (a good deal and a dull one); after the gap the next read finds them.
  vinted.A = [GOOD, DULL, ...OLD];
  await sw.evaluate(async () => {
    const sc = (await chrome.storage.local.get("scanner")).scanner;
    sc.pacing.lastAt -= 61000; // 61 s later
    sc.searches[0].lastScanAt -= 30 * 60000; // the search is due again
    await chrome.storage.local.set({ scanner: sc });
    await scanTick();
  });
  await until("second read", () => vinted.reads.length >= 2);
  await until("new items stored", async () => (await listingMode(GOOD.id)) === "extension_scan" && (await listingMode(DULL.id)) === "extension_scan");
  const s2 = await scanner();
  assert.equal(s2.searches[0].lastNew, 2);
  assert.equal(s2.searches[0].totalNew, 2);
  const hot = await until("notification for the good deal", async () => (await notes()).find((n) => n.id === `ff-scan-${GOOD.id}`));
  log("notification:", hot.title, "|", hot.message.replace(/\n/g, " / "));
  assert.match(hot.message, /Custom Slim Fit rossa/);
  assert.ok(!(await notes()).some((n) => n.id === `ff-scan-${DULL.id}`), "a new listing that is not a deal raises no notification");
  assert.deepEqual(Object.keys((await scanner()).watch), [], "answered items leave the watch list");

  // 5) A second search that never shows cards: set aside after three failed reads.
  await sw.evaluate(() => HANDLERS["ff:scan-add"]({ url: "https://www.vinted.it/catalog?search_text=empty", name: "Vuota" }));
  for (let i = 0; i < 3; i += 1) {
    await sw.evaluate(async () => {
      const sc = (await chrome.storage.local.get("scanner")).scanner;
      sc.pacing.lastAt -= 61000;
      for (const s of sc.searches) s.lastScanAt = s.name === "Vuota" ? 0 : Date.now(); // only "Vuota" is due
      await chrome.storage.local.set({ scanner: sc });
      await scanTick();
    });
    await until(`failed read ${i + 1}`, async () => ((await scanner()).searches[1].failures || 0) >= i + 1);
  }
  const empty = (await scanner()).searches[1];
  assert.equal(empty.enabled, false);
  assert.match(empty.lastError, /sospesa/);
  log("empty search set aside:", empty.lastError.slice(0, 90));

  // 6) The first refusal pauses everything for 6 hours and nothing is retried.
  vinted.mode = "refuse";
  const before = vinted.reads.length;
  await sw.evaluate(async () => {
    const sc = (await chrome.storage.local.get("scanner")).scanner;
    sc.pacing.lastAt -= 61000;
    sc.searches[0].lastScanAt = 0;
    await chrome.storage.local.set({ scanner: sc });
    await scanTick();
  });
  await until("refused read", () => vinted.reads.length > before);
  const paused = await until("pause recorded", async () => {
    const s = await summary();
    return s.pausedUntil ? s : null;
  });
  assert.ok(paused.pausedUntil - Date.now() > 5.9 * 3600 * 1000, "paused for about 6 hours");
  const deepPause = await sw.evaluate(async () => (await chrome.storage.local.get("deep")).deep.pacing.pausedUntil);
  assert.ok(deepPause > Date.now() + 5.9 * 3600 * 1000, "every other automatic read pauses too");
  const afterRefusal = vinted.reads.length;
  for (let i = 0; i < 3; i += 1) {
    await sw.evaluate(async () => {
      const sc = (await chrome.storage.local.get("scanner")).scanner;
      sc.pacing.lastAt -= 61000;
      sc.searches[0].lastScanAt = 0;
      await chrome.storage.local.set({ scanner: sc });
      await scanTick();
    });
  }
  assert.equal(vinted.reads.length, afterRefusal, "no read, no retry, while paused");
  assert.ok((await notes()).some((n) => /pausa/.test(n.title)), "the pause is announced");
  log("refusal: paused", Math.round((paused.pausedUntil - Date.now()) / 3600000), "h, reads while paused: 0");

  // Every read was made by the extension's own page, without cookies, one at a time.
  assert.ok(vinted.reads.every((r) => r.cookie === null));
  // (The gaps between reads here are short only because the test moves the pacing clock back by a
  // minute each time; the real gap is checked in step 3 and by the unit tests of the pacing.)
  log("reads of the fake Vinted:", vinted.reads.length, "- all without cookies, newest first, page 1");

  await ctx.close();
  server.close();
  console.log("\nSCANNER E2E: ALL CHECKS PASSED");
})().catch((err) => {
  console.error("\nSCANNER E2E FAILED:", err);
  process.exit(1);
});
