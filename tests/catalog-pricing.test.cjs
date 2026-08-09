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

test("never prices a PV package below the capacity required by annual usage", () => {
  const raised = pricing.estimateCatalogBudgetForUsage({
    type: "pv", areaM2: 140, annualUsageKwh: 6000, selectedPvKwp: 5, batteryKwh: 10,
  });
  assert.equal(raised.available, true);
  assert.equal(raised.effectivePvKwp, 8);
  assert.equal(raised.pv.package.capacity, 8);
  assert.equal(raised.low, 56000);

  const retained = pricing.estimateCatalogBudgetForUsage({
    type: "pv", areaM2: 140, annualUsageKwh: 4500, selectedPvKwp: 10, batteryKwh: 10,
  });
  assert.equal(retained.effectivePvKwp, 10);
  assert.equal(retained.low, 63000);

  const outside = pricing.estimateCatalogBudgetForUsage({
    type: "pv", areaM2: 140, annualUsageKwh: 10000, selectedPvKwp: 10, batteryKwh: 10,
  });
  assert.equal(outside.available, false);
  assert.equal(outside.demandOutsideCatalog, true);
  assert.equal(outside.recommendation.requiredCapacity, 10.5);
  assert.equal(outside.low, null);
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

test("adds exact catalogue prices to the existing heat-pump range", () => {
  const result = pricing.estimateCatalogBudget({ type: "combo", areaM2: 140, pvKwp: 6, batteryKwh: 10 });
  assert.deepEqual([result.low, result.high], [89200, 103400]);
});

test("combo sizing includes estimated future heat-pump electricity demand", () => {
  assert.equal(pricing.estimateHeatPumpElectricityKwh(140), 5035);
  const result = pricing.estimateCatalogBudgetForUsage({
    type: "combo", areaM2: 140, annualUsageKwh: 4500, selectedPvKwp: 5, batteryKwh: 10,
  });
  assert.equal(result.designUsageKwh, 9535);
  assert.equal(result.effectivePvKwp, 10);
  assert.equal(result.pv.package.capacity, 10);
  assert.deepEqual([result.low, result.high], [102200, 116400]);
});

test("requires an individual quote outside the available catalogue", () => {
  assert.equal(pricing.estimateCatalogBudget({ type: "pv", areaM2: 140, pvKwp: 10.5, batteryKwh: 10 }).available, false);
  assert.equal(pricing.estimateCatalogBudget({ type: "pv", areaM2: 140, pvKwp: 6, batteryKwh: 21 }).available, false);
});

test("all localized homepages load the catalogue model and contain valid inline JavaScript", () => {
  const root = path.resolve(__dirname, "..");
  for (const relative of ["index.html", "en/index.html", "de/index.html"]) {
    const html = fs.readFileSync(path.join(root, relative), "utf8");
    assert.match(html, /assets\/catalog-pricing\.js/);
    assert.match(html, /id="estimateBasis"/);
    assert.match(html, /estimateCatalogBudgetForUsage/);
    assert.match(html, /demandOutsideCatalog/);
    assert.match(html, /effectivePv/);
    assert.match(html, /<select id="pvRange"/);
    assert.match(html, /<select id="storageRange"/);
    assert.doesNotMatch(html, /<input id="(?:pvRange|storageRange)" type="range"/);
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
