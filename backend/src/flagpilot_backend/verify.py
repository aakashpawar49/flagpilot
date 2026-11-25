"""Build verification: apply the agent's changes to a fresh clone and run the project's build.

A pull request that doesn't compile wastes the reviewer's time and, worse, an
experiment can launch against code that never shipped. So FlagPilot builds the
change locally first and feeds any errors back to the model before opening a PR.
"""

import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .models import FileChange

LOG_TAIL = 6000


@dataclass
class BuildReport:
    ok: bool
    command: str
    log: str
    skipped_reason: str | None = None

    @property
    def summary(self) -> str:
        if self.skipped_reason:
            return f"Build check skipped: {self.skipped_reason}"
        return f"`{self.command}` {'passed' if self.ok else 'failed'}"


async def _run(cmd: list[str], *, cwd: Path, timeout: float, redact: str = "") -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return 124, f"Timed out after {timeout:.0f}s: {' '.join(cmd[:2])}"
    text = out.decode("utf-8", errors="replace")
    if redact:
        text = text.replace(redact, "***")
    return proc.returncode or 0, text[-LOG_TAIL:]


def _apply(root: Path, changes: list[FileChange]) -> None:
    for change in changes:
        target = (root / change.path).resolve()
        if root.resolve() not in target.parents:
            raise ValueError(f"{change.path} escapes the repository")
        if change.content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.content, encoding="utf-8", newline="\n")


async def verify_changes(
    *, repo: str, base_branch: str, token: str, changes: list[FileChange], app_path: str = ""
) -> BuildReport:
    """Clones `repo@base_branch`, applies `changes`, runs `npm install` and `npm run build` in `app_path`."""
    git, npm = shutil.which("git"), shutil.which("npm")
    if not git or not npm:
        return BuildReport(
            ok=True, command="", log="", skipped_reason="git or npm is not installed on the FlagPilot host."
        )
    tmp = Path(tempfile.mkdtemp(prefix="flagpilot-verify-"))
    try:
        url = f"https://x-access-token:{token}@github.com/{repo}.git"
        code, out = await _run(
            [git, "clone", "--quiet", "--depth", "1", "--branch", base_branch, url, "repo"],
            cwd=tmp,
            timeout=120,
            redact=token,
        )
        if code != 0:
            return BuildReport(ok=False, command="git clone", log=out)
        root = tmp / "repo"
        _apply(root, changes)

        project = root / app_path.strip("/") if app_path.strip("/") else root
        package_json = project / "package.json"
        if not package_json.exists():
            return BuildReport(ok=True, command="", log="", skipped_reason=f"no package.json under {app_path or '/'}")
        scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        if "build" not in scripts:
            return BuildReport(ok=True, command="", log="", skipped_reason="package.json has no build script")

        # `npm install`, not `npm ci`: the change may add a dependency the lockfile doesn't know yet.
        env_quiet = ["--no-audit", "--no-fund", "--loglevel=error"]
        code, out = await _run([npm, "install", *env_quiet], cwd=project, timeout=300)
        if code != 0:
            return BuildReport(ok=False, command="npm install", log=out)
        code, out = await _run([npm, "run", "build"], cwd=project, timeout=300)
        return BuildReport(ok=code == 0, command="npm run build", log=out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if os.name == "nt" and tmp.exists():  # node_modules sometimes needs a second pass on Windows
            shutil.rmtree(tmp, ignore_errors=True)
