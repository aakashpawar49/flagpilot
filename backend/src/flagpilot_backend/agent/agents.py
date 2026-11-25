"""FlagPilot's AI steps: select context, plan, implement, clean up, analyse.

Each step is one structured LLM call (plus self-correction if the output is
invalid), so a whole experiment needs about five requests. That keeps it inside
free-tier quotas. None of these steps write anywhere: the pipeline applies their
output only after a human approves.
"""

import json

from pydantic import BaseModel, Field

from ..config import get_settings
from ..flagsmith_admin import FlagsmithAdmin
from ..models import ExperimentPlan, Explanation, FileChange, Recommendation, ResultsSnapshot
from . import prompts
from .llm import Emit, complete_json
from .repo import RepoReader, render_files, render_tree


class FileSelection(BaseModel):
    files: list[str] = Field(description="Repository paths to read, most important first. At most 15.")


class CodeChanges(BaseModel):
    changes: list[FileChange] = Field(min_length=1)
    pr_title: str
    pr_body: str


async def _gather_context(goal: str, reader: RepoReader, emit: Emit) -> tuple[dict[str, int], dict[str, str]]:
    """Small repos are sent whole; larger ones go through a file-selection call first."""
    budget = get_settings().llm_max_context_chars
    files = await reader.files()
    if not files:
        raise ValueError(f"No source files found in {reader.repo}/{reader.app_path or ''} on {reader.ref}.")
    if sum(files.values()) <= budget:
        paths = list(files)
    else:
        await emit("info", f"Repository has {len(files)} files; asking the model which ones matter.")

        async def check(sel: FileSelection) -> str | None:
            unknown = [p for p in sel.files if p not in files]
            return f"These paths are not in the tree: {unknown}" if unknown else None

        selection = await complete_json(
            system=prompts.FILE_SELECTOR_SYSTEM,
            user=f"What the developer wants to test:\n{goal}\n\nFile tree:\n{render_tree(files)}",
            output=FileSelection,
            emit=emit,
            check=check,
        )
        paths = [*reader.entry_files(), *selection.files[:15]]
    contents = await reader.read_many(paths, budget)
    await emit("tool", f"Read {len(contents)} files", {"input": {"files": list(contents)}})
    return files, contents


async def plan_experiment(*, goal: str, reader: RepoReader, flagsmith: FlagsmithAdmin, emit: Emit) -> ExperimentPlan:
    files, contents = await _gather_context(goal, reader, emit)
    features = await flagsmith.list_features()
    metrics = await flagsmith.list_metrics()
    existing_flags = {f["name"] for f in features}
    await emit("tool", "Checked Flagsmith project", {"input": {"flags": len(features), "metrics": len(metrics)}})

    async def check(plan: ExperimentPlan) -> str | None:
        if plan.control.key != "control" or plan.control.value != "control":
            return "control.key and control.value must both be 'control'."
        keys = [v.key for v in plan.variants]
        if "control" in keys or len(set(keys)) != len(keys):
            return "Variant keys must be unique and must not be 'control'."
        if any(v.value != v.key for v in plan.variants):
            return "Each variant's value must equal its key."
        if plan.flag_name in existing_flags:
            return f"A flag named {plan.flag_name} already exists. Pick another name."
        return None

    flags_text = "\n".join(f"- {f['name']}" for f in features) or "(none)"
    metrics_text = (
        "\n".join(
            f"- {m['name']}: event={m['definition'].get('event')} aggregation={m['aggregation']}" for m in metrics
        )
        or "(none)"
    )
    user = (
        f"What the developer wants to test:\n{goal}\n\n"
        f"Repository: {reader.repo} (branch {reader.ref})"
        + (f", app under {reader.app_path}" if reader.app_path else "")
        + f"\n\nFlags already in Flagsmith:\n{flags_text}\n\nExperiment metrics already defined:\n{metrics_text}"
        + f"\n\nFile tree:\n{render_tree(files)}\n\nFile contents:\n{render_files(contents)}"
    )
    await emit("info", "Designing the experiment.")
    return await complete_json(system=prompts.PLANNER_SYSTEM, user=user, output=ExperimentPlan, emit=emit, check=check)


