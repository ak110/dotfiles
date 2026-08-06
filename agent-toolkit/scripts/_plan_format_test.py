"""_plan_format モジュールの単体テスト。

H2見出し抽出・H2節順検査・SSOT整合性を検査する。
フェンス内H2除外は本モジュールの`extract_h2_sections`が担う。
"""

import pathlib
import re

import _plan_format
import pytest

_PLAN_FILE_REF = pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "SKILL.md"

_VALID_CONTENT = (
    "# タイトル\n\n"
    "## 変更履歴\n\nx\n\n"
    "## 背景\n\nx\n\n"
    "## 対応方針\n\nx\n\n"
    "## 実装資料\n\nx\n\n"
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

    def test_backticks_crossing_block_boundary_do_not_hide_html_comment(self):
        content = "説明 `未成立\n<!--\n## コメント内\n-->\n終了`\n## 実在\n"
        assert _plan_format.extract_h2_sections(content) == ["実在"]

    @pytest.mark.parametrize(
        "blocks",
        [
            "先行 `未成立\n\n後続 `<!--`",
            "- 先行 `未成立\n\n- 後続 `<!--`",
            "| 先行 | 後続 |\n| --- | --- |\n| `未成立 | `<!--` |",
            "| 先行 | 後続 |\n| --- | --- |\n| `未成立 | 値 \\| `<!--` |",
        ],
    )
    def test_unclosed_backtick_in_previous_inline_block_does_not_affect_later_code_span(self, blocks: str):
        content = f"{blocks}\n\n## 実在\n\n-->\n"
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
            "## 実装資料\n\nx\n\n"
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

    def test_h2_inside_multiline_html_comment_not_counted_as_unexpected(self):
        content = _VALID_CONTENT.replace("## 背景\n\nx", "## 背景\n\nx\n\n<!--\n## コメント内\n-->")
        assert not _plan_format.check_h2_order(content)

    def test_h2_inside_midline_multiline_html_comment_not_counted_as_unexpected(self):
        content = _VALID_CONTENT.replace(
            "## 背景\n\nx",
            "## 背景\n\nx\n\n説明の途中 <!--\n## コメント内\n-->",
        )
        assert not _plan_format.check_h2_order(content)

    @pytest.mark.parametrize("literal", ["```text", "<!--"])
    def test_frontmatter_block_scalar_literal_does_not_hide_h2(self, literal: str):
        frontmatter = f"---\ndescription: |\n  {literal}\n---\n"
        assert not _plan_format.check_h2_order(frontmatter + _VALID_CONTENT)

    @pytest.mark.parametrize("literal", ["`<!--`", "`` `<!--` ``", r"`<!--\`", r"\<!--"])
    def test_html_comment_literal_does_not_hide_following_h2(self, literal: str):
        content = _VALID_CONTENT.replace("## 背景", f"{literal}は説明用のリテラルである。\n\n## 背景", 1)
        assert not _plan_format.check_h2_order(content)

    def test_multiline_code_span_html_comment_literal_does_not_hide_following_h2(self):
        content = _VALID_CONTENT.replace("## 背景", "`開始\n行内 <!--\n終了`\n\n## 背景", 1)
        assert not _plan_format.check_h2_order(content)

    def test_optional_completion_section_is_accepted(self):
        content = _VALID_CONTENT.replace("## 進捗ログ", "## 完了条件\n\nx\n\n## 進捗ログ", 1)
        assert not _plan_format.check_h2_order(content)

    def test_optional_section_does_not_mask_required_order_violation(self):
        content = _VALID_CONTENT.replace(
            "## 背景\n\nx\n\n## 対応方針",
            "## 対応方針\n\nx\n\n## 完了条件\n\nx\n\n## 背景",
            1,
        )
        violations = _plan_format.check_h2_order(content)
        assert any("out of order" in violation for violation in violations)

    def test_duplicate_optional_section_is_rejected(self):
        content = _VALID_CONTENT.replace(
            "## 進捗ログ",
            "## 完了条件\n\nx\n\n## 完了条件\n\nx\n\n## 進捗ログ",
            1,
        )
        violations = _plan_format.check_h2_order(content)
        assert any("optional H2 sections must be unique" in violation for violation in violations)

    def test_legacy_h2_with_transition_marker_is_accepted(self):
        content = _VALID_CONTENT.replace("## 実装資料", "## 調査結果").replace(
            "## 背景\n\nx",
            f"## 背景\n\nx\n\n{_plan_format.PLAN_LEGACY_H2_TRANSITION_MARKER}",
            1,
        )
        assert not _plan_format.check_h2_order(content)

    @pytest.mark.parametrize(
        "marker_block",
        [
            "---\ntransition: '- 計画形式移行: 調査結果から実装資料'\n---\n",
            "```text\n- 計画形式移行: 調査結果から実装資料\n```\n",
            "<!--\n- 計画形式移行: 調査結果から実装資料\n-->\n",
            "説明の途中 <!--\n- 計画形式移行: 調査結果から実装資料\n-->\n",
        ],
    )
    def test_transition_marker_in_excluded_region_is_ignored(self, marker_block: str):
        content = marker_block + _VALID_CONTENT.replace("## 実装資料", "## 調査結果")
        violations = _plan_format.check_h2_order(content)
        assert any("実装資料" in violation for violation in violations)

    def test_transition_marker_requires_exact_spelling(self):
        content = _VALID_CONTENT.replace("## 実装資料", "## 調査結果") + "\n- 計画形式移行:調査結果から実装資料\n"
        violations = _plan_format.check_h2_order(content)
        assert any("実装資料" in violation for violation in violations)

    def test_legacy_and_current_h2_cannot_be_mixed(self):
        content = _VALID_CONTENT.replace("## 実装資料", "## 調査結果\n\nx\n\n## 実装資料")
        content += f"\n{_plan_format.PLAN_LEGACY_H2_TRANSITION_MARKER}\n"
        violations = _plan_format.check_h2_order(content)
        assert any("unexpected H2 sections" in violation for violation in violations)


class TestIterMarkdownBodyLines:
    """iter_markdown_body_lines の除外領域とフェンス判定を検査する。"""

    def test_skips_frontmatter(self) -> None:
        content = "---\nkey: value\n---\n\nbody line\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "key: value" not in rendered
        assert "body line" in rendered

    @pytest.mark.parametrize("literal", ["```text", "<!--"])
    def test_frontmatter_literal_does_not_exclude_body(self, literal: str) -> None:
        content = f"---\ndescription: |\n  {literal}\n---\nbody line\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert rendered == ["body line"]

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

    def test_skips_midline_multiline_html_comment(self) -> None:
        content = "before\n説明の途中 <!--\nhidden\n-->\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert rendered == ["before", "after"]

    def test_keeps_single_line_html_comment_line(self) -> None:
        content = "before\n<!-- visible -->\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "<!-- visible -->" in rendered

    @pytest.mark.parametrize("literal", ["`<!--`", "`` `<!--` ``", r"`<!--\`", r"\<!--"])
    def test_keeps_html_comment_literal_and_following_lines(self, literal: str) -> None:
        content = f"before\n{literal}\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert rendered == ["before", literal, "after"]

    def test_keeps_multiline_code_span_html_comment_literal_and_following_lines(self) -> None:
        content = "before\n`開始\n行内 <!--\n終了`\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert rendered == ["before", "`開始", "行内 <!--", "終了`", "after"]

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

    def test_excludes_trailing_line_count_metadata(self) -> None:
        """`（現行N行）`等の付随メタ情報が抽出結果へ含まれない。

        `plan-mode/SKILL.md`が規定する標準形式
        `` `path`（現行N行） ``を検査対象とする。
        当該メタ情報はバッククォート囲みの外側にあるため捕捉群へ入らない。
        """
        content = (
            "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `app/src/foo.svelte`（現行10行）\n- [ ] `app/src/bar.ts`（新設）\n"
        )
        assert _plan_format.extract_target_files_from_changes(content) == [
            "app/src/foo.svelte",
            "app/src/bar.ts",
        ]

    def test_plain_backtick_path_without_metadata(self) -> None:
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `app/src/foo.svelte`\n"
        assert _plan_format.extract_target_files_from_changes(content) == ["app/src/foo.svelte"]

    def test_path_without_backtick_is_ignored(self) -> None:
        """バッククォートを省いた項目は`check_plan_file.py`と同じく抽出対象から外れる。"""
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] app/src/foo.svelte\n"
        assert not _plan_format.extract_target_files_from_changes(content)

    def test_completed_checkbox_is_ignored(self) -> None:
        """完了マークへ書き換えた項目は抽出対象から外れる。"""
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [x] `app/src/foo.svelte`（現行10行）\n"
        assert not _plan_format.extract_target_files_from_changes(content)

    def test_ignores_items_outside_target_file_list_h3(self) -> None:
        content = "## 変更内容\n\n### 別のH3\n\n- [ ] `app/src/foo.svelte`\n"
        assert not _plan_format.extract_target_files_from_changes(content)


