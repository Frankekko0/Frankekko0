// End-to-end: the real extension in Chromium, on fake Vinted pages (routed), against the
// running FlipFinder (APP_URL). Test-only manifest change: host permission for
// localhost (the permission prompt of the options page can't be clicked in headless mode).
//   APP_URL=http://localhost:3000 CHROME_PATH=/path/to/chrome node extension/e2e/live.e2e.cjs [screenshot dir]
// Needs a running FlipFinder that accepts sign-ups (ALLOW_REGISTRATION=true) and Playwright
// (PLAYWRIGHT_MODULE points to it when it is not installed next to this file).
const { chromium, request } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("assert/strict");
const fs = require("fs");
const path = require("path");
const os = require("os");
const OUT = process.argv[2] || ".";
const APP = process.env.APP_URL || "http://localhost:3000";
const SRC = path.resolve(__dirname, "..");

const TITLES = [
  ["Polo Ralph Lauren Custom Slim Fit blu", "M", "Ottime", 9], ["Hoodie Polo Ralph Lauren nera", "M", "Ottime", 14],
  ["Felpa cappuccio Polo Ralph Lauren grigia", "L", "Buone", 22], ["Polo Ralph Lauren Big Pony rossa", "L", "Ottime", 11],
  ["Ralph Lauren Maglione Polo Bear", "XL", "Buone", 70], ["Polo Ralph Lauren Pullover Cable Knit", "S", "Nuovo con cartellino", 19],
  ["Camicia Ralph Lauren Oxford azzurra", "M", "Ottime", 16], ["Polo Ralph Lauren slim fit bianca", "S", "Buone", 8],
  ["Felpa Ralph Lauren zip navy", "M", "Ottime", 27], ["Polo Ralph Lauren verde", "XL", "Discrete", 6],
  ["Maglione Ralph Lauren cotone beige", "L", "Ottime", 24], ["Hoodie Ralph Lauren bordeaux", "M", "Buone", 31],
  ["Polo Ralph Lauren gialla", "M", "Ottime", 10], ["Camicia Ralph Lauren a righe", "L", "Buone", 13],
  ["Felpa Polo Ralph Lauren girocollo", "S", "Ottime", 12], ["Polo Ralph Lauren custom fit nera", "M", "Nuovo senza cartellino", 15],
  ["Maglione Polo Ralph Lauren lana", "M", "Ottime", 29], ["Polo Ralph Lauren rosa", "S", "Ottime", 9],
];
const ID0 = 880000100;
const cardHtml = (t, i) => {
  const id = ID0 + i;
  const [title, size, cond, price] = t;
  const p = price.toFixed(2).replace(".", ",");
  const tot = (price * 1.05 + 0.7).toFixed(2).replace(".", ",");
  const sold = i === 4 ? '<span data-testid="item-status">Venduto</span>' : "";
  return `<div class="feed-grid__item"><div class="new-item-box" data-testid="product-item-id-${id}">
    <a href="/items/${id}-${title.toLowerCase().replace(/\W+/g, "-")}?referrer=catalog" title="${title}, brand: Ralph Lauren, condizioni: ${cond}, taglia: ${size}, ${p} €, ${tot} € include la Protezione acquisti" style="display:block">
      <img src="https://images1.vinted.net/t/${id}/f800.jpeg" alt="${title}" width="200" height="260" style="display:block;width:100%;height:260px;object-fit:cover;background:#e5e5e5"></a>
    ${sold}<p data-testid="product-item-id-${id}--description-title">Ralph Lauren</p><p data-testid="product-item-id-${id}--description-subtitle">${size} · ${cond}</p>
    <p data-testid="product-item-id-${id}--price-text">${p} €</p>
    <button data-testid="product-item-id-${id}--favourite" aria-label="Aggiunto ai preferiti da ${3 + i} persone">♡ ${3 + i}</button></div></div>`;
};
const catalogHtml = `<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Polo Ralph Lauren | Vinted</title>
<style>body{font-family:sans-serif;margin:0}header{height:64px;background:#09b1ba;color:#fff;display:flex;align-items:center;padding:0 24px;font-weight:700}
main{max-width:1100px;margin:0 auto;padding:16px}.feed-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.feed-grid__item p{margin:4px 0;font-size:13px}</style></head>
<body><header>Vinted (pagina di prova)</header><main><h1>polo ralph lauren</h1><div class="feed-grid" id="grid">${TITLES.slice(0, 12).map(cardHtml).join("")}</div></main>
<script>// infinite scroll like Vinted: more cards appended when near the bottom
const more=${JSON.stringify(TITLES.slice(12).map((t, i) => cardHtml(t, i + 12)))};let added=false;
addEventListener("scroll",()=>{if(!added&&innerHeight+scrollY>document.body.scrollHeight-300){added=true;document.getElementById("grid").insertAdjacentHTML("beforeend",more.join(""))}});</script></body></html>`;

