"""選定結果の既知欄と型を公開入口で確認する。本文とレーンの関係は呼出側で判定する。"""

from __future__ import annotations

import re
import typing

from agent_toolkit._plan import selection as _selection

_LANE_NONE = "なし"
_STALENESS_KEY = "鮮度"
_UPSTREAM_TARGETS_KEY = "上流投入先"
_IMPLEMENTATION_SECONDS_KEY = "実装秒数"
_INTEGRATION_SECONDS_KEY = "統合秒数"
_BLOCKERS_KEY = "続行できない理由"
_DECISION_STRING_KEYS = ("再開位置", "プロジェクト固有の公開後の操作の順序", "プロジェクト規範の指定", "上流投入", "上流要求")
_DECISION_KEYS = frozenset(
    {
        _selection.WI_KEY,
        _selection.LANE_KEY,
        _STALENESS_KEY,
        _selection.WRITE_FILES_KEY,
        _selection.PUBLIC_WRITE_FILES_KEY,
        _selection.MODEL_TYPES_KEY,
        _selection.EXCLUDED_PATHS_KEY,
        _selection.DERIVED_NEW_PATHS_KEY,
        _UPSTREAM_TARGETS_KEY,
        *_DECISION_STRING_KEYS,
    }
)
_DECISION_REQUIRED_KEYS = (_selection.WI_KEY, _selection.LANE_KEY, _STALENESS_KEY, _selection.WRITE_FILES_KEY)
# 旧形式の選定結果は秒数と先行レーンを英字の欄名で持つ。`_selection.lane_costs`が新しい欄名へそろえない欄名も、
# 版の異なるpickerが書いた選定結果を未知の欄として拒否しないよう既知の欄に含める。
_LEGACY_SECONDS_KEYS = {"implementation_seconds": _IMPLEMENTATION_SECONDS_KEY, "integration_seconds": _INTEGRATION_SECONDS_KEY}
_LANE_COST_KEYS = frozenset(
    {
        _selection.LANE_KEY,
        _selection.STAGE_KEY,
        _selection.PRIOR_LANES_KEY,
        _IMPLEMENTATION_SECONDS_KEY,
        _INTEGRATION_SECONDS_KEY,
        _selection.RATIONALE_KEY,
        "after_lanes",
        *_LEGACY_SECONDS_KEYS,
    }
)
_TOP_LEVEL_KEYS = frozenset(
    {
        _selection.DECISIONS_KEY,
        "decisions",
        _selection.LANE_COSTS_KEY,
        "lane_costs",
        _BLOCKERS_KEY,
        _selection.LANE_OVERLAPS_KEY,
        _selection.SINGLE_STAGE_ESTIMATE_KEY,
    }
)
_MODEL_ROLES = ("実装担当", "実行レビュー担当")
_MODEL_TYPE_RE = re.compile(r"(?:claude|codex|agy):[^,/\s]+/[^,/\s]+")


