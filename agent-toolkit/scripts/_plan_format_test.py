"""計画形式の共通解析を検証する。"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position

_BASE = "0123456789012345678901234567890123456789"

_HUMAN_SECTION = """# 計画の主題

## 概要

成果を得る。

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `/repo`
- 作業種別: 通常変更
- ベースコミット: `{base}`

## 実施内容

| 実施内容 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- |
| 診断件数を2件から1件へ減らす | 指示どおり | R-P-001-001 |

### 合意済みの除外・保持

| 合意内容 | 対象と箇所 | 素材・要求参照 | 確認方法 |
| --- | --- | --- | --- |
| 公開契約を維持する | 対象の公開API | P-001, R-P-001-001 | 差分を確認する |
| 対象外の挙動を変更しない | 対象外の入力処理 | P-001, R-P-001-001 | 回帰テストを実行する |

## 提示素材

| 素材ID | 種別 | キューID | 投入元 | 引用範囲 |
| --- | --- | --- | --- | --- |
| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |
| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |

| 要求ID | 素材参照 | 実装に必要な要件 | 採否 | 採用範囲 | 除外範囲 | 根拠 |
| --- | --- | --- | --- | --- | --- | --- |
| R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 | 指示と合意を反映するため。 |
| R-P-001-002 | P-001 | 対象外の検査を追加しない。 | 不採用 | 非該当 | 対象外の検査 | 実装上不要であるため。 |
| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |

## 変更履歴

| ID | 起点 | 指摘内容 | 採否・現在の結論 | 同期先 |
| --- | --- | --- | --- | --- |
| H-001 | ユーザー発言 | P-001 | 採用した。 | `## 実施内容` |

## 恒久化・リファクタリング内容

### 恒久化

| 知見 | 出所 | 反映先 | 根拠 |
| --- | --- | --- | --- |
| 更新経路を恒久化する | エージェント判断 | 対象ファイル | 後続の更新でも参照するため。 |

### リファクタリング

| 項目 | 内容 |
| --- | --- |
| 対象 | 対象ファイル。 |
| 現状の問題 | 重複がある。 |
| 対応 | 共通化する。 |
| 本計画に含めるか | 含める。 |

### 類似見直し

| 項目 | 内容 |
| --- | --- |
| 母集団 | リポジトリ全体。 |
| 点検観点 | 同じ重複が残るか。 |
| 該当箇所 | 対象ファイル。 |
"""

_IMPLEMENTER_SECTION = """
## 実装資料

### ファイル群別の変更説明

対象の構造と検査を更新する。

## 完了条件

基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。

## 進捗ログ

| 日時 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
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
| 類似見直し結果 | 対象ファイル。 |
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
        human = human.replace("\n## 恒久化・リファクタリング内容", f"{_BUG_SECTION}\n## 恒久化・リファクタリング内容")
    return human + _IMPLEMENTER_SECTION


_VALID_CONTENT = _plan()


def _legacy_plan() -> str:
    """旧形式の素材と合意表を持つ互換fixtureを返す。"""
    start = _VALID_CONTENT.index("## 提示素材")
    end = _VALID_CONTENT.index("## 変更履歴")
    legacy_materials = """## 提示素材

P-001:

```text
診断件数を2件から1件へ減らし、公開APIと対象外の挙動を変更しないでほしい。
```

"""
    content = _VALID_CONTENT[:start] + legacy_materials + _VALID_CONTENT[end:]
    content = content.replace(
        "| 実施内容 | ユーザー指示との関係 | 根拠 |\n"
        "| --- | --- | --- |\n"
        "| 診断件数を2件から1件へ減らす | 指示どおり | R-P-001-001 |",
        "| 実施内容 | ユーザー指示との関係 | 根拠 |\n"
        "| --- | --- | --- |\n"
        "| 診断件数を2件から1件へ減らす | 指示どおり | P-001 |",
    )
    content = content.replace("素材・要求参照", "原文参照")
    content = content.replace("P-001, R-P-001-001", "P-001")
    return content


_LEGACY_CONTENT = _legacy_plan()


def test_canonical_plan_passes_structure_check() -> None:
    """通常変更とバグ対応の正規形はいずれも構造検査を通過する。"""
    assert not _plan_format.check_plan_structure(_VALID_CONTENT)
    assert not _plan_format.check_plan_structure(_plan(bug=True))


