"""Pure calculations: energy distribution, summary percentages and costs.

Input values are keyed by *role* (see devices.py): grid_import, grid_export, solar_energy,
battery_charge, battery_discharge, gas.  All values are non-negative energy amounts for the period.
"""
from __future__ import annotations

from typing import Sequence


def _pos(v: float | None) -> float:
    return v if v is not None and v > 0 else 0.0


def compute_flows(values: dict[str, float]) -> dict[str, float]:
    """Allocate energy between sources and sinks.

    Priority: solar covers grid export first, then battery charge, then home.
    Battery discharge covers remaining export, then home.  The rest of the
    battery charge comes from the grid.
    """
    imp = _pos(values.get("grid_import"))
    exp = _pos(values.get("grid_export"))
    sol = _pos(values.get("solar_energy"))
    b_in = _pos(values.get("battery_charge"))
    b_out = _pos(values.get("battery_discharge"))

    solar_to_grid = min(exp, sol)
    solar_to_battery = min(b_in, sol - solar_to_grid)
    solar_to_home = sol - solar_to_grid - solar_to_battery
    battery_to_grid = min(exp - solar_to_grid, b_out)
    battery_to_home = b_out - battery_to_grid
    grid_to_battery = b_in - solar_to_battery
    grid_to_home = max(0.0, imp - grid_to_battery)
    return {
        "solar_to_grid": solar_to_grid,
        "solar_to_battery": solar_to_battery,
        "solar_to_home": solar_to_home,
        "battery_to_grid": battery_to_grid,
        "battery_to_home": battery_to_home,
        "grid_to_battery": grid_to_battery,
        "grid_to_home": grid_to_home,
    }


def compute_summary(values: dict[str, float]) -> dict:
    imp = _pos(values.get("grid_import"))
    exp = _pos(values.get("grid_export"))
    sol = _pos(values.get("solar_energy"))
    b_in = _pos(values.get("battery_charge"))
    b_out = _pos(values.get("battery_discharge"))
    home = max(0.0, imp + sol + b_out - exp - b_in)
    flows = compute_flows(values)
    self_consumed = None
    if sol > 0:
        self_consumed = round((sol - flows["solar_to_grid"]) / sol * 100, 1)
    self_sufficiency = None
    if home > 0:
        self_sufficiency = round(max(0.0, min(100.0, (1 - flows["grid_to_home"] / home) * 100)), 1)
    return {
        "values": {
            "grid_import": imp, "grid_export": exp, "solar_energy": sol,
            "battery_charge": b_in, "battery_discharge": b_out,
            "gas": _pos(values.get("gas")), "home": home,
        },
        "flows": flows,
        "net_grid": imp - exp,
        "self_consumed_solar_pct": self_consumed,
        "self_sufficiency_pct": self_sufficiency,
    }


def cost_for(points: Sequence[tuple[int, float]],
             price_points: Sequence[tuple[int, float]] | None = None,
             fixed: float | None = None) -> float | None:
    """Cost of consumption `points` [(ts, delta)] at a fixed or time-varying price.

    The time-varying price is forward-filled; before the first known price the
    first known price is used.  Returns None when no price information exists.
    """
    if fixed is not None:
        return sum(d for _, d in points) * fixed
    if not price_points:
        return None
    total = 0.0
    idx = 0
    price = price_points[0][1]
    for ts, delta in points:
        while idx < len(price_points) and price_points[idx][0] <= ts:
            price = price_points[idx][1]
            idx += 1
        total += delta * price
    return total


def stored_kwh(soc_pct: float, capacity_wh: float) -> float:
    """Energy held by a battery in kWh: state of charge in percent of its capacity in Wh."""
    return soc_pct / 100 * capacity_wh / 1000
