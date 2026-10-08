// End-to-end: Vinted favourite and Buy, started from FlipFinder's Analysis page, the tracking page
// and the extension panel. Real extension in Chromium, fake Vinted pages (routed) that keep the
// favourite on their "server" and lead to a checkout, a running FlipFinder (APP_URL).
// Every action starts from the web app's own buttons, clicked for real: Buy opens Vinted's
// checkout at the first click (ff:vinted-buy); only a changed price asks for a second click.
// The bridge accepts one action per real click: the few requests sent by a page script here
// follow a real click on the page, and only to show what is refused.
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
const A = ID0 + 1; // bought end to end, checkout at the first click
const B = ID0 + 2; // its price changes (dialog, cancelled; then the panel)
const C = ID0 + 3; // reserved for someone else
const D = ID0 + 4; // a page that differs from the expected one
const E = ID0 + 5; // first-click buy with no Vinted tab open
const F = ID0 + 6; // first click: the price changed, nothing clicked, then "Open checkout at" it
const G = ID0 + 7; // first click: sold in the meantime
const H = ID0 + 8; // first click with the service worker stopped
const J = ID0 + 9; // first click in a fresh browser
// favMode: "aria" (pressed state), "icon" (only the icon changes; state in the page data),
// "blind" (state nowhere). buyMode: "normal", "decoy" (a look-alike button first), "dead".
const ITEMS = {
  [A]: { title: "Polo Ralph Lauren Custom Slim Fit blu", price: 20, reserved: false },
  [B]: { title: "Felpa Polo Ralph Lauren grigia zip", price: 30, reserved: false },
  [C]: { title: "Maglione Polo Bear Ralph Lauren", price: 45, reserved: false },
  [D]: { title: "Camicia Ralph Lauren Oxford azzurra", price: 35, reserved: false },
  // consent: a cookie banner over the page; hydrateMs: the buttons work only that long after load.
  [E]: { title: "Giacca Harrington Ralph Lauren blu", price: 40, reserved: false, consent: true, hydrateMs: 250 },
  [F]: { title: "Polo Ralph Lauren piqué bianca", price: 50, reserved: false },
  [G]: { title: "Camicia Ralph Lauren lino bianca", price: 38, reserved: false },
  [H]: { title: "Cardigan Ralph Lauren cotone blu", price: 42, reserved: false, consent: true, hydrateMs: 250 },
  [J]: { title: "Gilet Ralph Lauren trapuntato verde", price: 33, reserved: false, consent: true, hydrateMs: 250 },
};
// What Vinted's servers know: your session, your favourites, the pages served.
const vinted = { signedIn: true, fav: new Map(), favPosts: [], checkouts: [], successes: [], decoys: 0 };

