"""review table batchの契約を検証する。"""

from __future__ import annotations

import contextlib
import json
import pathlib
from collections.abc import Generator

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_table as table
from agent_toolkit._testing.review_table_support import _TRACK, _isolated_home

__all__ = ["_isolated_home"]

# round 2への登録でCLIが求める指摘本文の必須ラベル。本文の保持を確かめる試験は、検証対象の本文をこの後ろへ置く。
_LABELS = "類似見直し完了件数: 3\n検収: 修正後の版で該当箇所を確認する\n区分: 新しい変更\n"


def test_public_add_items_preserves_bodies_and_uses_one_locked_save_and_readback(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """複数本文を共通roundで追記し、既存行を含めて同じロックで1回保存・読戻しする。"""
    path = tmp_path / "plan.exec-review.tsv"
    table.add(path, "1", _TRACK, "元の位置", "保持する指摘")
    assert capsys.readouterr().out.startswith("成功: 指摘行を1件追加した:")
    items = [
        {"location": "日本語の位置\n行2\t列", "issue": _LABELS + '複数行\n"引用"と`記号`\t$(保持)', "level": "仕様"},
        {"location": "別の位置", "issue": _LABELS + "結合文字e\u0301を保持\n"},
    ]
    source = tmp_path / "items.json"
    source.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    events: list[str] = []
    original_lock = table._table_lock  # pylint: disable=protected-access
    original_write = table._write_atomic  # pylint: disable=protected-access
    original_read = table._read  # pylint: disable=protected-access

    @contextlib.contextmanager
    def lock(target: pathlib.Path) -> Generator[None]:
        with original_lock(target):
            events.append("lock")
            yield
            events.append("unlock")

    def write(target: pathlib.Path, rows: list[list[str]]) -> None:
        events.append("write")
        original_write(target, rows)

    def read(target: pathlib.Path) -> list[list[str]]:
        events.append("read")
        return original_read(target)

    monkeypatch.setattr(table, "_table_lock", lock)
    monkeypatch.setattr(table, "_write_atomic", write)
    monkeypatch.setattr(table, "_read", read)
    with pytest.raises(SystemExit) as returned:
        atk.main(
            ["review-table", "add", str(path), "--round=2", f"--track={_TRACK}", "--items-file", str(source)],
            home=tmp_path,
        )
    assert returned.value.code == 0
    assert events == ["lock", "read", "write", "read", "unlock"]
    saved = original_read(path)
    assert saved[0] == ["1", _TRACK, "元の位置", "保持する指摘", "", "", ""]
    assert saved[1:] == [
        ["2", _TRACK, item["location"], item["issue"] + "\n<!-- review-issue-v2:acceptance -->", item.get("level", ""), "", ""]
        for item in items
    ]
    output = capsys.readouterr()
    assert output.out.startswith("成功: 指摘行を2件追加した:")
    assert not output.err
    assert items[0]["issue"] not in output.out


@pytest.mark.parametrize(
    "suffix",
    [
        {"location": "後半", "issue": _LABELS + "不正", "level": "重大"},
        {"location": "後半", "level": "詳細"},
        {"location": "後半", "issue": _LABELS + "不正", "level": ""},
        {"location": "後半", "issue": 1, "level": "詳細"},
        {"location": "後半", "issue": _LABELS + "不正", "level": "詳細", "round": "3"},
        {"location": "  ", "issue": _LABELS + "不正", "level": "詳細"},
        {"location": "正常", "issue": _LABELS + "最初の行", "level": "詳細"},
        {"location": "既存", "issue": _LABELS + "保存済み", "level": "詳細"},
        {"location": "後半", "issue": "類似見直し完了件数を書いていない", "level": "詳細"},
        {"location": "後半", "issue": "類似見直し完了件数: 全部\n区分: 新しい変更", "level": "詳細"},
        {"location": "後半", "issue": _LABELS.replace("新しい変更", "不明") + "指摘", "level": "詳細"},
        {"location": "後半", "issue": "指摘\n類似見直し完了件数: 3\n区分: 新しい変更", "level": "詳細"},
    ],
    ids=[
        "level",
        "missing-key",
        "empty-level",
        "value-type",
        "extra-key",
        "empty-location",
        "batch-duplicate",
        "existing-duplicate",
        "missing-similar-review",
        "non-numeric-similar-review",
        "invalid-classification",
        "missing-acceptance",
    ],
)
def test_public_add_items_rejects_invalid_later_item_without_partial_write(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], suffix: dict[str, str | int]
) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    table.add(path, "2", _TRACK, "既存", _LABELS + "保存済み")
    before = path.read_bytes()
    source = tmp_path / "items.json"
    source.write_text(
        json.dumps([{"location": "正常", "issue": _LABELS + "最初の行", "level": "詳細"}, suffix], ensure_ascii=False),
        encoding="utf-8",
    )
    capsys.readouterr()
    with pytest.raises(SystemExit) as returned:
        atk.main(
            ["review-table", "add", str(path), "--round=2", f"--track={_TRACK}", "--items-file", str(source)],
            home=tmp_path,
        )
    assert returned.value.code != 0
    assert path.read_bytes() == before
    output = capsys.readouterr()
    assert not output.out
    assert "次の操作:" in output.err
    if suffix.get("issue") == "指摘\n類似見直し完了件数: 3\n区分: 新しい変更":
        assert "検収:" in output.err
        assert "review-record.md" in output.err


