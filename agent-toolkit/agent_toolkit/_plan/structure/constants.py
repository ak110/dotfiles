"""計画ファイルの見出し、固定H3および表の行名を定める構造定数と、固定H2の別名の解決。

`agent-toolkit/skills/plan-mode/references/plan-file-standards.md`は、本モジュールの構造定数から導いた受理形式を記述する。
"""

from __future__ import annotations

import pathlib
import re


def canonical_h2_name(text: str) -> str:
    """固定H2の旧別名を新書式の正規名へ写す。未知の見出しはそのまま返す。"""
    return _PLAN_H2_CANONICAL_BY_ALIAS.get(text, text)


def h2_aliases(text: str) -> tuple[str, ...]:
    """固定H2の正規名に対応する受理名を返す。"""
    canonical = canonical_h2_name(text)
    return PLAN_H2_ALIASES.get(canonical, (canonical,))


PLAN_H2_OVERVIEW: str = "概要"
PLAN_H2_ACTION: str = "実施内容"
PLAN_H2_AGENT_JUDGMENT: str = "エージェント提案詳細"
PLAN_H2_MATERIALS: str = "提示素材"
PLAN_H2_HISTORY: str = "変更履歴（計画時）"
PLAN_H2_VERIFICATION: str = "検証区分"
PLAN_H2_TERMINATION: str = "終端工程"
PLAN_H2_BUG: str = "バグ調査結果"
PLAN_H2_PERMANENCE: str = "恒久化・リファクタリング内容"
PLAN_H2_IMPLEMENTATION: str = "実装資料"
PLAN_H2_COMPLETION: str = "完了条件"
PLAN_H2_PROGRESS: str = "進捗ログ（実行時）"

PLAN_H2_REQUIREMENTS: str = "要件・外部仕様"
PLAN_H2_CURRENT_PERMANENCE: str = "恒久化・リファクタリング"
PLAN_H2_CURRENT_HISTORY: str = "変更履歴"
PLAN_H2_CURRENT_VERIFICATION: str = "検証"
PLAN_H2_CURRENT_PROGRESS: str = "進捗ログ"

PLAN_SINGLE_FILE_H2_ORDER: tuple[str, ...] = (
    PLAN_H2_OVERVIEW,
    PLAN_H2_ACTION,
    PLAN_H2_REQUIREMENTS,
    PLAN_H2_CURRENT_PERMANENCE,
    PLAN_H2_CURRENT_HISTORY,
    PLAN_H2_CURRENT_VERIFICATION,
    PLAN_H2_TERMINATION,
    PLAN_H2_CURRENT_PROGRESS,
)
"""現行の1ファイル計画が固定順で持つH2。"""

PLAN_H2_LEGACY_HISTORY: str = "変更履歴"
PLAN_H2_LEGACY_PROGRESS: str = "進捗ログ"
PLAN_H2_LEGACY_AGENT_JUDGMENT: str = "エージェント判断"

PLAN_H2_ALIASES: dict[str, tuple[str, ...]] = {
    PLAN_H2_AGENT_JUDGMENT: (PLAN_H2_AGENT_JUDGMENT, PLAN_H2_LEGACY_AGENT_JUDGMENT),
    PLAN_H2_HISTORY: (PLAN_H2_HISTORY, PLAN_H2_LEGACY_HISTORY),
    PLAN_H2_PROGRESS: (PLAN_H2_PROGRESS, PLAN_H2_LEGACY_PROGRESS),
}
"""新書式の固定H2と、読み取り互換で受理する旧見出しの対応。"""

_PLAN_H2_CANONICAL_BY_ALIAS: dict[str, str] = {
    alias: canonical for canonical, aliases in PLAN_H2_ALIASES.items() for alias in aliases
}

PLAN_MAIN_H2_ORDER: tuple[str, ...] = (
    PLAN_H2_OVERVIEW,
    PLAN_H2_ACTION,
    PLAN_H2_AGENT_JUDGMENT,
    PLAN_H2_HISTORY,
    PLAN_H2_VERIFICATION,
    PLAN_H2_TERMINATION,
    PLAN_H2_PROGRESS,
)
"""新書式の計画ファイル（メイン）が固定順で持つH2。"""

PLAN_TWO_FILE_MAIN_H2_ORDER: tuple[str, ...] = (
    PLAN_H2_OVERVIEW,
    PLAN_H2_ACTION,
    PLAN_H2_AGENT_JUDGMENT,
    PLAN_H2_MATERIALS,
    PLAN_H2_HISTORY,
    PLAN_H2_VERIFICATION,
    PLAN_H2_TERMINATION,
    PLAN_H2_PROGRESS,
)
"""改訂前の二ファイル計画が持つ固定H2順序。読み取り互換専用。"""

