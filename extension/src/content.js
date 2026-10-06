/*
 * FlipFinder for Vinted - content script.
 *
 * Runs only on Vinted pages the user opens. It reads what is already rendered (no requests
 * to Vinted, no scrolling or clicking on the user's behalf) and, when the user clicks, opens
 * FlipFinder with the data in the URL fragment (never sent to any server by the browser):
 *  - on an item page: the full analysis of that listing (/analyze);
 *  - on a search/catalog page: every listing loaded on the page, analysed and ranked (/import).
 */
(() => {
  "use strict";
  const P = globalThis.FlipFinderParse;
  const DEFAULT_APP_URL = "http://localhost:3000";
  const ITEM_PATH = /\/items\/\d+/;

  const text = (el) => (el ? (el.textContent || "").replace(/\s+/g, " ").trim() : "");

  function collectPairs() {
    const pairs = [];
    const add = (label, value) => {
      if (label && value && label !== value) pairs.push([label, value]);
    };
    // 1. Structured attribute rows (Vinted marks the details block with data-testid / itemprop).
    document.querySelectorAll('[data-testid*="item-attributes"], [data-testid*="item-details"]').forEach((row) => {
      const kids = row.children;
      if (kids.length >= 2) add(text(kids[0]), text(kids[kids.length - 1]));
    });
    document.querySelectorAll('[itemprop="brand"], [itemprop="color"], [itemprop="size"]').forEach((el) => {
      add(el.getAttribute("itemprop"), text(el.querySelector('[itemprop="name"]') || el));
    });
    // 2. Definition lists.
    document.querySelectorAll("dt").forEach((dt) => add(text(dt), text(dt.nextElementSibling)));
    // 3. Fallback: a short element whose whole text is a known label; value = last sibling.
    const main = document.querySelector("main") || document.body;
    const walker = document.createTreeWalker(main, NodeFilter.SHOW_ELEMENT);
    let node = walker.currentNode;
    let seen = 0;
    while ((node = walker.nextNode()) && seen < 6000) {
      seen += 1;
      if (node.childElementCount > 0) continue;
      const label = text(node);
      if (!label || label.length > 30 || !P.labelKey(label)) continue;
      const row = node.parentElement;
      if (!row) continue;
      const candidates = [node.nextElementSibling, row.nextElementSibling, row.lastElementChild];
      const value = candidates.map(text).find((v) => v && v !== label && v.length <= 120);
      if (value) add(label, value);
    }
    return pairs;
  }

  function collect() {
    const jsonLd = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
      try {
        jsonLd.push(JSON.parse(s.textContent || ""));
      } catch {
        /* ignore malformed blocks */
      }
    });
    const meta = {};
    document.querySelectorAll("meta[property], meta[name], meta[itemprop]").forEach((m) => {
      const key = m.getAttribute("property") || m.getAttribute("name") || m.getAttribute("itemprop");
      const value = m.getAttribute("content");
      if (key && value) (meta[key] = meta[key] || []).push(value);
    });
    const description =
      text(document.querySelector('[itemprop="description"]')) ||
      text(document.querySelector('[data-testid*="item-description"]'));
    const seller =
      text(document.querySelector('[data-testid*="profile-username"]')) ||
      text(document.querySelector('a[href*="/member/"] [data-testid*="username"]'));
    return {
      location: location.href,
      canonical: document.querySelector('link[rel="canonical"]')?.getAttribute("href") || "",
      jsonLd,
      meta,
      pairs: collectPairs(),
      heading: text(document.querySelector("main h1") || document.querySelector("h1")),
      description,
      seller,
    };
  }

  // ------------------------------------------------------------------ search-results pages
  const ITEM_LINK = 'a[href*="/items/"]';

  function linkId(a) {
    return P.itemId(a.getAttribute("href") || "");
  }

  /** Distinct listings currently rendered on the page (cheap: used to label the button). */
  function countCards() {
    const ids = new Set();
    for (const a of document.querySelectorAll(ITEM_LINK)) {
      const id = linkId(a);
      if (id) ids.add(id);
    }
    return ids.size;
  }

  /** The largest ancestor of an item link that contains no other item: the result card. */
  function cardRoot(link, id) {
    let el = link;
    for (let depth = 0; depth < 10; depth += 1) {
      const parent = el.parentElement;
      if (!parent || parent === document.body) break;
      for (const a of parent.querySelectorAll(ITEM_LINK)) {
        if (linkId(a) !== id) return el;
      }
      el = parent;
    }
    return el;
  }

  function collectCard(root, link) {
    const img = root.querySelector("img");
    const testids = {};
    root.querySelectorAll("[data-testid]").forEach((el) => {
      const suffix = /--([a-z-]+)$/.exec(el.getAttribute("data-testid") || "")?.[1];
      if (suffix && !(suffix in testids)) testids[suffix] = text(el);
    });
    const texts = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode() && texts.length < 40) {
      const t = (walker.currentNode.textContent || "").replace(/\s+/g, " ").trim();
      if (t && t.length <= 200) texts.push(t);
    }
    const summaryLink = root.querySelector(`${ITEM_LINK}[title]`) || link;
    return {
      href: link.getAttribute("href") || "",
      summary: summaryLink.getAttribute("title") || summaryLink.getAttribute("aria-label") || "",
      alt: img?.getAttribute("alt") || "",
      image: img?.currentSrc || img?.getAttribute("src") || "",
      testids,
      texts,
    };
  }

  function collectCatalog() {
    const cards = [];
    const done = new Set();
    for (const link of document.querySelectorAll(ITEM_LINK)) {
      const id = linkId(link);
      if (!id || done.has(id)) continue;
      done.add(id);
      cards.push(collectCard(cardRoot(link, id), link));
    }
    return cards;
  }

  async function appUrl() {
    try {
      const stored = await chrome.storage.sync.get({ appUrl: DEFAULT_APP_URL });
      return stored.appUrl || DEFAULT_APP_URL;
    } catch {
      return DEFAULT_APP_URL;
    }
  }

  async function prepareCatalog() {
    const result = P.parseCatalog(collectCatalog(), location.href);
    if (!result.total) {
      return { error: "Nessun articolo leggibile qui. Apri una ricerca di Vinted o un annuncio e riprova." };
    }
    return {
      url: await P.importUrl(await appUrl(), result.data),
      count: result.data.items.length,
      overLimit: result.overLimit,
    };
  }

  /** Returns { url } to open, or { error } with a message for the user. */
  async function prepare() {
    if (!ITEM_PATH.test(location.pathname)) return prepareCatalog();
    const result = P.parseListing(collect());
    if (result.staleItem && result.missing.length) {
      return { error: "La pagina non è ancora aggiornata: ricaricala (F5) e riprova." };
    }
    if (!result.data.title && !result.data.price) {
      return { error: "Non riesco a leggere questo annuncio. Ricarica la pagina e riprova." };
    }
    // Missing fields are fine: FlipFinder shows the form so you can complete them.
    return { url: P.analyzeUrl(await appUrl(), result.data), missing: result.missing };
  }

  // ------------------------------------------------------------------ floating button
  let host = null;

  function mountButton() {
    if (host) return;
    host = document.createElement("div");
    host.id = "flipfinder-root";
    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <style>
        :host { all: initial; }
        .wrap { position: fixed; right: 20px; bottom: 20px; z-index: 2147483646;
          display: flex; flex-direction: column; align-items: flex-end; gap: 8px;
          font: 500 14px/1.2 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
        button { all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 8px;
          padding: 12px 18px 12px 14px; border-radius: 999px; color: #fff;
          background: linear-gradient(135deg, #e5532a, #f59e0b);
          box-shadow: 0 10px 30px -10px rgba(229, 83, 42, 0.7), 0 2px 6px rgba(0, 0, 0, 0.18);
          transition: transform 160ms cubic-bezier(0.23, 1, 0.32, 1), box-shadow 200ms ease;
          opacity: 1; transform: translateY(0);
          @starting-style { opacity: 0; transform: translateY(12px); } }
        button:active { transform: scale(0.97); }
        @media (hover: hover) and (pointer: fine) {
          button:hover { transform: translateY(-1px); box-shadow: 0 14px 34px -10px rgba(229, 83, 42, 0.8), 0 2px 6px rgba(0,0,0,.2); }
        }
        button:focus-visible { outline: 3px solid rgba(42, 120, 214, 0.6); outline-offset: 3px; }
        svg { width: 18px; height: 18px; flex: none; }
        .label { font-variant-numeric: tabular-nums; }
        button[aria-busy="true"] { cursor: progress; opacity: 0.8; }
        .msg { max-width: 280px; padding: 10px 12px; border-radius: 12px; background: #141415; color: #f5f5f3;
          font-weight: 400; font-size: 13px; line-height: 1.4; box-shadow: 0 10px 30px -12px rgba(0,0,0,.5); }
        .msg[hidden] { display: none; }
        @media (prefers-reduced-motion: reduce) { button { transition: none; } }
      </style>
      <div class="wrap">
        <div class="msg" role="status" hidden></div>
        <button type="button">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>
          <span class="label">Analizza con FlipFinder</span>
        </button>
      </div>`;
    const msg = shadow.querySelector(".msg");
    let timer = 0;
    const say = (message) => {
      msg.textContent = message;
      msg.hidden = false;
      clearTimeout(timer);
      timer = setTimeout(() => (msg.hidden = true), 5000);
    };
    const button = shadow.querySelector("button");
    button.addEventListener("click", async () => {
      if (button.getAttribute("aria-busy") === "true") return;
      button.setAttribute("aria-busy", "true");
      try {
        const res = await prepare();
        if (res.error) return say(res.error);
        if (res.overLimit) say(`Analizzo i primi ${res.count} articoli (massimo ${P.MAX_BATCH} per volta).`);
        await chrome.runtime.sendMessage({ type: "flipfinder:open", url: res.url });
      } catch {
        say("Estensione aggiornata: ricarica la pagina di Vinted e riprova.");
      } finally {
        button.removeAttribute("aria-busy");
      }
    });
    document.documentElement.appendChild(host);
  }

  function setButtonLabel(label, aria) {
    const shadow = host?.shadowRoot;
    if (!shadow) return;
    const span = shadow.querySelector(".label");
    if (span.textContent !== label) span.textContent = label;
    shadow.querySelector("button").setAttribute("aria-label", aria);
  }

  function unmountButton() {
    host?.remove();
    host = null;
  }

  // Vinted navigates client-side and loads results as you browse: keep the button in step.
  function sync() {
    if (ITEM_PATH.test(location.pathname)) {
      mountButton();
      setButtonLabel("Analizza con FlipFinder", "Analizza questo annuncio con FlipFinder");
      return;
    }
    const count = countCards();
    if (count < 2) return unmountButton();
    mountButton();
    const n = Math.min(count, P.MAX_BATCH);
    setButtonLabel(`Analizza ${n} articoli`, `Analizza con FlipFinder i ${n} articoli caricati in questa pagina`);
  }
  sync();
  setInterval(sync, 800);

  // Toolbar popup -> "analyze the open tab"
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message && message.type === "flipfinder:prepare") {
      prepare().then(sendResponse);
      return true; // async response
    }
    return false;
  });
})();
