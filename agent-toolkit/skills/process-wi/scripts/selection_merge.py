"""型を確認済みの選定結果を、メインが確定したレーン対応で合流する。"""

from __future__ import annotations

import copy
import typing

import selection_contract

from agent_toolkit._plan import selection as _selection

_Rows = list[dict[str, object]]
_SECONDS = {"implementation_seconds": "実装秒数", "integration_seconds": "統合秒数"}


def _rows(data: dict[str, object], *, costs: bool = False) -> _Rows:
    return typing.cast(_Rows, _selection.lane_costs(data) if costs else _selection.decisions(data))


def _costs(data: dict[str, object]) -> _Rows:
    rows = _rows(data, costs=True)
    lanes = {str(item[_selection.LANE_KEY]) for item in _rows(data)} - {"なし"}
    names = [str(row[_selection.LANE_KEY]) for row in rows]
    if len(names) != len(set(names)) or set(names) != lanes:
        raise ValueError("所要時間は対象レーンごとに1件を指定する")
    for row in rows:
        for legacy, current in _SECONDS.items():
            if current not in row:
                row[current] = row[legacy]
            row.pop(legacy, None)
        if "after_lanes" in row:
            row.setdefault(_selection.PRIOR_LANES_KEY, row.pop("after_lanes"))
    return rows


def merge_selection(
    existing: dict[str, object], added: dict[str, object], lane_map: object, cost_updates: object = None
) -> dict[str, object]:
    """合流結果を返す。変換不足や不整合は保存前に拒否し、元の入力を変更しない。"""
    existing, added = copy.deepcopy(existing), copy.deepcopy(added)
    first, second = _rows(existing), _rows(added)
    names = [str(item[_selection.WI_KEY]) for item in [*first, *second]]
    if len(names) != len(set(names)):
        raise ValueError("同一WIを二重に追加できない")
    if any(
        reason != "なし" for data in (existing, added) for reason in typing.cast(list[str], data.get("続行できない理由", []))
    ):
        raise ValueError("続行できない理由を解消してから統合する")
    lanes = {str(item[_selection.LANE_KEY]) for item in second} - {"なし"}
    if (
        not isinstance(lane_map, dict)
        or set(lane_map) != lanes
        or any(not isinstance(value, str) or not value.strip() or value == "なし" for value in lane_map.values())
    ):
        raise ValueError("レーン対応は追加側の全対象レーンだけをキー、最終レーンを空でない文字列の値とする")
    mapping = typing.cast(dict[str, str], lane_map)
    if cost_updates is not None:
        errors = selection_contract.cost_update_errors(cost_updates, set(mapping.values()))
        if errors:
            raise ValueError("\n".join(errors))

    def destination(lane: str) -> str:
        return mapping.get(lane, lane)

    for item in second:
        item[_selection.LANE_KEY] = destination(str(item[_selection.LANE_KEY]))
    # 元の追加結果で所要時間とレーンの対応を確認する。
    added_costs = _costs(added)
    costs = {str(row[_selection.LANE_KEY]): row for row in _costs(existing)}
    for row in added_costs:
        row[_selection.INTEGRATION_STATE_KEY] = _selection.NOT_INTEGRATED
        lane = destination(str(row[_selection.LANE_KEY]))
        row[_selection.LANE_KEY] = lane
        row[_selection.PRIOR_LANES_KEY] = list(
            dict.fromkeys(destination(value) for value in typing.cast(list[str], row.get(_selection.PRIOR_LANES_KEY, [])))
        )
        prior = costs.get(lane)
        if prior is None:
            costs[lane] = row
            continue
        prior[_selection.INTEGRATION_STATE_KEY] = _selection.NOT_INTEGRATED
        if prior.get(_selection.STAGE_KEY, 1) != row.get(_selection.STAGE_KEY, 1):
            raise ValueError(f"{lane}: 合流するレーンの段階が異なる。最終割当に合う入力へ直す")
        for key in _SECONDS.values():
            prior[key] = typing.cast(float, prior[key]) + typing.cast(float, row[key])
        prior[_selection.RATIONALE_KEY] = f"{prior[_selection.RATIONALE_KEY]}\n{row[_selection.RATIONALE_KEY]}"
        prior[_selection.PRIOR_LANES_KEY] = list(
            dict.fromkeys(
                [
                    *typing.cast(list[str], prior.get(_selection.PRIOR_LANES_KEY, [])),
                    *typing.cast(list[str], row[_selection.PRIOR_LANES_KEY]),
                ]
            )
        )
    for lane, update in typing.cast(dict[str, dict[str, object]], cost_updates or {}).items():
        costs[lane].update(update)
    overlaps: dict[tuple[str, str, str], dict[str, object]] = {}
    for data, convert in ((existing, False), (added, True)):
        for record in typing.cast(_Rows, data.get(_selection.LANE_OVERLAPS_KEY, [])):
            left, right = str(record["レーン1"]), str(record["レーン2"])
            if convert:
                left, right = destination(left), destination(right)
            if left == right:
                continue
            definitions = (record["レーン1の定義"], record["レーン2の定義"])
            if right < left:
                left, right = right, left
                definitions = definitions[::-1]
            key = (left, right, str(record["共通パス"]))
            if key not in overlaps:
                overlaps[key] = {
                    "レーン1": left,
                    "レーン2": right,
                    "共通パス": record["共通パス"],
                    "レーン1の定義": [],
                    "レーン2の定義": [],
                    "判定": record["判定"],
                }
            target = overlaps[key]
            if target["判定"] != record["判定"]:
                raise ValueError(f"{key}: 合流する重なりの判定が異なる。メインの意味判断で入力をそろえる")
            for field, values in zip(("レーン1の定義", "レーン2の定義"), definitions, strict=True):
                target[field] = list(dict.fromkeys([*typing.cast(list[str], target[field]), *typing.cast(list[str], values)]))
    result: dict[str, object] = {
        _selection.DECISIONS_KEY: [*first, *second],
        _selection.LANE_COSTS_KEY: list(costs.values()),
        _selection.LANE_OVERLAPS_KEY: list(overlaps.values()),
        "続行できない理由": ["なし"],
    }
    if _selection.INITIAL_ALLOCATION_KEY in existing:
        result[_selection.INITIAL_ALLOCATION_KEY] = existing[_selection.INITIAL_ALLOCATION_KEY]
    result[_selection.ADDED_WIS_KEY] = [
        *typing.cast(list[str], existing.get(_selection.ADDED_WIS_KEY, [])),
        *(str(item[_selection.WI_KEY]) for item in second),
    ]
    # 全体配分の比較値は追加時に再確定した値だけを使う。
    if _selection.SINGLE_STAGE_ESTIMATE_KEY in added:
        result[_selection.SINGLE_STAGE_ESTIMATE_KEY] = added[_selection.SINGLE_STAGE_ESTIMATE_KEY]
    return result
