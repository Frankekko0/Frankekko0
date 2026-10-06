/* FlipFinder for Vinted - toolbar popup. */
"use strict";

const DEFAULT_APP_URL = "http://localhost:3000";
const $ = (id) => document.getElementById(id);

function setStatus(message, isError = false) {
  const el = $("status");
  el.textContent = message;
  el.classList.toggle("error", isError);
}

function normalizeAppUrl(value) {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (!trimmed) return DEFAULT_APP_URL;
  const url = new URL(trimmed); // throws on invalid input
  if (!/^https?:$/.test(url.protocol)) throw new Error("protocol");
  return url.origin + url.pathname.replace(/\/+$/, "");
}

async function load() {
  const { appUrl } = await chrome.storage.sync.get({ appUrl: DEFAULT_APP_URL });
  $("appUrl").value = appUrl;
}

$("save").addEventListener("click", async () => {
  try {
    const appUrl = normalizeAppUrl($("appUrl").value);
    await chrome.storage.sync.set({ appUrl });
    $("appUrl").value = appUrl;
    $("saved").textContent = "Salvato";
  } catch {
    $("saved").textContent = "URL non valido";
  }
});

$("analyze").addEventListener("click", async () => {
  setStatus("Leggo la pagina…");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.id === undefined) return setStatus("Nessuna scheda attiva.", true);
  let res;
  try {
    res = await chrome.tabs.sendMessage(tab.id, { type: "flipfinder:prepare" });
  } catch {
    return setStatus("Apri un annuncio o una ricerca su Vinted (se è già aperta, ricarica la pagina).", true);
  }
  if (!res || res.error) return setStatus(res ? res.error : "Pagina non leggibile.", true);
  await chrome.tabs.create({ url: res.url, index: tab.index + 1, openerTabId: tab.id });
  window.close();
});

load();
