/* FlipFinder for Vinted - options page: pairing, features, thresholds, filters, slow reads, scanner, state. */
"use strict";

const K = globalThis.FlipFinderCore;
const $ = (id) => document.getElementById(id);
const fields = [...document.querySelectorAll("[data-opt]")];
let current = K.normalizeOptions({});

function setStatus(message, kind = "") {
  const el = $("pairStatus");
  el.textContent = message;
  el.className = `status ${kind}`;
}

function fill(opts) {
  for (const el of fields) {
    const key = el.getAttribute("data-opt");
    const v = opts[key];
    if (el.type === "checkbox") el.checked = Boolean(v);
    else el.value = v === null || v === undefined ? "" : String(v);
  }
}

function read() {
  const raw = { ...current };
  for (const el of fields) {
    const key = el.getAttribute("data-opt");
    if (el.type === "checkbox") raw[key] = el.checked;
    else if (el.type === "number") raw[key] = el.value === "" ? null : Number(el.value);
    else raw[key] = el.value;
  }
  return K.normalizeOptions(raw);
}

let savedTimer = 0;
async function save() {
  current = read();
  await chrome.storage.sync.set({ options: current });
  const badge = $("saved");
  badge.classList.add("on");
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => badge.classList.remove("on"), 1200);
}

let saveTimer = 0;
for (const el of fields) {
  el.addEventListener(el.type === "checkbox" ? "change" : "input", () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, el.type === "checkbox" ? 0 : 400);
  });
}

// ------------------------------------------------------------------ pairing
$("pair").addEventListener("click", async () => {
  let appUrl;
  try {
    appUrl = K.normalizeAppUrl($("appUrl").value);
  } catch {
    return setStatus("Indirizzo non valido: usa http(s)://…", "error");
  }
  const origin = new URL(appUrl).origin;
  // Access to FlipFinder's address only, asked now (not at install time).
  let granted = false;
  try {
    granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
  } catch {
    granted = false;
  }
  if (!granted) return setStatus(`Serve il permesso per contattare ${origin}.`, "error");
  const button = $("pair");
  button.setAttribute("aria-busy", "true");
  setStatus("Verifico la chiave…");
  try {
    const res = await chrome.runtime.sendMessage({ type: "ff:pair", appUrl, key: $("apiKey").value });
    if (!res || res.error) return setStatus((res && res.error) || "Associazione non riuscita.", "error");
    $("apiKey").value = "";
    $("appUrl").value = appUrl;
    current = { ...current, appUrl };
    setStatus(`Associato${res.account ? ` a ${res.account}` : ""}.`, "ok");
    await refresh();
  } finally {
    button.removeAttribute("aria-busy");
  }
});

$("unpair").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "ff:unpair" });
  setStatus("Estensione disassociata: la chiave è stata rimossa da questo browser. Revocala anche in FlipFinder se non serve più.");
  await refresh();
});

$("flush").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "ff:flush" });
  await refresh();
});

$("clear").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "ff:clear-queue" });
  await refresh();
});

// ------------------------------------------------------------------ automatic scanner
function setScanStatus(message, kind = "") {
  const el = $("scanStatus");
  el.textContent = message;
  el.className = `status ${kind}`;
}

function scanLine(s) {
  const bits = [];
  if (!s.enabled) bits.push("sospesa");
  else if (!s.allowed) bits.push("serve il permesso: aggiungila di nuovo");
  else if (!s.lastOkAt) bits.push(s.lastScanAt ? "prima lettura non riuscita" : "in attesa della prima lettura");
  else bits.push(`letta ${ago(s.lastOkAt)} · ${s.lastCount} annunci · ${s.lastNew} nuovi · ${s.totalNew} dall'inizio`);
  if (s.lastError) bits.push(s.lastError);
  return bits.join(" — ");
}

function renderScan(sc) {
  if (!sc) return;
  const active = sc.searches.filter((s) => s.enabled).length;
  $("scanPace").textContent = active
    ? `${active} ${active === 1 ? "ricerca" : "ricerche"}: una lettura ogni ${sc.effectiveIntervalMin} min ciascuna (circa ${sc.requestsPerHour} richieste all'ora, tetto ${sc.limits.perHour}/h e ${sc.limits.perDay}/giorno).${sc.effectiveIntervalMin > sc.intervalMin ? " L'intervallo è stato allungato per restare nei tetti." : ""}`
    : "Aggiungi almeno una ricerca.";
  const ul = $("scanList");
  ul.replaceChildren();
  if (!sc.searches.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "Nessuna ricerca nell'elenco.";
    ul.append(li);
  }
  for (const s of sc.searches) {
    const li = document.createElement("li");
    li.style.cssText = "display:flex;gap:8px;align-items:center;padding:8px 0;border-top:1px solid var(--line)";
    const info = document.createElement("div");
    info.className = "grow";
    const name = document.createElement("b");
    name.textContent = s.name;
    const link = document.createElement("a");
    link.href = s.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = " apri";
    link.style.fontSize = "12px";
    const line = document.createElement("div");
    line.className = "muted";
    line.style.fontSize = "12px";
    line.textContent = scanLine(s);
    info.append(name, link, line);
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "btn small ghost";
    toggle.textContent = s.enabled ? "Sospendi" : "Riattiva";
    toggle.addEventListener("click", async () => renderScan((await chrome.runtime.sendMessage({ type: "ff:scan-toggle", id: s.id, enabled: !s.enabled })).summary));
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn small ghost";
    del.textContent = "Rimuovi";
    del.addEventListener("click", async () => renderScan((await chrome.runtime.sendMessage({ type: "ff:scan-remove", id: s.id })).summary));
    li.append(info, toggle, del);
    ul.append(li);
  }
}

