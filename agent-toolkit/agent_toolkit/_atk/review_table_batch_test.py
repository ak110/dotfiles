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


def test_public_add_items_preserves_bodies_and_uses_one_locked_save_and_readback(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """複数本文を共通roundで追記し、既存行を含めて同じロックで1回保存・読戻しする。"""
    path = tmp_path / "plan.exec-review.tsv"
    table.add(path, "1", _TRACK, "元の位置", "保持する指摘")
    assert capsys.readouterr().out.startswith("成功: 指摘行を1件追加した:")
    items = [
        {"location": "日本語の位置\n行2\t列", "issue": '複数行\n"引用"と`記号`\t$(保持)', "level": "仕様"},
        {"location": "別の位置", "issue": "結合文字e\u0301を保持\n", "level": "実装"},
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
    assert saved[0] == ["1", _TRACK, "元の位置", "保持する指摘", "詳細", "", ""]
    assert saved[1:] == [["2", _TRACK, item["location"], item["issue"], item["level"], "", ""] for item in items]
    output = capsys.readouterr()
    assert output.out.startswith("成功: 指摘行を2件追加した:")
    assert not output.err
    assert items[0]["issue"] not in output.out


@pytest.mark.parametrize(
    "suffix",
    [
        {"location": "後半", "issue": "不正", "level": "重大"},
        {"location": "後半", "issue": "不正"},
        {"location": "後半", "issue": 1, "level": "詳細"},
        {"location": "後半", "issue": "不正", "level": "詳細", "round": "3"},
        {"location": "  ", "issue": "不正", "level": "詳細"},
        {"location": "正常", "issue": "最初の行", "level": "詳細"},
        {"location": "既存", "issue": "保存済み", "level": "詳細"},
    ],
    ids=["level", "missing-key", "value-type", "extra-key", "empty-location", "batch-duplicate", "existing-duplicate"],
)
def test_public_add_items_rejects_invalid_later_item_without_partial_write(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], suffix: dict[str, str | int]
) -> None:
    """後半の不正や重複がある配列では、先頭の正常な指摘も保存しない。"""
    path = tmp_path / "plan.exec-review.tsv"
    table.add(path, "2", _TRACK, "既存", "保存済み")
    before = path.read_bytes()
    source = tmp_path / "items.json"
    source.write_text(
        json.dumps([{"location": "正常", "issue": "最初の行", "level": "詳細"}, suffix], ensure_ascii=False),
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


@pytest.mark.parametrize("option", ["--level=詳細", "--location-file=location.txt", "--issue-file=issue.txt"])
def test_public_add_items_rejects_single_options(tmp_path: pathlib.Path, option: str) -> None:
    """配列入力と単数形のオプションを併用した場合は書き込まない。"""
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
    source.write_text('[{"location":"位置","issue":"内容","level":"仕様"}]', encoding="utf-8")
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
