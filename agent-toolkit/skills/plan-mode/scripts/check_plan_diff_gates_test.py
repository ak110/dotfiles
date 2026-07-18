"""agent-toolkit/skills/plan-mode/scripts/check_plan_diff_gates.py のテスト。

計画ファイル`## 変更内容`配下の差分ブロック本文へ`_scope_escalation.py` CLIと
`uvx pyfltr run-for-agent --commands=textlint,colloquial-check --enable=colloquial-check`を
事前適用する検査スクリプトを`monkeypatch.setattr("subprocess.run", ...)`でsubprocessをmockして検証する。
`[追記]`ラベル直接検出・colloquial-check併走引数の検証、および
`_check_transcription_declaration_consistency`の「同構造」「同旨」「同期」宣言時整合性検査もあわせて扱う。
差分ブロック走査系・textlintバッチ実行系の関数群のテストは`_plan_diff_gates_scan_test.py`へ分離済み。
"""

# 対象スクリプトは単独実行スクリプトであり公開APIは`main()`のみだが、
# 個別関数の抽出仕様・副作用・境界を単体レベルで検証するためprotected-accessを許容する。
# pylint: disable=protected-access,unused-argument

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest
from _plan_diff_gates_test_helpers import _load_module, _stub_subprocess, _write

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _fork_runner  # noqa: E402  # pylint: disable=wrong-import-position

_SCRIPT = pathlib.Path(__file__).resolve().parent / "check_plan_diff_gates.py"
_MOD = _load_module(_SCRIPT)


