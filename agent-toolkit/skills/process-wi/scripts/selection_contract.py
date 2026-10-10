"""候補情報と意味判断から選定の定型欄を生成し、既知の欄と型を確認する。本文との関係は呼出側が判定する。"""

from __future__ import annotations

import copy
import re
import typing
from collections.abc import Callable

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
        _selection.INTEGRATION_STATE_KEY,
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
        _selection.INITIAL_ALLOCATION_KEY,
        _selection.ADDED_WIS_KEY,
    }
)
_MODEL_ROLES = ("実装担当", "実行レビュー担当")
_MODEL_TYPE_RE = re.compile(r"(?:claude|codex|agy):[^,/\s]+/[^,/\s]+")


def generate_selection(
    judgments: object,
    candidates: list[dict[str, typing.Any]],
    metadata: Callable[[str], dict[str, typing.Any]],
    resume_plan: Callable[[object], str | None],
    *,
    additional: bool = False,
) -> dict[str, object]:
    """意味判断を保持し、候補情報と確定入力から定型欄を生成する。分類とモデルは推測しない。"""
    if not isinstance(judgments, dict):
        raise ValueError("判断入力は選定・レーンの所要時間・結合条件を持つ写像で指定する")
    if set(judgments) - (_TOP_LEVEL_KEYS | {"結合条件", "除外候補"}):
        raise ValueError("判断入力に未知の欄がある。selection-format.mdの生成入力へそろえる")
    if _selection.INITIAL_ALLOCATION_KEY in judgments:
        raise ValueError("初回配分は生成側が候補と判断から作成するため、判断入力から除く")
    facts: dict[str, dict[str, typing.Any]] = {}
    for candidate in candidates:
        name = candidate.get("filename")
        if not isinstance(name, str) or not name or name in facts or not isinstance(candidate.get("staleness"), dict):
            raise ValueError("候補情報はfilenameとstalenessを持つ重複のない一覧にする")
        facts[name] = candidate
    items = _selection.decisions(judgments)
    costs = _selection.lane_costs(judgments)
    if items is None or costs is None:
        raise ValueError("選定とレーンの所要時間は意味判断として列で指定する。0件も空列を明示する")
    generated: dict[str, object] = copy.deepcopy(judgments)
    for key in ("結合条件", "除外候補", "decisions", "lane_costs"):
        generated.pop(key, None)
    decisions: list[dict[str, typing.Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(_selection.WI_KEY), str):
            raise ValueError("各判断へWIファイル名を指定する")
        name = item[_selection.WI_KEY]
        if name not in facts or name in seen or _STALENESS_KEY in item:
            raise ValueError(f"{name}: 候補にないWI、重複判断、または手で転記した鮮度がある")
        seen.add(name)
        decisions.append(dict(copy.deepcopy(item), **{_STALENESS_KEY: copy.deepcopy(facts[name]["staleness"])}))
    generated[_selection.DECISIONS_KEY] = decisions
    generated[_selection.LANE_COSTS_KEY] = copy.deepcopy(costs)
    if additional:
        if judgments.get("結合条件"):
            raise ValueError("追加入力へ初回の結合条件を加えない。元の初回履歴を保持する")
        return generated
    excluded = judgments.get("除外候補", [])
    if (
        not is_string_list(excluded)
        or len(excluded) != len(set(excluded))
        or set(excluded) - facts.keys()
        or set(excluded) & seen
    ):
        raise ValueError("除外候補は候補にある未選定WIだけを重複なく指定する")
    assignments = {item[_selection.WI_KEY]: item[_selection.LANE_KEY] for item in decisions if _selection.LANE_KEY in item}
    if len(assignments) != len(decisions):
        raise ValueError("レーン割当は意味判断として全WIへ明示する")
    by_name = {item[_selection.WI_KEY]: item for item in decisions}
    stages = {row[_selection.LANE_KEY]: row.get(_selection.STAGE_KEY, 1) for row in costs if isinstance(row, dict)}
    raw_edges = judgments.get("結合条件", [])
    if not isinstance(raw_edges, list):
        raise ValueError("結合条件はメインが確定したWIの組と種別・定義の列で指定する")
    edges = [_generated_edge(edge, by_name, stages, metadata, resume_plan) for edge in raw_edges]
    members = [name for name, lane in assignments.items() if lane != _LANE_NONE]
    links: dict[str, set[str]] = {name: set() for name in members}
    for edge in edges:
        first, second = edge["WI1"], edge["WI2"]
        if first not in links or second not in links or first == second:
            raise ValueError("結合条件は実施する異なるWIの組へ対応付ける")
        links[first].add(second)
        links[second].add(first)
    remaining = set(members)
    groups = []
    while remaining:
        seed = next(name for name in members if name in remaining)
        connected = _connected_members(links, seed)
        groups.append(
            {
                "WI": [name for name in members if name in connected],
                "結合条件": [edge for edge in edges if edge["WI1"] in connected],
            }
        )
        remaining -= connected
    generated[_selection.INITIAL_ALLOCATION_KEY] = {
        "候補WI": [name for name in facts if name not in excluded],
        "レーン割当": assignments,
        "不可分成分": groups,
    }
    return generated


def _generated_edge(
    value: object,
    decisions: dict[str, dict[str, typing.Any]],
    stages: dict[typing.Any, typing.Any],
    metadata: Callable[[str], dict[str, typing.Any]],
    resume_plan: Callable[[object], str | None],
) -> dict[str, typing.Any]:
    if not isinstance(value, dict) or set(value) != {"WI1", "WI2", "種別", "根拠"}:
        raise ValueError("結合条件はWI1・WI2・種別・根拠の4欄で指定する")
    edge = copy.deepcopy(value)
    first, second, kind = (edge[key] for key in ("WI1", "WI2", "種別"))
    if first not in decisions or second not in decisions or not isinstance(edge["根拠"], dict):
        raise ValueError("結合条件のWIと根拠を判断入力の選定へ対応付ける")
    evidence = edge["根拠"]
    if kind == "書込重複":
        if set(evidence) != {"定義1", "定義2", "段階比較"}:
            raise ValueError("書込重複の判断は定義1・定義2・段階比較だけを指定する。パスと段階は生成する")
        for index, name in enumerate((first, second), 1):
            evidence[f"パス{index}"] = copy.deepcopy(decisions[name].get(_selection.WRITE_FILES_KEY, []))
            evidence[f"段階{index}"] = stages.get(decisions[name].get(_selection.LANE_KEY), 1)
    elif kind == "再開計画":
        if evidence:
            raise ValueError("再開計画の根拠は空写像で指定する。各WIの再開位置から生成する")
        for index, name in enumerate((first, second), 1):
            evidence[f"計画{index}"] = resume_plan(decisions[name].get("再開位置"))
    elif kind == "依存":
        if set(evidence) != {"依存元"} or evidence["依存元"] not in (first, second):
            raise ValueError("依存の判断は両WIのどちらが依存元かを指定する")
        evidence["depends_on"] = copy.deepcopy(metadata(evidence["依存元"]).get("depends_on", []))
        for index, name in enumerate((first, second), 1):
            evidence[f"リポジトリ{index}"] = metadata(name).get("target_repo", "")
    else:
        raise ValueError("結合条件の種別は依存・再開計画・書込重複のいずれかを明示する")
    return edge


def _connected_members(links: dict[str, set[str]], start: str) -> set[str]:
    pending = [start]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name not in visited:
            visited.add(name)
            pending.extend(links[name] - visited)
    return visited


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
        state = row.get(_selection.INTEGRATION_STATE_KEY, _selection.NOT_INTEGRATED)
        if state not in (_selection.NOT_INTEGRATED, _selection.INTEGRATED):
            errors.append(f"{label}: 統合状態は未統合か統合済みを指定する: {state!r}")
    blockers = selection.get(_BLOCKERS_KEY, [])
    if not is_string_list(blockers):
        errors.append(f"`{_BLOCKERS_KEY}`が文字列の列ではない: {blockers!r}")
    errors.extend(_overlap_structure_errors(selection.get(_selection.LANE_OVERLAPS_KEY, [])))
    if _selection.INITIAL_ALLOCATION_KEY in selection:
        errors.extend(_allocation_structure_errors(selection[_selection.INITIAL_ALLOCATION_KEY]))
    added = selection.get(_selection.ADDED_WIS_KEY, [])
    if not is_string_list(added) or len(added) != len(set(added)):
        errors.append("追加WIは重複のないWIファイル名の列にする")
    return errors, model_errors


def _allocation_structure_errors(value: object) -> list[str]:
    """初回配分は追加後も検査できる集合・割当・成分を保持する。"""
    if not isinstance(value, dict) or set(value) != {"候補WI", "レーン割当", "不可分成分"}:
        return ["初回配分には候補WI・レーン割当・不可分成分の3欄を指定する"]
    errors: list[str] = []
    candidates, assignments, components = (value[key] for key in ("候補WI", "レーン割当", "不可分成分"))
    if not is_string_list(candidates) or len(candidates) != len(set(candidates)):
        errors.append("初回配分の候補WIは重複のないWIファイル名の列にする")
    if not isinstance(assignments, dict) or any(
        not isinstance(key, str) or not isinstance(lane, str) or not lane.strip() for key, lane in assignments.items()
    ):
        errors.append("初回配分のレーン割当はWIから空でないレーン名への写像にする")
    if not isinstance(components, list):
        return [*errors, "初回配分の不可分成分は列にする"]
    for index, component in enumerate(components, 1):
        label = f"初回配分の不可分成分{index}"
        keys = {"WI", "結合条件"}
        if not isinstance(component, dict) or set(component) != keys:
            errors.append(f"{label}: 欄を{', '.join(sorted(keys))}へそろえ、成分別の実装秒数・統合秒数を除く")
            continue
        names = component["WI"]
        if not is_string_list(names) or not names or len(names) != len(set(names)):
            errors.append(f"{label}: WIは空でない重複のない文字列の列にする")
        edges = component["結合条件"]
        if not isinstance(edges, list):
            errors.append(f"{label}: 結合条件は列にする")
            continue
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) != {"WI1", "WI2", "種別", "根拠"}:
                errors.append(f"{label}: 結合条件にはWI1・WI2・種別・根拠を指定する")
            elif (
                not all(isinstance(edge[key], str) and edge[key].strip() for key in ("WI1", "WI2"))
                or edge["種別"] not in ("依存", "再開計画", "書込重複")
                or not isinstance(edge["根拠"], dict)
            ):
                errors.append(f"{label}: 結合条件のWI・種別・構造化した根拠を直す")
    return errors


