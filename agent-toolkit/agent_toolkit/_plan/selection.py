"""メインの選定結果（`selection-format.md`が定めるYAML）の欄名を新旧の両形式で読む。

選定結果の欄名は日本語名へ改めたが、委譲先が読む`<役割名>.subagent.md`と、選定結果を読むスクリプトの版は一致しない場合がある。
旧形式の英字の欄名で書かれた選定結果も同じ意味で読めるよう、読む側は本モジュールで新しい欄名へそろえてから扱う。
"""

from __future__ import annotations

import typing

DECISIONS_KEY = "選定"
"""選定した項目の列を持つ最上位の欄名。"""

WI_KEY = "WI"
LANE_KEY = "レーン"
WRITE_FILES_KEY = "書込対象"
PUBLIC_WRITE_FILES_KEY = "公開工程の書込対象"
EXCLUDED_PATHS_KEY = "書き込まない反映先"
LANE_COSTS_KEY = "レーンの所要時間"
RATIONALE_KEY = "根拠"
MODEL_TYPES_KEY = "担当モデル"
STAGE_KEY = "段階"
PRIOR_LANES_KEY = "先行レーン"
LANE_OVERLAPS_KEY = "レーン間の重なり"
SINGLE_STAGE_ESTIMATE_KEY = "単一段階案の完了見込み秒数"
STAGED_ESTIMATE_KEY = "段階案の完了見込み秒数"
NO_STAGED_PLAN = "成立する段階案なし"
PUBLIC_WRITE_RATIONALES_KEY = "公開工程の規範根拠"
WRITE_SCOPE_REASONS_KEY = "書込範囲の理由"
ESTIMATE_CORRECTION_KEY = "見込みの補正"
IMPLEMENTATION_COEFFICIENT_KEY = "実装係数"
INTEGRATION_COEFFICIENT_KEY = "統合係数"
SHARED_WAIT_SECONDS_KEY = "共有待ち秒数"
SHARED_WAIT_BASIS_KEY = "共有待ちの算定"
SHARED_WAIT_MEASURED = "実測"
SHARED_WAIT_ESTIMATED = "推定"
TARGET_OVERRUN_COMPARISON_KEY = "目標超過時の配分比較"
UNCORRECTED_IMPLEMENTATION_KEY = "補正前の実装秒数"
UNCORRECTED_INTEGRATION_KEY = "補正前の統合秒数"
DERIVED_NEW_PATHS_KEY = "導出した新設先"
INTEGRATION_STATE_KEY = "統合状態"
NOT_INTEGRATED = "未統合"
INTEGRATED = "統合済み"
INITIAL_ALLOCATION_KEY = "初回配分"
ADDED_WIS_KEY = "追加WI"


def correction_coefficients(correction: dict[str, object]) -> tuple[float, float]:
    """型検査済みの工程別係数を返す。旧形式の未分離係数は両工程へ適用する。"""
    if "係数" in correction:
        coefficient = typing.cast(float, correction["係数"])
        return coefficient, coefficient
    return (
        typing.cast(float, correction[IMPLEMENTATION_COEFFICIENT_KEY]),
        typing.cast(float, correction[INTEGRATION_COEFFICIENT_KEY]),
    )


def integrated_lanes(selection: object) -> set[str]:
    """統合済みとして記録されたレーンを返す。省略した状態は未統合である。"""
    return {
        str(row[LANE_KEY])
        for row in lane_costs(selection) or []
        if isinstance(row, dict) and row.get(INTEGRATION_STATE_KEY) == INTEGRATED
    }


