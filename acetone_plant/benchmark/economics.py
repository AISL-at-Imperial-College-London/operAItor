"""Plant economics for the fault campaigns: on-spec product revenue minus feed and utilities,
plus a shutdown/startup penalty when a SIF trips the plant.

All prices are benchmark assumptions (2026 USD, order-of-magnitude market values), kept in one
place so they can be changed. Every quantity is evaluated on the TRUE plant state (not on the
noisy / possibly faulty BPCS measurements), at the 0.5-min console step.

Revenue / credits [USD per hour]
  on-spec acetone   distillate acetone [kmol/h] x 58.08 kg/kmol x price, only while the distillate
                    purity is at or above the sales spec (0.90 mol frac, the DIST_X_L alarm limit);
                    off-spec distillate goes to the slop tank for rework: no revenue
  recovered IPA     IPA in the bottoms is recycled: credited at RECYCLE_CREDIT x the IPA price
  hydrogen          H2 vented through PCV/BDV/PSV goes to the fuel header: fuel value
Costs [USD per hour]
  IPA feed          fresh feed [kmol/h] x 60.10 kg/kmol x price
  reboiler steam    reboiler duty [MW] x steam price
  reactor heating   CSTR jacket duty [MW] x hot-utility price
Inventory
  the change in liquid hold-up (reactor + reboiler + reflux drum) over the window is valued at the
  IPA price: drawing hold-up down (lower level setpoints, feed cut) sells material the plant
  already had, so without this term it would look like profit.
Plant trip (a plant-trip SIF latches: R_P_HHH, RB_L_HH, DR_L_LL, DR_L_HH, COL_P_HH,
  COL_L_LL)
  the plant is down for the rest of the scenario window (no revenue, no costs) and pays
  SHUTDOWN_COST + STARTUP_COST once (flaring/off-spec during restart, restart utilities, labour).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

MW_ACETONE, MW_IPA, MW_H2 = 58.08, 60.10, 2.016


@dataclass(frozen=True)
class Prices:
    acetone_usd_t: float = 1300.0
    ipa_usd_t: float = 1000.0
    h2_fuel_usd_kg: float = 1.0
    steam_usd_GJ: float = 8.0
    hot_utility_usd_GJ: float = 8.0
    recycle_credit: float = 0.95            # fraction of the IPA price credited for recycled IPA
    product_spec_x: float = 0.90            # distillate acetone purity sales spec (DIST_X_L limit)
    shutdown_cost_usd: float = 5000.0       # controlled shutdown after the trip
    startup_cost_usd: float = 15000.0       # restart: flaring/off-spec product, utilities, labour

    @property
    def trip_penalty_usd(self) -> float:
        return self.shutdown_cost_usd + self.startup_cost_usd


PRICES = Prices()


def hourly_margin(summary: dict, prices: Prices = PRICES) -> dict:
    """USD/h components from ``IntegratedPlantDAE.operating_summary(y, actual_inputs)``."""
    D = summary["column.distillate_flow_kmol_h"]
    xD = summary["column.distillate_x_acetone"]
    B = summary["column.bottoms_flow_kmol_h"]
    xB = summary["column.bottoms_x_acetone"]
    on_spec = xD >= prices.product_spec_x
    acetone = D * xD * MW_ACETONE / 1000 * prices.acetone_usd_t if on_spec else 0.0
    ipa_credit = B * (1 - xB) * MW_IPA / 1000 * prices.ipa_usd_t * prices.recycle_credit
    h2 = summary["cstr.hydrogen_vent_kmol_h"] * MW_H2 * prices.h2_fuel_usd_kg
    feed = summary["feed_kmol_h"] * MW_IPA / 1000 * prices.ipa_usd_t
    steam = summary["column.reboiler_duty_MW"] * 3.6 * prices.steam_usd_GJ
    heat = summary["cstr.heater_duty_MW"] * 3.6 * prices.hot_utility_usd_GJ
    return {"acetone": acetone, "ipa_credit": ipa_credit, "h2": h2, "feed": -feed, "steam": -steam,
            "heating": -heat, "margin": acetone + ipa_credit + h2 - feed - steam - heat,
            "on_spec": bool(on_spec), "on_spec_kmol_h": D * xD if on_spec else 0.0}


INVENTORY_TAGS = ("cstr_holdup_kmol", "reboiler_holdup_kmol", "drum_holdup_kmol")


def inventory_value(tick: dict, prices: Prices = PRICES) -> float:
    """USD value of the liquid hold-up in one recorded tick (valued as IPA feed)."""
    return sum(tick[k] for k in INVENTORY_TAGS) * MW_IPA / 1000 * prices.ipa_usd_t


def prices_dict(prices: Prices = PRICES) -> dict:
    return {**asdict(prices), "trip_penalty_usd": prices.trip_penalty_usd}
