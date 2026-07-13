"""agent-toolkit/skills/plan-mode/scripts/check_plan_diff_gates.py のテスト。

計画ファイル`## 変更内容`配下の差分ブロック本文へ`_scope_escalation.py` CLIと
`uvx pyfltr run-for-agent --commands=textlint,colloquial-check --enable=colloquial-check`を
事前適用する検査スクリプトを`monkeypatch.setattr("subprocess.run", ...)`でsubprocessをmockして検証する。
`[追記]`ラベル直接検出・colloquial-check併走引数の検証もあわせて扱う。
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
    line_width_returncode: int = 0,
    line_width_stderr: str = "",
) -> list[list[str]]:
    """subprocess.runを差し替えてscope_escalation・textlint・check_line_widthの応答を注入する。

    `check_line_width.py`は違反行を`sys.stderr`へ出力するため、
    `line_width_stderr`をstderr側の応答として注入する。
    """
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if any("_scope_escalation.py" in part for part in cmd):
            return _completed(scope_returncode, stdout=scope_stdout)
        if any("check_line_width.py" in part for part in cmd):
            return _completed(line_width_returncode, stderr=line_width_stderr)
        if any(part == "pyfltr" or part.endswith("pyfltr") for part in cmd):
            return _completed(textlint_returncode, stdout=textlint_stdout)
        return _completed(0)

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls


class TestExtractDiffBlocks:
    """`_iter_diff_blocks`の抽出仕様を網羅する。"""

    def test_new_label_block_is_extracted(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nnew content line1\nnew content line2\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        label, _line, body, _ext = blocks[0]
        assert label == "`foo.md`"
        # ラベル行はfence内側1行目に配置され、本文集計・textlint検査から除外される。
        assert body == "new content line1\nnew content line2"

    def test_replacement_label_block_is_extracted(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[置換後]\nreplaced body\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "replaced body"

    def test_replacement_full_label_block_is_extracted(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[置換後（全文）]\nwhole file content\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "whole file content"

    def test_current_label_block_is_excluded(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[現行]\nold body\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert not blocks

    def test_deletion_rationale_label_block_is_excluded(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[削除根拠]\n削除の理由\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert not blocks

    def test_variable_length_fence_four_backticks_is_extracted(self) -> None:
        """外側4バッククォートのfenceも検査対象として認識される。"""
        text = "## 変更内容\n\n### `foo.md`\n\n````text\n[置換後]\ninner ``` allowed\n````\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "inner ``` allowed"

    def test_new_h3_marker_extracts_body(self) -> None:
        text = "## 変更内容\n\n### `new_file.py`（新設）\n\n新設スクリプト。\n\n```text\nsome body under new h3\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][0].startswith("`new_file.py`")

    def test_trigger_line_extracts_following_block(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n追記内容:\n\n```text\nadded line\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "added line"

    def test_reduction_heading_extracts_body(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n#### 縮減対象\n\n```text\nold verbose text\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1

    def test_non_text_fence_is_ignored(self) -> None:
        text = "## 変更内容\n\n### `foo.py`\n\n```python\ndef f(): pass\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert not blocks

    def test_no_change_section_returns_empty(self) -> None:
        text = "## 背景\n\n本文のみ。\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert not blocks

    def test_block_start_line_is_computed(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nline\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert blocks[0][1] > 0

    def test_addition_trigger_tokens_include_compression_after(self) -> None:
        assert "圧縮後:" in _MOD._ADDITION_TRIGGER_TOKENS

    def test_extract_diff_blocks_compression_after_trigger(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n圧縮後:\n\n```text\ncompressed body\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "compressed body"

    def test_addition_label_block_is_extracted(self) -> None:
        """`[追記]`ラベル単独ブロック（隣接文言に「追記」「追加」の語なし）も検査対象へ入る。"""
        text = "## 変更内容\n\n### `foo.md`\n\n特に前置きなし。\n\n```text\n[追記]\naddition body\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        # ラベル行はfence内側1行目に配置され本文抽出時に除外される。
        assert blocks[0][2] == "addition body"

    def test_classify_block_returns_addition_for_addition_label(self) -> None:
        """`_classify_block`は`[追記]`ラベル単独行を`"addition"`種別として返す。"""
        assert _MOD._classify_block(["[追記]", "body"], False, False, False) == "addition"


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

    def test_subprocess_args_include_colloquial_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_run_textlint`はcolloquial-check併走のため`--commands`と`--enable`引数を含める。"""
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            return _completed(0)

        monkeypatch.setattr("subprocess.run", fake_run)
        _MOD._run_textlint("hello")
        assert calls
        assert "--commands=textlint,colloquial-check" in calls[0]
        assert "--enable=colloquial-check" in calls[0]

    def test_batch_subprocess_args_include_colloquial_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """`_run_textlint_batch`もcolloquial-check併走引数を含める。"""
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            return _completed(0)

        monkeypatch.setattr("subprocess.run", fake_run)
        _MOD._run_textlint_batch([tmp_path / "a.md"])
        assert calls
        assert "--commands=textlint,colloquial-check" in calls[0]
        assert "--enable=colloquial-check" in calls[0]


