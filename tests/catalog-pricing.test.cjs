const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const pricing = require("../assets/catalog-pricing.js");

test("uses exact CRM catalogue prices for every PV package", () => {
  const expected = new Map([[3, 15900], [4, 19900], [5, 22900], [6, 26900], [8, 32900], [10, 39900]]);
  for (const [capacity, price] of expected) {
    const quote = pricing.selectPvPackage(capacity);
    assert.equal(quote.available, true);
    assert.equal(quote.package.capacity, capacity);
    assert.equal(quote.price, price);
  }
});

test("uses exact CRM catalogue prices for every battery package", () => {
  const expected = new Map([[5, 17500], [10, 23100], [15, 28900], [20, 37600]]);
  for (const [capacity, price] of expected) {
    const quote = pricing.selectBatteryPackage(capacity);
    assert.equal(quote.available, true);
    assert.equal(quote.package.capacity, capacity);
    assert.equal(quote.price, price);
  }
});

test("uses the same automatically selected heat-pump variants and installed prices as CRM", () => {
  const expected = [
    [40, 4, 31892.67, 47100],
    [61, 6, 34378.50, 50400],
    [100, 8, 37155.22, 54000],
    [140, 10, 39270.82, 57000],
    [160, 12, 41807.70, 60300],
    [200, 14, 59316.14, 78700],
    [220, 16, 50147.10, 70400],
  ];
  for (const [areaM2, capacity, equipmentPrice, totalPrice] of expected) {
    const budget = pricing.heatPumpBudget(areaM2);
    assert.equal(budget.available, true);
    assert.equal(budget.package.capacity, capacity);
    assert.equal(budget.equipmentPrice, equipmentPrice);
    assert.equal(budget.totalPrice, totalPrice);
  }
  assert.equal(pricing.heatPumpBudget(0).available, false);
  assert.equal(pricing.heatPumpBudget(250).available, false);
  assert.equal(pricing.heatPumpBudget(460).available, false);
});

test("selects the smallest sufficient package using CRM oversizing limits", () => {
  assert.equal(pricing.selectPvPackage(2).available, false);
  assert.equal(pricing.selectPvPackage(2.5).package.capacity, 3);
  assert.equal(pricing.selectPvPackage(6.5).package.capacity, 8);
  assert.equal(pricing.selectPvPackage(8.5).package.capacity, 10);
  assert.equal(pricing.selectPvPackage(10.5).available, false);

  assert.equal(pricing.selectBatteryPackage(0).package, null);
  assert.equal(pricing.selectBatteryPackage(1).package.capacity, 5);
  assert.equal(pricing.selectBatteryPackage(6).package.capacity, 10);
  assert.equal(pricing.selectBatteryPackage(11).package.capacity, 15);
  assert.equal(pricing.selectBatteryPackage(16).package.capacity, 20);
  assert.equal(pricing.selectBatteryPackage(21).available, false);
});

test("uses the CRM default PV yield and makes annual usage drive the minimum package", () => {
  assert.equal(pricing.defaultPvYieldKwhPerKwp, 980);

  const typical = pricing.recommendPvForUsage(4500);
  assert.equal(typical.requiredCapacity, 5);
  assert.equal(typical.package.capacity, 5);

  const raised = pricing.recommendPvForUsage(6000);
  assert.equal(raised.requiredCapacity, 6.5);
  assert.equal(raised.package.capacity, 8);

  assert.equal(pricing.recommendPvForUsage(9800).package.capacity, 10);
  assert.equal(pricing.recommendPvForUsage(10000).available, false);
  const minimum = pricing.recommendPvForUsage(1000);
  assert.equal(minimum.available, true);
  assert.equal(minimum.package.capacity, 3);
  assert.equal(minimum.mode, "catalog-minimum");
});

