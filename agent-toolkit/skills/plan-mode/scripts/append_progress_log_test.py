"""計画の進捗ログ追記処理を検証する。"""

import datetime
import pathlib

import append_progress_log
import pytest


def _plan(heading: str = "進捗ログ", *, newline: str = "\n", final_newline: bool = True) -> str:
    """進捗ログの固定表を持つ最小本文を返す。"""
    content = newline.join(
        [
            "# 計画",
            "",
            f"## {heading}",
            "",
            "| 日時 | 完了した工程 | 結果・特記事項 |",
            "| --- | --- | --- |",
        ]
    )
    return content + (newline if final_newline else "")


@pytest.mark.parametrize("heading", ["進捗ログ", "進捗ログ（実行時）"])
def test_appends_local_clock_row_for_current_and_legacy_heading(tmp_path: pathlib.Path, heading: str) -> None:
    """現行・旧見出しの固定3列表へ注入時計の1行だけを追加する。"""
    path = tmp_path / "plan.md"
    path.write_text(_plan(heading), encoding="utf-8")
    now = datetime.datetime(2026, 9, 20, 12, 34, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

    append_progress_log.append_progress_log(path, "工程", "成功", clock=lambda: now)

    assert path.read_text(encoding="utf-8").endswith("| 2026-09-20 12:34 | 工程 | 成功 |\n")


def test_preserves_crlf_and_missing_final_newline(tmp_path: pathlib.Path) -> None:
    """既存のCRLFを維持し、末尾改行が無い表にも1行を追加する。"""
    path = tmp_path / "plan.md"
    path.write_bytes(_plan(newline="\r\n", final_newline=False).encode())
    now = datetime.datetime(2026, 9, 20, 3, 34, tzinfo=datetime.UTC)

    append_progress_log.append_progress_log(path, "工程", "成功", clock=lambda: now)

    updated = path.read_bytes()
    assert b"\r\n" in updated
    assert not updated.endswith(b"\n")
    assert updated.endswith("| 2026-09-20 03:34 | 工程 | 成功 |".encode())


def test_escapes_table_cells(tmp_path: pathlib.Path) -> None:
    """改行、バックスラッシュ及びパイプを1つのGFM表セルへ収める。"""
    path = tmp_path / "plan.md"
    path.write_text(_plan(), encoding="utf-8")
    now = datetime.datetime(2026, 9, 20, 3, 34, tzinfo=datetime.UTC)

    append_progress_log.append_progress_log(path, "工程|A\nB", r"結果\値|C", clock=lambda: now)

    assert r"工程\|A<br>B | 結果\\値\|C" in path.read_text(encoding="utf-8")


def test_ignores_progress_heading_inside_code_fence(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.md"
    path.write_text("# 計画\n\n````markdown\n## 進捗ログ\n````\n\n" + _plan().removeprefix("# 計画\n\n"), encoding="utf-8")
    now = datetime.datetime(2026, 9, 20, 3, 34, tzinfo=datetime.UTC)

    append_progress_log.append_progress_log(path, "工程", "成功", clock=lambda: now)

    assert path.read_text(encoding="utf-8").endswith("| 2026-09-20 03:34 | 工程 | 成功 |\n")


@pytest.mark.parametrize(
    "content",
    [
        "# 計画\n",
        _plan() + _plan(),
        "# 計画\n\n## 進捗ログ\n\n本文だけ。\n",
        "# 計画\n\n## 進捗ログ\n\n| 日時 | 工程 | 結果 |\n| --- | --- | --- |\n",
    ],
)
def test_rejects_invalid_structure_without_changes(tmp_path: pathlib.Path, content: str) -> None:
    """見出し又は固定表が不正なら元のバイト列を変更しない。"""
    path = tmp_path / "plan.md"
    original = content.encode()
    path.write_bytes(original)

    with pytest.raises(append_progress_log.ProgressLogError):
        append_progress_log.append_progress_log(path, "工程", "結果")

    assert path.read_bytes() == original


def test_rejects_non_utf8_without_changes(tmp_path: pathlib.Path) -> None:
    """UTF-8でない入力は変更しない。"""
    path = tmp_path / "plan.md"
    original = b"\x81"
    path.write_bytes(original)

    with pytest.raises(append_progress_log.ProgressLogError, match="UTF-8"):
        append_progress_log.append_progress_log(path, "工程", "結果")

    assert path.read_bytes() == original


def test_writer_failure_keeps_original_file(tmp_path: pathlib.Path) -> None:
    """原子的書込みが失敗した場合は元ファイルを保つ。"""
    path = tmp_path / "plan.md"
    original = _plan().encode()
    path.write_bytes(original)

    def fail_writer(_path: pathlib.Path, _content: str) -> None:
        raise OSError("write failed")

    with pytest.raises(OSError, match="write failed"):
        append_progress_log.append_progress_log(path, "工程", "結果", writer=fail_writer)

    assert path.read_bytes() == original
