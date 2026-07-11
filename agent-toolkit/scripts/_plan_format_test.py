"""_plan_format モジュールの単体テスト。

H2見出し抽出・H2節順検査・SSOT整合性を検査する。
フェンス内H2除外は本モジュールの`extract_h2_sections`が担う。
"""

import pathlib
import re

import _plan_format

_PLAN_FILE_REF = pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "references" / "plan-file-guidelines.md"

_VALID_CONTENT = (
    "# タイトル\n\n"
    "## 変更履歴\n\nx\n\n"
    "## 背景\n\nx\n\n"
    "## 対応方針\n\nx\n\n"
    "## 調査結果\n\nx\n\n"
    "## 変更内容\n\nx\n\n"
    "## 実行方法\n\nx\n\n"
    "## 進捗ログ\n\nx\n\n"
    "## 計画ファイル（本ファイル）のパス\n\nx\n"
)


class TestExtractH2Sections:
    """extract_h2_sections の基本動作を検査する。"""

    def test_returns_all_h2_titles(self):
        content = "# H1\n\n## AAA\n\n## BBB\n"
        assert _plan_format.extract_h2_sections(content) == ["AAA", "BBB"]

    def test_empty_content_returns_empty(self):
        assert not _plan_format.extract_h2_sections("")

    def test_no_h2_returns_empty(self):
        assert not _plan_format.extract_h2_sections("# タイトルのみ\n\nテキスト\n")

    def test_trailing_whitespace_stripped(self):
        content = "## 背景  \n\nテキスト\n"
        assert _plan_format.extract_h2_sections(content) == ["背景"]

    def test_h2_inside_backtick_fence_is_excluded(self):
        content = "```\n## フェンス内\n```\n## 実在\n"
        assert _plan_format.extract_h2_sections(content) == ["実在"]

    def test_h2_inside_tilde_fence_is_excluded(self):
        content = "~~~\n## フェンス内\n~~~\n## 実在\n"
        assert _plan_format.extract_h2_sections(content) == ["実在"]

    def test_h2_inside_fence_with_info_string_is_excluded(self):
        """info string付きフェンス（```python等）の中身もフェンス内として除外する。"""
        content = "```python\n## フェンス内\n```\n## 実在\n"
        assert _plan_format.extract_h2_sections(content) == ["実在"]

    def test_inner_info_string_fence_does_not_close_outer(self):
        """フェンス内に出現する info string 付きフェンスを閉じ判定にしない。"""
        content = "```\n```python\n## フェンス内\n```\n## 外側\n"
        # 1行目 ``` で開き、2行目 ```python は閉じ判定にならず、4行目 ``` で閉じる
        # 5行目 ## 外側 が抽出される
        assert _plan_format.extract_h2_sections(content) == ["外側"]


