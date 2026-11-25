"""Turn Flagsmith's results payload into per-variant rows, then apply FlagPilot's own checks.

Flagsmith payload (mirrors its ResultsSummary dataclass):
  {"as_of": ..., "is_final": bool,
   "payload": {"srm_p_value": float|None,
               "metrics": [{"metric_id": int,
                            "variants": {key: {"n", "sum", "sum_squares"}},
                            "inference": {key: {"lift", "ci_low", "ci_high", "chance_to_win"}},
                            "conversions_timeseries": {"points": [{"bucket", "converted_identities": {key: n}}]}}],
               "exposures_timeseries": {"points": [{"bucket", "new_identities": {key: n}}]}}}
Parsing is defensive because the endpoint is beta.
"""

from typing import Any

from . import stats
from .models import ExperimentPlan, Health, ResultsSnapshot, VariantResult, now


def expected_shares(plan: ExperimentPlan) -> dict[str, float]:
    """The split FlagPilot configured: equal shares per variant, control takes the remainder."""
    share = round(100 / (len(plan.variants) + 1), 2) / 100
    shares = {v.key: share for v in plan.variants}
    shares["control"] = round(1 - share * len(plan.variants), 4)
    return shares


def _sum_points(timeseries: dict[str, Any] | None, field: str) -> dict[str, int]:
    totals: dict[str, int] = {}
    for point in (timeseries or {}).get("points") or []:
        for key, count in (point.get(field) or {}).items():
            totals[key] = totals.get(key, 0) + int(count)
    return totals


def parse_results(
    results: dict[str, Any] | None, metric_id: int | None, *, plan: ExperimentPlan | None = None
) -> ResultsSnapshot:
    snapshot = ResultsSnapshot(raw=results)
    if plan is not None:
        snapshot.required_per_arm = plan.required_per_arm
    if not results:
        return snapshot
    snapshot.as_of = results.get("as_of")
    payload = results.get("payload") or {}
    snapshot.exposures = _sum_points(payload.get("exposures_timeseries"), "new_identities")

    metrics = payload.get("metrics") or []
    metric = next((m for m in metrics if m.get("metric_id") == metric_id), metrics[0] if metrics else None)
    if metric:
        snapshot.conversions = _sum_points(metric.get("conversions_timeseries"), "converted_identities")
        inference = metric.get("inference") or {}
        for key, arm in (metric.get("variants") or {}).items():
            arm = arm or {}
            n, total = arm.get("n"), arm.get("sum")
            row = VariantResult(
                key=key, identities=n, conversions=total, rate=(total / n) if n and total is not None else None
            )
            if inf := inference.get(key):
                row.lift, row.ci_low, row.ci_high = inf.get("lift"), inf.get("ci_low"), inf.get("ci_high")
                row.chance_to_win = inf.get("chance_to_win")
            snapshot.variants.append(row)
        snapshot.variants.sort(key=lambda v: (v.key != "control", v.key))

    # SRM: FlagPilot's own test against the split it configured is preferred. Flagsmith's
    # check reads the environment default allocation (0% outside the experiment), which is
    # not the split a running experiment serves.
    if plan is not None and snapshot.exposures:
        snapshot.srm_p_value = stats.srm_p_value(snapshot.exposures, expected_shares(plan))
        snapshot.srm_source = "flagpilot" if snapshot.srm_p_value is not None else None
    if snapshot.srm_p_value is None and payload.get("srm_p_value") is not None:
        snapshot.srm_p_value, snapshot.srm_source = payload["srm_p_value"], "flagsmith"

    if snapshot.variants:
        snapshot.verdict = stats.decide(
            snapshot.variants, required_per_arm=snapshot.required_per_arm, srm_p=snapshot.srm_p_value
        )
    return snapshot


def build_health(plan: ExperimentPlan, snapshot: ResultsSnapshot, *, minutes_since_launch: float) -> Health:
    """Integration checks that catch the common ways an experiment silently collects nothing."""
    arms = ["control", *(v.key for v in plan.variants)]
    exposures = snapshot.exposures
    total = sum(exposures.get(a, 0) for a in arms)
    issues: list[str] = []
    if total == 0:
        if minutes_since_launch >= 10:
            issues.append(
                "No exposures received since launch. Check that the app reads the flag with "
                "getExperimentFlag / useExperiment, identifies each visitor, and has enableEvents: true."
            )
    else:
        for arm in arms:
            if exposures.get(arm, 0) == 0 and total >= 20:
                issues.append(
                    f"`{arm}` has received no exposures while other arms have. "
                    "Usually a variant key mismatch between the code and Flagsmith."
                )
        if snapshot.srm_p_value is not None and snapshot.srm_p_value < stats.SRM_ALARM_P:
            issues.append(
                f"Sample ratio mismatch (p = {snapshot.srm_p_value:.1e}): exposures don't match the configured split. "
                "Look for caching, bots, or exposure recorded in only one code path."
            )
        if total >= 100 and sum(snapshot.conversions.values()) == 0:
            issues.append(
                f"Exposures are arriving but no `{plan.metric.event}` conversions. Check the event name and that "
                "it is sent with the same identity after success."
            )
    return Health(
        checked_at=now(),
        exposures={a: exposures.get(a, 0) for a in arms},
        conversions={a: snapshot.conversions.get(a, 0) for a in arms},
        srm_p_value=snapshot.srm_p_value,
        issues=issues,
    )