test("keeps catalogue pricing tied to the selected package and reports demand separately", () => {
  const selectedFive = pricing.estimateCatalogBudgetForUsage({
    type: "pv", areaM2: 140, annualUsageKwh: 6000, selectedPvKwp: 5, batteryKwh: 10,
  });
  assert.equal(selectedFive.available, true);
  assert.equal(selectedFive.effectivePvKwp, 5);
  assert.equal(selectedFive.pv.package.capacity, 5);
  assert.equal(selectedFive.low, 46000);
  assert.equal(selectedFive.recommendation.package.capacity, 8);
  assert.equal(selectedFive.recommendationOutsideCatalog, false);

  const selectedTen = pricing.estimateCatalogBudgetForUsage({
    type: "pv", areaM2: 140, annualUsageKwh: 4500, selectedPvKwp: 10, batteryKwh: 10,
  });
  assert.equal(selectedTen.effectivePvKwp, 10);
  assert.equal(selectedTen.low, 63000);
  assert.equal(selectedTen.recommendation.package.capacity, 5);

  const demandAboveCatalogue = pricing.estimateCatalogBudgetForUsage({
    type: "pv", areaM2: 140, annualUsageKwh: 10000, selectedPvKwp: 10, batteryKwh: 10,
  });
  assert.equal(demandAboveCatalogue.available, true);
  assert.equal(demandAboveCatalogue.recommendationOutsideCatalog, true);
  assert.equal(demandAboveCatalogue.recommendation.requiredCapacity, 10.5);
  assert.equal(demandAboveCatalogue.low, 63000);
});

test("prices PV and battery combinations as exact catalogue sums", () => {
  const pvRows = [
    [3, [15900, 33400, 39000, 44800, 53500]],
    [4, [19900, 37400, 43000, 48800, 57500]],
    [5, [22900, 40400, 46000, 51800, 60500]],
    [6, [26900, 44400, 50000, 55800, 64500]],
    [8, [32900, 50400, 56000, 61800, 70500]],
    [10, [39900, 57400, 63000, 68800, 77500]],
  ];
  const batteryColumns = [0, 5, 10, 15, 20];
  for (const [pvKwp, totals] of pvRows) {
    for (const [index, batteryKwh] of batteryColumns.entries()) {
      const total = totals[index];
      const result = pricing.estimateCatalogBudget({ type: "pv", areaM2: 140, pvKwp, batteryKwh });
      assert.equal(result.available, true);
      assert.equal(result.low, total);
      assert.equal(result.high, total);
    }
  }

  for (const [pvKwp, batteryKwh, total] of [[6.5, 7, 56000]]) {
    const result = pricing.estimateCatalogBudget({ type: "pv", areaM2: 140, pvKwp, batteryKwh });
    assert.equal(result.available, true);
    assert.equal(result.low, total);
    assert.equal(result.high, total);
  }
});

test("adds exact PV and battery catalogue prices to the installed heat-pump catalogue price", () => {
  const result = pricing.estimateCatalogBudget({ type: "combo", areaM2: 140, pvKwp: 6, batteryKwh: 10 });
  assert.deepEqual([result.low, result.high], [107000, 107000]);
});

test("combo sizing includes estimated future heat-pump electricity demand", () => {
  assert.equal(pricing.estimateHeatPumpElectricityKwh(140), 5035);
  const result = pricing.estimateCatalogBudgetForUsage({
    type: "combo", areaM2: 140, annualUsageKwh: 4500, selectedPvKwp: 5, batteryKwh: 10,
  });
  assert.equal(result.designUsageKwh, 9535);
  assert.equal(result.effectivePvKwp, 5);
  assert.equal(result.pv.package.capacity, 5);
  assert.equal(result.recommendation.package.capacity, 10);
  assert.deepEqual([result.low, result.high], [103000, 103000]);
});

test("requires an individual quote outside the available catalogue", () => {
  assert.equal(pricing.estimateCatalogBudget({ type: "pv", areaM2: 140, pvKwp: 10.5, batteryKwh: 10 }).available, false);
  assert.equal(pricing.estimateCatalogBudget({ type: "pv", areaM2: 140, pvKwp: 6, batteryKwh: 21 }).available, false);
  assert.equal(pricing.estimateCatalogBudget({ type: "heat", areaM2: 250, pvKwp: 0, batteryKwh: 0 }).available, false);
});

