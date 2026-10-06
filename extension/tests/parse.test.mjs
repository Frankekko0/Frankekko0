// Run with: node --test extension/tests
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
require("../src/parser-config.js");
const P = require("../src/parse.js");
const C = P.compileConfig(globalThis.FF_PARSER_CONFIG);

// Fixtures shared with the server's parser tests: both sides must read them the same way.
const FIX = join(here, "..", "..", "backend", "tests", "fixtures", "vinted");
const fixture = (name) => readFileSync(join(FIX, name), "utf8");
const NOW = Date.parse("2026-10-06T12:00:00Z");

test("the bundled parser configuration is the server's file", async () => {
  const { render } = await import("../tools/sync-parser-config.mjs");
  const wanted = render(readFileSync(join(here, "..", "..", "backend", "app", "acquisition", "vinted_parser.json"), "utf8"));
  assert.equal(readFileSync(join(here, "..", "src", "parser-config.js"), "utf8"), wanted, "run node extension/tools/sync-parser-config.mjs");
});

test("the extension runs on exactly the Vinted domains of the configuration", () => {
  const manifest = JSON.parse(readFileSync(join(here, "..", "manifest.json"), "utf8"));
  const hosts = manifest.content_scripts[0].matches.map((m) => new URL(m.replace("/*", "/")).hostname.replace(/^www\./, ""));
  assert.deepEqual([...hosts].sort(), [...C.domains].sort());
  assert.deepEqual(manifest.permissions.sort(), ["alarms", "sidePanel", "storage"]);
  assert.equal(manifest.host_permissions, undefined); // FlipFinder's address is asked at pairing time only
});

test("item page: every field matches the shared expectation (same as the server)", async () => {
  const expected = JSON.parse(fixture("item_active.expected.json"));
  const item = P.parseItem(P.collectHtml(fixture("item_active.html")), `${expected.url}?referrer=catalog&time=1`, NOW, C);
  const num = (v) => (v === null ? null : Number(v));
  assert.deepEqual(
    {
      vinted_id: item.vinted_id,
      url: item.url,
      title: item.title,
      price: item.price,
      currency: item.currency,
      brand: item.brand,
      size: item.size,
      condition: item.condition,
      condition_label: item.condition_label,
      color: item.color,
      material: item.material,
      category_path: item.category_path,
      favourite_count: item.favourite_count,
      view_count: item.view_count,
      published_at: item.published_at,
      status: item.status,
      buyer_protection_fee: item.buyer_protection_fee,
      shipping_fee: item.shipping_fee,
      images: item.images,
      seller_rating: item.seller_rating,
      seller_review_count: item.seller_review_count,
    },
    {
      ...Object.fromEntries(Object.entries(expected).filter(([k]) => k !== "seller_key_from_member")),
      price: num(expected.price),
      buyer_protection_fee: num(expected.buyer_protection_fee),
      shipping_fee: num(expected.shipping_fee),
      seller_rating: num(expected.seller_rating),
    },
  );
  assert.equal(item.complete, true);
  // The seller becomes the same one-way hash the server computes; the member id stays here.
  assert.equal(item.member_id, expected.seller_key_from_member);
  const payload = await P.itemPayload(item, C);
  assert.equal(payload.seller_key, "h:2a1e19267046c92a7d19c32d");
  assert.ok(!JSON.stringify(payload).includes("98765") && !JSON.stringify(payload).includes("armadio"));
  assert.equal(payload.category_path, "Uomo > Abbigliamento > Felpe e maglioni");
  assert.equal(payload.condition, "Ottime");
});

test("sold and removed pages", () => {
  const sold = P.parseItem(P.collectHtml(fixture("item_sold.html")), "https://www.vinted.fr/items/5555-pull", NOW, C);
  assert.deepEqual([sold.status, sold.vinted_id, sold.price], ["sold", "5555", 35]);
  const gone = P.parseItem(P.collectHtml(fixture("item_removed.html")), "https://www.vinted.it/items/777-x", NOW, C);
  assert.equal(gone.status, "removed");
  assert.equal(gone.complete, false);
});

