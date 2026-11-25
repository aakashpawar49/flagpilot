import pytest

from flagpilot_backend.agent.agents import CodeChanges
from flagpilot_backend.models import FileChange
from flagpilot_backend.verify import BuildReport, _apply


def test_apply_writes_and_deletes_inside_repo(tmp_path):
    (tmp_path / "old.txt").write_text("x")
    _apply(
        tmp_path,
        [
            FileChange(path="src/new.ts", content="export const a = 1\n", summary="s"),
            FileChange(path="old.txt", content=None, summary="d"),
        ],
    )
    assert (tmp_path / "src" / "new.ts").read_text() == "export const a = 1\n"
    assert not (tmp_path / "old.txt").exists()


def test_apply_refuses_paths_outside_repo(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        _apply(tmp_path, [FileChange(path="../evil.txt", content="x", summary="s")])


def test_build_report_summary():
    assert BuildReport(ok=False, command="npm run build", log="TS1005").summary == "`npm run build` failed"
    assert "skipped" in BuildReport(ok=True, command="", log="", skipped_reason="no package.json").summary


def test_fix_merge_keeps_files_the_model_did_not_resend():
    """fix_build merges: files absent from the fix carry over from the previous attempt."""
    previous = CodeChanges(
        changes=[FileChange(path="a.ts", content="A", summary=""), FileChange(path="b.ts", content="B", summary="")],
        pr_title="t",
        pr_body="b",
    )
    fix = CodeChanges(changes=[FileChange(path="b.ts", content="B2", summary="")], pr_title="t", pr_body="b")
    merged = {c.path: c for c in previous.changes}
    merged.update({c.path: c for c in fix.changes})
    assert {c.path: c.content for c in merged.values()} == {"a.ts": "A", "b.ts": "B2"}
