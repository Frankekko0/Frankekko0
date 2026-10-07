// End-to-end: Vinted favourite and Buy, started from FlipFinder's Analysis page, the tracking page
// and the extension panel. Real extension in Chromium, fake Vinted pages (routed) that keep the
// favourite on their "server" and lead to a checkout, a running FlipFinder (APP_URL).
// Fake Vinted is a local HTTPS server that the browser reaches as www.vinted.it (host mapping):
// the tabs the extension opens itself are not covered by Playwright's routing. Test-only
// manifest change: host permission for APP (the permission prompt can't be clicked headless).
//   APP_URL=http://localhost:3001 CHROME_PATH=/path/to/chrome node extension/e2e/actions.e2e.cjs [screenshot dir]
// Needs a running FlipFinder web app + API that accepts sign-ups, and Playwright (PLAYWRIGHT_MODULE).
const { chromium, request } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("assert/strict");
const fs = require("fs");
const path = require("path");
const os = require("os");
const https = require("https");
const { execFileSync } = require("child_process");
const OUT = process.argv[2] || ".";
const APP = process.env.APP_URL || "http://localhost:3001";
const SRC = path.resolve(__dirname, "..");

const ID0 = 881000100 + (Date.now() % 100000) * 10; // fresh Vinted ids on every run
const A = ID0 + 1; // bought end to end
const B = ID0 + 2; // its price changes
const C = ID0 + 3; // reserved for someone else
const ITEMS = {
  [A]: { title: "Polo Ralph Lauren Custom Slim Fit blu", price: 20, reserved: false },
  [B]: { title: "Felpa Polo Ralph Lauren grigia zip", price: 30, reserved: false },
  [C]: { title: "Maglione Polo Bear Ralph Lauren", price: 45, reserved: false },
};
// What Vinted's servers know: your session, your favourites, the pages served.
const vinted = { signedIn: true, fav: new Map(), favPosts: [], checkouts: [], successes: [] };