PLAN_LEGACY_MAIN_H2_ORDER: tuple[str, ...] = (
    PLAN_H2_OVERVIEW,
    PLAN_H2_ACTION,
    PLAN_H2_MATERIALS,
    PLAN_H2_LEGACY_HISTORY,
    PLAN_H2_VERIFICATION,
    PLAN_H2_TERMINATION,
    PLAN_H2_LEGACY_PROGRESS,
)
"""改訂前の二ファイル計画が持つ固定H2順序。読み取り互換専用。"""

PLAN_DETAIL_H2_ORDER: tuple[str, ...] = (PLAN_H2_PERMANENCE, PLAN_H2_IMPLEMENTATION, PLAN_H2_COMPLETION)
"""新書式の計画ファイル（詳細）が固定順で持つH2（`バグ調査結果`を除く）。"""

PLAN_PERMANENCE_H3: tuple[str, ...] = ("恒久化", "リファクタリング")
"""`## 恒久化・リファクタリング内容`直下に固定順で置くH3。"""

PLAN_LEGACY_PERMANENCE_H3: tuple[str, ...] = ("類似見直し",)
"""廃止済みのH3。既存計画の読み取りでだけ受理し、新規作成では置かない。"""

PLAN_METADATA_H3: str = "計画メタ情報"
PLAN_EXCLUSION_H3: str = "合意済みの除外・保持"
PLAN_IMPLEMENTATION_UNITS_H3: str = "実装単位"
PLAN_ACCEPTANCE_H3: str = "受入シナリオ"
PLAN_ACCEPTANCE_TABLE_HEADER: tuple[str, ...] = (
    "シナリオ",
    "由来",
    "消費主体と入口",
    "操作",
    "期待結果",
    "テスト",
)
"""受入シナリオ表の列構成。第3列は人以外の消費主体も記すため「消費主体と入口」とする。"""
_PLAN_PREVIOUS_ACCEPTANCE_TABLE_HEADER: tuple[str, ...] = (
    *PLAN_ACCEPTANCE_TABLE_HEADER[:2],
    "利用者と入口",
    *PLAN_ACCEPTANCE_TABLE_HEADER[3:],
)
PLAN_LEGACY_ACCEPTANCE_TABLE_HEADERS: tuple[tuple[str, ...], ...] = (
    _PLAN_PREVIOUS_ACCEPTANCE_TABLE_HEADER,
    (*_PLAN_PREVIOUS_ACCEPTANCE_TABLE_HEADER[:-1], "\u691c\u4f53"),
)
"""改名前の受入シナリオ表の列構成。保存済み計画の読み取りでだけ受理し、新規作成・改訂では移行警告を拒否する。

第3列の旧名「利用者と入口」と、さらに古い最終列の旧名を持つ2形式を受理する。
最終列の旧名は、作業ツリーから旧名を一掃した状態を文字列検索で確かめられるようにUnicodeエスケープで保持する。
"""

PLAN_METADATA_FIELDS: tuple[str, ...] = ("起動経路", "対象リポジトリ", "作業種別", "ベースコミット")
"""計画メタ情報の正規形が持つ項目と順序（旧形式単一ファイル・新書式計画ファイル（詳細）は本4項目のみ）。"""

PLAN_METADATA_DETAIL_FIELD: str = "計画ファイル（詳細）"
"""改訂前の二ファイル計画が持つ計画ファイル（詳細）の参照項目。読み取り互換専用。"""

PLAN_METADATA_RELATED_WI_FIELD: str = "関連WI"
"""新書式計画ファイル（メイン）が入力したWIファイル名と要約を持つ項目。"""

PLAN_METADATA_LEGACY_DETAIL_FIELD: str = "実装詳細"
"""読み取り互換で受理する旧形式の計画ファイル（詳細）参照項目。"""

PLAN_METADATA_LEGACY_RELATED_FEEDBACK_FIELD: str = "関連フィードバック"
"""読み取り互換で受理する改名前の`関連WI`項目。"""

PLAN_METADATA_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    PLAN_METADATA_DETAIL_FIELD: (PLAN_METADATA_DETAIL_FIELD, PLAN_METADATA_LEGACY_DETAIL_FIELD),
    PLAN_METADATA_RELATED_WI_FIELD: (PLAN_METADATA_RELATED_WI_FIELD, PLAN_METADATA_LEGACY_RELATED_FEEDBACK_FIELD),
}
"""計画メタ情報の項目名と、読み取り互換で受理する旧名称の対応。"""

