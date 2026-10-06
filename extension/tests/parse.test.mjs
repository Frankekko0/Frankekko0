// Run with: node --test extension/tests
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const P = require("../src/parse.js");

const ITEM = "https://www.vinted.it/items/4242424242-felpa-ralph-lauren-blu";

function page(overrides = {}) {
  return {
    location: `${ITEM}?referrer=catalog`,
    canonical: ITEM,
    jsonLd: [
      {
        "@context": "https://schema.org",
        "@type": "Product",
        name: "Felpa Ralph Lauren blu",
        description: "Felpa in ottime condizioni.\n\nIndossata poche volte.",
        url: ITEM,
        image: ["https://images1.vinted.net/t/a.jpeg", "https://images1.vinted.net/t/b.jpeg"],
        brand: { "@type": "Brand", name: "Ralph Lauren" },
        offers: { "@type": "Offer", price: "18.00", priceCurrency: "EUR" },
      },
    ],
    meta: { "og:title": ["Felpa Ralph Lauren blu | Vinted"], "og:image": ["https://images1.vinted.net/t/a.jpeg"] },
    pairs: [
      ["Brand", "Ralph Lauren"],
      ["Taglia", "M"],
      ["Condizioni", "Ottime"],
      ["Colore", "Blu"],
    ],
    heading: "Felpa Ralph Lauren blu",
    description: "",
    seller: "armadio_8832",
    ...overrides,
  };
}

test("parses a complete Vinted item page", () => {
  const { ok, data, missing, staleItem } = P.parseListing(page());
  assert.equal(ok, true);
  assert.deepEqual(missing, []);
  assert.equal(staleItem, false);
  assert.equal(data.url, ITEM); // canonical, without tracking query
  assert.equal(data.title, "Felpa Ralph Lauren blu");
  assert.equal(data.price, 18);
  assert.equal(data.currency, "EUR");
  assert.equal(data.brand, "Ralph Lauren");
  assert.equal(data.size, "M");
  assert.equal(data.condition, "very_good");
  assert.equal(data.color, "Blu");
  assert.equal(data.description, "Felpa in ottime condizioni.\n\nIndossata poche volte.");
  assert.deepEqual(data.image_urls, ["https://images1.vinted.net/t/a.jpeg", "https://images1.vinted.net/t/b.jpeg"]);
  assert.equal(data.seller_username, "armadio_8832");
});

test("falls back to meta tags and page text when JSON-LD is missing", () => {
  const { ok, data } = P.parseListing(
    page({
      jsonLd: [],
      meta: {
        "og:title": ["Nike Air Max 90 - Vinted"],
        "product:price:amount": ["45,50"],
        "og:description": ["Scarpe usate due volte"],
        "og:image": ["https://images1.vinted.net/t/c.jpeg", "javascript:alert(1)"],
      },
      heading: "",
      pairs: [["Marca", "Nike"], ["Condizioni", "Nuovo con cartellino"]],
    }),
  );
  assert.equal(ok, true);
  assert.equal(data.title, "Nike Air Max 90");
  assert.equal(data.price, 45.5);
  assert.equal(data.brand, "Nike");
  assert.equal(data.condition, "new_with_tags");
  assert.deepEqual(data.image_urls, ["https://images1.vinted.net/t/c.jpeg"]); // only http(s)
});

test("handles @graph JSON-LD and array offers", () => {
  const { data } = P.parseListing(
    page({
      jsonLd: [{ "@graph": [{ "@type": "BreadcrumbList" }, { "@type": ["Product"], name: "Giacca", url: ITEM, offers: [{ price: 30 }] }] }],
    }),
  );
  assert.equal(data.title, "Giacca");
  assert.equal(data.price, 30);
});

test("ignores structured data left over from the previous item (client-side navigation)", () => {
  const other = "https://www.vinted.it/items/1111-old-item";
  const res = P.parseListing(
    page({
      canonical: other,
      jsonLd: [{ "@type": "Product", name: "Old item", url: other, offers: { price: "99" } }],
      heading: "Nuovo articolo",
      meta: {},
    }),
  );
  assert.equal(res.staleItem, true);
  assert.equal(res.data.title, "Nuovo articolo");
  assert.equal(res.data.price, null); // never the old item's price
  assert.deepEqual(res.missing, ["price"]);
  assert.equal(res.data.url, ITEM);
});

test("price parsing across formats", () => {
  assert.equal(P.parsePrice("18,00 €"), 18);
  assert.equal(P.parsePrice("€1.234,50"), 1234.5);
  assert.equal(P.parsePrice("1,234.50"), 1234.5);
  assert.equal(P.parsePrice("1.234"), 1234);
  assert.equal(P.parsePrice("12.5"), 12.5);
  assert.equal(P.parsePrice(20), 20);
  assert.equal(P.parsePrice("gratis"), null);
  assert.equal(P.parsePrice("0"), null);
});

test("condition labels in several Vinted languages", () => {
  const cases = {
    "Nuovo senza cartellino": "new_without_tags",
    "New with tags": "new_with_tags",
    "Très bon état": "very_good",
    "Sehr gut": "very_good",
    "Buone condizioni": "good",
    "Bon état": "good",
    "Discrete condizioni": "satisfactory",
    Satisfactory: "satisfactory",
    "": "",
  };
  for (const [label, expected] of Object.entries(cases)) assert.equal(P.normalizeCondition(label), expected, label);
});

test("label keys match multilingual attribute names", () => {
  assert.equal(P.labelKey("Taglia:"), "size");
  assert.equal(P.labelKey("Marque"), "brand");
  assert.equal(P.labelKey("Farbe"), "color");
  assert.equal(P.labelKey("Caricato"), null);
});

