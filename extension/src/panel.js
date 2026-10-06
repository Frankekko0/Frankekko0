/*
 * FlipFinder for Vinted - live side panel.
 *
 * Ranks, as you scroll, the cards of the Vinted tab next to it: the best one and the next four,
 * with price, total cost, estimated resale, net margin, score, confidence and a one-line
 * reason. Click an entry to scroll the page to that card. Filters, alerts, counters, an item
 * detail view, lock / reset / CSV export. Everything shown comes from FlipFinder's analyses,
 * already saved there ("visto in scorrimento" or "analizzato a fondo").
 */
"use strict";

const K = globalThis.FlipFinderCore;
const $ = (id) => document.getElementById(id);
const send = (msg) => chrome.runtime.sendMessage(msg).catch(() => null);

let tabId = null;
let state = null; // { session, tab, options, sync }
let filters = null;
let detail = { vid: null, data: null, image: 0, manual: false };
let lastBest = undefined;
let audio = null;

const FILTER_KEYS = ["budget", "minMargin", "brands", "sizes", "excludeFakeRisk"];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

const scored = (ev) => ev && ev.flip_score !== null && ev.flip_score !== undefined;
const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
const STATUS = { sold: "venduto", reserved: "riservato", removed: "rimosso", active: "attivo", unknown: "sconosciuto" };

// ------------------------------------------------------------------ data
// A Vinted tab can be pinned with ?tabId= (panel opened in its own window).
const pinnedTab = Number(new URLSearchParams(location.search).get("tabId")) || null;

async function activeTabId() {
  if (pinnedTab) return pinnedTab;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab && typeof tab.id === "number" ? tab.id : null;
}

async function loadFilters(options) {
  const { panelFilters } = await chrome.storage.local.get("panelFilters");
  const base = Object.fromEntries(FILTER_KEYS.map((k) => [k, options[k]]));
  filters = { ...base, ...(panelFilters || {}) };
}

async function load() {
  tabId = await activeTabId();
  state = tabId === null ? null : await send({ type: "ff:panel-state", tabId });
  if (!state) state = { session: null, tab: null, options: K.normalizeOptions({}), sync: { state: "idle" } };
  if (!filters) await loadFilters(state.options);
  // On an item page the panel shows that item; going back to the ranking is one click.
  const itemVid = state.tab && state.tab.pageType === "item" ? (/\/items\/(\d+)/.exec(state.tab.url || "") || [])[1] : null;
  if (itemVid && itemVid !== detail.vid && !detail.manual) openDetail(itemVid, false);
  if (!itemVid && detail.vid && !detail.manual) closeDetail();
  // A newer analysis of the item on screen (e.g. the full one after the card one): reload it.
  const fresh = detail.vid && state.session ? state.session.evals[detail.vid] : null;
  const shown = detail.data && detail.data.evaluation;
  if (fresh && shown && (fresh.analyzed_at !== shown.analyzed_at || fresh.analysis_depth !== shown.analysis_depth || fresh.tracked !== shown.tracked)) {
    openDetail(detail.vid, detail.manual);
  }
  render();
}

let renderTimer = 0;
function loadSoon() {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(load, 250);
}