class TestCheckH2Order:
    """check_h2_order の各違反パターンを検査する。"""

    def test_valid_plan_returns_empty(self):
        assert not _plan_format.check_h2_order(_VALID_CONTENT)

    def test_missing_required_section(self):
        content = "## 変更履歴\n\n## 背景\n\n## 対応方針\n\n"
        violations = _plan_format.check_h2_order(content)
        assert any("missing required H2 sections" in v for v in violations)

    def test_unexpected_section(self):
        content = _VALID_CONTENT + "\n## 予期せぬセクション\n\nx\n"
        violations = _plan_format.check_h2_order(content)
        assert any("unexpected H2 sections" in v for v in violations)

    def test_out_of_order(self):
        # 背景と対応方針を入れ替えて順序違反にする
        content = (
            "## 変更履歴\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            "## 変更内容\n\nx\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        violations = _plan_format.check_h2_order(content)
        assert any("out of order" in v for v in violations)

    def test_empty_content_reports_all_missing(self):
        violations = _plan_format.check_h2_order("")
        assert any("missing required H2 sections" in v for v in violations)

    def test_h2_inside_fence_not_counted_as_unexpected(self):
        content = _VALID_CONTENT + "\n```\n## フェンス内\n```\n"
        assert not _plan_format.check_h2_order(content)


class TestIterMarkdownBodyLines:
    """iter_markdown_body_lines の除外領域とフェンス判定を検査する。"""

    def test_skips_frontmatter(self) -> None:
        content = "---\nkey: value\n---\n\nbody line\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "key: value" not in rendered
        assert "body line" in rendered

    def test_skips_code_fence_content(self) -> None:
        content = "before\n```text\ninside\n```\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "before" in rendered
        assert "after" in rendered
        assert "inside" not in rendered

    def test_skips_multiline_html_comment(self) -> None:
        content = "before\n<!--\nhidden\n-->\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "before" in rendered
        assert "after" in rendered
        assert "hidden" not in rendered

    def test_keeps_single_line_html_comment_line(self) -> None:
        content = "before\n<!-- visible -->\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "<!-- visible -->" in rendered

    def test_long_backtick_fence_close(self) -> None:
        """4文字以上のバックティックフェンスでも閉じ判定が機能する。"""
        content = "````text\ninner\n````\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "after" in rendered
        assert "inner" not in rendered

    def test_tilde_fence_close(self) -> None:
        content = "~~~text\ninner\n~~~\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "after" in rendered
        assert "inner" not in rendered

    def test_lineno_starts_at_one(self) -> None:
        content = "first\nsecond\nthird\n"
        pairs = list(_plan_format.iter_markdown_body_lines(content))
        assert pairs == [(1, "first"), (2, "second"), (3, "third")]


class TestExtractTargetFilesFromChanges:
    """extract_target_files_from_changes の基本動作を検査する。"""

    def test_strips_trailing_line_count_metadata(self) -> None:
        """`（現行N行, 見込みM行）`等の付随メタ情報がパスから除去される。

        `plan-file-guidelines.md`が規定する標準形式
        `` `path`（現行N行, 見込みM行） ``を検査対象とする。
        """
        content = (
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `app/src/foo.svelte`（現行10行, 見込み20行）\n"
            "- [x] `app/src/bar.ts`（新設, 見込み5行）\n"
        )
        assert _plan_format.extract_target_files_from_changes(content) == [
            "app/src/foo.svelte",
            "app/src/bar.ts",
        ]

    def test_plain_backtick_path_without_metadata(self) -> None:
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `app/src/foo.svelte`\n"
        assert _plan_format.extract_target_files_from_changes(content) == ["app/src/foo.svelte"]

    def test_path_without_backtick(self) -> None:
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] app/src/foo.svelte\n"
        assert _plan_format.extract_target_files_from_changes(content) == ["app/src/foo.svelte"]

    def test_ignores_items_outside_target_file_list_h3(self) -> None:
        content = "## 変更内容\n\n### 別のH3\n\n- [ ] `app/src/foo.svelte`\n"
        assert not _plan_format.extract_target_files_from_changes(content)


class TestHasManifestFilesWhenBumpStepPresent:
    """has_manifest_files_when_bump_step_present の基本動作を検査する。"""

    def test_no_bump_step(self) -> None:
        content = "## 実行方法\n\nx\n"
        assert _plan_format.has_manifest_files_when_bump_step_present(content)

    def test_bump_step_with_both_manifests(self) -> None:
        content = (
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `agent-toolkit/.claude-plugin/plugin.json`\n"
            "- [ ] `.claude-plugin/marketplace.json`\n\n"
            "## 実行方法\n\n"
            "agent_toolkit_bump.py を実行する\n"
        )
        assert _plan_format.has_manifest_files_when_bump_step_present(content)

    def test_bump_step_missing_plugin_json(self) -> None:
        content = (
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `.claude-plugin/marketplace.json`\n\n"
            "## 実行方法\n\n"
            "agent_toolkit_bump.py を実行する\n"
        )
        assert not _plan_format.has_manifest_files_when_bump_step_present(content)

    def test_bump_step_missing_marketplace_json(self) -> None:
        content = (
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `agent-toolkit/.claude-plugin/plugin.json`\n\n"
            "## 実行方法\n\n"
            "agent_toolkit_bump.py を実行する\n"
        )
        assert not _plan_format.has_manifest_files_when_bump_step_present(content)