class TestRunLineWidth:
    """`_run_line_width`のsubprocessモック検証。"""

    def test_run_line_width_success_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, line_width_returncode=0)
        assert _MOD._run_line_width("body") is None

    def test_run_line_width_violation_returns_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, line_width_returncode=1, line_width_stderr="width violation!")
        result = _MOD._run_line_width("body")
        assert result is not None
        assert "width violation" in result


class TestCheckPlanFile:
    """`_check_plan_file`の統合動作。"""

    def test_no_violations_returns_empty(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=0)
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nclean\n```\n",
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
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nbad phrase\n```\n",
        )
        violations = _MOD._check_plan_file(plan)
        assert len(violations) == 1
        assert "process-omission" in violations[0]

    def test_textlint_violation_is_reported(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=1, textlint_stdout="length error")
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nlong body\n```\n",
        )
        violations = _MOD._check_plan_file(plan)
        assert len(violations) == 1
        assert "textlint" in violations[0]

    def test_missing_file_is_reported(self, tmp_path: pathlib.Path) -> None:
        missing = tmp_path / "missing.md"
        violations = _MOD._check_plan_file(missing)
        assert len(violations) == 1
        assert "読み込みに失敗" in violations[0]

    @pytest.mark.parametrize("ext", [".py", ".yaml", ".json"])
    def test_non_prose_extension_skips_textlint(
        self,
        ext: str,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=1, textlint_stdout="length error")
        plan = _write(
            tmp_path / "plan.md",
            f"## 変更内容\n\n### `foo{ext}`\n\n```text\n[新設]\ncode snippet\n```\n",
        )
        violations = _MOD._check_plan_file(plan)
        assert violations == []
        assert not any("pyfltr" in part or part.endswith("pyfltr") for cmd in calls for part in cmd)

    @pytest.mark.parametrize("ext", [".py", ".yaml", ".json"])
    def test_non_prose_extension_still_runs_scope_and_line_width(
        self,
        ext: str,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _stub_subprocess(
            monkeypatch,
            scope_returncode=2,
            scope_stdout="pattern-conformance\n",
            line_width_returncode=1,
            line_width_stderr="line too long",
        )
        plan = _write(
            tmp_path / "plan.md",
            f"## 変更内容\n\n### `foo{ext}`\n\n```text\n[新設]\ncode snippet\n```\n",
        )
        violations = _MOD._check_plan_file(plan)
        assert len(violations) == 2
        assert any("_scope_escalation.py" in part for cmd in calls for part in cmd)
        assert any("check_line_width.py" in part for cmd in calls for part in cmd)

    def test_md_extension_runs_all_rules(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=1, textlint_stdout="length error")
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nlong body\n```\n",
        )
        violations = _MOD._check_plan_file(plan)
        assert len(violations) == 1
        assert "textlint" in violations[0]
        assert any("pyfltr" in part or part.endswith("pyfltr") for cmd in calls for part in cmd)

    def test_check_plan_file_reports_line_width_violation(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_subprocess(
            monkeypatch,
            scope_returncode=0,
            textlint_returncode=0,
            line_width_returncode=1,
            line_width_stderr="line too long",
        )
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nlong body\n```\n",
        )
        violations = _MOD._check_plan_file(plan)
        assert len(violations) == 1
        assert "line-width" in violations[0]


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


def _bump_plan(target_paths: list[str], include_bump: bool) -> str:
    target_lines = "\n".join(f"- [ ] `{p}`" for p in target_paths)
    exec_lines = "- `scripts/agent_toolkit_bump.py patch`" if include_bump else "- 実装する"
    return f"## 変更内容\n\n### 対象ファイル一覧\n\n{target_lines}\n\n## 実行方法\n\n{exec_lines}\n"


class TestCheckBumpStep:
    """agent-toolkit配下対象計画のversion bumpステップ検査。"""

    def test_warns_when_missing(self, tmp_path: pathlib.Path) -> None:
        """agent-toolkit配下パス+bump未記載でwarnメッセージがstderrに出る（exit 0）。"""
        plan = _write(
            tmp_path / "plan.md",
            _bump_plan(["agent-toolkit/scripts/pretooluse.py"], include_bump=False),
        )
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(plan)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "agent_toolkit_bump.py" in result.stderr
        assert "[warn]" in result.stderr

    def test_passes_when_bump_step_present(self, tmp_path: pathlib.Path) -> None:
        plan = _write(
            tmp_path / "plan.md",
            _bump_plan(
                [
                    "agent-toolkit/scripts/pretooluse.py",
                    "agent-toolkit/.claude-plugin/plugin.json",
                    ".claude-plugin/marketplace.json",
                ],
                include_bump=True,
            ),
        )
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(plan)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "[warn]" not in result.stderr

    def test_passes_when_test_only_paths(self, tmp_path: pathlib.Path) -> None:
        """`*_test.py`のみは検査対象外。"""
        plan = _write(
            tmp_path / "plan.md",
            _bump_plan(
                ["agent-toolkit/scripts/pretooluse_test.py", "agent-toolkit/scripts/posttooluse_test.py"],
                include_bump=False,
            ),
        )
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(plan)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "[warn]" not in result.stderr

    def test_passes_when_no_agent_toolkit_paths(self, tmp_path: pathlib.Path) -> None:
        plan = _write(
            tmp_path / "plan.md",
            _bump_plan(["pytools/example.py"], include_bump=False),
        )
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(plan)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "[warn]" not in result.stderr


def _norm_scan_plan(
    target_paths: list[str],
    include_new_heading: bool,
    include_survey_heading: bool,
) -> str:
    """fb-3テスト用計画文面ビルダー。"""
    target_lines = "\n".join(f"- [ ] `{p}`" for p in target_paths)
    if include_new_heading:
        diff_block = "### `agent-toolkit/rules/01-agent.md`\n\n```text\n[置換後]\n## 新規節\n\n本文。\n```\n"
    else:
        diff_block = "### `agent-toolkit/rules/01-agent.md`\n\n```text\n[置換後]\n本文のみ。\n```\n"
    survey = "## 調査結果\n\n### 遡及スキャン結果\n\n本文。\n" if include_survey_heading else "## 調査結果\n\n本文。\n"
    return f"## 変更内容\n\n### 対象ファイル一覧\n\n{target_lines}\n\n{diff_block}\n{survey}"


class TestCheckRetroactiveScanWhenNewNormSection:
    """`_check_retroactive_scan_when_new_norm_section`のwarn分類検査動作を検証する。"""

    def test_warns_when_all_three_conditions_met(self, tmp_path: pathlib.Path) -> None:
        """規範対象+新規H2+小見出し不在の3条件成立でwarn警告を返す。"""
        text = _norm_scan_plan(
            ["agent-toolkit/rules/01-agent.md"],
            include_new_heading=True,
            include_survey_heading=False,
        )
        warning = _MOD._check_retroactive_scan_when_new_norm_section(tmp_path / "plan.md", text)
        assert warning is not None
        assert "[warn]" in warning
        assert "遡及スキャン結果" in warning

    def test_no_warning_when_survey_heading_exists(self, tmp_path: pathlib.Path) -> None:
        """遡及スキャン結果小見出しが存在する場合は無警告。"""
        text = _norm_scan_plan(
            ["agent-toolkit/rules/01-agent.md"],
            include_new_heading=True,
            include_survey_heading=True,
        )
        assert _MOD._check_retroactive_scan_when_new_norm_section(tmp_path / "plan.md", text) is None

    def test_no_warning_when_no_norm_target(self, tmp_path: pathlib.Path) -> None:
        """規範対象ファイルを含まない計画は無警告。"""
        text = _norm_scan_plan(
            ["pytools/example.py"],
            include_new_heading=True,
            include_survey_heading=False,
        )
        assert _MOD._check_retroactive_scan_when_new_norm_section(tmp_path / "plan.md", text) is None

    def test_no_warning_when_no_new_heading(self, tmp_path: pathlib.Path) -> None:
        """新規H2見出しを含まない計画は無警告。"""
        text = _norm_scan_plan(
            ["agent-toolkit/rules/01-agent.md"],
            include_new_heading=False,
            include_survey_heading=False,
        )
        assert _MOD._check_retroactive_scan_when_new_norm_section(tmp_path / "plan.md", text) is None


class TestCheckOuterLabelPlacement:
    """`_check_outer_label_placement`の単体テスト。"""

    def test_outer_label_immediately_before_fence(self, tmp_path: pathlib.Path) -> None:
        """ラベル単独行の直後にtextフェンス開始行がある場合は違反として検出。"""
        text = "## 変更内容\n\n### `foo.md`\n\n[置換後]\n```text\nbody\n```\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert len(violations) == 1
        assert "fence外側配置" in violations[0]

    def test_outer_label_with_blank_line_before_fence(self, tmp_path: pathlib.Path) -> None:
        """ラベル単独行の後に空行を1行以上おいたうえでtextフェンス開始行がある場合も違反として検出。"""
        text = "## 変更内容\n\n### `foo.md`\n\n[置換後]\n\n```text\nbody\n```\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert len(violations) == 1
        assert "fence外側配置" in violations[0]

    def test_fullwidth_label_inside_fence_no_violation(self, tmp_path: pathlib.Path) -> None:
        """fence内側配置の全角化ラベルは検査対象外（既存の内側ラベル配置規定で扱う）。"""
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[置換後］\nbody\n```\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert violations == []

    def test_fullwidth_label_outside_fence(self, tmp_path: pathlib.Path) -> None:
        """fence外側配置の全角化ラベルは違反として検出。"""
        text = "## 変更内容\n\n### `foo.md`\n\n[置換後］\n```text\nbody\n```\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert len(violations) == 1
        assert "全角化ラベル" in violations[0]

    def test_regular_inside_placement_no_violation(self, tmp_path: pathlib.Path) -> None:
        """fence直後1行目の半角ラベルは違反として検出しない。"""
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[置換後]\nbody\n```\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert violations == []

    def test_out_of_scope_ignored(self, tmp_path: pathlib.Path) -> None:
        """`## 変更内容`セクション外のラベル文言・全角化文字は違反として検出しない。"""
        text = "## 背景\n\n[置換後]\n```text\nbody\n```\n\n[置換後］\n\n## 変更内容\n\n本文なし。\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert violations == []


class TestCheckManifestFilesWhenBumpStep:
    """`_check_manifest_files_when_bump_step`の単体テスト。"""

    def test_no_bump_step(self, tmp_path: pathlib.Path) -> None:
        """`## 実行方法`本文にbump step出現なしの場合はNoneを返す。"""
        text = _bump_plan(["pytools/example.py"], include_bump=False)
        assert _MOD._check_manifest_files_when_bump_step(tmp_path / "plan.md", text) is None

    def test_bump_step_with_both_manifests(self, tmp_path: pathlib.Path) -> None:
        """bump step記載かつmanifest両方あり: Noneを返す。"""
        text = _bump_plan(
            [
                "agent-toolkit/scripts/pretooluse.py",
                "agent-toolkit/.claude-plugin/plugin.json",
                ".claude-plugin/marketplace.json",
            ],
            include_bump=True,
        )
        assert _MOD._check_manifest_files_when_bump_step(tmp_path / "plan.md", text) is None

    def test_bump_step_missing_plugin_json(self, tmp_path: pathlib.Path) -> None:
        """bump step記載かつplugin.json欠落: warn文言を返す。"""
        text = _bump_plan(
            ["agent-toolkit/scripts/pretooluse.py", ".claude-plugin/marketplace.json"],
            include_bump=True,
        )
        result = _MOD._check_manifest_files_when_bump_step(tmp_path / "plan.md", text)
        assert result is not None
        assert "[warn]" in result
        assert "manifest" in result

    def test_bump_step_missing_marketplace_json(self, tmp_path: pathlib.Path) -> None:
        """bump step記載かつmarketplace.json欠落: warn文言を返す。"""
        text = _bump_plan(
            ["agent-toolkit/scripts/pretooluse.py", "agent-toolkit/.claude-plugin/plugin.json"],
            include_bump=True,
        )
        result = _MOD._check_manifest_files_when_bump_step(tmp_path / "plan.md", text)
        assert result is not None
        assert "[warn]" in result
        assert "manifest" in result


class TestCheckTargetFilePathsRelative:
    """`_check_target_file_paths_relative`のwarn分類検査動作を検証する。"""

    def test_returns_none_for_relative_paths(self, tmp_path: pathlib.Path) -> None:
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] agent-toolkit/scripts/atk.py\n"
        plan_path = tmp_path / "plan.md"
        assert _MOD._check_target_file_paths_relative(plan_path, content) is None

    def test_warns_on_absolute_path(self, tmp_path: pathlib.Path) -> None:
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] /home/user/foo.py\n"
        plan_path = tmp_path / "plan.md"
        msg = _MOD._check_target_file_paths_relative(plan_path, content)
        assert msg is not None
        assert "[warn]" in msg
        assert "/home/user/foo.py" in msg

    def test_warns_on_parent_reference(self, tmp_path: pathlib.Path) -> None:
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] ../outside/bar.py\n"
        plan_path = tmp_path / "plan.md"
        msg = _MOD._check_target_file_paths_relative(plan_path, content)
        assert msg is not None
        assert "../outside/bar.py" in msg