// ------------------------------------------------------------------ ranking view
function render() {
  const sync = state.sync || { state: "idle" };
  const syncEl = $("sync");
  const tone = { ok: "ok", offline: "warn", error: "warn", unpaired: "err" }[sync.state] || "";
  syncEl.className = `sync ${tone}`;
  syncEl.lastElementChild.textContent =
    sync.state === "unpaired"
      ? "non associata (opzioni)"
      : sync.state === "offline"
        ? `FlipFinder non raggiungibile · ${sync.pending || 0} in coda`
        : sync.pending
          ? `${sync.pending} in invio…`
          : sync.lastSyncAt
            ? "sincronizzato"
            : "pronto";
  $("live").checked = Boolean(state.options.enabled && state.options.captureCards);

  const s = state.session;
  const tab = state.tab;
  let context = "Apri una ricerca, un armadio o i preferiti su Vinted e scorri: le schede vengono valutate quando entrano a schermo.";
  if (tab) {
    let query = "";
    try {
      query = new URL(tab.url).searchParams.get("search_text") || "";
    } catch {
      /* ignore */
    }
    context = { catalog: query ? `Ricerca: “${query}”` : "Catalogo", closet: "Armadio", favourites: "I tuoi preferiti", item: "Annuncio aperto" }[tab.pageType] || "Pagina Vinted";
  }
  $("context").textContent = context;
  $("lock").setAttribute("aria-pressed", String(Boolean(s && s.locked)));
  $("lock").textContent = s && s.locked ? "Sblocca" : "Blocca";
  $("c-seen").textContent = s ? String(s.seen) : "0";
  $("c-saved").textContent = s ? String(s.saved) : "0";
  $("c-best").textContent = s && s.bestMargin !== null ? K.eur(s.bestMargin, true) : "—";

  for (const input of document.querySelectorAll("[data-f]")) {
    if (document.activeElement === input) continue;
    const v = filters[input.getAttribute("data-f")];
    if (input.type === "checkbox") input.checked = Boolean(v);
    else input.value = v === null || v === undefined ? "" : String(v);
  }
  const active = [filters.budget ? `≤ ${K.eur(filters.budget)}` : "", filters.brands, filters.sizes, filters.excludeFakeRisk ? "no fake" : ""].filter(Boolean);
  $("filters-summary").textContent = active.join(" · ");

  const list = $("rank");
  list.replaceChildren();
  const evals = s ? Object.values(s.evals) : [];
  const top = K.rank(evals, filters, 5);
  if (!top.length) {
    list.append(el("li", { class: "empty", text: evals.length ? "Nessun articolo passa i filtri: allargali per vedere la classifica." : state.sync.state === "unpaired" ? "Associa l'estensione nelle opzioni per vedere la classifica." : "Ancora nessuna scheda valutata in questa ricerca." }));
  }
  top.forEach((ev, i) => list.append(el("li", {}, entry(ev, i === 0 && scored(ev)))));
  const best = top[0] && scored(top[0]) ? top[0].vinted_id : null;
  if (best !== lastBest && tabId !== null) {
    lastBest = best;
    send({ type: "ff:relay", tabId, message: { type: "ff:highlight", vid: best } });
  }
}

function entry(ev, best) {
  const img = ev.image_url ? el("img", { src: ev.image_url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }) : el("span", { class: "ph" });
  img.addEventListener?.("error", () => img.replaceWith(el("span", { class: "ph" })));
  const score = scored(ev)
    ? el("span", { class: "pill accent score", text: `${ev.flip_score}` })
    : el("span", { class: "pill warn", text: "dati insufficienti" });
  const margin = ev.net_margin;
  const btn = el(
    "button",
    { class: `entry${best ? " best" : ""}`, type: "button", "aria-label": `${best ? "Migliore: " : ""}${ev.title}. Vai alla scheda` },
    img,
    el(
      "div",
      { style: "min-width:0" },
      el("div", { class: "row" }, el("span", { class: "t grow", text: ev.title }), score),
      el(
        "div",
        { class: "nums" },
        el("span", { text: "Prezzo" }), el("span", { text: "Costo tot." }), el("span", { text: "Rivendita" }),
        el("b", { text: K.eur(ev.price) }), el("b", { text: K.eur(ev.total_cost) }), el("b", { text: K.eur(ev.resale_expected) }),
        el("span", { text: "Margine" }), el("span", { text: "Confidenza" }), el("span", { text: "" }),
        el("b", { class: margin === null || margin === undefined ? "" : margin >= 0 ? "pos" : "neg", text: K.eur(margin, true) }), el("b", { text: ev.confidence === null || ev.confidence === undefined ? "—" : `${ev.confidence}/100` }), el("b", { text: "" }),
      ),
      el("div", { class: "r", text: ev.reason || "" }),
      el(
        "div",
        { class: "tags" },
        el("span", { class: "pill", text: ev.analysis_depth === "full" ? "analizzato a fondo" : "visto in scorrimento" }),
        ev.tracked ? el("span", { class: "pill ok", text: "tracciato" }) : null,
        ev.fake_risk === "low" ? el("span", { class: "pill warn", text: "rischio fake basso" }) : null,
      ),
    ),
  );
  btn.addEventListener("click", async () => {
    const r = await send({ type: "ff:relay", tabId, message: { type: "ff:scroll-to", vid: ev.vinted_id } });
    if (!r || !r.ok) openDetail(ev.vinted_id, true); // the card is no longer on the page
  });
  btn.addEventListener("dblclick", () => openDetail(ev.vinted_id, true));
  btn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") openDetail(ev.vinted_id, true);
  });
  const wrap = el("div", {}, btn);
  const more = el("button", { class: "more", type: "button", text: "Dettaglio →", "aria-label": `Dettaglio di ${ev.title}` });
  more.addEventListener("click", () => openDetail(ev.vinted_id, true));
  wrap.append(more);
  return wrap;
}

