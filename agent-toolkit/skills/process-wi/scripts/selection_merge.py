"""型を確認済みの選定結果を、メインが確定したレーン対応で合流する。"""

from __future__ import annotations

import collections.abc
import copy
import typing

import selection_contract
from check_selection_lanes import _resume_plan_id, overlapping_pairs

from agent_toolkit._plan import selection as _selection

_Rows = list[dict[str, object]]
# 重なりの記録の鍵（昇順のレーン名の組と共通パス）と、その鍵で重なるWIの組と共通パス。
_RecordKey = tuple[str, str, str]
_Pair = tuple[frozenset[str], str]
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


def _pin_integrated_items(items: _Rows, integrated: set[str]) -> None:
    """統合済みレーンの項目へ統合状態を写す。

    レーン行を未統合へ戻した後も、その時点までに統合した項目を区別できるようにする。
    """
    for item in items:
        if item.get(_selection.LANE_KEY, item.get("lane")) in integrated:
            # 明示的に未統合の項目は、統合済みのレーン行でも未統合の作業として残す。
            item.setdefault(_selection.INTEGRATION_STATE_KEY, _selection.INTEGRATED)


def _retarget_overlaps(
    records: collections.abc.Iterable[tuple[dict[str, object], collections.abc.Callable[[str], str]]],
) -> _Rows:
    """重なりの記録を組ごとの写像でレーン名を付け替えて合わせる。

    同じレーン同士になった記録は除き、同じレーンの組と共通パスの記録は双方の定義を合わせて1件にする。
    """
    overlaps: dict[tuple[str, str, str], dict[str, object]] = {}
    for record, rename in records:
        left, right = rename(str(record["レーン1"])), rename(str(record["レーン2"]))
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
            raise ValueError(f"{key}: 合わせる重なりの判定が異なる。メインの意味判断で入力をそろえる")
        for field, values in zip(("レーン1の定義", "レーン2の定義"), definitions, strict=True):
            target[field] = list(dict.fromkeys([*typing.cast(list[str], target[field]), *typing.cast(list[str], values)]))
    return list(overlaps.values())


def _record_key(record: dict[str, object]) -> _RecordKey:
    left, right = sorted((str(record["レーン1"]), str(record["レーン2"])))
    return left, right, str(record["共通パス"])


def _pair_keys(items: _Rows) -> dict[_Pair, _RecordKey]:
    """別レーンで書込対象を共有するWIの組と共通パスごとに、それを判定する重なりの記録の鍵を返す。"""
    return {
        (frozenset((str(left[_selection.WI_KEY]), str(right[_selection.WI_KEY]))), path): (*lanes, path)
        for left, right, lanes, paths in overlapping_pairs(items)
        for path in paths
    }


def _lanes(items: _Rows) -> dict[str, str]:
    return {str(item[_selection.WI_KEY]): str(item[_selection.LANE_KEY]) for item in items}


def _carry_overlaps(sources: collections.abc.Sequence[tuple[_Rows, _Rows]], items: _Rows) -> _Rows:
    """記録時点に判定したWIの組が更新後の鍵の組を覆う重なりの記録だけを、更新後のレーン名で残す。

    `sources`は記録の列と、それを記録した時点の項目の配置の組の列とし、記録はその配置で同じ鍵に重なる組を判定したものとみなす。
    更新後の鍵が判定していない組を含む記録（既存レーンへ合流・移管したWIと相手レーンのWIの組など）と、
    判定した組が分かれる・消える記録は、どのWIの定義かを区別できないため除き、全体検査の記録不足として再判定させる。
    統合済みの項目を含む組は全体検査が記録を求めないため、判定していなくても記録を残す妨げにしない。
    """
    after_lanes = _lanes(items)
    settled = {
        str(item[_selection.WI_KEY]) for item in items if item.get(_selection.INTEGRATION_STATE_KEY) == _selection.INTEGRATED
    }
    judged: dict[tuple[int, _RecordKey], set[_Pair]] = collections.defaultdict(set)
    judges: dict[_Pair, set[tuple[int, _RecordKey]]] = collections.defaultdict(set)
    renames: dict[tuple[int, _RecordKey], dict[str, str]] = collections.defaultdict(dict)
    for index, (records, placed) in enumerate(sources):
        recorded = {_record_key(record) for record in records}
        before_lanes = _lanes(placed)
        for pair, key in _pair_keys(placed).items():
            if key in recorded:
                judged[index, key].add(pair)
                judges[pair].add((index, key))
                renames[index, key].update((before_lanes[wi], after_lanes[wi]) for wi in pair[0])
    targets: dict[_RecordKey, set[_Pair]] = collections.defaultdict(set)
    for pair, key in _pair_keys(items).items():
        targets[key].add(pair)
    kept: set[tuple[int, _RecordKey]] = set()
    for pairs in targets.values():
        origins = set().union(*(judges.get(pair, set()) for pair in pairs))
        covered = set().union(*(judged[origin] for origin in origins))
        if covered <= pairs and all(pair in covered or pair[0] & settled for pair in pairs):
            kept |= origins
    return _retarget_overlaps(
        (record, renames[origin].__getitem__)
        for index, (records, _) in enumerate(sources)
        for record in records
        if (origin := (index, _record_key(record))) in kept
    )


