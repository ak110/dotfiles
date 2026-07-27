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


def test_unknown_skill_name_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "実在しないスキル名の疑い" in captured.err
    assert "no-such-skill" in captured.err


def test_known_skill_name_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:coding-standards`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "実在しないスキル名の疑い" not in captured.err


def test_unknown_skill_name_with_prefix_mismatch_suggests_candidate(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """接頭辞違いで存在する候補があれば警告文へ候補名を添える。"""
    body = "## 実行方法\n\n- Skillツールで`coding-standards`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "接頭辞違いの候補: `agent-toolkit:coding-standards`" in captured.err


def test_unknown_skill_name_without_prefix_mismatch_candidate(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """接頭辞違いの候補も実在しない場合はその旨を警告文へ添える。"""
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "接頭辞違いの候補も無し" in captured.err


def test_deletion_marker_without_deletion_word_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n### `foo.md`\n\n```text\n節を圧縮する\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "指定内容の食い違いの疑い" in captured.err


def test_deletion_marker_with_deletion_word_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n```text\n本ファイルを削除する。退避先: archive/foo.md\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "指定内容の食い違いの疑い" not in captured.err


def test_deletion_marker_with_deletion_word_only_in_prose_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """削除指示語が地の文のみに現れ`text`コードブロック内に無い場合は食い違いとして警告する。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n本ファイルを削除する。\n\n```text\n節を圧縮する\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "指定内容の食い違いの疑い" in captured.err


def test_deletion_marker_with_deletion_word_only_in_python_block_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """削除指示語が`python`ブロックのみに現れ`text`ブロックが無い場合も食い違いとして警告する。

    `_has_code_block_after`は任意の情報文字列のコードブロック存在で通過するため、
    `text`以外のブロックのみのH3が両検査を回避しないことを確認する。
    """
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n```python\n# 本ファイルを削除する\nx = 1\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "指定内容の食い違いの疑い" in captured.err


def test_deprecated_identifier_residual_file_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "leftover.py").write_text("x = 1\n", encoding="utf-8")
    body = "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `scripts/leftover.py`\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "廃止・改名対象一覧の識別子が残存している疑い" in captured.err
    assert "scripts/leftover.py" in captured.err


def test_deprecated_identifier_removed_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `_removed_helper`\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "廃止・改名対象一覧の識別子が残存している疑い" not in captured.err


def test_deprecated_identifier_type_annotated_constant_residual_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """型注釈付き定数定義（`NAME: Type = value`）の残存も検出する。"""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "leftover.py").write_text(
        "_LEFTOVER_CONSTANT: frozenset[str] = frozenset({'x'})\n", encoding="utf-8"
    )
    body = "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `_LEFTOVER_CONSTANT`\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "廃止・改名対象一覧の識別子が残存している疑い" in captured.err
    assert "_LEFTOVER_CONSTANT" in captured.err


def test_deprecated_identifier_excludes_git_and_plan_file(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.git`配下・一時複製・計画ファイル自身は遡及走査の対象外とする。"""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "leftover.py").write_text("def _removed_helper():\n    pass\n", encoding="utf-8")
    (tmp_path / ".plan-check-sample-1234.md").write_text("`_removed_helper`\n", encoding="utf-8")
    body = (
        "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `_removed_helper`\n\n### 対象ファイル一覧\n\n"
        "計画ファイル自身が定義例を掲載する場合の自己参照除外を確認する。\n\n"
        "```python\ndef _removed_helper():\n    pass\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "廃止・改名対象一覧の識別子が残存している疑い" not in captured.err