@pytest.mark.parametrize("option", ["--level=詳細", "--location-file=location.txt", "--issue-file=issue.txt"])
def test_public_add_items_rejects_single_options(tmp_path: pathlib.Path, option: str) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    with pytest.raises(SystemExit) as returned:
        atk.main(
            ["review-table", "add", str(path), "--round=1", f"--track={_TRACK}", "--items-file=items.json", option],
            home=tmp_path,
        )
    assert returned.value.code != 0
    assert not path.exists()


@pytest.mark.parametrize("content", [b"[]", b"{}", b"[", b"\xff"])
def test_public_add_items_rejects_invalid_document_without_creating_table(tmp_path: pathlib.Path, content: bytes) -> None:
    """空配列・型不正・JSON不正・UTF-8不正を表の作成前に拒否する。"""
    path = tmp_path / "plan.exec-review.tsv"
    source = tmp_path / "items.json"
    source.write_bytes(content)
    with pytest.raises(SystemExit) as returned:
        atk.main(
            ["review-table", "add", str(path), "--round=1", f"--track={_TRACK}", "--items-file", str(source)],
            home=tmp_path,
        )
    assert returned.value.code != 0
    assert not path.exists()


@pytest.mark.parametrize("alter_existing", [False, True])
def test_public_add_items_detects_changed_saved_rows(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], alter_existing: bool
) -> None:
    """追加行のレベルと既存行の変更を、保存後の全行読戻しで失敗にする。"""
    path = tmp_path / "plan.exec-review.tsv"
    table.add(path, "1", _TRACK, "既存", "保持する指摘")
    source = tmp_path / "items.json"
    source.write_text(
        json.dumps([{"location": "位置", "issue": _LABELS + "内容", "level": "仕様"}], ensure_ascii=False), encoding="utf-8"
    )
    original_write = table._write_atomic  # pylint: disable=protected-access

    def changed_write(target: pathlib.Path, rows: list[list[str]]) -> None:
        changed = [row.copy() for row in rows]
        changed[0 if alter_existing else -1][4] = "要件"
        original_write(target, changed)

    monkeypatch.setattr(table, "_write_atomic", changed_write)
    capsys.readouterr()
    with pytest.raises(SystemExit) as returned:
        atk.main(
            ["review-table", "add", str(path), "--round=2", f"--track={_TRACK}", "--items-file", str(source)],
            home=tmp_path,
        )
    assert returned.value.code != 0
    output = capsys.readouterr()
    assert not output.out
    assert "全行が更新内容と一致しない" in output.err
    assert "保存は済んでいる" in output.err