// ------------------------------------------------------------------ detail view
async function openDetail(vid, manual) {
  detail = { vid, data: null, image: 0, manual };
  $("view-rank").hidden = true;
  $("view-detail").hidden = false;
  renderDetail();
  const res = await send({ type: "ff:item-detail", vid });
  if (detail.vid !== vid) return;
  detail.data = res && !res.error ? res : { error: (res && res.error) || "Dettaglio non disponibile." };
  renderDetail();
}

function closeDetail() {
  detail = { vid: null, data: null, image: 0, manual: false };
  $("view-detail").hidden = true;
  $("view-rank").hidden = false;
}

function renderDetail() {
  const root = $("detail");
  root.replaceChildren();
  const d = detail.data;
  if (!d) {
    root.append(el("p", { class: "empty", text: "Carico l'analisi…" }));
    $("d-depth").textContent = "";
    return;
  }
  if (d.error) {
    root.append(el("p", { class: "empty", text: d.error === "Articolo non trovato." ? "Questo annuncio non è ancora in FlipFinder: aspetta qualche secondo dopo averlo aperto." : d.error }));
    return;
  }
  const ev = d.evaluation || {};
  const a = d.analysis;
  $("d-depth").textContent = ev.analysis_depth === "full" ? "analizzato a fondo" : "visto in scorrimento";
  const images = d.images || [];
  if (images.length) {
    const i = Math.min(detail.image, images.length - 1);
    const main = el("img", { class: "main", src: images[i].url, alt: `${d.item.title} — foto ${i + 1} di ${images.length}`, referrerpolicy: "no-referrer" });
    root.append(main);
    const thumbs = el("div", { class: "thumbs", role: "tablist", "aria-label": "Foto" });
    images.forEach((img, j) => {
      const b = el("button", { type: "button", role: "tab", "aria-current": String(j === i), "aria-label": `Foto ${j + 1}` }, el("img", { src: img.url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }));
      b.addEventListener("click", () => {
        detail.image = j;
        renderDetail();
      });
      thumbs.append(b);
    });
    thumbs.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      detail.image = (i + (e.key === "ArrowRight" ? 1 : images.length - 1)) % images.length;
      renderDetail();
      $("detail").querySelectorAll(".thumbs button")[detail.image]?.focus();
    });
    root.append(thumbs);
  }
  root.append(el("h2", { text: d.item.title }));
  root.append(el("p", { class: "muted", text: [d.item.brand, d.item.size, STATUS[d.tracking.status] || d.tracking.status].filter(Boolean).join(" · ") }));
  const econ = (a && a.economics) || {};
  const head = el("div", { class: "row", style: "margin-top:8px" });
  head.append(scored(ev) ? el("span", { class: "pill accent", text: `Score ${ev.flip_score}` }) : el("span", { class: "pill warn", text: "Dati insufficienti" }));
  head.append(el("span", { class: "text-2", style: "font-size:12px", text: (a && a.headline) || ev.reason || "" }));
  root.append(head);
  const facts = el("dl", { class: "facts" });
  const fact = (label, value, cls) => facts.append(el("dt", { text: label }), el("dd", { class: cls, text: value }));
  fact("Prezzo", K.eur(d.item.price));
  fact("Costo totale", K.eur(econ.total_acquisition_cost ?? ev.total_cost));
  if (scored(ev)) {
    fact("Rivendita realistica", `${K.eur(econ.resale_low)} – ${K.eur(econ.resale_high)}`);
    fact("Margine netto", K.eur(econ.net_margin ?? ev.net_margin, true), (econ.net_margin ?? ev.net_margin ?? 0) >= 0 ? "pos" : "neg");
    fact("ROI", pct(econ.roi ?? ev.roi));
  }
  fact("Confidenza", ev.confidence === null || ev.confidence === undefined ? "—" : `${ev.confidence}/100`);
  if (ev.days_to_sell) fact("Vendita stimata", `~${Math.round(ev.days_to_sell)} giorni`);
  root.append(facts);
  if (a && a.market) {
    const m = a.market;
    const n = m.used ?? m.comparables_found;
    root.append(el("h3", { style: "font-size:12px;margin-top:8px", text: `Comparabili: ${n ?? 0} usati${m.found !== undefined ? ` su ${m.found} trovati` : ""}` }));
    const prices = m.prices || { min: m.min, p25: m.p25, median: m.median, p75: m.p75 };
    if (prices && prices.median !== null && prices.median !== undefined) {
      const pd = el("dl", { class: "facts" });
      for (const [label, key] of [["Min", "min"], ["P25", "p25"], ["Mediana", "median"], ["P75", "p75"]]) pd.append(el("dt", { text: label }), el("dd", { text: K.eur(prices[key]) }));
      root.append(pd);
    } else {
      root.append(el("p", { class: "muted", text: "Pochi comparabili: nessuna stima di prezzo." }));
    }
  }
  if (a && a.risk_signals && a.risk_signals.length) {
    const ul = el("ul", { class: "plain signals" });
    for (const sig of a.risk_signals) ul.append(el("li", {}, el("b", { text: `${sig.title}: ` }), sig.verifiable === false ? "non verificato" : sig.label));
    root.append(el("h3", { style: "font-size:12px;margin-top:8px", text: "Segnali di rischio" }), ul);
  }
  const bar = el("div", { class: "bar" });
  const track = el("button", { class: "btn small primary", type: "button", text: ev.tracked ? "Smetti di tracciare" : "Traccia" });
  track.addEventListener("click", async () => {
    const r = await send({ type: "ff:track", url: d.item.url, track: !ev.tracked });
    if (r && r.evaluation) {
      detail.data.evaluation = r.evaluation;
      renderDetail();
    }
  });
  bar.append(track);
  if (ev.analysis_depth !== "full") {
    const deep = el("button", { class: "btn small", type: "button", text: "Analisi approfondita" });
    deep.addEventListener("click", async () => {
      deep.disabled = true;
      const r = await send({ type: "ff:deep", vid: detail.vid, url: d.item.url, tabId });
      deep.textContent = r && r.error ? r.error : "In coda: lettura lenta della pagina…";
    });
    bar.append(deep);
  }
  const open = el("button", { class: "btn small", type: "button", text: "Pagina di tracking" });
  open.addEventListener("click", () => send({ type: "ff:open", path: `/items/${detail.vid}` }));
  bar.append(open);
  root.append(bar);
}

