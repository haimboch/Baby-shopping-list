import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
const script = scripts.find((entry) => entry[1].includes("buildShoppingBaskets"))?.[1];
assert.ok(script, "the application JavaScript must exist");

const elements = new Map();

function fakeElement(id) {
  const classes = new Set();
  return {
    id,
    value: "",
    textContent: "",
    innerHTML: "",
    disabled: false,
    dataset: {},
    style: {},
    classList: {
      add: (...values) => values.forEach((value) => classes.add(value)),
      remove: (...values) => values.forEach((value) => classes.delete(value)),
      toggle: (value, force) => {
        const next = force === undefined ? !classes.has(value) : force;
        if (next) classes.add(value);
        else classes.delete(value);
        return next;
      },
      contains: (value) => classes.has(value),
    },
    addEventListener() {},
    setAttribute() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    matches() { return false; },
    replaceChildren() {},
    scrollIntoView() {},
    focus() {},
    showModal() { this.open = true; },
    close() { this.open = false; },
  };
}

const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, fakeElement(id));
    return elements.get(id);
  },
  querySelectorAll() { return []; },
  querySelector() { return null; },
  createElement: fakeElement,
  body: { appendChild() {} },
  head: { appendChild() {} },
};

let scopedPriceRows = [];

const fakeSupabase = {
  auth: {
    async getSession() { return { data: { session: null } }; },
  },
  from(table) {
    assert.equal(table, "baby_normalized_prices");
    const filters = {};
    return {
      select() { return this; },
      in(column, values) { filters[column] = values; return this; },
      not() { return this; },
      eq() { return this; },
      order() { return this; },
      async range(start, end) {
        const rows = scopedPriceRows.filter((row) =>
          (!filters.branch_code || filters.branch_code.includes(String(row.branch_code)))
          && (!filters.need_key || filters.need_key.includes(row.need_key))
          && (!filters.chain_id || filters.chain_id.includes(row.chain_id)),
        );
        return { data: rows.slice(start, end + 1), error: null };
      },
    };
  },
};

const window = {
  supabase: { createClient: () => fakeSupabase },
  addEventListener() {},
  scrollTo() {},
};

const context = vm.createContext({
  console,
  document,
  window,
  navigator: {},
  location: { href: "https://example.test/", hash: "" },
  URL,
  setTimeout,
  clearTimeout,
  CSS: { escape: (value) => value },
});

vm.runInContext(
  `${script}
globalThis.testApi = {
  buildShoppingBaskets,
  shoppingBasketCard,
  fetchScopedPriceRows,
  matchingBasketRows,
  isOnlineBranch,
  inventoryPayload,
  authErrorMessage,
  setState(next) {
    catalog = next.catalog || [];
    household = next.household || { id: "house-1" };
    session = next.session || { user: { id: "user-1" } };
  }
};`,
  context,
  { filename: "index.html" },
);

const api = context.testApi;
const fresh = new Date().toISOString();
const stale = new Date(Date.now() - 72 * 60 * 60 * 1000).toISOString();

const branches = [
  {
    chain_id: "rami_levy",
    branch_code: "001",
    branch_name: "רמי לוי מרכז",
    latitude: 31.52,
    longitude: 34.59,
    distance_km: 1.2,
  },
  {
    chain_id: "yochananof",
    branch_code: "002",
    branch_name: "יוחננוף העיר",
    latitude: 31.53,
    longitude: 34.60,
    distance_km: 2.1,
  },
];

const diapers = {
  id: "p-diapers",
  name: "טיטולים",
  need_key: "diapers",
  dimension_type: "size",
  dimension_value: "4",
  preferred_barcode: "d-preferred",
  allow_alternatives: true,
};

const formula = {
  id: "p-formula",
  name: "תמ״ל",
  need_key: "formula",
  dimension_type: "stage",
  dimension_value: "2",
  preferred_barcode: "f-preferred",
  allow_alternatives: false,
};

const wipes = {
  id: "p-wipes",
  name: "מגבונים",
  need_key: "wipes",
  dimension_type: "none",
  dimension_value: null,
  preferred_barcode: null,
  allow_alternatives: true,
};