test("after client-side navigation, the previous item's data is never used", () => {
  const html = fixture("item_active.html");
  const collected = P.collectHtml(html);
  // Same document, but the user is now on another item: scripts and JSON-LD describe the old one.
  const other = P.parseItem({ ...collected, texts: ["Giacca nuova", "45,00 €"], heading: "Giacca nuova", canonical: null, meta: {} }, "https://www.vinted.it/items/1111-giacca", NOW, C, {
    useScripts: false,
  });
  assert.equal(other.title, "Giacca nuova");
  assert.equal(other.price, 45);
  assert.equal(other.favourite_count, null); // not the old item's 17
  assert.deepEqual(other.images, []);
  assert.ok(other.sources.includes("stale-jsonld"));
});

test("HTML collector: entities, comments, scripts and odd markup", () => {
  const c = P.collectHtml(
    '<!doctype html><title>T &amp; co</title><!-- <h1>no</h1> --><script>var x = "<h1>no</h1>";</script>' +
      '<meta property="og:title" content="A &quot;B&quot;"><h1>Felpa <b>blu</b> &euro;</h1><svg><text>skip</text></svg><p>1 &lt; 2</p><a href="/member/12-x">x</a> < stray',
  );
  assert.equal(c.heading, "Felpa blu €");
  assert.deepEqual(c.meta["og:title"], ['A "B"']);
  assert.ok(c.texts.includes("1 < 2") && !c.texts.includes("skip") && !c.texts.some((t) => t.includes("no</h1>")));
  assert.deepEqual(c.memberLinks, ["/member/12-x"]);
  assert.equal(c.scripts.length, 1);
});

test("prices, currencies and conditions across Vinted markets", () => {
  for (const [text, price, currency] of [
    ["18,00 €", 18, "EUR"],
    ["€1.234,50", 1234.5, "EUR"],
    ["1 234,50 zł", 1234.5, "PLN"],
    ["£12.00", 12, "GBP"],
    ["19,60 € include la Protezione acquisti", 19.6, "EUR"],
  ]) {
    const hit = P.findPrice(text, C);
    assert.deepEqual([hit.price, hit.currency], [price, currency], text);
  }
  assert.equal(P.parsePrice("gratis"), null);
  assert.equal(P.parsePrice("0"), null);
  const conditions = {
    "Nuovo senza cartellino": "new_without_tags",
    "New with tags": "new_with_tags",
    "Très bon état": "very_good",
    "Sehr gut": "very_good",
    "Buone condizioni": "good",
    Satisfactory: "satisfactory",
  };
  for (const [label, want] of Object.entries(conditions)) assert.equal(P.normalizeCondition(label, C), want, label);
  assert.equal(P.labelKey("Taglia:", C), "size");
  assert.equal(P.labelKey("Größe", C), "size");
  assert.equal(P.labelKey("Caricato", C), "uploaded");
  assert.equal(P.relativeTime("3 giorni fa", NOW, C).toISOString(), "2026-10-03T12:00:00.000Z");
  assert.equal(P.relativeTime("ieri", NOW, C).toISOString(), "2026-10-05T12:00:00.000Z");
});

test("page types from the configuration", () => {
  assert.equal(P.pageType("/items/123-felpa", C), "item");
  assert.equal(P.pageType("/member/items/favourite_list", C), "favourites");
  assert.equal(P.pageType("/member/123-armadio", C), "closet");
  assert.equal(P.pageType("/catalog", C), "catalog");
});

// ------------------------------------------------------------------ cards
function card(id, summary, extra = {}) {
  return { href: `/items/${id}-polo-ralph-lauren?referrer=catalog`, summary, alt: summary, image: `https://images1.vinted.net/t/${id}.jpeg`, testids: {}, texts: [], ...extra };
}
const SEARCH = "https://www.vinted.it/catalog?search_text=polo";

test("card summary: title, attributes, item price and buyer-protection total", () => {
  const s = P.parseCardSummary("Polo Ralph Lauren, slim fit, brand: Ralph Lauren, condizioni: Ottime, taglia: M, 15,00 €, 16,45 € include la Protezione acquisti", C);
  assert.equal(s.title, "Polo Ralph Lauren, slim fit");
  assert.deepEqual(s.attrs, { brand: "Ralph Lauren", condition: "Ottime", size: "M" });
  assert.deepEqual([s.price, s.currency, s.total], [15, "EUR", 16.45]);
  const uk = P.parseCardSummary("Levi's 501, brand: Levi's, condition: Good, size: W32, £12.00, £13.35 includes Buyer Protection", C);
  assert.deepEqual([uk.price, uk.currency], [12, "GBP"]);
  assert.equal(P.parseCardSummary("Air Max, brand: Nike, taglia: 42,5, 30,00 €", C).price, 30); // a shoe size is not a price
});

