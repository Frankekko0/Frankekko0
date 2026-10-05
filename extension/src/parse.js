/*
 * FlipFinder for Vinted - pure parsing of the listing the user is viewing.
 *
 * The content script collects what is already on the page (JSON-LD, meta tags, attribute
 * label/value pairs) and this module turns it into FlipFinder's manual-import format. No
 * network access, no DOM access: it is unit-tested in Node (see tests/parse.test.mjs).
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

  /** UTF-8 JSON -> base64url, the payload of FlipFinder's /analyze#import=... */
  function encodeImport(data) {
    const bytes = new TextEncoder().encode(JSON.stringify(data));
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function analyzeUrl(appUrl, data) {
    const base = String(appUrl || "http://localhost:3000").replace(/\/+$/, "");
    return `${base}/analyze#import=${encodeImport(data)}`;
  }

  return { parseListing, parsePrice, normalizeCondition, encodeImport, analyzeUrl, itemId, labelKey };
});