def structure_errors(selection: object) -> tuple[list[str], list[str]]:
    """選定結果の構造の誤りを、`担当モデル`以外の誤りと`担当モデル`の誤りに分けて返す。

    1回の実行で全ての誤りを返し、直すたびに次の誤りが現れる往復を避ける。
    """
    if not isinstance(selection, dict):
        return ["最上位が写像ではない"], []
    errors = [f"未知の欄: {key}" for key in selection if key not in _TOP_LEVEL_KEYS]
    comparison = _selection.SINGLE_STAGE_ESTIMATE_KEY
    if comparison in selection and not is_non_negative_number(selection[comparison]):
        errors.append(f"`{comparison}`の型はbool以外の0以上の数値とする: {selection[comparison]!r}")
    model_errors: list[str] = []
    items = _selection.decisions(selection)
    if items is None:
        errors.append(f"`{_selection.DECISIONS_KEY}`の列がない")
        items = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"`{_selection.DECISIONS_KEY}`の{index}件目が写像ではない")
            continue
        label = item.get(_selection.WI_KEY) if isinstance(item.get(_selection.WI_KEY), str) else f"{index}件目"
        errors.extend(_derived_structure_errors(str(label), item.get(_selection.DERIVED_NEW_PATHS_KEY, [])))
        errors.extend(f"{label}: 未知の欄: {key}" for key in item if key not in _DECISION_KEYS)
        errors.extend(f"{label}: 必須の欄がない: {key}" for key in _DECISION_REQUIRED_KEYS if key not in item)
        errors.extend(
            f"{label}: `{key}`が文字列ではない: {item[key]!r}"
            for key in (_selection.WI_KEY, _selection.LANE_KEY, *_DECISION_STRING_KEYS)
            if key in item and not isinstance(item[key], str)
        )
        if _STALENESS_KEY in item and not isinstance(item[_STALENESS_KEY], dict):
            errors.append(f"{label}: `{_STALENESS_KEY}`が写像ではない: {item[_STALENESS_KEY]!r}")
        errors.extend(
            f"{label}: `{key}`が文字列の列ではない: {item[key]!r}"
            for key in (
                _selection.WRITE_FILES_KEY,
                _selection.PUBLIC_WRITE_FILES_KEY,
                _selection.EXCLUDED_PATHS_KEY,
            )
            if key in item and not is_string_list(item[key])
        )
        upstream_targets = item.get(_UPSTREAM_TARGETS_KEY, _LANE_NONE)
        if upstream_targets != _LANE_NONE and not is_string_list(upstream_targets):
            errors.append(f"{label}: `{_UPSTREAM_TARGETS_KEY}`が文字列の列ではない: {upstream_targets!r}")
        model_types = item.get(_selection.MODEL_TYPES_KEY, {})
        if not isinstance(model_types, dict) or any(
            role not in _MODEL_ROLES or not isinstance(value, str) or not _MODEL_TYPE_RE.fullmatch(value)
            for role, value in model_types.items()
        ):
            model_errors.append(
                f"{label}: `{_selection.MODEL_TYPES_KEY}`が担当別のengine:model/effortではない: {model_types!r}"
            )
    costs = _selection.lane_costs(selection)
    if costs is None:
        errors.append(f"`{_selection.LANE_COSTS_KEY}`の列がない")
        costs = []
    for index, row in enumerate(costs, start=1):
        if not isinstance(row, dict):
            errors.append(f"`{_selection.LANE_COSTS_KEY}`の{index}件目が写像ではない")
            continue
        label = row.get(_selection.LANE_KEY) if isinstance(row.get(_selection.LANE_KEY), str) else f"{index}件目"
        errors.extend(f"{label}: 未知の欄: {key}" for key in row if key not in _LANE_COST_KEYS)
        if not isinstance(row.get(_selection.LANE_KEY), str):
            errors.append(f"{label}: `{_selection.LANE_KEY}`が文字列ではない")
        stage = row.get(_selection.STAGE_KEY, 1)
        if not isinstance(stage, int) or isinstance(stage, bool) or stage < 1:
            errors.append(f"{label}: `{_selection.STAGE_KEY}`が1以上の整数ではない: {stage!r}")
        prior = row.get(_selection.PRIOR_LANES_KEY, [])
        if not is_string_list(prior) or len(prior) != len(set(prior)):
            errors.append(f"{label}: `{_selection.PRIOR_LANES_KEY}`が重複のない文字列の列ではない: {prior!r}")
        for key in (_IMPLEMENTATION_SECONDS_KEY, _INTEGRATION_SECONDS_KEY):
            legacy = next(name for name, current in _LEGACY_SECONDS_KEYS.items() if current == key)
            value = row.get(key, row.get(legacy))
            if not is_non_negative_number(value):
                errors.append(f"{label}: `{key}`が0以上の数値ではない: {value!r}")
        rationale = row.get(_selection.RATIONALE_KEY)
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"{label}: `{_selection.RATIONALE_KEY}`が空でない文字列ではない: {rationale!r}")
    blockers = selection.get(_BLOCKERS_KEY, [])
    if not is_string_list(blockers):
        errors.append(f"`{_BLOCKERS_KEY}`が文字列の列ではない: {blockers!r}")
    errors.extend(_overlap_structure_errors(selection.get(_selection.LANE_OVERLAPS_KEY, [])))
    return errors, model_errors


