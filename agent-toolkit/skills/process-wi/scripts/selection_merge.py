"""型を確認済みの選定結果を、メインが確定したレーン対応で合流する。"""

from __future__ import annotations

import copy
import typing

import selection_contract

from agent_toolkit._plan import selection as _selection

_Rows = list[dict[str, object]]
_SECONDS = {"implementation_seconds": "実装秒数", "integration_seconds": "統合秒数"}
_NORM_SPEC_KEY = "プロジェクト規範の指定"
# 追加入力の`選定`の各項目が持てる欄。追加操作は「自身の追記で成立し得る全体検査の違反を、同じ追加入力で解消できる」
# ことを不変条件とするため、`check_selection.py`のうち`書込対象`に連動して別の欄を求める検査
# （導出記録、レーン間の重なり、プロジェクト規範の指定、区分間の重複）の解消手段をここで受け付ける。
# `書込対象`に連動する検査を`check_selection.py`へ加えるときは、この集合と`extend_write_targets`も見直す。
_EXTEND_FIELDS = frozenset({_selection.WI_KEY, _selection.WRITE_FILES_KEY, _selection.DERIVED_NEW_PATHS_KEY, _NORM_SPEC_KEY})


def _covers(entry: str, path: str) -> bool:
    """`check_selection.py`の同名の判定と同じく、`entry`が`path`と同じか、末尾`/`の範囲として配下に含むかを返す。"""
    return entry == path or (entry.endswith("/") and path.startswith(entry))


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


def extend_write_targets(existing: dict[str, object], addition: object) -> dict[str, object]:
    """指定WIの書込対象と関連記録を追記し、既存の配分と他の欄を保持する。"""
    allowed = {_selection.DECISIONS_KEY, _selection.LANE_OVERLAPS_KEY}
    if not isinstance(addition, dict) or set(addition) - allowed:
        raise ValueError("追加入力の最上位は`選定`・`レーン間の重なり`だけを持つ写像とする")
    updates = addition.get(_selection.DECISIONS_KEY, [])
    overlaps = addition.get(_selection.LANE_OVERLAPS_KEY, [])
    if not isinstance(updates, list) or not isinstance(overlaps, list):
        raise ValueError("追加入力の`選定`・`レーン間の重なり`は列とする")
    result = copy.deepcopy(existing)
    # 保存済みの旧欄名と、更新しない項目の表記も保持する。
    items = typing.cast(_Rows, result.get(_selection.DECISIONS_KEY, result.get("decisions")))
    by_wi = {str(item.get(_selection.WI_KEY, item.get("awi"))): item for item in items}
    fields = (_selection.WRITE_FILES_KEY, _selection.DERIVED_NEW_PATHS_KEY)
    for update in updates:
        if not isinstance(update, dict) or set(update) - _EXTEND_FIELDS:
            raise ValueError(
                "追加入力の`選定`の各項目は`WI`・`書込対象`・`導出した新設先`・`プロジェクト規範の指定`だけを持つ写像とする"
            )
        name = update.get(_selection.WI_KEY)
        if not isinstance(name, str) or name not in by_wi:
            raise ValueError(f"書込対象の追加先WIが保存済み選定に無い: {name}")
        item = by_wi[name]
        if item.get(_selection.LANE_KEY, item.get("lane")) == "なし":
            raise ValueError(f"{name}: レーンが`なし`の項目へ書込対象を追加できない")
        if _NORM_SPEC_KEY in update:
            spec = update[_NORM_SPEC_KEY]
            if not isinstance(spec, str):
                raise ValueError(f"{name}: 追加入力の`{_NORM_SPEC_KEY}`は文字列とする")
            # 旧欄名で保存された項目は旧欄名のまま置き換え、同じ意味の欄を二重に持たせない。
            item["project_notes" if "project_notes" in item and _NORM_SPEC_KEY not in item else _NORM_SPEC_KEY] = spec
        added = update.get(_selection.WRITE_FILES_KEY, [])
        excluded_key = next(
            (key for key in (_selection.EXCLUDED_PATHS_KEY, "excluded_paths") if key in item), _selection.EXCLUDED_PATHS_KEY
        )
        excluded = item.get(excluded_key)
        if isinstance(added, list) and isinstance(excluded, list):
            # 追加入力は「この項目がそのパスを書き込む」宣言なので、同じパスか追加パスの配下を非書込に分類したままにしない。
            # 非書込の範囲が追加パスを真に含む組は、全体検査が重複に数えない受理済みの選定なので残す。
            item[excluded_key] = [
                non_write
                for non_write in excluded
                if not any(
                    isinstance(write, str) and isinstance(non_write, str) and _covers(write, non_write) for write in added
                )
            ]
        for field in fields:
            if field not in update:
                continue
            values = update[field]
            if not isinstance(values, list):
                raise ValueError(f"{name}: 追加入力の`{field}`は列とする")
            key = (
                "write_files" if field == _selection.WRITE_FILES_KEY and "write_files" in item and field not in item else field
            )
            target = typing.cast(list[object], item.setdefault(key, []))
            target.extend(copy.deepcopy(value) for value in values if value not in target)
    if overlaps:
        target = typing.cast(list[object], result.setdefault(_selection.LANE_OVERLAPS_KEY, []))
        target.extend(copy.deepcopy(value) for value in overlaps if value not in target)
    errors, models = selection_contract.structure_errors(result)
    if errors or models:
        raise ValueError("\n".join([*models, *errors]))
    return result


