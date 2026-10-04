"""pickerの選定結果（`pick-wi.subagent.md`の`## 出力`が定めるYAML）の欄名を新旧の両形式で読む。

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
EXCLUDED_PATHS_KEY = "書き込まない反映先"
LANE_COSTS_KEY = "レーンの所要時間"
RATIONALE_KEY = "根拠"
MODEL_TYPES_KEY = "担当モデル"
STAGE_KEY = "段階"
PRIOR_LANES_KEY = "先行レーン"

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
    return [_normalize(item, _LEGACY_DECISION_KEYS) for item in items]


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