api.setState({
  catalog: [
    {
      barcode: "d-preferred",
      need_key: "diapers",
      dimension_value: "4",
      brand: "האגיס",
      active: true,
    },
    {
      barcode: "f-preferred",
      need_key: "formula",
      dimension_value: "2",
      brand: "מטרנה",
      active: true,
    },
  ],
});

function quote(branch, values) {
  return {
    chain_id: branch.chain_id,
    branch_code: branch.branch_code,
    source_updated_at: fresh,
    last_seen_at: fresh,
    ...values,
  };
}

const baseRows = [
  quote(branches[0], {
    barcode: "d-alternative-a",
    need_key: "diapers",
    dimension_value: "4",
    brand: "פמפרס",
    product_name: "חיתולים לתינוק מידה 4",
    effective_price: 25,
  }),
  quote(branches[0], {
    barcode: "f-preferred",
    need_key: "formula",
    dimension_value: "2",
    brand: "מטרנה",
    product_name: "מטרנה חלבי שלב 2",
    effective_price: 45,
  }),
  quote(branches[0], {
    barcode: "w-cheap-a",
    need_key: "wipes",
    brand: "לייף",
    product_name: "מגבונים לתינוק",
    effective_price: 6,
  }),
  quote(branches[0], {
    barcode: "w-expensive-a",
    need_key: "wipes",
    brand: "האגיס",
    product_name: "מגבונים לתינוק",
    effective_price: 9,
  }),
  quote(branches[1], {
    barcode: "d-preferred",
    need_key: "diapers",
    dimension_value: "4",
    brand: "האגיס",
    product_name: "חיתולים לתינוק מידה 4",
    effective_price: 35,
  }),
  quote(branches[1], {
    barcode: "d-alternative-b",
    need_key: "diapers",
    dimension_value: "4",
    brand: "פמפרס",
    product_name: "חיתולים לתינוק מידה 4",
    effective_price: 20,
  }),
  quote(branches[1], {
    barcode: "f-preferred",
    need_key: "formula",
    dimension_value: "2",
    brand: "מטרנה",
    product_name: "מטרנה חלבי שלב 2",
    effective_price: 55,
  }),
  quote(branches[1], {
    barcode: "w-cheap-b",
    need_key: "wipes",
    brand: "האגיס",
    product_name: "מגבונים לתינוק",
    effective_price: 8,
  }),
];

{
  const baskets = api.buildShoppingBaskets([diapers, formula, wipes], branches, baseRows);
  assert.equal(baskets.length, 2, "both nearby supermarkets must produce baskets");
  const first = baskets.find((basket) => basket.branch.chain_id === "rami_levy");
  const second = baskets.find((basket) => basket.branch.chain_id === "yochananof");
  assert.equal(first.availableCount, 3);
  assert.equal(first.replacedCount, 1, "unavailable preferred diapers should be substituted");
  assert.equal(first.chosen.find((item) => item.product.id === wipes.id).selected.effective_price, 6);
  assert.equal(first.chosen.find((item) => item.product.id === formula.id).selected.barcode, "f-preferred");
  assert.equal(second.savings, 15, "cheaper non-formula brands must produce potential savings");
  process.stdout.write("✓ Full baskets include safe substitutions and all-brand pricing.\n");
}

{
  const rows = baseRows.filter(
    (row) => !(row.chain_id === "rami_levy" && row.barcode === "f-preferred"),
  );
  rows.push(quote(branches[0], {
    barcode: "f-forbidden-alternative",
    need_key: "formula",
    dimension_value: "2",
    brand: "סימילאק",
    product_name: "סימילאק שלב 2",
    effective_price: 19,
  }));
  const baskets = api.buildShoppingBaskets([diapers, formula, wipes], branches, rows);
  const partial = baskets.find((basket) => basket.branch.chain_id === "rami_levy");
  const missing = partial.chosen.find((item) => item.product.id === formula.id);
  assert.equal(partial.availableCount, 2, "a partial basket must remain visible");
  assert.equal(partial.missingCount, 1);
  assert.equal(missing.status, "missing", "formula must never be silently substituted");
  assert.equal(missing.suggestions.length, 1);
  assert.equal(missing.suggestions[0].row.barcode, "f-preferred");
  assert.equal(partial.projectedTotal, 86);
  process.stdout.write("✓ Partial baskets stay visible and only suggest the exact formula.\n");
}

