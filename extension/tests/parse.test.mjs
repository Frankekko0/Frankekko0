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
