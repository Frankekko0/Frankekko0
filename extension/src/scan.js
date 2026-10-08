/*
 * FlipFinder for Vinted - scanner reader (offscreen document).
 *
 * The service worker has no DOM, so a saved search is read here: fetched as plain HTML without
 * cookies, parsed inert (no script runs, no image loads) and read with the same card reader and
 * parser configuration as the pages you open. A refusal or an anti-bot page is reported as
 * "blocked" and never worked around.
 */
"use strict";

const P = globalThis.FlipFinderParse;
const K = globalThis.FlipFinderCore;
const Cards = globalThis.FlipFinderCards;
const MAX_HTML = 6e6;

function compile(raw) {
  try {
    return P.compileConfig(raw);
  } catch {
    return P.compileConfig(globalThis.FF_PARSER_CONFIG); // a broken download never breaks the scanner
  }
}

/** What the reader received, for a read that yielded no cards (to fix the parser configuration without guessing). */
function diagnose(res, body, doc, C) {
  const lower = body.toLowerCase();
  const count = (rx) => (body.match(rx) || []).length;
  const first = body.search(/\/items\/\d{6,}/);
  let path = "";
  try {
    const u = new URL(res.url);
    path = u.hostname + u.pathname;
  } catch {
    /* no final address */
  }
  return {
    status: res.status,
    finalPage: path,
    redirected: Boolean(res.redirected),
    contentType: (res.headers.get("content-type") || "").slice(0, 60),
    bytes: body.length,
    title: ((doc.querySelector("title") || {}).textContent || "").trim().slice(0, 120),
    itemLinksInDom: doc.querySelectorAll(C.selectors.item_link).length,
    itemPathsInText: count(/\/items\/\d{6,}/g), // also counts links inside scripts / embedded data
    productTestIds: count(/data-testid="product-item-id-\d+/g),
    scripts: doc.scripts.length,
    hasNextFlight: lower.includes("__next_f"),
    hasNextData: lower.includes("__next_data__"),
    hasConsentWall: /onetrust|consent|cookie/.test(lower.slice(0, 20000)),
    challengeMarkers: ["datadome", "captcha", "cf-chl", "just a moment", "verify you are human"].filter((m) => lower.includes(m)),
    // A short look at the markup around the first item address (public search page, no cookies sent).
    around: first >= 0 ? body.slice(Math.max(0, first - 200), first + 300).replace(/\s+/g, " ") : "",
    text: (doc.body ? doc.body.textContent : "").replace(/\s+/g, " ").trim().slice(0, 300),
  };
}

async function readSearch(url, rawConfig) {
  const C = compile(rawConfig || globalThis.FF_PARSER_CONFIG);
  let target;
  try {
    target = new URL(url);
  } catch {
    return { outcome: "error", message: "Indirizzo della ricerca non valido." };
  }
  if (target.protocol !== "https:" || !P.isVintedUrl(target.href, C)) return { outcome: "error", message: "L'indirizzo non è di Vinted." };

  let res;
  try {
    res = await fetch(target.href, { credentials: "omit", redirect: "follow", headers: { Accept: "text/html" } });
  } catch {
    return { outcome: "error", message: "Vinted non raggiungibile." };
  }
  const body = (await res.text().catch(() => "")).slice(0, MAX_HTML);
  if (K.isRefusal(res.status, body)) return { outcome: "blocked", status: res.status, message: `Vinted ha rifiutato la lettura (HTTP ${res.status}).` };
  if (!res.ok) return { outcome: "error", status: res.status, message: `Risposta inattesa (HTTP ${res.status}).` };
  if (res.url && !P.isVintedUrl(res.url, C)) return { outcome: "error", status: res.status, message: "Vinted ha rimandato a un altro indirizzo." };

  const doc = new DOMParser().parseFromString(body, "text/html");
  const { items, unreadable } = P.parseCards(Cards.collectAll(doc, C, P.itemId), res.url || target.href, C);
  const cards = [];
  for (const parsed of items) {
    const payload = P.capturePayload(parsed, C);
    if (payload && parsed.vinted_id) cards.push({ vid: parsed.vinted_id, payload });
  }
  if (!cards.length) {
    return { outcome: "empty", status: res.status, unreadable, diag: diagnose(res, body, doc, C), message: "Nessuna scheda leggibile: la ricerca è vuota oppure la configurazione del parser va aggiornata." };
  }
  return { outcome: "ok", status: res.status, cards, unreadable };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !msg || msg.type !== "ff:scan-read") return false;
  readSearch(String(msg.url || ""), msg.config).then(sendResponse, (err) => sendResponse({ outcome: "error", message: (err && err.message) || "Errore inatteso." }));
  return true;
});
