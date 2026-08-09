(function (root) {
  "use strict";

  // Public snapshot used until the current CRM "Pakiet pod klucz" catalogue loads.
  // PV prices: confirmed from 2026-07-22. Battery prices: market benchmark from 2026-07-26.
  let PV_PACKAGES = Object.freeze([
    Object.freeze({ capacity: 3, price: 15900, sku: "GE-PV-3-STANDARD" }),
    Object.freeze({ capacity: 4, price: 19900, sku: "GE-PV-4-STANDARD" }),
    Object.freeze({ capacity: 5, price: 22900, sku: "GE-PV-5-STANDARD" }),
    Object.freeze({ capacity: 6, price: 26900, sku: "GE-PV-6-STANDARD" }),
    Object.freeze({ capacity: 8, price: 32900, sku: "GE-PV-8-STANDARD" }),
    Object.freeze({ capacity: 10, price: 39900, sku: "GE-PV-10-STANDARD" }),
  ]);

  let BATTERY_PACKAGES = Object.freeze([
    Object.freeze({ capacity: 5, price: 17500, sku: "GE-BAT-5-STANDARD" }),
    Object.freeze({ capacity: 10, price: 23100, sku: "GE-BAT-10-STANDARD" }),
    Object.freeze({ capacity: 15, price: 28900, sku: "GE-BAT-15-STANDARD" }),
    Object.freeze({ capacity: 20, price: 37600, sku: "GE-BAT-20-STANDARD" }),
  ]);

  // Lowest-priced active CRM variants for each automatically selected heat-pump class.
  // Equipment prices are gross; installation is added with the same defaults as the CRM calculator.
  let HEAT_PUMP_PACKAGES = Object.freeze([
    Object.freeze({ capacity: 4, price: 31892.67, sku: "ME-SUZ-SWM40VA2-ERSD-VM6E", includesTank: false }),
    Object.freeze({ capacity: 6, price: 34378.50, sku: "ME-SUZ-SWM60VA2-ERSD-VM6E", includesTank: false }),
    Object.freeze({ capacity: 8, price: 37155.22, sku: "ME-SUZ-SWM80VA2-ERSD-VM6E", includesTank: false }),
    Object.freeze({ capacity: 10, price: 39270.82, sku: "ME-SUZ-SWM100VA-ERSD-VM6E", includesTank: false }),
    Object.freeze({ capacity: 12, price: 41807.70, sku: "KIT-WC12K9E8", includesTank: false }),
    Object.freeze({ capacity: 14, price: 59316.14, sku: "ME-PUZ-SHWM140YAA-ERSF-YM9E", includesTank: false }),
    Object.freeze({ capacity: 16, price: 50147.10, sku: "KIT-WC16K9E8", includesTank: false }),
  ]);

  const DEFAULT_PV_YIELD_KWH_PER_KWP = 980;
  const DEFAULT_HEAT_LOSS_W_PER_M2 = 72;
  const DEFAULT_HEAT_DEMAND_KWH_PER_M2 = 105;
  const DEFAULT_HOT_WATER_DEMAND_KWH = 4 * 850;
  const DEFAULT_HEAT_PUMP_SCOP = 3.72 - (45 - 40) * 0.025;
  const DEFAULT_DHW_TANK_L = 250;
  const HEAT_PUMP_SIZES_KW = Object.freeze([4, 6, 8, 10, 12, 14, 16, 20, 24, 30]);
  let catalogVersion = "2026-07-26";

  const finiteNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  };

  const roundToHundreds = (value) => Math.round(finiteNumber(value) / 100) * 100;

  function applyCatalogSnapshot(snapshot) {
    const products = Array.isArray(snapshot?.products) ? snapshot.products : [];
    const normalize = (product) => {
      const capacity = Math.max(0, finiteNumber(product?.capacity ?? product?.nominal_power_kw));
      const price = Math.max(0, finiteNumber(product?.suggested_price ?? product?.price));
      const sku = String(product?.sku || "").trim();
      const category = String(product?.category || "").trim();
      return { capacity, price, sku, category, tankCapacityL: Math.max(0, finiteNumber(product?.tank_capacity_l)) };
    };
    const normalized = products.map(normalize).filter((product) => product.capacity > 0 && product.price > 0 && product.sku);
    const sortPackages = (packages) => packages.sort((left, right) => left.capacity - right.capacity || left.price - right.price);
    const pvPackages = sortPackages(normalized
      .filter((product) => product.category === "Fotowoltaika" && product.sku.startsWith("GE-PV-"))
      .map(({ capacity, price, sku }) => Object.freeze({ capacity, price, sku })));
    const batteryPackages = sortPackages(normalized
      .filter((product) => product.category === "Magazyn energii" && product.sku.startsWith("GE-BAT-"))
      .map(({ capacity, price, sku }) => Object.freeze({ capacity, price, sku })));
    const heatPumpPackages = sortPackages(normalized
      .filter((product) => product.category.startsWith("Pompa ciep"))
      .map(({ capacity, price, sku, tankCapacityL }) => Object.freeze({
        capacity,
        price,
        sku,
        includesTank: tankCapacityL > 0,
      })));

    const containsEveryCapacity = (packages, requiredCapacities) => requiredCapacities.every(
      (capacity) => packages.some((item) => item.capacity === capacity),
    );
    if (
      !containsEveryCapacity(pvPackages, [3, 4, 5, 6, 8, 10])
      || !containsEveryCapacity(batteryPackages, [5, 10, 15, 20])
      || !containsEveryCapacity(heatPumpPackages, [4, 6, 8, 10, 12, 14, 16])
    ) return false;
    PV_PACKAGES = Object.freeze(pvPackages);
    BATTERY_PACKAGES = Object.freeze(batteryPackages);
    HEAT_PUMP_PACKAGES = Object.freeze(heatPumpPackages);
    catalogVersion = String(snapshot.version || snapshot.updated_at || catalogVersion);
    return true;
  }

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

  function recommendHeatPumpForArea(areaM2) {
    const area = Math.max(0, finiteNumber(areaM2));
    const designHeatLoadKw = area * DEFAULT_HEAT_LOSS_W_PER_M2 / 1000;
    const targetHeatPumpPowerKw = designHeatLoadKw * 0.92;
    if (area < 30 || area > 1000) {
      return Object.freeze({
        available: false,
        area,
        designHeatLoadKw,
        targetHeatPumpPowerKw,
        recommendedCapacity: null,
        package: null,
        price: null,
        mode: "individual",
      });
    }
    const recommendedCapacity = HEAT_PUMP_SIZES_KW.find((capacity) => capacity >= targetHeatPumpPowerKw);
    if (!recommendedCapacity) {
      return Object.freeze({
        available: false,
        area,
        designHeatLoadKw,
        targetHeatPumpPowerKw,
        recommendedCapacity: null,
        package: null,
        price: null,
        mode: "individual",
      });
    }
    const selection = selectPackage(
      recommendedCapacity,
      HEAT_PUMP_PACKAGES,
      { absolute: 2, relative: 1.5 },
    );
    return Object.freeze({
      area,
      designHeatLoadKw,
      targetHeatPumpPowerKw,
      recommendedCapacity,
      ...selection,
    });
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
    const recommendation = recommendHeatPumpForArea(areaM2);
    if (!recommendation.available) {
      return Object.freeze({
        available: false,
        low: null,
        high: null,
        totalPrice: null,
        equipmentPrice: null,
        installationPrice: null,
        package: null,
        recommendation,
      });
    }
    const heatPumpPackage = recommendation.package;
    const tankPrice = heatPumpPackage.includesTank ? 0 : DEFAULT_DHW_TANK_L * 8;
    const installationPrice = 11500 + heatPumpPackage.capacity * 420 + tankPrice;
    const totalPrice = roundToHundreds(heatPumpPackage.price + installationPrice);
    return Object.freeze({
      available: true,
      low: totalPrice,
      high: totalPrice,
      totalPrice,
      equipmentPrice: heatPumpPackage.price,
      installationPrice,
      package: heatPumpPackage,
      recommendation,
    });
  }

  function estimateCatalogBudget({ type, areaM2, pvKwp, batteryKwh }) {
    const heat = heatPumpBudget(areaM2);
    if (type === "heat") {
      return Object.freeze({ available: heat.available, type, low: heat.low, high: heat.high, heatPump: heat, pv: null, battery: null, catalogTotal: 0 });
    }

    const pv = selectPvPackage(pvKwp);
    const battery = selectBatteryPackage(batteryKwh);
    if (!pv.available || !battery.available) {
      return Object.freeze({ available: false, type, low: null, high: null, heatPump: type === "combo" ? heat : null, pv, battery, catalogTotal: null });
    }

    const catalogTotal = pv.price + battery.price;
    if (type === "pv") {
      return Object.freeze({ available: true, type, low: catalogTotal, high: catalogTotal, heatPump: null, pv, battery, catalogTotal });
    }

    if (!heat.available) {
      return Object.freeze({ available: false, type, low: null, high: null, heatPump: heat, pv, battery, catalogTotal });
    }

    return Object.freeze({
      available: true,
      type,
      low: roundToHundreds(heat.totalPrice + catalogTotal),
      high: roundToHundreds(heat.totalPrice + catalogTotal),
      heatPump: heat,
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
    const recommendationOutsideCatalog = type !== "heat" && !recommendation.available;
    const effectivePvKwp = selectedPv;
    const result = estimateCatalogBudget({ type, areaM2, pvKwp: effectivePvKwp, batteryKwh });

    return Object.freeze({
      ...result,
      recommendation,
      recommendationOutsideCatalog,
      effectivePvKwp,
      heatPumpElectricityKwh,
      designUsageKwh,
    });
  }

  const api = Object.freeze({
    get catalogVersion() { return catalogVersion; },
    get pvPackages() { return PV_PACKAGES; },
    get batteryPackages() { return BATTERY_PACKAGES; },
    get heatPumpPackages() { return HEAT_PUMP_PACKAGES; },
    defaultPvYieldKwhPerKwp: DEFAULT_PV_YIELD_KWH_PER_KWP,
    applyCatalogSnapshot,
    selectPvPackage,
    selectBatteryPackage,
    recommendHeatPumpForArea,
    recommendPvForUsage,
    estimateHeatPumpElectricityKwh,
    heatPumpBudget,
    estimateCatalogBudget,
    estimateCatalogBudgetForUsage,
  });

  root.GreenExpertsCatalogPricing = api;
  if (typeof module === "object" && module.exports) module.exports = api;
}(typeof globalThis === "object" ? globalThis : this));
