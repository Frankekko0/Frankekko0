import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Q = require("../src/quick.js");

const COSTS = {
  buyer_protection_fixed: 0.7, buyer_protection_pct: 0.05, shipping_in: 3.49, use_listing_shipping: true, other_acquisition: 0,
  selling_fee_fixed: 0, selling_fee_pct: 0, payment_fee_fixed: 0, payment_fee_pct: 0, advertising: 0, packaging: 0.5, shipping_out: 0, other_sale: 0,
};
const MARKET = {
  version: "t1",
  costs: COSTS,
  condition_mult: { new_with_tags: 1.15, new_without_tags: 1.08, very_good: 1, good: 0.88, satisfactory: 0.7, unknown: 0.95 },
  brands: [
    ["ralph-lauren", "Ralph Lauren", ["polo ralph lauren", "ralph lauren", "rl"], 0.12, 0.45],
    ["stone-island", "Stone Island", ["stone island"], 0.35, 0.5],
    ["zara", "Zara", ["zara"], 0, 0.45],
  ],
  categories: [
    ["polo-shirts", "tops", ["polo", "polo shirt"]],
    ["hoodies", "tops", ["felpa con cappuccio", "hoodie"]],
    ["t-shirts", "tops", ["t-shirt", "maglietta"]],
    ["shirts", "tops", ["camicia", "shirt"]],
  ],
  lines: [["ralph-lauren", "polo-shirts", ["custom slim fit"]]],
  segments: {
    "ralph-lauren|polo-shirts": [25, 13.5, 36, 313, 0.655, 5.6],
    "ralph-lauren|hoodies": [41, 21.5, 60, 126, 0.651, 6.5],
    "ralph-lauren|*": [30, 14, 50, 600, 0.6, 6],
    "stone-island|hoodies": [120, 70, 190, 40, 0.5, 9],
  },
  auth: { lr: { price_far_below: 0.35, price_below: 0.75, suspicious_text: 0.12 }, suspicious: [["\\breplica\\b", "replica"], ["\\ba{3,}\\b(?:\\s*quality)?", "AAA"]], max_p: 0.97 },
};
const M = Q.compileMarket(MARKET);
const card = (over) => ({ vinted_id: "1", title: "Polo Ralph Lauren blu", brand: "Ralph Lauren", price: 10, condition: "very_good", status: "active", ...over });

test("net margin uses the same formulas as the server", () => {
  // Reference values from app.profit.calculator.profit_for with the same costs.
  assert.equal(Q.netMargin(20, 41, null, COSTS).net, 15.31);
  assert.equal(Q.netMargin(20, 36.08, 1.7, COSTS).net, 10.39);
  assert.equal(Q.netMargin(137, 300, null, { ...COSTS, selling_fee_pct: 0.05, payment_fee_fixed: 0.3 }).net, 136.16);
});

test("brand and category are recognised from the card, longest keyword first", () => {
  assert.equal(Q.findBrand(card({ brand: "", title: "Felpa con cappuccio Polo Ralph Lauren" }), M), "ralph-lauren");
  assert.equal(Q.findCategory("Felpa con cappuccio Polo Ralph Lauren", "ralph-lauren", M), "hoodies");
  assert.equal(Q.findCategory("T-shirt Ralph Lauren", "ralph-lauren", M), "t-shirts"); // never also "shirt"
  assert.equal(Q.findCategory("Ralph Lauren custom slim fit", "ralph-lauren", M), "polo-shirts"); // from the model line
  assert.equal(Q.findBrand(card({ brand: "", title: "Maglia girl power" }), M), null); // "rl" alone never matches inside words
});

test("quick estimate: resale from realized prices, risk-adjusted profit, reason", () => {
  const e = Q.quickEstimate(card({ title: "Felpa con cappuccio Ralph Lauren", price: 20, condition: "good" }), M);
  assert.equal(e.category, "hoodies");
  assert.equal(e.resale_expected, 36.08); // 41 x 0.88 (condition)
  assert.equal(e.net_margin, Q.netMargin(20, 36.08, null, COSTS).net);
  assert.ok(e.authenticity_probability > 0.8 && e.authenticity_probability <= 0.97);
  assert.ok(Math.abs(e.risk_adjusted_profit - e.net_margin * 0.651 * e.authenticity_probability) < 0.02);
  assert.match(e.reason, /sotto i venduti simili/);
});

test("a fake-looking price or replica wording lowers P(authentic)", () => {
  const fair = Q.quickEstimate(card({ brand: "Stone Island", title: "Felpa con cappuccio Stone Island", price: 90 }), M);
  const cheap = Q.quickEstimate(card({ brand: "Stone Island", title: "Felpa con cappuccio Stone Island", price: 30 }), M);
  const replica = Q.quickEstimate(card({ brand: "Stone Island", title: "Felpa con cappuccio Stone Island replica AAA", price: 90 }), M);
  assert.ok(cheap.authenticity_probability < fair.authenticity_probability);
  assert.ok(replica.authenticity_probability < 0.2);
  assert.match(cheap.reason, /prezzo troppo basso/);
});

test("no sales data -> no number, and the best card is the highest positive risk-adjusted profit", () => {
  assert.equal(Q.quickEstimate(card({ brand: "Zara", title: "Camicia Zara" }), M).insufficient, "troppe poche vendite di articoli simili");
  assert.ok(Q.quickEstimate(card({ brand: "Sconosciuto", title: "Giacca" }), M).insufficient);
  assert.ok(Q.quickEstimate(card({}), null).insufficient);
  const list = [
    Q.quickEstimate(card({ vinted_id: "a", price: 30 }), M), // loss
    Q.quickEstimate(card({ vinted_id: "b", price: 8 }), M),
    Q.quickEstimate(card({ vinted_id: "c", title: "Felpa con cappuccio Ralph Lauren", price: 12 }), M),
    { ...Q.quickEstimate(card({ vinted_id: "d", title: "Felpa con cappuccio Ralph Lauren", price: 5 }), M), status: "sold" },
  ];
  assert.equal(Q.bestOf(list).vinted_id, "c");
  assert.equal(Q.bestOf([list[0]]), null); // nothing worth buying: no highlight
});
