"""効果記録と旧確認キーの公開契約を検証する。"""

import datetime
import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import effect_check, frontmatter, readiness, sync
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_entry_files import write_awi


def test_legacy_confirmation_does_not_defer_processing(tmp_path: pathlib.Path) -> None:
    """保存済み確認キーは通常の未知キーとして着手可否に影響しない。"""
    path = write_awi(tmp_path, "legacy.md")
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\nconfirmation: required\n"), encoding="utf-8"
    )
    assert readiness.calculate_readiness(tmp_path, "github.com/example/repo").ready == (path.name,)


@pytest.fixture(name="effect_repository")
def _effect_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """実commitとローカルremoteを持つWI保存先を用意する。"""
    worktree = git_repository.init_repository(
        tmp_path / "repo",
        origin="https://github.com/example/repo.git",
        files={"agent-toolkit/rules/a.md": "規範A", "agent-toolkit/skills/b/SKILL.md": "規範B", "app.py": "pass"},
        commit_message="試験用の規範変更",
    )
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    notes = tmp_path / "notes"
    path = write_awi(notes, "effect.md", state="adopted")
    original = (
        path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\nconfirmation: required\ncustom: 保持\n")
        + "\n## ユーザーコメント\n\n保持対象\n\n## 処理結果\n\n- 採否: adopted\n"
        + f"- 処理日時: 2026-10-20T00:00:00Z\n- 対応commit: {oid}\n"
        + "- 既存メモ: 保持\n\n## 後続メモ\n\n保持する後続節\n"
    )
    path.write_text(original, encoding="utf-8")
    write_awi(notes, "corrective.md")
    remote = git_repository.init_bare_repository(tmp_path / "notes.git")
    git_repository.init_repository(notes, initial_branch="main", origin=str(remote), commit_message="採用済みAWI")
    git_repository.run_git(notes, "push", "--set-upstream", "origin", "main")
    monkeypatch.setattr(sync, "ensure_environment", lambda *_args, **_kwargs: notes)
    return notes, worktree, path


def _invoke(notes: pathlib.Path, worktree: pathlib.Path, filename: str, file: str, verdict: str, *extra: str) -> int:
    with pytest.raises(SystemExit) as finished:
        atk.main(
            [
                "wi",
                "effect-check",
                filename,
                "--worktree",
                str(worktree),
                "--file",
                file,
                "--verdict",
                verdict,
                "--checked-on",
                "2026-10-31",
                "--review-record",
                "claude:session:42",
                *extra,
            ],
            home=notes.parent,
            now=datetime.datetime(2026, 10, 31, tzinfo=datetime.UTC),
        )
    assert isinstance(finished.value.code, int)
    return finished.value.code