def test_optional_exclusion_section_may_be_absent() -> None:
    """除外・保持の合意が無い計画は任意H3を省略できる。"""
    start = _VALID_CONTENT.index("### 合意済みの除外・保持")
    end = _VALID_CONTENT.index("## 提示素材")
    assert not _plan_format.check_plan_structure(_VALID_CONTENT[:start] + _VALID_CONTENT[end:])


def test_canonical_fixture_accepts_mixed_agreements_and_numeric_target() -> None:
    """実施・除外・保持の条項分解と数値目標を含む正規fixtureを受理する。"""
    assert "診断件数を2件から1件へ減らす" in _VALID_CONTENT
    assert "対象外の挙動を変更しない" in _VALID_CONTENT
    assert "基準値は診断2件、目標は1件" in _VALID_CONTENT
    assert not _plan_format.check_plan_structure(_VALID_CONTENT)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | 実装経過 | P-001 |"), "`起点`は"),
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | ユーザー発言 | 要約 |"), "素材IDだけを書く"),
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | ユーザー発言 | P-999 |"), "素材IDが提示素材に無い"),
    ],
)
def test_history_origin_and_user_material_reference_are_checked(mutation: tuple[str, str], message: str) -> None:
    """変更履歴の起点固定値とユーザー発言の素材ID参照を検査する。"""
    errors = _plan_format.check_plan_structure(_VALID_CONTENT.replace(*mutation, 1))
    assert any(message in error for error in errors), errors


def test_implementation_materials_allows_free_h3_composition() -> None:
    """実装資料配下では自由なH3構成を受理する。"""
    content = _VALID_CONTENT.replace("### ファイル群別の変更説明", "### 実行方法\n\n手順。\n\n### 変更説明")
    assert not _plan_format.check_plan_structure(content)


def test_permanence_rejects_free_h3() -> None:
    """恒久化領域では固定3見出し以外のH3を拒否する。"""
    content = _VALID_CONTENT.replace("\n## 実装資料", "\n### 任意の補足\n\n補足する。\n\n## 実装資料")
    assert any("固定見出し以外のH3" in error for error in _plan_format.check_plan_structure(content))


def test_duplicate_fixed_table_is_rejected() -> None:
    """同一H2内に複製した固定表を拒否する。"""
    duplicate = """| 実施内容 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- |
| 追加の変更 | 具体化 | 検査契約を満たすため |

"""
    content = _VALID_CONTENT.replace("### 合意済みの除外・保持", duplicate + "### 合意済みの除外・保持", 1)
    errors = _plan_format.check_plan_structure(content)
    assert any("固定表は1件必要" in error for error in errors), errors


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("# 計画の主題\n\n", ""), "ATX H1が1件必要"),
        (("成果を得る。\n\n### 計画メタ情報", "### 計画メタ情報"), "直下の地の文"),
        (("## 変更履歴", "## 追加のH2\n\n補足。\n\n## 変更履歴"), "固定H2は"),
        (
            (
                "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n",
                "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n\n## 後書き\n\n補足。\n",
            ),
            "固定H2は",
        ),
        (("### 計画メタ情報", "### 総論"), "`### 計画メタ情報`を検査できない"),
        (("## 提示素材", "## 素材"), "固定H2は"),
        (("- 起動経路: `agent-toolkit:plan-mode`\n", ""), "この順序で1行ずつ置く"),
        (("- 作業種別: 通常変更", "- 作業種別: `通常変更`"), "バッククォートで囲まない"),
        (("- 対象リポジトリ: `/repo`", "- 対象リポジトリ: /repo"), "バッククォートで囲む"),
        (("- 作業種別: 通常変更", "- 作業種別: 改善"), "`作業種別`は"),
        ((f"- ベースコミット: `{_BASE}`", "- ベースコミット: `0123456`"), "完全長SHA"),
        (("| H-001 | ユーザー発言 | P-001 | 採用した。 | `## 実施内容` |\n", ""), "1行以上の内容が必要"),
        (
            ("| ID | 起点 | 指摘内容 | 採否・現在の結論 | 同期先 |", "| ID | 起点 | 指摘内容 | 結論 | 同期先 |"),
            "`## 変更履歴`は",
        ),
        (("| 日時 | 完了した工程 | 結果・特記事項 |", "| 日時 | 工程 | 結果 |"), "`## 進捗ログ`は"),
        (
            (
                "| 公開契約を維持する | 対象の公開API | P-001, R-P-001-001 | 差分を確認する |",
                "| 公開契約を維持する | 対象の公開API | P-999 | 差分を確認する |",
            ),
            "素材・要求参照が提示素材に無い",
        ),
        (
            ("| 診断件数を2件から1件へ減らす | 指示どおり |", "| 診断件数を2件から1件へ減らす | 追加対応 |"),
            "`ユーザー指示との関係`は",
        ),
        (
            (
                "| 公開契約を維持する | 対象の公開API | P-001, R-P-001-001 | 差分を確認する |",
                "| 公開契約を維持する |  | P-001, R-P-001-001 | 差分を確認する |",
            ),
            "空cell",
        ),
        (("### 類似見直し\n", "### 追加見直し\n"), "`### 類似見直し`は1件必要"),
        (("| 母集団 | リポジトリ全体。 |", "| 対象 | リポジトリ全体。 |"), "3行表を置く"),
        (("| 知見 | 出所 | 反映先 | 根拠 |", "| 項目 | 内容 |"), "4列表を置く"),
        (
            ("| 更新経路を恒久化する | エージェント判断 | 対象ファイル | 後続の更新でも参照するため。 |\n", ""),
            "表に1行以上の内容が必要",
        ),
        (("| 現状の問題 | 重複がある。 |\n", ""), "4行表を置く"),
        (("## 実装資料", "## 変更対象"), "固定H2は"),
        (
            (
                "基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。",
                "基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。\n\n#### 自由なH4",
            ),
            "H4以深の見出しは置かない",
        ),
    ],
)
def test_structure_violations_are_rejected(mutation: tuple[str, str], message: str) -> None:
    """固定領域の欠落、順序違反、追加H2、表違反を個別に拒否する。"""
    content = _VALID_CONTENT.replace(*mutation, 1)
    errors = _plan_format.check_plan_structure(content)
    assert any(message in error for error in errors), errors


