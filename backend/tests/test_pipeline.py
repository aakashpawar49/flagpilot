import asyncio

from flagpilot_backend import pipeline as pipeline_mod
from flagpilot_backend.agent.agents import CodeChanges
from flagpilot_backend.config import Settings
from flagpilot_backend.models import ExperimentPlan, FileChange, MetricPlan, Run, RunStatus, Variant
from flagpilot_backend.pipeline import Pipeline
from flagpilot_backend.store import RunStore

PLAN = ExperimentPlan(
    title="Short signup",
    hypothesis="Fewer fields, more signups.",
    flag_name="signup_form_variant",
    control=Variant(key="control", value="control", description="6 fields"),
    variants=[Variant(key="short_form", value="short_form", description="3 fields")],
    metric=MetricPlan(name="Signup completed", event="signup_completed"),
    files_to_change=["demo-app/src/SignupForm.tsx"],
    implementation_notes="n",
)


class FakeFlagsmith:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def create_multivariate_feature(self, *a):
        self.calls.append("feature")
        return {"id": 1}

    async def create_variant_option(self, *a):
        self.calls.append("option")
        return {"id": 2}

    async def list_metrics(self):
        return []

    async def create_metric(self, **k):
        self.calls.append("metric")
        return {"id": 3}

    async def create_experiment(self, **k):
        self.calls.append("experiment")
        return {"id": 4}

    async def experiment_url(self, i):
        return f"https://app.test/exp/{i}"


class FakeGitHub:
    async def create_branch(self, *a):
        pass

    async def commit_files(self, *a):
        return "sha"

    async def open_pr(self, repo, **k):
        return {"number": 9, "html_url": "https://github.test/pr/9"}


async def test_retry_resumes_build_without_recreating_flagsmith_objects(tmp_path, monkeypatch):
    attempts = {"n": 0}

    async def flaky_implement(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("model overloaded")
        return CodeChanges(
            changes=[FileChange(path="demo-app/src/SignupForm.tsx", content="x", summary="s")],
            pr_title="t",
            pr_body="b",
        )

    monkeypatch.setattr(pipeline_mod, "implement_experiment", flaky_implement)
    p = Pipeline(RunStore(str(tmp_path)), Settings(_env_file=None))
    fs, gh = FakeFlagsmith(), FakeGitHub()
    p._flagsmith, p._github = fs, gh  # type: ignore[assignment]

    run = Run(id="r1", goal="g" * 10, repo="o/r", base_branch="main", plan=PLAN, status=RunStatus.PLAN_READY)
    p.store.save(run)

    p.approve_plan("r1", None)
    await asyncio.gather(*p._tasks)
    failed = p.store.get("r1")
    assert failed.status == RunStatus.FAILED and failed.failed_step == "build"
    assert fs.calls == ["feature", "option", "metric", "experiment"]

    p.retry("r1")
    await asyncio.gather(*p._tasks)
    done = p.store.get("r1")
    assert done.status == RunStatus.PR_OPEN
    assert done.launch_pr.number == 9
    assert fs.calls == ["feature", "option", "metric", "experiment"]  # nothing created twice


def test_recover_interrupted_marks_busy_runs_failed(tmp_path):
    p = Pipeline(RunStore(str(tmp_path)), Settings(_env_file=None))
    p.store.save(Run(id="r2", goal="g" * 10, repo="o/r", base_branch="main", status=RunStatus.CODING))
    p.recover_interrupted()
    run = p.store.get("r2")
    assert run.status == RunStatus.FAILED and run.failed_step == "build"


def test_ship_requires_results_unless_early(tmp_path):
    import pytest

    from flagpilot_backend.pipeline import TransitionError

    p = Pipeline(RunStore(str(tmp_path)), Settings(_env_file=None))
    p.store.save(Run(id="r3", goal="g" * 10, repo="o/r", base_branch="main", plan=PLAN, status=RunStatus.RUNNING))
    with pytest.raises(TransitionError, match="no results"):
        p.ship("r3", winner="short_form")


async def test_actions_blocked_while_a_step_runs(tmp_path):
    import pytest

    from flagpilot_backend.pipeline import TransitionError

    p = Pipeline(RunStore(str(tmp_path)), Settings(_env_file=None))
    run = Run(id="r4", goal="g" * 10, repo="o/r", base_branch="main", plan=PLAN, status=RunStatus.RUNNING)
    p.store.save(run)
    gate = asyncio.Event()

    async def slow(run):
        await gate.wait()

    p._spawn(run, slow, "simulate")
    assert p.store.get("r4").activity == "simulate"
    with pytest.raises(TransitionError, match="still working"):
        p.ship("r4", winner="short_form", early=True)
    gate.set()
    await asyncio.gather(*p._tasks)
    assert p.store.get("r4").activity is None
