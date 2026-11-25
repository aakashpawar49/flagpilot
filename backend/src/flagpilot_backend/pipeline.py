"""Run lifecycle. Every write to Flagsmith or GitHub sits behind a human approval.

planning -> plan_ready --(approve plan)--> provisioning -> coding -> pr_open
pr_open --(launch, after PR merge)--> running --(analyse)--> ship_ready
ship_ready --(approve decision)--> shipping -> shipped
"""

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .agent import prompts
from .agent.agents import (
    cleanup_experiment,
    explain_results,
    fix_build,
    implement_experiment,
    plan_experiment,
)
from .agent.repo import RepoReader
from .config import Settings
from .flagsmith_admin import FlagsmithAdmin
from .github_client import GitHubClient
from .models import AgentEvent, ExperimentPlan, PullRequestRef, Run, RunStatus, now
from .results import build_health, parse_results
from .simulator import simulate_traffic
from .store import RunStore
from .verify import verify_changes


class TransitionError(RuntimeError):
    """The run isn't in a state that allows this action."""


class Pipeline:
    def __init__(self, store: RunStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._tasks: set[asyncio.Task[None]] = set()
        self._active: set[str] = set()  # run ids with a background step in progress
        self._monitors: dict[str, asyncio.Task[None]] = {}
        self._nudges: dict[str, asyncio.Event] = {}
        self._flagsmith: FlagsmithAdmin | None = None
        self._github: GitHubClient | None = None

    # --- clients ---------------------------------------------------------------

    @property
    def flagsmith(self) -> FlagsmithAdmin:
        if self._flagsmith is None:
            s = self.settings
            self._flagsmith = FlagsmithAdmin(
                api_url=s.flagsmith_api_url,
                app_url=s.flagsmith_app_url,
                admin_key=s.flagsmith_admin_key,
                project_id=s.flagsmith_project_id,
                environment_key=s.flagsmith_environment_key,
            )
        return self._flagsmith

    @property
    def github(self) -> GitHubClient:
        if self._github is None:
            self._github = GitHubClient(token=self.settings.github_token, api_url=self.settings.github_api_url)
        return self._github

    async def aclose(self) -> None:
        for task in [*self._tasks, *self._monitors.values()]:
            task.cancel()
        if self._flagsmith:
            await self._flagsmith.aclose()
        if self._github:
            await self._github.aclose()

    # --- helpers -----------------------------------------------------------------

    def _emitter(self, run: Run) -> Callable[..., Awaitable[None]]:
        async def emit(kind: str, message: str, detail: dict[str, Any] | None = None) -> None:
            run.events.append(AgentEvent(kind=kind, message=message, detail=detail))
            self.store.save(run)

        return emit

    def _spawn(self, run: Run, step: Callable[[Run], Awaitable[None]], name: str) -> None:
        # One background step per run at a time; otherwise two copies of the run overwrite each other.
        self._active.add(run.id)
        run.activity = name
        self.store.save(run)

        async def wrapper() -> None:
            run.error = None
            run.failed_step = None
            try:
                await step(run)
            except Exception as exc:  # surface every failure on the run timeline
                run.status = RunStatus.FAILED
                run.failed_step = name
                run.error = f"{type(exc).__name__}: {exc}"
                await self._emitter(run)("error", run.error)
            finally:
                self._active.discard(run.id)
                run.activity = None
                self.store.save(run)

        task = asyncio.create_task(wrapper())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _load(self, run_id: str, *allowed: RunStatus) -> Run:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run_id in self._active:
            raise TransitionError(f"FlagPilot is still working on this run ({run.activity}). Wait for it to finish.")
        if allowed and run.status not in allowed:
            raise TransitionError(f"Run is {run.status}; expected one of {[s.value for s in allowed]}.")
        return run

    def _reader(self, run: Run, ref: str | None = None) -> RepoReader:
        return RepoReader(self.github, run.repo, ref or run.base_branch, run.app_path)

    # --- 1. plan -----------------------------------------------------------------

    def create_run(self, *, goal: str, repo: str, base_branch: str, app_path: str) -> Run:
        run = Run(id=uuid.uuid4().hex[:10], goal=goal, repo=repo, base_branch=base_branch, app_path=app_path)
        self.store.save(run)
        self._spawn(run, self._plan, "plan")
        return run

    async def _plan(self, run: Run) -> None:
        emit = self._emitter(run)
        await emit("info", f"Reading {run.repo} and the Flagsmith project to design an experiment.")
        run.plan = await plan_experiment(goal=run.goal, reader=self._reader(run), flagsmith=self.flagsmith, emit=emit)
        run.status = RunStatus.PLAN_READY
        await emit(
            "approval",
            "Plan ready. Review it, edit if needed, then approve to create the flag, metric, experiment and PR.",
        )

    # --- 2. provision + implement --------------------------------------------------

    def approve_plan(self, run_id: str, plan: ExperimentPlan | None) -> Run:
        run = self._load(run_id, RunStatus.PLAN_READY)
        if plan is not None:
            run.plan = plan
        run.status = RunStatus.PROVISIONING
        run.events.append(AgentEvent(kind="approval", message="Plan approved by a human."))
        self.store.save(run)
        self._spawn(run, self._provision_and_implement, "build")
        return run

    async def _provision_and_implement(self, run: Run) -> None:
        emit = self._emitter(run)
        plan = run.plan
        assert plan is not None
        fs = self.flagsmith

        # Each step is skipped if an earlier attempt already did it, so a failed run can be retried.
        if run.flagsmith.feature_id is None:
            feature = await fs.create_multivariate_feature(plan.flag_name, plan.hypothesis, plan.control.value)
            run.flagsmith.feature_id = feature["id"]
            await emit("success", f"Created multivariate flag `{plan.flag_name}`.", {"feature_id": feature["id"]})
        feature_id = run.flagsmith.feature_id

        missing = [v for v in plan.variants if v.key not in run.flagsmith.option_ids]
        for variant in missing:
            option = await fs.create_variant_option(feature_id, variant.key, variant.value)
            run.flagsmith.option_ids[variant.key] = option["id"]
        if missing:
            await emit("success", f"Added variants: {', '.join(v.key for v in missing)}.")

        if run.flagsmith.experiment_id is None:
            await self._create_metric_and_experiment(run)

        run.status = RunStatus.CODING
        await emit("info", "Writing the code change for each variant.")
        code = await implement_experiment(plan=plan, reader=self._reader(run), emit=emit)
        code = await self._verified(run, code, system=prompts.CODER_SYSTEM)
        run.file_changes = code.changes
        run.launch_pr = await self._open_pr(
            run, f"flagpilot/{plan.flag_name}", code.changes, code.pr_title, code.pr_body
        )
        run.status = RunStatus.PR_OPEN
        await emit(
            "approval",
            f"Opened PR #{run.launch_pr.number}. Review and merge it, then launch the experiment.",
            {"url": run.launch_pr.url},
        )

    async def _create_metric_and_experiment(self, run: Run) -> None:
        emit = self._emitter(run)
        plan = run.plan
        assert plan is not None and run.flagsmith.feature_id is not None
        fs = self.flagsmith
        existing = next(
            (
                m
                for m in await fs.list_metrics()
                if m["definition"].get("event") == plan.metric.event and m["aggregation"] == plan.metric.aggregation
            ),
            None,
        )
        if existing:
            run.flagsmith.metric_id = existing["id"]
            await emit("info", f"Reusing existing metric `{existing['name']}` for event `{plan.metric.event}`.")
        else:
            metric = await fs.create_metric(
                name=plan.metric.name,
                description=plan.metric.description,
                event=plan.metric.event,
                aggregation=plan.metric.aggregation,
                direction=plan.metric.direction,
            )
            run.flagsmith.metric_id = metric["id"]
            await emit("success", f"Created metric `{plan.metric.name}` on event `{plan.metric.event}`.")

        # Even split: each variant gets an equal share, control takes the remainder.
        share = round(100 / (len(plan.variants) + 1), 2)
        experiment = await fs.create_experiment(
            feature_id=run.flagsmith.feature_id,
            name=plan.title,
            hypothesis=plan.hypothesis,
            metric_id=run.flagsmith.metric_id,
            expected_direction="increase" if plan.metric.direction == "up" else "decrease",
            control_value=plan.control.value,
            option_weights={run.flagsmith.option_ids[v.key]: share for v in plan.variants},
            rollout_percentage=plan.rollout_percentage,
        )
        run.flagsmith.experiment_id = experiment["id"]
        run.flagsmith.experiment_url = await fs.experiment_url(experiment["id"])
        await emit(
            "success", "Created the experiment in Flagsmith (not started).", {"url": run.flagsmith.experiment_url}
        )

    async def _verified(self, run: Run, code, *, system: str):
        """Builds the change on a clean clone; on failure asks the model to fix it, up to twice."""
        if not self.settings.verify_build:
            return code
        emit = self._emitter(run)
        for attempt in range(1, 4):
            await emit("info", f"Building the change on a clean clone (attempt {attempt}).")
            report = await verify_changes(
                repo=run.repo,
                base_branch=run.base_branch,
                token=self.settings.github_token,
                changes=code.changes,
                app_path=run.app_path,
            )
            await emit(
                "success" if report.ok else "error",
                f"Build check: {report.summary}",
                {"input": {"log": report.log[-1500:]}} if report.log else None,
            )
            if report.ok:
                return code
            if attempt == 3:
                raise RuntimeError("The change still fails to build after two fixes. Not opening a PR.")
            await emit("info", "Sending the build errors back to the model to fix.")
            code = await fix_build(
                previous=code, build_log=report.log, system=system, reader=self._reader(run), emit=emit
            )
        return code

    async def _open_pr(self, run: Run, branch: str, changes: list, title: str, body: str) -> PullRequestRef:
        branch = f"{branch}-{run.id[:4]}"
        await self.github.create_branch(run.repo, branch, run.base_branch)
        await self.github.commit_files(run.repo, branch, changes, title)
        footer = "\n\n---\nOpened by FlagPilot."
        if run.flagsmith.experiment_url:
            footer += f" Flagsmith experiment: {run.flagsmith.experiment_url}"
        pr = await self.github.open_pr(run.repo, head=branch, base=run.base_branch, title=title, body=body + footer)
        return PullRequestRef(number=pr["number"], url=pr["html_url"], branch=branch, title=title)

    # --- 3. launch -----------------------------------------------------------------

    async def launch(self, run_id: str, *, force: bool = False) -> Run:
        run = self._load(run_id, RunStatus.PR_OPEN)
        assert run.launch_pr and run.flagsmith.experiment_id
        pr = await self.github.get_pr(run.repo, run.launch_pr.number)
        if not pr.get("merged") and not force:
            raise TransitionError(f"PR #{run.launch_pr.number} isn't merged yet. Merge it first, or launch with force.")
        emit = self._emitter(run)
        await emit("approval", "Launch approved by a human.")
        await self.flagsmith.start_experiment(run.flagsmith.experiment_id)
        run.status = RunStatus.RUNNING
        run.launched_at = now()
        await emit("success", "Experiment started. Flagsmith is now splitting traffic.")
        await emit(
            "info",
            f"Stopping rule: at least {run.plan.required_per_arm:,} visitors per arm "
            f"(baseline {run.plan.baseline_rate:.0%}, minimum detectable lift {run.plan.min_detectable_lift:+.0%}). "
            "FlagPilot checks exposures and results every 5 minutes.",
        )
        self._start_monitor(run.id)
        return run

    # --- 3b. monitor: health checks and results, every 5 minutes -----------------------

    MONITOR_INTERVAL = 300  # Flagsmith allows one recompute per 5 minutes

    def start_monitors(self) -> None:
        for run in self.store.list():
            if run.status in (RunStatus.RUNNING, RunStatus.SHIP_READY):
                self._start_monitor(run.id)

    def _start_monitor(self, run_id: str) -> None:
        existing = self._monitors.get(run_id)
        if existing and not existing.done():
            return
        self._nudges[run_id] = asyncio.Event()
        self._monitors[run_id] = asyncio.create_task(self._monitor(run_id))

    def nudge(self, run_id: str) -> None:
        """Asks the monitor to check now instead of at its next 5-minute tick."""
        if event := self._nudges.get(run_id):
            event.set()

    async def _monitor(self, run_id: str) -> None:
        nudge = self._nudges[run_id]
        while True:
            try:
                await asyncio.wait_for(nudge.wait(), timeout=self.MONITOR_INTERVAL)
            except TimeoutError:
                pass
            nudge.clear()
            for _ in range(600):  # let a running step (simulation, analysis) finish first
                if run_id not in self._active:
                    break
                await asyncio.sleep(1)
            run = self.store.get(run_id)
            if run is None or run.status not in (RunStatus.RUNNING, RunStatus.SHIP_READY):
                return
            try:
                await self._refresh_snapshot(run)
            except Exception as exc:
                await self._emitter(run)("error", f"Monitor: {type(exc).__name__}: {exc}")

    async def _refresh_snapshot(self, run: Run) -> bool:
        """Pulls the latest results, applies the stopping rule and health checks. No LLM."""
        emit = self._emitter(run)
        exp_id = run.flagsmith.experiment_id
        assert exp_id and run.plan
        refreshed = await self.flagsmith.refresh_results(exp_id)
        if refreshed:
            await asyncio.sleep(10)  # recompute is asynchronous server-side
        raw = await self.flagsmith.get_results(exp_id)
        snapshot = parse_results(raw, run.flagsmith.metric_id, plan=run.plan)
        if run.results and run.results.as_of == snapshot.as_of:
            snapshot.recommendation = run.results.recommendation  # data unchanged, keep the explanation
        minutes = (now() - run.launched_at).total_seconds() / 60 if run.launched_at else 0
        health = build_health(run.plan, snapshot, minutes_since_launch=minutes)
        previous_issues = set(run.health.issues) if run.health else set()
        for issue in health.issues:
            if issue not in previous_issues:
                await emit("error", f"Health check: {issue}")
        if previous_issues and not health.issues:
            await emit("success", "Health check: exposures and conversions look right again.")
        run.health = health
        run.results = snapshot
        verdict = snapshot.verdict
        if verdict and verdict.decision in ("ship_variant", "keep_control") and run.status == RunStatus.RUNNING:
            run.status = RunStatus.SHIP_READY
            await emit("approval", f"{verdict.summary} Approve the rollout decision to finish the experiment.")
        elif verdict and verdict.decision == "keep_running" and run.status == RunStatus.SHIP_READY:
            run.status = RunStatus.RUNNING
        if not refreshed and not snapshot.variants and not health.issues:
            await emit("info", "Flagsmith allows one recompute every 5 minutes; checking again at the next tick.")
        self.store.save(run)
        return refreshed

    # --- 4. simulate (demo only) -----------------------------------------------------

    async def _server_key(self) -> str:
        if self.settings.flagsmith_server_key:
            return self.settings.flagsmith_server_key
        if key := self.store.get_value("flagsmith_server_key"):
            return key
        key = await self.flagsmith.create_server_key("FlagPilot simulator")
        self.store.set_value("flagsmith_server_key", key)
        return key

    def simulate(self, run_id: str, *, visitors: int, rates: dict[str, float]) -> Run:
        run = self._load(run_id, RunStatus.RUNNING, RunStatus.SHIP_READY)
        assert run.plan

        async def step(run: Run) -> None:
            emit = self._emitter(run)
            await emit(
                "info", f"Simulating {visitors} visitors (labelled simulated traffic). Conversion rates: {rates}."
            )
            key = await self._server_key()
            loop = asyncio.get_running_loop()

            def progress(done: int) -> None:
                asyncio.run_coroutine_threadsafe(emit("info", f"Simulated {done}/{visitors} visitors."), loop)

            counts = await asyncio.to_thread(
                simulate_traffic,
                server_key=key,
                sdk_api_url=None,
                flag_name=run.plan.flag_name,
                event=run.plan.metric.event,
                visitors=visitors,
                rates=rates,
                tag=f"{run.id}-{uuid.uuid4().hex[:4]}",
                on_progress=progress,
            )
            await emit("success", "Simulation finished. Fetching results from Flagsmith.", {"counts": counts})
            self.nudge(run.id)

        self._spawn(run, step, "simulate")
        return run

    # --- 5. analyse ------------------------------------------------------------------

    def analyse(self, run_id: str) -> Run:
        """Refreshes results, applies the stopping rule, then has the model explain the verdict."""
        run = self._load(run_id, RunStatus.RUNNING, RunStatus.SHIP_READY)

        async def step(run: Run) -> None:
            emit = self._emitter(run)
            await self._refresh_snapshot(run)
            snapshot = run.results
            if not snapshot or not snapshot.variants or not snapshot.verdict:
                await emit("info", "Flagsmith hasn't computed results yet. The monitor will keep checking.")
                return
            if snapshot.recommendation and snapshot.recommendation.decision == snapshot.verdict.decision:
                return  # this exact data was already explained
            await emit("info", f"Rule says: {snapshot.verdict.summary} Asking the model to explain the numbers.")
            snapshot.recommendation = await explain_results(plan=run.plan, snapshot=snapshot, emit=emit)
            await emit("info", snapshot.recommendation.headline)

        self._spawn(run, step, "analyse")
        return run

    # --- 6. ship ---------------------------------------------------------------------

    def ship(self, run_id: str, *, winner: str, early: bool = False) -> Run:
        run = self._load(run_id, RunStatus.SHIP_READY, RunStatus.RUNNING)
        assert run.plan
        valid = {"control", *(v.key for v in run.plan.variants)}
        if winner not in valid:
            raise TransitionError(f"winner must be one of {sorted(valid)}")
        verdict = run.results.verdict if run.results else None
        decided = verdict is not None and verdict.decision in ("ship_variant", "keep_control")
        if not decided and not early:
            raise TransitionError(
                "The stopping rule hasn't been met (planned sample not reached or no results). "
                "Wait, or explicitly end the experiment early."
            )
        run.status = RunStatus.SHIPPING
        run.ship_choice = winner
        if decided:
            recommended = verdict.variant_key if verdict.decision == "ship_variant" else "control"
            note = "" if winner == recommended else f" The rule recommended `{recommended}`; the human overrode it."
            run.events.append(AgentEvent(kind="approval", message=f"Human approved shipping `{winner}`.{note}"))
        else:
            run.events.append(
                AgentEvent(
                    kind="approval", message=f"Human ended the experiment early and chose `{winner}` without results."
                )
            )
        self.store.save(run)
        self._spawn(run, self._ship, "ship")
        return run

    async def _ship(self, run: Run) -> None:
        emit = self._emitter(run)
        winner = run.ship_choice
        assert run.plan and run.flagsmith.experiment_id and winner
        experiment = await self.flagsmith.get_experiment(run.flagsmith.experiment_id)
        if experiment.get("status") != "completed":  # skip if a previous attempt already completed it
            if winner == "control":
                await self.flagsmith.complete_experiment(run.flagsmith.experiment_id, "control")
            else:
                await self.flagsmith.complete_experiment(
                    run.flagsmith.experiment_id, "variant", run.flagsmith.option_ids[winner]
                )
            await emit("success", f"Experiment completed in Flagsmith. Everyone now gets `{winner}`.")
        await emit("info", "Removing the experiment code so the flag can be retired.")
        code = await cleanup_experiment(
            plan=run.plan,
            winner_key=winner,
            reader=self._reader(run),
            paths=[c.path for c in run.file_changes if c.content is not None],
            emit=emit,
        )
        code = await self._verified(run, code, system=prompts.CLEANUP_SYSTEM)
        run.ship_pr = await self._open_pr(
            run, f"flagpilot/{_slug(run.plan.flag_name)}-cleanup", code.changes, code.pr_title, code.pr_body
        )
        run.shipped = winner
        run.status = RunStatus.SHIPPED
        await emit("success", f"Opened cleanup PR #{run.ship_pr.number}.", {"url": run.ship_pr.url})

    # --- recovery --------------------------------------------------------------------

    def retry(self, run_id: str) -> Run:
        """Resumes a failed run from the step that failed. Work already done is not repeated."""
        run = self._load(run_id, RunStatus.FAILED)
        step = run.failed_step
        run.events.append(AgentEvent(kind="info", message=f"Retrying the `{step}` step."))
        if step == "plan":
            run.status = RunStatus.PLANNING
            self.store.save(run)
            self._spawn(run, self._plan, "plan")
        elif step == "build":
            run.status = RunStatus.PROVISIONING
            self.store.save(run)
            self._spawn(run, self._provision_and_implement, "build")
        elif step == "ship":
            run.status = RunStatus.SHIPPING
            self.store.save(run)
            self._spawn(run, self._ship, "ship")
        elif step in ("simulate", "analyse"):
            run.status = RunStatus.RUNNING  # both can simply be triggered again
            run.error = run.failed_step = None
            self.store.save(run)
        else:
            raise TransitionError(f"Don't know how to retry step {step!r}.")
        return run

    def recover_interrupted(self) -> None:
        """Marks runs whose background work died with the previous server process as failed."""
        busy = {
            RunStatus.PLANNING: "plan",
            RunStatus.PROVISIONING: "build",
            RunStatus.CODING: "build",
            RunStatus.ANALYSING: "analyse",
            RunStatus.SHIPPING: "ship",
        }
        for run in self.store.list():
            if run.status in busy:
                run.failed_step = busy[run.status]
                run.status = RunStatus.FAILED
                run.error = "Interrupted by a server restart. Retry to resume."
                run.events.append(AgentEvent(kind="error", message=run.error))
                self.store.save(run)

    def reject(self, run_id: str) -> Run:
        run = self._load(run_id, RunStatus.PLAN_READY)
        run.status = RunStatus.REJECTED
        run.events.append(AgentEvent(kind="approval", message="Plan rejected by a human."))
        return self.store.save(run)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
