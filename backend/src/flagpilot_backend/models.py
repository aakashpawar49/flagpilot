"""Domain models for an experiment run and the agent's outputs."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from .stats import Decision, Verdict, required_sample_per_arm

SLUG = r"^[a-z0-9][a-z0-9_-]{0,62}$"


def now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    PLANNING = "planning"
    PLAN_READY = "plan_ready"  # waiting for a human to approve the plan
    PROVISIONING = "provisioning"
    CODING = "coding"
    PR_OPEN = "pr_open"  # waiting for a human to review the PR and launch
    LAUNCHING = "launching"
    RUNNING = "running"
    ANALYSING = "analysing"
    SHIP_READY = "ship_ready"  # waiting for a human to approve the rollout decision
    SHIPPING = "shipping"
    SHIPPED = "shipped"
    REJECTED = "rejected"
    FAILED = "failed"


# --- Plan the agent proposes -------------------------------------------------


class Variant(BaseModel):
    key: str = Field(pattern=SLUG, description="Stable variant key, e.g. 'short_form'.")
    value: str = Field(description="String value the flag serves for this variant.")
    description: str = Field(description="What a visitor sees in this variant.")


class MetricPlan(BaseModel):
    name: str = Field(description="Human-readable metric name, e.g. 'Signup completed'.")
    event: str = Field(
        pattern=r"^[a-z0-9_.:-]{1,64}$",
        description="Event name the app sends with trackEvent, e.g. 'signup_completed'.",
    )
    aggregation: Literal["occurrence", "count", "sum", "mean"] = "occurrence"
    direction: Literal["up", "down"] = "up"
    description: str = ""


class ExperimentPlan(BaseModel):
    title: str
    hypothesis: str = Field(description="We believe X will cause Y, measured by Z.")
    flag_name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,62}$")
    control: Variant
    variants: list[Variant] = Field(min_length=1, max_length=3)
    metric: MetricPlan
    rollout_percentage: float = Field(default=100, ge=1, le=100)
    # Pre-registered power analysis. FlagPilot turns these into a required sample size
    # and refuses to call a winner before it is reached.
    baseline_rate: float = Field(
        default=0.1, gt=0, lt=1, description="Today's conversion rate for the metric, as a fraction (0.08 = 8%)."
    )
    min_detectable_lift: float = Field(
        default=0.2, gt=0, le=5, description="Smallest relative improvement worth detecting (0.2 = +20%)."
    )
    expected_daily_visitors: int | None = Field(
        default=None, ge=1, description="Visitors per day who reach the experiment, if known."
    )
    files_to_change: list[str]
    implementation_notes: str
    risks: list[str] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def required_per_arm(self) -> int:
        """Visitors each arm needs before the stopping rule can call a result (alpha 0.05, power 0.8)."""
        return required_sample_per_arm(self.baseline_rate, self.min_detectable_lift)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def estimated_days(self) -> float | None:
        if not self.expected_daily_visitors:
            return None
        enrolled_per_day = self.expected_daily_visitors * self.rollout_percentage / 100
        return round(self.required_per_arm * (len(self.variants) + 1) / enrolled_per_day, 1)


# --- Artifacts the run produces ---------------------------------------------


class FileChange(BaseModel):
    path: str
    content: str | None  # None deletes the file
    summary: str


class AgentEvent(BaseModel):
    ts: datetime = Field(default_factory=now)
    kind: Literal["info", "tool", "thinking", "approval", "error", "success"]
    message: str
    detail: dict[str, Any] | None = None


class FlagsmithRefs(BaseModel):
    feature_id: int | None = None
    option_ids: dict[str, int] = {}  # variant key -> multivariate option id
    metric_id: int | None = None
    experiment_id: int | None = None
    experiment_url: str | None = None


class PullRequestRef(BaseModel):
    number: int
    url: str
    branch: str
    title: str


class VariantResult(BaseModel):
    key: str
    identities: int | None = None
    conversions: float | None = None
    rate: float | None = None
    lift: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    chance_to_win: float | None = None


class Explanation(BaseModel):
    """What the language model writes. The decision itself comes from stats.decide."""

    headline: str = Field(description="One sentence a product manager can read.")
    explanation: str = Field(description="Plain-English reading of the numbers, consistent with the decision.")
    caveats: list[str] = []


class Recommendation(Explanation):
    decision: Decision
    variant_key: str | None = None


class Health(BaseModel):
    """Post-launch integration check: are exposures and conversions arriving as configured?"""

    checked_at: datetime = Field(default_factory=now)
    exposures: dict[str, int] = {}
    conversions: dict[str, int] = {}
    srm_p_value: float | None = None
    issues: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.issues


class ResultsSnapshot(BaseModel):
    fetched_at: datetime = Field(default_factory=now)
    as_of: str | None = None
    srm_p_value: float | None = None
    srm_source: Literal["flagsmith", "flagpilot"] | None = None
    required_per_arm: int | None = None
    exposures: dict[str, int] = {}  # identities bucketed per arm, from the exposures timeseries
    conversions: dict[str, int] = {}  # converted identities per arm
    variants: list[VariantResult] = []
    raw: dict[str, Any] | None = None
    verdict: Verdict | None = None
    recommendation: Recommendation | None = None


class Run(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    goal: str
    repo: str
    base_branch: str
    app_path: str = ""
    status: RunStatus = RunStatus.PLANNING
    plan: ExperimentPlan | None = None
    file_changes: list[FileChange] = []
    flagsmith: FlagsmithRefs = Field(default_factory=FlagsmithRefs)
    launch_pr: PullRequestRef | None = None
    ship_pr: PullRequestRef | None = None
    results: ResultsSnapshot | None = None
    health: Health | None = None
    launched_at: datetime | None = None
    ship_choice: str | None = None  # arm a human approved to ship
    shipped: str | None = None  # set once shipping finished
    events: list[AgentEvent] = []
    error: str | None = None
    failed_step: str | None = None  # which step to resume on retry
    activity: str | None = None  # background step in progress, if any
