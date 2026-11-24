"""Domain models for an experiment run and the agent's outputs."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

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
    files_to_change: list[str]
    implementation_notes: str
    risks: list[str] = []


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


class Recommendation(BaseModel):
    decision: Literal["ship_variant", "keep_control", "keep_running"]
    variant_key: str | None = Field(default=None, description="Variant to ship when decision is ship_variant.")
    confidence: Literal["low", "medium", "high"]
    headline: str = Field(description="One sentence a product manager can read.")
    explanation: str = Field(description="Plain-English reading of the numbers.")
    caveats: list[str] = []


class ResultsSnapshot(BaseModel):
    fetched_at: datetime = Field(default_factory=now)
    as_of: str | None = None
    srm_p_value: float | None = None
    variants: list[VariantResult] = []
    raw: dict[str, Any] | None = None
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
    ship_choice: str | None = None  # arm a human approved to ship
    shipped: str | None = None  # set once shipping finished
    events: list[AgentEvent] = []
    error: str | None = None
    failed_step: str | None = None  # which step to resume on retry
