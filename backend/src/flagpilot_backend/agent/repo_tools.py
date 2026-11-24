"""Repository tools shared by the planner and coder agents."""

from ..github_client import GitHubClient, GitHubError
from ..models import FileChange
from .loop import Tool, ToolInputError

MAX_FILE_CHARS = 120_000


class RepoWorkspace:
    """A read view of one repo ref plus a set of staged (uncommitted) edits."""

    def __init__(self, gh: GitHubClient, repo: str, ref: str, app_path: str = "") -> None:
        self.gh = gh
        self.repo = repo
        self.ref = ref
        self.app_path = app_path.strip("/") + "/" if app_path.strip("/") else ""
        self.staged: dict[str, FileChange] = {}
        self._files: list[str] | None = None

    async def files(self) -> list[str]:
        if self._files is None:
            self._files = await self.gh.list_files(self.repo, self.ref, self.app_path)
        return self._files

    async def read(self, path: str) -> str:
        if path in self.staged:
            change = self.staged[path]
            if change.content is None:
                raise ToolInputError(f"{path} was deleted earlier in this session.")
            return change.content
        try:
            return await self.gh.read_file(self.repo, path, self.ref)
        except GitHubError as exc:
            if exc.status == 404:
                raise ToolInputError(f"{path} does not exist. Use list_repo_files to see paths.") from exc
            raise

    # --- tool definitions ------------------------------------------------------

    def read_tools(self) -> list[Tool]:
        async def list_repo_files(inp: dict) -> str:
            files = await self.files()
            prefix = inp.get("prefix") or ""
            matched = [f for f in files if f.startswith(prefix)]
            listing = "\n".join(matched[:500])
            more = f"\n... and {len(matched) - 500} more" if len(matched) > 500 else ""
            return listing + more if matched else "No files match."

        async def read_repo_file(inp: dict) -> str:
            path = str(inp.get("path", "")).lstrip("/")
            text = await self.read(path)
            if len(text) > MAX_FILE_CHARS:
                raise ToolInputError(f"{path} is {len(text)} chars, too large to read whole.")
            return text

        return [
            Tool(
                name="list_repo_files",
                description="List file paths in the repository (vendored and binary files excluded).",
                input_schema={
                    "type": "object",
                    "properties": {"prefix": {"type": "string", "description": "Only paths starting with this."}},
                },
                handler=list_repo_files,
            ),
            Tool(
                name="read_repo_file",
                description="Read a file's full contents. Reflects edits staged earlier in this session.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                handler=read_repo_file,
            ),
        ]

    def write_tools(self) -> list[Tool]:
        async def write_file(inp: dict) -> str:
            path = str(inp.get("path", "")).lstrip("/")
            content = inp.get("content")
            summary = str(inp.get("summary", "")).strip()
            if not path or not isinstance(content, str) or not summary:
                raise ToolInputError("path, content and summary are all required.")
            if ".." in path.split("/"):
                raise ToolInputError("Path must stay inside the repository.")
            self.staged[path] = FileChange(path=path, content=content, summary=summary)
            return f"Staged {path} ({len(content)} chars)."

        async def delete_file(inp: dict) -> str:
            path = str(inp.get("path", "")).lstrip("/")
            if path not in await self.files() and path not in self.staged:
                raise ToolInputError(f"{path} does not exist.")
            self.staged[path] = FileChange(path=path, content=None, summary=str(inp.get("summary", "Removed")))
            return f"Staged deletion of {path}."

        return [
            Tool(
                name="write_file",
                description="Create or fully replace a file. Send the complete new file contents.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string", "description": "Complete file contents."},
                        "summary": {"type": "string", "description": "One line: what changed and why."},
                    },
                    "required": ["path", "content", "summary"],
                },
                handler=write_file,
            ),
            Tool(
                name="delete_file",
                description="Delete a file that is no longer needed.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "summary": {"type": "string"}},
                    "required": ["path", "summary"],
                },
                handler=delete_file,
            ),
        ]
