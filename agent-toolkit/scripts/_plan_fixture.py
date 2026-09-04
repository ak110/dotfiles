"""計画本文の検体を`_plan_format`の構造定数から組み立てる。

計画ファイル（メイン）、計画ファイル（詳細）、計画ファイル（バグ）の正常系本文を書式ごとに1箇所で組み立て、
固定H2名、表の列名及び表の行名を構造定数から導出する。
書式の改訂で追随が必要な値を本ファイルへ集約し、各テストが同じ値を文字列リテラルとして個別に持たない状態を保つ。
違反検体は、本ファイルが公開する行・表・見出しの定数を用いた置換で各テストが組み立てる。
"""

from __future__ import annotations

import pathlib

import _plan_format

BASE_COMMIT: str = "0123456789012345678901234567890123456789"
"""計画メタ情報のベースコミットへ書く参照値。"""

REPOSITORY: str = "/repo"
"""計画メタ情報の対象リポジトリの既定値。実在するGitルートとの照合を伴う検査では実パスを渡す。"""

VERIFICATION_COMMAND: str = "`pytest`"
INTEGRATION_COMMAND: str = "`make test`"

IMPLEMENTATION_H3: str = "変更説明"
"""`## 実装資料`直下へ置く自由H3の代表例。"""
IMPLEMENTATION_BODY: str = "対象の構造を更新する。"
COMPLETION_BODY: str = "基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。"

BUG_UNIT_H3: str = "対象の不整合"
BUG_FILLER: str = "発生条件と実際値を記載する。"
CAUSE_FILLER: str = "原因分析を記録する。"
TABLE_FILLER: str = "検討結果を記載する。"


def _header_row(header: tuple[str, ...]) -> str:
    """列名行と区切り行を組み立てる。"""
    return f"| {' | '.join(header)} |\n| {' | '.join('---' for _column in header)} |"


def item_row(name: str, filler: str = TABLE_FILLER) -> str:
    """`項目`・`内容`の2列表の1行を組み立てる。"""
    return f"| {name} | {filler} |"


def bug_row(name: str) -> str:
    """バグ調査表の1行を組み立てる。"""
    return item_row(name, BUG_FILLER)


def rows_table(rows: tuple[str, ...], filler: str = TABLE_FILLER) -> str:
    """行名を固定した`項目`・`内容`の2列表を組み立てる。"""
    return "\n".join([_header_row(_plan_format.PLAN_BUG_TABLE_HEADER), *(item_row(row, filler) for row in rows)])


def bug_cause_table(filler: str = CAUSE_FILLER) -> str:
    """バグ単位の原因分析表を組み立てる。"""
    header = _plan_format.PLAN_BUG_CAUSE_TABLE_HEADER
    contents = " | ".join(filler for _column in header[1:])
    return "\n".join([_header_row(header), *(f"| {row} | {contents} |" for row in _plan_format.PLAN_BUG_CAUSE_TABLE_ROWS)])


BUG_VARIANT_CURRENT: str = "current"
"""現行の調査表。原因分析表と固定行の調査表を置く。"""
BUG_VARIANT_LEGACY_ROWS: str = "legacy_rows"
"""統廃合前の調査表。原因分析表を伴い、行構成だけが旧い。"""
BUG_VARIANT_LEGACY_STANDALONE: str = "legacy_standalone"
"""原因分析表を持たない旧調査表。"""


def bug_investigation_table(filler: str = BUG_FILLER) -> str:
    """バグ単位の固定行の調査表を組み立てる。"""
    return rows_table(_plan_format.PLAN_BUG_TABLE_ROWS, filler)


def legacy_bug_investigation_table(filler: str = BUG_FILLER) -> str:
    """統廃合前の行構成を持つ調査表を組み立てる。"""
    return rows_table(_plan_format.PLAN_LEGACY_BUG_TABLE_ROWS, filler)


def bug_file(*, title: str = "計画の主題", variant: str = BUG_VARIANT_CURRENT) -> str:
    """計画ファイル（バグ）の正常系本文を返す。

    ``variant``は``BUG_VARIANT_CURRENT``、``BUG_VARIANT_LEGACY_ROWS``、
    ``BUG_VARIANT_LEGACY_STANDALONE``のいずれかを受理する。
    """
    if variant == BUG_VARIANT_LEGACY_STANDALONE:
        body = rows_table(_plan_format.PLAN_LEGACY_STANDALONE_BUG_TABLE_ROWS, BUG_FILLER)
    elif variant == BUG_VARIANT_LEGACY_ROWS:
        body = f"{bug_cause_table()}\n\n{legacy_bug_investigation_table()}"
    else:
        body = f"{bug_cause_table()}\n\n{bug_investigation_table()}"
    return f"# {title}\n\n### {BUG_UNIT_H3}\n\n{body}\n"