def _rerecord_pairs(items: _Rows, judged: set[_Pair], labels: dict[str, str]) -> dict[_RecordKey, list[str]]:
    """更新後の鍵ごとに、`judged`に無い未統合のWIの組を、記録に書くレーン名（`labels`）を添えた表記で返す。"""
    settled = {
        str(item[_selection.WI_KEY]) for item in items if item.get(_selection.INTEGRATION_STATE_KEY) == _selection.INTEGRATED
    }
    pairs: dict[_RecordKey, list[str]] = collections.defaultdict(list)
    for pair, key in _pair_keys(items).items():
        if pair not in judged and not pair[0] & settled:
            pairs[key].append("と".join(f"{wi}（{labels[wi]}）" for wi in sorted(pair[0])))
    return {key: sorted(values) for key, values in pairs.items()}


def merge_rerecord_pairs(
    existing: dict[str, object], added: dict[str, object], merged: dict[str, object]
) -> dict[_RecordKey, list[str]]:
    """合流後の鍵ごとに、合流前のどの記録も判定していない組を、追加YAMLの記録に書くレーン名を添えて返す。

    合流前の配置は`merge_selection`と同じく、追加分を仮名、既存分を既存レーンの名前に置く。
    """
    placed = [*_rows(added), *_rows(existing)]
    judged: set[_Pair] = set()
    for records, items in (
        (existing.get(_selection.LANE_OVERLAPS_KEY, []), _rows(existing)),
        (added.get(_selection.LANE_OVERLAPS_KEY, []), placed),
    ):
        recorded = {_record_key(record) for record in typing.cast(_Rows, records)}
        judged.update(pair for pair, key in _pair_keys(items).items() if key in recorded)
    return _rerecord_pairs(_rows(merged), judged, _lanes(placed))


def transfer_rerecord_pairs(transferred: dict[str, object]) -> dict[_RecordKey, list[str]]:
    """移管後の鍵ごとに、記録し直す全ての組を移管後のレーン名を添えて返す。

    記録不足の鍵は移管前の記録ごと除いているため、その鍵の全ての組を1件の記録で判定し直す。
    """
    items = _rows(transferred)
    return _rerecord_pairs(items, set(), _lanes(items))


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


def reopen_wis(existing: dict[str, object], names: list[str], *, new_plan: bool = False) -> dict[str, object]:
    """元の所属と初回配分を保ち、指定した既存WIと所属レーンだけを未統合へ戻す。"""
    result = copy.deepcopy(existing)
    items = typing.cast(_Rows, result.get(_selection.DECISIONS_KEY, result.get("decisions")))
    by_wi = {str(item.get(_selection.WI_KEY, item.get("awi"))): item for item in items}
    missing = set(names) - by_wi.keys()
    if missing:
        raise ValueError(f"再開対象WIが保存済み選定に無い: {sorted(missing)}")

    def lane_of(item: dict[str, object]) -> str:
        return str(item.get(_selection.LANE_KEY, item.get("lane")))

    targets = {lane_of(by_wi[name]) for name in names}
    if "なし" in targets:
        raise ValueError("レーンが`なし`のWIは元レーンで再開できない")
    _pin_integrated_items(items, _selection.integrated_lanes(existing))
    for name in names:
        by_wi[name][_selection.INTEGRATION_STATE_KEY] = _selection.NOT_INTEGRATED
        if new_plan:
            by_wi[name]["再開位置"] = "なし"
    costs = typing.cast(_Rows, result.get(_selection.LANE_COSTS_KEY, result.get("lane_costs")))
    if not targets <= {lane_of(row) for row in costs}:
        raise ValueError("再開対象WIの元レーンが所要時間に無い")
    for row in costs:
        if lane_of(row) in targets:
            row[_selection.INTEGRATION_STATE_KEY] = _selection.NOT_INTEGRATED
    return result