_PLAN_METADATA_CANONICAL_BY_FIELD_ALIAS: dict[str, str] = {
    alias: canonical for canonical, aliases in PLAN_METADATA_FIELD_ALIASES.items() for alias in aliases
}

PLAN_METADATA_MAIN_FIELDS: tuple[str, ...] = (
    "起動経路",
    "対象リポジトリ",
    PLAN_METADATA_RELATED_WI_FIELD,
    "作業種別",
    "ベースコミット",
)
"""新書式計画ファイル（メイン）の計画メタ情報が持つ項目と順序。"""

PLAN_METADATA_CURRENT_FIELDS: tuple[str, ...] = (
    "起動経路",
    "対象リポジトリ",
    PLAN_METADATA_RELATED_WI_FIELD,
    "作業種別",
)
"""現行の1ファイル計画が持つ計画メタ情報の固定4項目。"""

PLAN_METADATA_BUG_FIELD: str = "計画ファイル（バグ）"
"""バグ対応の1ファイル計画だけが末尾に持つ計画メタ情報。"""

PLAN_METADATA_TWO_FILE_FIELDS: tuple[str, ...] = (*PLAN_METADATA_FIELDS, PLAN_METADATA_DETAIL_FIELD)
"""改訂前の二ファイル計画が持つ計画メタ情報の項目と順序。読み取り互換専用。"""

PLAN_METADATA_QUOTED_FIELDS: frozenset[str] = frozenset(
    {
        "起動経路",
        "対象リポジトリ",
        "ベースコミット",
        PLAN_METADATA_DETAIL_FIELD,
        PLAN_METADATA_BUG_FIELD,
    }
)
"""値をバッククォートで囲む項目。`関連WI`と`作業種別`は裸で書く。"""

PLAN_WORK_TYPES: tuple[str, ...] = ("バグ対応", "通常変更")

PLAN_METADATA_FALLBACK_H2: tuple[str, ...] = ("目的", "実装契約", "背景")
"""正規配置を持たない既存計画で計画メタ情報を読み取る旧配置。読み取り専用の互換形式として使う。"""

PLAN_HISTORY_USER_EVENT_PREFIX: str = "ユーザー発言"
"""`## 変更履歴（計画時）`でユーザー発言の逐語記録を置くH3見出しの接頭辞。"""
PLAN_HISTORY_USER_EVENT_PATTERN = re.compile(rf"^{PLAN_HISTORY_USER_EVENT_PREFIX}(?P<sequence>[1-9][0-9]*)$")
"""ユーザー発言見出しの書式。接頭辞に1から始まる連番だけを続ける。"""
PLAN_LEGACY_HISTORY_USER_EVENT_PATTERN = re.compile(rf"^{PLAN_HISTORY_USER_EVENT_PREFIX}: .+$")
"""要旨を見出しへ書く旧書式。既存計画の読み取りでだけ受理する。"""

PLAN_HISTORY_TABLE_HEADER: tuple[str, ...] = ("ID", "起点", "指摘内容", "採否・現在の結論", "同期先")
PLAN_HISTORY_ORIGINS: tuple[str, ...] = ("ユーザー発言", "レビュー指摘", "方針転換")
PLAN_HISTORY_REVIEW_ID_PATTERN = re.compile(r"^R(?P<round>[0-9]+)-(?P<track>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)$")
"""レビュー指摘行のID書式。ラウンド番号と系統名を一意に分離できる形に限定する。"""
PLAN_HISTORY_TRACK_VALUES: tuple[str, ...] = ("plan-review", "plan-conformance", "independent")
"""`atk review-table`と共通する新形式の系統名。"""
PLAN_LEGACY_HISTORY_TRACK_VALUES: tuple[str, ...] = ("conformance",)
"""旧形式で既存計画に残る系統名の読み取り互換値。"""
PLAN_LEGACY_HISTORY_REVIEW_ID_PATTERN = re.compile(r"^C-[0-9]{3}$")
"""旧形式の単一ファイルだけで読み取り互換として受理するレビュー指摘行のID書式。"""
PLAN_PROGRESS_TABLE_HEADER: tuple[str, ...] = ("日時", "完了した工程", "結果・特記事項")
PLAN_EXCLUSION_TABLE_HEADER: tuple[str, ...] = ("合意内容", "対象と箇所", "素材・要求参照", "確認方法")
PLAN_LEGACY_EXCLUSION_TABLE_HEADER: tuple[str, ...] = ("合意内容", "対象と箇所", "原文参照", "確認方法")
PLAN_ACTION_TABLE_HEADER: tuple[str, ...] = ("実施内容", "採否", "ユーザー指示との関係", "根拠")
PLAN_LEGACY_ACTION_TABLE_HEADER: tuple[str, ...] = ("実施内容", "ユーザー指示との関係", "根拠")
PLAN_HUMAN_ACTION_TABLE_HEADER: tuple[str, ...] = ("実施内容", "由来", "採否", "根拠")
PLAN_HUMAN_JUDGMENT_TABLE_HEADER: tuple[str, ...] = ("実施内容", "観測事象", "ユーザー要求との関係", "具体化した内容", "根拠")
PLAN_HUMAN_WI_ORIGIN: str = "人間由来のWI"
"""WI本文の`source`と機械判定できる明示由来から確定する由来の区分。"""