class TestFindInvalidTargetFilePaths:
    """find_invalid_target_file_paths の相対パス表記違反検出を検査する。"""

    def test_find_invalid_target_file_paths_detects_absolute(self) -> None:
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `/home/user/project/foo.py`\n"
        assert _plan_format.find_invalid_target_file_paths(content) == ["/home/user/project/foo.py"]

    def test_find_invalid_target_file_paths_detects_parent_reference(self) -> None:
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `../outside/bar.py`\n"
        assert _plan_format.find_invalid_target_file_paths(content) == ["../outside/bar.py"]

    def test_find_invalid_target_file_paths_accepts_relative(self) -> None:
        content = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `agent-toolkit/scripts/atk.py`\n"
        assert not _plan_format.find_invalid_target_file_paths(content)

    def test_find_invalid_target_file_paths_handles_backtick_paths(self) -> None:
        content = (
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `/abs/foo.md`（現行1行）\n"
            "- [ ] `agent-toolkit/rules/x.md`（現行10行）\n"
        )
        assert _plan_format.find_invalid_target_file_paths(content) == ["/abs/foo.md"]

    def test_find_invalid_target_file_paths_excludes_allowed_repo_root(self) -> None:
        content = (
            "<!-- allowed-repo-root: /home/aki/other-repo -->\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `/home/aki/other-repo/docs/guide.md`（現行10行）\n"
            "- [ ] `/home/aki/unrelated-repo/foo.md`（現行1行）\n"
        )
        assert _plan_format.find_invalid_target_file_paths(content) == ["/home/aki/unrelated-repo/foo.md"]


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


