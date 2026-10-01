// Lock for the 2026-10-01 ad-price drift: the city page's house ad said
// "From $50/mo" while the map sidebar's said "From $75/mo" -- two prices
// typed into data/sponsors.json, neither tied to the advertise.html rate
// card. Prices now live only in data/pricing.json; house ads name a slot
// (price_slot) and the renderer appends that slot's floor.
//
// There are two renderers -- index.html's inline copy (map sidebar, home,
// detail panel) and /js/sponsors.js (city pages) -- so this renders every
// priced house ad through BOTH and asserts each quotes pricing.json and that
// they agree. Same "extract the function from index.html into a vm" move as
// tests/test_index_render_escaping.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(__dirname, "..");
const read = (p) => readFileSync(path.join(repoRoot, p), "utf8");

const SPONSORS = JSON.parse(read("data/sponsors.json"));
const PRICING = JSON.parse(read("data/pricing.json"));
const PRICED = SPONSORS.filter((s) => s.price_slot);

function between(src, start, end, label) {
  const i = src.indexOf(start);
  if (i < 0) throw new Error(`index.html: start marker for ${label} not found`);
  const j = src.indexOf(end, i + start.length);
  if (j < 0) throw new Error(`index.html: end marker for ${label} not found`);
  return src.slice(i, j);
}

const fakeStorage = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
};

// index.html: escapeHTML + everything from _haversineMi through renderSponsor.
const INDEX_SRC = (() => {
  const html = read("index.html");
  const esc = between(html, "function escapeHTML(s) {", "\n}\n", "escapeHTML") + "\n}\n";
  const block = between(html, "function _haversineMi(", "\nasync function loadCity(", "sponsor block");
  return esc + "\n" + block;
})();

function renderViaIndex(sponsors, pricing, slot, ctx) {
  const sandbox = {
    SPONSORS: sponsors,
    PRICING: pricing,
    state: { city: ctx.city || null, oversizedOnly: false },
    document: { addEventListener() {}, getElementById() { return null; } },
    localStorage: fakeStorage(),
    location: { origin: "https://willifit.ai" },
    URL,
  };
  vm.createContext(sandbox);
  vm.runInContext(INDEX_SRC, sandbox);
  return sandbox.renderSponsor(slot, ctx);
}

async function renderViaModule(sponsors, pricing, slot, ctx) {
  const files = { "/data/sponsors.json": sponsors, "/data/pricing.json": pricing };
  const sandbox = {
    window: {},
    localStorage: fakeStorage(),
    location: { origin: "https://willifit.ai" },
    URL,
    fetch: async (u) =>
      files[u] == null
        ? { ok: false, json: async () => null }
        : { ok: true, json: async () => files[u] },
  };
  vm.createContext(sandbox);
  vm.runInContext(read("js/sponsors.js"), sandbox);
  await sandbox.window.WillIFitSponsors.load();
  return sandbox.window.WillIFitSponsors.renderSponsor(slot, ctx);
}

const desc = (html) => {
  const m = html.match(/<div class="sponsor-desc">([^<]*)<\/div>/);
  assert.ok(m, "rendered card has no sponsor-desc: " + html);
  return m[1];
};
const floorOf = (key) => PRICING.slots.find((p) => p.key === key).from;
const ctxFor = (s) => ({ city: "las-vegas-nv", oversized_filter: !!(s.audience && s.audience.oversized_only) });

test("sponsors.json types no prices; every price_slot exists in pricing.json", () => {
  assert.doesNotMatch(read("data/sponsors.json"), /\$\s?\d/);
  assert.ok(PRICED.length >= 3, "sanity: sidebar, city-page and detail house ads are priced");
  for (const s of PRICED) {
    assert.ok(PRICING.slots.some((p) => p.key === s.price_slot), `${s.id}: unknown price_slot ${s.price_slot}`);
  }
});

test("map sidebar and city page house ads quote the same pricing.json floor", async () => {
  const sidebar = SPONSORS.find((s) => s.id === "house-sidebar-city");
  const cityHero = SPONSORS.find((s) => s.id === "house-city-hero");
  const viaIndex = desc(renderViaIndex([sidebar], PRICING, "sidebar", { city: "las-vegas-nv" }));
  const viaModule = desc(await renderViaModule([cityHero], PRICING, "city_hero", { city: "las-vegas-nv" }));
  assert.ok(viaIndex.endsWith(` From $${floorOf("sidebar")}/mo.`), viaIndex);
  assert.ok(viaModule.endsWith(` From $${floorOf("city_hero")}/mo.`), viaModule);
});

test("both renderers produce identical copy for every priced house ad", async () => {
  for (const s of PRICED) {
    const slot = s.tier[0];
    const a = desc(renderViaIndex([s], PRICING, slot, ctxFor(s)));
    const b = desc(await renderViaModule([s], PRICING, slot, ctxFor(s)));
    assert.equal(a, b, `${s.id}: index.html and js/sponsors.js disagree`);
    assert.ok(a.endsWith(` From $${floorOf(s.price_slot)}/mo.`), `${s.id}: ${a}`);
  }
});

test("no pricing file or an unknown slot renders no price, never a wrong one", async () => {
  const s = SPONSORS.find((x) => x.id === "house-city-hero");
  const bogus = { ...s, price_slot: "nope" };
  const bad = { slots: [{ key: "city_hero", from: "75<b>" }] };
  for (const [sp, pr] of [[s, null], [bogus, PRICING], [s, bad]]) {
    for (const out of [
      desc(renderViaIndex([sp], pr, "city_hero", { city: "x" })),
      desc(await renderViaModule([sp], pr, "city_hero", { city: "x" })),
    ]) {
      assert.equal(out, s.desc.replace(/'/g, "&#39;"));
    }
  }
});