$("scanAdd").addEventListener("click", async () => {
  const n = K.normalizeSearchUrl($("scanUrl").value);
  if (n.error) return setScanStatus(n.error, "error");
  // Access to this Vinted site only, asked now by a click (not at install time).
  let granted = false;
  try {
    granted = await chrome.permissions.request({ origins: [`${n.origin}/*`] });
  } catch {
    granted = false;
  }
  if (!granted) return setScanStatus(`Serve il permesso per leggere ${new URL(n.origin).hostname}.`, "error");
  const res = await chrome.runtime.sendMessage({ type: "ff:scan-add", url: n.url, name: $("scanName").value });
  if (!res || res.error) return setScanStatus((res && res.error) || "Non riesco ad aggiungerla.", "error");
  $("scanUrl").value = "";
  $("scanName").value = "";
  setScanStatus(current.scanEnabled ? "Aggiunta: la prima lettura parte a breve." : "Aggiunta. Attiva lo scanner per farla partire.", "ok");
  renderScan(res.summary);
});

// ------------------------------------------------------------------ state
const SYNC_LABEL = {
  idle: "in attesa della prima cattura",
  ok: "sincronizzato",
  offline: "FlipFinder non raggiungibile: le catture restano in coda",
  error: "errore, nuovo tentativo programmato",
  unpaired: "non associata",
};

function fact(dl, label, value) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.append(dt, dd);
}

function ago(ts) {
  if (!ts) return "mai";
  const s = Math.round((Date.now() - ts) / 1000);
  if (s < 60) return "ora";
  if (s < 3600) return `${Math.round(s / 60)} min fa`;
  return `${Math.round(s / 3600)} h fa`;
}

async function refresh() {
  const st = await chrome.runtime.sendMessage({ type: "ff:status" });
  if (!st) return;
  $("version").textContent = `Versione ${st.version} · configurazione del parser ${st.parserVersion}`;
  $("unpair").hidden = !st.paired;
  $("pair").textContent = st.paired ? "Cambia chiave" : "Associa";
  if (st.paired && !$("pairStatus").textContent) setStatus(`Associato${st.account ? ` a ${st.account}` : ""}.`, "ok");
  const dl = $("facts");
  dl.replaceChildren();
  fact(dl, "Sincronizzazione", SYNC_LABEL[st.sync.state] || st.sync.state);
  fact(dl, "In coda", String(st.sync.pending));
  fact(dl, "Ultimo invio", ago(st.sync.lastSyncAt));
  const sc = await chrome.runtime.sendMessage({ type: "ff:scan-state" });
  if (sc) {
    fact(dl, "Scanner", !sc.enabled ? "spento" : sc.pausedUntil ? `in pausa per ${Math.ceil((sc.pausedUntil - Date.now()) / 60000)} min (${sc.pauseReason})` : `attivo · ${sc.searches.filter((x) => x.enabled).length} ricerche · ${sc.usedToday}/${sc.limits.perDay} letture oggi`);
    renderScan(sc);
  }
  fact(dl, "Letture di altre pagine", st.deep.pausedUntil > Date.now() ? `in pausa per ${Math.ceil((st.deep.pausedUntil - Date.now()) / 60000)} min (${st.deep.pauseReason})` : `${st.deep.queued} in attesa`);
  const ul = $("errors");
  ul.replaceChildren();
  if (!st.errors.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "Nessun errore.";
    ul.append(li);
  }
  for (const e of st.errors) {
    const li = document.createElement("li");
    li.textContent = `${new Date(e.at).toLocaleString("it-IT")} · ${e.where}: ${e.message}`;
    ul.append(li);
  }
}

(async () => {
  const { options } = await chrome.storage.sync.get("options");
  current = K.normalizeOptions(options);
  fill(current);
  $("appUrl").value = current.appUrl;
  await refresh();
  setInterval(refresh, 5000);
})();