def allocation_errors(selection: dict[str, object]) -> list[str]:
    """初回候補から決まる上限を、初回割当と現在の全レーンへ適用する。"""
    data = typing.cast(dict[str, typing.Any], selection[_selection.INITIAL_ALLOCATION_KEY])
    assignments: dict[str, str] = data["レーン割当"]
    candidates = set(data["候補WI"])
    items = typing.cast(list[dict[str, typing.Any]], _selection.decisions(selection))
    selected = {item[_selection.WI_KEY] for item in items}
    added = set(typing.cast(list[str], selection.get(_selection.ADDED_WIS_KEY, [])))
    errors: list[str] = []
    if not set(assignments) <= candidates or candidates & added or set(assignments) & added:
        errors.append("初回候補・初回割当・追加WIの集合が矛盾する。初回の元候補と追加区分を直す")
    if selected != set(assignments) | added:
        errors.append("選定のWI集合が初回割当と追加WIの和に一致しない")
    current = {item[_selection.WI_KEY]: item[_selection.LANE_KEY] for item in items}
    if any(current.get(name) != lane for name, lane in assignments.items()):
        errors.append("初回のレーン割当が現在の選定から失われている。初回の項目とレーンを保持する")
    groups: list[dict[str, typing.Any]] = data["不可分成分"]
    members = [name for group in groups for name in group["WI"]]
    active = {name for name, lane in assignments.items() if lane != _LANE_NONE}
    if set(members) != active or len(members) != len(set(members)):
        errors.append("初回配分の不可分成分は初回の実施対象WIを過不足なく1回ずつ覆うようにする")
    for group in groups:
        names = set(group["WI"])
        links: dict[str, set[str]] = {name: set() for name in names}
        for edge in group["結合条件"]:
            first, second = edge["WI1"], edge["WI2"]
            if first == second or first not in names or second not in names:
                errors.append("結合条件のWIの組が不可分成分の異なる構成項目を指していない")
                continue
            if not valid_allocation_edge(edge):
                errors.append(f"{first}と{second}: {edge['種別']}の結合根拠が成立しない")
                continue
            links[first].add(second)
            links[second].add(first)
        visited = _connected_members(links, next(iter(names)))
        if visited != names:
            errors.append(f"不可分成分が有効な結合条件で連結していない: {sorted(names)}")
        if len({assignments.get(name) for name in names}) != 1:
            errors.append(f"不可分成分を複数の初回レーンへ分けている: {sorted(names)}")
    count = len(candidates)
    bound = (count + 9) // 10
    current_lanes = set(current.values()) | {
        row[_selection.LANE_KEY] for row in typing.cast(list[dict[str, typing.Any]], _selection.lane_costs(selection))
    }
    for scope, lanes in (("初回配分", set(assignments.values())), ("現在の全体配分", current_lanes)):
        total = len(lanes - {_LANE_NONE})
        if total > bound:
            errors.append(
                f"{scope}: N={count}、上限B={bound}、実レーン数={total}: レーン数が上限を超える。"
                "統合済み・後段を含めて上限内のレーンへまとめ、複数成分は同じレーンで順に扱う"
            )
    return errors


