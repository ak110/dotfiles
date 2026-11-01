"""`Operations`の確認状態の同期失敗で返す案内文のテスト。"""

import pathlib

import pytest

from agent_toolkit._atk.serve import wi_operations
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
    entries, _warnings = wi_operations.Operations(tmp_path).entries_with_warnings({"type": "awi", "status": "active"})
    assert [(item["filename"], item["blocked"]) for item in entries] == [("awi.md", True)]


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