PLAN_AGENT_WI_ORIGIN: str = "エージェント由来のWI"
"""WI本文が機械判定できる明示由来を持たない場合の由来の区分。"""

PLAN_LEGACY_HUMAN_FEEDBACK_ORIGIN: str = "人間由来のフィードバック"
"""読み取り互換で受理する改名前の人間由来の区分。"""

PLAN_LEGACY_AGENT_FEEDBACK_ORIGIN: str = "エージェント由来のフィードバック"
"""読み取り互換で受理する改名前のエージェント由来の区分。"""

PLAN_WI_ORIGIN_ALIASES: dict[str, tuple[str, ...]] = {
    PLAN_HUMAN_WI_ORIGIN: (PLAN_HUMAN_WI_ORIGIN, PLAN_LEGACY_HUMAN_FEEDBACK_ORIGIN),
    PLAN_AGENT_WI_ORIGIN: (PLAN_AGENT_WI_ORIGIN, PLAN_LEGACY_AGENT_FEEDBACK_ORIGIN),
}
"""WI由来の`由来`欄が持つ区分と、読み取り互換で受理する旧名称の対応。"""

_PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS: dict[str, str] = {
    alias: canonical for canonical, aliases in PLAN_WI_ORIGIN_ALIASES.items() for alias in aliases
}

PLAN_USER_INSTRUCTION_ORIGIN: str = "ユーザー指示"
"""ユーザー自身の発話から確定した実施内容の由来の区分。"""

PLAN_HUMAN_ORIGINS: tuple[str, ...] = (
    PLAN_HUMAN_WI_ORIGIN,
    PLAN_AGENT_WI_ORIGIN,
    PLAN_USER_INSTRUCTION_ORIGIN,
    "エージェント提案",
)
PLAN_HUMAN_REVIEW_ORIGIN_PATTERN = re.compile(r"^計画レビュー第(?P<round>[1-9][0-9]*)ラウンド$")
PLAN_WI_ORIGIN_PATTERN = re.compile(
    "(?P<kind>" + "|".join(re.escape(alias) for alias in _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS) + ") "
    r"\((?P<name>[^/\\()\s]+\.md)\)(?P<note> \[対話由来\])?"
)
"""WI由来の`由来`欄。機械判定できない明示由来には`[対話由来]`注記を付ける。"""

PLAN_WI_SOURCE_KEY: str = "source"
"""WIファイルのfrontmatterで投入元スキルを表すキー。"""

PLAN_WI_USER_COMMENT_HEADING: str = "ユーザーコメント"
"""WIファイルの末尾に置く、ユーザー専用の記入欄の見出し。"""

PLAN_WI_ANSWER_HEADING: str = "回答"
"""UWIファイルでユーザーの回答を記録する見出し。"""

PLAN_WI_MACHINE_DETECTABLE_ORIGIN_HEADINGS: dict[str, bool] = {
    PLAN_WI_USER_COMMENT_HEADING: True,
    PLAN_WI_ANSWER_HEADING: False,
}
"""計画構造の判定が`source`を持つWIの本文から人間由来を読み取るH2見出しと、本文末尾のH2に限るかの対応。

計画書式の`[対話由来]`注記の条文はこの集合を列挙し、`structure_contract_invariant_test.py`が条文と集合の一致を確かめる。
"""

PLAN_WI_SCOPE_HEADING: str = "適用範囲"
"""通常AWIファイルで誤りの機構が依存する条件を記録する見出し。"""