class TestIsAgentDocTargetFile:
    """is_agent_doc_target_file の対象パス判定を検査する。"""

    def test_matches_agent_references_md(self) -> None:
        assert _plan_format.is_agent_doc_target_file("agent-toolkit/references/plan-impl/execution-process.md")

    def test_matches_chezmoi_dot_claude_skills(self) -> None:
        assert _plan_format.is_agent_doc_target_file(".chezmoi-source/dot_claude/skills/refine-prompt/SKILL.md")

    def test_matches_agents_top_md(self) -> None:
        assert _plan_format.is_agent_doc_target_file("AGENTS.md")

    def test_matches_claude_md_in_subdirectory(self) -> None:
        assert _plan_format.is_agent_doc_target_file("subdir/CLAUDE.md")

    def test_matches_rules_with_subdirectory(self) -> None:
        assert _plan_format.is_agent_doc_target_file("agent-toolkit/rules/sub/nested.md")

    def test_does_not_match_unrelated_path(self) -> None:
        assert not _plan_format.is_agent_doc_target_file("pytools/foo.py")

    def test_empty_path_returns_false(self) -> None:
        assert not _plan_format.is_agent_doc_target_file("")

    def test_backslash_path_normalized(self) -> None:
        assert _plan_format.is_agent_doc_target_file("agent-toolkit\\agents\\foo.md")


class TestPlanFormatSsot:
    """PLAN_REQUIRED_H2がplan-file-guidelines.mdと整合することを検査する。"""

    def test_required_h2_appear_in_plan_file_ref(self):
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        for heading in _plan_format.PLAN_REQUIRED_H2:
            assert f"## {heading}" in text, f"plan-file-guidelines.md に `## {heading}` が無い"

    def test_section_definition_order_matches_required_h2(self):
        """`plan-file-guidelines.md`のセクション定義H3と`PLAN_REQUIRED_H2`の順序が一致することを検査する。

        セクション定義H3は`### XXX（`## YYY`）`形式で記述されており、
        バッククォート内のH2名（YYY）が登場順に`PLAN_REQUIRED_H2`と完全一致するべき。
        """
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        pattern = re.compile(r"^### .+?（`## ([^`]+)`）", re.MULTILINE)
        defined_h2 = tuple(pattern.findall(text))
        assert defined_h2 == _plan_format.PLAN_REQUIRED_H2, (
            f"plan-file-guidelines.md のセクション定義順 {defined_h2} が"
            f" PLAN_REQUIRED_H2 {_plan_format.PLAN_REQUIRED_H2} と一致しない"
        )


def test_iter_h3_sections_under_h2_absent_h2() -> None:
    content = "## 別の節\n\nbody\n"
    assert not list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))


def test_iter_h3_sections_under_h2_no_h3() -> None:
    content = "## 変更内容\n\nbodyのみ\n"
    assert not list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))


def test_iter_h3_sections_under_h2_single_h3() -> None:
    content = "## 変更内容\n\n### foo\nbody1\nbody2\n"
    result = list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))
    assert len(result) == 1
    assert result[0][0] == "foo"
    assert [line for _, line in result[0][1]] == ["body1", "body2"]


def test_iter_h3_sections_under_h2_multiple_h3_and_h2_boundary() -> None:
    content = "## 変更内容\n\n### a\naaa\n### b\nbbb\nbbb2\n## 次\n### c\nccc\n"
    result = list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))
    headings = [h for h, _ in result]
    assert headings == ["a", "b"]
    assert [line for _, line in result[1][1]] == ["bbb", "bbb2"]


def test_iter_h3_sections_under_h2_preserves_code_fence_lines() -> None:
    content = "## 変更内容\n\n### foo\n```text\ncontent\n```\n"
    result = list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))
    assert len(result) == 1
    body_texts = [line for _, line in result[0][1]]
    assert "```text" in body_texts
    assert "```" in body_texts