def inline_bug_section(*, variant: str = BUG_VARIANT_CURRENT) -> str:
    """計画本文へ直接置く旧形式の`## バグ調査結果`節を返す。"""
    body = bug_file(variant=variant).split(f"### {BUG_UNIT_H3}\n\n", 1)[1]
    return f"## {_plan_format.PLAN_H2_BUG}\n\n### {BUG_UNIT_H3}\n\n{body}\n"


def bug_reference_section(reference: str | pathlib.Path) -> str:
    """計画ファイル（バグ）の分離先を参照する`## バグ調査結果`節を返す。"""
    return f"## {_plan_format.PLAN_H2_BUG}\n\n{_plan_format.PLAN_BUG_FILE_REFERENCE_PREFIX} {reference}\n\n"


VERIFICATION_TABLE: str = (
    f"{_header_row(_plan_format.PLAN_VERIFICATION_TABLE_HEADER)}\n"
    f"| {_plan_format.PLAN_VERIFICATION_TABLE_ROWS[0]} | {VERIFICATION_COMMAND} |\n"
    f"| {_plan_format.PLAN_VERIFICATION_TABLE_ROWS[1]} | {INTEGRATION_COMMAND} |\n"
)
PROGRESS_TABLE: str = f"{_header_row(_plan_format.PLAN_PROGRESS_TABLE_HEADER)}\n"
PROGRESS_ROW: str = "| 2026-08-09 12:00 | 実装 | 成功。 |\n"

PERMANENCE_ROW: str = "| 更新経路を恒久化する | エージェント提案詳細 | 対象ファイル | 後続の更新でも参照するため。 |"
PERMANENCE_TABLE: str = f"{_header_row(_plan_format.PLAN_PERMANENCE_TABLE_HEADER)}\n{PERMANENCE_ROW}"
REFACTORING_TABLE: str = rows_table(_plan_format.PLAN_REFACTORING_TABLE_ROWS)
LEGACY_SIMILAR_REVIEW_SECTION: str = (
    f"### {_plan_format.PLAN_LEGACY_PERMANENCE_H3[0]}\n\n{rows_table(('母集団', '点検観点', '該当箇所'))}\n\n"
)
"""廃止済みのH3を持つ既存計画を再現する節。読み取り互換の検体だけに使う。"""


def _permanence_sections(*, refactoring: str = REFACTORING_TABLE, legacy_similar_review: bool = False) -> str:
    """`## 恒久化・リファクタリング内容`の本文を組み立てる。"""
    return (
        f"## {_plan_format.PLAN_H2_PERMANENCE}\n\n"
        f"### {_plan_format.PLAN_PERMANENCE_H3[0]}\n\n{PERMANENCE_TABLE}\n\n"
        f"### {_plan_format.PLAN_PERMANENCE_H3[1]}\n\n{refactoring}\n\n"
        + (LEGACY_SIMILAR_REVIEW_SECTION if legacy_similar_review else "")
    )


# --- 新書式（人間向けの計画ファイル（メイン）・計画ファイル（詳細）） ---

WI_FILES: tuple[tuple[str, str], ...] = (
    ("20260817-223603-001.md", "入力の境界を追加確認する"),
    ("20260817-223603-002.md", "保留中の要求を確認する"),
)
"""`関連WI`へ記載する正本ファイル名と1行要約。"""

USER_ACTION_SUBJECT: str = "公開契約に必要な変更を実装する"
USER_ACTION_ROW: str = f"| {USER_ACTION_SUBJECT} | ユーザー指示 | 採用 | - |"
PROPOSAL_ACTION_ROW: str = (
    "| 類似するが対象外の記述は変更しない | エージェント提案 | 対象外 | 当初目的と公開契約への影響が無いため。 |"
)
WI_ACTION_REASON: str = "対象外の入力経路は変更しない。要求範囲に含まれないため。"
WI_ACTION_ROW: str = f"| 入力の境界を追加確認する | 人間由来のWI ({WI_FILES[0][0]}) | 部分採用 | {WI_ACTION_REASON} |"
JUDGMENT_ROW: str = "| 類似するが対象外の記述は変更しない | 影響なし。 | 対象外。 | 維持する。 | 実測。 |"

