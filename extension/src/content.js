/*
 * FlipFinder for Vinted - content script.
 *
 * Runs only on Vinted pages the user opens. It reads what is already rendered on the item
 * page (no requests to Vinted, no automation) and, when the user clicks, opens FlipFinder's
 * analysis with the data in the URL fragment (never sent to any server by the browser).
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

  async function appUrl() {
    try {
      const stored = await chrome.storage.sync.get({ appUrl: DEFAULT_APP_URL });
      return stored.appUrl || DEFAULT_APP_URL;
    } catch {
      return DEFAULT_APP_URL;
    }
  }

  /** Returns { url } to open, or { error } with a message for the user. */
  async function prepare() {
    if (!ITEM_PATH.test(location.pathname)) {
      return { error: "Apri la pagina di un singolo articolo su Vinted." };
    }
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
        .msg { max-width: 280px; padding: 10px 12px; border-radius: 12px; background: #141415; color: #f5f5f3;
          font-weight: 400; font-size: 13px; line-height: 1.4; box-shadow: 0 10px 30px -12px rgba(0,0,0,.5); }
        .msg[hidden] { display: none; }
        @media (prefers-reduced-motion: reduce) { button { transition: none; } }
      </style>
      <div class="wrap">
        <div class="msg" role="status" hidden></div>
        <button type="button" aria-label="Analizza questo annuncio con FlipFinder">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>
          Analizza con FlipFinder
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
    shadow.querySelector("button").addEventListener("click", async () => {
      const res = await prepare();
      if (res.error) return say(res.error);
      try {
        await chrome.runtime.sendMessage({ type: "flipfinder:open", url: res.url });
      } catch {
        say("Estensione aggiornata: ricarica la pagina di Vinted e riprova.");
      }
    });
    document.documentElement.appendChild(host);
  }

  function unmountButton() {
    host?.remove();
    host = null;
  }

  // Vinted navigates client-side: follow the path and show the button only on item pages.
  let lastPath = "";
  function sync() {
    if (location.pathname === lastPath) return;
    lastPath = location.pathname;
    if (ITEM_PATH.test(lastPath)) mountButton();
    else unmountButton();
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