@pytest.mark.parametrize(
    "rows",
    [
        "",
        "| 2026-08-09 12:00 | 実装 | 成功。 |\n",
        "| 2026-08-09 12:00 | 実装 | 成功。 |\n| 2026-08-09 13:00 | 検証 | 成功。 |\n",
    ],
)
def test_progress_table_accepts_zero_one_or_multiple_rows(rows: str) -> None:
    """進捗表だけは0件を許容し、1件以上の既存形式も受理する。"""
    marker = "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n"
    content = _VALID_CONTENT.replace(marker, marker + rows, 1)
    assert not _plan_format.check_plan_structure(content)


def test_progress_table_rejects_empty_cells_when_a_row_exists() -> None:
    """進捗表に内容行がある場合は従来どおり全cellの値を要求する。"""
    marker = "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n"
    content = _VALID_CONTENT.replace(marker, marker + "| 2026-08-09 12:00 | 実装 |  |\n", 1)
    errors = _plan_format.check_plan_structure(content)
    assert any("空cell" in error for error in errors), errors


def test_permanence_table_accepts_no_candidate_row() -> None:
    """候補0件の理由を記載した1行の恒久化表を受理する。"""
    no_candidate = "| 候補なし | 提示素材と調査結果 | 計画限り | 当該計画固有でない知見が無いため。 |"
    content = _VALID_CONTENT.replace(
        "| 更新経路を恒久化する | エージェント判断 | 対象ファイル | 後続の更新でも参照するため。 |",
        no_candidate,
    )
    assert not _plan_format.check_plan_structure(content)


def test_permanence_table_rejects_no_candidate_row_mixed_with_findings() -> None:
    """候補なしの行と実在する知見の混在を拒否する。"""
    row = "| 更新経路を恒久化する | エージェント判断 | 対象ファイル | 後続の更新でも参照するため。 |"
    no_candidate = "| 候補なし | 提示素材と調査結果 | 計画限り | 当該計画固有でない知見が無いため。 |"
    errors = _plan_format.check_plan_structure(_VALID_CONTENT.replace(row, f"{no_candidate}\n{row}"))
    assert any("`候補なし`の行だけを置く" in error for error in errors), errors


