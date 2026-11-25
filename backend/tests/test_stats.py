from dataclasses import dataclass

import pytest

from flagpilot_backend import stats


@dataclass
class Arm:
    key: str
    identities: int | None = None
    conversions: float | None = None
    chance_to_win: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None


def test_required_sample_matches_textbook_values():
    # 10% baseline, +20% relative (to 12%), alpha 0.05, power 0.8: the classic ~3,841 per arm.
    assert stats.required_sample_per_arm(0.10, 0.20) == 3841
    # A bigger effect needs fewer visitors.
    assert stats.required_sample_per_arm(0.10, 0.40) < stats.required_sample_per_arm(0.10, 0.20)
    with pytest.raises(ValueError):
        stats.required_sample_per_arm(0.5, 2.0)  # 150% conversion is impossible


def test_chi_square_sf_known_points():
    assert stats.chi_square_sf(3.841, 1) == pytest.approx(0.05, abs=1e-3)
    assert stats.chi_square_sf(5.991, 2) == pytest.approx(0.05, abs=1e-3)
    assert stats.chi_square_sf(7.815, 3) == pytest.approx(0.05, abs=2e-3)
    assert stats.chi_square_sf(0, 1) == 1.0


def test_srm_detects_broken_split():
    even = {"control": 0.5, "three_field": 0.5}
    assert stats.srm_p_value({"control": 307, "three_field": 293}, even) > 0.3
    assert stats.srm_p_value({"control": 500, "three_field": 100}, even) < 1e-6
    assert stats.srm_p_value({"control": 30, "three_field": 20}, even) is None  # too little traffic


def arms(n_control=307, c_control=38, n_var=293, c_var=38, p_win=0.58, ci=(-0.39, 0.49)):
    return [
        Arm("control", n_control, c_control),
        Arm("three_field", n_var, c_var, p_win, ci[0], ci[1]),
    ]


def test_verdict_keeps_running_before_planned_sample_even_if_winning():
    v = stats.decide(arms(p_win=0.99, ci=(0.1, 0.6)), required_per_arm=1034, srm_p=0.57)
    assert v.decision == "keep_running"
    assert "peeking" in v.summary
    assert [c.ok for c in v.checks] == [True, True, False, True]


def test_verdict_ships_clear_winner_at_planned_sample():
    v = stats.decide(arms(n_control=1100, n_var=1050, p_win=0.98, ci=(0.05, 0.4)), required_per_arm=1034, srm_p=0.5)
    assert v.decision == "ship_variant" and v.variant_key == "three_field"


def test_verdict_keeps_control_when_inconclusive_at_planned_sample():
    v = stats.decide(arms(n_control=1100, n_var=1050), required_per_arm=1034, srm_p=0.5)
    assert v.decision == "keep_control"


def test_verdict_invalid_on_srm():
    v = stats.decide(arms(), required_per_arm=1034, srm_p=1e-9)
    assert v.decision == "invalid"


def test_verdict_collecting_data_below_floors():
    v = stats.decide([Arm("control", 40, 3), Arm("three_field", 45, 2)], required_per_arm=None, srm_p=None)
    assert v.decision == "keep_running"
    assert v.checks[1].name == "Minimum data" and v.checks[1].ok is False