def _derived_structure_errors(label: str, records: object) -> list[str]:
    """導出記録は要求と配置根拠を失わない4欄の列として受け取る。"""
    field = _selection.DERIVED_NEW_PATHS_KEY
    if not isinstance(records, list):
        return [f"{label}: `{field}`が列ではない"]
    keys = {"パス", "反映範囲", "要求", "配置根拠"}
    errors: list[str] = []
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict) or set(record) != keys:
            errors.append(f"{label}: `{field}`の{index}件目の欄を{', '.join(sorted(keys))}へそろえる")
        elif any(not isinstance(value, str) or not value.strip() for value in record.values()):
            errors.append(f"{label}: `{field}`の{index}件目の全値を空でない文字列にする")
    return errors


def cost_update_errors(value: object, destinations: set[str]) -> list[str]:
    """最終レーンへ渡す費用更新だけを受理する。省略は呼出側で判定する。"""
    if not isinstance(value, dict):
        return ["費用更新JSONは最終レーンから3欄への写像とする"]
    errors: list[str] = []
    keys = {_IMPLEMENTATION_SECONDS_KEY, _INTEGRATION_SECONDS_KEY, _selection.RATIONALE_KEY}
    for lane, row in value.items():
        if lane not in destinations:
            errors.append(f"費用更新の対象外レーン: {lane}")
        if not isinstance(row, dict) or set(row) != keys:
            errors.append(f"{lane}: 費用更新の欄を{', '.join(sorted(keys))}へそろえる")
            continue
        for key in (_IMPLEMENTATION_SECONDS_KEY, _INTEGRATION_SECONDS_KEY):
            if not is_non_negative_number(row[key]):
                errors.append(f"{lane}: `{key}`をbool以外の0以上の数値にする")
        rationale = row[_selection.RATIONALE_KEY]
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"{lane}: `{_selection.RATIONALE_KEY}`を空でない文字列にする")
    return errors


def _overlap_structure_errors(records: object) -> list[str]:
    """重なりの各記録が所定の欄と型を持つか確かめる。空列の記録欠落は共有パスの処理で判定する。"""
    if not isinstance(records, list):
        return [f"`{_selection.LANE_OVERLAPS_KEY}`が列ではない"]
    errors: list[str] = []
    keys = {"レーン1", "レーン2", "共通パス", "レーン1の定義", "レーン2の定義", "判定"}
    for index, record in enumerate(records, 1):
        label = f"{_selection.LANE_OVERLAPS_KEY}の{index}件目"
        if not isinstance(record, dict) or set(record) != keys:
            errors.append(f"{label}: 欄を{', '.join(sorted(keys))}へそろえる")
            continue
        for key in ("レーン1", "レーン2", "共通パス"):
            if not isinstance(record[key], str) or not record[key].strip():
                errors.append(f"{label}: {key}が空でない文字列ではない")
        for key in ("レーン1の定義", "レーン2の定義"):
            value = record[key]
            if not is_string_list(value) or not value or any(not item.strip() for item in value):
                errors.append(f"{label}: {key}が空でない定義の列ではない")
        if record["判定"] not in ("交わる", "交わらない"):
            errors.append(f"{label}: 判定は交わるか交わらないを指定する")
    return errors


def is_string_list(value: object) -> typing.TypeGuard[list[str]]:
    """値が文字列だけの列かを返す。"""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def is_non_negative_number(value: object) -> bool:
    """値が真偽値以外の0以上の数値かを返す。"""
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
