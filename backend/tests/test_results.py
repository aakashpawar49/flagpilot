from flagpilot_backend.results import parse_results


def test_parse_results_rows_and_inference():
    raw = {
        "as_of": "2026-11-28T10:00:00Z",
        "is_final": False,
        "payload": {
            "srm_p_value": 0.4,
            "metrics": [
                {
                    "metric_id": 9,
                    "variants": {
                        "short_form": {"n": 300, "sum": 45, "sum_squares": 45},
                        "control": {"n": 310, "sum": 31, "sum_squares": 31},
                    },
                    "inference": {
                        "short_form": {"lift": 0.5, "ci_low": 0.1, "ci_high": 0.9, "chance_to_win": 0.98},
                        "control": None,
                    },
                }
            ],
        },
    }
    snap = parse_results(raw, metric_id=9)
    assert snap.srm_p_value == 0.4
    assert [v.key for v in snap.variants] == ["control", "short_form"]
    control, short = snap.variants
    assert control.rate == 0.1
    assert short.rate == 0.15
    assert short.chance_to_win == 0.98
    assert control.chance_to_win is None


def test_parse_results_handles_not_computed():
    assert parse_results(None, 9).variants == []
    assert parse_results({"payload": None}, 9).variants == []