function itemHtml(id, title, price) {
  const ld = { "@context": "https://schema.org", "@type": "Product", name: title, url: `https://www.vinted.it/items/${id}-x`, description: "Polo originale Ralph Lauren, etichetta interna presente, nessun difetto. Misure: ascella-ascella 52 cm, lunghezza 70 cm.", image: [1, 2, 3, 4].map((n) => `https://images1.vinted.net/t/${id}/${n}.jpeg`), brand: { "@type": "Brand", name: "Ralph Lauren" }, offers: { "@type": "Offer", price: price.toFixed(2), priceCurrency: "EUR", availability: "https://schema.org/InStock" } };
  return `<!doctype html><html lang="it"><head><meta charset="utf-8"><title>${title} | Vinted</title><link rel="canonical" href="https://www.vinted.it/items/${id}-x">
<script type="application/ld+json">${JSON.stringify(ld)}</script></head><body><header style="height:64px;background:#09b1ba"><a href="/member/1-me">Il mio profilo</a></header><main style="max-width:900px;margin:0 auto;font-family:sans-serif">
<div data-testid="item-photos">${[1, 2, 3, 4].map((n) => `<figure data-testid="item-photo-${n}"><img src="https://images1.vinted.net/t/${id}/${n}.jpeg" width="300" height="380"></figure>`).join("")}</div>
<h1>${title}</h1><div data-testid="item-price"><p>${price.toFixed(2).replace(".", ",")} €</p><p>${(price * 1.05 + 0.7).toFixed(2).replace(".", ",")} € include la Protezione acquisti</p></div>
<div data-testid="item-attributes-brand"><div>Brand</div><div>Ralph Lauren</div></div><div data-testid="item-attributes-size"><div>Taglia</div><div>M</div></div>
<div data-testid="item-attributes-status"><div>Condizioni</div><div>Ottime</div></div><div data-testid="item-attributes-color"><div>Colore</div><div>Blu</div></div>
<div data-testid="item-attributes-favourite_count"><div>Interessati</div><div>21 membri</div></div><div data-testid="item-attributes-view_count"><div>Visualizzazioni</div><div>310</div></div>
<div data-testid="item-attributes-upload_date"><div>Caricato</div><div>2 giorni fa</div></div>
<div itemprop="description">${ld.description}</div><a href="/member/4242-venditore"><span>venditore_x</span></a><div data-testid="seller-rating"><span aria-label="Valutazione 4,8 su 5">★★★★★</span><span>52 recensioni</span></div>
</main><script>self.__next_f.push([1,"{\\"item\\":{\\"id\\":${id},\\"favourite_count\\":21,\\"view_count\\":310,\\"is_reserved\\":false,\\"is_closed\\":false,\\"user\\":{\\"feedback_reputation\\":0.96,\\"feedback_count\\":52}}}"])</script></body></html>`;
}
const SVG = (n) => `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="380"><rect width="300" height="380" fill="hsl(${(n * 47) % 360},45%,72%)"/><text x="150" y="200" font-size="40" text-anchor="middle" fill="#fff" font-family="sans-serif">${n}</text></svg>`;