class TestCheckPlanFile:
    """`_check_plan_file`の統合動作。"""

    def test_no_violations_returns_empty(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=0)
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nclean\n```\n",
        )
        assert _MOD._check_plan_file(plan, tmp_path) == []

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
        violations = _MOD._check_plan_file(plan, tmp_path)
        assert len(violations) == 1
        assert "process-omission" in violations[0]

    def test_scope_violation_suppressed_by_allow_marker(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """直前行に`<!-- scope-escalation-ok -->`があるフェンスは縮退フレーズ検査を抑止する。"""
        _stub_subprocess(
            monkeypatch,
            scope_returncode=2,
            scope_stdout="process-omission\n",
            textlint_returncode=0,
        )
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n<!-- scope-escalation-ok -->\n\n```text\n[新設]\nbad phrase\n```\n",
        )
        assert _MOD._check_plan_file(plan, tmp_path) == []

    def test_scope_violation_reported_without_allow_marker(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """マーカー無しの隣接ブロックは通常どおり縮退フレーズ検査の対象となる。"""
        _stub_subprocess(
            monkeypatch,
            scope_returncode=2,
            scope_stdout="process-omission\n",
            textlint_returncode=0,
        )
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n"
            "<!-- scope-escalation-ok -->\n\n```text\n[新設]\nbad phrase\n```\n\n"
            "```text\n[新設]\nanother bad phrase\n```\n",
        )
        violations = _MOD._check_plan_file(plan, tmp_path)
        assert len(violations) == 1

    def test_textlint_violation_is_reported_but_not_counted(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=1, textlint_stdout="length error")
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nlong body\n```\n",
        )
        violations = _MOD._check_plan_file(plan, tmp_path)
        assert violations == []
        assert "textlint" in capsys.readouterr().err

    def test_inner_label_coexistence_is_reported(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_check_plan_file`経由でもfence内側の`[現行]`/`[置換後]`併記を違反として検出する。"""
        _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=0)
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[現行]\nold\n[置換後]\nnew\n```\n",
        )
        violations = _MOD._check_plan_file(plan, tmp_path)
        assert len(violations) == 1
        assert "fence内側配置の`[現行]`/`[置換後]`併記を検出" in violations[0]

    def test_missing_file_is_reported(self, tmp_path: pathlib.Path) -> None:
        missing = tmp_path / "missing.md"
        violations = _MOD._check_plan_file(missing, tmp_path)
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
        violations = _MOD._check_plan_file(plan, tmp_path)
        assert violations == []
        assert not any("pyfltr" in part or part.endswith("pyfltr") for cmd in calls for part in cmd)

    @pytest.mark.parametrize("ext", [".py", ".yaml", ".json"])
    def test_non_prose_extension_still_runs_scope(
        self,
        ext: str,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _stub_subprocess(
            monkeypatch,
            scope_returncode=2,
            scope_stdout="pattern-conformance\n",
        )
        plan = _write(
            tmp_path / "plan.md",
            f"## 変更内容\n\n### `foo{ext}`\n\n```text\n[新設]\ncode snippet\n```\n",
        )
        violations = _MOD._check_plan_file(plan, tmp_path)
        assert len(violations) == 1
        assert any("_scope_escalation.py" in part for cmd in calls for part in cmd)

    def test_md_extension_runs_all_rules(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls = _stub_subprocess(monkeypatch, scope_returncode=0, textlint_returncode=1, textlint_stdout="length error")
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nlong body\n```\n",
        )
        violations = _MOD._check_plan_file(plan, tmp_path)
        assert violations == []
        assert "textlint" in capsys.readouterr().err
        assert any("pyfltr" in part or part.endswith("pyfltr") for cmd in calls for part in cmd)


class TestMainEntrypoint:
    """subprocess経由でエントリポイントを起動し、複数計画ファイル対応と終了コードを確認する。"""

    def test_exit_zero_on_no_violations(self, tmp_path: pathlib.Path) -> None:
        # 空の`## 変更内容`のみ。抽出0件のためsubprocess呼び出しも発生せずexit 0。
        plan = _write(tmp_path / "plan.md", "## 変更内容\n\n本文なし。\n")
        result = _fork_runner.run_script(_SCRIPT, argv=(str(plan),))
        assert result.returncode == 0, result.stderr

    def test_multiple_plan_files_are_all_checked(self, tmp_path: pathlib.Path) -> None:
        p1 = _write(tmp_path / "p1.md", "## 変更内容\n\n本文なし。\n")
        p2 = _write(tmp_path / "p2.md", "## 変更内容\n\n本文なし。\n")
        result = _fork_runner.run_script(_SCRIPT, argv=(str(p1), str(p2)))
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
        result = _fork_runner.run_script(_SCRIPT, argv=(str(plan),))
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
        result = _fork_runner.run_script(_SCRIPT, argv=(str(plan),))
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
        result = _fork_runner.run_script(_SCRIPT, argv=(str(plan),))
        assert result.returncode == 0
        assert "[warn]" not in result.stderr

    def test_passes_when_no_agent_toolkit_paths(self, tmp_path: pathlib.Path) -> None:
        plan = _write(
            tmp_path / "plan.md",
            _bump_plan(["pytools/example.py"], include_bump=False),
        )
        result = _fork_runner.run_script(_SCRIPT, argv=(str(plan),))
        assert result.returncode == 0
        assert "[warn]" not in result.stderr


class TestCheckRecurrencePreventionRecorded:
    """恒久化・リファクタリング内容の再発予防記述要件を検証する。"""

    def test_no_section_returns_none(self, tmp_path: pathlib.Path) -> None:
        text = "## 対応方針\n\n### 実装方針\n\n本文。\n"
        assert _MOD._check_recurrence_prevention_recorded(tmp_path / "plan.md", text) is None

    def test_marker_present_returns_none(self, tmp_path: pathlib.Path) -> None:
        text = "## 対応方針\n\n### 恒久化・リファクタリング内容\n\n再発予防として検査を追加する。\n"
        assert _MOD._check_recurrence_prevention_recorded(tmp_path / "plan.md", text) is None

    def test_marker_missing_returns_violation(self, tmp_path: pathlib.Path) -> None:
        text = "## 対応方針\n\n### 恒久化・リファクタリング内容\n\n検査を追加する。\n"
        result = _MOD._check_recurrence_prevention_recorded(tmp_path / "plan.md", text)
        assert result is not None
        assert "再発予防" in result


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

    def test_backticked_outer_label_before_fence(self, tmp_path: pathlib.Path) -> None:
        """バッククォート囲みラベル単独行がfence外側配置なら違反検出する。"""
        content = "## 変更内容\n\n`[現行]`\n\n```text\nfoo\n```\n"
        plan = tmp_path / "plan.md"
        plan.write_text(content, encoding="utf-8")
        violations = _MOD._check_outer_label_placement(plan, content)
        assert len(violations) == 1

    def test_backticked_labeled_annotation_before_fence(self, tmp_path: pathlib.Path) -> None:
        """バッククォート囲みと注記コロン形式がfence外側配置なら違反検出する。"""
        content = "## 変更内容\n\n`[現行]`（内訳バレット末尾）:\n\n```text\nfoo\n```\n"
        plan = tmp_path / "plan.md"
        plan.write_text(content, encoding="utf-8")
        violations = _MOD._check_outer_label_placement(plan, content)
        assert len(violations) == 1

    @pytest.mark.parametrize(
        "label",
        [
            "[追記]",
            "[新設]",
            "[置換後（全文）]",
            "[削除根拠]",
            "[追記×2]",
            "[追記×10]",
            "[追記（frontmatter）]",
            "[現行（frontmatter）]",
            "[置換後（frontmatter）]",
            "[削除根拠（frontmatter）]",
        ],
    )
    def test_extended_label_outer_placement_detected(self, tmp_path: pathlib.Path, label: str) -> None:
        """差分ラベル全種・派生形・frontmatterサブラベルのfence外側配置を検出する。"""
        text = f"## 変更内容\n\n### `foo.md`\n\n{label}\n```text\nbody\n```\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert len(violations) == 1
        assert "fence外側配置" in violations[0]

    def test_appended_zero_times_label_not_detected(self, tmp_path: pathlib.Path) -> None:
        """`[追記×0]`は`[1-9][0-9]*`パターンにより検出対象外。"""
        text = "## 変更内容\n\n### `foo.md`\n\n[追記×0]\n```text\nbody\n```\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert violations == []

    @pytest.mark.parametrize(
        "fullwidth_label",
        [
            "[追記］",
            "[新設］",
            "[置換後（全文）］",
            "[削除根拠］",
            "[追記×2］",
            "[追記×10］",
            "[追記（frontmatter）］",
            "[現行（frontmatter）］",
            "[置換後（frontmatter）］",
            "[削除根拠（frontmatter）］",
        ],
    )
    def test_extended_fullwidth_label_outside_fence_detected(self, tmp_path: pathlib.Path, fullwidth_label: str) -> None:
        """差分ラベル全種・frontmatterサブラベルの全角化ラベルをfence外側で検出する。"""
        text = f"## 変更内容\n\n### `foo.md`\n\n{fullwidth_label}\n```text\nbody\n```\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert len(violations) == 1
        assert "全角化ラベル" in violations[0]

    @pytest.mark.parametrize("label", ["[追記]", "[新設]", "[置換後（全文）]", "[削除根拠]", "[追記（frontmatter）]"])
    def test_extended_label_backticked_outer_placement_detected(self, tmp_path: pathlib.Path, label: str) -> None:
        """差分ラベル全種のバッククォート囲み形式がfence外側配置なら違反検出する。"""
        content = f"## 変更内容\n\n`{label}`\n\n```text\nfoo\n```\n"
        plan = tmp_path / "plan.md"
        plan.write_text(content, encoding="utf-8")
        violations = _MOD._check_outer_label_placement(plan, content)
        assert len(violations) == 1

    @pytest.mark.parametrize(
        "annotated_line",
        [
            "[追記]（説明）",
            "[追記](説明)",
            "[追記]:",
        ],
    )
    def test_extended_label_annotation_forms_outer_placement_detected(
        self, tmp_path: pathlib.Path, annotated_line: str
    ) -> None:
        """半角・全角の注記形式付き差分ラベルのfence外側配置を検出する。"""
        content = f"## 変更内容\n\n{annotated_line}\n\n```text\nfoo\n```\n"
        plan = tmp_path / "plan.md"
        plan.write_text(content, encoding="utf-8")
        violations = _MOD._check_outer_label_placement(plan, content)
        assert len(violations) == 1

    @pytest.mark.parametrize(
        "label",
        [
            "[追記]",
            "[新設]",
            "[置換後（全文）]",
            "[削除根拠]",
            "[追記×3]",
            "[追記（frontmatter）]",
            "[現行（frontmatter）]",
            "[置換後（frontmatter）]",
            "[削除根拠（frontmatter）]",
        ],
    )
    def test_extended_label_inside_fence_first_line_no_violation(self, tmp_path: pathlib.Path, label: str) -> None:
        """差分ラベル全種・派生形・frontmatterサブラベルがfence直後1行目に配置される場合は違反なし。"""
        text = f"## 変更内容\n\n### `foo.md`\n\n```text\n{label}\nbody\n```\n"
        violations = _MOD._check_outer_label_placement(tmp_path / "plan.md", text)
        assert violations == []


class TestCheckInnerLabelCoexistence:
    """`_check_inner_label_coexistence`の単体テスト。"""

    def test_coexistence_in_same_fence_detected(self, tmp_path: pathlib.Path) -> None:
        """同一fence内側に`[現行]`と`[置換後]`が併記される場合は違反として検出。"""
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[現行]\nold\n[置換後]\nnew\n```\n"
        violations = _MOD._check_inner_label_coexistence(tmp_path / "plan.md", text)
        assert len(violations) == 1
        assert "fence内側配置の`[現行]`/`[置換後]`併記を検出" in violations[0]

    def test_independent_fences_no_violation(self, tmp_path: pathlib.Path) -> None:
        """独立フェンスへ`[現行]`と`[置換後]`を分割配置した場合は違反なし。"""
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[現行]\nold\n```\n\n```text\n[置換後]\nnew\n```\n"
        violations = _MOD._check_inner_label_coexistence(tmp_path / "plan.md", text)
        assert violations == []

    def test_current_only_no_violation(self, tmp_path: pathlib.Path) -> None:
        """`[現行]`のみを含むfenceは違反なし。"""
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[現行]\nold\n```\n"
        violations = _MOD._check_inner_label_coexistence(tmp_path / "plan.md", text)
        assert violations == []

    def test_out_of_section_no_violation(self, tmp_path: pathlib.Path) -> None:
        """`## 変更内容`節外のfence内併記は違反対象外。"""
        text = "## 背景\n\n```text\n[現行]\nold\n[置換後]\nnew\n```\n\n## 変更内容\n\n本文なし。\n"
        violations = _MOD._check_inner_label_coexistence(tmp_path / "plan.md", text)
        assert violations == []

    def test_empty_input_no_violation(self, tmp_path: pathlib.Path) -> None:
        """空入力は違反なし。"""
        assert _MOD._check_inner_label_coexistence(tmp_path / "plan.md", "") == []

    def test_single_line_input_no_violation(self, tmp_path: pathlib.Path) -> None:
        """単一行入力は併記不可のため違反なし。"""
        text = "## 変更内容"
        assert _MOD._check_inner_label_coexistence(tmp_path / "plan.md", text) == []

    def test_empty_fence_no_violation(self, tmp_path: pathlib.Path) -> None:
        """空フェンス（本文0行）は違反なし。"""
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n```\n"
        violations = _MOD._check_inner_label_coexistence(tmp_path / "plan.md", text)
        assert violations == []

    def test_crlf_detects_same_as_lf(self, tmp_path: pathlib.Path) -> None:
        """CRLF改行でもLFと同一の検出結果となる。"""
        text = "## 変更内容\r\n\r\n### `foo.md`\r\n\r\n```text\r\n[現行]\r\nold\r\n[置換後]\r\nnew\r\n```\r\n"
        violations = _MOD._check_inner_label_coexistence(tmp_path / "plan.md", text)
        assert len(violations) == 1

    def test_unclosed_fence_no_violation(self, tmp_path: pathlib.Path) -> None:
        """未閉じフェンス（EOF終端も含む）は検査対象外として違反なし。"""
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[現行]\nold\n[置換後]\nnew\n"
        violations = _MOD._check_inner_label_coexistence(tmp_path / "plan.md", text)
        assert violations == []

    def test_no_changes_section_no_violation(self, tmp_path: pathlib.Path) -> None:
        """`## 変更内容`節が計画ファイルに存在しない場合は違反対象外。"""
        text = "## 背景\n\n```text\n[現行]\nold\n[置換後]\nnew\n```\n"
        violations = _MOD._check_inner_label_coexistence(tmp_path / "plan.md", text)
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

    def test_prose_block_appears_in_prose_paths(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """散文系拡張子（`.md`）ブロックはprose_pathsへ追加される。"""
        _stub_subprocess(monkeypatch, scope_returncode=0)
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nprose body\n```\n",
        )
        messages, (prose_paths, location_map) = _MOD._extract_diff_blocks(plan)
        assert messages == []
        assert len(prose_paths) == 1
        assert str(prose_paths[0]) in location_map
        assert "foo.md" in location_map[str(prose_paths[0])]
        for path in prose_paths:
            path.unlink(missing_ok=True)

    def test_code_block_produces_no_tmpfile(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非散文系拡張子（`.py`）ブロックは一時ファイルを生成しない。"""
        _stub_subprocess(monkeypatch, scope_returncode=0)
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.py`\n\n```text\n[新設]\ndef main(): pass\n```\n",
        )
        messages, (prose_paths, location_map) = _MOD._extract_diff_blocks(plan)
        assert messages == []
        assert prose_paths == []
        assert location_map == {}

    def test_inner_label_coexistence_is_reported(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_extract_diff_blocks`経由でもfence内側の`[現行]`/`[置換後]`併記を違反として検出する。"""
        _stub_subprocess(monkeypatch, scope_returncode=0)
        plan = _write(
            tmp_path / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n```text\n[現行]\nold\n[置換後]\nnew\n```\n",
        )
        messages, (prose_paths, _location_map) = _MOD._extract_diff_blocks(plan)
        assert len(messages) == 1
        assert "fence内側配置の`[現行]`/`[置換後]`併記を検出" in messages[0]
        for path in prose_paths:
            path.unlink(missing_ok=True)


class TestCheckExtractedPaths:
    """`_check_extracted_paths`のバッチ実行と位置情報復元を検証する。"""

    def test_empty_paths_returns_empty(self) -> None:
        """全リストが空なら空リストを返す。"""
        assert _MOD._check_extracted_paths(([], {})) == []

    def test_rewrites_tmp_path_to_location_marker(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """textlint出力中の一時ファイルパスがH3位置マーカーへ書き換えられる。"""
        prose_file = tmp_path / "block.md"
        prose_file.write_text("body", encoding="utf-8")
        location_marker = "plan.md: H3=`foo.md` L42"
        location_map = {str(prose_file): location_marker}

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if "textlint" in "".join(cmd):
                return subprocess.CompletedProcess(args=[], returncode=1, stdout=f"{prose_file}:5: violation", stderr="")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)
        messages = _MOD._check_extracted_paths(([prose_file], location_map))
        assert any(location_marker in m for m in messages)
        assert not any(str(prose_file) in m for m in messages)


class TestTranscriptionDeclarationConsistency:
    """`_check_transcription_declaration_consistency`の「同構造」「同旨」「同期」宣言時の整合性検査を検証する。"""

    def test_warns_when_declared_transcription_conflicts_with_target(self, tmp_path: pathlib.Path) -> None:
        """「同構造」宣言つき追記で対象ファイルに矛盾キーワードがある場合、warnが発火する。"""
        target = _write(tmp_path / "target.md", "## 判断基準\n\npushは行わない。\n")
        text = (
            "## 変更内容\n\n"
            f"### `{target}`\n\n"
            "既存の追記文言を同構造として転記する。\n\n"
            "```text\n[追記]\npushを実施する。\n```\n"
        )
        warnings = _MOD._check_transcription_declaration_consistency(pathlib.Path("plan.md"), text, tmp_path)
        assert len(warnings) == 1
        assert "push" in warnings[0]
        assert str(target) in warnings[0]

    def test_no_warning_when_no_conflict(self, tmp_path: pathlib.Path) -> None:
        """「同旨」宣言つき追記で対象ファイルに矛盾キーワードが無い場合、warnは発火しない。"""
        target = _write(tmp_path / "target.md", "## 判断基準\n\npushを実施する。\n")
        text = (
            f"## 変更内容\n\n### `{target}`\n\n既存の追記文言を同旨として転記する。\n\n```text\n[追記]\npushを実施する。\n```\n"
        )
        warnings = _MOD._check_transcription_declaration_consistency(pathlib.Path("plan.md"), text, tmp_path)
        assert warnings == []

    def test_responsibility_diff_table_suppresses_warning(self, tmp_path: pathlib.Path) -> None:
        """`### エージェント判断`配下に責務差分表の見出しが存在する場合、warnが抑制される。"""
        target = _write(tmp_path / "target.md", "## 判断基準\n\npushは行わない。\n")
        text = (
            "### エージェント判断\n\n"
            "#### 責務差分表\n\n"
            "対象ファイルごとの責務差分は次のとおり。\n\n"
            "## 変更内容\n\n"
            f"### `{target}`\n\n"
            "既存の追記文言を同構造として転記する。\n\n"
            "```text\n[追記]\npushを実施する。\n```\n"
        )
        warnings = _MOD._check_transcription_declaration_consistency(pathlib.Path("plan.md"), text, tmp_path)
        assert warnings == []

    def test_single_target_file_without_declaration_is_out_of_scope(self, tmp_path: pathlib.Path) -> None:
        """単一対象ファイルへの追記で宣言表現（「同構造」「同旨」「同期して」）が無い場合、対象外。"""
        target = _write(tmp_path / "target.md", "## 判断基準\n\npushは行わない。\n")
        text = f"## 変更内容\n\n### `{target}`\n\n追記内容:\n\n```text\n[追記]\npushを実施する。\n```\n"
        warnings = _MOD._check_transcription_declaration_consistency(pathlib.Path("plan.md"), text, tmp_path)
        assert warnings == []

    def test_no_warning_when_conflict_keyword_absent_from_added_body(self, tmp_path: pathlib.Path) -> None:
        """対象ファイルに`レビュー`の否定文脈があっても、追記文言案が同語を含まない場合はwarnしない。"""
        target = _write(tmp_path / "target.md", "## 判断基準\n\nレビューは対象外。\n")
        text = (
            f"## 変更内容\n\n### `{target}`\n\n"
            "既存の追記文言を同構造として転記する。\n\n"
            "```text\n[追記]\n設定値を更新する。\n```\n"
        )
        warnings = _MOD._check_transcription_declaration_consistency(pathlib.Path("plan.md"), text, tmp_path)
        assert warnings == []

    def test_warns_only_for_keyword_present_in_added_body(self, tmp_path: pathlib.Path) -> None:
        """追記文言案が`push`のみ言及する場合、対象ファイルの`レビュー`否定文脈はwarn対象に含めない。"""
        target = _write(
            tmp_path / "target.md",
            "## 判断基準\n\npushは行わない。\nレビューは対象外。\n",
        )
        text = (
            f"## 変更内容\n\n### `{target}`\n\n"
            "既存の追記文言を同構造として転記する。\n\n"
            "```text\n[追記]\npushを実施する。\n```\n"
        )
        warnings = _MOD._check_transcription_declaration_consistency(pathlib.Path("plan.md"), text, tmp_path)
        assert len(warnings) == 1
        assert "push" in warnings[0]
        assert "レビュー" not in warnings[0]

    def test_warns_for_backtickless_h3_heading(self, tmp_path: pathlib.Path) -> None:
        """バッククォート無しのH3見出し（SSOT規定の標準形式）でも対象ファイルを抽出しwarnが発火する。"""
        target = _write(tmp_path / "target.md", "## 判断基準\n\npushは行わない。\n")
        text = (
            f"## 変更内容\n\n### {target}\n\n既存の追記文言を同構造として転記する。\n\n```text\n[追記]\npushを実施する。\n```\n"
        )
        warnings = _MOD._check_transcription_declaration_consistency(pathlib.Path("plan.md"), text, tmp_path)
        assert len(warnings) == 1
        assert "push" in warnings[0]
        assert str(target) in warnings[0]

    def test_warns_for_backtickless_h3_heading_with_new_marker(self, tmp_path: pathlib.Path) -> None:
        """バッククォート無しH3見出しに`_NEW_H3_MARKER`注記が付く場合も注記を除去して対象ファイルを抽出する。"""
        target = _write(tmp_path / "target.md", "## 判断基準\n\npushは行わない。\n")
        text = (
            f"## 変更内容\n\n### {target}（新設）\n\n"
            "既存の追記文言を同構造として転記する。\n\n"
            "```text\n[追記]\npushを実施する。\n```\n"
        )
        warnings = _MOD._check_transcription_declaration_consistency(pathlib.Path("plan.md"), text, tmp_path)
        assert len(warnings) == 1
        assert "push" in warnings[0]
        assert str(target) in warnings[0]

    def test_no_warning_for_h3_heading_with_only_new_marker(self, tmp_path: pathlib.Path) -> None:
        """H3見出し本文が`_NEW_H3_MARKER`注記のみで対象ファイル名が空になる場合、warnは発火しない。"""
        text = (
            "## 変更内容\n\n### （新設）\n\n既存の追記文言を同構造として転記する。\n\n```text\n[追記]\npushを実施する。\n```\n"
        )
        warnings = _MOD._check_transcription_declaration_consistency(pathlib.Path("plan.md"), text, tmp_path)
        assert warnings == []


class TestCheckSkillAgentConfusion:
    """`_check_skill_agent_confusion`のSkill/Agent誤記検出を検証する。"""

    @staticmethod
    def _make_repo_root(tmp_path: pathlib.Path, agent_names: tuple[str, ...] = ("plan-impl-executor",)) -> pathlib.Path:
        """`agent-toolkit/agents/<name>.md`を配置したダミーリポジトリルートを構築する。"""
        agents_dir = tmp_path / "agent-toolkit" / "agents"
        agents_dir.mkdir(parents=True)
        for name in agent_names:
            _write(agents_dir / f"{name}.md", "# ダミー\n")
        return tmp_path

    def test_agent_identifier_passes(self, tmp_path: pathlib.Path) -> None:
        """実在するAgent識別子への言及は警告しない。"""
        repo_root = self._make_repo_root(tmp_path)
        text = (
            "## 変更内容\n\n### `foo.md`\n\n```text\n[追記]\nAgentツールで`agent-toolkit:plan-impl-executor`を起動する。\n```\n"
        )
        warnings = _MOD._check_skill_agent_confusion(pathlib.Path("plan.md"), text, repo_root)
        assert warnings == []

    def test_skill_identifier_warns(self, tmp_path: pathlib.Path) -> None:
        """Skill定義（Agent定義に実在しない識別子）への「Agentツール」表記は警告する。"""
        repo_root = self._make_repo_root(tmp_path)
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[追記]\nAgentツールで`agent-toolkit:careful-review`を起動する。\n```\n"
        warnings = _MOD._check_skill_agent_confusion(pathlib.Path("plan.md"), text, repo_root)
        assert len(warnings) == 1
        assert "agent-toolkit:careful-review" in warnings[0]

    def test_agent_tool_english_form_warns(self, tmp_path: pathlib.Path) -> None:
        """英語表記「Agent tool」でもトリガーとして検出する。"""
        repo_root = self._make_repo_root(tmp_path)
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[追記]\nAgent tool `agent-toolkit:process-feedbacks`を起動する。\n```\n"
        warnings = _MOD._check_skill_agent_confusion(pathlib.Path("plan.md"), text, repo_root)
        assert len(warnings) == 1
        assert "agent-toolkit:process-feedbacks" in warnings[0]

    def test_adjacent_line_neighborhood_warns(self, tmp_path: pathlib.Path) -> None:
        """トリガーと識別子が直前・直後1行に別々に出現しても近傍として検出する。"""
        repo_root = self._make_repo_root(tmp_path)
        text = (
            "## 変更内容\n\n### `foo.md`\n\n"
            "```text\n[追記]\nAgentツールで起動する。\n`agent-toolkit:careful-review`を対象とする。\n```\n"
        )
        warnings = _MOD._check_skill_agent_confusion(pathlib.Path("plan.md"), text, repo_root)
        assert len(warnings) == 1
        assert "agent-toolkit:careful-review" in warnings[0]

    def test_out_of_neighborhood_no_warn(self, tmp_path: pathlib.Path) -> None:
        """トリガーと識別子が3行以上離れて出現する場合は近傍外として警告しない。"""
        repo_root = self._make_repo_root(tmp_path)
        text = (
            "## 変更内容\n\n### `foo.md`\n\n"
            "```text\n[追記]\nAgentツールで起動する。\n中間行その1。\n中間行その2。\n"
            "`agent-toolkit:careful-review`を対象とする。\n```\n"
        )
        warnings = _MOD._check_skill_agent_confusion(pathlib.Path("plan.md"), text, repo_root)
        assert warnings == []

    def test_current_label_excluded(self, tmp_path: pathlib.Path) -> None:
        """`[現行]`ラベル配下は`_iter_diff_blocks`が既に対象外とするため警告しない。"""
        repo_root = self._make_repo_root(tmp_path)
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[現行]\nAgentツールで`agent-toolkit:careful-review`を起動する。\n```\n"
        warnings = _MOD._check_skill_agent_confusion(pathlib.Path("plan.md"), text, repo_root)
        assert warnings == []

    def test_outside_change_section_no_warn(self, tmp_path: pathlib.Path) -> None:
        """`## 変更内容`外の記述は`_iter_diff_blocks`が走査しないため警告しない。"""
        repo_root = self._make_repo_root(tmp_path)
        text = (
            "## 背景\n\nAgentツールで`agent-toolkit:careful-review`を起動する。\n\n"
            "## 変更内容\n\n### `foo.md`\n\n```text\n[追記]\n設定値を更新する。\n```\n"
        )
        warnings = _MOD._check_skill_agent_confusion(pathlib.Path("plan.md"), text, repo_root)
        assert warnings == []

    def test_extract_diff_blocks_real_agent_identifier_no_warn(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """統合ランナー経路（`_extract_diff_blocks`のcwd起点`repo_root`解決）でも実在Agent識別子は警告しない。"""
        repo_root = self._make_repo_root(tmp_path)
        (repo_root / ".git").mkdir()
        _stub_subprocess(monkeypatch, scope_returncode=0)
        monkeypatch.chdir(repo_root)
        plan = _write(
            repo_root / "plan.md",
            "## 変更内容\n\n### `foo.md`\n\n"
            "```text\n[追記]\nAgentツールで`agent-toolkit:plan-impl-executor`を起動する。\n```\n",
        )
        messages, (prose_paths, _location_map) = _MOD._extract_diff_blocks(plan)
        assert messages == []
        assert "Skill/Agent誤記候補" not in capsys.readouterr().err
        for path in prose_paths:
            path.unlink(missing_ok=True)