test("applies a complete public CRM catalogue snapshot and rejects incomplete data", () => {
  const original = {
    version: pricing.catalogVersion,
    products: [
      ...pricing.pvPackages.map((item) => ({ ...item, category: "Fotowoltaika", suggested_price: item.price })),
      ...pricing.batteryPackages.map((item) => ({ ...item, category: "Magazyn energii", suggested_price: item.price })),
      ...pricing.heatPumpPackages.map((item) => ({
        ...item,
        category: "Pompa ciepła",
        suggested_price: item.price,
        tank_capacity_l: item.includesTank ? 185 : 0,
      })),
    ],
  };

  assert.equal(pricing.applyCatalogSnapshot({ version: "incomplete", products: original.products.filter((item) => item.sku !== "GE-PV-10-STANDARD") }), false);
  assert.equal(pricing.catalogVersion, original.version);

  const changed = {
    version: "live-test-1",
    products: original.products.map((item) => {
      if (item.sku === "GE-PV-5-STANDARD") return { ...item, suggested_price: 25000 };
      if (item.sku === "GE-BAT-10-STANDARD") return { ...item, suggested_price: 24000 };
      if (item.sku === "ME-SUZ-SWM100VA-ERSD-VM6E") return { ...item, suggested_price: 40000 };
      return item;
    }),
  };

  try {
    assert.equal(pricing.applyCatalogSnapshot(changed), true);
    assert.equal(pricing.catalogVersion, "live-test-1");
    assert.equal(pricing.selectPvPackage(5).price, 25000);
    assert.equal(pricing.selectBatteryPackage(10).price, 24000);
    assert.equal(pricing.heatPumpBudget(140).equipmentPrice, 40000);
    assert.equal(pricing.estimateCatalogBudget({ type: "pv", areaM2: 140, pvKwp: 5, batteryKwh: 10 }).low, 49000);
  } finally {
    assert.equal(pricing.applyCatalogSnapshot(original), true);
  }
});

test("all localized homepages load the catalogue model and contain valid inline JavaScript", () => {
  const root = path.resolve(__dirname, "..");
  for (const relative of ["index.html", "en/index.html", "de/index.html"]) {
    const html = fs.readFileSync(path.join(root, relative), "utf8");
    assert.match(html, /assets\/catalog-pricing\.js\?v=20260809-2/);
    assert.match(html, /id="estimateBasis"/);
    assert.match(html, /estimateCatalogBudgetForUsage/);
    assert.match(html, /api\/public\/catalog-prices/);
    assert.match(html, /applyCatalogSnapshot/);
    assert.match(html, /syncCatalogPrices/);
    assert.match(html, /catalogIsLive/);
    assert.match(html, /catalogSource/);
    assert.match(html, /2026/);
    assert.match(html, /effectivePv/);
    assert.match(html, /storageRange\.value='0'/);
    assert.match(html, /type==='combo'&&\+storageRange\.value===0/);
    assert.match(html, /<select id="pvRange"/);
    assert.match(html, /<select id="storageRange"/);
    assert.doesNotMatch(html, /<input id="(?:pvRange|storageRange)" type="range"/);
    assert.doesNotMatch(html, /pvRange\.value=String\(effectivePv\)/);
    assert.doesNotMatch(html, /pv\*1900|storage\*1600|pv\*1750|storage\*1450/);

    for (const match of html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)) {
      const attributes = match[1];
      if (/\bsrc=|application\/ld\+json/i.test(attributes)) continue;
      assert.doesNotThrow(() => new vm.Script(match[2], { filename: relative }));
    }
  }
});

test("public PV and battery price labels match the catalogue", () => {
  const root = path.resolve(__dirname, "..");
  const files = [
    "index.html", "fotowoltaika.html", "magazyny-energii.html",
    "en/index.html", "en/fotowoltaika.html", "en/magazyny-energii.html",
    "de/index.html", "de/fotowoltaika.html", "de/magazyny-energii.html",
  ];
  const content = files.map((relative) => fs.readFileSync(path.join(root, relative), "utf8")).join("\n");
  assert.doesNotMatch(content, /14 900|14,900|14\.900|21 900|21,900|21\.900/);
  assert.match(content, /15 900/);
  assert.match(content, /15,900/);
  assert.match(content, /15\.900/);
  assert.match(content, /23 100/);
  assert.match(content, /23,100/);
  assert.match(content, /23\.100/);
});