USER_EVENT_HEADING: str = f"### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}1"
LEGACY_USER_EVENT_HEADING: str = f"### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}: 本セッションの直接指示"
USER_EVENT_TEXT: str = "公開契約に必要な変更だけを実施する。"
HISTORY_REVIEW_HEADING: str = "### レビューで確定した変更"
HISTORY_REVIEW_BODY: str = "レビューで確認した対象範囲を反映した。"

HUMAN_UNIT_ROWS: tuple[str, ...] = (
    f"| 契約境界の更新 | 公開契約の判定を更新する | なし | 1 | {VERIFICATION_COMMAND} |",
    f"| 回帰検証の追加 | 更新後の挙動を検証する | 契約境界の更新 | 2 | {VERIFICATION_COMMAND} |",
)
HUMAN_UNITS_TABLE: str = "\n".join([_header_row(_plan_format.PLAN_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER), *HUMAN_UNIT_ROWS])


def human_action_table(*, wi: bool) -> str:
    """新書式の実施内容4列表を組み立てる。"""
    rows = [USER_ACTION_ROW, PROPOSAL_ACTION_ROW]
    if wi:
        rows.append(WI_ACTION_ROW)
    return "\n".join([_header_row(_plan_format.PLAN_HUMAN_ACTION_TABLE_HEADER), *rows])


def _related_wi_block(entries: tuple[tuple[str, str], ...]) -> str:
    """計画メタ情報の`関連WI`項目を組み立てる。"""
    field = _plan_format.PLAN_METADATA_RELATED_WI_FIELD
    if not entries:
        return f"- {field}: なし"
    children = "\n".join(f"  - {filename}: {summary}" for filename, summary in entries)
    return f"- {field}:\n{children}"


def legacy_wi_names(content: str) -> str:
    """新書式の本文を、改名前の`関連WI`項目名と`由来`欄の区分へ差し戻す。"""
    return content.replace(
        f"- {_plan_format.PLAN_METADATA_RELATED_WI_FIELD}:",
        f"- {_plan_format.PLAN_METADATA_LEGACY_RELATED_FEEDBACK_FIELD}:",
    ).replace(_plan_format.PLAN_HUMAN_WI_ORIGIN, _plan_format.PLAN_LEGACY_HUMAN_FEEDBACK_ORIGIN)


def human_main(
    *,
    repo: str | pathlib.Path = REPOSITORY,
    base: str = "作成時点の参照値",
    work_type: str = "通常変更",
    related_wi: tuple[tuple[str, str], ...] = (),
) -> str:
    """新書式の人間向け計画ファイル（メイン）の正常系本文を返す。"""
    judgment_table = "\n".join([_header_row(_plan_format.PLAN_HUMAN_JUDGMENT_TABLE_HEADER), JUDGMENT_ROW])
    return f"""# 計画の主題

## {_plan_format.PLAN_H2_OVERVIEW}

対象の公開契約を更新する。

### {_plan_format.PLAN_METADATA_H3}

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `{repo}`
{_related_wi_block(related_wi)}
- 作業種別: {work_type}
- ベースコミット: `{base}`

## {_plan_format.PLAN_H2_ACTION}

{human_action_table(wi=bool(related_wi))}

## {_plan_format.PLAN_H2_AGENT_JUDGMENT}

{judgment_table}

## {_plan_format.PLAN_H2_HISTORY}

{USER_EVENT_HEADING}

```text
{USER_EVENT_TEXT}
```

{HISTORY_REVIEW_HEADING}

{HISTORY_REVIEW_BODY}

## {_plan_format.PLAN_H2_VERIFICATION}

{VERIFICATION_TABLE}
## {_plan_format.PLAN_H2_TERMINATION}

なし

## {_plan_format.PLAN_H2_PROGRESS}

{PROGRESS_TABLE}"""


def human_detail(*, bug_section: str = "", legacy_similar_review: bool = False) -> str:
    """新書式の人間向け計画ファイル（詳細）の正常系本文を返す。"""
    permanence = _permanence_sections(legacy_similar_review=legacy_similar_review)
    return f"""{bug_section}{permanence}## {_plan_format.PLAN_H2_IMPLEMENTATION}

### {_plan_format.PLAN_IMPLEMENTATION_UNITS_H3}

{HUMAN_UNITS_TABLE}

### {IMPLEMENTATION_H3}

{IMPLEMENTATION_BODY}

## {_plan_format.PLAN_H2_COMPLETION}

{COMPLETION_BODY}
"""