def test_permanence_table_accepts_no_candidate_phrase_within_finding() -> None:
    """予約値を含む通常の知見と別の知見を併記した表を受理する。"""
    row = "| 更新経路を恒久化する | エージェント判断 | 対象ファイル | 後続の更新でも参照するため。 |"
    additional = "| 候補なし表記の検査を追加する | P-001 | 対象ファイル | 誤った記載を拒否するため。 |"
    assert not _plan_format.check_plan_structure(_VALID_CONTENT.replace(row, f"{row}\n{additional}"))


def test_permanence_table_accepts_multiple_findings() -> None:
    """恒久化表は知見を複数行で記載できる。"""
    row = "| 更新経路を恒久化する | エージェント判断 | 対象ファイル | 後続の更新でも参照するため。 |"
    additional = "| 公開契約を維持する | P-001 | 計画限り | 既存文書へ記載済みのため。 |"
    assert not _plan_format.check_plan_structure(_VALID_CONTENT.replace(row, f"{row}\n{additional}"))


@pytest.mark.parametrize(
    "row",
    [
        "| 更新経路を恒久化する | エージェント判断 |  | 後続の更新でも参照するため。 |",
        "| 更新経路を恒久化する | エージェント判断 | 対象ファイル |",
    ],
)
def test_permanence_table_rejects_empty_cells_and_column_mismatch(row: str) -> None:
    """恒久化表の空セルと列数不一致を拒否する。"""
    original = "| 更新経路を恒久化する | エージェント判断 | 対象ファイル | 後続の更新でも参照するため。 |"
    errors = _plan_format.check_plan_structure(_VALID_CONTENT.replace(original, row))
    assert any("空cellまたは列数不一致" in error for error in errors), errors


def test_permanence_sections_reject_conclusion_words_only() -> None:
    """恒久化等の結論語だけの記載を検討の省略として拒否する。"""
    content = _VALID_CONTENT[: _VALID_CONTENT.index("### 類似見直し")] + "### 類似見直し\n\n該当なし\n" + _IMPLEMENTER_SECTION
    errors = _plan_format.check_plan_structure(content)
    assert any("結論語だけの記載は成立しない" in error for error in errors)


def test_structure_check_allows_bug_work_type_without_similar_review_table() -> None:
    """構造検査はバグ対応へ3行表を一律に要求せず、バグ調査表との意味上の対応をレビュー担当へ委ねる。"""
    content = _plan(bug=True)
    reference = "### 類似見直し\n\n観点と結果はバグ調査表を正本として参照する。\n"
    content = content[: content.index("### 類似見直し")] + reference + _IMPLEMENTER_SECTION
    assert not _plan_format.check_plan_structure(content)


@pytest.mark.parametrize(
    "table",
    [
        "| 項目 | 内容 |\n| --- | --- |\n| 母集団 | 全体。 |",
        "| 項目 | 内容 |\n| - | - |\n| 母集団 | 全体。 |",
        "| 項目 | 内容 |\n|:-:|:-:|\n| 母集団 | 全体。 |",
        "項目 | 内容\n--- | ---\n母集団 | 全体。",
    ],
)
def test_extract_tables_accepts_gfm_notations(table: str) -> None:
    """区切り行のダッシュ数、整列コロン、行頭パイプ省略の各記法を表として抽出する。"""
    lines: list[tuple[int, str]] = list(enumerate(table.splitlines(), start=1))
    assert _plan_format.extract_tables(lines) == [_plan_format.MarkdownTable(1, ("項目", "内容"), (("母集団", "全体。"),))]


def test_extract_tables_keeps_row_column_count() -> None:
    """列数が見出しと異なる行を切り詰めずに返し、列数不一致を後段で検出できるようにする。"""
    table = "| A | B |\n| --- | --- |\n| 1 | 2 | 3 |\n| 4 |"
    lines: list[tuple[int, str]] = list(enumerate(table.splitlines(), start=1))
    assert _plan_format.extract_tables(lines)[0].rows == (("1", "2", "3"), ("4",))


def test_structure_check_accepts_short_delimiter_tables() -> None:
    """区切り行が1ダッシュの固定表を受理する。"""
    assert not _plan_format.check_plan_structure(_VALID_CONTENT.replace(" --- ", " - "))


