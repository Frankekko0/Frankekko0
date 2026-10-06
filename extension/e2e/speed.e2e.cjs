// Speed check of the instant verdict: the real extension in Chromium on a 96-card search page
// with the weight of a real Vinted page (about 1.5 MB of page data in scripts and thousands of
// nodes), against a running FlipFinder (APP_URL, the API itself or the web app).
//   APP_URL=http://localhost:8000 CHROME_PATH=/path/to/chrome node extension/e2e/speed.e2e.cjs
// Measures, from navigation start: instant verdicts on the cards and the best opportunity
// highlighted (with expected profit and reason); also with the CPU slowed 4x, with FlipFinder
// unreachable, and the main-thread long tasks while scrolling.
const { chromium, request } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("assert/strict");
const fs = require("fs");
const path = require("path");
const os = require("os");
const APP = process.env.APP_URL || "http://localhost:8000";
const SRC = path.resolve(__dirname, "..");
const LIMIT_MS = 1000;

const MODELS = [
  ["Ralph Lauren", "Polo Ralph Lauren custom slim fit", 12], ["Ralph Lauren", "Felpa con cappuccio Ralph Lauren", 18],
  ["Nike", "Felpa con cappuccio Nike vintage", 15], ["Nike", "Nike Air Max 90 sneakers", 35], ["The North Face", "Piumino The North Face Nuptse 700", 90],
  ["Stone Island", "Felpa Stone Island girocollo", 70], ["Carhartt", "Giacca Carhartt Detroit", 45], ["Levi's", "Jeans Levi's 501", 15],
  ["Lacoste", "Polo Lacoste classic fit", 14], ["Tommy Hilfiger", "Polo Tommy Hilfiger", 9], ["Moncler", "Piumino Moncler Maya", 250],
  ["Patagonia", "Pile Patagonia Synchilla", 30], ["Adidas", "Tuta Adidas vintage", 25], ["Zara", "Maglione Zara", 8], ["Stüssy", "T-shirt Stüssy logo", 12],
  ["Champion", "Felpa Champion reverse weave", 20],
];
const CONDS = ["Ottime condizioni", "Buone condizioni", "Nuovo con cartellino", "Nuovo senza cartellino"];
const SIZES = ["S", "M", "L", "XL"];
const ID0 = 970000000;

function card(i) {
  const [brand, title, base] = MODELS[i % MODELS.length];
  const id = ID0 + i;
  const price = Math.max(3, Math.round(base * (0.45 + ((i * 37) % 70) / 100) * 100) / 100);
  const size = SIZES[i % 4];
  const cond = CONDS[i % 4];
  const p = price.toFixed(2).replace(".", ",");
  const tot = (price * 1.05 + 0.7).toFixed(2).replace(".", ",");
  return `<div class="feed-grid__item"><div class="new-item-box__container" data-testid="product-item-id-${id}">
  <div class="new-item-box__image-container"><a href="/items/${id}-${title.toLowerCase().replace(/\W+/g, "-")}?referrer=catalog" class="new-item-box__overlay" title="${title}, brand: ${brand}, condizioni: ${cond}, taglia: ${size}, ${p} €, ${tot} € include la Protezione acquisti" data-testid="product-item-id-${id}--overlay-link"></a>
  <div class="web_ui__Image__image"><img src="https://images1.vinted.net/t/${id}/f800.jpeg" alt="${title}" data-testid="product-item-id-${id}--image--img" width="200" height="260" style="display:block;width:100%;height:260px;background:#e5e5e5"></div></div>
  <div class="new-item-box__summary"><a href="/member/${5000 + i}-seller" class="avatar"><img src="https://images1.vinted.net/t/avatar${i}.jpeg" width="20" height="20"></a>
  <p data-testid="product-item-id-${id}--description-title">${brand}</p><p data-testid="product-item-id-${id}--description-subtitle">${size} · ${cond}</p>
  <p data-testid="product-item-id-${id}--price-text">${p} €</p><button data-testid="product-item-id-${id}--favourite" aria-label="Aggiunto ai preferiti da ${i % 30} persone">♡ ${i % 30}</button></div></div></div>`;
}