(async () => {
  // Test copy of the extension with the localhost permission pre-granted.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ffext-"));
  fs.cpSync(SRC, dir, { recursive: true });
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json")));
  manifest.host_permissions = [`${APP}/*`];
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest));

  // A pairing key from FlipFinder (a fresh test account), as Settings → Browser extension would create.
  const api = await request.newContext({ baseURL: APP });
  const reg = await api.post("/api/v1/auth/register", { data: { email: `e2e-${Date.now()}@example.com`, password: "E2e-test-pass-2026!" } });
  if (!reg.ok()) throw new Error("register: " + (await reg.text()));
  const session = await reg.json();
  const created = await api.post("/api/v1/extension/keys", { data: { name: "e2e" }, headers: { "X-CSRF-Token": session.csrf_token } });
  const { key, id: keyId } = await created.json();
  if (!key) throw new Error("no key: " + (await created.text()));

  const ctx = await chromium.launchPersistentContext(fs.mkdtempSync(path.join(os.tmpdir(), "ffprof-")), {
    headless: false,
    executablePath: process.env.CHROME_PATH || undefined,
    args: ["--headless=new", `--disable-extensions-except=${dir}`, `--load-extension=${dir}`, "--no-sandbox"],
    viewport: { width: 1280, height: 900 },
  });
  const reads = [];
  await ctx.route("https://images1.vinted.net/**", (r) => r.fulfill({ contentType: "image/svg+xml", body: SVG(Number(/\/t\/\d+\/(\d+)/.exec(r.request().url())?.[1] || 7)) }));
  await ctx.route("https://www.vinted.it/**", (r) => {
    const u = new URL(r.request().url());
    const m = /^\/items\/(\d+)/.exec(u.pathname);
    if (m) {
      const id = Number(m[1]);
      reads.push({ id, cookie: r.request().headers().cookie || null });
      const t = TITLES[(id - ID0) % TITLES.length];
      return r.fulfill({ contentType: "text/html", body: itemHtml(id, t[0], t[3]) });
    }
    return r.fulfill({ contentType: "text/html", body: catalogHtml });
  });
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent("serviceworker");
  const extId = new URL(sw.url()).host;
  const log = (...a) => console.log("•", ...a);
  log("extension", extId, manifest.version);

  // 1) Not paired yet: queue + badge "off", nothing is sent.
  await sw.evaluate(async ({ app }) => chrome.storage.sync.set({ options: { appUrl: app } }), { app: APP });

  // 2) Pair with a wrong address first: captures stay queued (offline), then the right one.
  await sw.evaluate(async ({ key }) => {
    await chrome.storage.sync.set({ options: { appUrl: "http://localhost:3999" } });
    await chrome.storage.local.set({ apiKey: key });
  }, { key });
  const page = await ctx.newPage();
  page.on("console", (m) => m.type() === "error" && !/favicon|ERR_FAILED|3999/.test(m.text()) && console.log("  page error:", m.text()));
  await page.goto("https://www.vinted.it/catalog?search_text=polo+ralph+lauren");
  await page.waitForTimeout(2500);
  const offline = await sw.evaluate(async () => (await chrome.storage.session.get("sync")).sync);
  log("offline state:", offline.state, "pending", offline.pending);
  assert.equal(offline.state, "offline");
  assert.ok(offline.pending > 0, "captures wait in the queue while FlipFinder is unreachable");
  const cookieless = await page.evaluate(() => document.cookie);
  await sw.evaluate(async ({ app }) => chrome.storage.sync.set({ options: { appUrl: app } }), { app: APP });
  // Badges show the instant verdict at once (estimate, or "dati insuff." without the market
  // summary): wait for the sync itself, then for the full analysis on the badges.
  for (let i = 0; i < 60 && (await sw.evaluate(async () => (await chrome.storage.session.get("sync")).sync.state)) !== "ok"; i += 1) await page.waitForTimeout(500);
  await page.waitForFunction(() => [...document.querySelectorAll("ff-badge")].filter((b) => /\d+ ·/.test(b.shadowRoot.querySelector(".b").textContent)).length >= 8, null, { timeout: 30000 });
  const synced = await sw.evaluate(async () => (await chrome.storage.session.get("sync")).sync);
  log("after fixing the address:", synced.state, "pending", synced.pending);
  assert.equal(synced.state, "ok");

  // 3) Scroll like a user: new cards (infinite scroll) are evaluated as they enter the screen.
  for (let y = 0; y < 6; y += 1) {
    await page.mouse.wheel(0, 700);
    await page.waitForTimeout(500);
  }
  await page.waitForFunction(() => document.querySelectorAll("ff-badge").length >= 16, null, { timeout: 30000 });
  await page.waitForTimeout(3000);
  const badges = await page.evaluate(() => [...document.querySelectorAll("ff-badge")].map((b) => b.shadowRoot.querySelector(".b").textContent.trim()));
  log(`badges (${badges.length}):`, badges.slice(0, 18).join(" | "));
  assert.ok(badges.length >= 16, "cards loaded by infinite scroll are evaluated too");
  assert.ok(badges.includes("venduto"), "sold badge read from the card");
  await page.evaluate(() => scrollTo(0, 0));
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/ext-catalog.png` });

  // 4) Quick actions on click: open the badge menu, "Traccia".
  const firstVid = await page.evaluate(() => document.querySelector("[data-ff-vid]").getAttribute("data-ff-vid"));
  const badge = page.locator(`[data-ff-vid="${firstVid}"] ff-badge`);
  const trackedBefore = (await badge.locator(".b").innerHTML()).includes("<svg");
  await badge.locator(".b").click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/ext-badge-menu.png`, clip: { x: 0, y: 60, width: 700, height: 520 } });
  await badge.locator('button[data-act="track"]').click();
  await page.waitForTimeout(1500);
  const trackedBadge = await badge.locator(".b").innerHTML();
  log("tracking toggled from the badge:", trackedBefore, "→", trackedBadge.includes("<svg"));
  log("url after clicks (no navigation):", page.url());
  assert.equal(trackedBadge.includes("<svg"), !trackedBefore, "Traccia / Smetti di tracciare toggles");
  assert.ok(page.url().includes("/catalog"), "badge clicks never open the listing");

  // 5) Nothing reads other pages on its own: browsing the catalog and using the badge asked Vinted
  //    for no item page. "Apri su Vinted" is a link the user clicks: it opens the listing in a tab.
  const openVid = await page.evaluate(() => [...document.querySelectorAll("[data-ff-vid]")][2].getAttribute("data-ff-vid"));
  const openBadge = page.locator(`[data-ff-vid="${openVid}"] ff-badge`);
  await openBadge.locator(".b").click();
  assert.equal(await openBadge.locator('button[data-act="deep"]').count(), 0, "no deep-read button");
  assert.equal(reads.length, 0, "the extension requested no item page by itself");
  const [opened] = await Promise.all([ctx.waitForEvent("page"), openBadge.locator('button[data-act="vinted"]').click()]);
  await opened.waitForLoadState("domcontentloaded");
  log("Apri su Vinted opened:", opened.url());
  assert.match(opened.url(), /\/items\/\d+/);
  await opened.close();
  await page.bringToFront();

  // 6) Live panel (pinned to this tab), ranking + click to scroll.
  const tabId = await sw.evaluate(async () => Object.keys((await chrome.storage.session.get("tabs")).tabs || {}).map(Number)[0]);
  const panel = await ctx.newPage();
  await panel.setViewportSize({ width: 380, height: 900 });
  await panel.goto(`chrome-extension://${extId}/src/panel.html?tabId=${tabId}`);
  await panel.waitForSelector("ol.rank .entry", { timeout: 15000 });
  await panel.waitForTimeout(800);
  const ranking = await panel.evaluate(() => [...document.querySelectorAll("ol.rank .entry .t")].map((t) => t.textContent));
  const counters = await panel.evaluate(() => ["c-seen", "c-saved", "c-best"].map((id) => document.getElementById(id).textContent));
  log("panel ranking:", ranking.join(" | "));
  log("counters seen/saved/best:", counters.join(" / "));
  assert.ok(ranking.length >= 3 && ranking.length <= 5);
  assert.ok(Number(counters[0]) >= 16);
  await panel.screenshot({ path: `${OUT}/ext-panel.png`, fullPage: true });
  await page.evaluate(() => scrollTo(0, document.body.scrollHeight));
  await panel.locator("ol.rank .entry").nth(1).click();
  await page.waitForTimeout(1200);
  const scrolled = await page.evaluate(() => scrollY < document.body.scrollHeight - innerHeight - 50);
  log("page scrolled to the clicked card:", scrolled);
  assert.ok(scrolled);
  const csv = await panel.evaluate(() => {
    const K = globalThis.FlipFinderCore;
    return K.rankingCsv([]).slice(0, 40);
  });
  log("csv header:", JSON.stringify(csv));

  // 7) Item page: full capture of all fields and photos, small box with the result.
  await page.goto(`https://www.vinted.it/items/${ID0 + 7}-x`);
  await page.waitForFunction(() => document.getElementById("flipfinder-item-box")?.shadowRoot.querySelector(".acts"), null, { timeout: 30000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/ext-item.png` });
  await panel.waitForTimeout(2500);
  await panel.screenshot({ path: `${OUT}/ext-panel-detail.png`, fullPage: true });
  log("panel overflow (scrollWidth/clientWidth):", JSON.stringify(await panel.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth, innerWidth])));
  const detail = await (await api.get(`/api/v1/items/${ID0 + 7}`)).json();
  log("item stored:", detail.item.capture_level, detail.item.acquisition_mode, "images", detail.images.length, "views", detail.view_count, "seller", JSON.stringify(detail.seller));
  log("snapshots:", detail.snapshots.map((s) => s.acquisition_mode).join(", "));
  assert.equal(detail.item.capture_level, "full");
  assert.equal(detail.images.length, 4);
  assert.equal(detail.view_count, 310);
  assert.ok(detail.snapshots.some((s) => s.acquisition_mode === "extension_item"));
  const archive = await (await api.get(`/api/v1/items`, { params: { mode: "extension_card", page_size: 50 } })).json();
  log("archive extension_card records:", archive.total);
  log("cookies sent to Vinted:", reads.filter((r) => r.cookie).length, "document.cookie on page:", JSON.stringify(cookieless));
  assert.equal(reads.filter((r) => r.cookie).length, 0);
  console.log("e2e OK");
  await ctx.close();
  await api.delete(`/api/v1/extension/keys/${keyId}`, { headers: { "X-CSRF-Token": session.csrf_token } }); // test key revoked
  await api.dispose();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