async def _write_code(*, system: str, task: str, reader: RepoReader, paths: list[str], emit: Emit) -> CodeChanges:
    files = await reader.files()
    contents = await reader.read_many([*paths, *reader.entry_files()], get_settings().llm_max_context_chars)
    await emit("tool", f"Read {len(contents)} files", {"input": {"files": list(contents)}})

    async def check(out: CodeChanges) -> str | None:
        for change in out.changes:
            if ".." in change.path.split("/") or change.path.startswith("/"):
                return f"{change.path}: paths must be repository-relative."
            if change.content is None and change.path not in files:
                return f"{change.path}: cannot delete a file that doesn't exist."
            if change.content is not None and not change.content.strip():
                return f"{change.path}: content is empty. Give the complete file or null to delete."
        return None

    user = f"{task}\n\nRepository: {reader.repo} (branch {reader.ref})\n\nCurrent files:\n{render_files(contents)}"
    out = await complete_json(system=system, user=user, output=CodeChanges, emit=emit, check=check)
    for change in out.changes:
        verb = "Delete" if change.content is None else ("Edit" if change.path in files else "Create")
        await emit("tool", f"{verb} {change.path}", {"input": {"summary": change.summary}})
    return out


async def fix_build(
    *, previous: CodeChanges, build_log: str, system: str, reader: RepoReader, emit: Emit
) -> CodeChanges:
    """Second pass: the previous change failed to build; the model corrects it."""
    files = await reader.files()
    staged = "\n\n".join(f"=== {c.path} ===\n{c.content}" for c in previous.changes if c.content is not None)
    user = (
        "Your previous change did not build. Fix it. Return the COMPLETE corrected contents of every file "
        "that needs to change (you may include unchanged files from your previous change as-is). Keep the "
        f"same pull-request title and body unless the fix changes what the PR does.\n\nBuild output:\n```\n{build_log}\n```"
        f"\n\nYour previous change:\n{staged}"
    )

    async def check(out: CodeChanges) -> str | None:
        for change in out.changes:
            if ".." in change.path.split("/") or change.path.startswith("/"):
                return f"{change.path}: paths must be repository-relative."
            if change.content is None and change.path not in files:
                return f"{change.path}: cannot delete a file that doesn't exist."
        return None

    out = await complete_json(system=system, user=user, output=CodeChanges, emit=emit, check=check)
    # Changes the model didn't resend carry over unchanged.
    merged = {c.path: c for c in previous.changes}
    merged.update({c.path: c for c in out.changes})
    return CodeChanges(changes=list(merged.values()), pr_title=out.pr_title, pr_body=out.pr_body)


async def implement_experiment(*, plan: ExperimentPlan, reader: RepoReader, emit: Emit) -> CodeChanges:
    task = (
        "Implement this approved experiment plan. The flag, its variants and the metric already exist "
        f"in Flagsmith with exactly these names.\n\n```json\n{plan.model_dump_json(indent=2)}\n```"
    )
    return await _write_code(
        system=prompts.CODER_SYSTEM, task=task, reader=reader, paths=plan.files_to_change, emit=emit
    )


async def cleanup_experiment(
    *, plan: ExperimentPlan, winner_key: str, reader: RepoReader, paths: list[str], emit: Emit
) -> CodeChanges:
    task = (
        f"The experiment on flag `{plan.flag_name}` is over. Keep the `{winner_key}` experience permanently "
        f"and remove the experiment code.\n\nPlan for reference:\n```json\n{plan.model_dump_json(indent=2)}\n```"
    )
    return await _write_code(
        system=prompts.CLEANUP_SYSTEM, task=task, reader=reader, paths=[*paths, *plan.files_to_change], emit=emit
    )


async def explain_results(*, plan: ExperimentPlan, snapshot: ResultsSnapshot, emit: Emit) -> Recommendation:
    """The model explains a verdict that stats.decide already made; it cannot change it."""
    assert snapshot.verdict is not None
    data = {
        "verdict": snapshot.verdict.model_dump(),
        "plan": plan.model_dump(
            include={"title", "hypothesis", "metric", "baseline_rate", "min_detectable_lift", "required_per_arm"}
        ),
        "per_variant": [v.model_dump() for v in snapshot.variants],
        "exposures": snapshot.exposures,
        "srm_p_value": snapshot.srm_p_value,
        "results_as_of": snapshot.as_of,
    }
    user = "Explain this experiment verdict to the team.\n\n" + json.dumps(data, default=str, indent=2)
    explanation = await complete_json(system=prompts.ANALYST_SYSTEM, user=user, output=Explanation, emit=emit)
    return Recommendation(
        decision=snapshot.verdict.decision, variant_key=snapshot.verdict.variant_key, **explanation.model_dump()
    )