# --- 旧二ファイル形式（ID表）の検体 ---

TWO_FILE_ACTION_ROW: str = "| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-001 |"
TWO_FILE_ACTION_TABLE: str = "\n".join([_header_row(_plan_format.PLAN_ACTION_TABLE_HEADER), TWO_FILE_ACTION_ROW])
HISTORY_USER_ROW: str = "| H-001 | ユーザー発言 | P-001 | 採用した。 | `## 実施内容` |"
HISTORY_TABLE: str = "\n".join([_header_row(_plan_format.PLAN_HISTORY_TABLE_HEADER), HISTORY_USER_ROW])
MATERIAL_ROWS: tuple[str, ...] = (
    "| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |",
    "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
)
REQUIREMENT_ROWS: tuple[str, ...] = (
    "| R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 "
    "| 指示と合意を反映するため。 |",
    "| R-P-001-002 | P-001 | 対象外の検査を追加しない。 | 不採用 | 非該当 | 対象外の検査 | 実装上不要であるため。 |",
    "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |",
)
TWO_FILE_REQUIREMENT_ROW: str = (
    "| R-P-001-001 | P-001 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 | 指示を反映するため。 |"
)
TWO_FILE_UNIT_ROW: str = f"| U-001 | 診断件数を更新する | なし | 1 | {VERIFICATION_COMMAND} |"
TWO_FILE_UNITS_TABLE: str = "\n".join([_header_row(_plan_format.PLAN_IMPLEMENTATION_UNITS_TABLE_HEADER), TWO_FILE_UNIT_ROW])
LEGACY_MATERIALS_SECTION: str = f"""## {_plan_format.PLAN_H2_MATERIALS}

P-001:

```text
診断件数を2件から1件へ減らし、公開APIと対象外の挙動を変更しないでほしい。
```

"""


def _materials_section(*, requirements: tuple[str, ...], materials: tuple[str, ...]) -> str:
    """提示素材の素材表と要求表を組み立てる。"""
    material_table = "\n".join([_header_row(_plan_format.PLAN_MATERIAL_TABLE_HEADER), *materials])
    requirement_table = "\n".join([_header_row(_plan_format.PLAN_REQUIREMENT_TABLE_HEADER), *requirements])
    return f"## {_plan_format.PLAN_H2_MATERIALS}\n\n{material_table}\n\n{requirement_table}\n\n"


def two_file_main(
    *,
    repo: str | pathlib.Path = REPOSITORY,
    base: str = BASE_COMMIT,
    detail_name: str = "sample.detail.md",
    work_type: str = "通常変更",
) -> str:
    """旧二ファイル形式の計画ファイル（メイン）を返す。読み取り互換の検体に使う。"""
    materials = _materials_section(materials=MATERIAL_ROWS[:1], requirements=(TWO_FILE_REQUIREMENT_ROW,))
    return f"""# 計画の主題

## {_plan_format.PLAN_H2_OVERVIEW}

成果を得る。

### {_plan_format.PLAN_METADATA_H3}

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `{repo}`
- 作業種別: {work_type}
- ベースコミット: `{base}`
- {_plan_format.PLAN_METADATA_DETAIL_FIELD}: `{detail_name}`

## {_plan_format.PLAN_H2_ACTION}

{TWO_FILE_ACTION_TABLE}

{materials}## {_plan_format.PLAN_H2_LEGACY_HISTORY}

{HISTORY_TABLE}

## {_plan_format.PLAN_H2_VERIFICATION}

{VERIFICATION_TABLE}
## {_plan_format.PLAN_H2_TERMINATION}

なし

## {_plan_format.PLAN_H2_LEGACY_PROGRESS}

{PROGRESS_TABLE}"""


def two_file_detail(*, bug_section: str = "", legacy_similar_review: bool = True) -> str:
    """旧二ファイル形式の計画ファイル（詳細）を返す。"""
    permanence = _permanence_sections(legacy_similar_review=legacy_similar_review)
    return f"""{bug_section}{permanence}## {_plan_format.PLAN_H2_IMPLEMENTATION}

### {_plan_format.PLAN_IMPLEMENTATION_UNITS_H3}

{TWO_FILE_UNITS_TABLE}

### {IMPLEMENTATION_H3}

{IMPLEMENTATION_BODY}

## {_plan_format.PLAN_H2_COMPLETION}

{COMPLETION_BODY}
"""