{
  const rows = baseRows.map((row) =>
    row.barcode === "f-preferred" && row.chain_id === "rami_levy"
      ? { ...row, source_updated_at: stale, last_seen_at: stale }
      : row,
  );
  const baskets = api.buildShoppingBaskets([formula], branches, rows);
  const partial = baskets.find((basket) => basket.branch.chain_id === "rami_levy");
  assert.equal(partial.missingCount, 1, "stale prices must not count as available products");
  assert.equal(partial.chosen[0].suggestions[0].branch.chain_id, "yochananof");
  process.stdout.write("✓ Expired prices are excluded without suppressing the supermarket.\n");
}

{
  const diaperPayload = api.inventoryPayload("diapers", "4");
  const formulaPayload = api.inventoryPayload("formula", "2", "f-preferred");
  assert.equal(diaperPayload.preferred_barcode, null);
  assert.equal(diaperPayload.allow_alternatives, true);
  assert.equal(diaperPayload.is_active, true);
  assert.equal(formulaPayload.preferred_barcode, "f-preferred");
  assert.equal(formulaPayload.allow_alternatives, false);
  assert.equal(api.authErrorMessage({ message: "Invalid login credentials" }, "login"), "האימייל או הסיסמה אינם נכונים.");
  process.stdout.write("✓ Brand defaults, formula safeguards and clear login errors are preserved.\n");
}

{
  const superPharm = {
    chain_id: "super_pharm",
    branch_code: "sp-sderot",
    branch_name: "סופר-פארם שדרות",
    latitude: 31.525,
    longitude: 34.595,
    distance_km: 0.8,
  };
  const onlineEstimate = quote(superPharm, {
    barcode: "d-preferred",
    need_key: "diapers",
    dimension_value: "4",
    brand: "האגיס",
    product_name: "האגיס חיתולים מידה 4",
    effective_price: 29.9,
    online_price_reference: true,
    in_store_price_verified: false,
    in_store_stock_verified: false,
  });
  scopedPriceRows = [{
    ...onlineEstimate,
    branch_code: "online",
    online_price_reference: undefined,
  }];
  const scoped = await api.fetchScopedPriceRows([diapers], [superPharm]);
  assert.equal(scoped.error, null);
  assert.equal(scoped.data.length, 1);
  assert.equal(scoped.data[0].branch_code, "sp-sderot");
  assert.equal(scoped.data[0].online_price_reference, true);
  assert.equal(scoped.data[0].in_store_stock_verified, false);

  scopedPriceRows.push({
    ...onlineEstimate,
    effective_price: 35,
    online_price_reference: false,
  });
  const physicalFirst = await api.fetchScopedPriceRows([diapers], [superPharm]);
  assert.equal(physicalFirst.data.length, 1);
  assert.equal(physicalFirst.data[0].effective_price, 35);

  scopedPriceRows[1] = {
    ...scopedPriceRows[1],
    source_updated_at: stale,
    last_seen_at: stale,
  };
  const staleFallsBackToOnline = await api.fetchScopedPriceRows([diapers], [superPharm]);
  assert.equal(staleFallsBackToOnline.data.length, 2);
  assert.equal(
    staleFallsBackToOnline.data.filter((row) => row.online_price_reference).length,
    1,
  );

  const baskets = api.buildShoppingBaskets([diapers], [superPharm], [onlineEstimate]);
  assert.equal(baskets[0].onlineEstimate, true);
  const card = api.shoppingBasketCard(baskets[0], 0, 1);
  assert.match(card, /אומדן לפי מחיר אונליין/);
  assert.match(card, /המחיר והמלאי בסניף הקרוב לא אומתו/);
  assert.equal(api.isOnlineBranch(superPharm), false);
  assert.equal(api.isOnlineBranch({chain_id: "super_pharm", branch_code: "online"}), true);
  assert.equal(api.isOnlineBranch({
    chain_id: "super_pharm", branch_code: "5f186c4390ae893cc6e86587",
  }), true);
  process.stdout.write("✓ Super-Pharm online estimates are transparent and never claim verified store stock.\n");
}

process.stdout.write("✓ All v0.47–v0.50 frontend behavior tests passed.\n");
