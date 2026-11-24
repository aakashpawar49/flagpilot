"""The three FlagPilot agents: planner, coder (launch and cleanup), analyst."""

import json
from typing import Any

from pydantic import ValidationError

from ..flagsmith_admin import FlagsmithAdmin
from ..models import ExperimentPlan, FileChange, Recommendation, ResultsSnapshot
from . import prompts
from .loop import Emit, Tool, ToolInputError, run_agent, run_structured
from .repo_tools import RepoWorkspace


async def plan_experiment(
    *, goal: str, workspace: RepoWorkspace, flagsmith: FlagsmithAdmin, emit: Emit
) -> ExperimentPlan:
    existing_flags: set[str] = set()

    async def list_flagsmith_flags(_: dict) -> str:
        features = await flagsmith.list_features()
        existing_flags.update(f["name"] for f in features)
        if not features:
            return "The project has no flags yet."
        return "\n".join(f"- {f['name']} ({f.get('type', '?')})" for f in features)

    async def list_flagsmith_metrics(_: dict) -> str:
        metrics = await flagsmith.list_metrics()
        if not metrics:
            return "No experiment metrics exist yet."
        return "\n".join(
            f"- {m['name']}: event={m['definition'].get('event')} aggregation={m['aggregation']} direction={m['direction']}"
            for m in metrics
        )

    async def submit_plan(inp: dict) -> str:
        try:
            plan = ExperimentPlan.model_validate(inp)
        except ValidationError as exc:
            raise ToolInputError(f"Plan is invalid: {exc}") from exc
        if plan.control.key != "control" or plan.control.value != "control":
            raise ToolInputError("control.key and control.value must both be 'control'.")
        keys = [v.key for v in plan.variants]
        if "control" in keys or len(set(keys)) != len(keys):
            raise ToolInputError("Variant keys must be unique and must not be 'control'.")
        if any(v.value != v.key for v in plan.variants):
            raise ToolInputError("Each variant's value must equal its key.")
        if not existing_flags:
            existing_flags.update(f["name"] for f in await flagsmith.list_features())
        if plan.flag_name in existing_flags:
            raise ToolInputError(f"A flag named {plan.flag_name} already exists. Pick another name.")
        return "Plan submitted for human review."

    tools = [
        *workspace.read_tools(),
        Tool(
            name="list_flagsmith_flags",
            description="List flags that already exist in the Flagsmith project.",
            input_schema={"type": "object", "properties": {}},
            handler=list_flagsmith_flags,
        ),
        Tool(
            name="list_flagsmith_metrics",
            description="List experiment metrics already defined in the Flagsmith environment.",
            input_schema={"type": "object", "properties": {}},
            handler=list_flagsmith_metrics,
        ),
        Tool(
            name="submit_plan",
            description="Submit the experiment plan for human approval. Ends planning.",
            input_schema=ExperimentPlan.model_json_schema(),
            handler=submit_plan,
            terminal=True,
        ),
    ]
    user = (
        f"Repository: {workspace.repo} (branch {workspace.ref})"
        + (f", app lives under {workspace.app_path}" if workspace.app_path else "")
        + f"\n\nWhat the developer wants to test:\n{goal}"
    )
    result = await run_agent(system=prompts.PLANNER_SYSTEM, user=user, tools=tools, emit=emit, effort="high")
    return ExperimentPlan.model_validate(result)


async def _run_coder(
    *, system: str, user: str, workspace: RepoWorkspace, emit: Emit
) -> tuple[list[FileChange], str, str]:
    async def finish(inp: dict) -> str:
        if not workspace.staged:
            raise ToolInputError("No files changed yet. Use write_file before finishing.")
        if not inp.get("pr_title") or not inp.get("pr_body"):
            raise ToolInputError("pr_title and pr_body are required.")
        return "Changes submitted."

    tools = [
        *workspace.read_tools(),
        *workspace.write_tools(),
        Tool(
            name="finish",
            description="Submit the staged changes as a pull request for human review.",
            input_schema={
                "type": "object",
                "properties": {"pr_title": {"type": "string"}, "pr_body": {"type": "string"}},
                "required": ["pr_title", "pr_body"],
            },
            handler=finish,
            terminal=True,
        ),
    ]
    result = await run_agent(system=system, user=user, tools=tools, emit=emit, effort="high", max_turns=40)
    return list(workspace.staged.values()), result["pr_title"], result["pr_body"]


async def implement_experiment(
    *, plan: ExperimentPlan, workspace: RepoWorkspace, emit: Emit
) -> tuple[list[FileChange], str, str]:
    user = (
        f"Repository: {workspace.repo} (branch {workspace.ref})\n"
        "Implement this approved experiment plan. The flag, its variants and the metric "
        "already exist in Flagsmith with exactly these names.\n\n"
        f"```json\n{plan.model_dump_json(indent=2)}\n```"
    )
    return await _run_coder(system=prompts.CODER_SYSTEM, user=user, workspace=workspace, emit=emit)


async def cleanup_experiment(
    *, plan: ExperimentPlan, winner_key: str, workspace: RepoWorkspace, emit: Emit
) -> tuple[list[FileChange], str, str]:
    user = (
        f"Repository: {workspace.repo} (branch {workspace.ref})\n"
        f"The experiment on flag `{plan.flag_name}` is over. Keep the `{winner_key}` experience "
        "permanently and remove the experiment code.\n\n"
        f"Plan for reference:\n```json\n{plan.model_dump_json(indent=2)}\n```"
    )
    return await _run_coder(system=prompts.CLEANUP_SYSTEM, user=user, workspace=workspace, emit=emit)


async def analyse_results(*, plan: ExperimentPlan, snapshot: ResultsSnapshot, exposures: Any) -> Recommendation:
    data = {
        "plan": plan.model_dump(),
        "per_variant": [v.model_dump() for v in snapshot.variants],
        "srm_p_value": snapshot.srm_p_value,
        "results_as_of": snapshot.as_of,
        "exposures": exposures,
    }
    user = (
        "Read these experiment results and recommend what to do.\n\n```json\n"
        + json.dumps(data, default=str, indent=2)
        + "\n```"
    )
    return await run_structured(system=prompts.ANALYST_SYSTEM, user=user, output_model=Recommendation)