class TestExtractDiffBlocksPublic:
    """統合ランナー向け公開関数`_extract_diff_blocks(plan_path)`の挙動を検証する。"""

    def test_prose_block_appears_in_both_lists(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """散文系拡張子（`.md`）ブロックはprose_paths・line_width_paths双方へ追加される。"""
        _stub_subprocess(monkeypatch, scope_returncode=0)
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nprose body\n```\n",
        )
        messages, (prose_paths, line_width_paths, location_map) = _MOD._extract_diff_blocks(plan)
        assert messages == []
        assert len(prose_paths) == 1
        assert len(line_width_paths) == 1
        assert prose_paths[0] == line_width_paths[0]
        assert str(prose_paths[0]) in location_map
        assert "foo.md" in location_map[str(prose_paths[0])]
        for path in {*prose_paths, *line_width_paths}:
            path.unlink(missing_ok=True)

    def test_code_block_appears_only_in_line_width_list(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非散文系拡張子（`.py`）ブロックはline_width_pathsのみへ追加され、textlint適用対象から除外される。"""
        _stub_subprocess(monkeypatch, scope_returncode=0)
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.py`\n\n```text\n[新設]\ndef main(): pass\n```\n",
        )
        messages, (prose_paths, line_width_paths, location_map) = _MOD._extract_diff_blocks(plan)
        assert messages == []
        assert prose_paths == []
        assert len(line_width_paths) == 1
        assert str(line_width_paths[0]) in location_map
        for path in line_width_paths:
            path.unlink(missing_ok=True)