_FRONTMATTER_DELIMITER: str = "---"
_FRONTMATTER_SOURCE_PATTERN = re.compile(rf"^{PLAN_WI_SOURCE_KEY}:[ \t]*\S")
PLAN_HUMAN_REVIEW_ROOT_PATTERN = re.compile(r"^(?P<path>\S.*?\.tsv)のround (?P<round>[1-9][0-9]*)(?:。(?P<reason>.+))?$")


def plan_human_review_path_is_absolute(path: str) -> bool:
    """POSIXまたはWindowsの純粋パスとして絶対パスである場合に真を返す。"""
    return pathlib.PurePosixPath(path).is_absolute() or pathlib.PureWindowsPath(path).is_absolute()


PLAN_ACTION_DECISIONS: tuple[str, ...] = ("採用", "部分採用", "不採用", "充足済み", "保留", "対象外", "移管")
"""計画ファイル（メイン）の実施内容表が受理する採否値。"""
PLAN_ACTION_NON_ADOPTED_DECISIONS: tuple[str, ...] = ("不採用", "充足済み", "保留", "対象外", "移管")
"""実装単位を持たない採否値。根拠へ理由の記載を要求し、`ユーザー指示との関係`へ`非該当`を許容する。"""
PLAN_ACTION_RELATIONS: tuple[str, ...] = ("指示どおり", "具体化", "エージェント追加")
PLAN_BUG_FILE_REFERENCE_PREFIX: str = "- 計画ファイル（バグ）:"
PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX: str = "- バグ調査ファイル:"

PLAN_IMPLEMENTATION_UNITS_TABLE_HEADER: tuple[str, ...] = (
    "単位ID",
    "目的",
    "先行依存",
    "統合順",
    "変更範囲の検証",
)
PLAN_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER: tuple[str, ...] = (
    "実装単位",
    "目的",
    "先行依存",
    "統合順",
    "変更範囲の検証",
)
PLAN_LEGACY_CURRENT_IMPLEMENTATION_UNITS_TABLE_HEADER: tuple[str, ...] = (
    "単位ID",
    "目的",
    "先行依存",
    "統合順",
    "近接検証",
)
PLAN_LEGACY_CURRENT_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER: tuple[str, ...] = (
    "実装単位",
    "目的",
    "先行依存",
    "統合順",
    "近接検証",
)
PLAN_LEGACY_IMPLEMENTATION_UNITS_TABLE_HEADER: tuple[str, ...] = (
    "単位ID",
    "目的",
    "対象の実施内容",
    "先行依存",
    "統合順",
    "近接検証",
)
PLAN_IMPLEMENTATION_UNIT_ID_PATTERN = re.compile(r"^U-[0-9]{3}$")
"""計画ファイル（詳細）の実装単位表が持つ固定列と単位ID書式。"""

PLAN_VERIFICATION_TABLE_HEADER: tuple[str, ...] = ("区分", "検証コマンド")
PLAN_VERIFICATION_TABLE_ROWS: tuple[str, ...] = ("レーン内検証", "統合後検証")
"""`## 検証区分`が持つ固定2行2列表。行は`レーン内検証`・`統合後検証`の順で固定する。"""

PLAN_CURRENT_VERIFICATION_TABLE_ROWS: tuple[str, ...] = ("変更範囲の検証",)
"""現行の`## 検証`が持つ固定行。"""
PLAN_LEGACY_CURRENT_SINGLE_VERIFICATION_TABLE_ROWS: tuple[str, ...] = ("近接検証",)
"""進行中の既存計画を読む場合だけ受理する旧名の1行表。"""
PLAN_LEGACY_CURRENT_VERIFICATION_TABLE_ROWS: tuple[str, ...] = ("近接検証", "全体検証")
"""進行中の既存計画を読む場合だけ受理する行。"""

PLAN_MATERIAL_TABLE_HEADER: tuple[str, ...] = ("素材ID", "種別", "キューID", "投入元", "引用範囲")
PLAN_REQUIREMENT_TABLE_HEADER: tuple[str, ...] = (
    "要求ID",
    "素材参照",
    "実装に必要な要件",
    "採否",
    "採用範囲",
    "除外範囲",
    "根拠",
)
PLAN_MATERIAL_TYPES: tuple[str, ...] = (
    "フィードバック",
    "ユーザー指示",
    "ユーザー合意",
    # 改称前の種別名。保存済み計画の読み取り互換に限り受理する。
    "利用者指示",
    "利用者合意",
    "参考素材",
    "処理対象資料",
    "起動事実",
)
"""新規表記と既存計画の読み取り互換表記を含む提示素材の種別。"""
PLAN_NON_QUEUE_VALUE: str = "非該当"
PLAN_MATERIAL_ID_PATTERN = re.compile(r"^P-[0-9A-Za-z][0-9A-Za-z_-]*$")
PLAN_REQUIREMENT_ID_PATTERN = re.compile(r"^R-(?P<material>P-[0-9A-Za-z][0-9A-Za-z_-]*)-(?P<sequence>[0-9]{3})$")
PLAN_QUEUE_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9]{3,}\.md$")

