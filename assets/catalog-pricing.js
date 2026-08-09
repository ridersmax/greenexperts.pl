(function (root) {
  "use strict";

  // Public snapshot of the CRM "Pakiet pod klucz" catalogue.
  // PV prices: confirmed from 2026-07-22. Battery prices: market benchmark from 2026-07-26.
  const PV_PACKAGES = Object.freeze([
    Object.freeze({ capacity: 3, price: 15900, sku: "GE-PV-3-STANDARD" }),
    Object.freeze({ capacity: 4, price: 19900, sku: "GE-PV-4-STANDARD" }),
    Object.freeze({ capacity: 5, price: 22900, sku: "GE-PV-5-STANDARD" }),
    Object.freeze({ capacity: 6, price: 26900, sku: "GE-PV-6-STANDARD" }),
    Object.freeze({ capacity: 8, price: 32900, sku: "GE-PV-8-STANDARD" }),
    Object.freeze({ capacity: 10, price: 39900, sku: "GE-PV-10-STANDARD" }),
  ]);

  const BATTERY_PACKAGES = Object.freeze([
    Object.freeze({ capacity: 5, price: 17500, sku: "GE-BAT-5-STANDARD" }),
    Object.freeze({ capacity: 10, price: 23100, sku: "GE-BAT-10-STANDARD" }),
    Object.freeze({ capacity: 15, price: 28900, sku: "GE-BAT-15-STANDARD" }),
    Object.freeze({ capacity: 20, price: 37600, sku: "GE-BAT-20-STANDARD" }),
  ]);

  const DEFAULT_PV_YIELD_KWH_PER_KWP = 980;
  const DEFAULT_HEAT_DEMAND_KWH_PER_M2 = 105;
  const DEFAULT_HOT_WATER_DEMAND_KWH = 4 * 850;
  const DEFAULT_HEAT_PUMP_SCOP = 3.72 - (45 - 40) * 0.025;

  const finiteNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  };

  const roundToHundreds = (value) => Math.round(finiteNumber(value) / 100) * 100;

  function selectPackage(requestedCapacity, packages, oversizing, allowZero = false) {
    const requested = Math.max(0, finiteNumber(requestedCapacity));
    if (allowZero && requested === 0) {
      return Object.freeze({ available: true, requestedCapacity: 0, package: null, price: 0, mode: "none" });
    }

    const maximumCapacity = Math.max(
      requested + oversizing.absolute,
      requested * oversizing.relative,
    );
    const selected = packages.find((item) => (
      item.capacity >= requested && item.capacity <= maximumCapacity + Number.EPSILON
    ));

    if (!selected) {
      return Object.freeze({ available: false, requestedCapacity: requested, package: null, price: null, mode: "individual" });
    }

    return Object.freeze({
      available: true,
      requestedCapacity: requested,
      package: selected,
      price: selected.price,
      mode: selected.capacity === requested ? "exact" : "next",
    });
  }

  function selectPvPackage(capacityKwp) {
    return selectPackage(capacityKwp, PV_PACKAGES, { absolute: 0.5, relative: 1.25 });
  }

  function selectBatteryPackage(capacityKwh) {
    return selectPackage(capacityKwh, BATTERY_PACKAGES, { absolute: 5, relative: 1.5 }, true);
  }

  function recommendPvForUsage(annualUsageKwh) {
    const annualUsage = Math.max(0, finiteNumber(annualUsageKwh));
    const requiredCapacity = Math.ceil((annualUsage / DEFAULT_PV_YIELD_KWH_PER_KWP) * 2) / 2;
    let selection = selectPvPackage(requiredCapacity);
    if (!selection.available && requiredCapacity > 0 && requiredCapacity < PV_PACKAGES[0].capacity) {
      selection = Object.freeze({
        available: true,
        requestedCapacity: requiredCapacity,
        package: PV_PACKAGES[0],
        price: PV_PACKAGES[0].price,
        mode: "catalog-minimum",
      });
    }
    return Object.freeze({
      annualUsage,
      requiredCapacity,
      ...selection,
    });
  }

  // Simplified homepage estimate matching the CRM defaults: average insulation,
  // four residents, mixed emitters and a 45 °C supply temperature.
  function estimateHeatPumpElectricityKwh(areaM2) {
    const area = Math.max(0, finiteNumber(areaM2));
    return Math.round((
      area * DEFAULT_HEAT_DEMAND_KWH_PER_M2 + DEFAULT_HOT_WATER_DEMAND_KWH
    ) / DEFAULT_HEAT_PUMP_SCOP);
  }

  function heatPumpBudget(areaM2) {
    const area = Math.max(0, finiteNumber(areaM2));
    return Object.freeze({
      low: roundToHundreds(32900 + area * 45),
      high: roundToHundreds(42900 + area * 75),
    });
  }

  function estimateCatalogBudget({ type, areaM2, pvKwp, batteryKwh }) {
    const heat = heatPumpBudget(areaM2);
    if (type === "heat") {
      return Object.freeze({ available: true, type, low: heat.low, high: heat.high, pv: null, battery: null, catalogTotal: 0 });
    }

    const pv = selectPvPackage(pvKwp);
    const battery = selectBatteryPackage(batteryKwh);
    if (!pv.available || !battery.available) {
      return Object.freeze({ available: false, type, low: null, high: null, pv, battery, catalogTotal: null });
    }

    const catalogTotal = pv.price + battery.price;
    if (type === "pv") {
      return Object.freeze({ available: true, type, low: catalogTotal, high: catalogTotal, pv, battery, catalogTotal });
    }

    return Object.freeze({
      available: true,
      type,
      low: roundToHundreds(heat.low + catalogTotal),
      high: roundToHundreds(heat.high + catalogTotal),
      pv,
      battery,
      catalogTotal,
    });
  }

  function estimateCatalogBudgetForUsage({ type, areaM2, annualUsageKwh, selectedPvKwp, batteryKwh }) {
    const selectedPv = Math.max(0, finiteNumber(selectedPvKwp));
    const heatPumpElectricityKwh = type === "combo" ? estimateHeatPumpElectricityKwh(areaM2) : 0;
    const designUsageKwh = Math.max(0, finiteNumber(annualUsageKwh)) + heatPumpElectricityKwh;
    const recommendation = recommendPvForUsage(designUsageKwh);
    const demandOutsideCatalog = type !== "heat" && !recommendation.available;
    const effectivePvKwp = type === "heat" || demandOutsideCatalog
      ? selectedPv
      : Math.max(selectedPv, recommendation.package.capacity);
    const result = estimateCatalogBudget({ type, areaM2, pvKwp: effectivePvKwp, batteryKwh });

    return Object.freeze({
      ...result,
      available: demandOutsideCatalog ? false : result.available,
      low: demandOutsideCatalog ? null : result.low,
      high: demandOutsideCatalog ? null : result.high,
      recommendation,
      demandOutsideCatalog,
      effectivePvKwp,
      heatPumpElectricityKwh,
      designUsageKwh,
    });
  }

  const api = Object.freeze({
    catalogVersion: "2026-07-26",
    pvPackages: PV_PACKAGES,
    batteryPackages: BATTERY_PACKAGES,
    defaultPvYieldKwhPerKwp: DEFAULT_PV_YIELD_KWH_PER_KWP,
    selectPvPackage,
    selectBatteryPackage,
    recommendPvForUsage,
    estimateHeatPumpElectricityKwh,
    heatPumpBudget,
    estimateCatalogBudget,
    estimateCatalogBudgetForUsage,
  });

  root.GreenExpertsCatalogPricing = api;
  if (typeof module === "object" && module.exports) module.exports = api;
}(typeof globalThis === "object" ? globalThis : this));
