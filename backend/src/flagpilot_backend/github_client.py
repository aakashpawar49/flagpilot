"""Async GitHub REST client: read a repo, commit files to a branch, open PRs."""

import base64
from typing import Any

import httpx

from .models import FileChange

# Paths the agent never needs to read.
SKIP_PARTS = ("node_modules/", "dist/", "build/", ".git/", ".venv/", "__pycache__/", ".next/", "coverage/")
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".lock", ".map", ".pdf")
SKIP_NAMES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock")


class GitHubError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: Any) -> None:
        super().__init__(f"GitHub {method} {path} -> {status}: {body}")
        self.status = status


class GitHubClient:
    def __init__(self, *, token: str, api_url: str = "https://api.github.com") -> None:
        self._http = httpx.AsyncClient(
            base_url=api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = await self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json().get("message", resp.text[:300])
            except ValueError:
                body = resp.text[:300]
            raise GitHubError(method, path, resp.status_code, body)
        return resp.json() if resp.content else None

    async def whoami(self) -> str:
        return (await self._req("GET", "/user"))["login"]

    async def list_files(self, repo: str, ref: str, prefix: str = "") -> list[str]:
        tree = await self._req("GET", f"/repos/{repo}/git/trees/{ref}", params={"recursive": "1"})
        paths = []
        for item in tree["tree"]:
            path = item["path"]
            if item["type"] != "blob" or not path.startswith(prefix):
                continue
            if any(p in path for p in SKIP_PARTS) or path.endswith(SKIP_SUFFIXES):
                continue
            if path.rsplit("/", 1)[-1] in SKIP_NAMES:
                continue
            paths.append(path)
        return paths

    async def read_file(self, repo: str, path: str, ref: str) -> str:
        data = await self._req("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref})
        return base64.b64decode(data["content"]).decode("utf-8")

    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        head = await self._req("GET", f"/repos/{repo}/git/ref/heads/{base}")
        await self._req(
            "POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": head["object"]["sha"]}
        )

    async def commit_files(self, repo: str, branch: str, changes: list[FileChange], message: str) -> str:
        """Writes all changes as one commit on `branch`. Returns the commit sha."""
        head = await self._req("GET", f"/repos/{repo}/git/ref/heads/{branch}")
        parent_sha = head["object"]["sha"]
        parent = await self._req("GET", f"/repos/{repo}/git/commits/{parent_sha}")
        tree_items = [
            {"path": c.path, "mode": "100644", "type": "blob", "content": c.content}
            if c.content is not None
            else {"path": c.path, "mode": "100644", "type": "blob", "sha": None}
            for c in changes
        ]
        tree = await self._req(
            "POST", f"/repos/{repo}/git/trees", json={"base_tree": parent["tree"]["sha"], "tree": tree_items}
        )
        commit = await self._req(
            "POST",
            f"/repos/{repo}/git/commits",
            json={"message": message, "tree": tree["sha"], "parents": [parent_sha]},
        )
        await self._req("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", json={"sha": commit["sha"]})
        return commit["sha"]

    async def open_pr(self, repo: str, *, head: str, base: str, title: str, body: str) -> dict[str, Any]:
        return await self._req(
            "POST", f"/repos/{repo}/pulls", json={"head": head, "base": base, "title": title, "body": body}
        )

    async def get_pr(self, repo: str, number: int) -> dict[str, Any]:
        return await self._req("GET", f"/repos/{repo}/pulls/{number}")