const euro = (v) => v.toFixed(2).replace(".", ",");
function itemHtml(id) {
  const it = ITEMS[id];
  const fav = vinted.fav.get(id) === true;
  const ld = { "@context": "https://schema.org", "@type": "Product", name: it.title, url: `https://www.vinted.it/items/${id}-x`, description: "Originale Ralph Lauren, etichetta interna presente, nessun difetto. Misure: ascella-ascella 52 cm, lunghezza 70 cm.", image: [1, 2, 3].map((n) => `https://images1.vinted.net/t/${id}/${n}.jpeg`), brand: { "@type": "Brand", name: "Ralph Lauren" }, offers: { "@type": "Offer", price: it.price.toFixed(2), priceCurrency: "EUR", availability: it.sold ? "https://schema.org/SoldOut" : "https://schema.org/InStock" } };
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
  ${favButton(it.favMode || "aria", fav)}
  ${it.buyMode === "decoy" ? '<button type="button" data-testid="item-buyer-protection-button" onclick="fetch(\'/api/decoy\', { method: \'POST\' })">Protezione acquisti</button>' : ""}
  ${it.sold ? '<p data-testid="item-status--sold">Venduto</p>' : it.reserved ? '<p data-testid="item-status--reserved">Riservato</p>' : `<button type="button" data-testid="${it.buyMode === "decoy" ? "item-buy-btn" : "item-buy-button"}">Acquista</button>`}
</div>
</main>
${it.consent ? '<div id="onetrust-banner-sdk" style="position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.6)"><button id="onetrust-accept-btn-handler">Accetta tutti</button></div>' : ""}
<script>
const hydrate = () => {
const fb = document.querySelector('[data-testid="item-favourite-button"]');
fb.addEventListener("click", async () => {
  const r = await fetch("/api/v2/items/${id}/favourite", { method: "POST" }).then((x) => x.json());
  if (fb.hasAttribute("aria-pressed")) {
    fb.setAttribute("aria-pressed", String(r.on));
    fb.setAttribute("aria-label", r.on ? "Rimuovi dai preferiti" : "Aggiungi ai preferiti");
  } else fb.querySelector("path").setAttribute("d", r.on ? "M1 1h8v8H1z" : "M1 1h8");
});
const bb = document.querySelector('[data-testid^="item-buy-b"]');
if (bb && ${it.buyMode !== "dead"}) bb.addEventListener("click", () => { location.href = "/checkout?transaction_id=9${id}"; });
};
${it.hydrateMs ? `addEventListener("load", () => setTimeout(hydrate, ${it.hydrateMs}));` : "hydrate();"}
</script>
<script>self.__next_f.push([1,"{\\"item\\":{\\"id\\":${id},\\"favourite_count\\":21,\\"view_count\\":310,${it.favMode === "blind" ? "" : `\\"is_favourite\\":${fav},`}\\"is_reserved\\":${it.reserved},\\"is_closed\\":${Boolean(it.sold)},\\"user\\":{\\"feedback_reputation\\":0.96,\\"feedback_count\\":52}}}"])</script></body></html>`;
}
// The favourite button: with a pressed state and label, or only an icon that changes.
const favButton = (mode, fav) =>
  mode === "aria"
    ? `<button type="button" data-testid="item-favourite-button" aria-pressed="${fav}" aria-label="${fav ? "Rimuovi dai preferiti" : "Aggiungi ai preferiti"}">♡ 21</button>`
    : `<button type="button" data-testid="item-favourite-button" aria-label="Preferiti"><svg width="16" height="16"><path d="${fav ? "M1 1h8v8H1z" : "M1 1h8"}"/></svg></button>`;
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
let current = null; // the FlipFinder page in use, for the failure screenshot
(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ffext-"));
  fs.cpSync(SRC, dir, { recursive: true });
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json")));
  manifest.host_permissions = [`${new URL(APP).origin}/*`];
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest));

  // A fresh FlipFinder account and a pairing key, as Settings → Browser extension creates.
  // ignoreHTTPSErrors: a production stack tested at https://localhost has Caddy's local certificate.
  const api = await request.newContext({ baseURL: APP, ignoreHTTPSErrors: true });
  const reg = await api.post("/api/v1/auth/register", { data: { email: `e2e-actions-${Date.now()}@example.com`, password: "E2e-test-pass-2026!" } });
  if (!reg.ok()) throw new Error("register: " + (await reg.text()));
  const session = await reg.json();
  const created = await api.post("/api/v1/extension/keys", { data: { name: "e2e" }, headers: { "X-CSRF-Token": session.csrf_token } });
  const { key } = await created.json();
  const capture = await request.newContext({ baseURL: APP, ignoreHTTPSErrors: true, extraHTTPHeaders: { Authorization: `Bearer ${key}` } });

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
    if (u.pathname === "/api/decoy") return (vinted.decoys += 1), send("{}", "application/json");
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
    ignoreHTTPSErrors: true,
  });
  await ctx.addCookies((await api.storageState()).cookies); // signed in to FlipFinder in this browser
  // The extension's service worker (not a web page's own, such as the app's PWA worker).
  const isExt = (w) => w.url().startsWith("chrome-extension://");
  let sw = ctx.serviceWorkers().find(isExt);
  if (!sw) sw = await ctx.waitForEvent("serviceworker", { predicate: isExt });
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
      await new Promise((res) => setTimeout(res, 500)); // gentle with the API's rate limits
    }
  };

  await sw.evaluate(async ({ app, key }) => {
    await chrome.storage.local.set({ apiKey: key });
    await chrome.storage.sync.set({ options: { appUrl: app } });
  }, { app: APP, key });

  // 1) You browse the three listings on Vinted: the extension records them in FlipFinder.
  const vintedTab = await ctx.newPage();
  const ids = {};
  for (const id of [A, B, C, D, E, F, G, H, J]) {
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
  current = app;
  diagnose = async () => {
    const toasts = await current.evaluate(() => [...document.querySelectorAll("[data-sonner-toast]")].map((t) => t.innerText)).catch(() => []);
    const errors = await sw.evaluate(async () => (await chrome.storage.local.get("errors")).errors || []).catch(() => []);
    console.error("toasts:", JSON.stringify(toasts), "\nextension errors:", JSON.stringify(errors).slice(0, 1500), "\ntabs:", current.context().pages().map((p) => p.url()).join(" "));
    await current.screenshot({ path: `${OUT}/actions-failure.png` }).catch(() => {});
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

  // 5b) One click, one action: a page script riding on one real click gets that click's single
  // action; a second request right behind it - activation still on - is refused, Vinted untouched.
  const neutralClick = async (page) => {
    await page.evaluate(() => {
      if (document.getElementById("e2e-neutral")) return;
      const d = document.createElement("div"); // an empty spot of the page, nothing to activate
      d.id = "e2e-neutral";
      d.style.cssText = "position:fixed;left:0;bottom:0;width:24px;height:24px;z-index:2147483647";
      document.body.appendChild(d);
    });
    await page.locator("#e2e-neutral").click();
  };
  // Requests posted by a page script, all at once; activeAtSend: the browser's user activation then.
  const postRequests = (page, list) =>
    page.evaluate(
      (list) =>
        Promise.all(
          list.map(
            ({ type, payload }) =>
              new Promise((res) => {
                const id = `p-${Math.random()}`;
                const activeAtSend = navigator.userActivation.isActive;
                const on = (e) => {
                  if (!e.data || e.data.ff !== "response" || e.data.id !== id) return;
                  removeEventListener("message", on);
                  res({ activeAtSend, ...e.data.result });
                };
                addEventListener("message", on);
                postMessage({ ff: "request", id, type, payload }, location.origin);
              }),
          ),
        ),
      list,
    );
  const item = (id, extra = {}) => ({ vid: String(id), url: `https://www.vinted.it/items/${id}-x`, ...extra });
  const favs0 = vinted.favPosts.length;
  const checkouts0 = vinted.checkouts.length;
  const tabs0 = ctx.pages().length;
  await neutralClick(app);
  const [firstReq, secondReq] = await postRequests(app, [
    { type: "ff:vinted-favourite", payload: item(C, { want: true }) },
    { type: "ff:vinted-buy", payload: item(E, { expect_price: 40 }) },
  ]);
  log("one click, two requests:", JSON.stringify({ ok: firstReq.ok, favourite: firstReq.favourite }), "|", JSON.stringify({ code: secondReq.code, activeAtSend: secondReq.activeAtSend }));
  assert.equal(firstReq.ok, true, JSON.stringify(firstReq));
  assert.equal(vinted.fav.get(C), true);
  assert.deepEqual(vinted.favPosts.slice(favs0), [C], "the click's one action, done once");
  assert.equal(secondReq.code, "no_click");
  assert.equal(secondReq.activeAtSend, true, "refused by the one-click-one-action rule, not by an expired activation");
  assert.equal(vinted.checkouts.length, checkouts0, "the second request did nothing on Vinted");
  await until("favourite tab closed", async () => ctx.pages().length === tabs0, 5000);

  // The web app's Buy button; clicked, then what it shows (toast or dialog) and how long it took
  // from the click (the page already rendered and the button enabled).
  const buyButton = (page) => page.getByRole("button", { name: "Buy on Vinted" });
  const clickFor = async (page, button, re, ms = 60000) => {
    await button.waitFor({ timeout: 30000 });
    await until("button enabled", () => button.isEnabled(), 30000);
    await page.bringToFront(); // you click in the FlipFinder tab you are looking at
    const t = Date.now();
    await button.click();
    const el = page.getByText(re).first();
    await el.waitFor({ timeout: ms });
    return { text: (await el.innerText()).trim().replace(/\s+/g, " "), ms: Date.now() - t };
  };
  const noDialog = async (page) => assert.equal(await page.getByRole("dialog").count(), 0, "no dialog: one click is enough");
  const once = (id) => vinted.checkouts.filter((v) => v === id).length;

  // 6) Buy at the first click: the extension opens the listing, finds it on sale at the analysed
  // price and presses Acquista once; Vinted's checkout opens, no dialog in between.
  let out = await clickFor(app, buyButton(app), /Vinted checkout open/);
  log("first-click buy:", JSON.stringify(out.text), `${out.ms} ms`);
  assert.match(out.text, /Vinted checkout open at €20:/);
  await noDialog(app);
  assert.deepEqual(vinted.checkouts, [A], "exactly one Buy click on Vinted, for this listing");
  s = await until("checkout recorded", async () => ((await state(ids[A].lid)).checkout_opened ? state(ids[A].lid) : null));
  assert.equal(Number(s.checkout_opened.price), 20);
  assert.equal(s.purchased, null, "opening the checkout is not a purchase");
  log("checkout opened at", s.checkout_opened.price, "| purchased:", s.purchased);
  await app.screenshot({ path: `${OUT}/actions-buy-first-click.png` });

  // ...you confirm the payment on Vinted: FlipFinder records the purchase with the total paid.
  const checkoutTab = await until("checkout tab", async () => ctx.pages().find((p) => p.url().includes(`/checkout?transaction_id=9${A}`)));
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
  await app.getByRole("button", { name: "Bought", exact: true, disabled: true }).waitFor(); // Buy, now disabled
  assert.equal(await buyButton(app).count(), 0, "no second purchase of a bought listing");
  await app.screenshot({ path: `${OUT}/actions-bought.png` });
  await checkoutTab.close();

  // 7) Price changed since the analysis: nothing clicked, a dialog with the new price; Cancel.
  ITEMS[B].price = 27;
  await app.goto(`${APP}/deals/${ids[B].opp}`);
  out = await clickFor(app, buyButton(app), /Price changed/);
  await app.getByText(/nothing was clicked/).waitFor();
  await app.getByRole("button", { name: "Open checkout at €27" }).waitFor();
  await app.screenshot({ path: `${OUT}/actions-price-changed.png` });
  log("price change dialog:", JSON.stringify(out.text), `${out.ms} ms`);
  assert.match(out.text, /€30 → €27\./);
  assert.equal(vinted.checkouts.length, 1, "no click on Vinted at a price you did not see");
  await app.getByRole("button", { name: "Cancel" }).click();
  await app.getByRole("dialog").waitFor({ state: "detached" });
  assert.equal(vinted.checkouts.length, 1, "cancelled: no checkout");

  // 8) Reserved for someone else: a warning, no dialog, nothing clicked, its tab goes away.
  ITEMS[C].reserved = true;
  await app.goto(`${APP}/deals/${ids[C].opp}`);
  let tabsNow = ctx.pages().length;
  out = await clickFor(app, buyButton(app), /reserved for another buyer/);
  await noDialog(app);
  assert.ok(!vinted.checkouts.includes(C));
  await until("reserved tab closed", async () => ctx.pages().length === tabsNow, 5000);
  await app.screenshot({ path: `${OUT}/actions-reserved.png` });
  log("reserved →", JSON.stringify(out.text));

  // 9) Tracking page: the same Buy (the reserved listing: the same answer, nothing clicked).
  await app.goto(`${APP}/items/${C}`);
  out = await clickFor(app, buyButton(app), /reserved for another buyer/);
  await noDialog(app);
  assert.ok(!vinted.checkouts.includes(C));
  log("tracking page → Buy:", JSON.stringify(out.text));

  // 10a) Vinted's page differs from the expected one: never a blind or wrong click.
  ITEMS[D].favMode = "icon"; // the heart only changes its icon: checked by reading the page again
  await app.goto(`${APP}/deals/${ids[D].opp}`);
  await app.getByRole("button", { name: "Add to Vinted favourites" }).click();
  await app.getByText("Added to your Vinted favourites.").waitFor({ timeout: 45000 });
  assert.deepEqual(vinted.favPosts.filter((v) => v === D), [D], "one click on Vinted");
  assert.equal(vinted.fav.get(D), true);
  s = await until("favourite D recorded", async () => ((await state(ids[D].lid)).favourite?.value === true ? state(ids[D].lid) : null));
  log("heart without a readable state → confirmed by reading the page again:", s.favourite.value, `(${s.favourite.source})`);
  ITEMS[D].favMode = "blind"; // state nowhere on the page: no click at all
  await app.getByRole("button", { name: "In Vinted favourites" }).click();
  await app.getByText(/can't read whether the listing is already in your Vinted favourites/).waitFor({ timeout: 45000 });
  assert.equal(vinted.favPosts.filter((v) => v === D).length, 1, "no blind click");
  assert.equal(vinted.fav.get(D), true);
  log("heart state unreadable → refused, no click");
  ITEMS[D].buyMode = "decoy"; // "Protezione acquisti" before the real "Acquista"
  out = await clickFor(app, buyButton(app), /Vinted checkout open/);
  await noDialog(app);
  assert.equal(vinted.decoys, 0, "the look-alike button is never clicked");
  assert.equal(once(D), 1, "Acquista pressed once");
  const opened = await until("checkout D recorded", async () => (await state(ids[D].lid)).checkout_opened);
  log("look-alike button next to Acquista → ignored, checkout opened at the first click", `${out.ms} ms`);
  for (const p of ctx.pages()) if (p.url().includes(`transaction_id=9${D}`)) await p.close();
  ITEMS[D].buyMode = "dead"; // Acquista doesn't lead to the checkout
  out = await clickFor(app, buyButton(app), /the checkout did not open/, 60000);
  assert.equal(once(D), 1);
  assert.equal((await state(ids[D].lid)).checkout_opened.at, opened.at, "not recorded as opened");
  log("Acquista without a checkout → reported, nothing recorded:", JSON.stringify(out.text));

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
  const widths = await panel.evaluate(() => {
    const cw = document.documentElement.clientWidth;
    const wide = [...document.querySelectorAll("body *")].filter((e) => e.getBoundingClientRect().right > cw + 0.5).slice(0, 5);
    return { scroll: document.documentElement.scrollWidth, client: cw, wide: wide.map((e) => `${e.tagName}.${e.className}:${Math.round(e.getBoundingClientRect().right)}`) };
  });
  assert.deepEqual(widths.wide, [], `nothing wider than the panel: ${JSON.stringify(widths)}`);
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

  // 12) First-click Buy with no Vinted tab open, a cookie banner over the listing and buttons that
  // work only a moment after load.
  for (const p of ctx.pages()) if (p !== app) await p.close();
  await app.goto(`${APP}/deals/${ids[E].opp}`);
  assert.ok(!ctx.pages().some((p) => p.url().includes("vinted.it")), "no Vinted tab open");
  out = await clickFor(app, buyButton(app), /Vinted checkout open/);
  log("no Vinted tab → first-click buy:", JSON.stringify(out.text), `${out.ms} ms`);
  assert.match(out.text, /at €40:/);
  await noDialog(app);
  assert.equal(once(E), 1, "Vinted's checkout opened once");
  s = await until("one-click checkout recorded", async () => ((await state(ids[E].lid)).checkout_opened ? state(ids[E].lid) : null));
  assert.equal(Number(s.checkout_opened.price), 40);
  assert.ok(ctx.pages().some((p) => p.url().includes(`transaction_id=9${E}`)), "the checkout tab is there for your payment");

  // ...the price changed since the analysis: nothing clicked, the dialog offers the new price;
  // "Open checkout at €44" uses the tab already read.
  ITEMS[F].price = 44;
  await app.goto(`${APP}/deals/${ids[F].opp}`);
  const before = vinted.checkouts.length;
  out = await clickFor(app, buyButton(app), /Price changed/);
  log("first click, price changed:", JSON.stringify(out.text), `${out.ms} ms`);
  assert.match(out.text, /€50 → €44\./);
  assert.equal(vinted.checkouts.length, before, "no click at a different price");
  const pagesBefore = ctx.pages().length;
  out = await clickFor(app, app.getByRole("button", { name: "Open checkout at €44" }), /Vinted checkout open/);
  assert.deepEqual(vinted.checkouts.slice(before), [F], "one Buy click, for this listing");
  assert.equal(ctx.pages().length, pagesBefore, "the tab already opened is reused");
  s = await until("checkout F recorded", async () => ((await state(ids[F].lid)).checkout_opened ? state(ids[F].lid) : null));
  assert.equal(Number(s.checkout_opened.price), 44);
  log("→ Open checkout at €44:", JSON.stringify(out.text), `${out.ms} ms`, "| same tab, recorded at", s.checkout_opened.price);

  // ...sold in the meantime: a warning, nothing clicked, the tab opened for it goes away.
  ITEMS[G].sold = true;
  await app.goto(`${APP}/deals/${ids[G].opp}`);
  tabsNow = ctx.pages().length;
  out = await clickFor(app, buyButton(app), /has been sold/);
  log("first click, sold:", JSON.stringify(out.text));
  await noDialog(app);
  assert.ok(!vinted.checkouts.includes(G));
  await until("sold tab closed", async () => ctx.pages().length === tabsNow, 5000);

  // 13) The service worker stopped right before the click (the browser stops it when idle): the
  // web app's buttons still work at the first click.
  for (const p of ctx.pages()) if (p !== app) await p.close();
  await app.goto(`${APP}/deals/${ids[H].opp}`);
  await buyButton(app).waitFor({ timeout: 30000 });
  await app.waitForFunction(() => document.documentElement.dataset.flipfinderExtension, null, { timeout: 10000 });
  // Stopped through DevTools, as the browser does after 30 s idle: its status is followed there.
  const cdp = await ctx.newCDPSession(app);
  let swStatus = "running";
  cdp.on("ServiceWorker.workerVersionUpdated", (e) => {
    for (const v of e.versions) if (v.scriptURL.startsWith(`chrome-extension://${extId}/`)) swStatus = v.runningStatus;
  });
  await cdp.send("ServiceWorker.enable");
  const stopWorker = async () => {
    ctx.waitForEvent("serviceworker", { predicate: isExt, timeout: 60000 }).then((w) => (sw = w), () => {});
    await cdp.send("ServiceWorker.stopAllWorkers");
    for (const end = Date.now() + 5000; swStatus !== "stopped"; ) {
      if (Date.now() > end) throw new Error(`service worker not stopped (${swStatus})`);
      await new Promise((res) => setTimeout(res, 20));
    }
  };
  await stopWorker();
  let t0 = Date.now();
  const [status] = await postRequests(app, [{ type: "ff:bridge-status", payload: {} }]); // no click needed to ask
  log("worker stopped → bridge status:", JSON.stringify({ ok: status.ok, paired: status.paired }), `${Date.now() - t0} ms`);
  assert.equal(status.ok, true);
  assert.equal(status.paired, true);
  await stopWorker();
  out = await clickFor(app, buyButton(app), /Vinted checkout open/);
  log("worker stopped → first-click buy:", JSON.stringify(out.text), `${out.ms} ms`);
  await noDialog(app);
  assert.equal(once(H), 1);
  for (const p of ctx.pages()) if (p !== app) await p.close();
  // ...and a FlipFinder page loaded while the worker is stopped gets its bridge.
  await stopWorker();
  await app.reload();
  await app.waitForFunction(() => document.documentElement.dataset.flipfinderExtension, null, { timeout: 10000 });
  await stopWorker();
  const favH = vinted.favPosts.length;
  out = await clickFor(app, app.getByRole("button", { name: "Add to Vinted favourites" }), "Added to your Vinted favourites.");
  log("worker stopped → favourite:", JSON.stringify(out.text), `${out.ms} ms`);
  assert.equal(vinted.fav.get(H), true);
  assert.equal(vinted.favPosts.length, favH + 1, "exactly one click");
  await cdp.detach();
  await ctx.close();

  // 14) First click in a fresh browser: never on Vinted, FlipFinder already open when the
  // extension gets its address and key (the bridge arrives without reloading the page).
  const ctx2 = await chromium.launchPersistentContext(fs.mkdtempSync(path.join(os.tmpdir(), "ffprof-")), {
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
  await ctx2.addCookies((await api.storageState()).cookies);
  const sw2 = ctx2.serviceWorkers().find(isExt) || (await ctx2.waitForEvent("serviceworker", { predicate: isExt }));
  const app2 = ctx2.pages()[0] || (await ctx2.newPage());
  current = app2;
  await app2.goto(`${APP}/deals/${ids[J].opp}`);
  await buyButton(app2).waitFor({ timeout: 30000 });
  await sw2.evaluate(async ({ app, key }) => {
    await chrome.storage.local.set({ apiKey: key });
    await chrome.storage.sync.set({ options: { appUrl: app } });
  }, { app: APP, key });
  await app2.waitForFunction(() => document.documentElement.dataset.flipfinderExtension, null, { timeout: 10000 });
  assert.ok(!ctx2.pages().some((p) => p.url().includes("vinted.it")), "never on Vinted in this browser");
  out = await clickFor(app2, buyButton(app2), /Vinted checkout open/);
  log("fresh browser → first-click buy:", JSON.stringify(out.text), `${out.ms} ms`);
  await noDialog(app2);
  assert.equal(once(J), 1);
  out = await clickFor(app2, app2.getByRole("button", { name: "Add to Vinted favourites" }), "Added to your Vinted favourites.");
  assert.equal(vinted.fav.get(J), true);
  log("fresh browser → favourite:", JSON.stringify(out.text), `${out.ms} ms`);
  await ctx2.close();
  server.close();
  console.log("OK actions e2e");
})().catch(async (err) => {
  console.error(err);
  await diagnose();
  process.exit(1);
});