test("import payload round-trips through base64url (UTF-8 safe)", () => {
  const data = { v: 1, title: "Felpa “blu” — è perfetta", price: 18 };
  const url = P.analyzeUrl("http://localhost:3000/", data);
  assert.match(url, /^http:\/\/localhost:3000\/analyze#import=[A-Za-z0-9_-]+$/);
  const payload = url.split("#import=")[1];
  const json = Buffer.from(payload.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
  assert.deepEqual(JSON.parse(json), data);
});

// ------------------------------------------------------------------ search-results pages
const SEARCH = "https://www.vinted.it/catalog?search_text=polo%20ralph%20lauren&order=newest_first";

function card(id, summary, extra = {}) {
  return {
    href: `/items/${id}-polo-ralph-lauren?referrer=catalog`,
    summary,
    alt: summary,
    image: `https://images1.vinted.net/t/${id}.jpeg`,
    testids: {},
    texts: [],
    ...extra,
  };
}

test("reads Vinted's card summary: title, attributes and the item price (not the protection total)", () => {
  const s = P.parseCardSummary(
    "Polo Ralph Lauren, slim fit, brand: Ralph Lauren, condizioni: Ottime, taglia: M, 15,00 €, 16,45 € include la Protezione acquisti",
  );
  assert.equal(s.title, "Polo Ralph Lauren, slim fit");
  assert.deepEqual(s.attrs, { brand: "Ralph Lauren", condition: "Ottime", size: "M" });
  assert.equal(s.price, 15);
  assert.equal(s.currency, "EUR");
  const fr = P.parseCardSummary("Pull, marque: Lacoste, état: Très bon état, taille: L / 40 / 12, 1 234,50 €, 1 297,00 € inclut");
  assert.deepEqual([fr.title, fr.price, fr.attrs.size], ["Pull", 1234.5, "L / 40 / 12"]);
  const uk = P.parseCardSummary("Levi's 501, brand: Levi's, condition: Good, size: W32, £12.00, £13.35 includes Buyer Protection");
  assert.deepEqual([uk.price, uk.currency], [12, "GBP"]);
  // A decimal shoe size is not a price.
  assert.equal(P.parseCardSummary("Air Max, brand: Nike, taglia: 42,5, 30,00 €").price, 30);
});

test("parses every loaded result card, once each, with clean URLs", () => {
  const cards = [
    card("111", "Polo Ralph Lauren blu, brand: Ralph Lauren, condizioni: Ottime, taglia: M, 15,00 €, 16,45 €"),
    card("222", "Polo Ralph Lauren rossa, brand: Ralph Lauren, condizioni: Nuovo senza cartellino, taglia: L, 9,00 €"),
    // The same listing promoted at the top and again in the organic results.
    card("111", "Polo Ralph Lauren blu, brand: Ralph Lauren, condizioni: Ottime, taglia: M, 15,00 €, 16,45 €"),
    // No summary: falls back to the card's own fields.
    card("333", "", {
      alt: "",
      testids: { "description-title": "Ralph Lauren", "description-subtitle": "S · Buone", "price-text": "12,50 €" },
      texts: ["Ralph Lauren", "S · Buone", "12,50 €", "13,83 € incl."],
    }),
    // Unreadable (no price anywhere): skipped, not guessed.
    card("444", "Polo, brand: Ralph Lauren", { alt: "" }),
    // Not a listing link.
    { href: "/member/123-armadio", summary: "Armadio" },
  ];
  const res = P.parseCatalog(cards, SEARCH);
  assert.equal(res.total, 3);
  assert.equal(res.unreadable, 2);
  assert.equal(res.overLimit, 0);
  assert.equal(res.data.source, "vinted_search");
  assert.equal(res.data.query, "polo ralph lauren");
  const [a, b, c] = res.data.items;
  assert.deepEqual(
    { url: a.url, title: a.title, price: a.price, brand: a.brand, size: a.size, condition: a.condition },
    {
      url: "https://www.vinted.it/items/111-polo-ralph-lauren",
      title: "Polo Ralph Lauren blu",
      price: 15,
      brand: "Ralph Lauren",
      size: "M",
      condition: "very_good",
    },
  );
  assert.equal(b.condition, "new_without_tags");
  assert.deepEqual([c.title, c.price, c.size, c.condition], ["Ralph Lauren S", 12.5, "S", "good"]);
  assert.deepEqual(a.image_urls, ["https://images1.vinted.net/t/111.jpeg"]);
});

test("caps a batch at the server's limit and reports the rest", () => {
  const many = Array.from({ length: P.MAX_BATCH + 15 }, (_, i) =>
    card(String(1000 + i), `Polo ${i}, brand: Ralph Lauren, 10,00 €`),
  );
  const res = P.parseCatalog(many, SEARCH);
  assert.equal(res.data.items.length, P.MAX_BATCH);
  assert.equal(res.overLimit, 15);
});

test("batch payload is compressed base64url that round-trips", async () => {
  const res = P.parseCatalog(
    Array.from({ length: 96 }, (_, i) =>
      card(String(5000 + i), `Felpa Ralph Lauren ${i}, brand: Ralph Lauren, condizioni: Ottime, taglia: M, 18,00 €`),
    ),
    SEARCH,
  );
  const url = await P.importUrl("https://flip.example.com/", res.data);
  assert.match(url, /^https:\/\/flip\.example\.com\/import#batch=[A-Za-z0-9_-]+$/);
  const payload = url.split("#batch=")[1];
  const raw = JSON.stringify(res.data);
  assert.ok(payload.length < raw.length / 3, `compressed ${payload.length} vs ${raw.length}`);
  const b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
  const bytes = Uint8Array.from(atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)), (ch) => ch.charCodeAt(0));
  const text = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"))).text();
  assert.deepEqual(JSON.parse(text), res.data);
});
