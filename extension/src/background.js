/* FlipFinder for Vinted - opens the analysis tab requested by the content script or popup. */
"use strict";

// Only FlipFinder's two import pages can be opened, whatever a page script might send.
const OPENABLE = new Set(["/analyze", "/import"]);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "flipfinder:open") return false;
  let url;
  try {
    url = new URL(message.url);
  } catch {
    sendResponse({ ok: false });
    return false;
  }
  if (!/^https?:$/.test(url.protocol) || !OPENABLE.has(url.pathname)) {
    sendResponse({ ok: false });
    return false;
  }
  const opener = sender.tab ? { index: sender.tab.index + 1, openerTabId: sender.tab.id } : {};
  chrome.tabs.create({ url: url.href, ...opener }).then(
    () => sendResponse({ ok: true }),
    () => sendResponse({ ok: false }),
  );
  return true;
});
