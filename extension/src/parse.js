/*
 * FlipFinder for Vinted - pure parsing of the page the user is viewing.
 *
 * The content script collects what is already on the page (JSON-LD, meta tags, attribute
 * label/value pairs, or the result cards of a search) and this module turns it into
 * FlipFinder's import format. No network access, no DOM access: it is unit-tested in Node
 * (see tests/parse.test.mjs).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FlipFinderParse = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const LIMITS = { title: 300, description: 5000, short: 120, images: 20 };

  // Attribute labels as Vinted shows them across its markets (lowercase, no colon).
  const LABELS = {
    brand: ["brand", "marca", "marque", "marke", "merk", "marka", "značka", "prekės ženklas", "märke", "mærke", "μάρκα"],
    size: ["taglia", "size", "taille", "größe", "grösse", "talla", "maat", "rozmiar", "tamanho", "velikost", "dydis", "storlek", "størrelse", "koko", "μέγεθος"],
    condition: ["condizioni", "condition", "état", "etat", "zustand", "estado", "staat", "stan", "stav", "būklė", "skick", "stand", "kunto", "κατάσταση"],
    color: ["colore", "color", "colour", "couleur", "farbe", "kleur", "kolor", "cor", "barva", "spalva", "färg", "farve", "väri", "χρώμα"],
  };

  // Order matters: "very good" before "good", "without tags" is checked by its own phrases.
  const CONDITIONS = [
    ["new_with_tags", /(con cartellino|with tags|avec étiquettes?|avec etiquettes?|mit etikett|con etiquetas?|met labels?|z metk|com etiquetas?|med etiket)/i],
    ["new_without_tags", /(senza cartellino|without tags|sans étiquettes?|sans etiquettes?|ohne etikett|sin etiquetas?|zonder labels?|bez metek|sem etiquetas?|uten etikett|utan etikett)/i],
    ["very_good", /(ottim|very good|très bon|tres bon|sehr gut|muy bueno|zeer goed|bardzo dobr|muito bom|meget god|mycket bra)/i],
    ["good", /(buon|\bgood\b|bon état|bon etat|\bgut\b|\bbueno\b|\bgoed\b|\bdobr|\bbom\b|\bgod\b|\bbra\b)/i],
    ["satisfactory", /(discret|satisfactory|satisfaisant|zufriedenstellend|satisfactorio|redelijk|zadowalaj|satisfatório|tilfredsstillende|okej)/i],
  ];

  function clean(value, max) {
    if (value === null || value === undefined) return "";
    const text = String(value).replace(/\s+/g, " ").trim();
    return text.length > max ? text.slice(0, max) : text;
  }

  function cleanMultiline(value, max) {
    if (!value) return "";
    const text = String(value)
      .replace(/\r/g, "")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    return text.length > max ? text.slice(0, max) : text;
  }

  function isHttpUrl(value) {
    return typeof value === "string" && /^https?:\/\/[^\s]+$/i.test(value);
  }

  /** "18,00 €", "€1.234,50", "18.5", 18 -> number; null when not a price. */
  function parsePrice(value) {
    if (typeof value === "number") return Number.isFinite(value) && value > 0 ? value : null;
    if (typeof value !== "string") return null;
    let s = value.replace(/[^\d.,]/g, "");
    if (!s) return null;
    const lastComma = s.lastIndexOf(",");
    const lastDot = s.lastIndexOf(".");
    if (lastComma > -1 && lastDot > -1) {
      const decimal = lastComma > lastDot ? "," : ".";
      const thousands = decimal === "," ? "." : ",";
      s = s.split(thousands).join("").replace(decimal, ".");
    } else if (lastComma > -1) {
      // "18,50" decimal comma; "1,234" thousands separator
      s = /,\d{1,2}$/.test(s) ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
    } else if (lastDot > -1 && !/\.\d{1,2}$/.test(s)) {
      s = s.replace(/\./g, ""); // "1.234" thousands separator
    }
    const n = Number(s);
    return Number.isFinite(n) && n > 0 ? Math.round(n * 100) / 100 : null;
  }

  function normalizeCondition(text) {
    if (!text) return "";
    for (const [value, pattern] of CONDITIONS) if (pattern.test(text)) return value;
    return "";
  }

  function itemId(url) {
    const m = typeof url === "string" ? url.match(/\/items\/(\d+)/) : null;
    return m ? m[1] : null;
  }

  function stripSiteSuffix(title) {
    return title.replace(/\s*[|\-–]\s*Vinted\s*$/i, "").trim();
  }

  function asArray(value) {
    if (value === null || value === undefined) return [];
    return Array.isArray(value) ? value : [value];
  }

  /** Every schema.org Product found in the page's JSON-LD blocks (handles @graph and arrays). */
  function findProducts(jsonLd) {
    const out = [];
    const visit = (node) => {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) return node.forEach(visit);
      const type = asArray(node["@type"]).map(String);
      if (type.some((t) => /product/i.test(t))) out.push(node);
      if (node["@graph"]) visit(node["@graph"]);
    };
    asArray(jsonLd).forEach(visit);
    return out;
  }

  function labelKey(label) {
    const l = clean(label, 60).toLowerCase().replace(/[:：]\s*$/, "").trim();
    if (!l) return null;
    for (const [key, words] of Object.entries(LABELS)) {
      if (words.includes(l)) return key;
    }
    return null;
  }

  function imageUrls(product, meta) {
    const urls = [];
    for (const img of asArray(product && product.image)) {
      const u = typeof img === "string" ? img : img && (img.url || img.contentUrl);
      if (isHttpUrl(u)) urls.push(u);
    }
    for (const u of asArray(meta["og:image"])) if (isHttpUrl(u)) urls.push(u);
    return [...new Set(urls)].slice(0, LIMITS.images);
  }

  /**
   * raw = { location, canonical, jsonLd: [], meta: {name: [values]}, pairs: [[label, value]],
   *         heading, description, seller }
   * Returns { ok, data, missing, staleItem } where data matches FlipFinder's import format.
   */
  function parseListing(raw) {
    const meta = raw.meta || {};
    const first = (key) => asArray(meta[key])[0];
    const product = findProducts(raw.jsonLd || [])[0] || null;
    const offer = product ? asArray(product.offers)[0] || {} : {};

    const attrs = {};
    for (const [label, value] of raw.pairs || []) {
      const key = labelKey(label);
      const v = clean(value, LIMITS.short);
      if (key && v && !attrs[key]) attrs[key] = v;
    }

    const brandLd = product && product.brand;
    const brand = clean(typeof brandLd === "string" ? brandLd : brandLd && brandLd.name, LIMITS.short) || attrs.brand || "";

    const locationUrl = raw.location || "";
    const canonical = isHttpUrl(raw.canonical) ? raw.canonical : "";
    const productUrl = product && isHttpUrl(product.url) ? product.url : "";
    const url = ([canonical, productUrl, first("og:url"), locationUrl].find(
      (u) => isHttpUrl(u) && itemId(u) && itemId(u) === itemId(locationUrl),
    ) || locationUrl).split(/[?#]/)[0]; // no tracking parameters

    // Single-page navigation can leave the previous item's structured data in the <head>.
    const ldId = itemId(productUrl) || itemId(canonical);
    const staleItem = Boolean(ldId && itemId(locationUrl) && ldId !== itemId(locationUrl));

    const title = stripSiteSuffix(
      clean((!staleItem && product && product.name) || raw.heading || first("og:title") || "", LIMITS.title),
    );
    const price =
      (!staleItem && parsePrice(offer.price !== undefined ? String(offer.price) : null)) ||
      parsePrice(first("product:price:amount")) ||
      parsePrice(first("og:price:amount")) ||
      null;
    const currency = clean(offer.priceCurrency || first("product:price:currency") || "EUR", 3).toUpperCase();
    const description = cleanMultiline(
      (!staleItem && product && product.description) || raw.description || first("og:description") || "",
      LIMITS.description,
    );
    // schema.org itemCondition is too coarse (New/Used); the visible label carries the grade.
    const conditionText = attrs.condition || "";

    const data = {
      v: 1,
      source: "vinted",
      url,
      title,
      price,
      currency,
      brand,
      size: attrs.size || "",
      condition: normalizeCondition(conditionText),
      condition_label: conditionText,
      color: attrs.color || "",
      description,
      image_urls: staleItem ? [] : imageUrls(product, meta),
      seller_username: clean(raw.seller, LIMITS.short),
    };
    const missing = ["title", "price"].filter((k) => !data[k]);
    return { ok: missing.length === 0 && isHttpUrl(url), data, missing, staleItem };
  }

  // ------------------------------------------------------------------ search-results pages

  /** FlipFinder analyses at most this many listings per import (the server enforces it too). */
  const MAX_BATCH = 200;

  const CURRENCIES = [
    ["EUR", /€|\beur\b/i],
    ["GBP", /£/],
    ["PLN", /zł/i],
    ["CZK", /kč/i],
    ["HUF", /\bft\b/i],
    ["RON", /\blei\b/i],
    ["SEK", /\bkr\b/i],
    ["USD", /\$/],
  ];
  const CURRENCY = "(?:€|£|\\$|zł|kč|kr|ft|lei|eur)";
  const AMOUNT = "\\d{1,3}(?:[.,\\s\\u00a0\\u202f]\\d{3})*(?:[.,]\\d{1,2})?";
  // "15,00 €", "€15.00", "1 234,50 zł" - an amount is only a price next to a currency.
  const PRICE_TOKEN = new RegExp(`${CURRENCY}\\s?${AMOUNT}|${AMOUNT}\\s?${CURRENCY}(?![a-z])`, "i");
  const LABEL_WORDS = Object.values(LABELS)
    .flat()
    .sort((a, b) => b.length - a.length)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const SUMMARY_LABEL = new RegExp(`,\\s*(${LABEL_WORDS.join("|")})\\s*:\\s*`, "gi");

  function currencyOf(text) {
    for (const [code, pattern] of CURRENCIES) if (pattern.test(text)) return code;
    return "";
  }

  /** First price in a text, with its currency: { price, currency, index } or null. */
  function findPrice(text) {
    const m = PRICE_TOKEN.exec(text || "");
    if (!m) return null;
    const price = parsePrice(m[0].replace(/[\s\u00a0\u202f](?=\d{3}\b)/g, ""));
    return price ? { price, currency: currencyOf(m[0]), index: m.index } : null;
  }

  /**
   * The one-line summary Vinted puts on each result card (link title / image alt), e.g.
   * "Polo Ralph Lauren, brand: Ralph Lauren, condizioni: Ottime, taglia: M, 15,00 €, 16,45 € include la Protezione acquisti".
   */
  function parseCardSummary(text) {
    const s = clean(text, 600);
    const out = { title: "", attrs: {}, price: null, currency: "" };
    if (!s) return out;
    const first = findPrice(s);
    // Everything after the first price is buyer-protection small print.
    const head = first ? s.slice(0, first.index).replace(/[,\s]+$/, "") : s;
    if (first) Object.assign(out, { price: first.price, currency: first.currency });
    const marks = [];
    SUMMARY_LABEL.lastIndex = 0;
    let m;
    while ((m = SUMMARY_LABEL.exec(head))) marks.push({ key: labelKey(m[1]), start: m.index, from: SUMMARY_LABEL.lastIndex });
    out.title = clean(marks.length ? head.slice(0, marks[0].start) : head, LIMITS.title);
    marks.forEach((mark, i) => {
      const value = clean(head.slice(mark.from, i + 1 < marks.length ? marks[i + 1].start : head.length), LIMITS.short);
      if (mark.key && value && !out.attrs[mark.key]) out.attrs[mark.key] = value;
    });
    return out;
  }

  function absoluteItemUrl(href, base) {
    try {
      const url = new URL(href, base);
      if (!/^https?:$/.test(url.protocol) || !itemId(url.pathname)) return "";
      return url.origin + url.pathname; // no tracking parameters
    } catch {
      return "";
    }
  }

  /**
   * One result card -> FlipFinder import item, or null when it can't be read.
   * raw = { href, summary, alt, image, testids: {suffix: text}, texts: [leaf texts] }
   */
  function parseCatalogCard(raw, base) {
    const url = absoluteItemUrl(raw.href, base);
    if (!url) return null;
    const summary = parseCardSummary(raw.summary || "");
    const alt = parseCardSummary(raw.alt || "");
    const ids = raw.testids || {};
    const texts = (raw.texts || []).map((t) => clean(t, 200)).filter(Boolean);

    let price = summary.price || alt.price || null;
    let currency = summary.currency || alt.currency || "";
    for (const candidate of [ids["price-text"], ...texts]) {
      if (price) break;
      const found = findPrice(candidate);
      if (found) ({ price, currency } = found);
    }

    // Card subtitle: "M · Ottime" (size and condition, in either order).
    const sub = { size: "", condition: "" };
    for (const part of clean(ids["description-subtitle"], 200).split(/\s*[·•|]\s*/)) {
      if (!part) continue;
      if (!sub.condition && normalizeCondition(part)) sub.condition = part;
      else if (!sub.size && part.length <= 30) sub.size = part;
    }
    const attrs = { ...alt.attrs, ...summary.attrs };
    const brand = attrs.brand || clean(ids["description-title"], LIMITS.short);
    const conditionText = attrs.condition || sub.condition;
    const title =
      summary.title || alt.title || clean(ids["title"], LIMITS.title) || [brand, sub.size].filter(Boolean).join(" ");
    return {
      url,
      title: stripSiteSuffix(title),
      price,
      currency: currency || "EUR",
      brand,
      size: attrs.size || sub.size,
      condition: normalizeCondition(conditionText),
      condition_label: conditionText,
      color: attrs.color || "",
      image_urls: isHttpUrl(raw.image) ? [raw.image] : [],
    };
  }

  /**
   * Every listing card loaded on a search/catalog page -> the batch FlipFinder imports.
   * Returns { items, unreadable, overLimit, total } - duplicates (promoted + organic) count once.
   */
  function parseCatalog(rawCards, location) {
    const seen = new Set();
    const items = [];
    let unreadable = 0;
    for (const raw of rawCards || []) {
      const item = parseCatalogCard(raw, location);
      const id = item && itemId(item.url);
      if (id && seen.has(id)) continue;
      if (id) seen.add(id);
      if (!item || !item.title || item.title.length < 3 || !item.price) {
        unreadable += 1;
        continue;
      }
      items.push(item);
    }
    let query = "";
    try {
      query = clean(new URL(location).searchParams.get("search_text"), LIMITS.short);
    } catch {
      /* not a URL */
    }
    return {
      total: items.length,
      unreadable,
      overLimit: Math.max(0, items.length - MAX_BATCH),
      data: { v: 1, source: "vinted_search", query, items: items.slice(0, MAX_BATCH) },
    };
  }

  function toBase64Url(bytes) {
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  /** UTF-8 JSON -> base64url, the payload of FlipFinder's /analyze#import=... */
  function encodeImport(data) {
    return toBase64Url(new TextEncoder().encode(JSON.stringify(data)));
  }

  /** UTF-8 JSON -> deflate-raw -> base64url, the payload of FlipFinder's /import#batch=... */
  async function encodeBatch(data) {
    const json = new Blob([JSON.stringify(data)]).stream();
    const zipped = await new Response(json.pipeThrough(new CompressionStream("deflate-raw"))).arrayBuffer();
    return toBase64Url(new Uint8Array(zipped));
  }

  function appBase(appUrl) {
    return String(appUrl || "http://localhost:3000").replace(/\/+$/, "");
  }

  function analyzeUrl(appUrl, data) {
    return `${appBase(appUrl)}/analyze#import=${encodeImport(data)}`;
  }

  async function importUrl(appUrl, data) {
    return `${appBase(appUrl)}/import#batch=${await encodeBatch(data)}`;
  }

  return {
    MAX_BATCH,
    parseListing,
    parseCatalog,
    parseCatalogCard,
    parseCardSummary,
    parsePrice,
    normalizeCondition,
    encodeImport,
    encodeBatch,
    analyzeUrl,
    importUrl,
    itemId,
    labelKey,
  };
});
