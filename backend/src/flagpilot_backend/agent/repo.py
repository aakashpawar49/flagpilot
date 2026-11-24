"""Read-only view of a repository ref, used to give the model code context."""

import asyncio

from ..github_client import GitHubClient

ENTRY_HINTS = ("package.json", "src/main.tsx", "src/main.ts", "src/main.jsx", "src/index.tsx", "src/App.tsx")


class RepoReader:
    def __init__(self, gh: GitHubClient, repo: str, ref: str, app_path: str = "") -> None:
        self.gh = gh
        self.repo = repo
        self.ref = ref
        self.app_path = app_path.strip("/") + "/" if app_path.strip("/") else ""
        self._files: dict[str, int] | None = None

    async def files(self) -> dict[str, int]:
        if self._files is None:
            self._files = await self.gh.list_files(self.repo, self.ref, self.app_path)
        return self._files

    async def read_many(self, paths: list[str], budget_chars: int) -> dict[str, str]:
        """Reads existing files in order until the character budget runs out."""
        files = await self.files()
        chosen: list[str] = []
        used = 0
        for path in dict.fromkeys(paths):  # de-duplicate, keep order
            size = files.get(path)
            if size is None or used + size > budget_chars:
                continue
            chosen.append(path)
            used += size
        contents = await asyncio.gather(*(self.gh.read_file(self.repo, p, self.ref) for p in chosen))
        return dict(zip(chosen, contents, strict=True))

    def entry_files(self) -> list[str]:
        return [self.app_path + hint for hint in ENTRY_HINTS]


def render_files(contents: dict[str, str]) -> str:
    return "\n\n".join(f"=== {path} ===\n{text}" for path, text in contents.items())


def render_tree(files: dict[str, int]) -> str:
    return "\n".join(f"{path} ({size} B)" for path, size in files.items())