_BUMP_MATRIX_NONE_REQUIRED = (
    "## 対応方針\n\n### エージェント判断\n\n"
    "| ファイル | 改訂節数 | 節名 | 判定 | 該当基準 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| `agent-toolkit/skills/foo/SKILL.md` | 1 | 「例」節 | bump不要 | コメントのみの変更 |\n"
    "| `agent-toolkit/skills/foo/SKILL_test.md` | 1 | 「例」節 | bump不要 | docstringのみの変更 |\n\n"
)

_BUMP_MATRIX_MIXED = (
    "## 対応方針\n\n### エージェント判断\n\n"
    "| ファイル | 改訂節数 | 節名 | 判定 | 該当基準 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| `agent-toolkit/skills/foo/SKILL.md` | 1 | 「例」節 | PATCH | バグ修正 |\n"
    "| `agent-toolkit/skills/foo/SKILL_test.md` | 1 | 「例」節 | bump不要 | docstringのみの変更 |\n\n"
)


class TestBumpMatrixSuppression:
    """版更新マトリクスの「判定」列が全行`bump不要`の場合の抑止動作を検査する。"""

    def test_bump_step_suppressed_by_none_required_matrix(self) -> None:
        content = (
            _BUMP_MATRIX_NONE_REQUIRED
            + "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `agent-toolkit/skills/foo/SKILL.md`\n\n"
            + "## 実行方法\n\nx\n"
        )
        assert _plan_format.has_bump_step_when_required(content)
        assert _plan_format.has_manifest_files_when_bump_step_present(content)

    def test_bump_step_not_suppressed_by_mixed_matrix(self) -> None:
        content = (
            _BUMP_MATRIX_MIXED
            + "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `agent-toolkit/skills/foo/SKILL.md`\n\n"
            + "## 実行方法\n\nx\n"
        )
        assert not _plan_format.has_bump_step_when_required(content)

    def test_manifest_not_suppressed_by_mixed_matrix_requires_manifests(self) -> None:
        """判定列混在時は抑止されず、bump script記載時にmanifest記載要件が通常どおり課される。"""
        content = (
            _BUMP_MATRIX_MIXED
            + "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `agent-toolkit/skills/foo/SKILL.md`\n\n"
            + "## 実行方法\n\n`scripts/agent_toolkit_bump.py patch`を実行する。\n"
        )
        assert not _plan_format.has_manifest_files_when_bump_step_present(content)


