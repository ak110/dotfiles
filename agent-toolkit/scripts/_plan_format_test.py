"""計画形式の共通解析を検証する。"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position

_BASE = "0123456789012345678901234567890123456789"

_HUMAN_SECTION = """# 計画の主題

## 変更履歴

| ID | 起点 | 採否・現在の結論 | 同期先 |
| --- | --- | --- | --- |
| H-001 | 初版 | 固定構造で起草した。 | `目的`、`対応方針` |

## 目的

### 概要

成果を得る。

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `/repo`
- 作業種別: 通常変更
- ベースコミット: `{base}`

### 提示素材

P-001:

```text
対象を更新してほしい。
```

### ユーザー合意済み事項

| 合意事項 | 適用範囲 | 原文参照 |
| --- | --- | --- |
| 対象を更新する | 対象ファイルだけ | P-001 |

## 対応方針

### 実施内容

| 実施内容 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- |
| 対象を更新する | 指示どおり | P-001が更新を求めている。 |

### 恒久化・リファクタリング内容

#### 恒久化

| 項目 | 内容 |
| --- | --- |
| 観測事象 | 対象が古い。 |
| 根本原因 | 更新経路が無い。 |
| 反映先 | 対象ファイル。 |
| 反映内容 | 更新手順を追加する。 |
| 対策強度 | 機械検査で担保する。 |

#### リファクタリング

| 項目 | 内容 |
| --- | --- |
| 対象 | 対象ファイル。 |
| 現状の問題 | 重複がある。 |
| 対応 | 共通化する。 |
| 本計画に含めるか | 含める。 |

#### 類似見直し

| 項目 | 内容 |
| --- | --- |
| 母集団 | リポジトリ全体。 |
| 点検観点 | 同じ重複が残るか。 |
| 該当件数と箇所 | 1件、対象ファイル。 |
"""

_IMPLEMENTER_SECTION = """
## 実装契約

### 対象ファイル一覧

- `existing.py`
- `new.py`（新設）
- `old.py`（削除）

共通変更説明だけで実装を特定する。

## 完了条件

検証が成功する。

## 進捗ログ

未着手。
"""

_BUG_SECTION = """
## バグ調査結果

### バグ調査結果: 対象の陳腐化