// Page data like Next.js puts in a real catalog page (~1.5 MB), plus filters and footer nodes.
const blob = JSON.stringify({ items: Array.from({ length: 96 }, (_, i) => ({ id: ID0 + i, title: MODELS[i % MODELS.length][1], photos: Array.from({ length: 6 }, (_, k) => ({ url: `https://images1.vinted.net/t/${ID0 + i}/${k}.jpeg`, dominant_color: "#aabbcc", thumbnails: Array.from({ length: 8 }, (_, t) => ({ type: `thumb${t}`, url: `https://images1.vinted.net/t/${ID0 + i}/${k}/${t}.jpeg`, width: 100 * t, height: 130 * t })) })), user: { id: 5000 + i, login: `seller${i}`, photo: null }, promoted: false, padding: "x".repeat(2000) })) });
const filters = Array.from({ length: 1200 }, (_, i) => `<li class="filter"><label><input type="checkbox"><span>Filtro ${i}</span><span class="count">${i * 3}</span></label></li>`).join("");
const PAGE = `<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Risultati | Vinted</title>
<style>body{font-family:sans-serif;margin:0}header{height:64px;background:#09b1ba}main{max-width:1200px;margin:0 auto;padding:16px;display:flex;gap:16px}aside{width:220px;max-height:600px;overflow:auto}
.feed-grid{flex:1;display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.feed-grid__item p{margin:4px 0;font-size:13px}.new-item-box__image-container{position:relative}.new-item-box__overlay{position:absolute;inset:0;z-index:1}</style>
</head><body><header><a href="/member/1-me"><img src="https://images1.vinted.net/t/me.jpeg" width="32" height="32"></a></header><main><aside><ul>${filters}</ul></aside>
<div class="feed-grid">${Array.from({ length: 96 }, (_, i) => card(i)).join("")}</div></main><footer>${"<p>Vinted footer link</p>".repeat(200)}</footer>
<script>self.__next_f=self.__next_f||[];self.__next_f.push([1,${JSON.stringify(blob)}])</script></body></html>`;

