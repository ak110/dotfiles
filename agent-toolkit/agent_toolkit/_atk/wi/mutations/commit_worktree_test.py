"""対象worktreeの外からcommit付きの採否を行う公開CLIの受入テスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import git_sync
from agent_toolkit._atk.wi.common import WI_STATES


@pytest.mark.parametrize("action", ["adopt", "reject"])
@pytest.mark.parametrize("case", ["resolved", "missing-worktree", "missing-revision"])
def test_commit_transition_from_outside_worktree(
    action: str,
    case: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """明示パスで実Gitのcommitを記録し、解決不能なら状態と本文を保持する。"""
    notes = tmp_path / "notes"
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    outside.mkdir()

    def git(directory: pathlib.Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(directory), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        ).stdout.strip()

    for directory in (notes, target):
        directory.mkdir()
        git(directory, "init", "-q", "--initial-branch=main")
    for state in WI_STATES:
        (notes / state).mkdir()
        (notes / state / ".gitkeep").touch()
    (notes / git_sync.LOCAL_ONLY_MARKER).touch()
    source = notes / "processing/entry.md"
    original = "---\ntarget_repo: github.com/example/target\ntype: awi\nsource: test\n---\n\n要求\n"
    source.write_text(original, encoding="utf-8")
    git(notes, "add", ".")
    git(notes, "commit", "-q", "-m", "base")
    (target / "content.txt").write_text("反映済み\n", encoding="utf-8")
    git(target, "add", ".")
    git(target, "commit", "-q", "-m", "要求を反映する")
    git(target, "remote", "add", "origin", "https://github.com/example/target.git")
    oid = git(target, "rev-parse", "HEAD")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.chdir(outside)
    target_argument = "github.com/example/target" if case == "missing-worktree" else str(target)
    revision = "no-such-revision" if case == "missing-revision" else oid

    with pytest.raises(SystemExit) as captured:
        atk.main(["wi", action, "entry.md", "--target-repo", target_argument, "--commit", revision], home=tmp_path)

    output = capsys.readouterr()
    if case == "resolved":
        assert captured.value.code == 0, output
        result = notes / ("adopted" if action == "adopt" else "rejected") / source.name
        text = result.read_text(encoding="utf-8")
        assert f"- 対応commit: {oid}" in text
        assert "- 対応commit件名: 要求を反映する" in text
        assert "- 対応commit作成者日時:" in text
        assert not source.exists()
    else:
        assert captured.value.code == 2, output
        assert source.read_text(encoding="utf-8") == original
        assert not (notes / "adopted/entry.md").exists()
        assert not (notes / "rejected/entry.md").exists()
