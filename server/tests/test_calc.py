import pytest

from app import calc


def test_summary_matches_screenshot_example():
    s = calc.compute_summary({
        "grid_import": 43.9, "grid_export": 5.84, "solar_energy": 6.65,
        "battery_charge": 5.68, "battery_discharge": 4.75, "gas": 4.58})
    assert s["values"]["home"] == pytest.approx(43.78, abs=0.01)
    assert s["net_grid"] == pytest.approx(38.06, abs=0.01)
    assert s["self_sufficiency_pct"] is not None and s["self_sufficiency_pct"] < 20


def test_flows_balance():
    v = {"grid_import": 10, "grid_export": 3, "solar_energy": 8, "battery_charge": 2, "battery_discharge": 4}
    f = calc.compute_flows(v)
    assert f["solar_to_grid"] == 3
    assert f["solar_to_battery"] == 2
    assert f["solar_to_home"] == 3
    assert f["battery_to_home"] == 4
    assert f["grid_to_home"] == 10
    # solar is fully allocated
    assert f["solar_to_grid"] + f["solar_to_battery"] + f["solar_to_home"] == 8


def test_battery_exports_when_solar_short():
    f = calc.compute_flows({"grid_export": 5, "solar_energy": 2, "battery_discharge": 4})
    assert f["solar_to_grid"] == 2 and f["battery_to_grid"] == 3 and f["battery_to_home"] == 1


def test_percentages_none_without_data():
    s = calc.compute_summary({})
    assert s["self_consumed_solar_pct"] is None
    assert s["self_sufficiency_pct"] is None
    assert s["values"]["home"] == 0


def test_full_self_consumption():
    s = calc.compute_summary({"solar_energy": 5, "grid_import": 5})
    assert s["self_consumed_solar_pct"] == 100
    assert s["self_sufficiency_pct"] == 50


def test_cost_variable_forward_fill():
    pts = [(0, 1.0), (60, 1.0), (120, 1.0)]
    prices = [(60, 0.2), (120, 0.4)]
    # t=0 uses first known price (0.2), t=60 0.2, t=120 0.4
    assert calc.cost_for(pts, prices) == pytest.approx(0.8)


def test_cost_without_price_is_none():
    assert calc.cost_for([(0, 1.0)], []) is None


def test_stored_kwh():
    assert calc.stored_kwh(50, 10000) == pytest.approx(5.0)
    assert calc.stored_kwh(0, 10000) == 0 and calc.stored_kwh(100, 13500) == pytest.approx(13.5)