class TestBumpMatrixRowNamedGroups:
    """FB[4]: `_BUMP_MATRIX_ROW_RE`が`file`・`revision_count`・`criteria`列を抽出できることを検証する。"""

    def test_extracts_file_revision_count_and_criteria(self) -> None:
        content = (
            "| ファイル | 改訂節数 | 節名 | 判定 | 該当基準 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| `agent-toolkit/rules/example.md` | 1 | `対象節` | PATCH | 単一節改訂 |\n"
        )
        matches = list(_plan_format._BUMP_MATRIX_ROW_RE.finditer(content))  # pylint: disable=protected-access  # noqa: SLF001
        assert len(matches) == 1
        assert matches[0].group("file") == "agent-toolkit/rules/example.md"
        assert matches[0].group("revision_count") == "1"
        assert matches[0].group("judgment") == "PATCH"
        assert matches[0].group("criteria") == "単一節改訂"


class TestIsAgentDocTargetFile:
    """is_agent_doc_target_file の対象パス判定を検査する。"""

    @pytest.mark.parametrize(
        "path",
        [
            "agent-toolkit/skills/codex-exec/references/plan-codex-review.md",
            "agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md",
        ],
    )
    def test_matches_skill_references_md(self, path: str) -> None:
        assert _plan_format.is_agent_doc_target_file(path)

    def test_rejects_removed_top_level_references_layout(self) -> None:
        removed_path = "/".join(("agent-toolkit", "references", "removed.md"))
        assert not _plan_format.is_agent_doc_target_file(removed_path)

    def test_matches_chezmoi_dot_claude_skills(self) -> None:
        assert _plan_format.is_agent_doc_target_file(".chezmoi-source/dot_claude/skills/refine-prompt/SKILL.md")

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (".claude/rules/foo.md", True),
            (".claude/rules/agent-toolkit/01-agent.md", True),
            (".claude/skills/x/SKILL.md", True),
            (".claude/skills/x/references/y.md", True),
            (".claude/skills/x/templates/z.md", False),
            ("/home/u/proj/.claude/rules/a.md", True),
            ("sub/dir/.claude/skills/x/SKILL.md", True),
        ],
    )
    def test_matches_project_local_agent_documents(self, path: str, expected: bool) -> None:
        """プロジェクト直下の規範文書を、スキル配下の既存粒度に揃えて判定する。"""
        assert _plan_format.is_agent_doc_target_file(path) is expected

    @pytest.mark.parametrize(
        "path",
        [
            ".chezmoi-source/dot_claude/rules/myprojects.md.tmpl",
            ".chezmoi-source/dot_claude/skills/example/SKILL.md.tmpl",
        ],
    )
    def test_matches_chezmoi_markdown_template(self, path: str) -> None:
        """chezmoiテンプレートも配布先ではエージェント向け文書として読み込まれるため対象とする。"""
        assert _plan_format.is_agent_doc_target_file(path)

    def test_does_not_match_unrelated_template(self) -> None:
        """`.tmpl`終端の受理を配布元のエージェント向け文書以外へ広げない。"""
        assert not _plan_format.is_agent_doc_target_file(".chezmoi-source/dot_config/git/config.md.tmpl")

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
    """PLAN_REQUIRED_H2がplan-modeスキル本文の「計画ファイルの完成条件」節と整合することを検査する。"""

    def test_required_h2_appear_in_plan_file_ref(self):
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        for heading in _plan_format.PLAN_REQUIRED_H2:
            assert f"## {heading}" in text, f"plan-mode/SKILL.md に `## {heading}` が無い"

    def test_optional_h2_appear_as_permitted_in_plan_file_ref(self):
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        for heading in _plan_format.PLAN_OPTIONAL_H2:
            assert f"## {heading}`へ記載してもよい" in text

    def test_section_definition_order_matches_required_h2(self):
        """`plan-mode/SKILL.md`のセクション定義H3と`PLAN_REQUIRED_H2`の順序が一致することを検査する。

        セクション定義H3は`` ### `## YYY` ``形式で記述されており、
        バッククォート内のH2名（YYY）が登場順に`PLAN_REQUIRED_H2`と完全一致するべき。
        """
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        pattern = re.compile(r"^### `## ([^`]+)`", re.MULTILINE)
        defined_h2 = tuple(pattern.findall(text))
        assert defined_h2 == _plan_format.PLAN_REQUIRED_H2, (
            f"plan-mode/SKILL.md のセクション定義順 {defined_h2} が"
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


def test_iter_h3_sections_under_h2_heading_like_lines_inside_fence_are_not_boundaries() -> None:
    """フェンス内の`##`・`###`で始まる行を実見出しと誤認せず、以降のH3走査が継続することを検査する。"""
    content = "## 変更内容\n\n### foo.md\n```text\n## 偽見出し\n### 偽H3\n```\n\n### bar.md\nbody\n"
    result = list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))
    headings = [h for h, _ in result]
    assert headings == ["foo.md", "bar.md"]