const euro = (v) => v.toFixed(2).replace(".", ",");
function itemHtml(id) {
  const it = ITEMS[id];
  const fav = vinted.fav.get(id) === true;
  const ld = { "@context": "https://schema.org", "@type": "Product", name: it.title, url: `https://www.vinted.it/items/${id}-x`, description: "Originale Ralph Lauren, etichetta interna presente, nessun difetto. Misure: ascella-ascella 52 cm, lunghezza 70 cm.", image: [1, 2, 3].map((n) => `https://images1.vinted.net/t/${id}/${n}.jpeg`), brand: { "@type": "Brand", name: "Ralph Lauren" }, offers: { "@type": "Offer", price: it.price.toFixed(2), priceCurrency: "EUR", availability: "https://schema.org/InStock" } };
  const header = vinted.signedIn ? '<a href="/member/1-me">Il mio profilo</a>' : '<a data-testid="header--login-button" href="/member/signup/select_type">Registrati | Accedi</a>';
  return `<!doctype html><html lang="it"><head><meta charset="utf-8"><title>${it.title} | Vinted</title><link rel="canonical" href="https://www.vinted.it/items/${id}-x">
<script type="application/ld+json">${JSON.stringify(ld)}</script></head><body><header style="height:64px;background:#09b1ba">${header}</header><main style="max-width:900px;margin:0 auto;font-family:sans-serif">
<div data-testid="item-photos">${[1, 2, 3].map((n) => `<figure data-testid="item-photo-${n}"><img src="https://images1.vinted.net/t/${id}/${n}.jpeg" width="300" height="380"></figure>`).join("")}</div>
<h1>${it.title}</h1><div data-testid="item-price"><p>${euro(it.price)} €</p><p>${euro(it.price * 1.05 + 0.7)} € include la Protezione acquisti</p></div>
<div data-testid="item-attributes-brand"><div>Brand</div><div>Ralph Lauren</div></div><div data-testid="item-attributes-size"><div>Taglia</div><div>M</div></div>
<div data-testid="item-attributes-status"><div>Condizioni</div><div>Ottime</div></div><div data-testid="item-attributes-color"><div>Colore</div><div>Blu</div></div>
<div data-testid="item-attributes-favourite_count"><div>Interessati</div><div>21 membri</div></div><div data-testid="item-attributes-view_count"><div>Visualizzazioni</div><div>310</div></div>
<div data-testid="item-attributes-upload_date"><div>Caricato</div><div>2 giorni fa</div></div>
<div itemprop="description">${ld.description}</div><a href="/member/4242-venditore"><span>venditore_x</span></a><div data-testid="seller-rating"><span aria-label="Valutazione 4,8 su 5">★★★★★</span><span>52 recensioni</span></div>
<div data-testid="item-sidebar">
  <button type="button" data-testid="item-favourite-button" aria-pressed="${fav}" aria-label="${fav ? "Rimuovi dai preferiti" : "Aggiungi ai preferiti"}">♡ 21</button>
  ${it.reserved ? '<p data-testid="item-status--reserved">Riservato</p>' : '<button type="button" data-testid="item-buy-button">Acquista</button>'}
</div>
</main>
<script>
const fb = document.querySelector('[data-testid="item-favourite-button"]');
fb.addEventListener("click", async () => {
  const r = await fetch("/api/v2/items/${id}/favourite", { method: "POST" }).then((x) => x.json());
  fb.setAttribute("aria-pressed", String(r.on));
  fb.setAttribute("aria-label", r.on ? "Rimuovi dai preferiti" : "Aggiungi ai preferiti");
});
const bb = document.querySelector('[data-testid="item-buy-button"]');
if (bb) bb.addEventListener("click", () => { location.href = "/checkout?transaction_id=9${id}"; });
</script>
<script>self.__next_f.push([1,"{\\"item\\":{\\"id\\":${id},\\"favourite_count\\":21,\\"view_count\\":310,\\"is_reserved\\":${it.reserved},\\"is_closed\\":false,\\"user\\":{\\"feedback_reputation\\":0.96,\\"feedback_count\\":52}}}"])</script></body></html>`;
}
const checkoutHtml = (id) => {
  const p = ITEMS[id].price;
  return `<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Checkout | Vinted</title></head><body style="font-family:sans-serif">
<header style="height:64px;background:#09b1ba"><a href="/member/1-me">Il mio profilo</a></header><main style="max-width:600px;margin:0 auto">
<h1>Riepilogo dell'ordine</h1><p>Ordine ${euro(p)} €</p><p>Protezione acquisti ${euro(p * 0.05 + 0.7)} €</p><p>Spedizione 3,49 €</p>
<p><b>Totale</b> ${euro(p * 1.05 + 0.7 + 3.49)} €</p><button id="pay" type="button">Paga</button></main>
<script>document.getElementById("pay").onclick = () => { location.href = "/checkout/success?transaction_id=9${id}"; };</script></body></html>`;
};
// The confirmation renders a moment after load, as a single-page app would.
const successHtml = (id) => {
  const p = ITEMS[id].price;
  return `<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Vinted</title></head><body style="font-family:sans-serif"><main id="m">Caricamento…</main>
<script>setTimeout(() => { document.getElementById("m").innerHTML = "<h1>Pagamento completato</h1><p>Grazie per il tuo acquisto.</p><p>Totale pagato: ${euro(p * 1.05 + 0.7 + 3.49)} €</p>"; }, 1200);</script></body></html>`;
};
const SVG = (n) => `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="380"><rect width="300" height="380" fill="hsl(${(n * 47) % 360},45%,72%)"/></svg>`;