def test_public_effect_check_partial_then_complete_preserves_original(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """公開保存操作の部分確認・全確認とshow・一覧JSON、remoteへの反映を確認する。"""
    notes, worktree, path = effect_repository
    original = path.read_text(encoding="utf-8")
    assert _invoke(notes, worktree, path.name, "agent-toolkit/rules/a.md", "confirmed") == 0
    partial = path.read_text(encoding="utf-8")
    assert "effect_check:" not in partial
    assert list(effect_check.checked_files(partial)) == ["agent-toolkit/rules/a.md"]
    assert _invoke(notes, worktree, path.name, "agent-toolkit/skills/b/SKILL.md", "confirmed") == 0
    saved = path.read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(saved)
    assert parsed is not None
    assert parsed[0]["effect_check"] == "confirmed"
    restored = "\n".join(
        line for line in saved.split("\n") if not line.startswith(("effect_check:", effect_check.RECORD_PREFIX))
    )
    assert restored == original
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == git_repository.git_output(notes, "rev-parse", "origin/main")
    capsys.readouterr()
    with pytest.raises(SystemExit) as show:
        atk.main(["wi", "show", path.name, "--skip-pull"], home=notes.parent)
    assert show.value.code == 0
    assert "effect_check: confirmed" in capsys.readouterr().out
    with pytest.raises(SystemExit) as listed:
        atk.main(["wi", "list", "--target-repo", str(worktree), "--state=adopted", "--jsonl", "--skip-pull"], home=notes.parent)
    assert listed.value.code == 0
    assert json.loads(capsys.readouterr().out)["effect_check"] == "confirmed"


def test_public_effect_insufficient_requires_saved_corrective_awi(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    """不足判定は是正先保存を要し、成功時にinsufficientを記録する。"""
    notes, worktree, path = effect_repository
    original = path.read_bytes()
    assert _invoke(notes, worktree, path.name, "agent-toolkit/rules/a.md", "insufficient") != 0
    assert path.read_bytes() == original
    assert (
        _invoke(notes, worktree, path.name, "agent-toolkit/rules/a.md", "insufficient", "--corrective-awi", "missing.md") != 0
    )
    assert path.read_bytes() == original
    assert (
        _invoke(notes, worktree, path.name, "agent-toolkit/rules/a.md", "insufficient", "--corrective-awi", "corrective.md")
        == 0
    )
    parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["effect_check"] == "insufficient"


@pytest.mark.parametrize(
    "file", ["../rules/a.md", "/etc/passwd", "agent-toolkit/rules/missing.md", "agent-toolkit//rules/a.md"]
)
def test_public_effect_rejects_invalid_file_without_writing(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], file: str
) -> None:
    """逸脱・正規化の差・対応commitに無い対象を拒否する。"""
    notes, worktree, path = effect_repository
    original = path.read_bytes()
    assert _invoke(notes, worktree, path.name, file, "confirmed") != 0
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "extra",
    [
        ("--checked-on", "2026-02-30"),
        ("--checked-on", "20261031"),
        ("--verdict", "unknown"),
        ("--review-record", ""),
        ("--review-record", "line1\nline2"),
    ],
)
def test_public_effect_rejects_invalid_values(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], extra: tuple[str, str]
) -> None:
    """不正な日付・判定・記録位置を保存前に拒否する。"""
    notes, worktree, path = effect_repository
    original = path.read_bytes()
    assert _invoke(notes, worktree, path.name, "agent-toolkit/rules/a.md", "confirmed", *extra) != 0
    assert path.read_bytes() == original


def test_public_effect_rejects_duplicate_file(effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path]) -> None:
    """部分確認済みのファイルを上書きしない。"""
    notes, worktree, path = effect_repository
    assert _invoke(notes, worktree, path.name, "agent-toolkit/rules/a.md", "confirmed") == 0
    original = path.read_bytes()
    assert (
        _invoke(notes, worktree, path.name, "agent-toolkit/rules/a.md", "insufficient", "--corrective-awi", "corrective.md")
        != 0
    )
    assert path.read_bytes() == original


def test_norm_files_include_distributed_templates() -> None:
    """配布される規範テンプレートも全体確認で数える。"""
    assert effect_check.is_norm_file(".chezmoi-source/dot_claude/rules/example.md.tmpl")
    assert not effect_check.is_norm_file("app.py")


def test_result_values_ignore_fenced_examples() -> None:
    """処理結果のコード例を実際の対応commitとして読み取らない。"""
    text = "## 処理結果\n\n```text\n- 対応commit: example\n```\n\n- 対応commit: abcdef0\n"
    assert effect_check.result_values(text, "対応commit") == ["abcdef0"]


def test_public_effect_rejects_unadopted_and_ordinary_edit(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """未採用の効果記録と通常editによる採用済み本文の変更を拒否する。"""
    notes, worktree, path = effect_repository
    assert _invoke(notes, worktree, "corrective.md", "agent-toolkit/rules/a.md", "confirmed") != 0
    body = tmp_path / "body.md"
    body.write_text("上書き", encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(SystemExit) as edited:
        atk.main(["wi", "edit", path.name, "--body-file", str(body)], home=notes.parent)
    assert edited.value.code != 0
    assert path.read_bytes() == original
