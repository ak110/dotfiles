"""対象worktreeの外からcommit付きの採否を行う公開CLIの受入テスト。"""

import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import git_sync
from agent_toolkit._atk.wi.constants import WI_STATES
from agent_toolkit._testing import git_repository


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

    for directory in (notes, target):
        directory.mkdir()
        git_repository.init_repository(directory, initial_branch="main")
    for state in WI_STATES:
        (notes / state).mkdir()
        (notes / state / ".gitkeep").touch()
    (notes / git_sync.LOCAL_ONLY_MARKER).touch()
    source = notes / "processing/entry.md"
    original = "---\ntarget_repo: github.com/example/target\ntype: awi\nsource: test\n---\n\n要求\n"
    source.write_text(original, encoding="utf-8")
    git_repository.git_output(notes, "add", ".")
    git_repository.git_output(notes, "commit", "-q", "-m", "base")
    (target / "content.txt").write_text("反映済み\n", encoding="utf-8")
    git_repository.git_output(target, "add", ".")
    git_repository.git_output(target, "commit", "-q", "-m", "要求を反映する")
    git_repository.git_output(target, "remote", "add", "origin", "https://github.com/example/target.git")
    oid = git_repository.git_output(target, "rev-parse", "HEAD")
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
        short = git_repository.git_output(target, "rev-parse", "--short", oid)
        assert f"- 対応commit: {short}\n" in text
        assert "- 対応commit件名: 要求を反映する" in text
        assert oid not in text
        assert "- 対応commit作成者日時:" not in text
        assert not source.exists()
    else:
        assert captured.value.code == 2, output
        assert source.read_text(encoding="utf-8") == original
        assert not (notes / "adopted/entry.md").exists()
        assert not (notes / "rejected/entry.md").exists()


@pytest.mark.parametrize("action", ["adopt", "reject"])
def test_help_describes_short_oid_and_subject(action: str, capsys: pytest.CaptureFixture[str]) -> None:
    """adoptとrejectのヘルプが、--commitで短縮OIDと件名を記録すると示す。"""
    with pytest.raises(SystemExit):
        atk.main(["wi", action, "--help"])
    output = "".join(capsys.readouterr().out.split())
    assert "短縮OIDと件名を記録する" in output
    if action == "adopt":
        assert "対応commitの短縮OID・件名を記録する" in output
