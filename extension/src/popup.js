/* FlipFinder for Vinted - toolbar popup: sync state, live panel, open the page in FlipFinder. */
"use strict";

const $ = (id) => document.getElementById(id);

function setStatus(message, isError = false) {
  const el = $("status");
  el.textContent = message;
  el.classList.toggle("error", isError);
}

const LABEL = {
  idle: ["Pronta", ""],
  ok: ["Sincronizzata", "ok"],
  offline: ["FlipFinder non raggiungibile", "warn"],
  error: ["Errore di sincronizzazione", "warn"],
  unpaired: ["Non associata: apri le opzioni", "err"],
};

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab && tab.id !== undefined ? tab : null;
}

async function load() {
  const st = await chrome.runtime.sendMessage({ type: "ff:status" });
  if (!st) return;
  const state = st.paired ? st.sync.state : "unpaired";
  const [text, tone] = LABEL[state] || [state, ""];
  $("state").textContent = st.paired && st.account ? `${text} · ${st.account}` : text;
  const pending = $("pending");
  pending.hidden = !st.sync.pending;
  pending.textContent = `${st.sync.pending} in coda`;
  pending.className = `pill ${tone}`;
  if (st.deep.pausedUntil > Date.now()) $("sub").textContent = "Letture di altre pagine in pausa dopo un rifiuto di Vinted.";
  $("panel").disabled = !chrome.sidePanel;
}

$("panel").addEventListener("click", async () => {
  const tab = await activeTab();
  if (!tab || !chrome.sidePanel) return setStatus("Pannello laterale non disponibile in questo browser.", true);
  try {
    await chrome.sidePanel.open({ tabId: tab.id });
    window.close();
  } catch {
    setStatus("Non riesco ad aprire il pannello. Usa Alt+Shift+F.", true);
  }
});

// Works without pairing: the page's data travels in the URL fragment (never sent to a server).
$("analyze").addEventListener("click", async () => {
  setStatus("Leggo la pagina…");
  const tab = await activeTab();
  if (!tab) return setStatus("Nessuna scheda attiva.", true);
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

$("app").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "ff:open", path: "/items" });
  window.close();
});

$("options").addEventListener("click", () => chrome.runtime.openOptionsPage());

load();
