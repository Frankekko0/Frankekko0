/*
 * FlipFinder for Vinted - reading search cards out of a document.
 *
 * Shared by the content script (the page you opened) and the scanner's offscreen document (a saved
 * search fetched as HTML and parsed inert: no script runs, no image loads). Selectors and labels
 * come from the parser configuration, so a change on Vinted is fixed by updating that file.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FlipFinderCards = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const text = (el) => (el ? (el.textContent || "").replace(/\s+/g, " ").trim() : "");

  /** The largest ancestor of an item link that contains no link to another item: the card. */
  function cardRoot(link, id, C) {
    const doc = link.ownerDocument;
    const other = `${C.selectors.item_link}:not([href*="/items/${id}-"]):not([href$="/items/${id}"]):not([href*="/items/${id}?"])`;
    let el = link;
    for (let depth = 0; depth < 10; depth += 1) {
      const parent = el.parentElement;
      if (!parent || parent === doc.body || parent.tagName === "MAIN") break;
      if (parent.querySelector(other)) return el;
      el = parent;
    }
    return el;
  }

  /** The raw fields of one card, as the parser expects them. */
  function collectCard(root, link, C) {
    // The item's own photo: never the seller's avatar shown on the card.
    const img = [...root.querySelectorAll(C.selectors.card_image || "img")].find((el) => !el.closest(C.selectors.card_exclude)) || null;
    const testids = {};
    const suffixRx = new RegExp(C.selectors.card_testid_suffix || "--([a-z-]+)$");
    root.querySelectorAll("[data-testid]").forEach((el) => {
      const suffix = suffixRx.exec(el.getAttribute("data-testid") || "")?.[1];
      if (suffix && !(suffix in testids)) testids[suffix] = text(el);
    });
    const texts = [];
    const walker = root.ownerDocument.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
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

  /** Every card of a document, once each (the first link to an item wins). */
  function collectAll(doc, C, itemId) {
    const raw = [];
    const done = new Set();
    for (const link of doc.querySelectorAll(C.selectors.item_link)) {
      const id = itemId(link.getAttribute("href") || "", C);
      if (!id || done.has(id)) continue;
      done.add(id);
      raw.push(collectCard(cardRoot(link, id, C), link, C));
    }
    return raw;
  }

  return { text, cardRoot, collectCard, collectAll };
});