def test_iter_h3_sections_under_h2_heading_like_lines_inside_tilde_fence_are_not_boundaries() -> None:
    """チルダフェンス内の`##`・`###`で始まる行も実見出しと誤認しない。"""
    content = "## 変更内容\n\n### foo.md\n~~~text\n## 偽見出し\n### 偽H3\n~~~\n\n### bar.md\nbody\n"
    result = list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))
    headings = [h for h, _ in result]
    assert headings == ["foo.md", "bar.md"]


def test_iter_h3_sections_under_h2_heading_like_lines_inside_html_comment_are_not_boundaries() -> None:
    content = "## 変更内容\n### a.md\n説明の途中 <!--\n### fake.md\n## 別H2\n-->\nbody\n"
    result = list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))
    assert result == [("a.md", [(3, "説明の途中 <!--"), (4, "### fake.md"), (5, "## 別H2"), (6, "-->"), (7, "body")])]


def test_iter_h3_sections_under_h2_unclosed_fence_to_eof_keeps_heading_like_lines_in_body() -> None:
    """フェンスがEOFまで閉じない場合、内部の見出し類似行を境界判定せず本文行として保持する。"""
    content = "## 変更内容\n\n### foo.md\n```text\n## 偽見出し\n### 偽H3\n本文\n"
    result = list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))
    headings = [h for h, _ in result]
    assert headings == ["foo.md"]
    body_lines = [line for _lineno, line in result[0][1]]
    assert "## 偽見出し" in body_lines
    assert "### 偽H3" in body_lines


def test_iter_h3_sections_under_h2_tilde_line_inside_backtick_fence_does_not_close_it() -> None:
    """バッククォートフェンス内に出現するチルダ行は閉じ判定にならない（フェンス種別混在）。"""
    content = "## 変更内容\n\n### foo.md\n```text\n~~~\n## 偽見出し\n```\n\n### bar.md\nbody\n"
    result = list(_plan_format.iter_h3_sections_under_h2(content, "変更内容"))
    headings = [h for h, _ in result]
    assert headings == ["foo.md", "bar.md"]