def transfer_wi(existing: dict[str, object], name: str, destination: str, cost_updates: object) -> dict[str, object]:
    """追加済みで未着手のWIを、既存の別レーンへ移す。

    初回割当・統合済み・凍結した再開計画を持つWIは移さない。
    所要時間の更新は、移した後に残る移管元と移管先の最終値を受け取る。
    移管元が空になる場合は、そのレーン行を除く。
    移管元は移したWIの`移管元レーン`へ残し、累計のレーン上限に数える。
    レーン間の重なりの記録は、判定した組が移管後の鍵の組を覆うものだけを移管先へ付け替え、他は除いて再判定させる。
    """
    result = copy.deepcopy(existing)
    items = _rows(result)
    by_wi = {str(item[_selection.WI_KEY]): item for item in items}
    item = by_wi.get(name)
    if item is None:
        raise ValueError(f"移管対象WIが保存済み選定に無い: {name}")
    source = str(item[_selection.LANE_KEY])
    if name not in typing.cast(list[str], result.get(_selection.ADDED_WIS_KEY, [])):
        raise ValueError(f"{name}: 追加済みでないWIは移管できない（初回割当は保持する）")
    if source == "なし":
        raise ValueError(f"{name}: レーンが`なし`のWIは移管できない")
    if not destination.strip() or destination == "なし":
        raise ValueError("移管先は空でない既存レーン名とする")
    if destination == source:
        raise ValueError(f"{name}: 移管先が現在のレーン{source}と同じ")
    if item.get(_selection.INTEGRATION_STATE_KEY) == _selection.INTEGRATED or source in _selection.integrated_lanes(result):
        raise ValueError(f"{name}: 統合済みのWIは移管できない")
    if _resume_plan_id(item.get("再開位置")) is not None:
        raise ValueError(f"{name}: 凍結した再開計画を持つWIは移管できない。元レーンで再開する")
    costs = _costs(result)
    rows = {str(row[_selection.LANE_KEY]): row for row in costs}
    if destination not in rows:
        raise ValueError(f"移管先レーンが所要時間に無い: {destination}")
    remains = any(other is not item and other[_selection.LANE_KEY] == source for other in items)
    needed = {source, destination} if remains else {destination}
    if not isinstance(cost_updates, dict) or set(cost_updates) != needed:
        raise ValueError(f"費用更新は移管後の最終値を{sorted(needed)}の全てへ指定する")
    errors = selection_contract.cost_update_errors(cost_updates, needed)
    if errors:
        raise ValueError("\n".join(errors))
    _pin_integrated_items(items, _selection.integrated_lanes(result))
    placed = [dict(item) for item in items]
    item[_selection.LANE_KEY] = destination
    item.pop(_selection.INTEGRATION_STATE_KEY, None)
    history = typing.cast(list[str], item.get(_selection.TRANSFERRED_FROM_KEY, []))
    item[_selection.TRANSFERRED_FROM_KEY] = list(dict.fromkeys([*history, source]))
    for lane, update in typing.cast(dict[str, dict[str, object]], cost_updates).items():
        rows[lane].update(update)
    rows[destination][_selection.INTEGRATION_STATE_KEY] = _selection.NOT_INTEGRATED
    if not remains:
        if any(source in typing.cast(list[str], row.get(_selection.PRIOR_LANES_KEY, [])) for row in costs):
            raise ValueError(f"{source}: 移管で空になるレーンを先行レーンとして参照するレーンがある")
        costs = [row for row in costs if row[_selection.LANE_KEY] != source]
    records = typing.cast(_Rows, result.get(_selection.LANE_OVERLAPS_KEY, []))
    result[_selection.LANE_OVERLAPS_KEY] = _carry_overlaps([(records, placed)], items)
    result.pop("decisions", None)
    result.pop("lane_costs", None)
    result[_selection.DECISIONS_KEY] = items
    result[_selection.LANE_COSTS_KEY] = costs
    errors, models = selection_contract.structure_errors(result)
    if errors or models:
        raise ValueError("\n".join([*models, *errors]))
    return result


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
    coefficients = _selection.correction_coefficients(correction)
    if ("係数" in correction) != ("係数" in additional_correction):
        raise ValueError("既存と追加の`見込みの補正`の形式が異なる。既存と同じ形式で追加分を記録する")
    if coefficients != _selection.correction_coefficients(additional_correction):
        raise ValueError("追加YAMLの`見込みの補正`の係数が既存と異なる。同じ工程別係数で追加分を補正する")
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

    _pin_integrated_items(first, _selection.integrated_lanes(existing))
    # 追加側の記録は、追加分を仮名、既存分を既存レーンの名前に置いた合流前の配置で判定したものとする。
    # 仮名が同じ名前の既存レーンへ写る場合、その名前の記録は追加分と既存分の双方を判定する。
    added_placed = [*(dict(item) for item in second), *first]
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
        if prior is None:
            # 初回配分は保持し、新レーンも合流後の累計上限・再開計画の全体検査へ渡す。
            costs[lane] = row
            continue
        prior[_selection.INTEGRATION_STATE_KEY] = _selection.NOT_INTEGRATED
        if prior.get(_selection.STAGE_KEY, 1) != row.get(_selection.STAGE_KEY, 1):
            raise ValueError(f"{lane}: 合流するレーンの段階が異なる。最終割当に合う入力へ直す")
        for key, raw, coefficient in zip(
            _SECONDS.values(),
            (_selection.UNCORRECTED_IMPLEMENTATION_KEY, _selection.UNCORRECTED_INTEGRATION_KEY),
            coefficients,
            strict=True,
        ):
            if raw not in prior or raw not in row:
                raise ValueError(f"{lane}: 合流する両行へ`{raw}`を記録する")
            prior[raw] = typing.cast(float, prior[raw]) + typing.cast(float, row[raw])
            prior[key] = typing.cast(float, prior[raw]) * coefficient
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
    overlaps = _carry_overlaps(
        [
            (typing.cast(_Rows, existing.get(_selection.LANE_OVERLAPS_KEY, [])), first),
            (typing.cast(_Rows, added.get(_selection.LANE_OVERLAPS_KEY, [])), added_placed),
        ],
        [*first, *second],
    )
    result: dict[str, object] = {
        _selection.DECISIONS_KEY: [*first, *second],
        _selection.LANE_COSTS_KEY: list(costs.values()),
        _selection.LANE_OVERLAPS_KEY: overlaps,
        "続行できない理由": ["なし"],
        correction_key: {
            **correction,
            "参照した記録": list(
                dict.fromkeys(
                    typing.cast(list[str], correction["参照した記録"])
                    + typing.cast(list[str], additional_correction["参照した記録"])
                )
            ),
            "除外した記録": list(typing.cast(list[object], correction["除外した記録"])),
        },
    }
    if _selection.SHARED_WAIT_SECONDS_KEY in correction:
        # 追加側は全体配分の待ちを再確定する。既存側との加算は同じ待ちを二重に数える。
        typing.cast(dict[str, object], result[correction_key])[_selection.SHARED_WAIT_SECONDS_KEY] = additional_correction[
            _selection.SHARED_WAIT_SECONDS_KEY
        ]
    if _selection.SHARED_WAIT_BASIS_KEY in additional_correction:
        # 待ちの秒数と同じく、算定の区分も追加側で再確定した全体配分の値を採用する。
        typing.cast(dict[str, object], result[correction_key])[_selection.SHARED_WAIT_BASIS_KEY] = additional_correction[
            _selection.SHARED_WAIT_BASIS_KEY
        ]
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
    if _selection.TARGET_OVERRUN_COMPARISON_KEY in added:
        result[_selection.TARGET_OVERRUN_COMPARISON_KEY] = added[_selection.TARGET_OVERRUN_COMPARISON_KEY]
    if _selection.PROCESS_ESTIMATE_KEY in added:
        # 追加分だけの経路を既存へ足すと共有工程を二重計上するため、全体を再確定した入力を使う。
        result[_selection.PROCESS_ESTIMATE_KEY] = added[_selection.PROCESS_ESTIMATE_KEY]
    # 追加側が見込みを持たない場合、追加分を含まない既存の値を残すと誤った見込みを表示するため合流結果へ引き継がない。
    # 見込みの欠落は検査の警告として出す。
    excluded = typing.cast(dict[str, object], result[correction_key])["除外した記録"]
    assert isinstance(excluded, list)
    excluded.extend(
        record for record in typing.cast(list[object], additional_correction["除外した記録"]) if record not in excluded
    )
    return result
