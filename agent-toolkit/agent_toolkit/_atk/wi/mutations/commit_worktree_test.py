import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._testing import git_repository


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    1.0,
    reason=("実Gitの状態・履歴を使う結合境界。修正前Linux4並列全件計測の所有CPU最大0.418秒。"),
)
@pytest.mark.parametrize("action", ["adopt", "reject"])
@pytest.mark.parametrize("case", ["resolved", "missing-worktree", "missing-revision"])
def test_commit_transition_from_outside_worktree(
    action: str,
    case: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = tmp_path / "notes"
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    outside.mkdir()

    for directory in (notes, target):
        directory.mkdir()
        git_repository.init_repository(directory, initial_branch="main")
    source = notes / "2026/10/entry.md"
    source.parent.mkdir(parents=True)
    original = "---\nstate: processing\ntarget_repo: github.com/example/target\ntype: awi\nsource: test\n---\n\n要求\n"
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

    effect = ["--effect-check=pending"] if action == "adopt" else []
    with pytest.raises(SystemExit) as captured:
        atk.main(["wi", action, "entry.md", "--target-repo", target_argument, "--commit", revision, *effect], home=tmp_path)

    output = capsys.readouterr()
    if case == "resolved":
        assert captured.value.code == 0, output
        text = source.read_text(encoding="utf-8")
        assert f"state: {'adopted' if action == 'adopt' else 'rejected'}\n" in text
        short = git_repository.git_output(target, "rev-parse", "--short", oid)
        assert f"- 対応commit: {short}\n" in text
        assert "- 対応commit件名: 要求を反映する" in text
        assert oid not in text
        assert "- 対応commit作成者日時:" not in text
        assert source.exists()
    else:
        assert captured.value.code == 2, output
        assert source.read_text(encoding="utf-8") == original
        assert not (notes / "adopted/entry.md").exists()
        assert not (notes / "rejected/entry.md").exists()
