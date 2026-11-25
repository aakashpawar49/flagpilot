"""Deterministic experiment statistics.

These numbers gate decisions (when to stop, what to ship), so they are computed
here, not by the language model. The model only explains them afterwards.
"""

import math
from statistics import NormalDist
from typing import Literal, Protocol

from pydantic import BaseModel

_NORMAL = NormalDist()

# Flagsmith reports no inference below these floors; mirrored so the verdict agrees with its dashboard.
MIN_IDENTITIES_PER_ARM = 50
MIN_CONVERSIONS_PER_ARM = 5
SRM_MIN_TOTAL = 100
SRM_ALARM_P = 0.001
WIN_THRESHOLD = 0.95
LOSE_THRESHOLD = 0.05


# --- Sample size ---------------------------------------------------------------


def required_sample_per_arm(
    baseline_rate: float, relative_lift: float, *, alpha: float = 0.05, power: float = 0.8
) -> int:
    """Visitors needed in EACH arm to detect `relative_lift` over `baseline_rate`.

    Two-sided two-proportion z-test (pooled variance under H0, unpooled under H1),
    the formula behind most online A/B calculators.
    """
    p1 = baseline_rate
    p2 = p1 * (1 + relative_lift)
    if not (0 < p1 < 1 and 0 < p2 < 1):
        raise ValueError("Conversion rates must be strictly between 0 and 1.")
    if p1 == p2:
        raise ValueError("relative_lift must be non-zero.")
    z_alpha = _NORMAL.inv_cdf(1 - alpha / 2)
    z_beta = _NORMAL.inv_cdf(power)
    p_bar = (p1 + p2) / 2
    numerator = (z_alpha * math.sqrt(2 * p_bar * (1 - p_bar)) + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(numerator / (p2 - p1) ** 2)


# --- Sample ratio mismatch ---------------------------------------------------------


def chi_square_sf(chi2: float, df: int) -> float:
    """Upper-tail probability of a chi-square statistic (no scipy dependency)."""
    if df < 1:
        raise ValueError("df must be at least 1")
    if chi2 <= 0:
        return 1.0
    if df == 1:
        return 2 * (1 - _NORMAL.cdf(math.sqrt(chi2)))
    if df == 2:
        return math.exp(-chi2 / 2)
    # Wilson-Hilferty normal approximation; within ~1e-3 for df >= 3.
    z = ((chi2 / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))
    return 1 - _NORMAL.cdf(z)


def srm_p_value(observed: dict[str, int], expected_shares: dict[str, float]) -> float | None:
    """Chi-square goodness-of-fit of observed exposures against the configured split.

    A very small p-value means the bucketing is broken (an unkeyed variant, an
    identity mismatch, a caching layer) and no result from the experiment can be
    trusted. Returns None while there is too little traffic to judge.
    """
    arms = {k: s for k, s in expected_shares.items() if s > 0}
    total = sum(observed.get(k, 0) for k in arms)
    if total < SRM_MIN_TOTAL or len(arms) < 2:
        return None
    chi2 = sum((observed.get(k, 0) - total * s) ** 2 / (total * s) for k, s in arms.items())
    return chi_square_sf(chi2, len(arms) - 1)


# --- Verdict -------------------------------------------------------------------------


class ArmLike(Protocol):
    key: str
    identities: int | None
    conversions: float | None
    chance_to_win: float | None
    ci_low: float | None
    ci_high: float | None


Decision = Literal["ship_variant", "keep_control", "keep_running", "invalid"]


class Check(BaseModel):
    name: str
    ok: bool | None  # None: not applicable yet
    detail: str


class Verdict(BaseModel):
    decision: Decision
    variant_key: str | None = None
    summary: str
    checks: list[Check]


def decide(arms: list[ArmLike], *, required_per_arm: int | None, srm_p: float | None) -> Verdict:
    """The pre-registered stopping rule, applied mechanically.

    Ship a variant only when the planned sample is reached AND it clearly beats
    control. Reaching the planned sample without a clear winner means keep control:
    continuing to run "until it wins" is the peeking error this rule exists to stop.
    """
    checks: list[Check] = []
    treatments = [a for a in arms if a.key != "control"]

    # 1. Is the traffic split healthy?
    if srm_p is None:
        checks.append(Check(name="Traffic split", ok=None, detail="Not enough traffic yet to check the split."))
    elif srm_p < SRM_ALARM_P:
        checks.append(
            Check(
                name="Traffic split",
                ok=False,
                detail=f"Sample ratio mismatch (p = {srm_p:.1e}). Exposures don't match the configured weights, "
                "so the arms are not comparable. Fix the integration before trusting any number.",
            )
        )
        return Verdict(
            decision="invalid",
            summary="The traffic split is broken (sample ratio mismatch). Results can't be trusted.",
            checks=checks,
        )
    else:
        checks.append(Check(name="Traffic split", ok=True, detail=f"Matches the configured weights (p = {srm_p:.2f})."))

    # 2. Enough data in every arm for inference at all?
    thin = [
        a
        for a in arms
        if (a.identities or 0) < MIN_IDENTITIES_PER_ARM or (a.conversions or 0) < MIN_CONVERSIONS_PER_ARM
    ]
    if thin:
        names = ", ".join(a.key for a in thin)
        checks.append(
            Check(
                name="Minimum data",
                ok=False,
                detail=f"{names}: fewer than {MIN_IDENTITIES_PER_ARM} visitors or {MIN_CONVERSIONS_PER_ARM} conversions.",
            )
        )
        return Verdict(decision="keep_running", summary="Still collecting data.", checks=checks)
    checks.append(Check(name="Minimum data", ok=True, detail="Every arm has enough visitors and conversions."))

    # 3. Planned sample size reached?
    smallest = min((a.identities or 0) for a in arms)
    reached = required_per_arm is None or smallest >= required_per_arm
    if required_per_arm is not None:
        pct = min(100, round(100 * smallest / required_per_arm))
        checks.append(
            Check(
                name="Planned sample",
                ok=reached,
                detail=f"{smallest:,} of {required_per_arm:,} visitors in the smallest arm ({pct}%).",
            )
        )

    # 4. Evidence.
    scored = [a for a in treatments if a.chance_to_win is not None]
    best = max(scored, key=lambda a: a.chance_to_win or 0) if scored else None
    if best is None:
        checks.append(Check(name="Evidence", ok=None, detail="Flagsmith hasn't computed inference yet."))
        return Verdict(decision="keep_running", summary="Waiting for Flagsmith to compute inference.", checks=checks)

    p_win = best.chance_to_win or 0
    wins = p_win >= WIN_THRESHOLD and (best.ci_low or 0) > 0
    loses = all((a.chance_to_win or 0) <= LOSE_THRESHOLD for a in scored)
    checks.append(
        Check(
            name="Evidence",
            ok=wins or loses,
            detail=f"{best.key}: {p_win:.0%} chance to beat control"
            + (f", 95% interval {best.ci_low:+.0%} to {best.ci_high:+.0%}." if best.ci_low is not None else "."),
        )
    )

    if not reached:
        return Verdict(
            decision="keep_running",
            summary=f"Keep running: {smallest:,} of {required_per_arm:,} planned visitors per arm. "
            "Deciding now would be peeking.",
            checks=checks,
        )
    if wins:
        return Verdict(
            decision="ship_variant",
            variant_key=best.key,
            summary=f"Ship {best.key}: it beats control with {p_win:.0%} probability at the planned sample size.",
            checks=checks,
        )
    if loses:
        return Verdict(
            decision="keep_control",
            summary="Keep control: every variant is worse at the planned sample size.",
            checks=checks,
        )
    return Verdict(
        decision="keep_control",
        summary="Keep control: the planned sample is in and no variant clearly beats it.",
        checks=checks,
    )