def merge_selection(
    existing: dict[str, object], added: dict[str, object], lane_map: object, cost_updates: object = None
) -> dict[str, object]:
    """合流結果を返す。変換不足や不整合は保存前に拒否し、元の入力を変更しない。"""
    existing, added = copy.deepcopy(existing), copy.deepcopy(added)
    correction_key = _selection.ESTIMATE_CORRECTION_KEY
    if correction_key not in existing or correction_key not in added:
        raise ValueError("既存と追加の両YAMLへ`見込みの補正`を記録する")
    correction = typing.cast(dict[str, object], existing[correction_key])
    additional_correction = typing.cast(dict[str, object], added[correction_key])
    if correction["係数"] != additional_correction["係数"]:
        raise ValueError("追加YAMLの`見込みの補正`の係数が既存と異なる。同じ係数で追加分を補正する")
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
    existing_lanes = {str(item[_selection.LANE_KEY]) for item in first} - {"なし"}
    new_destinations = set(mapping.values()) - existing_lanes
    if new_destinations:
        raise ValueError(
            f"追加の最終レーンが既存選定にない: {', '.join(sorted(new_destinations))}。"
            "対応JSONの値を既存レーンへ直し、同じ担当の工程境界で追加する"
        )
    if cost_updates is not None:
        errors = selection_contract.cost_update_errors(cost_updates, set(mapping.values()))
        if errors:
            raise ValueError("\n".join(errors))

    def destination(lane: str) -> str:
        return mapping.get(lane, lane)

    # 合流先のレーン行は未統合へ戻るため、統合済みレーンの既存項目へ状態を写して旧項目を区別できるようにする。
    integrated = _selection.integrated_lanes(existing)
    for item in first:
        if item[_selection.LANE_KEY] in integrated:
            item[_selection.INTEGRATION_STATE_KEY] = _selection.INTEGRATED
    for item in second:
        item[_selection.LANE_KEY] = destination(str(item[_selection.LANE_KEY]))
        item.pop(_selection.INTEGRATION_STATE_KEY, None)
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
        assert prior is not None
        prior[_selection.INTEGRATION_STATE_KEY] = _selection.NOT_INTEGRATED
        if prior.get(_selection.STAGE_KEY, 1) != row.get(_selection.STAGE_KEY, 1):
            raise ValueError(f"{lane}: 合流するレーンの段階が異なる。最終割当に合う入力へ直す")
        for key, raw in zip(
            _SECONDS.values(), (_selection.UNCORRECTED_IMPLEMENTATION_KEY, _selection.UNCORRECTED_INTEGRATION_KEY), strict=True
        ):
            if raw not in prior or raw not in row:
                raise ValueError(f"{lane}: 合流する両行へ`{raw}`を記録する")
            prior[raw] = typing.cast(float, prior[raw]) + typing.cast(float, row[raw])
            prior[key] = typing.cast(float, prior[raw]) * typing.cast(float, correction["係数"])
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
        correction_key: {
            "係数": correction["係数"],
            "参照した記録": list(
                dict.fromkeys(
                    typing.cast(list[str], correction["参照した記録"])
                    + typing.cast(list[str], additional_correction["参照した記録"])
                )
            ),
            "除外した記録": list(typing.cast(list[object], correction["除外した記録"])),
        },
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
    if _selection.STAGED_ESTIMATE_KEY in added:
        result[_selection.STAGED_ESTIMATE_KEY] = added[_selection.STAGED_ESTIMATE_KEY]
    excluded = typing.cast(dict[str, object], result[correction_key])["除外した記録"]
    assert isinstance(excluded, list)
    excluded.extend(
        record for record in typing.cast(list[object], additional_correction["除外した記録"]) if record not in excluded
    )
    return result