class TestCheckExtractedPaths:
    """`_check_extracted_paths`のバッチ実行と位置情報復元を検証する。"""

    def test_empty_paths_returns_empty(self) -> None:
        """全リストが空なら空リストを返す。"""
        assert _MOD._check_extracted_paths(([], [], {})) == []

    def test_rewrites_tmp_path_to_location_marker(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """textlint出力中の一時ファイルパスがH3位置マーカーへ書き換えられる。"""
        prose_file = tmp_path / "block.md"
        prose_file.write_text("body", encoding="utf-8")
        location_marker = "plan.md: H3=`foo.md` L42"
        location_map = {str(prose_file): location_marker}

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if "textlint" in "".join(cmd):
                return _completed(1, stdout=f"{prose_file}:5: violation")
            return _completed(0)

        monkeypatch.setattr("subprocess.run", fake_run)
        messages = _MOD._check_extracted_paths(([prose_file], [prose_file], location_map))
        assert any(location_marker in m for m in messages)
        assert not any(str(prose_file) in m for m in messages)


class TestRewriteLocations:
    """`_rewrite_locations`の置換ロジックを検証する。"""

    def test_replaces_all_registered_paths(self) -> None:
        """位置マップに登録された全パスがマーカーへ置換される。"""
        output = "/tmp/a.md:3: A\n/tmp/b.md:5: B\n"
        location_map = {"/tmp/a.md": "plan.md: H3=`x` L1", "/tmp/b.md": "plan.md: H3=`y` L2"}
        result = _MOD._rewrite_locations(output, location_map)
        assert "plan.md: H3=`x` L1:3: A" in result
        assert "plan.md: H3=`y` L2:5: B" in result

    def test_leaves_unmatched_content_intact(self) -> None:
        """位置マップに存在しないパスはそのまま残る。"""
        output = "/tmp/other.md:1: something"
        assert _MOD._rewrite_locations(output, {"/tmp/known.md": "marker"}) == output
