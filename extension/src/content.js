/*
 * FlipFinder for Vinted - content script (only on Vinted pages).
 *
 * Reads what the page you opened shows, as you scroll it: result cards when at least half of a
 * card enters the screen, the whole listing on an item page. It never navigates, scrolls or
 * opens listings by itself and never acts on your Vinted account (no purchases, offers,
 * favourites or messages). It reads no cookies or tokens.
 *
 * Captures go to the service worker (queue + sync with FlipFinder); evaluations come back as
 * badges on the cards and a small box on item pages, with quick actions on your click.
 */
(() => {
  "use strict";
  if (window.__flipfinder) return; // injected once per page
  window.__flipfinder = true;

  const P = globalThis.FlipFinderParse;
  const K = globalThis.FlipFinderCore;
  const Q = globalThis.FlipFinderQuick;
  let market = null; // compiled market summary (downloaded by the service worker)
  let C = P.compileConfig(globalThis.FF_PARSER_CONFIG);
  let opts = K.normalizeOptions({});
  let paired = false;

  const text = (el) => (el ? (el.textContent || "").replace(/\s+/g, " ").trim() : "");
  const send = (msg) => chrome.runtime.sendMessage(msg).catch(() => null);
  // Timing marks (visible in DevTools > Performance): where the instant verdict spends its time.
  const mark = (name) => performance.mark(`ff:${name}`);
  const idle = (fn) => (window.requestIdleCallback ? requestIdleCallback(fn, { timeout: 800 }) : setTimeout(fn, 50));

  function debounce(fn, ms) {
    let t = 0;
    return (...a) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...a), ms);
    };
  }

  function useConfig(raw) {
    try {
      if (raw && raw.version) C = P.compileConfig(raw);
    } catch {
      C = P.compileConfig(globalThis.FF_PARSER_CONFIG); // a broken download never breaks the page
    }
  }

  // ------------------------------------------------------------------ state
  let pageType = P.pageType(location.pathname, C);
  let lastHref = location.href;
  const evals = new Map(); // vid -> evaluation from FlipFinder (full analysis, your costs)
  const quick = new Map(); // vid -> instant verdict from the service worker (local estimate or cached evaluation)
  const verdict = (vid) => evals.get(vid) || quick.get(vid);
  const cards = new Map(); // vid -> { root, captured, badge }
  const registered = new WeakSet();
  const pendingCards = new Map(); // vid -> payload (to send)
  const deepState = new Map(); // vid -> reading | paused | error message

  // ------------------------------------------------------------------ live page collection
  /** The parser's input from the live page (the same structure collectHtml builds). */
  function collectDocument(useScripts) {
    const S = C.selectors;
    const main = document.querySelector("main") || document.body;
    const c = {
      jsonld: [],
      scripts: [],
      meta: {},
      canonical: document.querySelector('link[rel="canonical"]')?.getAttribute("href") || null,
      memberLinks: [],
      texts: [],
      heading: text(main.querySelector("h1") || document.querySelector("h1")) || null,
      images: [],
      pairs: [],
      statusTexts: [],
      breadcrumbs: [],
    };
    document.querySelectorAll(S.jsonld).forEach((s) => c.jsonld.push(s.textContent || ""));
    if (useScripts) {
      let budget = 3e6;
      for (const s of document.scripts) {
        const type = (s.type || "").toLowerCase();
        if (type && !type.includes("javascript") && type !== "application/json") continue;
        const body = s.textContent || "";
        if (!body || budget <= 0) continue;
        c.scripts.push(body.slice(0, budget));
        budget -= body.length;
      }
    }
    document.querySelectorAll("meta[property], meta[name], meta[itemprop]").forEach((m) => {
      const key = m.getAttribute("property") || m.getAttribute("name") || m.getAttribute("itemprop");
      const value = m.getAttribute("content");
      if (key && value) (c.meta[key] = c.meta[key] || []).push(value);
    });
    // The seller's link, not your own profile in the header.
    main.querySelectorAll(S.seller_profile_link).forEach((a) => {
      if (!a.closest("header, nav, footer")) c.memberLinks.push(a.getAttribute("href") || "");
    });
    const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement && n.parentElement.closest("script, style, noscript, template, svg") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    while (walker.nextNode() && c.texts.length < 4000) {
      const t = (walker.currentNode.textContent || "").replace(/\s+/g, " ").trim();
      if (t) c.texts.push(t);
    }
    document.querySelectorAll(S.attribute_rows).forEach((row) => {
      const kids = row.children;
      if (kids.length >= 2) c.pairs.push([text(kids[0]), text(kids[kids.length - 1])]);
      else if (row.getAttribute("itemprop")) c.pairs.push([row.getAttribute("itemprop"), text(row.querySelector('[itemprop="name"]') || row)]);
    });
    // Photos only from the item's gallery container (never avatars, logos, banners or the
    // thumbnails of suggested items elsewhere on the page).
    const imgSrc = (img) => img.currentSrc || img.getAttribute("src") || img.getAttribute("data-src") || "";
    const galleryBox = main.querySelector(S.gallery_container);
    if (galleryBox) {
      galleryBox.querySelectorAll(S.gallery_images || "img").forEach((img) => {
        const src = imgSrc(img);
        if (/^https:\/\//.test(src) && !img.closest(S.card_exclude)) c.images.push(src);
      });
    }
    c.avatarUrls = [];
    document.querySelectorAll(S.avatar_images).forEach((img) => {
      const src = imgSrc(img);
      if (src) c.avatarUrls.push(src);
    });
    const desc = main.querySelector(S.description);
    if (desc) c.description = desc.innerText || desc.textContent || "";
    const fav = main.querySelector(S.favourite_count);
    if (fav) c.favourites = fav.getAttribute("aria-label") || text(fav);
    main.querySelectorAll(S.status_badges).forEach((el) => {
      const t = text(el);
      if (t && t.length <= 40) c.statusTexts.push(t);
    });
    const rating = main.querySelector(S.seller_rating);
    if (rating) c.sellerRating = rating.getAttribute("aria-label") || text(rating);
    const reviews = main.querySelector(S.seller_reviews);
    if (reviews) c.sellerReviews = text(reviews);
    document.querySelectorAll(S.breadcrumbs).forEach((a) => {
      const t = text(a);
      if (t) c.breadcrumbs.push(t);
    });
    return c;
  }

  /** Page scripts describe the first item opened; after client-side navigation only the
   * visible page is trusted. */
  function scriptsAreFresh() {
    const nav = performance.getEntriesByType("navigation")[0];
    const initial = P.itemId(nav ? nav.name : "", C);
    return Boolean(initial) && initial === P.itemId(location.href, C);
  }

  // ------------------------------------------------------------------ cards
  function linkId(a) {
    return P.itemId(a.getAttribute("href") || "", C);
  }

  /** The largest ancestor of an item link that contains no link to another item: the card. */
  function cardRoot(link, id) {
    const other = `${C.selectors.item_link}:not([href*="/items/${id}-"]):not([href$="/items/${id}"]):not([href*="/items/${id}?"])`;
    let el = link;
    for (let depth = 0; depth < 10; depth += 1) {
      const parent = el.parentElement;
      if (!parent || parent === document.body || parent.tagName === "MAIN") break;
      if (parent.querySelector(other)) return el;
      el = parent;
    }
    return el;
  }

  function collectCard(root, link) {
    // The item's own photo: never the seller's avatar shown on the card.
    const img = [...root.querySelectorAll(C.selectors.card_image || "img")].find((el) => !el.closest(C.selectors.card_exclude)) || null;
    const testids = {};
    const suffixRx = new RegExp(C.selectors.card_testid_suffix || "--([a-z-]+)$");
    root.querySelectorAll("[data-testid]").forEach((el) => {
      const suffix = suffixRx.exec(el.getAttribute("data-testid") || "")?.[1];
      if (suffix && !(suffix in testids)) testids[suffix] = text(el);
    });
    const texts = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode() && texts.length < 40) {
      const t = (walker.currentNode.textContent || "").replace(/\s+/g, " ").trim();
      if (t && t.length <= 200) texts.push(t);
    }
    const summaryLink = root.querySelector(C.selectors.card_summary_link) || link;
    const fav = root.querySelector(C.selectors.favourite_count);
    return {
      href: link.getAttribute("href") || "",
      summary: summaryLink.getAttribute("title") || summaryLink.getAttribute("aria-label") || "",
      alt: img?.getAttribute("alt") || "",
      image: img?.currentSrc || img?.getAttribute("src") || "",
      testids,
      texts,
      favourites: fav ? fav.getAttribute("aria-label") || text(fav) : "",
    };
  }

  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const vid = entry.target.getAttribute("data-ff-vid");
        const card = vid && cards.get(vid);
        if (!card || card.captured) continue;
        card.captured = true;
        io.unobserve(entry.target);
        idle(() => captureCard(vid, card));
      }
    },
    { threshold: 0.5 },
  );

  function captureCard(vid, card) {
    if (!card.root.isConnected || !paired) return;
    const link = card.root.matches(C.selectors.item_link) ? card.root : card.root.querySelector(C.selectors.item_link);
    if (!link) return;
    const parsed = P.parseCatalogCard(collectCard(card.root, link), location.href, C);
    const payload = parsed && parsed.title && parsed.title.length >= 3 && parsed.price ? P.capturePayload(parsed, C) : null;
    if (!payload) {
      drawBadge(vid, { unreadable: true });
      return;
    }
    if (!opts.captureCards) return;
    pendingCards.set(vid, payload);
    if (!verdict(vid)) drawBadge(vid, null);
    card.quicked = false; // read late (lazy content): instant verdict now
    quickSoon();
    flushCards();
  }

  const quickSoon = debounce(() => quickPass(), 80);

  const flushCards = debounce(() => {
    if (!pendingCards.size) return;
    const batch = [...pendingCards.entries()].slice(0, 60);
    for (const [vid] of batch) pendingCards.delete(vid);
    send({ type: "ff:cards", pageType: pageType === "item" ? "other" : pageType, pageUrl: location.href, cards: batch.map(([vid, payload]) => ({ vid, payload })) });
    if (pendingCards.size) flushCards();
  }, 300);

  // ------------------------------------------------------------------ instant verdict
  // Every card of the page is read at once (the page you opened, nothing else) and scored by the
  // service worker from the downloaded market summary, so the best opportunity is highlighted
  // right away; the full analysis from FlipFinder then replaces each estimate.
  function quickCard(vid, card) {
    const link = card.root.matches(C.selectors.item_link) ? card.root : card.root.querySelector(C.selectors.item_link);
    if (!link) return null;
    const parsed = P.parseCatalogCard(collectCard(card.root, link), location.href, C);
    return parsed && parsed.title && parsed.title.length >= 3 && parsed.price ? parsed : null;
  }

  function quickPass() {
    if (!paired || !opts.enabled) return;
    mark("parse-start");
    const batch = [];
    const titles = new Map();
    for (const [vid, card] of cards) {
      if (card.quicked || !card.root.isConnected) continue;
      card.quicked = true;
      const parsed = quickCard(vid, card);
      if (!parsed) continue; // read again when it scrolls into view (lazy content)
      titles.set(vid, parsed.title);
      batch.push({
        vinted_id: vid,
        title: parsed.title,
        brand: parsed.brand,
        price: parsed.price,
        condition: parsed.condition,
        buyer_protection_fee: parsed.buyer_protection_fee,
        status: parsed.status,
      });
      // Second step, in the background: the whole card goes to FlipFinder for the full analysis.
      const payload = opts.captureCards ? P.capturePayload(parsed, C) : null;
      if (payload) {
        pendingCards.set(vid, payload);
        card.captured = true;
        io.unobserve(card.root);
      }
    }
    if (!batch.length) return;
    mark("parse-end");
    flushCards();
    // Scored right here from the stored market summary (~0.1 ms a card): no wait on the service
    // worker, which may be busy receiving full analyses. Server evaluations replace these.
    for (const c of batch) {
      const v = Q.quickEstimate(c, market);
      v.title = titles.get(c.vinted_id);
      quick.set(c.vinted_id, v);
    }
    // The best one first, the other badges in small slices: no long task while the page loads.
    updateBest();
    mark("best");
    const todo = batch.map((c) => c.vinted_id).filter((vid) => !evals.has(vid) && vid !== bestBox.vid);
    const slice = () => {
      for (const vid of todo.splice(0, 24)) if (!evals.has(vid)) drawBadge(vid, quick.get(vid));
      if (todo.length) requestAnimationFrame(slice);
      else {
        mark("badges");
        if (!document.documentElement.hasAttribute("data-ff-quick-ms")) document.documentElement.setAttribute("data-ff-quick-ms", String(Math.round(performance.now())));
      }
    };
    slice();
  }

  /** A newer market summary: re-score the cards that have no full analysis yet. */
  function rescore() {
    for (const [vid, v] of quick) {
      if (v.source !== "local") continue;
      const card = cards.get(vid);
      if (!card || !card.root.isConnected) continue;
      const parsed = quickCard(vid, card);
      if (!parsed) continue;
      const nv = Q.quickEstimate({ ...parsed, vinted_id: vid }, market);
      nv.title = parsed.title;
      quick.set(vid, nv);
      if (!evals.has(vid)) drawBadge(vid, nv);
    }
    updateBest();
  }

  const bestBox = { host: null, vid: null };

  /** Highest positive risk-adjusted profit among the cards on sale on this page. */
  function updateBest() {
    if (pageType === "item" || !opts.badges) return;
    let top = null;
    let topVal = 0;
    for (const [vid, card] of cards) {
      if (!card.root.isConnected) continue;
      const v = verdict(vid);
      const value = v && !v.insufficient && v.status === "active" ? v.risk_adjusted_profit : null;
      if (value !== null && value !== undefined && value > topVal) {
        top = vid;
        topVal = value;
      }
    }
    if (top === bestBox.vid && bestBox.host) return renderBest(top);
    const prev = bestBox.vid && cards.get(bestBox.vid);
    if (prev && prev.pageBest) {
      prev.pageBest = false;
      if (!prev.best) prev.root.style.outline = "";
    }
    bestBox.vid = top;
    if (!top) return renderBest(null);
    const card = cards.get(top);
    if (!evals.has(top)) drawBadge(top, verdict(top));
    card.pageBest = true;
    card.root.style.outline = "3px solid #16a34a";
    card.root.style.outlineOffset = "2px";
    renderBest(top);
    if (!document.documentElement.hasAttribute("data-ff-best-ms")) document.documentElement.setAttribute("data-ff-best-ms", String(Math.round(performance.now())));
  }
  const bestSoon = debounce(updateBest, 120);

  const BEST_CSS = `
    :host { all: initial; position: fixed; top: 76px; right: 16px; z-index: 2147483000; }
    .box { all: unset; box-sizing: border-box; display: block; width: 300px; max-width: calc(100vw - 32px); padding: 10px 12px; border-radius: 14px; cursor: pointer;
      background: #0f3d22; color: #f2fbf5; box-shadow: 0 10px 30px -8px rgba(0,0,0,.5); font: 400 12px/1.4 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .box:focus-visible { outline: 2px solid #4b8fea; outline-offset: 2px; }
    .k { font-weight: 600; letter-spacing: .02em; color: #86efac; }
    .v { font: 700 18px/1.2 ui-sans-serif, system-ui, sans-serif; font-variant-numeric: tabular-nums; margin: 2px 0; }
    .t { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
    .r { color: #c6e9d2; margin-top: 2px; }
    .x { all: unset; position: absolute; top: 6px; right: 8px; cursor: pointer; padding: 2px 6px; border-radius: 6px; color: #c6e9d2; }
    .x:hover, .x:focus-visible { background: rgba(255,255,255,.12); }
    @media (prefers-reduced-motion: no-preference) { .box { animation: in 180ms cubic-bezier(.23,1,.32,1); } }
    @keyframes in { from { opacity: 0; transform: translateY(-4px); } }`;

  function renderBest(vid) {
    const v = vid && verdict(vid);
    if (!v) {
      if (bestBox.host) bestBox.host.hidden = true;
      return;
    }
    if (!bestBox.host) {
      const host = document.createElement("div");
      host.id = "flipfinder-best";
      const shadow = host.attachShadow({ mode: "open" });
      shadow.innerHTML = `<style>${BEST_CSS}</style><div class="box" role="button" tabindex="0" aria-live="polite"><div class="k"></div><div class="v"></div><div class="t"></div><div class="r"></div></div><button class="x" type="button" aria-label="Chiudi">×</button>`;
      const go = () => {
        const card = cards.get(bestBox.vid);
        if (card && card.root.isConnected) card.root.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
      };
      shadow.querySelector(".box").addEventListener("click", go);
      shadow.querySelector(".box").addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), go()));
      shadow.querySelector(".x").addEventListener("click", (e) => {
        e.stopPropagation();
        host.remove();
        bestBox.host = null;
        bestBox.closed = true;
      });
      bestBox.host = host;
    }
    if (bestBox.closed) return;
    if (!bestBox.host.isConnected) document.documentElement.appendChild(bestBox.host);
    bestBox.host.hidden = false;
    const sh = bestBox.host.shadowRoot;
    sh.querySelector(".k").textContent = v.source === "local" ? "★ Migliore della pagina · stima rapida" : "★ Migliore della pagina";
    sh.querySelector(".v").textContent = `${K.eur(v.risk_adjusted_profit, true)} profitto atteso`;
    const card = cards.get(vid);
    sh.querySelector(".t").textContent = v.title || (card && quickTitle(card)) || "";
    sh.querySelector(".r").textContent = v.reason || "";
  }

  function quickTitle(card) {
    const link = card.root.matches(C.selectors.item_link) ? card.root : card.root.querySelector(C.selectors.item_link);
    return link ? (link.getAttribute("title") || "").split(",")[0] : "";
  }

  function register(link) {
    const vid = linkId(link);
    if (!vid) return;
    if (pageType === "item" && vid === P.itemId(location.href, C)) return; // the item itself
    const root = cardRoot(link, vid);
    if (registered.has(root)) return;
    registered.add(root);
    const prev = cards.get(vid);
    if (prev && prev.root.isConnected && prev.root !== root) return; // promoted + organic: first one wins
    root.setAttribute("data-ff-vid", vid);
    cards.set(vid, { root, captured: false, badge: null });
    if (evals.has(vid)) drawBadge(vid, verdict(vid));
    io.observe(root);
  }

  const addedRoots = new Set();
  function scanAdded() {
    if (!paired || !opts.enabled) return addedRoots.clear();
    mark("scan-start");
    const roots = addedRoots.size ? [...addedRoots] : [document.body];
    addedRoots.clear();
    const known = [];
    for (const r of roots) {
      if (!r.isConnected) continue;
      if (r.matches && r.matches(C.selectors.item_link)) register(r);
      r.querySelectorAll?.(C.selectors.item_link).forEach(register);
    }
    quickPass();
    for (const [vid, card] of cards) if (!card.badge && card.root.isConnected) known.push(vid);
    if (known.length && paired) {
      send({ type: "ff:get-evals", vids: known.slice(0, 300) }).then((r) => {
        for (const ev of (r && r.evals) || []) onEval(ev);
      });
    }
  }
  const scanSoon = debounce(scanAdded, 250);

  const mo = new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const n of m.addedNodes) if (n.nodeType === 1 && n.tagName !== "FF-BADGE" && !(n.id || "").startsWith("flipfinder")) addedRoots.add(n);
    }
    if (location.href !== lastHref) onNavigate();
    if (addedRoots.size) scanSoon();
    if (pageType === "item" && itemCapture.waiting) itemCapture.retrySoon();
  });

  // ------------------------------------------------------------------ badges
  const BADGE_CSS = `
    :host { all: initial; position: absolute; top: 6px; left: 6px; z-index: 5; }
    .b { all: unset; box-sizing: border-box; cursor: pointer; display: inline-flex; align-items: center; gap: 5px;
      padding: 4px 8px; border-radius: 999px; font: 600 12px/1.1 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      color: #fff; background: rgba(20, 20, 21, 0.86); box-shadow: 0 2px 8px rgba(0,0,0,.25); font-variant-numeric: tabular-nums;
      backdrop-filter: blur(4px); transition: transform 140ms cubic-bezier(.23,1,.32,1); }
    .b:active { transform: scale(.96); }
    .b:focus-visible { outline: 2px solid #4b8fea; outline-offset: 2px; }
    .b.good { background: #157f3b; } .b.bad { background: #b42318; } .b.muted { background: rgba(80,80,80,.85); font-weight: 500; }
    .b.hot { box-shadow: 0 0 0 2px #fff, 0 0 0 4px #16a34a, 0 4px 14px rgba(22,163,74,.5); }
    .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; opacity: .7; animation: p 1s ease-in-out infinite; }
    @keyframes p { 50% { opacity: .2; } }
    svg { width: 12px; height: 12px; }
    .menu { position: absolute; top: calc(100% + 6px); left: 0; width: 240px; padding: 10px; border-radius: 12px;
      background: #141415; color: #f5f5f3; box-shadow: 0 12px 32px -8px rgba(0,0,0,.55); font: 400 12px/1.4 ui-sans-serif, system-ui, sans-serif; }
    .menu[hidden] { display: none; }
    .menu p { margin: 0 0 6px; color: #bdbcb4; }
    .menu dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; margin: 0 0 8px; }
    .menu dt { color: #85847e; } .menu dd { margin: 0; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }
    .menu button { all: unset; box-sizing: border-box; cursor: pointer; display: block; width: 100%; padding: 7px 8px; border-radius: 8px; color: #f5f5f3; }
    .menu button:hover, .menu button:focus-visible { background: rgba(255,255,255,.1); }
    .menu button[disabled] { opacity: .5; cursor: default; }
    @media (prefers-reduced-motion: reduce) { .b { transition: none; } .dot { animation: none; } }`;

  let sharedSheet;
  function badgeSheet() {
    if (sharedSheet === undefined) {
      try {
        sharedSheet = new CSSStyleSheet();
        sharedSheet.replaceSync(BADGE_CSS);
      } catch {
        sharedSheet = null;
      }
    }
    return sharedSheet;
  }

  const EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';

  function badgeLabel(ev) {
    if (!ev) return { cls: "muted", html: '<span class="dot"></span>', aria: "FlipFinder: analisi in corso" };
    if (ev.unreadable) return { cls: "muted", html: "n/d", aria: "FlipFinder: scheda non leggibile" };
    if (ev.source === "local") {
      if (ev.status && ev.status !== "active") return { cls: "muted", html: { sold: "venduto", reserved: "riservato" }[ev.status] || ev.status, aria: "FlipFinder: non in vendita" };
      if (ev.insufficient) return { cls: "muted", html: "dati insuff.", aria: `FlipFinder: dati insufficienti (${ev.insufficient})` };
      const v = ev.risk_adjusted_profit ?? ev.net_margin;
      return {
        cls: v > 0 && ev.net_margin >= opts.minMargin ? "good" : v < 0 ? "bad" : "",
        html: `≈ ${K.eur(v, true)}`,
        aria: `FlipFinder, stima rapida: profitto atteso ${K.eur(v, true)}, margine ${K.eur(ev.net_margin, true)}`,
      };
    }
    const eye = ev.tracked ? EYE : "";
    if (ev.status && ev.status !== "active") {
      const label = { sold: "venduto", reserved: "riservato", removed: "rimosso" }[ev.status] || ev.status;
      return { cls: "muted", html: `${eye}${label}`, aria: `FlipFinder: ${label}` };
    }
    if (ev.flip_score === null || ev.flip_score === undefined) {
      return { cls: "muted", html: `${eye}dati insuff.`, aria: "FlipFinder: dati insufficienti per una stima" };
    }
    const margin = ev.net_margin;
    const expected = ev.risk_adjusted_profit ?? margin;
    const good = ev.flip_score >= opts.minScore && (margin ?? -Infinity) >= opts.minMargin;
    const cls = good ? "good" : margin !== null && margin < 0 ? "bad" : "";
    return {
      cls,
      html: `${eye}${ev.flip_score} · ${K.eur(expected, true)}`,
      aria: `FlipFinder: score ${ev.flip_score}, profitto atteso ${K.eur(expected, true)}, margine netto ${K.eur(margin, true)}`,
    };
  }

  function drawBadge(vid, ev) {
    const card = cards.get(vid);
    if (!card || !card.root.isConnected || !opts.badges || !paired) return;
    if (!card.badge) {
      const host = document.createElement("ff-badge");
      const shadow = host.attachShadow({ mode: "open" });
      // One stylesheet shared by every badge: ~100 badges appear at once on a search page.
      const css = badgeSheet();
      if (css) shadow.adoptedStyleSheets = [css];
      shadow.innerHTML = `${css ? "" : `<style>${BADGE_CSS}</style>`}<button class="b" type="button" aria-haspopup="true" aria-expanded="false"></button><div class="menu" role="menu" hidden></div>`;
      const stop = (e) => {
        e.stopPropagation();
        e.preventDefault();
      };
      // The badge sits on the card: its clicks must not reach the card (no navigation, no
      // Vinted handlers). Bubble phase, so the badge's own buttons still get them first.
      for (const type of ["click", "mousedown", "pointerdown", "mouseup", "pointerup", "touchstart"]) host.addEventListener(type, (e) => e.stopPropagation());
      shadow.querySelector(".b").addEventListener("click", (e) => {
        stop(e);
        toggleMenu(vid);
      });
      shadow.querySelector(".menu").addEventListener("click", (e) => {
        stop(e);
        const btn = e.target.closest("button[data-act]");
        if (btn && !btn.disabled) quickAction(vid, btn.getAttribute("data-act"));
      });
      if (getComputedStyle(card.root).position === "static") card.root.style.position = "relative";
      card.root.appendChild(host);
      card.badge = host;
    }
    const { cls, html, aria } = badgeLabel(ev);
    const b = card.badge.shadowRoot.querySelector(".b");
    b.className = `b ${cls}${card.hot ? " hot" : ""}`;
    b.innerHTML = html;
    b.setAttribute("aria-label", aria);
    const menu = card.badge.shadowRoot.querySelector(".menu");
    if (!menu.hidden) renderMenu(vid, menu);
  }

  function renderMenu(vid, menu) {
    const ev = verdict(vid);
    const ds = deepState.get(vid);
    const scoredEv = ev && !ev.insufficient && ((ev.source === "local" && ev.net_margin !== undefined) || (ev.flip_score !== null && ev.flip_score !== undefined));
    const p = (v) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
    const rows = scoredEv
      ? `<dl><dt>Costo totale</dt><dd>${K.eur(ev.total_cost)}</dd><dt>Rivendita</dt><dd>${K.eur(ev.resale_expected)}</dd><dt>Margine netto</dt><dd>${K.eur(ev.net_margin, true)}</dd><dt>Vendita in 30 gg</dt><dd>${p(ev.sale_probability)}</dd><dt>Autentico</dt><dd>${p(ev.authenticity_probability)}</dd><dt>Profitto atteso</dt><dd>${K.eur(ev.risk_adjusted_profit, true)}</dd><dt>Confidenza</dt><dd>${ev.confidence ?? "—"}/100</dd></dl>`
      : "";
    const reason = ev ? ev.reason || ev.insufficient || "" : "Analisi in corso…";
    const depth = ev && ev.source === "local" ? "Stima rapida dal riepilogo di mercato: analisi completa in arrivo" : ev && ev.analysis_depth === "full" ? "Analizzato a fondo" : ev ? "Visto in scorrimento (dati della scheda)" : "";
    menu.innerHTML = `${rows}<p></p><p class="depth"></p>
      <button type="button" role="menuitem" data-act="track">${ev && ev.tracked ? "Smetti di tracciare" : "Traccia"}</button>
      <button type="button" role="menuitem" data-act="deep" ${ds === "reading" ? "disabled" : ""}>${ds === "reading" ? "Lettura in corso…" : ds === "paused" ? "Letture in pausa (riprova più tardi)" : "Analisi approfondita"}</button>
      <button type="button" role="menuitem" data-act="open">Apri nella pagina di tracking</button>`;
    const ps = menu.querySelectorAll("p");
    ps[0].textContent = reason; // never HTML from the network
    ps[1].textContent = depth + (typeof ds === "string" && !["reading", "paused", "ok"].includes(ds) ? ` · ${ds}` : "");
  }

  function toggleMenu(vid) {
    const card = cards.get(vid);
    if (!card || !card.badge) return;
    const menu = card.badge.shadowRoot.querySelector(".menu");
    const open = menu.hidden;
    for (const other of cards.values()) {
      const m = other.badge?.shadowRoot.querySelector(".menu");
      if (m && !m.hidden) {
        m.hidden = true;
        other.badge.shadowRoot.querySelector(".b").setAttribute("aria-expanded", "false");
      }
    }
    if (open) {
      renderMenu(vid, menu);
      menu.hidden = false;
      card.badge.shadowRoot.querySelector(".b").setAttribute("aria-expanded", "true");
      menu.querySelector("button")?.focus();
    }
  }

  document.addEventListener("click", (e) => {
    if (e.target && e.target.tagName === "FF-BADGE") return;
    for (const card of cards.values()) {
      const m = card.badge?.shadowRoot.querySelector(".menu");
      if (m && !m.hidden) m.hidden = true;
    }
  });

  function itemUrl(vid) {
    const ev = evals.get(vid);
    if (ev && ev.url) return ev.url;
    const card = cards.get(vid);
    const link = card && (card.root.matches(C.selectors.item_link) ? card.root : card.root.querySelector(C.selectors.item_link));
    return link ? P.cleanItemUrl(link.getAttribute("href") || "", location.href) : `${location.origin}/items/${vid}`;
  }

  /** Quick actions: only on your click. */
  async function quickAction(vid, act) {
    if (act === "open") return send({ type: "ff:open", path: `/items/${vid}` });
    if (act === "track") {
      const ev = evals.get(vid);
      const r = await send({ type: "ff:track", url: itemUrl(vid), track: !(ev && ev.tracked) });
      if (r && r.evaluation) onEval(r.evaluation);
      else if (r && r.error) note(vid, r.error);
      return;
    }
    if (act === "deep") {
      const r = await send({ type: "ff:deep", vid, url: itemUrl(vid) });
      if (r && r.error) note(vid, r.error);
      else deepState.set(vid, r && r.pacing && r.pacing.pausedUntil > Date.now() ? "paused" : "reading");
      drawBadge(vid, verdict(vid));
    }
  }

  function note(vid, message) {
    deepState.set(vid, message);
    drawBadge(vid, verdict(vid));
  }

  function onEval(ev) {
    if (!ev || !ev.vinted_id) return;
    evals.set(ev.vinted_id, ev);
    drawBadge(ev.vinted_id, ev);
    bestSoon();
    if (pageType === "item" && ev.vinted_id === P.itemId(location.href, C)) itemBox.render();
  }

  function flash(vid, color) {
    const card = cards.get(vid);
    if (!card || !card.root.isConnected) return false;
    card.root.style.outline = `3px solid ${color}`;
    card.root.style.outlineOffset = "2px";
    card.root.style.borderRadius = card.root.style.borderRadius || "8px";
    return true;
  }

  // ------------------------------------------------------------------ item page
  const itemCapture = {
    vid: null,
    sent: false,
    waiting: false,
    tries: 0,
    timer: 0,
    start() {
      this.vid = P.itemId(location.href, C);
      this.sent = false;
      this.tries = 0;
      this.waiting = true;
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.attempt(), 500);
    },
    retrySoon: debounce(() => itemCapture.attempt(), 400),
    async attempt() {
      if (!this.waiting || this.sent || pageType !== "item" || !paired) return;
      this.tries += 1;
      const parsed = P.parseItem(collectDocument(scriptsAreFresh()), location.href, Date.now(), C, { useScripts: scriptsAreFresh() });
      if (!parsed.complete) {
        if (this.tries < 12) {
          clearTimeout(this.timer);
          this.timer = setTimeout(() => this.attempt(), 700);
        } else {
          this.waiting = false;
          itemBox.render(`Non riesco a leggere ${parsed.missing.join(" e ") || "l'annuncio"} in questa pagina. Ricaricala; se succede ancora, la configurazione del parser va aggiornata.`);
        }
        return;
      }
      this.waiting = false;
      const payload = await P.itemPayload(parsed, C);
      if (!payload) {
        itemBox.render(parsed.currency !== "EUR" ? "Valuta non supportata: FlipFinder analizza solo prezzi in euro." : "Dati dell'annuncio non validi.");
        return;
      }
      if (!opts.captureItems) return;
      this.sent = true;
      itemBox.render();
      send({ type: "ff:item", vid: this.vid, payload, pageUrl: location.href });
    },
  };

  const BOX_CSS = `
    :host { all: initial; }
    .box { position: fixed; right: 18px; bottom: 18px; z-index: 2147483646; width: 300px; max-width: calc(100vw - 36px);
      border-radius: 16px; background: #141415; color: #f5f5f3; box-shadow: 0 18px 44px -14px rgba(0,0,0,.6);
      font: 400 13px/1.4 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; overflow: hidden;
      opacity: 1; transform: translateY(0); transition: opacity 180ms ease, transform 220ms cubic-bezier(.23,1,.32,1); }
    @starting-style { .box { opacity: 0; transform: translateY(10px); } }
    .head { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,.08); }
    .logo { width: 22px; height: 22px; border-radius: 7px; display: grid; place-items: center; background: linear-gradient(135deg,#e5532a,#f59e0b); }
    .logo svg { width: 13px; height: 13px; }
    .title { flex: 1; font-weight: 650; }
    .x { all: unset; cursor: pointer; padding: 2px 6px; border-radius: 6px; color: #bdbcb4; }
    .x:hover, .x:focus-visible { background: rgba(255,255,255,.1); color: #fff; }
    .body { padding: 12px; }
    .score { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
    .ring { flex: none; width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center; font: 700 16px/1 inherit; font-variant-numeric: tabular-nums;
      background: conic-gradient(var(--c) calc(var(--v) * 1%), rgba(255,255,255,.12) 0); position: relative; }
    .ring::after { content: ""; position: absolute; inset: 4px; border-radius: 50%; background: #141415; }
    .ring span { position: relative; z-index: 1; }
    .insuff { flex: none; padding: 6px 8px; border-radius: 10px; background: rgba(217,119,6,.18); color: #f5b45a; font-weight: 600; font-size: 12px; }
    .reason { color: #bdbcb4; }
    dl { display: grid; grid-template-columns: 1fr auto; gap: 3px 10px; margin: 8px 0; }
    dt { color: #85847e; } dd { margin: 0; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }
    .pos { color: #4ade80; } .neg { color: #f87171; }
    .acts { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
    .acts button { all: unset; box-sizing: border-box; cursor: pointer; flex: 1 1 auto; text-align: center; padding: 8px 10px; border-radius: 10px; background: rgba(255,255,255,.1); font-weight: 600; font-size: 12px; }
    .acts button.primary { background: linear-gradient(135deg,#e5532a,#f59e0b); color: #fff; }
    .acts button:hover, .acts button:focus-visible { filter: brightness(1.15); outline: none; }
    .muted { color: #85847e; font-size: 11px; margin-top: 8px; }
    @media (prefers-reduced-motion: reduce) { .box { transition: none; } }`;

  const LOGO = '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>';

  const itemBox = {
    host: null,
    closedFor: null,
    mount() {
      if (this.host) return;
      this.host = document.createElement("div");
      this.host.id = "flipfinder-item-box";
      const shadow = this.host.attachShadow({ mode: "open" });
      shadow.innerHTML = `<style>${BOX_CSS}</style><section class="box" role="complementary" aria-label="FlipFinder"><div class="head"><span class="logo">${LOGO}</span><span class="title">FlipFinder</span><button class="x" type="button" aria-label="Chiudi">✕</button></div><div class="body" aria-live="polite"></div></section>`;
      shadow.querySelector(".x").addEventListener("click", () => {
        this.closedFor = P.itemId(location.href, C);
        this.unmount();
      });
      shadow.querySelector(".body").addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-act]");
        if (btn) this.action(btn.getAttribute("data-act"));
      });
      document.documentElement.appendChild(this.host);
    },
    unmount() {
      this.host?.remove();
      this.host = null;
    },
    async action(act) {
      const vid = P.itemId(location.href, C);
      if (act === "pair") return send({ type: "ff:open-options" });
      if (act === "open") return send({ type: "ff:open", path: `/items/${vid}` });
      if (act === "full") {
        const ev = evals.get(vid);
        return send({ type: "ff:open", path: ev && ev.opportunity_id ? `/deals/${ev.opportunity_id}` : `/items/${vid}` });
      }
      if (act === "track") {
        const ev = evals.get(vid);
        const r = await send({ type: "ff:track", url: location.href.split(/[?#]/)[0], track: !(ev && ev.tracked) });
        if (r && r.evaluation) onEval(r.evaluation);
        else if (r && r.error) this.render(r.error);
      }
    },
    render(message) {
      if (pageType !== "item" || !opts.enabled) return this.unmount();
      const vid = P.itemId(location.href, C);
      if (this.closedFor === vid) return;
      this.mount();
      const body = this.host.shadowRoot.querySelector(".body");
      if (!paired) {
        body.innerHTML = `<p class="reason">Associa l'estensione a FlipFinder per vedere margine, score e rischio di ogni annuncio mentre navighi.</p><div class="acts"><button class="primary" type="button" data-act="pair">Associa</button></div>`;
        return;
      }
      const ev = evals.get(vid);
      if (message) {
        body.innerHTML = `<p class="reason"></p>`;
        body.querySelector(".reason").textContent = message;
        return;
      }
      if (!ev || ev.analysis_depth !== "full") {
        body.innerHTML = `<div class="score"><span class="insuff">…</span><span class="reason">Analisi dell'annuncio in corso…</span></div>`;
        return;
      }
      const scoredItem = ev.flip_score !== null && ev.flip_score !== undefined;
      const color = !scoredItem ? "#85847e" : ev.flip_score >= opts.minScore ? "#22c55e" : ev.flip_score >= 50 ? "#f59e0b" : "#ef4444";
      const status = ev.status !== "active" ? `<p class="muted">Stato: ${{ sold: "venduto", reserved: "riservato", removed: "rimosso" }[ev.status] || ev.status}</p>` : "";
      body.innerHTML = `
        <div class="score">${scoredItem ? `<span class="ring" style="--v:${ev.flip_score};--c:${color}"><span>${ev.flip_score}</span></span>` : '<span class="insuff">Dati insufficienti</span>'}<span class="reason"></span></div>
        ${scoredItem ? `<dl><dt>Costo totale</dt><dd>${K.eur(ev.total_cost)}</dd><dt>Rivendita stimata</dt><dd>${K.eur(ev.resale_expected)}</dd><dt>Margine netto</dt><dd class="${(ev.net_margin ?? 0) >= 0 ? "pos" : "neg"}">${K.eur(ev.net_margin, true)}</dd><dt>ROI</dt><dd>${ev.roi === null ? "—" : Math.round(ev.roi * 100) + "%"}</dd><dt>Confidenza</dt><dd>${ev.confidence ?? "—"}/100</dd></dl>` : `<dl><dt>Costo totale</dt><dd>${K.eur(ev.total_cost)}</dd></dl>`}
        ${status}
        <div class="acts"><button class="primary" type="button" data-act="track">${ev.tracked ? "Tracciato ✓" : "Traccia"}</button><button type="button" data-act="open">Tracking</button><button type="button" data-act="full">Analisi</button></div>
        <p class="muted">Analizzato a fondo · le azioni partono solo su tuo clic.</p>`;
      body.querySelector(".reason").textContent = ev.reason || "";
    },
  };

  // ------------------------------------------------------------------ deep reads (on request of the service worker)
  /** Reads one item page, without cookies, for a deep analysis you asked for (or a slow,
   * opt-in automatic one). A refusal is reported, never worked around. */
  async function readPage(url, vid) {
    let target;
    let rewritten = false;
    try {
      const u = new URL(url);
      if (!P.isVintedUrl(u.href, C) || P.itemId(u.pathname, C) !== String(vid)) return { outcome: "error", message: "Indirizzo non valido." };
      target = u.host === location.host ? u.origin + u.pathname : location.origin + u.pathname;
      rewritten = u.host !== location.host;
    } catch {
      return { outcome: "error", message: "Indirizzo non valido." };
    }
    let res;
    try {
      res = await fetch(target, { credentials: "omit", redirect: "follow", headers: { Accept: "text/html" } });
    } catch {
      return { outcome: "error", message: "Vinted non raggiungibile." };
    }
    const body = await res.text().catch(() => "");
    if (K.isRefusal(res.status, body)) return { outcome: "blocked", status: res.status, message: `Vinted ha rifiutato la lettura (HTTP ${res.status}).` };
    if (res.status === 404 || res.status === 410) {
      return rewritten ? { outcome: "error", status: res.status, message: "Non trovato su questo dominio di Vinted." } : { outcome: "not_found", status: res.status, message: "Annuncio non più disponibile." };
    }
    if (!res.ok) return { outcome: "error", status: res.status, message: `Risposta inattesa (HTTP ${res.status}).` };
    const parsed = P.parseItem(P.collectHtml(body.slice(0, 3e6)), target, Date.now(), C);
    if (!parsed.complete) {
      if (parsed.status === "removed") return { outcome: "not_found", status: 200, message: "Annuncio rimosso dal venditore." };
      return { outcome: "error", status: 200, message: `Pagina letta ma senza ${parsed.missing.join(" e ")}: va aggiornata la configurazione del parser.` };
    }
    const payload = await P.itemPayload({ ...parsed, url: url.split(/[?#]/)[0] }, C);
    return payload ? { outcome: "ok", status: 200, payload } : { outcome: "error", status: 200, message: "Valuta non supportata o dati non validi." };
  }

  // ------------------------------------------------------------------ navigation
  function onNavigate() {
    lastHref = location.href;
    pageType = P.pageType(location.pathname, C);
    send({ type: "ff:hello", pageType, url: location.href });
    if (pageType === "item") {
      itemBox.closedFor = null;
      itemBox.render();
      itemCapture.start();
    } else {
      itemCapture.waiting = false;
      itemBox.unmount();
    }
    addedRoots.clear();
    bestBox.closed = false;
    bestBox.vid = null;
    updateBest();
    if (pageType === "item" && bestBox.host) bestBox.host.hidden = true;
    scanSoon();
  }

  // ------------------------------------------------------------------ messages
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || typeof msg.type !== "string") return false;
    switch (msg.type) {
      case "ff:evals":
        for (const ev of msg.evals || []) {
          onEval(ev);
          if (msg.deep && msg.deep.vid === ev.vinted_id && msg.deep.mode !== "extension_item") deepState.set(ev.vinted_id, "ok");
        }
        return false;
      case "ff:hot":
        for (const vid of msg.vids || []) {
          const card = cards.get(vid);
          if (!card) continue;
          card.hot = true;
          drawBadge(vid, verdict(vid));
        }
        return false;
      case "ff:deep-status":
        deepState.set(msg.vid, msg.state === "reading" || msg.state === "paused" ? msg.state : msg.message || msg.state);
        drawBadge(msg.vid, verdict(msg.vid));
        return false;
      case "ff:read-page":
        readPage(msg.url, msg.vid).then(sendResponse);
        return true;
      case "ff:scroll-to": {
        const card = cards.get(msg.vid);
        if (!card || !card.root.isConnected) {
          sendResponse({ ok: false });
          return false;
        }
        card.root.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
        flash(msg.vid, "#2a78d6");
        setTimeout(() => {
          if (!card.best) card.root.style.outline = "";
        }, 2200);
        sendResponse({ ok: true });
        return false;
      }
      case "ff:highlight": {
        for (const [vid, card] of cards) {
          if (card.best && vid !== msg.vid) {
            card.best = false;
            card.root.style.outline = "";
          }
        }
        const card = msg.vid ? cards.get(msg.vid) : null;
        if (card) {
          card.best = true;
          flash(msg.vid, "#16a34a");
        }
        sendResponse({ ok: Boolean(card) });
        return false;
      }
      case "flipfinder:prepare":
        prepareLink().then(sendResponse);
        return true;
      default:
        return false;
    }
  });

  /** Without pairing: open the page's data in FlipFinder through the URL fragment. */
  async function prepareLink() {
    const { options } = await chrome.storage.sync.get("options");
    const appUrl = K.normalizeOptions(options).appUrl;
    if (pageType === "item") {
      const parsed = P.parseItem(collectDocument(scriptsAreFresh()), location.href, Date.now(), C, { useScripts: scriptsAreFresh() });
      if (!parsed.title && !parsed.price) return { error: "Non riesco a leggere questo annuncio. Ricarica la pagina e riprova." };
      return {
        url: P.analyzeUrl(appUrl, {
          url: parsed.url,
          title: parsed.title,
          price: parsed.price,
          currency: parsed.currency,
          brand: parsed.brand || "",
          size: parsed.size || "",
          condition: parsed.condition || "",
          condition_label: parsed.condition_label || "",
          color: parsed.color || "",
          description: parsed.description,
          image_urls: parsed.images,
        }),
      };
    }
    const raw = [];
    const done = new Set();
    for (const link of document.querySelectorAll(C.selectors.item_link)) {
      const id = linkId(link);
      if (!id || done.has(id)) continue;
      done.add(id);
      raw.push(collectCard(cardRoot(link, id), link));
    }
    const { items } = P.parseCards(raw, location.href, C);
    if (!items.length) return { error: "Nessun articolo leggibile qui. Apri una ricerca di Vinted o un annuncio e riprova." };
    const max = C.limits.max_batch || 200;
    let query = "";
    try {
      query = new URL(location.href).searchParams.get("search_text") || "";
    } catch {
      /* ignore */
    }
    return { url: await P.importUrl(appUrl, items.slice(0, max), query), count: Math.min(items.length, max) };
  }

  // ------------------------------------------------------------------ start
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "sync" && changes.options) {
      opts = K.normalizeOptions(changes.options.newValue);
      for (const [vid] of cards) drawBadge(vid, verdict(vid));
    }
    if (area === "local" && changes.parserConfig) useConfig(changes.parserConfig.newValue);
    if (area === "local" && changes.marketCache) {
      market = changes.marketCache.newValue ? Q.compileMarket(changes.marketCache.newValue) : null;
      rescore();
    }
    if (area === "local" && changes.paired) {
      paired = Boolean(changes.paired.newValue);
      if (pageType === "item") {
        itemBox.render();
        if (paired) itemCapture.start();
      }
      if (paired) scanAdded();
    }
  });

  const domReady = () =>
    document.readyState === "loading" ? new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve, { once: true })) : Promise.resolve();

  async function start() {
    mark("start");
    // Straight from storage (not through the service worker): options, pairing flag (never the
    // key), parser configuration and market summary.
    const [store, synced] = await Promise.all([
      chrome.storage.local.get(["paired", "parserConfig", "marketCache"]).catch(() => ({})),
      chrome.storage.sync.get("options").catch(() => ({})),
    ]);
    mark("hello");
    opts = K.normalizeOptions(synced.options);
    paired = Boolean(store.paired);
    useConfig(store.parserConfig);
    market = store.marketCache ? Q.compileMarket(store.marketCache) : null;
    pageType = P.pageType(location.pathname, C);
    send({ type: "ff:hello", pageType, url: location.href }); // live panel bookkeeping
    if (!opts.enabled) return;
    // Injected at the start of the navigation: settings are read while the page loads, the
    // cards are scored as soon as the document is parsed.
    await domReady();
    mark("dom");
    mo.observe(document.body, { childList: true, subtree: true });
    if (pageType === "item") {
      itemBox.render();
      if (paired) itemCapture.start();
    }
    if (paired) scanAdded();
  }

  start();
})();