def test_legacy_materials_require_verbatim_fence() -> None:
    """旧形式では素材IDの直後に逐語fenceが無い提示素材を拒否する。"""
    content = _LEGACY_CONTENT.replace(
        "```text\n診断件数を2件から1件へ減らし、公開APIと対象外の挙動を変更しないでほしい。\n```",
        "診断件数を2件から1件へ減らし、公開APIと対象外の挙動を変更しないでほしい。",
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("逐語転記が無い" in error for error in errors)


def test_parse_plan_materials_returns_structured_ids_and_legacy_flag() -> None:
    """新旧形式の素材と要求を識別し、ID集合を返す。"""
    materials, errors = _plan_format.parse_plan_materials(_VALID_CONTENT)
    assert not errors
    assert materials == _plan_format.PlanMaterials(
        frozenset({"P-001", "P-002"}),
        frozenset({"R-P-001-001", "R-P-001-002", "R-P-002-001"}),
        False,
        frozenset({"R-P-001-001", "R-P-002-001"}),
    )

    legacy_materials, legacy_errors = _plan_format.parse_plan_materials(_LEGACY_CONTENT)
    assert not legacy_errors
    assert legacy_materials == _plan_format.PlanMaterials(frozenset({"P-001"}), frozenset(), True)


def test_new_material_tables_take_priority_over_legacy_fence() -> None:
    """新形式の表と旧形式の素材記法が混在する場合は新形式を解析する。"""
    legacy_tail = """
P-999:

```text
旧形式の本文。
```
"""
    content = _VALID_CONTENT.replace("\n## 変更履歴", f"{legacy_tail}\n## 変更履歴", 1)
    materials, errors = _plan_format.parse_plan_materials(content)
    assert not errors
    assert materials is not None
    assert not materials.is_legacy
    assert materials.material_ids == frozenset({"P-001", "P-002"})


def test_structured_material_ids_preserve_full_namespace() -> None:
    """英字、ハイフン及びアンダースコアを含む素材IDを要求IDの名前空間へ保持する。"""
    content = _VALID_CONTENT.replace("P-001", "P-alpha_1-x")
    content = content.replace("P-alpha_1-x, P-002", "P-002, P-alpha_1-x")
    first = (
        "| R-P-alpha_1-x-001 | P-002, P-alpha_1-x | 診断件数を2件から1件へ減らす。 | "
        "採用 | 診断件数の更新 | 非該当 | 指示と合意を反映するため。 |"
    )
    rejected = (
        "| R-P-alpha_1-x-002 | P-alpha_1-x | 対象外の検査を追加しない。 | 不採用 | 非該当 | "
        "対象外の検査 | 実装上不要であるため。 |"
    )
    second = "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |"
    content = content.replace(f"{first}\n{rejected}\n{second}", f"{second}\n{first}\n{rejected}", 1)
    materials, errors = _plan_format.parse_plan_materials(content)
    assert not errors
    assert materials is not None
    assert materials.material_ids == frozenset({"P-alpha_1-x", "P-002"})
    assert materials.requirement_ids == frozenset({"R-P-alpha_1-x-001", "R-P-alpha_1-x-002", "R-P-002-001"})
    assert materials.adopted_requirement_ids == frozenset({"R-P-alpha_1-x-001", "R-P-002-001"})


def test_action_references_rejected_requirement_are_rejected() -> None:
    """実施内容の根拠に不採用要求を指定できない。"""
    content = _VALID_CONTENT.replace(
        "| 診断件数を2件から1件へ減らす | 指示どおり | R-P-001-001 |",
        "| 診断件数を2件から1件へ減らす | 指示どおり | R-P-001-002 |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("不採用要求を参照できない: R-P-001-002" in error for error in errors), errors


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
            "| P-002 | 利用者合意 | queue.md | 本セッション | 全文 |",
            "キューIDは非該当にする",
        ),
        (
            "| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |",
            "| P-001 | フィードバック | 非該当 | 値なし | 本文全文 |",
            "フィードバック素材のキューIDが不正である",
        ),
        (
            "| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |",
            "| P-001 | フィードバック | feedback.md | 値なし | 本文全文 |",
            "フィードバック素材のキューIDが不正である",
        ),
        (
            "| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |",
            "| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 要約 |",
            "引用範囲は本文全文にする",
        ),
        (
            "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
            "| P-002 | 利用者合意 | 非該当 | AskUserQuestion | 全文 |",
            "回答全文にする",
        ),
        (
            "R-P-002-001 | P-002 |",
            "R-P-002-000 | P-002 |",
            "末尾連番が001から欠番なく続かない",
        ),
        (
            "R-P-002-001 | P-002 |",
            "R-P-999-001 | P-002 |",
            "素材表に無い",
        ),
        (
            "R-P-001-001 | P-001, P-002 |",
            "R-P-001-001 | P-002, P-001 |",
            "素材参照はID昇順で並べる",
        ),
        (
            "R-P-001-001 | P-001, P-002 |",
            "R-P-001-001 | P-001, P-001 |",
            "素材参照が重複している",
        ),
        (
            "R-P-001-001 | P-001, P-002 |",
            "R-P-001-001 | P-001/P-002 |",
            "素材参照は`P-001, P-002`形式で記載する",
        ),
        (
            "R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 |",
            "R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 保留 | 診断件数の更新 | 非該当 |",
            "採否は採用又は不採用にする",
        ),
        (
            "R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 |",
            "R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 非該当 | 非該当 |",
            "採用範囲又は除外範囲が不正である",
        ),
    ],
)
def test_structured_material_contract_rejects_invalid_combinations(old: str, new: str, message: str) -> None:
    """素材種別、参照、要求ID及び採否の不整合を拒否する。"""
    materials, errors = _plan_format.parse_plan_materials(_VALID_CONTENT.replace(old, new, 1))
    assert materials is not None
    assert any(message in error for error in errors), errors


