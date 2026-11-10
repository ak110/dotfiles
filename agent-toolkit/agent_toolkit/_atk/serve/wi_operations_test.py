"""`Operations`の確認状態の同期失敗で返す案内文のテスト。"""

import datetime
import pathlib
import subprocess

import pytest

from agent_toolkit._atk.serve import wi_operations
from agent_toolkit._atk.wi import add, entries, frontmatter, sync
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_entry_files import write_awi, write_uwi


def test_uwi_dependency_blocked_refreshes_without_changing_awi(tmp_path: pathlib.Path) -> None:
    """未回答UWIを待つAWIだけにblockedを返し、依存先の回答だけを保存した再取得では保存状態を変えずに解除する。

    blockedは依存先の状態から導く値であり、項目自身のキーで保持する表示キャッシュへ入れると、
    依存先だけが変わった再取得で古い値を返す。
    """
    write_uwi(tmp_path, "answer.md")
    path = write_awi(tmp_path, "awi.md", depends_on=("answer.md", "ordinary.md"))
    write_awi(tmp_path, "ordinary.md")
    write_awi(tmp_path, "held.md", state="hold")
    original = path.read_bytes()
    operations = wi_operations.Operations(tmp_path)
    filters = {"type": "all", "status": "active"}

    before, _warnings = operations.entries_with_warnings(filters)
    assert {item["filename"]: item["blocked"] for item in before} == {
        "answer.md": False,
        "awi.md": True,
        "held.md": False,
        "ordinary.md": False,
    }
    assert {item["filename"]: item["state"] for item in before}["awi.md"] == "inbox"

    write_uwi(tmp_path, "answer.md", answer="回答済み")
    after, _warnings = operations.entries_with_warnings(filters)
    assert not any(item["blocked"] for item in after)
    assert path.read_bytes() == original


@pytest.mark.parametrize("legacy", [False, True])
def test_uwi_dependency_blocked_supports_legacy(tmp_path: pathlib.Path, legacy: bool) -> None:
    """旧`external-user`形式の依存も、明示依存と同じく未回答UWIを待つAWIとしてblockedを返す。"""
    write_uwi(tmp_path, "answer.md")
    if legacy:
        write_awi(
            tmp_path,
            "awi.md",
            legacy_dependency="    kind: external-user\n    condition: 回答後\n    uwi_filename: answer.md",
        )
    else:
        write_awi(tmp_path, "awi.md", depends_on=("answer.md",))
    rows, _warnings = wi_operations.Operations(tmp_path).entries_with_warnings({"type": "awi", "status": "active"})
    assert [(item["filename"], item["blocked"]) for item in rows] == [("awi.md", True)]


def test_legacy_confirmation_is_preserved_without_a_special_operation(tmp_path: pathlib.Path) -> None:
    """旧確認キーを保持し、専用の変更操作を公開しない。"""
    path = write_awi(tmp_path, "legacy.md")
    content = path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\nconfirmation: required\n")
    path.write_text(content, encoding="utf-8")
    operations = wi_operations.Operations(tmp_path)
    rows, warnings = operations.entries_with_warnings({"type": "awi", "status": "active"})
    assert not warnings
    assert "confirmation" not in rows[0]
    assert path.read_text(encoding="utf-8") == content
    assert not hasattr(operations, "set_confirmation")


@pytest.mark.parametrize("operation", ["add", "batch", "edit", "answer", "important", "transition"])
@pytest.mark.parametrize("commit_fails", [False, True])
def test_storage_saves_locally_and_preserves_commit_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    commit_fails: bool,
) -> None:
    """画面の各保存操作はremoteへ接続せず、commit失敗でも書込済み内容を保持する。"""
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="初期状態")
    filename = add.add_entries(
        notes,
        messages=["質問しますか？" if operation == "answer" else "元の要求"],
        target_repo="github.com/example/repo",
        source=None,
        now=datetime.datetime(2026, 11, 8, 12),
        entry_type="uwi" if operation == "answer" else "awi",
        question_type="yes-no" if operation == "answer" else None,
    )[0]
    path = entries.entry_path(notes, filename)
    original = path.read_text(encoding="utf-8")
    before_commit = git_repository.git_output(notes, "rev-parse", "HEAD")
    operations = wi_operations.Operations(notes)

    def unexpected_sync(*_args: object, **_kwargs: object) -> None:
        pytest.fail("画面の保存はremote同期を行わない")

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    monkeypatch.setattr(sync, "pull", unexpected_sync)
    monkeypatch.setattr(sync, "push_pending_commits", unexpected_sync)
    if commit_fails:
        monkeypatch.setattr(sync, "commit_and_push", fail_commit)

    def save() -> None:
        if operation == "add":
            operations.add(["追加した要求"], entry_type="awi", target_repo="github.com/example/repo")
        elif operation == "batch":
            operations.add_batch(f"### imported.md\n{original}")
        elif operation == "edit":
            operations.edit("inbox", filename, original.replace("元の要求", "更新した要求"), original)
        elif operation == "answer":
            operations.answer_uwi(filename, "はい", original)
        elif operation == "important":
            operations.important(filename, True, original)
        else:
            operations.transition("hold", [filename])

    if commit_fails:
        with pytest.raises(subprocess.CalledProcessError):
            save()
        assert git_repository.git_output(notes, "rev-parse", "HEAD") == before_commit
        assert git_repository.git_output(notes, "status", "--porcelain")
    else:
        save()
        assert git_repository.git_output(notes, "rev-parse", "HEAD") != before_commit
        assert not git_repository.git_output(notes, "status", "--porcelain")
    saved = path.read_text(encoding="utf-8")
    if operation == "add":
        assert any("追加した要求" in entry.read_text(encoding="utf-8") for entry in notes.rglob("*.md"))
    elif operation == "batch":
        assert entries.entry_path(notes, "imported.md").read_text(encoding="utf-8") == original
    elif operation == "edit":
        assert "更新した要求" in saved
    elif operation == "answer":
        assert saved.endswith("\nはい\n")
    elif operation == "important":
        parsed = frontmatter.parse_frontmatter(saved)
        assert parsed is not None and parsed[0]["important"] is True
    else:
        assert entries.entry_state_of(path) == "hold"


def test_importance_no_change_and_conflict_preserve_saved_entry(tmp_path: pathlib.Path) -> None:
    """重要フラグの無変更と競合は本文とcommitを更新しない。"""
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="初期状態")
    path = write_awi(notes, "entry.md")
    git_repository.commit_all(notes, "要求を保存")
    original = path.read_text(encoding="utf-8")
    operations = wi_operations.Operations(notes)
    before = git_repository.git_output(notes, "rev-parse", "HEAD")
    assert not operations.important(path.name, False, original)
    with pytest.raises(RuntimeError, match="他プロセス"):
        operations.important(path.name, True, original + "別の内容")
    assert path.read_text(encoding="utf-8") == original
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == before