(async () => {
  console.log(`page: 96 cards, ${(PAGE.length / 1e6).toFixed(2)} MB of HTML`);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ffext-"));
  fs.cpSync(SRC, dir, { recursive: true });
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json")));
  manifest.host_permissions = [`${APP}/*`];
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest));

  const api = await request.newContext({ baseURL: APP });
  const email = `speed-${Date.now()}@example.com`;
  const reg = await api.post("/api/v1/auth/register", { data: { email, password: "Speed-test-2026!" } });
  if (!reg.ok()) throw new Error("register: " + (await reg.text()));
  const session = await reg.json();
  const created = await api.post("/api/v1/extension/keys", { data: { name: "speed" }, headers: { "X-CSRF-Token": session.csrf_token } });
  const { key } = await created.json();
  if (!key) throw new Error("no key: " + (await created.text()));

  const ctx = await chromium.launchPersistentContext(fs.mkdtempSync(path.join(os.tmpdir(), "ffprof-")), {
    headless: false,
    executablePath: process.env.CHROME_PATH || undefined,
    args: ["--headless=new", `--disable-extensions-except=${dir}`, `--load-extension=${dir}`, "--no-sandbox"],
    viewport: { width: 1366, height: 900 },
  });
  await ctx.route("https://images1.vinted.net/**", (r) => r.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="260"><rect width="200" height="260" fill="#ddd"/></svg>' }));
  await ctx.route("https://www.vinted.it/**", (r) => r.fulfill({ contentType: "text/html", body: PAGE }));
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent("serviceworker");
  await sw.evaluate(async ({ app, key }) => {
    await chrome.storage.sync.set({ options: { appUrl: app } });
    await chrome.storage.local.set({ apiKey: key });
  }, { app: APP, key });
  // The market summary is downloaded once at pairing (here: wait for it).
  for (let i = 0; i < 40; i += 1) {
    const ok = await sw.evaluate(async () => Boolean((await chrome.storage.local.get("marketCache")).marketCache));
    if (ok) break;
    await sw.evaluate(() => self.updateMarketCache && self.updateMarketCache());
    await new Promise((r) => setTimeout(r, 500));
  }
  const summary = await sw.evaluate(async () => {
    const m = (await chrome.storage.local.get("marketCache")).marketCache;
    return m ? { version: m.version, segments: Object.keys(m.segments).length, bytes: JSON.stringify(m).length } : null;
  });
  assert.ok(summary, "market summary downloaded");
  console.log("market summary:", JSON.stringify(summary));

  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await ctx.addInitScript(() => {
    window.__ltLoad = [];
    try {
      new PerformanceObserver((l) => window.__ltLoad.push(...l.getEntries().map((e) => [Math.round(e.startTime), Math.round(e.duration)]))).observe({ type: "longtask" });
    } catch {
      /* not supported */
    }
  });
  async function measure(label, { throttle = 1, settleMs = 0 } = {}) {
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: throttle });
    await page.goto(`https://www.vinted.it/catalog?search_text=test&r=${Math.random()}`);
    await page.waitForFunction(() => document.documentElement.hasAttribute("data-ff-best-ms") && document.documentElement.hasAttribute("data-ff-quick-ms"), null, { timeout: 15000 });
    const r = await page.evaluate(() => ({
      quick: Number(document.documentElement.getAttribute("data-ff-quick-ms")),
      best: Number(document.documentElement.getAttribute("data-ff-best-ms")),
      dcl: Math.round(performance.getEntriesByType("navigation")[0].domInteractive),
      badges: document.querySelectorAll("ff-badge").length,
      pill: document.getElementById("flipfinder-best")?.shadowRoot.querySelector(".box").textContent.trim().replace(/\s+/g, " ").slice(0, 200),
      ext_long_tasks: (() => {
        const m = Object.fromEntries(performance.getEntriesByType("mark").filter((x) => x.name.startsWith("ff:")).map((x) => [x.name.slice(3), x.startTime]));
        return (window.__ltLoad || []).filter(([t, d]) => t + d > (m.dom ?? Infinity) && t < (m.badges ?? 0)).map(([, d]) => d);
      })(),
      marks: Object.fromEntries(performance.getEntriesByType("mark").filter((m) => m.name.startsWith("ff:")).map((m) => [m.name.slice(3), Math.round(m.startTime)])),
    }));
    if (settleMs) await page.waitForTimeout(settleMs);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
    console.log(`${label}: best ${r.best} ms from navigation, ${r.best - r.dcl} ms after the page was parsed (${r.dcl} ms), badges ${r.badges}, extension work ${Math.round(r.marks.badges - r.marks.dom)} ms (long tasks during it: ${JSON.stringify(r.ext_long_tasks)}); marks ${JSON.stringify(r.marks)}`);
    return r;
  }

  const runs = [];
  // Between runs, time to look at the page (the server finishes the previous full analysis:
  // here it shares the machine with the browser, in real use it does not).
  for (let i = 0; i < 5; i += 1) {
    runs.push(await measure(`run ${i + 1}`));
    await page.waitForTimeout(4000);
  }
  const first = runs[0];
  console.log("highlight:", first.pill);
  assert.ok(first.badges >= 90, "every card gets a verdict");
  assert.match(first.pill, /profitto atteso/);
  const slow = await measure("CPU 4x slower", { throttle: 4 });

  // Full analysis arrives in the background and replaces the quick estimates.
  await page.waitForFunction(() => [...document.querySelectorAll("ff-badge")].filter((b) => !/≈/.test(b.shadowRoot.querySelector(".b").textContent)).length >= 40, null, { timeout: 60000 }).catch(() => null);
  const server = await page.evaluate(() => [...document.querySelectorAll("ff-badge")].filter((b) => !/≈/.test(b.shadowRoot.querySelector(".b").textContent)).length);
  console.log("cards with the full server analysis after the second step:", server);

  // Scroll: long tasks on the main thread while scrolling through the page.
  await page.evaluate(() => {
    window.__long = [];
    new PerformanceObserver((l) => window.__long.push(...l.getEntries().map((e) => Math.round(e.duration)))).observe({ type: "longtask", buffered: false });
  });
  for (let y = 0; y < 12; y += 1) {
    await page.mouse.wheel(0, 600);
    await page.waitForTimeout(120);
  }
  const longTasks = await page.evaluate(() => window.__long);
  console.log("long tasks while scrolling (ms):", JSON.stringify(longTasks));

  // FlipFinder unreachable: the instant verdict still works from the stored summary.
  await sw.evaluate(async () => chrome.storage.sync.set({ options: { appUrl: "http://localhost:3999" } }));
  await page.waitForTimeout(300);
  const offline = await measure("FlipFinder offline");
  await sw.evaluate(async ({ app }) => chrome.storage.sync.set({ options: { appUrl: app } }), { app: APP });

  const after = (r) => r.best - r.dcl;
  const all = [...runs, slow, offline];
  const result = {
    from_page_loaded_ms: { runs: runs.map(after), cpu4x: after(slow), offline: after(offline), worst: Math.max(...all.map(after)) },
    extension_work_ms: all.map((r) => Math.round(r.marks.badges - r.marks.dom)),
    extension_long_tasks_ms: all.flatMap((r) => r.ext_long_tasks),
    from_navigation_ms: { runs: runs.map((r) => r.best), cpu4x: slow.best, offline: offline.best },
    max_long_task_scrolling_ms: longTasks.length ? Math.max(...longTasks) : 0,
  };
  console.log("RESULT", JSON.stringify(result));
  assert.ok(result.from_page_loaded_ms.worst <= LIMIT_MS, `best highlighted within ${LIMIT_MS} ms of the page being loaded`);
  console.log("speed e2e OK");
  await ctx.close();
  await api.dispose();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