// ------------------------------------------------------------------ alerts
function beep() {
  try {
    audio = audio || new AudioContext();
    if (audio.state === "suspended") audio.resume();
    const t = audio.currentTime;
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.frequency.setValueAtTime(880, t);
    osc.frequency.setValueAtTime(1175, t + 0.12);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.12, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.3);
    osc.connect(gain).connect(audio.destination);
    osc.start(t);
    osc.stop(t + 0.32);
  } catch {
    /* no audio: the visual alert stays */
  }
}

function onHot(items) {
  const opts = state ? state.options : K.normalizeOptions({});
  const best = K.rank(items, filters, 1)[0];
  if (!best) return;
  if (opts.alertsVisual) {
    const box = $("alert");
    box.textContent = `Nuovo candidato: ${best.title} · score ${best.flip_score} · ${K.eur(best.net_margin, true)}`;
    box.hidden = false;
    box.classList.remove("flash");
    void box.offsetWidth;
    box.classList.add("flash");
    box.onclick = () => send({ type: "ff:relay", tabId, message: { type: "ff:scroll-to", vid: best.vinted_id } });
  }
  if (opts.alertsSound) beep();
}

// ------------------------------------------------------------------ controls
$("live").addEventListener("change", async (e) => {
  const { options } = await chrome.storage.sync.get("options");
  const next = K.normalizeOptions({ ...(options || {}), captureCards: e.target.checked, enabled: e.target.checked ? true : (options || {}).enabled });
  await chrome.storage.sync.set({ options: next });
  loadSoon();
});