def valid_allocation_edge(edge: dict[str, typing.Any]) -> bool:
    """選定が保持する元入力の構造化根拠から結合の成立を判定する。意味の検収はメインが行う。"""
    first, second, kind, evidence = (edge[key] for key in ("WI1", "WI2", "種別", "根拠"))
    if kind == "依存":
        return (
            set(evidence) == {"リポジトリ1", "リポジトリ2", "依存元", "depends_on"}
            and isinstance(evidence["リポジトリ1"], str)
            and bool(evidence["リポジトリ1"].strip())
            and evidence["リポジトリ1"] == evidence["リポジトリ2"]
            and evidence["依存元"] in (first, second)
            and is_string_list(evidence["depends_on"])
            and (second if evidence["依存元"] == first else first) in evidence["depends_on"]
        )
    if kind == "再開計画":
        return (
            set(evidence) == {"計画1", "計画2"}
            and isinstance(evidence["計画1"], str)
            and bool(evidence["計画1"].strip())
            and evidence["計画1"] == evidence["計画2"]
            and evidence["計画1"] not in ("なし", "計画なし")
        )
    return (
        set(evidence) == {"パス1", "パス2", "定義1", "定義2", "段階1", "段階2", "段階比較"}
        and all(
            is_string_list(evidence[key]) and evidence[key] and all(value.strip() for value in evidence[key])
            for key in ("パス1", "パス2", "定義1", "定義2")
        )
        and bool(set(evidence["定義1"]) & set(evidence["定義2"]))
        and any(
            left == right or left.endswith("/") and right.startswith(left) or right.endswith("/") and left.startswith(right)
            for left in evidence["パス1"]
            for right in evidence["パス2"]
        )
        and all(isinstance(evidence[key], int) and not isinstance(evidence[key], bool) for key in ("段階1", "段階2"))
        and evidence["段階1"] >= 1
        and evidence["段階1"] == evidence["段階2"]
        and isinstance(evidence["段階比較"], str)
        and bool(evidence["段階比較"].strip())
    )


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
