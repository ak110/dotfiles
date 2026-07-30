"""check_plan_file.pyの軽量機械チェックのテスト。"""

from __future__ import annotations

import pathlib

import pytest
from check_plan_file import main


def _write_plan(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    plan = tmp_path / "sample.md"
    plan.write_text(body, encoding="utf-8")
    return plan


def test_missing_h3_errors(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _write_plan(
        tmp_path,
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n",
    )
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "H3見出しが無い対象ファイル" in captured.err


def test_code_block_presence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n```text\n[新設]\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0


def test_change_structure_outside_change_section_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`## 変更内容`節外のチェックボックスとH3見出しを変更対象として検出しない。"""
    body = (
        "## 調査結果\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`\n\n"
        "### `does/not/exist.md`\n\n変更内容ではない説明。\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_all_repeated_change_sections_are_checked(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """複数の`## 変更内容`節がある場合は全出現の対象ファイル一覧を検査する。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `first.md`（新設）\n\n"
        "### `first.md`\n\n```text\ncontent\n```\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `second.md`（新設）\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "H3見出しが無い対象ファイル: ['second.md']" in capsys.readouterr().err


def test_nonexistent_path_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`\n\n"
        "### `does/not/exist.md`\n\n```text\nplaceholder\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在確認できないパス" in captured.err


def test_unrelated_new_marker_before_h3_does_not_skip_existence_check(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対象H3直前の無関係な`（新設）`では実在確認を免除しない。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`\n\n"
        "別項目の説明（新設）\n"
        "### `does/not/exist.md`\n\n```text\ncontent\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "実在確認できないパス: does/not/exist.md" in capsys.readouterr().err


def test_new_marker_on_checkbox_skips_existence_check(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対象パスのチェックボックスにある`（新設）`は実在確認を免除する。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`（新設）\n\n"
        "### `does/not/exist.md`\n\n```text\ncontent\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_nonexistent_path_with_prefixed_deletion_marker_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`（現行N行、廃止・削除）`のような前置き付き複合形のマーカーも実在確認を免除する。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`（現行87行、廃止・削除）\n\n"
        "### `does/not/exist.md`\n\n```text\n本ファイルを削除する。\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "実在確認できないパス" not in captured.err


def test_fence_nesting_violation_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
        "### `foo.md`\n\n```text\n[新設]\n````markdown\nnested\n````\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
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


def test_unknown_skill_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
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
    """接頭辞違いで存在する候補があればerror文へ候補名を添える。"""
    body = "## 実行方法\n\n- Skillツールで`coding-standards`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "接頭辞違いの候補: `agent-toolkit:coding-standards`" in captured.err


def test_unknown_skill_name_without_prefix_mismatch_candidate(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """接頭辞違いの候補も実在しない場合はその旨をerror文へ添える。"""
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "接頭辞違いの候補も無し" in captured.err


def test_agent_tool_subagent_name_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Agentツールで`構文のサブエージェント名は実在すればerrorとして検出しない。"""
    body = "## 実行方法\n\n- Agentツールで`agent-toolkit:plan-impl-executor`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "の疑い" not in captured.err


def test_agent_tool_unknown_subagent_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Agentツールで`構文で実在しない名前はサブエージェント名としてerrorにする。"""
    body = "## 実行方法\n\n- Agentツールで`agent-toolkit:no-such-agent`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在しないサブエージェント名の疑い" in captured.err
    assert "no-such-agent" in captured.err


def test_agent_tool_with_skill_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Agentツールで`構文にスキル名を書いた取り違えはerrorにする。"""
    body = "## 実行方法\n\n- Agentツールで`agent-toolkit:coding-standards`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在しないサブエージェント名の疑い" in captured.err
    assert "coding-standards" in captured.err


def test_local_subagent_name_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.claude/agents/*.md`のローカル定義はサブエージェント候補として受理する。"""
    local_agents = tmp_path / ".claude" / "agents"
    local_agents.mkdir(parents=True)
    (local_agents / "local-reviewer.md").write_text("# local-reviewer\n", encoding="utf-8")
    body = "## 実行方法\n\n- Agentツールで`local-reviewer`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "実在しないサブエージェント名の疑い" not in captured.err


def test_skill_tool_with_subagent_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Skillツールで`構文にサブエージェント名を書いた取り違えはerrorにする。"""
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:plan-impl-executor`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在しないスキル名の疑い" in captured.err


def test_slash_prefix_with_subagent_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/`接頭辞にサブエージェント名を書いた取り違えはスキル名としてerrorにする。"""
    body = "## 実行方法\n\n- `/agent-toolkit:plan-reviewer`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在しないスキル名の疑い" in captured.err
    assert "plan-reviewer" in captured.err


def test_same_name_used_correctly_and_incorrectly_in_different_syntax(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同名を正しい構文と誤った構文の双方で使った場合、誤った側だけを独立してerrorにする。

    名前だけをキーにする実装では一方の種別が失われ、この取り違えを検出できない。
    """
    body = (
        "## 実行方法\n\n"
        "- Skillツールで`agent-toolkit:plan-reviewer`を呼び出す\n"
        "- Agentツールで`agent-toolkit:plan-reviewer`を起動する\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    # `agent-toolkit:plan-reviewer`はサブエージェント定義であり、スキル定義ではない。
    # `Agentツールで`側は正しい呼び出しでありerrorとして検出しない。`Skillツールで`側は
    # 取り違えであり、スキル名として実在しないことをerrorとして報告する。
    assert "実在しないスキル名の疑い: `agent-toolkit:plan-reviewer`" in captured.err
    assert "実在しないサブエージェント名の疑い" not in captured.err


def test_bare_agent_toolkit_reference_accepts_subagent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """構文を伴わない参照はスキル・サブエージェントいずれかに実在すればerrorとして検出しない。"""
    body = "## 実行方法\n\n- レビューは`agent-toolkit:plan-reviewer`の担当とする\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "の疑い" not in captured.err


def test_sample_plan_has_no_invocation_name_error(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """配布している計画ファイルの記述例が呼び出し名の実在確認でerrorを報告しない。

    記述例は先頭行を除く全体が````markdown`フェンス内にあり、構造抽出処理が
    フェンス外の行に限定されるため、埋め込み内の架空パス・呼び出し名を含め
    計画構造が1件も抽出されずerror・warningいずれも生じない。
    本テストの検証対象は呼び出し名の実在確認に限定するため、当該error文言
    （`実在しないスキル名の疑い`・`実在しないサブエージェント名の疑い`）が
    標準エラー出力に含まれないことを直接検証する。
    """
    sample = pathlib.Path(__file__).resolve().parents[1] / "references" / "sample.md"
    assert sample.is_file()
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(sample)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "実在しないスキル名の疑い" not in captured.err
    assert "実在しないサブエージェント名の疑い" not in captured.err
    assert "実在しないスキル・サブエージェント名の疑い" not in captured.err


def test_deletion_marker_without_deletion_word_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n### `foo.md`\n\n```text\n節を圧縮する\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
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


def test_deletion_marker_with_deletion_word_only_in_prose_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """削除指示語が地の文のみに現れ`text`コードブロック内に無い場合は食い違いとしてerrorにする。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n本ファイルを削除する。\n\n```text\n節を圧縮する\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "指定内容の食い違いの疑い" in captured.err


def test_deletion_marker_with_deletion_word_only_in_python_block_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """削除指示語が`python`ブロックのみに現れ`text`ブロックが無い場合も食い違いとしてerrorにする。

    `_has_code_block_after`は任意の情報文字列のコードブロック存在で通過するため、
    `text`以外のブロックのみのH3が両検査を回避しないことを確認する。
    """
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n```python\n# 本ファイルを削除する\nx = 1\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
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


_META_NORM_PLAN_BODY = (
    "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
    "### `foo.md`\n\n```text\n+- 起動時にnameを指定しない\n```\n"
)
_NO_TRIGGER_PLAN_BODY = (
    "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n```text\n+- 起動手順を追記する\n```\n"
)


@pytest.mark.parametrize(
    ("investigation_section", "expect_error"),
    [
        pytest.param(
            "## 調査結果\n\n- 対象パターン: 汎用禁止形バレット\n- 検出件数: 1件\n- 対応方針: 機械チェックと対で記載する\n\n"
            + _META_NORM_PLAN_BODY,
            False,
            id="required_items_present",
        ),
        pytest.param(
            "## 調査結果\n\n- 対象パターン: 汎用禁止形バレット\n- 対応方針: 機械チェックと対で記載する\n\n"
            + _META_NORM_PLAN_BODY,
            True,
            id="required_item_missing",
        ),
        pytest.param(
            "## 調査結果\n\n- 既存節へ手順を1件追記する\n\n" + _NO_TRIGGER_PLAN_BODY,
            False,
            id="not_triggered",
        ),
    ],
)
def test_retroactive_scan_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    investigation_section: str,
    expect_error: bool,
) -> None:
    """遡及スキャン必須3語の充足有無に応じてerrorの有無が切り替わる。"""
    plan = _write_plan(tmp_path, investigation_section)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == (1 if expect_error else 0)
    captured = capsys.readouterr()
    assert ("遡及スキャン記録の不足の疑い" in captured.err) is expect_error


def test_no_violation_returns_zero_without_stderr(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n```text\n[新設]\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_warning_only_returns_zero_with_warn_prefix(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- `agent-toolkit:session-review`で振り返りを実施する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    stderr = capsys.readouterr().err
    assert stderr.startswith("[warn] ")
    assert "セッション運用工程" in stderr


def test_error_returns_one_without_warn_prefix(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    stderr = capsys.readouterr().err
    assert "H3見出しが無い対象ファイル" in stderr
    assert not stderr.startswith("[warn] ")


def test_no_argument_returns_two_with_usage(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["check_plan_file.py"])
    assert main() == 2
    assert capsys.readouterr().err == "usage: check_plan_file.py <plan-file-path>（使用法: 計画ファイルのパスを1つ指定する）\n"


def test_nonexistent_plan_file_returns_two(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = tmp_path / "missing.md"
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 2
    assert "計画ファイルを読み込めない" in capsys.readouterr().err


def test_invalid_utf8_plan_file_returns_two(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = tmp_path / "invalid.md"
    plan.write_bytes(b"\xff")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 2
    assert "計画ファイルを読み込めない" in capsys.readouterr().err


def test_duplicate_paths_return_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `foo.md`（新設）\n- [ ] `foo.md`（新設）\n\n"
        "### `foo.md`\n\n```text\nfirst\n```\n\n"
        "### `foo.md`\n\n```text\nsecond\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    stderr = capsys.readouterr().err
    assert "対象ファイル一覧に重複したパス" in stderr
    assert "重複したH3見出し" in stderr


def test_nonexistent_absolute_h3_path_returns_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing" / "foo.md"
    body = f"## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `{missing}`\n\n### `{missing}`\n\n```text\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "実在確認できないパス" in capsys.readouterr().err


def test_h3_without_code_block_returns_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n変更後の説明のみ。\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "コードブロックが無いH3" in capsys.readouterr().err


def test_h3_without_checkbox_returns_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "foo.md"
    existing.write_text("content\n", encoding="utf-8")
    body = f"## 変更内容\n\n### 対象ファイル一覧\n\n### `{existing}`\n\n```text\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "対象ファイル一覧に無いH3見出し" in capsys.readouterr().err


def test_tilde_code_block_returns_zero(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n~~~text\ncontent\n~~~\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_indented_fenced_code_block_returns_zero(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    indent: str,
) -> None:
    """1〜3文字インデントしたフェンスをH3配下のコードブロックとして認識する。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        f"### `foo.md`\n\n{indent}```text\n本ファイルを削除する。\n{indent}```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("following_heading", ["## 実行方法", "### 補足", "#### 詳細"])
def test_h3_body_ends_at_any_supported_heading(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    following_heading: str,
) -> None:
    """対象H3より後のH2〜H4配下にあるフェンスを対象H3のコードブロックとみなさない。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
        f"### `foo.md`\n\n変更後の説明のみ。\n\n{following_heading}\n\n```text\n無関係なコードブロック\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "コードブロックが無いH3: foo.md" in capsys.readouterr().err


def test_repeated_execution_method_sections_are_all_checked(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同名H2が再出現しても、先行する節の呼び出し名検査を保持する。"""
    body = (
        "## 実行方法\n\n- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n\n"
        "## 実行方法\n\n- 実装する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "実在しないスキル名の疑い" in capsys.readouterr().err


def test_embedded_and_actual_same_h3_uses_actual_section(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
        "````markdown\n### `foo.md`\n\n埋め込み側にはコードブロックが無い。\n````\n\n"
        "### `foo.md`\n\n~~~text\ncontent\n~~~\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_fenced_example_in_execution_method_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 実行方法\n\n- 実装する\n\n````markdown\n"
        "- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n"
        "- `agent-toolkit:session-review`で振り返りを実施する\n"
        "````\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    stderr = capsys.readouterr().err
    assert "実在しないスキル名の疑い" not in stderr
    assert "セッション運用工程" not in stderr


def test_retroactive_scan_words_in_fenced_example_do_not_satisfy_requirement(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 調査結果\n\n````text\n対象パターン\n検出件数\n対応方針\n````\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
        "### `foo.md`\n\n```text\n+- 起動時にnameを指定しない\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "遡及スキャン記録の不足の疑い" in capsys.readouterr().err


def test_entire_plan_structure_in_markdown_fence_returns_zero(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "# 記述例\n\n````markdown\n## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `does/not/exist.md`\n\n### `does/not/exist.md`\n\n"
        "```text\ncontent\n```\n\n## 実行方法\n\n"
        "- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n````\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_embedded_h2_with_session_operation_is_not_a_section(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "````markdown\n## 実行方法\n\n"
        "- `agent-toolkit:session-review`で振り返りを実施する\n````\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    stderr = capsys.readouterr().err
    assert "セッション運用工程" not in stderr


def test_only_unfenced_violation_is_reported(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "outer-missing.md"
    body = (
        "````markdown\n## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `embedded-missing.md`\n\n### `embedded-missing.md`\n\n```text\ncontent\n```\n````\n\n"
        f"## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `{outer}`\n\n"
        f"### `{outer}`\n\n```text\ncontent\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    stderr = capsys.readouterr().err
    assert str(outer) in stderr
    assert "embedded-missing.md" not in stderr


def test_deprecated_identifier_list_in_fence_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "leftover.py").write_text("def _leftover_helper():\n    pass\n", encoding="utf-8")
    body = "````markdown\n#### 廃止・改名対象一覧\n\n- `_leftover_helper`\n````\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_deleted_checkbox_in_fence_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "````markdown\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n```text\n節を圧縮する\n```\n````\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_entire_plan_structure_in_tilde_fence_returns_zero(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "~~~~markdown\n## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `does/not/exist.md`（廃止・削除）\n\n"
        "#### 廃止・改名対象一覧\n\n- `_leftover_helper`\n\n"
        "### `does/not/exist.md`\n\n```text\n節を圧縮する\n```\n"
        "~~~~\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""
