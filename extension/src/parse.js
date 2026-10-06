/*
 * FlipFinder for Vinted - pure parsing (no DOM, no network), unit-tested in Node.
 *
 * Every label, regex and selector comes from the shared configuration (vinted_parser.json,
 * the same file the FlipFinder server uses). The extension ships a copy (parser-config.js) and
 * downloads newer versions from FlipFinder, so a change on Vinted's pages is fixed in one place
 * without publishing a new extension.
 *
 *  - collectHtml(html): what the parser needs from a page's HTML (JSON-LD, scripts, meta tags,
 *    member links, visible text in reading order). content.js builds the same structure from
 *    the live page.
 *  - parseItem(collected, url, now, C): an item page -> every field, like the server parser.
 *  - parseCardSummary / parseCatalogCard: search, closet and favourites result cards.
 *  - capturePayload: the record sent to FlipFinder, checked against the server's limits.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FlipFinderParse = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // ------------------------------------------------------------------ configuration
  function fold(text) {
    return String(text || "")
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/’/g, "'")
      .split(/\s+/)
      .filter(Boolean)
      .join(" ");
  }

  const escapeRx = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  let lastRaw = null;
  let lastCompiled = null;

  /** Compiles the shared configuration (memoized on the object). Throws on an invalid file. */
  function compileConfig(raw) {
    if (raw === lastRaw && lastCompiled) return lastCompiled;
    if (!raw || typeof raw !== "object" || !raw.patterns || !raw.labels) throw new Error("invalid parser config");
    const patterns = {};
    const global = {};
    for (const [name, p] of Object.entries(raw.patterns)) {
      const flags = (p.flags || "").includes("i") ? "i" : "";
      patterns[name] = new RegExp(p.source, flags);
      global[name] = new RegExp(p.source, flags + "g");
    }
    const labels = {};
    for (const [key, words] of Object.entries(raw.labels)) labels[key] = new Set(words.map(fold));
    const units = {};
    for (const [unit, words] of Object.entries(raw.relative_units || {})) for (const w of words) units[fold(w)] = unit;
    const labelWords = Object.values(raw.labels)
      .flat()
      .sort((a, b) => b.length - a.length)
      .map(escapeRx);
    const C = {
      raw,
      version: String(raw.version || "?"),
      patterns,
      global,
      labels,
      units,
      conditions: (raw.conditions || []).map(([name, src]) => [name, new RegExp(src, "i")]),
      currencies: (raw.currencies || []).map(([code, src]) => [code, new RegExp(src, "i")]),
      selectors: raw.selectors || {},
      limits: { title: 300, description: 5000, short: 120, images: 20, max_batch: 200, ...(raw.limits || {}) },
      domains: raw.domains || [],
      // ", brand: " / ", taglia: " separators inside a card summary.
      summaryLabel: new RegExp(`,\\s*(${labelWords.join("|")})\\s*:\\s*`, "gi"),
    };
    lastRaw = raw;
    lastCompiled = C;
    return C;
  }

  // ------------------------------------------------------------------ small helpers
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

  const isHttpUrl = (v) => typeof v === "string" && /^https?:\/\/[^\s]+$/i.test(v);
  const round2 = (n) => Math.round(n * 100) / 100;

  /** "18,00 €", "€1.234,50", "18.5", 18 -> number; null when not a price. */
  function parsePrice(value) {
    if (typeof value === "number") return Number.isFinite(value) && value > 0 ? round2(value) : null;
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
      s = /,\d{1,2}$/.test(s) ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
    } else if (lastDot > -1 && !/\.\d{1,2}$/.test(s)) {
      s = s.replace(/\./g, "");
    }
    const n = Number(s);
    return Number.isFinite(n) && n > 0 ? round2(n) : null;
  }

  function currencyOf(text, C) {
    for (const [code, rx] of C.currencies) if (rx.test(text)) return code;
    return "";
  }

  /** First price in a text, with its currency: { price, currency, index, end } or null. */
  function findPrice(text, C, from = 0) {
    const s = String(text || "").slice(from);
    const m = C.patterns.price_token.exec(s);
    if (!m) return null;
    const price = parsePrice(m[0].replace(/[\s  ](?=\d{3}\b)/g, ""));
    return price ? { price, currency: currencyOf(m[0], C), index: from + m.index, end: from + m.index + m[0].length } : null;
  }

  function normalizeCondition(text, C) {
    if (!text) return "";
    for (const [name, rx] of C.conditions) if (rx.test(text)) return name;
    return "";
  }

  function labelKey(label, C) {
    const key = fold(clean(label, 60)).replace(/[:：]+$/, "").trim();
    if (!key) return null;
    for (const [name, words] of Object.entries(C.labels)) if (words.has(key)) return name;
    return null;
  }

  const UNIT_MS = { minute: 6e4, hour: 36e5, day: 864e5, week: 6048e5, month: 30 * 864e5, year: 365 * 864e5 };

  /** "3 giorni fa", "2 hours ago", "ieri" -> approximate Date; null when not a relative time. */
  function relativeTime(text, now, C) {
    if (!text) return null;
    const t = fold(text);
    if (C.patterns.relative_now.test(t)) return new Date(now);
    if (C.patterns.relative_yesterday.test(t)) return new Date(now - UNIT_MS.day);
    const m = C.patterns.relative_amount.exec(t);
    if (!m) return null;
    const amount = Number(m[1]);
    const word = m[2];
    let unit = C.units[word];
    if (!unit) {
      for (const [w, u] of Object.entries(C.units)) {
        if (w.length >= 3 && word.startsWith(w)) {
          unit = u;
          break;
        }
      }
    }
    return unit ? new Date(now - amount * UNIT_MS[unit]) : null;
  }

  function itemId(url, C) {
    const m = typeof url === "string" ? (C ? C.patterns.item_id : /\/items\/(\d+)/).exec(url) : null;
    return m ? m[1] : null;
  }

  /** item | favourites | closet | catalog, from the page path. */
  function pageType(pathname, C) {
    if (C.patterns.page_item.test(pathname)) return "item";
    if (C.patterns.page_favourites.test(pathname)) return "favourites";
    if (C.patterns.page_member.test(pathname)) return "closet";
    return "catalog";
  }

  function isVintedUrl(url, C) {
    try {
      const u = new URL(url);
      return u.protocol === "https:" && C.domains.some((d) => u.hostname === `www.${d}` || u.hostname === d);
    } catch {
      return false;
    }
  }

  /** "https://www.vinted.it/items/123-x?ref=1" -> "https://www.vinted.it/items/123-x" */
  function cleanItemUrl(href, base) {
    try {
      const url = new URL(href, base);
      if (!/^https?:$/.test(url.protocol) || !/\/items\/\d+/.test(url.pathname)) return "";
      return url.origin + url.pathname;
    } catch {
      return "";
    }
  }

  function unescapeJsonString(s) {
    return String(s).replace(/\\+\//g, "/").replace(/\\u0026/g, "&");
  }

  /** One-way hash of the Vinted member id (same as the server): groups a seller's listings,
   * reveals nothing. The member id itself never leaves the browser. */
  async function sellerKey(memberId) {
    const data = new TextEncoder().encode(`vinted-member:${memberId}`);
    const digest = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", data));
    return "h:" + Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("").slice(0, 24);
  }


  // ------------------------------------------------------------------ the item's embedded object
  // Same algorithm as backend/app/acquisition/embedded.py. A Vinted item page embeds several
  // objects (the item, its seller, the signed-in user with their profile photo, suggested items):
  // the item is located by its id and only its own fields are read.
  const FLIGHT_CHUNK = /self\.__next_f\.push\(\[\s*\d+\s*,\s*("(?:[^"\\]|\\.)*")\s*\]\)/g;
  const LITERAL = /-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/y;
  const MAX_BACKSCAN = 400000;
  const DEFAULT_ITEM_JSON = {
    photos: ["photos"],
    photo_url: ["full_size_url", "url"],
    profile_photo: ["photo", "avatar"],
    markers: ["photos", "title", "favourite_count", "view_count", "is_closed", "price"],
    favourites: ["favourite_count", "favorite_count"],
    views: ["view_count"],
    reserved: ["is_reserved"],
    closed: ["is_closed"],
    closing_action: ["item_closing_action"],
    created: ["created_at_ts", "created_at"],
    material: ["material", "material_title"],
    service_fee: ["service_fee.amount", "service_fee"],
    shipping: ["shipping_price.amount", "shipping_fee.amount", "shipping_price"],
    seller: ["user"],
    seller_id: ["id"],
    seller_rating: ["feedback_reputation"],
    seller_reviews: ["feedback_count"],
  };

  function itemJsonKeys(C) {
    return { ...DEFAULT_ITEM_JSON, ...((C && C.raw && C.raw.item_json) || {}) };
  }

  function payloadTexts(scripts) {
    const flight = [];
    const others = [];
    for (const s of scripts || []) {
      let found = false;
      for (const m of String(s).matchAll(FLIGHT_CHUNK)) {
        found = true;
        try {
          flight.push(JSON.parse(m[1]));
        } catch {
          /* skip a broken chunk */
        }
      }
      if (!found) others.push(String(s));
    }
    return (flight.length ? [flight.join("")] : []).concat(others);
  }

  class EmbeddedReader {
    constructor(text, q) {
      this.t = text;
      this.q = q;
      this.m = q + 1;
      this.decodes = Math.round(Math.log2(this.m)) + 1;
      this.prefix = "\\".repeat(q) + '"';
    }
    isDelim(j) {
      if (this.t[j] !== '"') return false;
      let n = 0;
      while (j - 1 - n >= 0 && this.t[j - 1 - n] === "\\") n += 1;
      const r = n - this.q;
      return r >= 0 && r % this.m === 0 && (r / this.m) % 2 === 0;
    }
    enclosingObject(pos) {
      let depth = 0;
      let inStr = false;
      const stop = Math.max(0, pos - MAX_BACKSCAN);
      for (let j = pos - 1; j >= stop; j -= 1) {
        const c = this.t[j];
        if (c === '"' && this.isDelim(j)) inStr = !inStr;
        else if (!inStr) {
          if (c === "}" || c === "]") depth += 1;
          else if (c === "{" || c === "[") {
            if (depth === 0) return c === "{" ? j : null;
            depth -= 1;
          }
        }
      }
      return null;
    }
    ws(i) {
      while (i < this.t.length && " \t\r\n".includes(this.t[i])) i += 1;
      return i;
    }
    string(i) {
      const start = i + this.q;
      if (this.t[start] !== '"') throw new Error("string expected");
      let j = start + 1;
      for (;;) {
        j = this.t.indexOf('"', j);
        if (j < 0) throw new Error("unterminated string");
        if (this.isDelim(j)) break;
        j += 1;
      }
      let value = this.t.slice(start + 1, j - this.q);
      for (let k = 0; k < this.decodes; k += 1) {
        try {
          value = JSON.parse(`"${value}"`);
        } catch {
          break;
        }
      }
      return [value, j + 1];
    }
    value(i, depth = 0) {
      if (depth > 60) throw new Error("too deep");
      i = this.ws(i);
      const c = this.t[i];
      if (c === undefined) throw new Error("end of text");
      if (c === "{") {
        const obj = {};
        i = this.ws(i + 1);
        if (this.t[i] === "}") return [obj, i + 1];
        for (;;) {
          i = this.ws(i);
          if (!this.t.startsWith(this.prefix, i)) throw new Error("key expected");
          let key;
          [key, i] = this.string(i);
          i = this.ws(i);
          if (this.t[i] !== ":") throw new Error("colon expected");
          [obj[key], i] = this.value(i + 1, depth + 1);
          i = this.ws(i);
          if (this.t[i] === ",") i += 1;
          else if (this.t[i] === "}") return [obj, i + 1];
          else throw new Error("comma or brace expected");
        }
      }
      if (c === "[") {
        const arr = [];
        i = this.ws(i + 1);
        if (this.t[i] === "]") return [arr, i + 1];
        for (;;) {
          let v;
          [v, i] = this.value(i, depth + 1);
          arr.push(v);
          i = this.ws(i);
          if (this.t[i] === ",") i += 1;
          else if (this.t[i] === "]") return [arr, i + 1];
          else throw new Error("comma or bracket expected");
        }
      }
      if (this.t.startsWith(this.prefix, i)) return this.string(i);
      LITERAL.lastIndex = i;
      const m = LITERAL.exec(this.t);
      if (!m) throw new Error("value expected");
      const lit = m[0];
      const end = i + lit.length;
      if (lit === "true" || lit === "false") return [lit === "true", end];
      if (lit === "null") return [null, end];
      return [Number(lit), end];
    }
  }

  const escapeKey = (k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function findItem(scripts, vintedId, keys) {
    if (!vintedId || !/^\d+$/.test(vintedId)) return null;
    const wanted = [...keys.photos, ...keys.markers];
    for (const text of payloadTexts(scripts)) {
      if (!text.includes(vintedId)) continue;
      const rx = new RegExp(`(\\\\*)"id\\1"\\s*:\\s*(?:\\\\*")?${vintedId}(?![\\d.])`, "g");
      for (const m of text.matchAll(rx)) {
        const q = m[1].length;
        if ((q + 1) & q) continue;
        const reader = new EmbeddedReader(text, q);
        const start = reader.enclosingObject(m.index);
        if (start === null) continue;
        let obj;
        try {
          [obj] = reader.value(start);
        } catch {
          continue;
        }
        if (!obj || typeof obj !== "object" || Array.isArray(obj) || String(obj.id) !== vintedId) continue;
        if (wanted.some((k) => k in obj)) return obj;
      }
    }
    return null;
  }

  function photoUrlsOf(obj, urlKeys) {
    const out = new Set();
    const add = (o) => {
      for (const k of urlKeys) if (typeof o[k] === "string" && /^https?:\/\//.test(o[k])) out.add(o[k]);
    };
    add(obj);
    for (const v of Object.values(obj)) if (Array.isArray(v)) for (const t of v) if (t && typeof t === "object") add(t);
    return out;
  }

  /** Every profile photo in the page (signed-in user, seller, other members): never item photos. */
  function profilePhotoUrls(scripts, keys) {
    const urls = new Set();
    for (const text of payloadTexts(scripts)) {
      for (const key of keys.profile_photo) {
        const rx = new RegExp(`(\\\\*)"${escapeKey(key)}\\1"\\s*:\\s*\\{`, "g");
        for (const m of text.matchAll(rx)) {
          const q = m[1].length;
          if ((q + 1) & q) continue;
          try {
            const [obj] = new EmbeddedReader(text, q).value(m.index + m[0].length - 1);
            if (obj && typeof obj === "object") for (const u of photoUrlsOf(obj, keys.photo_url)) urls.add(u);
          } catch {
            /* not an object */
          }
        }
      }
    }
    return urls;
  }

  function galleryOf(item, keys) {
    for (const k of keys.photos) {
      const photos = item && item[k];
      if (!Array.isArray(photos)) continue;
      const urls = [];
      for (const p of photos) {
        if (typeof p === "string" && /^https?:\/\//.test(p)) urls.push(p);
        else if (p && typeof p === "object") {
          const u = keys.photo_url.map((k2) => p[k2]).find((v) => typeof v === "string" && /^https?:\/\//.test(v));
          if (u) urls.push(u);
        }
      }
      return [...new Set(urls)];
    }
    return [];
  }

  /** First present value among keys (dotted paths allowed). */
  function pick(obj, keys) {
    if (!obj) return null;
    for (const key of keys) {
      let cur = obj;
      for (const part of key.split(".")) cur = cur && typeof cur === "object" ? cur[part] : undefined;
      if (cur !== undefined && cur !== null && cur !== "") return cur;
    }
    return null;
  }

  // ------------------------------------------------------------------ HTML collection
  const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", euro: "€", pound: "£" };

  function decodeEntities(s) {
    return String(s).replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (all, e) => {
      if (e[0] === "#") {
        const code = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        return Number.isFinite(code) && code > 0 && code < 0x110000 ? String.fromCodePoint(code) : all;
      }
      const named = ENTITIES[e.toLowerCase()];
      return named === undefined ? all : named;
    });
  }

  function attrsOf(text) {
    const out = {};
    const rx = /([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+)))?/g;
    let m;
    while ((m = rx.exec(text))) out[m[1].toLowerCase()] = decodeEntities(m[2] ?? m[3] ?? m[4] ?? "");
    return out;
  }

  function emptyCollected() {
    return { jsonld: [], scripts: [], meta: {}, canonical: null, memberLinks: [], texts: [], heading: null };
  }

  const SKIP = new Set(["style", "noscript", "template", "svg"]);
  const VOID = new Set(["meta", "link", "img", "br", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"]);

  /** The structure the parser reads, from raw HTML (tolerant tokenizer, no HTML engine). */
  function collectHtml(html) {
    const out = emptyCollected();
    const src = String(html || "");
    const rx = /<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<![^>]*>|<(\/?)([a-zA-Z][\w:-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>|([^<]+)|</g;
    let skip = 0;
    let chrome = 0; // inside <header>, <nav> or <footer>: links there are the signed-in user's
    let inH1 = false;
    let h1 = [];
    let m;
    while ((m = rx.exec(src))) {
      const [, closing, rawTag, rawAttrs, text] = m;
      if (text !== undefined || (!rawTag && m[0] === "<")) {
        if (skip > 0) continue;
        const t = decodeEntities(text ?? "<").replace(/\s+/g, " ").trim();
        if (!t) continue;
        out.texts.push(t);
        if (inH1) h1.push(t);
        continue;
      }
      if (!rawTag) continue; // comment, doctype
      const tag = rawTag.toLowerCase();
      if (closing) {
        if (SKIP.has(tag)) skip = Math.max(0, skip - 1);
        if (tag === "header" || tag === "nav" || tag === "footer") chrome = Math.max(0, chrome - 1);
        if (tag === "h1" && inH1) {
          inH1 = false;
          out.heading = h1.join(" ").replace(/\s+/g, " ").trim() || null;
        }
        continue;
      }
      const a = attrsOf(rawAttrs || "");
      const selfClosing = /\/\s*$/.test(rawAttrs || "");
      if (tag === "script" || tag === "style") {
        const end = src.toLowerCase().indexOf(`</${tag}`, rx.lastIndex);
        const body = src.slice(rx.lastIndex, end === -1 ? src.length : end);
        rx.lastIndex = end === -1 ? src.length : src.indexOf(">", end) + 1 || src.length;
        if (tag === "script") ((a.type || "").includes("ld+json") ? out.jsonld : out.scripts).push(body);
        continue;
      }
      if (tag === "meta") {
        const key = a.property || a.name || a.itemprop;
        if (key && "content" in a) (out.meta[key] = out.meta[key] || []).push(a.content);
      } else if (tag === "link" && (a.rel || "").includes("canonical")) {
        out.canonical = a.href || null;
      } else if (tag === "a" && (a.href || "").includes("/member/")) {
        if (chrome === 0) out.memberLinks.push(a.href);
      } else if (tag === "header" || tag === "nav" || tag === "footer") {
        chrome += 1;
      } else if (tag === "h1" && out.heading === null && !inH1) {
        inH1 = true;
        h1 = [];
      }
      if (SKIP.has(tag) && !selfClosing && !VOID.has(tag)) skip += 1;
    }
    return out;
  }

  // ------------------------------------------------------------------ item page
  function asArray(v) {
    if (v === null || v === undefined) return [];
    return Array.isArray(v) ? v : [v];
  }

  function products(blocks) {
    const out = [];
    const visit = (node) => {
      if (Array.isArray(node)) return node.forEach(visit);
      if (!node || typeof node !== "object") return;
      if (asArray(node["@type"]).some((t) => typeof t === "string" && /product/i.test(t))) out.push(node);
      if (node["@graph"]) visit(node["@graph"]);
    };
    for (const b of blocks) {
      try {
        visit(typeof b === "string" ? JSON.parse(b) : b);
      } catch {
        /* malformed block */
      }
    }
    return out;
  }

  function breadcrumbs(blocks) {
    for (const b of blocks) {
      let data;
      try {
        data = typeof b === "string" ? JSON.parse(b) : b;
      } catch {
        continue;
      }
      for (const node of asArray(data)) {
        if (node && typeof node === "object" && node["@type"] === "BreadcrumbList") {
          return asArray(node.itemListElement)
            .filter((i) => i && typeof i === "object")
            .map((i) => String((i.item && i.item.name) || i.name || ""))
            .filter(Boolean)
            .slice(0, 6);
        }
      }
    }
    return [];
  }

  /** "Brand" followed by "Ralph Lauren" in reading order -> { brand: "Ralph Lauren" }. */
  function labelPairs(texts, C, pairs) {
    const found = {};
    for (const [label, value] of pairs || []) {
      const key = labelKey(label, C);
      const v = clean(value, C.limits.short);
      if (key && v && !(key in found) && labelKey(v, C) === null) found[key] = v;
    }
    for (let i = 0; i < texts.length - 1; i += 1) {
      const t = texts[i];
      if (t.length > 30) continue;
      const key = labelKey(t, C);
      if (key && !(key in found)) {
        const value = texts[i + 1];
        if (value.length > 0 && value.length <= 120 && labelKey(value, C) === null) found[key] = value;
      }
    }
    return found;
  }

  const first = (meta, key) => asArray(meta[key])[0];

  /**
   * Item page -> every field the server parser reads (same rules, same shared configuration).
   * `opts.useScripts = false` ignores the page scripts: after Vinted's client-side navigation
   * they still describe the first item opened, so only the visible page is trusted.
   */
  function parseItem(collected, url, now, C, opts = {}) {
    const c = { ...emptyCollected(), ...collected };
    const useScripts = opts.useScripts !== false;
    const id = itemId(url, C) || itemId(c.canonical || "", C);
    const item = {
      vinted_id: id,
      url: String(url).split(/[?#]/)[0],
      title: "",
      description: "",
      price: null,
      currency: "EUR",
      brand: null,
      size: null,
      condition_label: null,
      condition: null,
      color: null,
      material: null,
      category_path: [],
      favourite_count: null,
      view_count: null,
      published_at: null,
      status: "active",
      status_source: "default",
      buyer_protection_fee: null,
      shipping_fee: null,
      images: [],
      images_source: "none",
      member_id: null,
      seller_rating: null,
      seller_review_count: null,
      sources: [],
    };
    let product = products(c.jsonld)[0] || null;
    // Structured data of another item (left over by client-side navigation) is ignored.
    const productId = product ? itemId(String(product.url || ""), C) : null;
    if (product && productId && id && productId !== id) {
      product = null;
      item.sources.push("stale-jsonld");
    }
    const metaId = itemId(String(first(c.meta, "og:url") || c.canonical || ""), C);
    const meta = metaId && id && metaId !== id ? {} : c.meta;
    let offer = {};
    if (product) {
      offer = asArray(product.offers)[0] || {};
      item.sources.push("jsonld");
    }
    // The item's own embedded object; never values of the signed-in user or suggested items.
    const keys = itemJsonKeys(C);
    const obj = useScripts ? findItem(c.scripts, id, keys) : null;
    if (obj) item.sources.push("item_json");
    const emb = (name) => {
      const v = pick(obj, keys[name]);
      if (v === null || typeof v === "object") return null;
      return String(v);
    };

    const title = (product && product.name) || c.heading || first(meta, "og:title") || "";
    item.title = decodeEntities(clean(title, C.limits.title).replace(C.patterns.site_suffix, ""));
    let price = offer.price !== undefined && offer.price !== null ? parsePrice(String(offer.price)) : null;
    price = price || parsePrice(first(meta, "product:price:amount")) || parsePrice(first(meta, "og:price:amount"));
    if (!price) {
      for (const t of c.texts.slice(0, 400)) {
        const hit = findPrice(t, C);
        if (hit) {
          price = hit.price;
          item.currency = hit.currency || item.currency;
          break;
        }
      }
    }
    item.price = price || null;
    const currency = offer.priceCurrency || first(meta, "product:price:currency");
    if (currency) item.currency = String(currency).toUpperCase().slice(0, 3);

    item.description = cleanMultiline((product && product.description) || c.description || first(meta, "og:description") || "", C.limits.description);
    const pairs = labelPairs(c.texts, C, c.pairs);
    if (Object.keys(pairs).length) item.sources.push("labels");
    const brand = product && product.brand;
    item.brand = clean((brand && typeof brand === "object" ? brand.name : brand) || pairs.brand || "", C.limits.short) || null;
    item.size = pairs.size || null;
    item.condition_label = pairs.condition || null;
    item.condition = normalizeCondition(item.condition_label, C) || null;
    item.color = pairs.color || (product && product.color) || null;
    item.material = pairs.material || (product && product.material) || null;
    if (!item.material) {
      const mat = emb("material");
      if (mat) item.material = unescapeJsonString(mat);
    }
    item.category_path = breadcrumbs(c.jsonld);
    if (!item.category_path.length && c.breadcrumbs) item.category_path = c.breadcrumbs.slice(0, 6);

    const digits = (s) => String(s || "").replace(/\D/g, "");
    const fav = emb("favourites") || digits(pairs.favourites) || digits(c.favourites) || null;
    const views = emb("views") || digits(pairs.views) || null;
    item.favourite_count = fav ? Number(fav) : null;
    item.view_count = views ? Number(views) : null;
    if (item.favourite_count !== null || item.view_count !== null) item.sources.push("demand");

    const created = emb("created");
    if (created) {
      const d = new Date(created);
      item.published_at = Number.isNaN(d.getTime()) ? null : created;
    }
    if (!item.published_at && pairs.uploaded) {
      const d = relativeTime(pairs.uploaded, now, C);
      item.published_at = d ? d.toISOString() : null;
    }

    // Fees: "€19,60 include la Protezione acquisti" -> protection = total - price.
    if (item.price) {
      for (const t of c.texts.slice(0, 600)) {
        if (!C.patterns.protection_included.test(t)) continue;
        const hit = findPrice(t, C);
        if (!hit) continue;
        const diff = round2(hit.price - item.price);
        if (diff > 0 && diff <= item.price * 0.2 + 5) {
          item.buyer_protection_fee = diff;
          break;
        }
      }
      if (item.buyer_protection_fee === null) {
        const fee = emb("service_fee");
        if (fee) item.buyer_protection_fee = parsePrice(fee);
      }
    }
    const ship = emb("shipping");
    if (ship !== null) item.shipping_fee = parsePrice(ship);

    // Status: embedded flags, schema.org availability, then visible badges.
    const action = emb("closing_action");
    const closed = emb("closed");
    const reserved = emb("reserved");
    const availability = String(offer.availability || "");
    const pageText = c.texts.slice(0, 250).join(" ");
    const topText = [...(c.statusTexts || []), ...c.texts.slice(0, 60)].join(" ");
    const set = (status, source) => {
      item.status = status;
      item.status_source = source;
    };
    if (action === "sold" || (closed === "true" && (action === null || action === "sold"))) set("sold", "embedded");
    else if (closed === "true") set("removed", "embedded");
    else if (reserved === "true") set("reserved", "embedded");
    else if (availability.includes("SoldOut")) set("sold", "jsonld");
    else if (!product && C.patterns.status_removed.test(pageText)) set("removed", "text");
    else if (closed === "false" || availability.includes("InStock")) set("active", closed ? "embedded" : "jsonld");
    else if (C.patterns.status_sold.test(topText)) set("sold", "text");
    else if (C.patterns.status_reserved.test(topText)) set("reserved", "text");

    // Photos: only the item's gallery, in its order. The item's structured data first; then the
    // product's JSON-LD, the page's gallery container, its preview image. Profile photos found
    // anywhere in the page are excluded whatever the source.
    let images = galleryOf(obj, keys);
    item.images_source = images.length ? "item_json" : "none";
    if (!images.length) {
      for (const img of asArray(product && product.image)) {
        const u = typeof img === "string" ? img : img && (img.url || img.contentUrl);
        if (typeof u === "string") images.push(u);
      }
      if (images.length) item.images_source = "jsonld";
    }
    if (!images.length && (c.images || []).length) {
      images = [...c.images];
      item.images_source = "gallery_dom";
    }
    if (!images.length && asArray(meta["og:image"]).length) {
      images = [asArray(meta["og:image"])[0]];
      item.images_source = "meta";
    }
    const avatars = profilePhotoUrls(useScripts ? c.scripts : [], keys);
    for (const u of c.avatarUrls || []) avatars.add(u);
    item.images = [...new Set(images.filter((u) => isHttpUrl(u) && !avatars.has(u)))].slice(0, C.limits.images);

    // Seller: from the item's own seller object (member id hashed before sending), rating and
    // review count. A member link in the page body only when the page has no structured data.
    const sellerObj = pick(obj, keys.seller);
    const seller = sellerObj && typeof sellerObj === "object" && !Array.isArray(sellerObj) ? sellerObj : null;
    const sid = pick(seller, keys.seller_id);
    if (sid !== null) item.member_id = String(sid);
    else if (!obj) {
      for (const href of c.memberLinks) {
        const mm = C.patterns.member_id.exec(href);
        if (mm) {
          item.member_id = mm[1];
          break;
        }
      }
    }
    const semb = (name) => {
      const v = pick(seller, keys[name]);
      return v === null || typeof v === "object" || typeof v === "boolean" ? null : String(v);
    };
    const rep = semb("seller_rating");
    if (rep && Number.isFinite(Number(rep))) {
      const v = Number(rep);
      item.seller_rating = round2(v <= 1 ? v * 5 : v);
    } else if (c.sellerRating) {
      const v = parsePrice(String(c.sellerRating).replace(/\s*(su|of|sur|von|de)\s*5.*$/i, ""));
      if (v && v <= 5) item.seller_rating = v;
    }
    const count = semb("seller_reviews") || (obj ? null : digits(c.sellerReviews)) || null;
    item.seller_review_count = count ? Number(count) : null;

    item.missing = ["title", "price"].filter((k) => !item[k]);
    item.complete = item.missing.length === 0 && Boolean(item.vinted_id);
    return item;
  }

  // ------------------------------------------------------------------ result cards
  /**
   * The one-line summary Vinted puts on each result card (link title / image alt), e.g.
   * "Polo Ralph Lauren, brand: Ralph Lauren, condizioni: Ottime, taglia: M, 15,00 €, 16,45 € include la Protezione acquisti".
   */
  function parseCardSummary(text, C) {
    const s = clean(text, 600);
    const out = { title: "", attrs: {}, price: null, currency: "", total: null };
    if (!s) return out;
    const p = findPrice(s, C);
    // Everything after the first price is buyer-protection small print.
    const head = p ? s.slice(0, p.index).replace(/[,\s]+$/, "") : s;
    if (p) {
      Object.assign(out, { price: p.price, currency: p.currency });
      const tail = s.slice(p.end);
      const total = findPrice(tail, C);
      if (total && C.patterns.protection_included.test(tail)) out.total = total.price;
    }
    const marks = [];
    C.summaryLabel.lastIndex = 0;
    let m;
    while ((m = C.summaryLabel.exec(head))) marks.push({ key: labelKey(m[1], C), start: m.index, from: C.summaryLabel.lastIndex });
    out.title = clean(marks.length ? head.slice(0, marks[0].start) : head, C.limits.title);
    marks.forEach((mark, i) => {
      const value = clean(head.slice(mark.from, i + 1 < marks.length ? marks[i + 1].start : head.length), C.limits.short);
      if (mark.key && value && !out.attrs[mark.key]) out.attrs[mark.key] = value;
    });
    return out;
  }

  function stripSiteSuffix(title, C) {
    return title.replace(C.patterns.site_suffix, "").trim();
  }

  /**
   * One result card -> card record, or null when it can't be read.
   * raw = { href, summary, alt, image, testids: {suffix: text}, texts: [leaf texts], favourites }
   */
  function parseCatalogCard(raw, base, C) {
    const url = cleanItemUrl(raw.href, base);
    if (!url) return null;
    const summary = parseCardSummary(raw.summary || "", C);
    const alt = parseCardSummary(raw.alt || "", C);
    const ids = raw.testids || {};
    const texts = (raw.texts || []).map((t) => clean(t, 200)).filter(Boolean);

    let price = summary.price || alt.price || null;
    let currency = summary.currency || alt.currency || "";
    let total = summary.total || alt.total || null;
    for (const candidate of [ids["price-text"], ...texts]) {
      if (price) break;
      const found = findPrice(candidate, C);
      if (found) ({ price, currency } = found);
    }
    if (price && !total) {
      for (const t of texts) {
        if (!C.patterns.protection_included.test(t)) continue;
        const hit = findPrice(t, C);
        if (hit && hit.price > price) {
          total = hit.price;
          break;
        }
      }
    }
    const sub = { size: "", condition: "" };
    for (const part of clean(ids["description-subtitle"], 200).split(/\s*[·•|]\s*/)) {
      if (!part) continue;
      if (!sub.condition && normalizeCondition(part, C)) sub.condition = part;
      else if (!sub.size && part.length <= 30) sub.size = part;
    }
    const attrs = { ...alt.attrs, ...summary.attrs };
    const brand = attrs.brand || clean(ids["description-title"], C.limits.short);
    const conditionText = attrs.condition || sub.condition;
    const title = summary.title || alt.title || clean(ids.title, C.limits.title) || [brand, sub.size].filter(Boolean).join(" ");
    // Badges are short ("Venduto", "Riservato"); titles and descriptions are not checked.
    const badges = texts.filter((t) => t.length <= 25);
    let status = "active";
    if (badges.some((t) => C.patterns.status_sold.test(t))) status = "sold";
    else if (badges.some((t) => C.patterns.status_reserved.test(t))) status = "reserved";
    const favDigits = String(raw.favourites || "").replace(/\D/g, "");
    let fee = null;
    if (price && total) {
      const diff = round2(total - price);
      if (diff > 0 && diff <= price * 0.2 + 5) fee = diff;
    }
    return {
      vinted_id: itemId(url, C),
      url,
      title: stripSiteSuffix(title, C),
      price,
      currency: currency || "EUR",
      brand,
      size: attrs.size || sub.size,
      condition: normalizeCondition(conditionText, C),
      condition_label: conditionText,
      color: attrs.color || "",
      image_urls: isHttpUrl(raw.image) ? [raw.image] : [],
      status,
      favourite_count: favDigits && favDigits.length <= 6 ? Number(favDigits) : null,
      buyer_protection_fee: fee,
      images_source: "card",
    };
  }

  /** Every readable card (once each). Returns { items, unreadable }. */
  function parseCards(rawCards, base, C) {
    const seen = new Set();
    const items = [];
    let unreadable = 0;
    for (const raw of rawCards || []) {
      const card = parseCatalogCard(raw, base, C);
      const id = card && card.vinted_id;
      if (id && seen.has(id)) continue;
      if (id) seen.add(id);
      if (!card || !card.title || card.title.length < 3 || !card.price) {
        unreadable += 1;
        continue;
      }
      items.push(card);
    }
    return { items, unreadable };
  }

  // ------------------------------------------------------------------ payload for FlipFinder
  const STATUSES = new Set(["active", "reserved", "sold", "removed"]);
  const IMAGE_SOURCES = new Set(["item_json", "jsonld", "gallery_dom", "meta", "card"]);

  function intOrNull(v, max = 1e9) {
    const n = Number(v);
    return v !== null && v !== undefined && v !== "" && Number.isInteger(n) && n >= 0 && n <= max ? n : null;
  }

  function moneyOrNull(v, max) {
    const n = Number(v);
    return v !== null && v !== undefined && Number.isFinite(n) && n >= 0 && n <= max ? round2(n) : null;
  }

  /**
   * The record sent to FlipFinder, within the server's limits (a single invalid field would
   * reject a whole batch). Returns null when the listing can't be sent (no title/price, not
   * a Vinted item, or a currency other than euro, which FlipFinder doesn't analyse).
   */
  function capturePayload(fields, C) {
    const url = cleanItemUrl(fields.url || "", "https://www.vinted.it/");
    if (!url || !isVintedUrl(url, C)) return null;
    const title = clean(fields.title, 300);
    const price = moneyOrNull(fields.price, 100000);
    if (title.length < 3 || !price) return null;
    if (fields.currency && String(fields.currency).toUpperCase() !== "EUR") return null;
    const out = { url, title, price };
    const text = (key, max) => {
      const v = clean(fields[key], max);
      if (v) out[key] = v;
    };
    text("brand", 120);
    text("size", 60);
    text("color", 60);
    text("material", 120);
    const condition = clean(fields.condition_label || fields.condition, 60);
    if (condition) out.condition = condition;
    const category = clean(Array.isArray(fields.category_path) ? fields.category_path.join(" > ") : fields.category_path, 200);
    if (category) out.category_path = category;
    const description = cleanMultiline(fields.description, 5000);
    if (description) out.description = description;
    out.image_urls = [...new Set((fields.image_urls || fields.images || []).filter((u) => isHttpUrl(u) && u.length <= 2000))].slice(0, 20);
    if (IMAGE_SOURCES.has(fields.images_source)) out.images_source = fields.images_source;
    if (fields.seller_key && /^h:[0-9a-f]{24}$/.test(fields.seller_key)) out.seller_key = fields.seller_key;
    const rating = moneyOrNull(fields.seller_rating, 5);
    if (rating !== null) out.seller_rating = rating;
    const reviews = intOrNull(fields.seller_review_count);
    if (reviews !== null) out.seller_review_count = reviews;
    const ship = moneyOrNull(fields.shipping_fee, 1000);
    if (ship !== null) out.shipping_fee = ship;
    const fee = moneyOrNull(fields.buyer_protection_fee, 1000);
    if (fee !== null) out.buyer_protection_fee = fee;
    const fav = intOrNull(fields.favourite_count);
    if (fav !== null) out.favourite_count = fav;
    const views = intOrNull(fields.view_count);
    if (views !== null) out.view_count = views;
    if (fields.published_at && !Number.isNaN(new Date(fields.published_at).getTime())) out.published_at = new Date(fields.published_at).toISOString();
    if (STATUSES.has(fields.status)) out.status = fields.status;
    return out;
  }

  /** Item page -> payload, the seller reduced to its one-way hash. */
  async function itemPayload(parsed, C) {
    const seller_key = parsed.member_id ? await sellerKey(parsed.member_id) : null;
    return capturePayload({ ...parsed, seller_key }, C);
  }

  // ------------------------------------------------------------------ links without pairing
  function toBase64Url(bytes) {
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
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

  /** One item, opened in FlipFinder's analysis form (works without pairing). */
  function analyzeUrl(appUrl, payload) {
    return `${appBase(appUrl)}/analyze#import=${encodeImport({ v: 1, source: "vinted", ...payload })}`;
  }

  /** Several cards, opened in FlipFinder's batch import (works without pairing). */
  async function importUrl(appUrl, items, query = "") {
    return `${appBase(appUrl)}/import#batch=${await encodeBatch({ v: 1, source: "vinted_search", query, items })}`;
  }

  return {
    fold,
    compileConfig,
    parsePrice,
    findPrice,
    normalizeCondition,
    labelKey,
    relativeTime,
    itemId,
    pageType,
    isVintedUrl,
    cleanItemUrl,
    sellerKey,
    decodeEntities,
    collectHtml,
    payloadTexts,
    findItem,
    profilePhotoUrls,
    itemJsonKeys,
    parseItem,
    parseCardSummary,
    parseCatalogCard,
    parseCards,
    capturePayload,
    itemPayload,
    encodeImport,
    encodeBatch,
    analyzeUrl,
    importUrl,
  };
});
