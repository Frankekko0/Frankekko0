/*
 * FlipFinder for Vinted - instant verdict on search pages.
 *
 * Scores a result card locally, from the market summary FlipFinder sends (realized prices per
 * brand and category, share sold within 30 days, your costs, counterfeit rules), so the best
 * opportunity on the page is known as soon as the cards are read. Same formulas as the server
 * (costs, condition, risk-adjusted profit = margin x P(sale) x P(authentic)); the full server
 * analysis arrives later and replaces it. A segment without enough sales gives no estimate
 * ("dati insufficienti"), never a guessed number. The most specific data first: concluded sales
 * of the recognised model (market summary "models"), then brand + category, then brand; the
 * page statistics FlipFinder sends for the page (one request) refine it further.
 * No browser APIs: unit-tested in Node.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FlipFinderQuick = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function fold(text) {
    return String(text || "")
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/’/g, "'")
      .split(/\s+/)
      .filter(Boolean)
      .join(" ");
  }

  const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const wordRx = (kw) => new RegExp(`(^|[^a-z0-9])${escape(kw)}(?=$|[^a-z0-9])`);
  const round2 = (v) => Math.round(v * 100) / 100;

  /** Regexes and lookup tables built once per downloaded summary. */
  function compileMarket(m) {
    if (!m || !m.segments) return null;
    const brandAliases = [];
    const brands = {};
    for (const [slug, name, aliases, risk, floor] of m.brands || []) {
      brands[slug] = { slug, name, risk, floor };
      for (const a of aliases) brandAliases.push([a, slug, wordRx(a)]);
    }
    brandAliases.sort((a, b) => b[0].length - a[0].length); // longest first: "polo ralph lauren" before "ralph"
    const keywords = [];
    for (const [slug, , kws] of m.categories || []) for (const k of kws) keywords.push([k, slug, wordRx(k)]);
    keywords.sort((a, b) => b[0].length - a[0].length);
    const lines = (m.lines || []).map(([brand, cat, kws]) => [brand, cat, kws.map(wordRx)]);
    const suspicious = [];
    for (const [pattern, label] of (m.auth && m.auth.suspicious) || []) {
      try {
        suspicious.push([new RegExp(pattern), label]);
      } catch {
        /* a pattern JS cannot read is skipped */
      }
    }
    // Models with enough concluded sales, per brand, longest name first ("air max 90" before "air max").
    const models = {};
    for (const [key, row] of Object.entries(m.models || {})) {
      const cut = key.indexOf("|");
      const brand = key.slice(0, cut);
      const name = key.slice(cut + 1);
      if (!brand || name.length < 2 || !Array.isArray(row)) continue;
      (models[brand] = models[brand] || []).push([name, null, row]); // regex built on first use
    }
    for (const list of Object.values(models)) list.sort((a, b) => b[0].length - a[0].length);
    return { raw: m, version: m.version, brands, brandAliases, keywords, lines, suspicious, models };
  }

  /** The recognised model of a card, among the models with concluded sales: [name, stats row]. */
  function findModel(title, brand, M) {
    const list = M.models && M.models[brand];
    if (!list) return null;
    const folded = fold(title);
    for (const entry of list) {
      if (!entry[1]) entry[1] = wordRx(entry[0]);
      if (entry[1].test(folded)) return [entry[0], entry[2]];
    }
    return null;
  }

  function findBrand(card, M) {
    const field = fold(card.brand);
    if (field) {
      for (const [alias, slug] of M.brandAliases) if (alias === field) return slug;
    }
    const title = fold(card.title);
    for (const [alias, slug, rx] of M.brandAliases) if (alias.length >= 4 && rx.test(title)) return slug;
    return null;
  }

  /** Longest keyword first, each match masked (so "t-shirt" never also counts as "shirt"). */
  function findCategory(title, brand, M) {
    let text = fold(title);
    const scores = {};
    for (const [kw, slug, rx] of M.keywords) {
      const m = rx.exec(text);
      if (!m) continue;
      scores[slug] = (scores[slug] || 0) + (kw.length > 4 ? 3 : 2);
      const start = m.index + m[1].length;
      text = text.slice(0, start) + " ".repeat(kw.length) + text.slice(start + kw.length);
    }
    const folded = fold(title);
    for (const [b, cat, rxs] of M.lines) if (b === brand && rxs.some((rx) => rx.test(folded))) scores[cat] = (scores[cat] || 0) + 2.5;
    let best = null;
    for (const [slug, s] of Object.entries(scores)) if (!best || s > scores[best]) best = slug;
    return best;
  }

  function netMargin(price, resale, fee, costs) {
    const bp = fee !== null && fee !== undefined ? fee : round2(costs.buyer_protection_fixed + costs.buyer_protection_pct * price);
    const acquisition = round2(price + bp + costs.shipping_in + costs.other_acquisition);
    const sale =
      resale -
      round2(costs.selling_fee_fixed + costs.selling_fee_pct * resale) -
      round2(costs.payment_fee_fixed + costs.payment_fee_pct * resale) -
      costs.advertising -
      costs.packaging -
      costs.shipping_out -
      costs.other_sale;
    return { net: round2(sale - acquisition), acquisition };
  }

  function authenticity(card, price, resale, brand, M) {
    const lr = M.raw.auth.lr;
    const info = M.brands[brand];
    const risk = Math.min(0.9, Math.max(0.01, info ? info.risk || 0.05 : 0.1));
    let score = Math.log((1 - risk) / risk);
    const flags = [];
    const floor = info && info.floor ? info.floor : 0.45;
    const ratio = resale > 0 ? price / resale : 1;
    if (ratio < floor) {
      score += Math.log(lr.price_far_below);
      flags.push("prezzo troppo basso per il brand");
    } else if (ratio < floor + 0.15) score += Math.log(lr.price_below);
    const title = fold(`${card.title} ${card.description || ""}`);
    for (const [rx, label] of M.suspicious) {
      if (rx.test(title)) {
        score += Math.log(lr.suspicious_text);
        flags.push(`"${label}" nel titolo`);
      }
    }
    return { p: Math.min(M.raw.auth.max_p, 1 / (1 + Math.exp(-score))), flags };
  }

  const pct = (v) => `${Math.round(v * 100)}%`;
  const eur = (v, sign) => `${v < 0 ? "−" : sign && v > 0 ? "+" : ""}€${Math.abs(v).toFixed(Math.abs(v) >= 100 ? 0 : 2).replace(".", ",")}`;

  /** The quick verdict of one card (or why there is none). */
  function quickEstimate(card, M) {
    const out = { vinted_id: card.vinted_id, source: "local", status: card.status || "active" };
    if (!M) return { ...out, insufficient: "riepilogo di mercato non ancora scaricato" };
    const price = Number(card.price);
    if (!(price > 0)) return { ...out, insufficient: "prezzo non leggibile" };
    const brand = findBrand(card, M);
    if (!brand) return { ...out, insufficient: "brand non riconosciuto" };
    const category = findCategory(card.title, brand, M);
    const catSeg = (category && M.raw.segments[`${brand}|${category}`]) || null;
    const seg = catSeg || M.raw.segments[`${brand}|*`];
    const model = findModel(card.title, brand, M);
    if (model) {
      // Concluded sales of this very model: [median, low, high, n_sales, days, sell_through].
      const [median, low, high, nSales, days, sellThrough] = model[1];
      const pSale = seg ? seg[4] : null;
      return verdictFrom(card, M, out, price, brand, category, {
        median, low, high, n: nSales, pSale: pSale ?? sellThrough ?? null, days: days ?? (seg ? seg[5] : null),
        level: "model", model: model[0], conditioned: false, label: "venduti del modello",
      });
    }
    if (!seg) return { ...out, brand, category, insufficient: "troppe poche vendite di articoli simili" };
    const [median, p10, p90, nSold, pSale, days] = seg;
    return verdictFrom(card, M, out, price, brand, category, {
      median, low: p10, high: p90, n: nSold, pSale, days, level: catSeg ? "category" : "brand", model: null, conditioned: false, label: "venduti simili",
    });
  }

  /**
   * The same verdict from the statistics FlipFinder computed for this card (page statistics:
   * the most specific segment with data, concluded sales first). Asking prices are used only
   * when no sale is known, as the server does; a segment already per condition needs no
   * condition adjustment. Returns null when the statistics add nothing usable.
   */
  function refineWithStats(card, stat, M) {
    if (!M || !stat || !(stat.median > 0)) return null;
    const price = Number(card.price);
    if (!(price > 0)) return null;
    const out = { vinted_id: card.vinted_id, source: "local", status: card.status || "active" };
    const brand = (stat.brand && M.brands[stat.brand] ? stat.brand : null) || findBrand(card, M);
    if (!brand) return null;
    const category = stat.category || findCategory(card.title, brand, M);
    const seg = (category && M.raw.segments[`${brand}|${category}`]) || M.raw.segments[`${brand}|*`];
    const sold = stat.basis === "sold";
    const n = sold ? stat.n_sales : stat.n_asking;
    if (!(n > 0)) return null;
    const pSale = seg && seg[4] !== null && seg[4] !== undefined ? seg[4] : stat.sell_through ?? null;
    const v = verdictFrom(card, M, out, price, brand, category, {
      median: stat.median, low: stat.low, high: stat.high, n, pSale, days: stat.days ?? (seg ? seg[5] : null),
      level: stat.level, model: stat.model || null, conditioned: /condition/.test(stat.level || ""),
      label: sold ? (stat.model ? "venduti del modello" : "venduti simili") : "prezzi chiesti",
    });
    v.refined = true;
    v.basis = stat.basis;
    if (!sold) v.confidence = Math.round(v.confidence * 0.6); // asks are not sales
    return v;
  }

  function verdictFrom(card, M, out, price, brand, category, d) {
    const mult = d.conditioned ? 1 : M.raw.condition_mult[card.condition || "unknown"] ?? M.raw.condition_mult.unknown ?? 0.95;
    const median = d.median;
    const p10 = d.low;
    const p90 = d.high;
    const nSold = d.n;
    const pSale = d.pSale;
    const days = d.days;
    const resale = round2(median * mult);
    const { net, acquisition } = netMargin(price, resale, card.buyer_protection_fee ?? null, M.raw.costs);
    const auth = authenticity(card, price, resale, brand, M);
    const rap = net <= 0 ? net : pSale === null || pSale === undefined ? null : round2(net * pSale * auth.p);
    const discount = resale > 0 ? 1 - price / resale : 0;
    const parts = [
      discount > 0 ? `${pct(discount)} sotto i ${d.label} (${eur(resale)})` : `sopra i ${d.label} (${eur(resale)})`,
      `margine ${eur(net, true)}`,
    ];
    if (pSale !== null && pSale !== undefined) parts.push(`${pct(pSale)} venduti in 30 gg`);
    if (auth.flags.length) parts.push(`⚠ ${auth.flags[0]}`);
    return {
      ...out,
      brand,
      category: category || null,
      segment: d.level,
      model: d.model,
      price,
      total_cost: acquisition,
      resale_expected: resale,
      resale_range: [round2(p10 * mult), round2(p90 * mult)],
      net_margin: net,
      sale_probability: pSale ?? null,
      authenticity_probability: Math.round(auth.p * 1000) / 1000,
      risk_adjusted_profit: rap,
      days_to_sell: days,
      comparables: nSold,
      confidence: Math.round(100 * (1 - Math.exp(-nSold / 20)) * (category || d.model ? 1 : 0.7)),
      reason: parts.join(" · "),
    };
  }

  /** The page's best opportunity: highest positive risk-adjusted profit among cards on sale. */
  function bestOf(estimates) {
    let best = null;
    for (const e of estimates) {
      if (!e || e.status !== "active") continue;
      const v = e.risk_adjusted_profit;
      if (v === null || v === undefined || v <= 0) continue;
      if (!best || v > best.risk_adjusted_profit) best = e;
    }
    return best;
  }

  return { fold, compileMarket, quickEstimate, refineWithStats, bestOf, findBrand, findCategory, findModel, netMargin };
});
