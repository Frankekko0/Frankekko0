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
  const Cards = globalThis.FlipFinderCards;
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
  // This page's progress (per search; what was sent is never sent again): what went to FlipFinder, what came back, and
  // the timings exposed on <html> for measurement (data-ff-* attributes, ff:* marks).
  const page = {
    firstSent: false, // the best cards went out (the first, small capture request)
    sent: new Set(), // vids sent for the full analysis
    statsAsked: new Set(), // vids sent for the page statistics
    stats: new Map(), // vid -> page statistics
    visible: new Set(), // vids at least half on screen
    order: 0, // cards registered, in page order
    timing: { read_ms: 0, scan_ms: 0, cards: 0, requests: [] },
  };
  const html = document.documentElement;
  /** What identifies a list of results: the path and the search text and filters (not paging tokens). */
  function searchOf(href) {
    try {
      const u = new URL(href);
      const keep = [...u.searchParams].filter(([k]) => !/^(time|search_id|search_session_id|referrer)$/.test(k)).sort();
      return `${u.pathname}?${new URLSearchParams(keep)}`;
    } catch {
      return href;
    }
  }
  page.search = searchOf(location.href);
  const markOnce = (name) => {
    if (html.hasAttribute(`data-ff-${name}-ms`)) return;
    mark(name);
    html.setAttribute(`data-ff-${name}-ms`, String(Math.round(performance.now())));
  };
  function addTiming(t) {
    if (!t) return;
    page.timing.requests.push({ ...t, at: Math.round(performance.now()) });
    if (page.timing.requests.length > 40) page.timing.requests.shift();
    html.setAttribute("data-ff-timing", JSON.stringify(page.timing));
  }

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
    collectItemElements(c);
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

  /**
   * The item's favourite button, total price and status badges on the live page, read by
   * data-testid exactly as the server reads them from the HTML (item_dom in the configuration):
   * never inside another item's card or the site header/nav/footer. The status badges are the
   * item's own status element and the short texts right before the item's summary in its
   * sidebar ("Venduto", "Riservato"); a "Venduto" elsewhere (a title, a suggested item) is no sale.
   */
  function collectItemElements(c) {
    const dom = P.itemDomConfig(C);
    const anyOf = (ids) => (ids.length ? [...document.querySelectorAll(ids.map((id) => `[data-testid="${CSS.escape(id)}"]`).join(", "))] : []);
    const exclude = ["header", "nav", "footer", ...dom.exclude_testid_prefixes.map((p) => `[data-testid^="${CSS.escape(p)}"]`)].join(", ");
    const own = (el) => !el.closest(exclude);
    const max = Number(dom.badge_max_length);
    const short = (t) => Boolean(t) && [...t].length <= max;
    for (const el of anyOf(dom.favourite_testids).filter(own)) {
      const label = el.getAttribute("aria-label") || "";
      const value = /\d/.test(label) ? label : /\d/.test(text(el)) ? text(el) : null;
      if (value !== null) {
        c.favourites = value;
        break;
      }
    }
    const total = anyOf(dom.total_price_testids).find((el) => own(el) && text(el));
    if (total) c.totalPrice = text(total);
    for (const el of anyOf(dom.status_testids).filter(own)) if (short(text(el))) c.statusTexts.push(text(el));
    const summary = anyOf(dom.summary_testids).find(own);
    const zoneSel = [...dom.zone_tags, ...dom.zone_id_prefixes.map((p) => `[id^="${CSS.escape(p)}"]`)].join(", ");
    const zone = summary && zoneSel && summary.parentElement ? summary.parentElement.closest(zoneSel) : null;
    if (!zone) return;
    const badges = [];
    const walker = document.createTreeWalker(zone, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement && n.parentElement.closest("script, style, noscript, template, svg") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (summary.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING) break; // the summary and after
      const t = (node.textContent || "").replace(/\s+/g, " ").trim();
      if (short(t) && node.parentElement && own(node.parentElement)) badges.push(t);
    }
    c.statusTexts.push(...badges.slice(-3));
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

  // Card reading is shared with the scanner (src/cards.js).
  const cardRoot = (link, id) => Cards.cardRoot(link, id, C);
  const collectCard = (root, link) => Cards.collectCard(root, link, C);

  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const vid0 = entry.target.getAttribute("data-ff-vid");
        if (vid0) entry.isIntersecting ? page.visible.add(vid0) : page.visible.delete(vid0);
        if (!entry.isIntersecting) continue;
        const vid = vid0;
        const card = vid && cards.get(vid);
        if (!card || card.captured) continue;
        card.captured = true;
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
    if (!opts.captureCards || page.sent.has(vid)) return;
    pendingCards.set(vid, payload);
    if (!verdict(vid)) drawBadge(vid, null);
    card.quicked = false; // read late (lazy content): instant verdict now
    quickSoon();
    flushCards();
  }

  const quickSoon = debounce(() => quickPass(), 80);

  // ------------------------------------------------------------------ full analysis, best first
  // The value a card is sent for: its instant verdict (risk-adjusted profit), unknown ones last.
  function valueOf(vid) {
    const v = verdict(vid);
    return v && !v.insufficient && typeof v.risk_adjusted_profit === "number" ? v.risk_adjusted_profit : -1e9;
  }
  const onScreen = (vid) => page.visible.has(vid) || (!page.visible.size && (cards.get(vid)?.order ?? 99) < 12);

  /** The first request: the best instant verdicts and the cards on screen, at most 12. */
  function firstWave(keys) {
    const vids = [...keys]; // page order (an iterator can be walked only once)
    const byValue = [...vids].sort((a, b) => valueOf(b) - valueOf(a));
    const pick = new Set(byValue.filter((v) => valueOf(v) > 0).slice(0, 6));
    for (const v of vids) if (pick.size < 12 && onScreen(v)) pick.add(v);
    for (const v of byValue) if (pick.size < 12) pick.add(v);
    return [...pick];
  }
  const restOrder = (vids) => [...vids].sort((a, b) => onScreen(b) - onScreen(a) || valueOf(b) - valueOf(a));

  function postCards(vids, first) {
    for (let i = 0; i < vids.length; i += 120) {
      const chunk = vids.slice(i, i + 120).filter((vid) => pendingCards.has(vid));
      if (!chunk.length) continue;
      const cardsOut = chunk.map((vid) => ({ vid, payload: pendingCards.get(vid) }));
      for (const vid of chunk) {
        pendingCards.delete(vid);
        page.sent.add(vid);
      }
      send({ type: "ff:cards", first, pageType: pageType === "item" ? "other" : pageType, pageUrl: location.href, cards: cardsOut }).then((r) => {
        if (r && typeof r.queued === "number") return;
        // Not delivered (the service worker was restarting): the cards go back in line, never lost.
        for (const c of cardsOut) {
          page.sent.delete(c.vid);
          if (!pendingCards.has(c.vid)) pendingCards.set(c.vid, c.payload);
        }
        flushCards();
      });
    }
  }

  /**
   * While the page is still arriving, the first request leaves as soon as two dozen cards are
   * scored (its best ones and the ones on screen); the rest goes when the document is complete,
   * best first. Cards read later (scrolling) follow in small groups.
   */
  function sendCards() {
    if (!pendingCards.size) return;
    const loading = document.readyState === "loading";
    if (!page.firstSent) {
      if (loading && pendingCards.size < 24) return;
      page.firstSent = true;
      postCards(firstWave(pendingCards.keys()), true);
    }
    if (!loading) postCards(restOrder(pendingCards.keys()), false);
  }
  const flushCards = debounce(sendCards, 300);

  // ------------------------------------------------------------------ page statistics
  // One request for the page's cards (and one per new batch when more cards load): pre-computed
  // statistics per model / size / condition that refine the instant verdicts.
  async function askStats() {
    if (!paired || !opts.enabled || pageType === "item") return;
    const items = [];
    for (const [vid, card] of cards) {
      if (!card.q || page.statsAsked.has(vid) || evals.has(vid)) continue;
      page.statsAsked.add(vid);
      items.push({ vinted_id: vid, title: card.q.title.slice(0, 300), brand: card.q.brand || null, size: card.q.size || null, condition: card.q.condition_label || card.q.condition || null });
      if (items.length >= 120) break;
    }
    if (!items.length) return;
    const r = await send({ type: "ff:page-stats", items });
    if (!r || !r.ok) {
      // Asked again with the next batch (the statistics are per card, not per page).
      for (const it of items) page.statsAsked.delete(it.vinted_id);
      return;
    }
    addTiming(r.timing);
    let changed = 0;
    for (const [vid, stat] of Object.entries(r.stats || {})) {
      page.stats.set(vid, stat);
      if (refine(vid)) changed += 1;
    }
    if (changed) updateBest();
    markOnce("stats");
  }

  /** The instant verdict of a card from its page statistics (true when its badge changed). */
  function refine(vid) {
    const card = cards.get(vid);
    const stat = page.stats.get(vid);
    if (!card || !card.q || !stat || evals.has(vid)) return false;
    const nv = Q.refineWithStats(card.q, stat, market);
    if (!nv) return false;
    nv.title = card.q.title;
    quick.set(vid, nv);
    if (card.root.isConnected) drawBadge(vid, nv);
    return true;
  }

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

  // Badges still to paint (instant verdicts), a few per frame: no long task while the page loads.
  const paintQueue = [];
  let painting = false;
  function paintSlice() {
    const t0 = performance.now();
    while (paintQueue.length && performance.now() - t0 < 6) {
      const vid = paintQueue.shift();
      if (!evals.has(vid) && quick.has(vid)) drawBadge(vid, quick.get(vid));
    }
    if (paintQueue.length) return requestAnimationFrame(paintSlice);
    painting = false;
    mark("badges");
    quickDone();
  }

  /** Every card of the complete document has its instant verdict painted. */
  function quickDone() {
    if (painting || paintQueue.length || document.readyState === "loading" || !page.finalPass || html.hasAttribute("data-ff-quick-ms")) return;
    mark("cards-read");
    markOnce("quick");
    html.setAttribute("data-ff-timing", JSON.stringify(page.timing));
  }

  function quickPass() {
    if (!paired || !opts.enabled) return;
    const t0 = performance.now();
    mark("parse-start");
    const batch = [];
    for (const [vid, card] of cards) {
      if (card.quicked || !card.root.isConnected) continue;
      card.quicked = true;
      const parsed = quickCard(vid, card);
      if (!parsed) continue; // read again when it scrolls into view (lazy content)
      card.q = {
        vinted_id: vid,
        title: parsed.title,
        brand: parsed.brand,
        size: parsed.size,
        condition_label: parsed.condition_label,
        price: parsed.price,
        condition: parsed.condition,
        buyer_protection_fee: parsed.buyer_protection_fee,
        status: parsed.status,
      };
      batch.push(card.q);
      // Second step, in the background: the whole card goes to FlipFinder for the full analysis.
      const payload = opts.captureCards && !page.sent.has(vid) ? P.capturePayload(parsed, C) : null;
      if (payload) {
        pendingCards.set(vid, payload);
        card.captured = true;
      }
    }
    page.timing.read_ms += performance.now() - t0;
    page.timing.cards += batch.length;
    if (!batch.length) return quickDone();
    mark("parse-end");
    // Scored right here from the stored market summary (~0.1 ms a card): no wait on the service
    // worker, which may be busy receiving full analyses. Server evaluations replace these.
    for (const c of batch) {
      const v = Q.quickEstimate(c, market);
      v.title = c.title;
      quick.set(c.vinted_id, v);
      if (page.stats.has(c.vinted_id)) refine(c.vinted_id);
    }
    // The best one first (progressively, as the page arrives), the other badges in slices.
    updateBest();
    mark("best");
    sendCards();
    for (const c of batch) if (!evals.has(c.vinted_id) && c.vinted_id !== bestBox.vid) paintQueue.push(c.vinted_id);
    page.timing.read_ms = Math.round(page.timing.read_ms * 10) / 10;
    if (!painting && paintQueue.length) {
      painting = true;
      paintSlice();
    } else quickDone();
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
      if (refine(vid)) continue;
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
    if (!html.hasAttribute("data-ff-best-ms")) html.setAttribute("data-ff-best-ms", String(Math.round(performance.now())));
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

  const seenLinks = new WeakSet();
  function register(link) {
    if (seenLinks.has(link)) return;
    seenLinks.add(link);
    const vid = linkId(link);
    if (!vid) return;
    if (pageType === "item" && vid === P.itemId(location.href, C)) return; // the item itself
    const root = cardRoot(link, vid);
    if (registered.has(root)) return;
    registered.add(root);
    const prev = cards.get(vid);
    if (prev && prev.root.isConnected && prev.root !== root) return; // promoted + organic: first one wins
    root.setAttribute("data-ff-vid", vid);
    cards.set(vid, { root, captured: page.sent.has(vid), badge: null, order: page.order++ });
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
    page.finalPass = true;
    quickPass();
    sendCards();
    askStats();
    for (const [vid, card] of cards) if (!card.asked && !evals.has(vid) && card.root.isConnected) known.push(vid);
    lookup(known);
  }
  const scanSoon = debounce(scanAdded, 250);

  /** Server verdicts already known (the extension's cache; FlipFinder only for cards not sent for analysis). */
  function lookup(vids) {
    if (!vids.length || !paired) return;
    for (const vid of vids) cards.get(vid).asked = true;
    const remote = vids.filter((vid) => !page.sent.has(vid) && !pendingCards.has(vid));
    send({ type: "ff:get-evals", vids: vids.slice(0, 300), remote }).then((r) => {
      for (const ev of (r && r.evals) || []) onEval(ev);
    });
  }

  // ------------------------------------------------------------------ while the page arrives
  // Vinted's HTML is large (several MB) and the cards come in its first part: they are read and
  // scored as soon as each one is complete (the one still arriving waits for the next look),
  // the best card is highlighted and updated progressively; the pass at the end of the document
  // reads whatever is left.
  let streamTimer = 0;
  let streamFrom = 0;
  function scanStream() {
    streamTimer = 0;
    if (!paired || !opts.enabled || document.readyState !== "loading") return;
    const t0 = performance.now();
    if (!page.streamed) {
      page.streamed = true;
      mark("scan-start");
    }
    const links = document.querySelectorAll(C.selectors.item_link);
    if (links.length <= streamFrom) return;
    const lastVid = linkId(links[links.length - 1]);
    const fresh = [];
    let i = streamFrom;
    for (; i < links.length; i += 1) {
      const vid = linkId(links[i]);
      if (vid && vid === lastVid) break; // still arriving
      const before = cards.size;
      register(links[i]);
      if (cards.size > before) fresh.push(vid);
    }
    streamFrom = i;
    page.timing.scan_ms = Math.round((page.timing.scan_ms + performance.now() - t0) * 10) / 10;
    if (!fresh.length) return;
    quickPass();
    lookupSoon(fresh);
  }
  // Called from the mutation observer, i.e. between two chunks of the parser: a timer would wait
  // behind the parser's own tasks (100-200 ms on Vinted). At most one look every 25 ms.
  let streamAt = 0;
  const streamSoon = () => {
    const now = performance.now();
    if (now - streamAt >= 25) {
      streamAt = now;
      clearTimeout(streamTimer);
      scanStream();
    } else if (!streamTimer) streamTimer = setTimeout(scanStream, 30);
  };
  let lookupWaiting = [];
  const lookupLater = debounce(() => {
    const vids = lookupWaiting.filter((vid) => cards.has(vid) && !cards.get(vid).asked && !evals.has(vid));
    lookupWaiting = [];
    lookup(vids);
  }, 150);
  function lookupSoon(vids) {
    lookupWaiting.push(...vids);
    lookupLater();
  }

  const mo = new MutationObserver((mutations) => {
    if (document.readyState === "loading") return streamSoon(); // the page is still arriving
    for (const m of mutations) {
      for (const n of m.addedNodes) if (n.nodeType === 1 && n.tagName !== "FF-BADGE" && !(n.getAttribute("id") || "").startsWith("flipfinder")) addedRoots.add(n); // the attribute: n.id is an element when a form has a field named "id"
    }
    if (location.href !== lastHref) onNavigate();
    if (onCheckoutPath()) purchaseSoon(); // the confirmation may render well after load
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
      if (ev.status && ev.status !== "active") return { cls: "muted", html: { sold: "venduto", reserved: "riservato" }[ev.status] || "non in vendita", aria: "FlipFinder: non in vendita" };
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
      const label = { sold: "venduto", reserved: "riservato", removed: "rimosso" }[ev.status] || "non in vendita";
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
    card.badge.setAttribute("data-ff-src", !ev ? "pending" : ev.unreadable ? "unreadable" : ev.source === "local" ? (ev.refined ? "stats" : "local") : "server");
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
      // Your favourite state on Vinted, as this page shows it (keeps FlipFinder aligned).
      const fav = favouriteButton();
      const value = fav && !signedOut() ? favouriteNow(fav, scriptsAreFresh() ? parsed : null) : null;
      if (value !== null) send({ type: "ff:favourite-seen", vid: this.vid, value });
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
      const status = ev.status !== "active" ? `<p class="muted">Stato: ${{ sold: "venduto", reserved: "riservato", removed: "rimosso" }[ev.status] || "non in vendita"}</p>` : "";
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
    const parsed = P.parseItem(P.collectHtml(body.slice(0, 3e6), C), target, Date.now(), C);
    if (!parsed.complete) {
      if (parsed.status === "removed") return { outcome: "not_found", status: 200, message: "Annuncio rimosso dal venditore." };
      return { outcome: "error", status: 200, message: `Pagina letta ma senza ${parsed.missing.join(" e ")}: va aggiornata la configurazione del parser.` };
    }
    const payload = await P.itemPayload({ ...parsed, url: url.split(/[?#]/)[0] }, C);
    return payload ? { outcome: "ok", status: 200, payload } : { outcome: "error", status: 200, message: "Valuta non supportata o dati non validi." };
  }

  // ------------------------------------------------------------------ actions on Vinted (your click in FlipFinder)
  // Favourite and Buy are done here, in your Vinted session, by clicking Vinted's own buttons
  // once - only when you asked from FlipFinder. Nothing is paid: the checkout waits for you.
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function actionButtons(selector) {
    const S = C.selectors;
    return [...document.querySelectorAll(selector || "")].filter((el) => !(S.action_scope_exclude && el.closest(S.action_scope_exclude)) && el.offsetParent !== null);
  }

  function favouriteButton() {
    return actionButtons(C.selectors.favourite_button)[0] || null;
  }

  /** true / false when the button says it (pressed state or label), null when it can't be read. */
  function favouriteState(btn) {
    if (!btn) return null;
    const pressed = btn.getAttribute("aria-pressed");
    if (pressed === "true" || pressed === "false") return pressed === "true";
    const label = `${btn.getAttribute("aria-label") || ""} ${btn.getAttribute("title") || ""} ${text(btn)}`;
    if (C.patterns.favourite_on && C.patterns.favourite_on.test(label)) return true;
    if (C.patterns.favourite_off && C.patterns.favourite_off.test(label)) return false;
    return null;
  }

  // Vinted's own Buy button: its exact test id, otherwise a button whose whole text is "Acquista"
  // (never a look-alike such as "Protezione acquisti" or "Fai un'offerta").
  function buyButton() {
    const rx = C.patterns.buy_text;
    const all = actionButtons(C.selectors.buy_button);
    return all.find((b) => b.getAttribute("data-testid") === "item-buy-button") || all.find((b) => rx && rx.test(text(b))) || null;
  }

  // Vinted's buttons are in the served HTML before its scripts make them work: a click before
  // that would be lost. Clicks wait for the page's load event (and a short settle), a few seconds at most.
  const pageLoaded = () =>
    Promise.race([
      document.readyState === "complete" ? Promise.resolve() : new Promise((resolve) => addEventListener("load", resolve, { once: true })),
      sleep(12000),
    ]).then(() => sleep(400));

  const signedOut = () => Boolean(C.selectors.signed_out_marker && document.querySelector(C.selectors.signed_out_marker));

  async function itemSnapshot(vid) {
    // The page may still be rendering: wait for the item data (and the buttons) a few seconds.
    // Time-bound, not count-bound: in a background tab the browser slows timers down to 1/s.
    let parsed = null;
    const until = Date.now() + 10000;
    for (;;) {
      parsed = P.parseItem(collectDocument(scriptsAreFresh()), location.href, Date.now(), C, { useScripts: scriptsAreFresh() });
      if (parsed.complete && (favouriteButton() || buyButton() || signedOut() || parsed.status !== "active")) break;
      if (Date.now() > until) break;
      await sleep(250);
    }
    const fav = favouriteButton();
    return {
      vid,
      status: parsed ? parsed.status : "unknown",
      price: parsed ? parsed.price : null,
      currency: parsed ? parsed.currency : null,
      title: parsed ? parsed.title : null,
      signedIn: !signedOut(),
      favourite: favouriteNow(fav, parsed),
      canFavourite: Boolean(fav),
      canBuy: Boolean(buyButton()),
    };
  }

  // The button first; otherwise the item's own data as the page was served (null if neither).
  function favouriteNow(btn, parsed) {
    const shown = favouriteState(btn);
    if (shown !== null) return shown;
    return parsed && typeof parsed.favourite_by_me === "boolean" ? parsed.favourite_by_me : null;
  }

  async function vintedAct(msg) {
    // Asked as soon as the tab answers: the document (and the settings) first, a few seconds at most.
    await Promise.race([Promise.all([domReady(), started]), sleep(8000)]);
    // A tab sent to a new address answers only from the new page, never from the one it is leaving.
    if (msg.after && performance.timeOrigin + 300 < msg.after) return null;
    if (pageType !== "item" || P.itemId(location.href, C) !== String(msg.vid)) return { ok: false, code: "wrong_page", message: "Pagina dell'annuncio non aperta." };
    const snap = await itemSnapshot(String(msg.vid));
    if (msg.action === "state") return { ok: true, ...snap };
    if (msg.action === "buy") {
      // Checked right before the click, on the page as it is now: never a purchase of something
      // else than what you saw in FlipFinder.
      if (snap.status !== "active") return { ok: false, code: snap.status, message: "L'articolo non è più acquistabile.", ...snap };
      const expect = msg.expect_price === undefined || msg.expect_price === null ? null : Number(msg.expect_price);
      if (expect !== null) {
        if (typeof snap.price !== "number") return { ok: false, code: "no_price", message: "Non riesco a leggere il prezzo su Vinted: nessun clic fatto.", ...snap };
        if (Math.abs(snap.price - expect) >= 0.01) return { ok: false, code: "price_changed", message: "Il prezzo è cambiato: nessun clic fatto.", ...snap, expected: expect };
      }
    }
    if (!snap.signedIn) return { ok: false, code: "signed_out", message: "Non sei collegato a Vinted in questo browser: accedi su Vinted e riprova.", ...snap };
    if (msg.action === "favourite") {
      const btn = favouriteButton();
      if (!btn) return { ok: false, code: "no_button", message: "Pulsante dei preferiti non trovato in questa pagina (configurazione da aggiornare).", ...snap };
      // Never a blind click: if the current state can't be read, a click could undo it.
      if (snap.favourite === null) return { ok: false, code: "unknown_state", message: "Non riesco a leggere se l'annuncio è già nei tuoi preferiti: nessun clic fatto (configurazione da aggiornare).", ...snap };
      if (snap.favourite === Boolean(msg.want)) return { ok: true, changed: false, ...snap };
      await pageLoaded();
      if (favouriteState(favouriteButton()) === Boolean(msg.want)) return { ok: true, changed: false, ...snap, favourite: Boolean(msg.want) };
      (favouriteButton() || btn).click(); // one click, as you would
      for (const until = Date.now() + 4000; Date.now() < until; ) {
        await sleep(200);
        if (favouriteState(favouriteButton()) === Boolean(msg.want)) return { ok: true, changed: true, ...snap, favourite: Boolean(msg.want) };
        if (signedOut()) return { ok: false, code: "signed_out", message: "Vinted chiede di accedere: accedi e riprova.", ...snap };
      }
      // Clicked once, but the button doesn't show the new state: the page is reloaded and read again.
      return { ok: false, code: "verify", clicked: true, ...snap };
    }
    if (msg.action === "buy") {
      if (!buyButton()) return { ok: false, code: "no_button", message: "Tasto Acquista non trovato in questa pagina (configurazione da aggiornare).", ...snap };
      await pageLoaded();
      // The wait can be long: still this item, still on sale, still at the expected price?
      if (pageType !== "item" || P.itemId(location.href, C) !== String(msg.vid)) return { ok: false, code: "wrong_page", message: "Pagina dell'annuncio cambiata: nessun clic fatto." };
      const now = await itemSnapshot(String(msg.vid));
      if (now.status !== "active") return { ok: false, code: now.status, message: "L'articolo non è più acquistabile.", ...now };
      const expected = msg.expect_price === undefined || msg.expect_price === null ? null : Number(msg.expect_price);
      if (expected !== null && (typeof now.price !== "number" || Math.abs(now.price - expected) >= 0.01)) {
        return { ok: false, code: typeof now.price === "number" ? "price_changed" : "no_price", message: "Il prezzo è cambiato: nessun clic fatto.", ...now, expected };
      }
      const btn = buyButton();
      if (!btn) return { ok: false, code: "no_button", message: "Tasto Acquista non trovato in questa pagina (configurazione da aggiornare).", ...now };
      btn.click(); // opens Vinted's checkout: the payment is confirmed by you
      return { ok: true, ...now };
    }
    return { ok: false, code: "unknown", message: "Azione non supportata." };
  }

  /** The checkout you confirmed is complete: total paid, read from the page you are on. */
  function purchaseDone() {
    const path = location.pathname;
    const pat = C.patterns;
    const body = (document.body && document.body.innerText) || "";
    const done = (pat.purchase_done_path && pat.purchase_done_path.test(path) && (!pat.purchase_done_text || pat.purchase_done_text.test(body) || /checkout|transaction/.test(path))) ||
      (pat.page_checkout && pat.page_checkout.test(path) && pat.purchase_done_text && pat.purchase_done_text.test(body));
    if (!done) return null;
    let total = null;
    for (const line of body.split("\n")) {
      if (!/total|totale|gesamt/i.test(line)) continue;
      const hit = P.findPrice(line, C);
      if (hit && (!total || hit.price > total)) total = hit.price;
    }
    return { url: location.origin + path, total };
  }

  // ------------------------------------------------------------------ navigation
  function onNavigate() {
    lastHref = location.href;
    pageType = P.pageType(location.pathname, C);
    // Another search (not just the address rewritten by the page): its own first request and
    // timings. What was sent or asked in this document is never sent again.
    const search = searchOf(location.href);
    if (search !== page.search) {
      Object.assign(page, { search, firstSent: false, order: 0, finalPass: true });
      page.timing = { read_ms: 0, scan_ms: 0, cards: 0, requests: [] };
    }
    send({ type: "ff:hello", pageType, url: location.href });
    if (pageType === "item") {
      itemBox.closedFor = null;
      itemBox.render();
      itemCapture.start();
    } else {
      itemCapture.waiting = false;
      itemBox.unmount();
    }
    checkPurchase();
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
        addTiming(msg.timing);
        if ((msg.evals || []).some((ev) => page.sent.has(ev.vinted_id))) {
          markOnce("server-first");
          if (!pendingCards.size && [...page.sent].every((vid) => evals.has(vid))) markOnce("server-all");
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
      case "ff:page-info":
        // For the popup: what kind of Vinted page this is (to offer "add to the scanner").
        sendResponse({ pageType, url: location.href });
        return false;
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
      case "ff:vinted-act":
        vintedAct(msg).then(sendResponse, () => sendResponse({ ok: false, code: "error", message: "Errore nella pagina di Vinted." }));
        return true;
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
    const { items } = P.parseCards(Cards.collectAll(document, C, P.itemId), location.href, C);
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

  let purchaseReported = null;
  function onCheckoutPath() {
    const pat = C.patterns;
    return Boolean((pat.purchase_done_path && pat.purchase_done_path.test(location.pathname)) || (pat.page_checkout && pat.page_checkout.test(location.pathname)));
  }
  const purchaseSoon = debounce(() => checkPurchase(), 500);

  let purchaseWait = null;
  function checkPurchase() {
    if (!paired) return;
    const done = purchaseDone();
    if (!done || purchaseReported === done.url) return;
    if (done.total === null) {
      // The total paid may render a moment later: wait for it, then report what the page shows.
      purchaseWait = purchaseWait || setTimeout(() => {
        const last = purchaseDone();
        if (last && purchaseReported !== last.url) reportPurchase(last);
      }, 6000);
      return;
    }
    reportPurchase(done);
  }

  function reportPurchase(done) {
    clearTimeout(purchaseWait);
    purchaseWait = null;
    purchaseReported = done.url;
    send({ type: "ff:purchase-done", ...done });
  }

  // The document is parsed (DOM interactive): Vinted's own deferred scripts don't need to run first.
  const domReady = () =>
    document.readyState === "loading"
      ? new Promise((resolve) => {
          const on = () => {
            if (document.readyState === "loading") return;
            document.removeEventListener("readystatechange", on);
            resolve();
          };
          document.addEventListener("readystatechange", on);
        })
      : Promise.resolve();

  let startDone = () => {};
  const started = new Promise((resolve) => {
    startDone = resolve;
  });

  async function start() {
    mark("start");
    // Straight from storage (not through the service worker): options, pairing flag (never the
    // key), parser configuration and market summary - the copy in memory (session storage) when
    // the service worker has published it, else the stored ones.
    const boot = await chrome.storage.session.get("boot").then((r) => r.boot || null, () => null);
    mark(boot ? "boot" : "boot-missing");
    const [store, synced] = boot
      ? [boot, { options: boot.options }]
      : await Promise.all([
          chrome.storage.local.get(["paired", "parserConfig", "marketCache"]).catch(() => ({})),
          chrome.storage.sync.get("options").catch(() => ({})),
        ]);
    mark("hello");
    opts = K.normalizeOptions(synced.options);
    paired = Boolean(store.paired);
    useConfig(store.parserConfig);
    market = store.marketCache ? Q.compileMarket(store.marketCache) : null;
    pageType = P.pageType(location.pathname, C);
    startDone();
    send({ type: "ff:hello", pageType, url: location.href }); // live panel bookkeeping
    if (!opts.enabled) return;
    // Injected at the start of the navigation: settings are read while the page loads, the
    // cards are scored while the document arrives (see scanStream), the rest once it is parsed.
    mo.observe(document.documentElement, { childList: true, subtree: true });
    if (paired) streamSoon();
    await domReady();
    mark("dom");
    addedRoots.clear(); // the pass below reads the whole document
    setTimeout(checkPurchase, 600); // the confirmation text may render a moment after load
    if (pageType === "item") {
      itemBox.render();
      if (paired) itemCapture.start();
    }
    if (paired) scanAdded();
  }

  start();
})();
