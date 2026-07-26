"""check_plan_file.pyの軽量機械チェックのテスト。"""

from __future__ import annotations

import pathlib

import pytest
from check_plan_file import main


def _write_plan(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    plan = tmp_path / "sample.md"
    plan.write_text(body, encoding="utf-8")
    return plan


def test_missing_h3_warns(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _write_plan(
        tmp_path,
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n",
    )
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "H3見出しが無い対象ファイル" in captured.err


def test_code_block_presence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n```text\n[新設]\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0


def test_nonexistent_path_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`\n\n"
        "### `does/not/exist.md`\n\n```text\nplaceholder\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "実在確認できないパス" in captured.err


def test_fence_nesting_violation_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
        "### `foo.md`\n\n```text\n[新設]\n````markdown\nnested\n````\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "フェンス" in captured.err


def test_execution_method_session_review_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 実行方法\n\n- 実装する\n- `agent-toolkit:session-review`で振り返りを実施する\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "セッション運用工程" in captured.err


def test_execution_method_without_session_ops_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- 実装する\n- 検証する\n- コミットする\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "セッション運用工程" not in captured.err
