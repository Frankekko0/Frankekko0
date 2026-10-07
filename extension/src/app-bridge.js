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
  if (window.__flipfinderBridge) return;
  window.__flipfinderBridge = true;

  const ACTIONS = new Set(["ff:vinted-favourite", "ff:vinted-buy-check", "ff:vinted-buy-open"]);
  const hello = chrome.runtime.sendMessage({ type: "ff:bridge-hello" }).catch(() => null);

  hello.then((h) => {
    if (!h || !h.ok) return;
    document.documentElement.dataset.flipfinderExtension = h.version;
    window.postMessage({ ff: "bridge-ready", version: h.version, paired: h.paired }, location.origin);
  });

  window.addEventListener("message", async (event) => {
    if (event.source !== window || event.origin !== location.origin) return;
    const m = event.data;
    if (!m || m.ff !== "request" || typeof m.id !== "string") return;
    const reply = (result) => window.postMessage({ ff: "response", id: m.id, result }, location.origin);
    if (m.type === "ff:bridge-status") {
      const h = await hello;
      return reply(h && h.ok ? { ok: true, version: h.version, paired: h.paired } : { ok: false });
    }
    if (!ACTIONS.has(m.type)) return reply({ ok: false, code: "forbidden", message: "Azione non consentita." });
    if (!(navigator.userActivation && navigator.userActivation.isActive)) {
      return reply({ ok: false, code: "no_click", message: "L'azione parte solo da un tuo clic sul pulsante." });
    }
    const p = m.payload || {};
    const result = await chrome.runtime
      .sendMessage({ type: m.type, vid: String(p.vid || ""), url: String(p.url || ""), want: Boolean(p.want) })
      .catch(() => ({ ok: false, code: "extension", message: "Estensione non raggiungibile: ricarica la pagina." }));
    reply(result);
  });
})();