let diagnose = async () => {};
(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ffext-"));
  fs.cpSync(SRC, dir, { recursive: true });
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json")));
  manifest.host_permissions = [`${new URL(APP).origin}/*`];
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest));

  // A fresh FlipFinder account and a pairing key, as Settings → Browser extension creates.
  const api = await request.newContext({ baseURL: APP });
  const reg = await api.post("/api/v1/auth/register", { data: { email: `e2e-actions-${Date.now()}@example.com`, password: "E2e-test-pass-2026!" } });
  if (!reg.ok()) throw new Error("register: " + (await reg.text()));
  const session = await reg.json();
  const created = await api.post("/api/v1/extension/keys", { data: { name: "e2e" }, headers: { "X-CSRF-Token": session.csrf_token } });
  const { key } = await created.json();
  const capture = await request.newContext({ baseURL: APP, extraHTTPHeaders: { Authorization: `Bearer ${key}` } });

  // Fake Vinted (and its image host) over HTTPS with a throwaway self-signed certificate.
  const certDir = fs.mkdtempSync(path.join(os.tmpdir(), "ffcert-"));
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=www.vinted.it", "-addext", "subjectAltName=DNS:www.vinted.it,DNS:images1.vinted.net", "-keyout", path.join(certDir, "k.pem"), "-out", path.join(certDir, "c.pem")], { stdio: "ignore" });
  const server = https.createServer({ key: fs.readFileSync(path.join(certDir, "k.pem")), cert: fs.readFileSync(path.join(certDir, "c.pem")) }, (req, res) => {
    const u = new URL(req.url, `https://${req.headers.host}`);
    const send = (body, type = "text/html", status = 200) => res.writeHead(status, { "Content-Type": `${type}; charset=utf-8`, "Cache-Control": "no-store" }).end(body);
    if (u.hostname === "images1.vinted.net") return send(SVG(Number((/\/t\/(\d+)/.exec(u.pathname) || [0, 7])[1]) % 97), "image/svg+xml");
    let m = /^\/api\/v2\/items\/(\d+)\/favourite$/.exec(u.pathname);
    if (m && req.method === "POST") {
      const id = Number(m[1]);
      if (!vinted.signedIn) return send("{}", "application/json", 401);
      vinted.fav.set(id, !vinted.fav.get(id));
      vinted.favPosts.push(id);
      return send(JSON.stringify({ on: vinted.fav.get(id) }), "application/json");
    }
    const tx = Number((u.searchParams.get("transaction_id") || "").slice(1));
    if (u.pathname === "/checkout") return vinted.checkouts.push(tx), send(checkoutHtml(tx));
    if (u.pathname === "/checkout/success") return vinted.successes.push(tx), send(successHtml(tx));
    m = /^\/items\/(\d+)/.exec(u.pathname);
    if (m && ITEMS[m[1]]) return send(itemHtml(Number(m[1])));
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
  });
  await ctx.addCookies((await api.storageState()).cookies); // signed in to FlipFinder in this browser
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent("serviceworker");
  if (process.env.E2E_DEBUG) {
    sw.on("console", (m) => console.log("  [sw]", m.text()));
    ctx.on("page", (p) => p.on("console", (m) => console.log(`  [${p.url().slice(0, 50)}]`, m.text())));
  }
  const extId = new URL(sw.url()).host;
  const log = (...a) => console.log("•", ...a);
  const state = async (lid) => (await (await api.get(`/api/v1/listings/${lid}/vinted`)).json());
  const until = async (what, fn, ms = 20000) => {
    const end = Date.now() + ms;
    for (;;) {
      const v = await fn();
      if (v) return v;
      if (Date.now() > end) throw new Error(`timeout: ${what}`);
      await new Promise((res) => setTimeout(res, 300));
    }
  };

  await sw.evaluate(async ({ app, key }) => {
    await chrome.storage.local.set({ apiKey: key });
    await chrome.storage.sync.set({ options: { appUrl: app } });
  }, { app: APP, key });

  // 1) You browse the three listings on Vinted: the extension records them in FlipFinder.
  const vintedTab = await ctx.newPage();
  const ids = {};
  for (const id of [A, B, C]) {
    await vintedTab.goto(`https://www.vinted.it/items/${id}-x`);
    ids[id] = await until(`capture ${id}`, async () => {
      const r = await capture.post("/api/v1/capture/evaluations", { data: { vinted_ids: [String(id)] } });
      const [ev] = r.ok() ? await r.json() : [];
      return ev && ev.opportunity_id && ev.analysis_depth === "full" ? { lid: ev.listing_id, opp: ev.opportunity_id } : null;
    });
  }
  log("captured", JSON.stringify(ids));

  // 2) Analysis page: the bridge is there, requests without your click are refused.
  const app = await ctx.newPage();
  diagnose = async () => {
    const toasts = await app.evaluate(() => [...document.querySelectorAll("[data-sonner-toast]")].map((t) => t.innerText)).catch(() => []);
    const errors = await sw.evaluate(async () => (await chrome.storage.local.get("errors")).errors || []).catch(() => []);
    console.error("toasts:", JSON.stringify(toasts), "\nextension errors:", JSON.stringify(errors).slice(0, 1500), "\ntabs:", ctx.pages().map((p) => p.url()).join(" "));
    await app.screenshot({ path: `${OUT}/actions-failure.png` }).catch(() => {});
  };
  await app.goto(`${APP}/deals/${ids[A].opp}`);
  const favBtn = app.getByRole("button", { name: /Vinted favourites/ });
  await favBtn.waitFor({ timeout: 30000 });
  await app.waitForFunction(() => document.documentElement.dataset.flipfinderExtension, null, { timeout: 10000 });
  // A page script acting on its own: Playwright's evaluate counts as a user gesture, so the
  // request leaves from a timer after the browser's activation window (5 s) has expired.
  const ask = (type) =>
    app.evaluate(
      ({ type, vid }) =>
        new Promise((res) => {
          const id = `t-${Math.random()}`;
          addEventListener("message", (e) => e.data && e.data.ff === "response" && e.data.id === id && res({ active: navigator.userActivation.isActive, ...e.data.result }));
          setTimeout(() => postMessage({ ff: "request", id, type, payload: { vid, url: `https://www.vinted.it/items/${vid}-x`, want: true } }, location.origin), 6000);
        }),
      { type, vid: String(A) },
    );
  const tabsBefore = ctx.pages().length;
  const noClick = await ask("ff:vinted-favourite");
  const notAllowed = await ask("ff:open");
  log("request without a click:", noClick.code, "| other action:", notAllowed.code);
  assert.equal(noClick.code, "no_click");
  assert.equal(notAllowed.code, "forbidden");
  assert.equal(vinted.favPosts.length, 0, "nothing done on Vinted without your click");
  assert.equal(ctx.pages().length, tabsBefore, "no tab opened without your click");

  // 3) Favourite: one click adds it on Vinted, one click removes it.
  await app.screenshot({ path: `${OUT}/actions-analysis.png` });
  await favBtn.click();
  await app.locator("[data-sonner-toast]").first().waitFor({ timeout: 60000 });
  log("toast:", await app.locator("[data-sonner-toast]").first().innerText());
  await app.getByText("Added to your Vinted favourites.").waitFor({ timeout: 5000 });
  assert.deepEqual(vinted.favPosts, [A], "exactly one favourite click on Vinted");
  assert.equal(vinted.fav.get(A), true);
  let s = await until("favourite recorded", async () => ((await state(ids[A].lid)).favourite?.value === true ? state(ids[A].lid) : null));
  assert.equal(s.favourite.source, "click");
  await app.getByRole("button", { name: "In Vinted favourites" }).waitFor();
  log("favourite on → Vinted:", vinted.fav.get(A), "FlipFinder:", s.favourite.value);
  await app.getByRole("button", { name: "In Vinted favourites" }).click();
  await app.getByText("Removed from your Vinted favourites.").waitFor({ timeout: 30000 });
  assert.deepEqual(vinted.favPosts, [A, A]);
  assert.equal(vinted.fav.get(A), false);
  s = await until("favourite removed", async () => ((await state(ids[A].lid)).favourite?.value === false ? state(ids[A].lid) : null));
  log("favourite off → Vinted:", vinted.fav.get(A), "FlipFinder:", s.favourite.value);
  assert.equal(ctx.pages().length, tabsBefore, "the favourite tab closes by itself");

  // 4) Changed on Vinted directly: the next visit of the page aligns FlipFinder.
  vinted.fav.set(A, true);
  await vintedTab.goto(`https://www.vinted.it/items/${A}-x`);
  s = await until("favourite seen on the page", async () => ((await state(ids[A].lid)).favourite?.value === true ? state(ids[A].lid) : null));
  assert.equal(s.favourite.source, "page");
  await app.reload();
  await app.getByRole("button", { name: "In Vinted favourites" }).waitFor({ timeout: 30000 });
  log("favourite added on Vinted itself → FlipFinder:", s.favourite.value, `(${s.favourite.source})`);

  // 5) Signed out of Vinted: a clear warning, nothing clicked.
  vinted.signedIn = false;
  await app.getByRole("button", { name: "In Vinted favourites" }).click();
  await app.getByText(/not signed in to Vinted/).first().waitFor({ timeout: 30000 });
  assert.equal(vinted.favPosts.length, 2, "no click while signed out");
  log("signed out → warning shown, Vinted untouched");
  vinted.signedIn = true;

  // 6) Buy: availability and price checked first, then one more click opens Vinted's checkout.
  await app.getByRole("button", { name: "Buy on Vinted" }).click();
  await app.getByText(/Available at/).waitFor({ timeout: 30000 });
  await app.screenshot({ path: `${OUT}/actions-buy-check.png` });
  assert.equal(vinted.checkouts.length, 0, "the check never opens the checkout");
  await app.getByRole("button", { name: /^Open checkout/ }).click();
  await app.getByText(/Vinted checkout open/).waitFor({ timeout: 30000 });
  assert.deepEqual(vinted.checkouts, [A]);
  s = await until("checkout recorded", async () => ((await state(ids[A].lid)).checkout_opened ? state(ids[A].lid) : null));
  assert.equal(Number(s.checkout_opened.price), 20);
  assert.equal(s.purchased, null, "opening the checkout is not a purchase");
  log("checkout opened at", s.checkout_opened.price, "| purchased:", s.purchased);

  // ...you confirm the payment on Vinted: FlipFinder records the purchase with the total paid.
  const checkoutTab = await until("checkout tab", async () => ctx.pages().find((p) => p.url().includes("/checkout?")));
  await checkoutTab.click("#pay");
  s = await until("purchase recorded", async () => ((await state(ids[A].lid)).purchased ? state(ids[A].lid) : null));
  assert.equal(Number(s.purchased.price), 25.19);
  const flips = await (await api.get("/api/v1/flips")).json();
  const flip = flips.find((f) => f.opportunity_id === ids[A].opp);
  assert.ok(flip, "the purchase is in Flips");
  assert.equal(Number(flip.purchase_price), 20);
  log("purchased: paid", s.purchased.price, "| flip purchase price", flip.purchase_price, "total cost", flip.total_cost);
  await app.reload();
  await app.locator("p:visible", { hasText: /Bought .* paid/ }).waitFor({ timeout: 30000 });
  await app.screenshot({ path: `${OUT}/actions-bought.png` });
  await checkoutTab.close();

  // 7) Price changed since the analysis: warned before the checkout.
  ITEMS[B].price = 27;
  await app.goto(`${APP}/deals/${ids[B].opp}`);
  await app.getByRole("button", { name: "Buy on Vinted" }).click();
  await app.getByText(/Price changed/).waitFor({ timeout: 30000 });
  const changedText = await app.getByText(/Price changed/).textContent();
  await app.getByRole("button", { name: /Open checkout at/ }).waitFor();
  await app.screenshot({ path: `${OUT}/actions-price-changed.png` });
  await app.getByRole("button", { name: "Cancel" }).click();
  log("price change warning:", changedText.trim());
  assert.match(changedText, /30.*27/);
  assert.equal(vinted.checkouts.length, 1, "no checkout without your second click");

  // 8) Reserved for someone else: warned, no checkout button.
  ITEMS[C].reserved = true;
  await app.goto(`${APP}/deals/${ids[C].opp}`);
  await app.getByRole("button", { name: "Buy on Vinted" }).click();
  await app.getByText(/reserved for another buyer/).waitFor({ timeout: 30000 });
  assert.equal(await app.getByRole("button", { name: /^Open checkout/ }).count(), 0);
  await app.screenshot({ path: `${OUT}/actions-reserved.png` });
  await app.getByRole("button", { name: "Cancel" }).click();
  log("reserved → warning, no checkout button");

  // 9) Tracking page: the same Buy.
  await app.goto(`${APP}/items/${B}`);
  await app.getByRole("button", { name: "Buy on Vinted" }).click();
  await app.getByText(/Available at|Price changed/).waitFor({ timeout: 30000 });
  await app.getByRole("button", { name: "Cancel" }).click();
  log("tracking page → Buy check works");

  // 10) Extension panel, on the listing you are looking at: check, warning, open the checkout.
  for (const p of ctx.pages()) if (p !== app && p !== vintedTab) await p.close();
  await vintedTab.goto(`https://www.vinted.it/items/${B}-x`);
  await vintedTab.waitForTimeout(1500);
  ITEMS[B].price = 25;
  const tabId = await until("tab registered", () =>
    sw.evaluate(async (vid) => {
      const tabs = (await chrome.storage.session.get("tabs")).tabs || {};
      const hit = Object.entries(tabs).find(([, t]) => t.url.includes(`/items/${vid}`));
      return hit ? Number(hit[0]) : null;
    }, B),
  );
  const panel = await ctx.newPage();
  await panel.setViewportSize({ width: 380, height: 900 });
  await panel.goto(`chrome-extension://${extId}/src/panel.html?tabId=${tabId}`);
  await panel.getByRole("button", { name: "Acquista su Vinted" }).click({ timeout: 30000 });
  const note = panel.locator(".buy-note");
  await until("panel check", async () => !/^(|Verifico.*)$/.test((await note.textContent()).trim()), 30000);
  log("panel note:", (await note.textContent()).trim());
  await panel.getByRole("button", { name: /Apri il checkout/ }).waitFor({ timeout: 5000 });
  log("panel:", (await note.textContent()).trim());
  assert.match(await note.textContent(), /prezzo cambiato da 27,00.* a 25,00/);
  await panel.screenshot({ path: `${OUT}/actions-panel.png`, fullPage: true });
  await panel.getByRole("button", { name: /Apri il checkout/ }).click();
  await until("panel checkout", async () => /Checkout aperto/.test(await note.textContent()));
  s = await until("checkout B recorded", async () => ((await state(ids[B].lid)).checkout_opened ? state(ids[B].lid) : null));
  assert.equal(Number(s.checkout_opened.price), 25);
  log("panel → checkout opened at", s.checkout_opened.price);

  // 11) You leave that checkout for another listing: a later confirmation in that tab is not
  // attributed to it.
  const tabB = await until("checkout tab B", async () => ctx.pages().find((p) => p.url().includes(`transaction_id=9${B}`)));
  await tabB.goto(`https://www.vinted.it/items/${C}-x`);
  await tabB.waitForTimeout(800);
  await tabB.goto(`https://www.vinted.it/checkout/success?transaction_id=9${A}`);
  await tabB.waitForTimeout(3000);
  assert.equal((await state(ids[B].lid)).purchased, null, "abandoned checkout is never recorded as bought");
  log("abandoned checkout → not recorded as a purchase");

  await ctx.close();
  server.close();
  console.log("OK actions e2e");
})().catch(async (err) => {
  console.error(err);
  await diagnose();
  process.exit(1);
});
