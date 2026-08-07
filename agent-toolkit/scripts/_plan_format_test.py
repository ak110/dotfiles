"""計画形式の共通解析を検証する。"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position

_VALID_CONTENT = """## 目的

成果

## 実装契約

### 計画メタ情報

- 対象リポジトリ: `/repo`
- ベースコミット: `0123456789012345678901234567890123456789`
- 作業種別: 通常変更

### 対象ファイル一覧

- `existing.py`
- `new.py`（新設）
- `old.py`（削除）

## 完了条件

検証できる。

## 進捗ログ

未着手。
"""


def test_required_anchors_accept_any_order_and_additional_h2() -> None:
    """4アンカーは順序と追加H2に依存せず受理される。"""
    content = _VALID_CONTENT.replace("## 目的\n", "## 補足\n\n任意。\n\n## 目的\n").replace(
        "## 完了条件\n\n検証できる。\n\n## 進捗ログ",
        "## 進捗ログ\n\n未着手。\n\n## 完了条件\n\n検証できる。\n\n## 追加情報",
    )
    assert not _plan_format.check_h2_order(content)


def test_required_anchors_report_missing_and_duplicate() -> None:
    """意味アンカーの欠落と重複はそれぞれ報告される。"""
    content = _VALID_CONTENT.replace("## 完了条件\n\n検証できる。\n\n", "").replace(
        "## 目的\n", "## 目的\n\n成果。\n\n## 目的\n", 1
    )
    violations = _plan_format.check_h2_order(content)
    assert any("missing" in violation and "完了条件" in violation for violation in violations)
    assert any("unique" in violation and "目的" in violation for violation in violations)


def test_extract_h2_sections_ignores_frontmatter_fences_and_comments() -> None:
    """構造外の見出し候補は抽出されない。"""
    content = """---
title: x
---
## 実在

```text
## フェンス内
```

<!--
## コメント内
-->
"""
    assert _plan_format.extract_h2_sections(content) == ["実在"]


def test_extract_plan_targets_supports_existing_new_and_deleted() -> None:
    """通常箇条書きの3状態を構造化して返す。"""
    assert _plan_format.extract_plan_targets(_VALID_CONTENT) == [
        _plan_format.PlanTarget("existing.py"),
        _plan_format.PlanTarget("new.py", "new"),
        _plan_format.PlanTarget("old.py", "deleted"),
    ]
    assert _plan_format.extract_target_files_from_changes(_VALID_CONTENT) == ["existing.py", "new.py", "old.py"]


def test_extract_plan_targets_ignores_other_sections_and_fences() -> None:
    """対象H3外とコードフェンス内の類似項目は抽出しない。"""
    content = (
        _VALID_CONTENT.replace(
            "- `existing.py`\n",
            "```text\n- `hidden.py`\n```\n- `existing.py`\n",
        )
        + "\n## 補足\n\n### 対象ファイル一覧\n\n- `other.py`\n"
    )
    assert _plan_format.extract_target_files_from_changes(content) == ["existing.py", "new.py", "old.py"]


def test_find_invalid_target_paths() -> None:
    """絶対パスと親参照を危険な対象パスとして返す。"""
    content = _VALID_CONTENT.replace(
        "- `existing.py`",
        "- `/abs/file.py`\n- `C:\\abs\\file.py`\n- `../outside.py`\n- `safe/file.py`",
    )
    assert _plan_format.find_invalid_target_file_paths(content) == [
        "/abs/file.py",
        "C:\\abs\\file.py",
        "../outside.py",
    ]


def test_allowed_repo_root_comment_cannot_authorize_absolute_target() -> None:
    """本文コメントで絶対パスを自己許可できない。"""
    content = "<!-- allowed-repo-root: /other -->\n" + _VALID_CONTENT.replace("- `existing.py`", "- `/other/file.py`")
    assert _plan_format.find_invalid_target_file_paths(content) == ["/other/file.py"]


def test_find_invalid_target_entries_reports_unrecognized_bullets() -> None:
    """有効な対象と併記された形式外の箇条書きを報告する。"""
    content = _VALID_CONTENT.replace("- `existing.py`", "- `existing.py`\n- [ ] `hidden.py`\n* `other.py`")
    assert _plan_format.find_invalid_target_entries(content) == [
        (16, "- [ ] `hidden.py`"),
        (17, "* `other.py`"),
    ]


def test_bump_contract_uses_implementation_contract() -> None:
    """版更新宣言とmanifest対象は実装契約内で判定する。"""
    content = _VALID_CONTENT.replace(
        "- `old.py`（削除）",
        "- `old.py`（削除）\n"
        "- `agent-toolkit/skills/example/SKILL.md`\n"
        "- `agent-toolkit/.claude-plugin/plugin.json`\n"
        "- `.claude-plugin/marketplace.json`",
    ).replace("### 対象ファイル一覧", "scripts/agent_toolkit_bump.py minor\n\n### 対象ファイル一覧")
    assert _plan_format.has_bump_step_when_required(content)
    assert _plan_format.has_manifest_files_when_bump_step_present(content)


def test_agent_document_target_paths() -> None:
    """配布規範とagent定義をエージェント向け文書として判定する。"""
    assert _plan_format.is_agent_doc_target_file("agent-toolkit/skills/example/SKILL.md")
    assert _plan_format.is_agent_doc_target_file("agent-toolkit/agents/example.md")
    assert not _plan_format.is_agent_doc_target_file("pytools/example.py")


def test_iter_h3_sections_keeps_raw_fence_body() -> None:
    """H3本文走査はフェンスを含む生の本文を保持する。"""
    content = "## 実装契約\n\n### 一\n\n```text\n### 偽\n```\n\n### 二\n\n本文\n"
    sections = list(_plan_format.iter_h3_sections_under_h2(content, "実装契約"))
    assert [heading for heading, _ in sections] == ["一", "二"]
    assert any(line == "### 偽" for _, line in sections[0][1])