def to_canonical_main(content: str) -> str:
    """旧見出しの計画ファイル（メイン）を新しい固定H2へ変換する。"""
    return (
        content.replace(
            f"\n## {_plan_format.PLAN_H2_MATERIALS}\n",
            f"\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n\nなし\n\n## {_plan_format.PLAN_H2_MATERIALS}\n",
            1,
        )
        .replace(f"## {_plan_format.PLAN_H2_LEGACY_HISTORY}", f"## {_plan_format.PLAN_H2_HISTORY}", 1)
        .replace(f"## {_plan_format.PLAN_H2_LEGACY_PROGRESS}", f"## {_plan_format.PLAN_H2_PROGRESS}", 1)
    )


# --- 旧単一ファイル形式の検体 ---

EXCLUSION_ROWS: tuple[str, ...] = (
    "| 公開契約を維持する | 対象の公開API | P-002, R-P-002-001 | 差分を確認する |",
    "| 対象外の挙動を変更しない | 対象外の入力処理 | P-001, R-P-001-002 | 回帰テストを実行する |",
)
EXCLUSION_SECTION: str = (
    f"\n### {_plan_format.PLAN_EXCLUSION_H3}\n\n"
    + "\n".join([_header_row(_plan_format.PLAN_EXCLUSION_TABLE_HEADER), *EXCLUSION_ROWS])
    + "\n"
)


def single_file_plan(
    *,
    repo: str | pathlib.Path = REPOSITORY,
    base: str = BASE_COMMIT,
    bug: bool = False,
    exclusions: bool = True,
) -> str:
    """旧単一ファイル形式の計画本文を返す。読み取り互換の検体に使う。"""
    work_type = "バグ対応" if bug else "通常変更"
    materials = _materials_section(materials=MATERIAL_ROWS, requirements=REQUIREMENT_ROWS)
    exclusion = EXCLUSION_SECTION if exclusions else ""
    # 除外・保持表を置かない検体では、当該表でだけ被覆していた採用要求の参照を`根拠`へ移して被覆を保つ。
    action_table = (
        TWO_FILE_ACTION_TABLE
        if exclusions
        else TWO_FILE_ACTION_TABLE.replace("| R-P-001-001 |", "| R-P-001-001, R-P-002-001 |", 1)
    )
    bug_body = inline_bug_section() if bug else ""
    permanence = _permanence_sections(legacy_similar_review=True)
    return f"""# 計画の主題

## {_plan_format.PLAN_H2_OVERVIEW}

成果を得る。

### {_plan_format.PLAN_METADATA_H3}

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `{repo}`
- 作業種別: {work_type}
- ベースコミット: `{base}`

## {_plan_format.PLAN_H2_ACTION}

{action_table}
{exclusion}
{materials}## {_plan_format.PLAN_H2_LEGACY_HISTORY}

{HISTORY_TABLE}

{bug_body}{permanence}## {_plan_format.PLAN_H2_IMPLEMENTATION}

### {IMPLEMENTATION_H3}

{IMPLEMENTATION_BODY}

## {_plan_format.PLAN_H2_COMPLETION}

{COMPLETION_BODY}

## {_plan_format.PLAN_H2_LEGACY_PROGRESS}

{PROGRESS_TABLE}"""


def legacy_materials_single_file_plan(*, repo: str | pathlib.Path = REPOSITORY, base: str = BASE_COMMIT) -> str:
    """旧素材記法と旧3列の実施内容表を持つ単一ファイル計画を返す。"""
    content = single_file_plan(repo=repo, base=base)
    start = content.index(f"## {_plan_format.PLAN_H2_MATERIALS}")
    end = content.index(f"## {_plan_format.PLAN_H2_LEGACY_HISTORY}")
    content = content[:start] + LEGACY_MATERIALS_SECTION + content[end:]
    legacy_action_table = "\n".join(
        [
            _header_row(_plan_format.PLAN_LEGACY_ACTION_TABLE_HEADER),
            "| 診断件数を2件から1件へ減らす | 指示どおり | P-001 |",
        ]
    )
    content = content.replace(TWO_FILE_ACTION_TABLE, legacy_action_table, 1)
    content = content.replace("素材・要求参照", "原文参照")
    # 旧形式では要求IDを持たないため、除外・保持の参照を素材IDだけへそろえる。
    return content.replace("P-002, R-P-002-001", "P-001").replace("P-001, R-P-001-002", "P-001")
