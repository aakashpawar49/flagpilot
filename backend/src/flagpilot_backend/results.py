"""Turn Flagsmith's results payload into per-variant rows.

The payload mirrors Flagsmith's ResultsSummary dataclass:
  {"srm_p_value": float|None,
   "metrics": [{"metric_id": int,
                "variants": {key: {"n": int, "sum": float, "sum_squares": float}},
                "inference": {key: {"lift", "ci_low", "ci_high", "chance_to_win"} | None},
                ...}],
   ...}
Parsing is defensive because the endpoint is beta and may evolve.
"""

from typing import Any

from .models import ResultsSnapshot, VariantResult


def parse_results(results: dict[str, Any] | None, metric_id: int | None) -> ResultsSnapshot:
    snapshot = ResultsSnapshot(raw=results)
    if not results:
        return snapshot
    snapshot.as_of = results.get("as_of")
    payload = results.get("payload") or {}
    snapshot.srm_p_value = payload.get("srm_p_value")

    metrics = payload.get("metrics") or []
    metric = next((m for m in metrics if m.get("metric_id") == metric_id), metrics[0] if metrics else None)
    if not metric:
        return snapshot

    inference = metric.get("inference") or {}
    for key, stats in (metric.get("variants") or {}).items():
        stats = stats or {}
        n = stats.get("n")
        total = stats.get("sum")
        row = VariantResult(
            key=key,
            identities=n,
            conversions=total,
            rate=(total / n) if n and total is not None else None,
        )
        inf = inference.get(key)
        if inf:
            row.lift = inf.get("lift")
            row.ci_low = inf.get("ci_low")
            row.ci_high = inf.get("ci_high")
            row.chance_to_win = inf.get("chance_to_win")
        snapshot.variants.append(row)

    snapshot.variants.sort(key=lambda v: (v.key != "control", v.key))
    return snapshot