def pending_lanes(selection: dict[str, object]) -> list[dict[str, object]]:
    """選定全体の所属と状態の整合性を検査し、公開前に判断する未統合集合を返す。"""
    items = typing.cast(list[dict[str, object]], decisions(selection))
    costs = typing.cast(list[dict[str, object]], lane_costs(selection))
    names = [str(item[WI_KEY]) for item in items]
    lanes = [str(row[LANE_KEY]) for row in costs]
    assigned = {str(item[LANE_KEY]) for item in items} - {"なし"}
    if len(names) != len(set(names)) or len(lanes) != len(set(lanes)) or assigned != set(lanes):
        raise ValueError("選定のWI・レーンが重複しているか、レーンの所要時間との対応が一致しない")
    pending = []
    for row in costs:
        members = [item for item in items if item[LANE_KEY] == row[LANE_KEY]]
        if any(
            prior not in assigned or prior == row[LANE_KEY] for prior in typing.cast(list[str], row.get(PRIOR_LANES_KEY, []))
        ):
            raise ValueError(f"{row[LANE_KEY]}: 先行レーンが存在しないか自身を指している")
        if row.get(INTEGRATION_STATE_KEY, NOT_INTEGRATED) != INTEGRATED or any(
            item.get(INTEGRATION_STATE_KEY) == NOT_INTEGRATED for item in members
        ):
            pending.append(
                {
                    LANE_KEY: row[LANE_KEY],
                    STAGE_KEY: row.get(STAGE_KEY, 1),
                    WI_KEY: [item[WI_KEY] for item in members],
                    PRIOR_LANES_KEY: row.get(PRIOR_LANES_KEY, []),
                }
            )
    return pending


_LEGACY_DECISIONS_KEY = "decisions"

# 旧欄名から新しい欄名への対応。項目の欄だけを対象とし、値は変えない。
_LEGACY_DECISION_KEYS: typing.Final[dict[str, str]] = {
    "awi": WI_KEY,
    "lane": LANE_KEY,
    "staleness": "鮮度",
    "write_files": WRITE_FILES_KEY,
    "excluded_paths": EXCLUDED_PATHS_KEY,
    "terminal_order": "プロジェクト固有の公開後の操作の順序",
    "project_notes": "プロジェクト規範の指定",
    "upstream_submission": "上流投入",
    "upstream_target_repo": "上流投入先",
    "upstream_request": "上流要求",
    "model_types": MODEL_TYPES_KEY,
}


def decisions(selection: object) -> list[object] | None:
    """選定結果の項目の列を新しい欄名へそろえて返す。

    最上位が写像で、`選定`か旧欄名`decisions`の値が列の場合だけ列を返し、それ以外は`None`を返す。
    写像である項目は旧欄名を新しい欄名へ置き換えた複製を返し、写像でない項目はそのまま返す。
    同じ項目に新旧の欄名が並ぶ場合は新しい欄名の値を採用する。
    """
    if not isinstance(selection, dict):
        return None
    items = selection.get(DECISIONS_KEY, selection.get(_LEGACY_DECISIONS_KEY))
    if not isinstance(items, list):
        return None
    normalized = [_normalize(item, _LEGACY_DECISION_KEYS) for item in items]
    for item in normalized:
        if isinstance(item, dict):
            item.setdefault(PUBLIC_WRITE_FILES_KEY, [])
    return normalized


def lane_costs(selection: object) -> list[object] | None:
    """レーンの所要時間の列を読み、旧lane_costs・lane・rationaleを現在の欄名へそろえる。"""
    if not isinstance(selection, dict):
        return None
    items = selection.get(LANE_COSTS_KEY, selection.get("lane_costs"))
    if not isinstance(items, list):
        return None
    return [
        _normalize(item, {"lane": LANE_KEY, "rationale": RATIONALE_KEY, "stage": STAGE_KEY, "prior_lanes": PRIOR_LANES_KEY})
        for item in items
    ]


def _normalize(item: object, keys: dict[str, str]) -> object:
    """1件の項目の旧欄名を新しい欄名へ置き換える。"""
    if not isinstance(item, dict):
        return item
    normalized = {key: value for key, value in item.items() if key not in keys}
    for legacy, current in keys.items():
        if legacy in item and current not in item:
            normalized[current] = item[legacy]
    return normalized
