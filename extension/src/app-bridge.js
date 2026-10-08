/*
 * FlipFinder for Vinted - bridge on FlipFinder's own pages (only the address set in the options).
 *
 * Lets the Analysis page ask the extension to add/remove a Vinted favourite or to start a
 * purchase. Accepted only right after a real click on the page (one click, one action) and only
 * for these actions; the extension then works in your Vinted tab. No cookie, token or password
 * passes through here.
 */
(() => {
  "use strict";
  // Once per page - unless the bridge already here belongs to a reloaded (updated) extension.
  if (typeof window.__flipfinderBridge === "function" && window.__flipfinderBridge()) return;
  const me = {};
  window.__flipfinderBridge = () => Boolean(chrome.runtime && chrome.runtime.id);
  window.__flipfinderBridgeLatest = me;

  const ACTIONS = new Set(["ff:vinted-favourite", "ff:vinted-buy", "ff:vinted-buy-check", "ff:vinted-buy-open"]);
  // Every message of this bridge carries its generation and what it can do: the web app then
  // ignores answers of a bridge left on the page by an older extension (it would refuse
  // "ff:vinted-buy" first and trigger the old two-step purchase as well).
  const BRIDGE = 2;
  const FEATURES = ["buy"];
  // One click, one action: each real click (or Enter/Space) on the page allows one request.
  let gestures = 0;
  let used = 0;
  window.addEventListener("click", (e) => e.isTrusted && (gestures += 1), true);
  window.addEventListener("keydown", (e) => e.isTrusted && (e.key === "Enter" || e.key === " ") && (gestures += 1), true);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const NO_RECEIVER = /Receiving end does not exist|Could not establish connection/i;

  /**
   * Asks the service worker. It may be asleep or restarting (the browser stops it when idle):
   * the message wakes it; a message lost while it was stopping is sent again (it never ran).
   */
  async function toWorker(message, tries = 3) {
    for (let i = 0; ; i += 1) {
      try {
        return { answer: await chrome.runtime.sendMessage(message) };
      } catch (err) {
        const msg = String((err && err.message) || err);
        // Extension reloaded or removed: this page keeps the old bridge until it is reloaded.
        if (!chrome.runtime || !chrome.runtime.id || /context invalidated/i.test(msg)) return { error: "invalidated" };
        if (!NO_RECEIVER.test(msg) || i + 1 >= tries) return { error: msg };
      }
      await sleep(200 * (i + 1));
    }
  }

  /** {ok, version, paired} from the service worker now, or null when it did not answer. */
  async function hello(tries) {
    const r = await toWorker({ type: "ff:bridge-hello" }, tries);
    return r.answer && typeof r.answer.ok === "boolean" ? r.answer : null;
  }

  let announced = false;
  function announce(h) {
    document.documentElement.dataset.flipfinderExtension = h.version;
    announced = true;
    window.postMessage({ ff: "bridge-ready", version: h.version, paired: h.paired, bridge: BRIDGE, features: FEATURES }, location.origin);
  }

  // At page start the service worker may be asleep or busy starting: retried with backoff until
  // it answers (a definitive "no" - not FlipFinder's address - ends it).
  (async () => {
    for (const wait of [0, 300, 700, 1500, 3000, 6000, 12000]) {
      if (wait) await sleep(wait);
      const h = await hello(2);
      if (h) {
        if (h.ok) announce(h);
        return;
      }
    }
  })();

  window.addEventListener("message", async (event) => {
    if (event.source !== window || event.origin !== location.origin) return;
    const m = event.data;
    if (!m || m.ff !== "request" || typeof m.id !== "string") return;
    if (window.__flipfinderBridgeLatest !== me) return; // a newer bridge answers
    const reply = (result) => window.postMessage({ ff: "response", id: m.id, result, bridge: BRIDGE }, location.origin);
    if (m.type === "ff:bridge-status") {
      // Asked now, never a remembered failure: the extension may have been asleep a moment ago.
      const h = await hello(4);
      if (h && h.ok && !announced) announce(h);
      return reply(h && h.ok ? { ok: true, version: h.version, paired: h.paired, features: FEATURES } : { ok: false });
    }
    if (!ACTIONS.has(m.type)) return reply({ ok: false, code: "forbidden", message: "Azione non consentita." });
    if (!(navigator.userActivation && navigator.userActivation.isActive) || gestures <= used) {
      return reply({ ok: false, code: "no_click", message: "L'azione parte solo da un tuo clic sul pulsante." });
    }
    used = gestures;
    const p = m.payload || {};
    const expect = Number(p.expect_price);
    const r = await toWorker({
      type: m.type,
      vid: String(p.vid || ""),
      url: String(p.url || ""),
      want: Boolean(p.want),
      ...(Number.isFinite(expect) && expect > 0 ? { expect_price: expect } : {}),
    });
    if (r.error === "invalidated") return reply({ ok: false, code: "extension", message: "Estensione aggiornata: ricarica la pagina." });
    reply(r.answer || { ok: false, code: "extension", message: "Estensione non raggiungibile: ricarica la pagina." });
  });
})();