| 項目 | 内容 |
| --- | --- |
| 観測事象 | 発生条件は更新後の再実行であり、実際値は旧値のままである。 |
| 期待する契約 | 更新後の値を返す。 |
| 直接的原因 | キャッシュを破棄していない。 |
| 混入要因 | 破棄経路を設計に含めなかった。 |
| 動機的要因 | 更新頻度が低いと仮定した。 |
| 見逃し原因 | 再実行の回帰テストが無かった。 |
| 根本原因 | 更新と破棄の対応付けが契約化されていない。 |
| 原因分析の根拠 | 再現ログと導入コミットを確認した。 |
| 類似見直しの観点 | 同じキャッシュ利用箇所が残るか。 |
| 類似見直し結果 | 1件、対象ファイル。 |
| 是正処置 | 破棄処理を追加する。 |
| 横展開処置 | 同型の利用箇所を修正する。 |
| 再発防止処置 | 回帰テストを追加する。 |
| 設計意図の記録 | 破棄契約をコメントへ残す。 |
"""


def _plan(*, base: str = _BASE, bug: bool = False) -> str:
    """固定構造を満たす計画本文を返す。"""
    human = _HUMAN_SECTION.format(base=base)
    if bug:
        human = human.replace("- 作業種別: 通常変更", "- 作業種別: バグ対応")
        human = human.replace("\n## 対応方針", f"{_BUG_SECTION}\n## 対応方針")
    return human + _IMPLEMENTER_SECTION


_VALID_CONTENT = _plan()


def test_canonical_plan_passes_structure_check() -> None:
    """通常変更とバグ対応の正規形はいずれも構造検査を通過する。"""
    assert not _plan_format.check_plan_structure(_VALID_CONTENT)
    assert not _plan_format.check_plan_structure(_plan(bug=True))


def test_implementer_region_allows_free_composition() -> None:
    """実装者向け領域のH2構成を変えても構造検査は通過する。"""
    content = _VALID_CONTENT.replace("## 実装契約", "## 変更内容").replace(
        "## 完了条件", "## 実行方法\n\n手順。\n\n## 完了条件"
    )
    assert not _plan_format.check_plan_structure(content)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("# 計画の主題\n\n", ""), "ATX H1が1件必要"),
        (("## 変更履歴", "## 追加のH2\n\n補足。\n\n## 変更履歴"), "人間向け固定領域のH2"),
        (("## 対応方針", "## 補足\n\n任意。\n\n## 対応方針"), "人間向け固定領域のH2"),
        (("\n## 進捗ログ\n\n未着手。\n", "\n## 進捗ログ\n\n未着手。\n\n## 後書き\n\n補足。\n"), "最後のH2にする"),
        (("### 概要", "### 総論"), "`### 概要`は1件必要"),
        (("### 提示素材\n\nP-001:", "### ユーザー合意済み事項\n\nP-001:"), "1件必要"),
        (("- 起動経路: `agent-toolkit:plan-mode`\n", ""), "この順序で1行ずつ置く"),
        (("- 作業種別: 通常変更", "- 作業種別: `通常変更`"), "バッククォートで囲まない"),
        (("- 対象リポジトリ: `/repo`", "- 対象リポジトリ: /repo"), "バッククォートで囲む"),
        (("- 作業種別: 通常変更", "- 作業種別: 改善"), "`作業種別`は"),
        ((f"- ベースコミット: `{_BASE}`", "- ベースコミット: `0123456`"), "完全長SHA"),
        (("| H-001 | 初版 | 固定構造で起草した。 | `目的`、`対応方針` |\n", ""), "1行以上の内容が必要"),
        (("| ID | 起点 | 採否・現在の結論 | 同期先 |", "| ID | 起点 | 結論 | 同期先 |"), "`## 変更履歴`は"),
        (
            ("| 対象を更新する | 対象ファイルだけ | P-001 |", "| 対象を更新する | 対象ファイルだけ | P-999 |"),
            "原文参照が提示素材に無い",
        ),
        (("| 対象を更新する | 指示どおり |", "| 対象を更新する | 追加対応 |"), "`ユーザー指示との関係`は"),
        (("| 対象を更新する | 対象ファイルだけ | P-001 |", "| 対象を更新する |  | P-001 |"), "空cell"),
        (("#### 類似見直し\n", "#### 追加見直し\n"), "`#### 類似見直し`は1件必要"),
        (("| 母集団 | リポジトリ全体。 |", "| 対象 | リポジトリ全体。 |"), "3行表を置く"),
        (("| 反映内容 | 更新手順を追加する。 |\n", ""), "5行表を置く"),
        (("| 現状の問題 | 重複がある。 |\n", ""), "4行表を置く"),
        (("### 対象ファイル一覧", "### 変更対象"), "`### 対象ファイル一覧`は1件必要"),
    ],
)
def test_structure_violations_are_rejected(mutation: tuple[str, str], message: str) -> None:
    """固定領域の欠落、順序違反、追加H2、表違反を個別に拒否する。"""
    content = _VALID_CONTENT.replace(*mutation, 1)
    errors = _plan_format.check_plan_structure(content)
    assert any(message in error for error in errors), errors


def test_permanence_sections_reject_conclusion_words_only() -> None:
    """恒久化等の結論語だけの記載を検討の省略として拒否する。"""
    content = _VALID_CONTENT[: _VALID_CONTENT.index("#### 類似見直し")] + "#### 類似見直し\n\n該当なし\n" + _IMPLEMENTER_SECTION
    errors = _plan_format.check_plan_structure(content)
    assert any("結論語だけの記載は成立しない" in error for error in errors)


def test_materials_require_verbatim_fence() -> None:
    """素材IDの直後に逐語fenceが無い提示素材を拒否する。"""
    content = _VALID_CONTENT.replace("```text\n対象を更新してほしい。\n```", "対象を更新してほしい。")
    errors = _plan_format.check_plan_structure(content)
    assert any("逐語転記が無い" in error for error in errors)


def test_bug_section_requires_fixed_fourteen_rows() -> None:
    """バグ調査表の14行から1行を削除した計画を拒否する。"""
    content = _plan(bug=True).replace("| 動機的要因 | 更新頻度が低いと仮定した。 |\n", "")
    errors = _plan_format.check_plan_structure(content)
    assert any("固定14行の調査表" in error for error in errors)


def test_bug_section_is_required_only_for_bug_work_type() -> None:
    """バグ対応でのみバグ調査結果を要求し、通常変更では置かせない。"""
    missing = _plan(bug=True).replace(_BUG_SECTION, "\n")
    assert any("人間向け固定領域のH2" in error for error in _plan_format.check_plan_structure(missing))
    extra = _VALID_CONTENT.replace("\n## 対応方針", f"{_BUG_SECTION}\n## 対応方針")
    assert any("`## バグ調査結果`は置かない" in error for error in _plan_format.check_plan_structure(extra))


def test_metadata_prefers_canonical_placement() -> None:
    """正規配置がある計画では旧配置を無視する。"""
    content = _VALID_CONTENT.replace(
        "### 対象ファイル一覧",
        f"### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n\n### 対象ファイル一覧",
    )
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert not errors
    assert metadata is not None
    assert metadata.is_canonical
    assert metadata.values["ベースコミット"] == _BASE


def test_metadata_falls_back_to_legacy_placement() -> None:
    """正規配置が無い既存計画は旧配置を読み取り互換で解析する。"""
    content = f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n"
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert not errors
    assert metadata is not None
    assert metadata.parent == "背景"
    assert metadata.base_commit_candidates == ("a" * 40,)


@pytest.mark.parametrize(
    "content",
    [
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n\n"
        f"### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n",
        f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n\n"
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n",
    ],
)
def test_metadata_rejects_ambiguous_placement(content: str) -> None:
    """旧配置の候補が複数ある計画は曖昧として解析結果を返さない。"""
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert metadata is None
    assert errors


def test_metadata_rejects_conflicting_values() -> None:
    """同じ項目に異なる値を持つ計画は競合として拒否する。"""
    content = _VALID_CONTENT.replace(
        "- 作業種別: 通常変更\n",
        "- 作業種別: 通常変更\n- 作業種別: バグ対応\n",
    )
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert metadata is None
    assert any("競合する値" in error for error in errors)


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


def test_extract_plan_targets_ignores_human_region_and_fences() -> None:
    """人間向け固定領域とコードフェンス内の類似項目は抽出しない。"""
    content = _VALID_CONTENT.replace(
        "- `existing.py`\n",
        "```text\n- `hidden.py`\n```\n- `existing.py`\n",
    ).replace(
        "### 概要\n",
        "### 概要\n\n### 対象ファイル一覧\n\n- `human.py`\n",
    )
    assert _plan_format.extract_target_files_from_changes(content) == ["existing.py", "new.py", "old.py"]


def test_extract_plan_targets_reads_legacy_contract_section() -> None:
    """`## 対応方針`を持たない既存計画は`## 実装契約`配下を読み取り互換で扱う。"""
    content = "## 目的\n\nx\n\n## 実装契約\n\n### 対象ファイル一覧\n\n- `legacy.py`\n\n## 進捗ログ\n\nx\n"
    assert _plan_format.extract_target_files_from_changes(content) == ["legacy.py"]


@pytest.mark.parametrize(
    ("path", "is_invalid"),
    [
        ("/abs/file.py", True),
        ("../outside.py", True),
        ("C:\\Windows\\system.ini", True),
        ("D:outside.py", True),
        ("\\\\server\\share\\system.ini", True),
        ("\\Windows\\system.ini", True),
        ("safe/path.py", False),
    ],
)
def test_target_path_boundary(path: str, *, is_invalid: bool) -> None:
    """全対応形式の対象パス境界を検証する。"""
    content = _VALID_CONTENT.replace("- `existing.py`", f"- `{path}`")
    expected = [path] if is_invalid else []
    assert _plan_format.find_invalid_target_file_paths(content) == expected


def test_allowed_repo_root_comment_cannot_authorize_absolute_target() -> None:
    """本文コメントで絶対パスを自己許可できない。"""
    content = "<!-- allowed-repo-root: /other -->\n" + _VALID_CONTENT.replace("- `existing.py`", "- `/other/file.py`")
    assert _plan_format.find_invalid_target_file_paths(content) == ["/other/file.py"]


@pytest.mark.parametrize(
    ("entry", "is_invalid"),
    [
        ("* Makefile", True),
        ("* src/module", True),
        ("- `Makefile` extra", True),
        ("- [ ] Makefile", True),
        ("- plain/path（新設）", True),
        ("- 変更対象の例は `src/module.py` とする", False),
        ("- ファイル src/module.py を説明する", False),
    ],
)
def test_target_entry_candidate_boundary(entry: str, *, is_invalid: bool) -> None:
    """対象項目の構文標識と説明用箇条書きの境界を検証する。"""
    content = _VALID_CONTENT.replace("- `existing.py`", f"- `existing.py`\n{entry}")
    invalid = _plan_format.find_invalid_target_entries(content)
    assert bool(invalid) is is_invalid


def test_bump_contract_uses_implementer_region() -> None:
    """版更新宣言とmanifest対象は実装者向け領域で判定する。"""
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
