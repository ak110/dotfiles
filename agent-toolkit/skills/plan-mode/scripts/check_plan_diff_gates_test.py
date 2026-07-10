"""agent-toolkit/skills/plan-mode/scripts/check_plan_diff_gates.py のテスト。

計画ファイル`## 変更内容`配下の差分ブロック本文へ`_scope_escalation.py` CLIと
`uvx pyfltr run-for-agent --commands=textlint`を事前適用する検査スクリプトを
`monkeypatch.setattr("subprocess.run", ...)`でsubprocessをmockして検証する。
"""

# 対象スクリプトは単独実行スクリプトであり公開APIは`main()`のみだが、
# 個別関数の抽出仕様・副作用・境界を単体レベルで検証するためprotected-accessを許容する。
# pylint: disable=protected-access,unused-argument

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import types

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "check_plan_diff_gates.py"


def _load_module() -> types.ModuleType:
    """PEP 723単独実行スクリプトをテスト用にimportする。"""
    spec = importlib.util.spec_from_file_location("check_plan_diff_gates", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MOD = _load_module()


def _write(path: pathlib.Path, content: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _stub_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    scope_returncode: int = 0,
    scope_stdout: str = "",
    textlint_returncode: int = 0,
    textlint_stdout: str = "",
) -> list[list[str]]:
    """subprocess.runを差し替えてscope_escalation/textlint双方の応答を注入する。"""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if any("_scope_escalation.py" in part for part in cmd):
            return _completed(scope_returncode, stdout=scope_stdout)
        if any(part == "pyfltr" or part.endswith("pyfltr") for part in cmd):
            return _completed(textlint_returncode, stdout=textlint_stdout)
        return _completed(0)

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls


class TestExtractDiffBlocks:
    """`_extract_diff_blocks`の抽出仕様を網羅する。"""

    def test_new_label_block_is_extracted(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n[新設]:\n\n```text\nnew content line1\nnew content line2\n```\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert len(blocks) == 1
        label, _line, body = blocks[0]
        assert label == "`foo.md`"
        assert body == "new content line1\nnew content line2"

    def test_replacement_label_block_is_extracted(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n[置換後]:\n\n```text\nreplaced body\n```\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert len(blocks) == 1

    def test_replacement_full_label_block_is_extracted(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n[置換後（全文）]:\n\n```text\nwhole file content\n```\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert len(blocks) == 1

    def test_current_label_block_is_excluded(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n[現行]:\n\n```text\nold body\n```\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert not blocks

    def test_new_h3_marker_extracts_body(self) -> None:
        text = "## 変更内容\n\n### `new_file.py`（新設）\n\n新設スクリプト。\n\n```text\nsome body under new h3\n```\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][0].startswith("`new_file.py`")

    def test_trigger_line_extracts_following_block(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n追記内容:\n\n```text\nadded line\n```\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "added line"

    def test_reduction_heading_extracts_body(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n#### 縮減対象\n\n```text\nold verbose text\n```\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert len(blocks) == 1

    def test_non_text_fence_is_ignored(self) -> None:
        text = "## 変更内容\n\n### `foo.py`\n\n[新設]:\n\n```python\ndef f(): pass\n```\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert not blocks

    def test_no_change_section_returns_empty(self) -> None:
        text = "## 背景\n\n本文のみ。\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert not blocks

    def test_block_start_line_is_computed(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n[新設]:\n\n```text\nline\n```\n"
        blocks = list(_MOD._extract_diff_blocks(text))
        assert blocks[0][1] > 0


class TestRunScopeEscalation:
    """`_run_scope_escalation`のsubprocessモック検証。"""

    def test_returns_category_when_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=2, scope_stdout="pattern-conformance\n")
        assert _MOD._run_scope_escalation("some body") == "pattern-conformance"

    def test_returns_none_when_not_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=0)
        assert _MOD._run_scope_escalation("clean body") is None

    def test_returns_none_for_empty_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _stub_subprocess(monkeypatch)
        assert _MOD._run_scope_escalation("") is None
        assert not calls


class TestRunTextlint:
    """`_run_textlint`のsubprocessモック検証と一時ファイル拡張子検証。"""

    def test_returns_none_when_no_violation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, textlint_returncode=0)
        assert _MOD._run_textlint("body") is None

    def test_returns_stderr_when_violation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, textlint_returncode=1, textlint_stdout="violation!")
        result = _MOD._run_textlint("body")
        assert result is not None
        assert "violation" in result

    def test_tmpfile_extension_is_md(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            return _completed(0)

        monkeypatch.setattr("subprocess.run", fake_run)
        _MOD._run_textlint("hello")
        assert calls
        # 最後の引数（一時ファイルパス）が`.md`で終わる。
        assert calls[0][-1].endswith(".md")


class TestCheckPlanFile:
    """`_check_plan_file`の統合動作。"""

    def test_no_violations_returns_empty(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=0)
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n[新設]:\n\n```text\nclean\n```\n",
        )
        assert _MOD._check_plan_file(plan) == []

    def test_scope_violation_is_reported(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _stub_subprocess(
            monkeypatch,
            scope_returncode=2,
            scope_stdout="process-omission\n",
            textlint_returncode=0,
        )
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n[新設]:\n\n```text\nbad phrase\n```\n",
        )
        violations = _MOD._check_plan_file(plan)
        assert len(violations) == 1
        assert "process-omission" in violations[0]

    def test_textlint_violation_is_reported(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=1, textlint_stdout="length error")
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n[新設]:\n\n```text\nlong body\n```\n",
        )
        violations = _MOD._check_plan_file(plan)
        assert len(violations) == 1
        assert "textlint" in violations[0]

    def test_missing_file_is_reported(self, tmp_path: pathlib.Path) -> None:
        missing = tmp_path / "missing.md"
        violations = _MOD._check_plan_file(missing)
        assert len(violations) == 1
        assert "読み込みに失敗" in violations[0]


class TestMainEntrypoint:
    """subprocess経由でエントリポイントを起動し、複数計画ファイル対応と終了コードを確認する。"""

    def test_exit_zero_on_no_violations(self, tmp_path: pathlib.Path) -> None:
        # 空の`## 変更内容`のみ。抽出0件のためsubprocess呼び出しも発生せずexit 0。
        plan = _write(tmp_path / "plan.md", "## 変更内容\n\n本文なし。\n")
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(plan)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_multiple_plan_files_are_all_checked(self, tmp_path: pathlib.Path) -> None:
        p1 = _write(tmp_path / "p1.md", "## 変更内容\n\n本文なし。\n")
        p2 = _write(tmp_path / "p2.md", "## 変更内容\n\n本文なし。\n")
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(p1), str(p2)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