test("cards: once each, status badges, favourites, protection fee; unreadable ones skipped", () => {
  const raw = [
    card("111", "Polo Ralph Lauren blu, brand: Ralph Lauren, condizioni: Ottime, taglia: M, 15,00 €, 16,45 € include la Protezione acquisti", { favourites: "Aggiunto ai preferiti da 12 persone" }),
    card("111", "Polo Ralph Lauren blu, brand: Ralph Lauren, condizioni: Ottime, taglia: M, 15,00 €"), // promoted + organic
    card("222", "Polo rossa, brand: Ralph Lauren, condizioni: Nuovo senza cartellino, taglia: L, 9,00 €", { texts: ["Venduto", "Polo rossa"] }),
    card("333", "", { alt: "", testids: { "description-title": "Ralph Lauren", "description-subtitle": "S · Buone", "price-text": "12,50 €" }, texts: ["Riservato", "12,50 €", "13,83 € incl."] }),
    card("444", "Polo, brand: Ralph Lauren", { alt: "" }),
    { href: "/member/123-armadio", summary: "Armadio" },
  ];
  const { items, unreadable } = P.parseCards(raw, SEARCH, C);
  assert.equal(unreadable, 2);
  assert.deepEqual(items.map((i) => [i.vinted_id, i.status]), [["111", "active"], ["222", "sold"], ["333", "reserved"]]);
  const [a, , c] = items;
  assert.deepEqual([a.url, a.price, a.favourite_count, a.buyer_protection_fee, a.condition], ["https://www.vinted.it/items/111-polo-ralph-lauren", 15, 12, 1.45, "very_good"]);
  assert.deepEqual([c.title, c.price, c.size, c.buyer_protection_fee], ["Ralph Lauren S", 12.5, "S", 1.33]);
});

test("capture payload stays within the server's limits", () => {
  const ok = P.capturePayload(
    { url: "https://www.vinted.it/items/9-x?ref=2", title: "  Felpa  ", price: 12.345, brand: "B".repeat(300), image_urls: ["javascript:alert(1)", "https://images1.vinted.net/a.jpg"], seller_rating: 7, favourite_count: -1, status: "sold" },
    C,
  );
  assert.equal(ok.url, "https://www.vinted.it/items/9-x");
  assert.equal(ok.title, "Felpa");
  assert.equal(ok.price, 12.35);
  assert.equal(ok.brand.length, 120);
  assert.deepEqual(ok.image_urls, ["https://images1.vinted.net/a.jpg"]);
  assert.equal(ok.seller_rating, undefined);
  assert.equal(ok.favourite_count, undefined);
  assert.equal(ok.status, "sold");
  assert.equal(P.capturePayload({ url: "https://evil.example/items/9", title: "Felpa", price: 5 }, C), null);
  assert.equal(P.capturePayload({ url: "https://www.vinted.it.evil.com/items/9", title: "Felpa", price: 5 }, C), null);
  assert.equal(P.capturePayload({ url: "https://www.vinted.pl/items/9", title: "Bluza", price: 50, currency: "PLN" }, C), null); // euro only
  assert.equal(P.capturePayload({ url: "https://www.vinted.it/items/9", title: "Fe", price: 5 }, C), null);
});

test("links without pairing round-trip (UTF-8 safe, compressed batch)", async () => {
  const url = P.analyzeUrl("http://localhost:3000/", { title: "Felpa “blu” — è perfetta", price: 18 });
  const payload = url.split("#import=")[1];
  const json = Buffer.from(payload.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
  assert.deepEqual(JSON.parse(json), { v: 1, source: "vinted", title: "Felpa “blu” — è perfetta", price: 18 });
  const { items } = P.parseCards(Array.from({ length: 60 }, (_, i) => card(String(5000 + i), `Felpa ${i}, brand: Ralph Lauren, 18,00 €`)), SEARCH, C);
  const batchUrl = await P.importUrl("https://flip.example.com/", items, "felpa");
  const b64 = batchUrl.split("#batch=")[1].replace(/-/g, "+").replace(/_/g, "/");
  const bytes = Uint8Array.from(atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)), (ch) => ch.charCodeAt(0));
  const text = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"))).text();
  assert.deepEqual(JSON.parse(text), { v: 1, source: "vinted_search", query: "felpa", items });
});