def test_structured_material_contract_rejects_duplicate_material_id() -> None:
    """素材IDの重複を拒否する。"""
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        "| P-001 | 利用者合意 | 非該当 | 本セッション | 全文 |\n| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("素材IDが重複している" in error for error in errors), errors


def test_structured_material_contract_rejects_requirement_order_and_gap() -> None:
    """要求表のID順序と素材内連番の欠落を拒否する。"""
    content = _VALID_CONTENT.replace("R-P-002-001 | P-002 |", "R-P-002-003 | P-002 |", 1)
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("末尾連番が001から欠番なく続かない" in error for error in errors), errors


def test_structured_material_contract_rejects_requirement_table_order() -> None:
    """要求表を要求IDの昇順以外で並べた場合に拒否する。"""
    first = (
        "| R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | "
        "診断件数の更新 | 非該当 | 指示と合意を反映するため。 |"
    )
    rejected = "| R-P-001-002 | P-001 | 対象外の検査を追加しない。 | 不採用 | 非該当 | 対象外の検査 | 実装上不要であるため。 |"
    second = "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |"
    content = _VALID_CONTENT.replace(f"{first}\n{rejected}\n{second}", f"{second}\n{rejected}\n{first}", 1)
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("要求表は要求ID昇順" in error for error in errors), errors


def test_structured_material_contract_requires_adjacent_tables() -> None:
    """素材表と要求表の間に説明文又は別表を置かない。"""
    content = _VALID_CONTENT.replace(
        "\n| 要求ID | 素材参照 |",
        "\n説明文を配置する。\n\n| 要求ID | 素材参照 |",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("素材表の直後に要求表" in error for error in errors), errors


@pytest.mark.parametrize(
    "row",
    [
        "| P-002 | 利用者指示 | 非該当 | 本セッション | 全文 |",
        "| P-002 | 利用者指示 | 非該当 | 委譲元:user message | 第1段落 |",
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        "| P-002 | 利用者合意 | 非該当 | AskUserQuestion | 回答全文 |",
        "| P-002 | 利用者合意 | 非該当 | TBD:decision.md#回答 | 回答全文 |",
        "| P-002 | 参考素材 | 非該当 | docs/reference.md | 節1 |",
        "| P-002 | 処理対象資料 | 非該当 | input.json | $.items |",
        "| P-002 | 起動事実 | 非該当 | 常駐自動起動 | 非該当 |",
    ],
)
def test_structured_material_types_preserve_source_and_citation(row: str) -> None:
    """フィードバック以外の素材種別も投入元と引用範囲を保持して受理する。"""
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        row,
        1,
    )
    materials, errors = _plan_format.parse_plan_materials(content)
    assert not errors
    assert materials is not None
    assert not materials.is_legacy


