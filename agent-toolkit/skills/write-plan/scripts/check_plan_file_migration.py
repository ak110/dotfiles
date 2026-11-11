"""計画の旧書式からの移行警告。"""

from __future__ import annotations

from check_plan_file_common import _ClassifiedWarning

from agent_toolkit._plan import locations, structure


def _legacy_action_warnings(text: str) -> list[_ClassifiedWarning]:
    """旧3列表の実施内容表を新4列表へ移行するwarningを返す。"""
    if not structure.has_legacy_action_table(text):
        return []
    return [("migration", "実施内容表が旧3列表である。新規作成・改訂では4列表へ移行する")]


def _legacy_bug_warnings(text: str) -> list[_ClassifiedWarning]:
    """旧形式の本文内バグ調査表を分離先ファイルへ移行するwarningを返す。"""
    if not structure.has_legacy_bug_table(text):
        return []
    return [("migration", "バグ調査結果が旧形式の本文内表である。新規作成・改訂ではバグ調査ファイルへ移行する")]


def _legacy_refactoring_warnings(text: str) -> list[_ClassifiedWarning]:
    """旧2列4行のリファクタリング表を現行3列表へ移行するwarningを返す。"""
    if not structure.has_legacy_refactoring_table(text):
        return []
    return [
        (
            "migration",
            "リファクタリング表が旧2列4行形式である。新規作成・改訂では`対象`、`現状の問題`、`対応`の3列表へ移行する",
        )
    ]


def _legacy_acceptance_warnings(text: str) -> list[_ClassifiedWarning]:
    """改名前の列名を持つ受入シナリオ表を現行の列名へ移行するwarningを返す。"""
    if not structure.has_legacy_acceptance_table(text):
        return []
    header = "`, `".join(structure.PLAN_ACCEPTANCE_TABLE_HEADER)
    return [("migration", f"受入シナリオ表の列名が旧形式である。新規作成・改訂では`{header}`の7列表へ移行する")]


def _legacy_h2_warnings(text: str) -> list[_ClassifiedWarning]:
    """新書式で旧見出し別名を使っている場合の移行warningを返す。"""
    if not structure.is_canonical_main_format(text):
        return []
    headings = structure.extract_headings(text)
    names = {heading.text for heading in headings if heading.level == 2}
    warnings: list[_ClassifiedWarning] = []
    if structure.PLAN_H2_LEGACY_AGENT_JUDGMENT in names:
        warnings.append(
            ("migration", "エージェント提案の詳細の見出しが旧形式である。新規作成・改訂では`## エージェント提案詳細`へ移行する")
        )
    if structure.has_legacy_history_user_event(text):
        warnings.append(
            (
                "migration",
                "変更履歴のユーザー発言見出しが旧形式である。新規作成・改訂では"
                f"`### {structure.PLAN_HISTORY_USER_EVENT_PREFIX}<1から始まる連番>`へ移行する",
            )
        )
    if structure.PLAN_H2_LEGACY_HISTORY in names:
        warnings.append(("migration", "変更履歴の見出しが旧形式である。新規作成・改訂では`## 変更履歴（計画時）`へ移行する"))
    if structure.PLAN_H2_LEGACY_PROGRESS in names:
        warnings.append(("migration", "進捗ログの見出しが旧形式である。新規作成・改訂では`## 進捗ログ（実行時）`へ移行する"))
    return warnings


def _legacy_wi_origin_warnings(text: str) -> list[_ClassifiedWarning]:
    """実施内容表の`由来`欄が改名前のWI区分を使っている場合の移行warningを返す。"""
    return [
        ("migration", f"`## 実施内容`の`由来`が旧形式である。新規作成・改訂では`{canonical}`へ移行する")
        for canonical in structure.legacy_wi_origins(text)
    ]


def _legacy_fixed_notation_warnings(text: str) -> list[_ClassifiedWarning]:
    """読み取り互換で受理した旧形式の固定記法に移行警告を返す。"""
    warnings: list[_ClassifiedWarning] = []
    metadata, _errors = structure.parse_plan_metadata(text)
    if metadata is not None and any(field == structure.PLAN_METADATA_LEGACY_DETAIL_FIELD for field, _value in metadata.entries):
        warnings.append(("migration", "計画メタ情報の項目名が旧形式である。新規作成・改訂では`計画ファイル（詳細）`へ移行する"))
    if metadata is not None and any(
        field == structure.PLAN_METADATA_LEGACY_RELATED_FEEDBACK_FIELD for field, _value in metadata.entries
    ):
        warnings.append(
            (
                "migration",
                f"計画メタ情報の項目名が旧形式である。新規作成・改訂では`{structure.PLAN_METADATA_RELATED_WI_FIELD}`へ移行する",
            )
        )
    warnings.extend(_legacy_wi_origin_warnings(text))
    if metadata is not None and structure.PLAN_METADATA_DETAIL_FIELD in metadata.values:
        warnings.append(
            (
                "migration",
                "計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける",
            )
        )
    headings = structure.extract_headings(text)
    if structure.find_heading_index(headings, 2, structure.PLAN_H2_MATERIALS) is not None:
        warnings.append(
            (
                "migration",
                "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の"
                f"`{structure.PLAN_METADATA_RELATED_WI_FIELD}`へ移行する",
            )
        )
    if any(
        line.strip().startswith(structure.PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX)
        for _lineno, line in structure.iter_markdown_body_lines(text)
    ):
        warnings.append(
            (
                "migration",
                "バグ調査ファイル参照が旧形式である。新規作成・改訂では`- 計画ファイル（バグ）:`へ移行する",
            )
        )
    reference = structure.extract_bug_file_reference(text)
    if reference is not None and not locations.is_plan_adjunct_reference(reference):
        warnings.append(
            (
                "migration",
                "計画本文の付属ファイル参照が旧表記である。新規作成・改訂では"
                f"`{locations.PLAN_ADJUNCT_REFERENCE_PREFIX}<ファイル名>`へ移行する",
            )
        )
    return warnings


def _legacy_verification_name_warnings(text: str) -> list[_ClassifiedWarning]:
    """旧検証名の表を読取互換で受理し、改訂時だけ移行を求める。"""
    tables = structure.extract_tables(list(structure.iter_markdown_body_lines(text)))
    legacy_headers = (
        structure.PLAN_LEGACY_CURRENT_IMPLEMENTATION_UNITS_TABLE_HEADER,
        structure.PLAN_LEGACY_CURRENT_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
    )
    if any(
        table.header in legacy_headers
        or (
            table.header == structure.PLAN_VERIFICATION_TABLE_HEADER
            and table.row_labels() == structure.PLAN_LEGACY_CURRENT_SINGLE_VERIFICATION_TABLE_ROWS
        )
        for table in tables
    ):
        return [("migration", "検証名が旧形式である。新規作成・改訂では`変更範囲の検証`へ移行する")]
    return []