PLAN_BUG_TABLE_HEADER: tuple[str, ...] = ("項目", "内容")
PLAN_BUG_CAUSE_TABLE_HEADER: tuple[str, ...] = ("要因系統", "L1 現象", "L2 判断", "L3 構造", "L4 システム")
PLAN_BUG_CAUSE_TABLE_ROWS: tuple[str, ...] = ("作り込み要因", "見逃し要因")
"""バグ単位の原因分析表の固定5列2行。`agent-toolkit:bugfix`の原因分析の段階と対応させる。"""

PLAN_BUG_TABLE_ROWS: tuple[str, ...] = (
    "現象",
    "期待する契約",
    "直接的原因",
    "原因分析の根拠",
    "対策",
    "類似見直し観点",
    "類似見直し結果",
    "再発防止策",
)
"""バグ調査表の固定行。行名と順序を`agent-toolkit:bugfix`の`references/analysis-records.md`「原因分析の記録先」と対応させる。

根本原因は原因分析表の到達した最深段のセルへ、原因分析の品質確認は原因分析の工程へ、
設計意図の記録は`再発防止策`の記載内容へ統合したため、いずれも独立した行を持たない。
"""

PLAN_LEGACY_BUG_TABLE_ROWS: tuple[str, ...] = (
    "観測事象",
    "期待する契約",
    "直接的原因",
    "根本原因",
    "原因分析の根拠",
    "原因分析の品質確認",
    "類似見直しの観点",
    "類似見直し結果",
    "是正処置",
    "横展開処置",
    "再発防止処置",
    "設計意図の記録",
)
"""統廃合前のバグ調査表の固定行。原因分析表を伴う既存計画の読み取り互換にだけ用いる。"""

PLAN_LEGACY_STANDALONE_BUG_TABLE_ROWS: tuple[str, ...] = (
    "観測事象",
    "期待する契約",
    "直接的原因",
    "混入要因",
    "動機的要因",
    "見逃し原因",
    "根本原因",
    "原因分析の根拠",
    "類似見直しの観点",
    "類似見直し結果",
    "是正処置",
    "横展開処置",
    "再発防止処置",
    "設計意図の記録",
)
"""原因分析表を持たない旧バグ調査表の固定行。既存計画の読み取り互換にだけ用いる。"""

PLAN_PERMANENCE_TABLE_HEADER: tuple[str, ...] = ("知見", "出所", "反映先", "根拠")
"""通常変更の恒久化表の固定4列。バグ対応ではバグ調査表を使う。"""

PLAN_REFACTORING_TABLE_HEADER: tuple[str, ...] = ("対象", "現状の問題", "対応")
"""`### リファクタリング`が持つ現行3列。対象が無い場合は表を置かず地の文とする。"""

PLAN_LEGACY_REFACTORING_TABLE_ROWS: tuple[str, ...] = ("対象", "現状の問題", "対応", "本計画に含めるか")
"""旧2列リファクタリング表の固定4行。既存計画の読み取り互換にだけ用いる。"""

PLAN_PLACEHOLDER_WORDS: frozenset[str] = frozenset({"なし", "不要", "該当なし", "特になし"})
"""検討結果として成立しない結論語。これだけの記載は検討の省略として拒否する。"""

PLUGIN_MANIFEST_PATH: str = "agent-toolkit/.claude-plugin/plugin.json"
"""`scripts/agent_toolkit_bump.py`が更新するagent-toolkitプラグインmanifestの相対パス。"""

MARKETPLACE_MANIFEST_PATH: str = ".claude-plugin/marketplace.json"
"""`scripts/agent_toolkit_bump.py`が更新するmarketplace manifestの相対パス。"""

BUMP_MANIFEST_PATHS: frozenset[str] = frozenset({PLUGIN_MANIFEST_PATH, MARKETPLACE_MANIFEST_PATH})