@pytest.mark.parametrize("material_type", ["参考素材", "処理対象資料", "起動事実"])
def test_structured_material_types_may_be_unreferenced(material_type: str) -> None:
    """参考素材、処理対象資料及び起動事実は要求を直接持たなくても受理する。"""
    row = {
        "参考素材": "| P-002 | 参考素材 | 非該当 | docs/reference.md | 節1 |",
        "処理対象資料": "| P-002 | 処理対象資料 | 非該当 | input.json | $.items |",
        "起動事実": "| P-002 | 起動事実 | 非該当 | 常駐自動起動 | 非該当 |",
    }[material_type]
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        row,
        1,
    )
    content = content.replace("P-001, P-002", "P-001", 1)
    content = content.replace(
        "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |\n",
        "",
        1,
    )
    materials, errors = _plan_format.parse_plan_materials(content)
    assert not errors
    assert materials is not None


def test_structured_material_types_reject_unreferenced_user_agreement() -> None:
    """利用者指示又は利用者合意を要求表から未参照にしない。"""
    content = _VALID_CONTENT.replace("P-001, P-002", "P-001", 1).replace(
        "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |\n",
        "",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("要求表から参照されていない" in error for error in errors), errors


@pytest.mark.parametrize("material_id", ["P-001（利用者発言）:", "P-001: 利用者発言"])
def test_material_id_annotation_is_rejected_near_the_invalid_line(material_id: str) -> None:
    content = _LEGACY_CONTENT.replace("P-001:", material_id, 1)

    errors = _plan_format.check_plan_structure(content)

    assert f"提示素材の素材ID行に注記を含めない: {material_id}" in errors


def test_material_id_candidate_check_ignores_normal_notes_and_fenced_text() -> None:
    content = _LEGACY_CONTENT.replace(
        "P-001:\n\n```text\n対象を更新してほしい。",
        "注記: 提示素材の説明\nSource: user transcript\n\nP-001:\n\n```text\nP-999: fence内の文字列\n対象を更新してほしい。",
    )

    errors = _plan_format.check_plan_structure(content)

    assert not any("素材ID行に注記" in error for error in errors)


def test_bug_section_requires_fixed_fourteen_rows() -> None:
    """バグ調査表の14行から1行を削除した計画を拒否する。"""
    content = _plan(bug=True).replace("| 動機的要因 | 更新頻度が低いと仮定した。 |\n", "")
    errors = _plan_format.check_plan_structure(content)
    assert any("固定14行の調査表" in error for error in errors)


def test_bug_section_is_required_only_for_bug_work_type() -> None:
    """バグ対応でのみバグ調査結果を要求し、通常変更では置かせない。"""
    missing = _plan(bug=True).replace(_BUG_SECTION, "\n")
    assert any("固定H2" in error for error in _plan_format.check_plan_structure(missing))
    extra = _VALID_CONTENT.replace("\n## 恒久化・リファクタリング内容", f"{_BUG_SECTION}\n## 恒久化・リファクタリング内容")
    assert any("作業種別が`通常変更`" in error for error in _plan_format.check_plan_structure(extra))


def test_metadata_prefers_canonical_placement() -> None:
    """正規配置がある計画では旧配置を無視する。"""
    content = _VALID_CONTENT.replace(
        "### ファイル群別の変更説明",
        f"### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n\n### ファイル群別の変更説明",
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
    "line",
    [
        f"- ベースコミット: `{'a' * 40}`（`git rev-parse HEAD`で実測）",
        f"- ベースコミット: `{'a' * 40}`（実測値）。",
        f"  - ベースコミット: `{'a' * 40}`",
        f"- 基準コミット:  `{'a' * 40}`",
    ],
)
def test_metadata_reads_base_commit_with_legacy_notations(line: str) -> None:
    """注記付き、字下げ、旧別名のベースコミット記法からもOIDを読み取る。"""
    content = f"## 背景\n\n### 計画メタ情報\n\n{line}\n"
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert not errors
    assert metadata is not None
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


def test_markdown_body_lines_exclude_frontmatter_fences_and_comments() -> None:
    """フロントマター、コードフェンス、複数行HTMLコメントの行は本文行に含めない。"""
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
    headings = [line for _lineno, line in _plan_format.iter_markdown_body_lines(content) if line.startswith("## ")]
    assert headings == ["## 実在"]


def test_agent_document_target_paths() -> None:
    """配布規範とagent定義をエージェント向け文書として判定する。"""
    assert _plan_format.is_agent_doc_target_file("agent-toolkit/skills/example/SKILL.md")
    assert _plan_format.is_agent_doc_target_file("agent-toolkit/agents/example.md")
    assert not _plan_format.is_agent_doc_target_file("pytools/example.py")