$("lock").addEventListener("click", async () => {
  if (tabId === null) return;
  await send({ type: "ff:session-lock", tabId, locked: !(state.session && state.session.locked) });
  load();
});

$("reset").addEventListener("click", async () => {
  if (tabId === null) return;
  await send({ type: "ff:session-reset", tabId });
  lastBest = undefined;
  load();
});

$("export").addEventListener("click", () => {
  const evals = state && state.session ? Object.values(state.session.evals) : [];
  const rows = K.rank(evals, filters, Infinity);
  const blob = new Blob([K.rankingCsv(rows)], { type: "text/csv;charset=utf-8" });
  const a = el("a", { href: URL.createObjectURL(blob), download: `flipfinder-classifica-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.csv` });
  document.body.append(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 1000);
});

let filterTimer = 0;
for (const input of document.querySelectorAll("[data-f]")) {
  input.addEventListener(input.type === "checkbox" ? "change" : "input", () => {
    const key = input.getAttribute("data-f");
    filters[key] = input.type === "checkbox" ? input.checked : input.type === "number" ? (input.value === "" ? null : Number(input.value)) : input.value;
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      chrome.storage.local.set({ panelFilters: filters });
      render();
    }, 250);
  });
}

$("filters-reset").addEventListener("click", async () => {
  await chrome.storage.local.remove("panelFilters");
  filters = null;
  await loadFilters(state.options);
  render();
});

$("back").addEventListener("click", () => {
  closeDetail();
  detail.manual = true; // stay on the ranking even on an item page
  render();
});

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || typeof msg.type !== "string") return false;
  if (msg.type === "ff:sync" && state) {
    state.sync = msg.sync;
    render();
  } else if ((msg.type === "ff:session-update" || msg.type === "ff:session-reset" || msg.type === "ff:tab-update") && msg.tabId === tabId) {
    if (msg.type !== "ff:session-update") detail.manual = false;
    loadSoon();
  } else if (msg.type === "ff:hot" && msg.tabId === tabId) {
    onHot(msg.items || []);
  }
  return false;
});

chrome.tabs.onActivated.addListener(() => {
  detail.manual = false;
  lastBest = undefined;
  load();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "sync" && changes.options) loadSoon();
});

document.addEventListener("pointerdown", () => {
  // Audio needs a gesture once; prepare it so alerts can sound later.
  if (!audio && state && state.options.alertsSound) {
    try {
      audio = new AudioContext();
    } catch {
      /* ignore */
    }
  }
}, { once: true });

load();
