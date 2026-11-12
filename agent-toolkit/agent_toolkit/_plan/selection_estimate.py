"""全工程の依存と有限資源に基づく静的な所要見込みを計算する。"""

from __future__ import annotations

import dataclasses
import graphlib
import math
import typing
from collections.abc import Mapping, Sequence

_RESOURCES = ("モデル", "検証", "直列")
_PHASES = {"選定", "準備", "実装", "レビュー", "検証", "統合", "公開", "後始末", "報告"}


@dataclasses.dataclass(frozen=True)
class Forecast:
    """数値の見込みと、その成立を妨げる不足を分けて保持する。"""

    total_seconds: float | None
    normal_seconds: float | None
    issues: list[str]
    excluded_wis: list[str]
    resource_work_seconds: dict[str, float]
    resource_wait_seconds: dict[str, float]
    dependency_seconds: float | None


@dataclasses.dataclass(frozen=True)
class _Node:
    id: str
    phase: str
    wis: tuple[str, ...]
    predecessors: tuple[str, ...]
    resource: str
    seconds: float | None
    basis: str
    assumptions: tuple[str, ...]


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strings(value: object, *, unique: bool = True) -> bool:
    return (
        isinstance(value, (list, tuple))
        and all(_text(item) for item in value)
        and (not unique or len(value) == len(set(value)))
    )


def _number(value: object, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False


def _keys(value: object, required: set[str], optional: set[str] | None = None) -> bool:
    return isinstance(value, dict) and required <= value.keys() <= required | (optional or set())


def structure_errors(plan: object) -> list[str]:
    """型・数値・識別子・依存の不正を返す。意味上の不足はforecastが扱う。"""
    if not _keys(plan, {"並列数", "工程"}, {"巨大WI"}):
        return ["全工程の見込み: 並列数・工程が必要で、未知の項目は指定できない"]
    data = typing.cast(dict[str, typing.Any], plan)
    errors: list[str] = []
    concurrency = data["並列数"]
    if not _keys(concurrency, {"モデル同時数", "資源上限", "サービス上限", "根拠"}):
        errors.append("並列数: モデル同時数・資源上限・サービス上限・根拠が必要")
    else:
        limits = [concurrency[key] for key in ("モデル同時数", "資源上限", "サービス上限")]
        if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in limits):
            errors.append("並列数: 同時数と上限はbool以外の正の整数が必要")
        elif limits[0] > min(limits[1:]):
            errors.append("並列数: モデル同時数が資源上限またはサービス上限を超えている")
        evidence = concurrency["根拠"]
        if not _keys(evidence, {"資源", "サービス", "費用"}) or not all(_text(v) for v in evidence.values()):
            errors.append("並列数.根拠: 資源・サービス・費用の非空文字列が必要")
    rows = data["工程"]
    if not isinstance(rows, (list, tuple)) or not rows:
        return [*errors, "工程: 非空の列が必要"]
    required = {"ID", "工程", "WI", "先行工程", "資源", "秒数", "算定", "根拠", "未確認前提"}
    for index, row in enumerate(rows):
        label = f"工程[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label}: 項目は辞書で指定する")
            continue
        optional = {"残量", "速度"} if row.get("算定") == "残量/速度" else set()
        if not _keys(row, required, optional):
            errors.append(f"{label}: 必須項目が不足しているか未知の項目がある")
            continue
        if not all(_text(row[key]) for key in ("ID", "根拠")):
            errors.append(f"{label}: ID・根拠は非空文字列が必要")
        if not isinstance(row["工程"], str) or row["工程"] not in _PHASES:
            errors.append(f"{label}: 未知の工程")
        if row["資源"] not in _RESOURCES:
            errors.append(f"{label}: 未知の資源")
        phase = row["工程"]
        expected_resource = (
            {"実装": "モデル", "レビュー": "モデル", "検証": "検証", "統合": "直列"}.get(phase)
            if isinstance(phase, str)
            else None
        )
        if expected_resource is not None and row["資源"] != expected_resource:
            errors.append(f"{label}: {phase}は{expected_resource}資源を使う。モデル仕事の直列性は先行工程へ記録する")
        elif phase != "検証" and row["資源"] == "検証":
            errors.append(f"{label}: 検証資源は検証工程だけに指定する")
        for key in ("WI", "先行工程", "未確認前提"):
            if not _strings(row[key], unique=key != "未確認前提"):
                errors.append(f"{label}.{key}: 非空文字列の列が必要（WI・先行工程は重複不可）")
        seconds = row["秒数"]
        if seconds is not None and not _number(seconds):
            errors.append(f"{label}: 秒数は非負有限数かnullが必要")
        basis = row["算定"]
        if basis not in ("実測", "残量/速度", "条件付き試算", "未確認"):
            errors.append(f"{label}: 未知の算定")
        if basis == "実測" and seconds is None:
            errors.append(f"{label}: 実測には秒数が必要")
        if basis == "残量/速度":
            if not _number(row.get("残量")) or not _number(row.get("速度"), positive=True) or not _number(seconds):
                errors.append(f"{label}: 残量・速度・秒数の数値が不正")
            elif not math.isfinite(row["残量"] / row["速度"]) or abs(seconds - row["残量"] / row["速度"]) > 1:
                errors.append(f"{label}: 秒数と残量/速度の誤差が1秒を超えている")
    if errors:
        return errors
    nodes = _nodes(data)
    ids = [node.id for node in nodes]
    if len(ids) != len(set(ids)):
        errors.append("工程: IDが重複している")
    for node in nodes:
        if node.id in node.predecessors or set(node.predecessors) - set(ids):
            errors.append(f"{node.id}: 自己依存または未知の先行工程がある")
    if not errors:
        try:
            tuple(graphlib.TopologicalSorter({node.id: node.predecessors for node in nodes}).static_order())
        except graphlib.CycleError:
            errors.append("工程: 依存が循環している")
    giants = data.get("巨大WI", [])
    if not isinstance(giants, (list, tuple)):
        return [*errors, "巨大WI: 列が必要"]
    target = {wi for node in nodes for wi in node.wis}
    seen: set[str] = set()
    for giant in giants:
        if not _keys(giant, {"WI", "巨大判定根拠"}, {"承認元"}):
            errors.append("巨大WI: WI・巨大判定根拠が必要で未知の項目は指定できない")
            continue
        if not _text(giant["WI"]) or not _text(giant["巨大判定根拠"]):
            errors.append("巨大WI: WI・巨大判定根拠は非空文字列が必要")
            continue
        if giant["WI"] in seen or giant["WI"] not in target:
            errors.append("巨大WI: WIが重複しているか工程の対象外である")
        seen.add(giant["WI"])
        if "承認元" in giant:
            approval = giant["承認元"]
            if (
                not _keys(approval, {"対象WI", "判断", "出所"})
                or not all(_text(approval[k]) for k in ("対象WI", "出所"))
                or approval["判断"] not in ("承認", "拒否", "未回答")
            ):
                errors.append("巨大WI.承認元: 対象WI・判断（承認/拒否/未回答）・出所が必要")
    return errors


def _nodes(plan: dict[str, typing.Any]) -> list[_Node]:
    return [
        _Node(
            row["ID"],
            row["工程"],
            tuple(row["WI"]),
            tuple(row["先行工程"]),
            row["資源"],
            None if row["秒数"] is None else float(row["秒数"]),
            row["算定"],
            tuple(row["未確認前提"]),
        )
        for row in plan["工程"]
    ]


def forecast(
    plan: object,
    wi_lanes: Mapping[str, str] | None = None,
    prior_lanes: Mapping[str, Sequence[str]] | None = None,
) -> Forecast:
    """依存・資源を守る決定論的な構成の見込みを返す。最短性は保証しない。"""
    errors = structure_errors(plan)
    if errors:
        raise ValueError("; ".join(errors))
    data = typing.cast(dict[str, typing.Any], plan)
    nodes = _nodes(data)
    order = tuple(graphlib.TopologicalSorter({node.id: node.predecessors for node in nodes}).static_order())
    by_id = {node.id: node for node in nodes}
    ancestors: dict[str, set[str]] = {}
    for identifier in order:
        ancestors[identifier] = set(by_id[identifier].predecessors)
        for prior in by_id[identifier].predecessors:
            ancestors[identifier].update(ancestors[prior])
    issues = _path_issues(nodes, ancestors, wi_lanes, prior_lanes)
    for node in nodes:
        if node.seconds is None or node.basis in ("条件付き試算", "未確認") or node.assumptions:
            issues.append(f"{node.id}: 秒数・算定または前提が未確認（{node.basis}）")
    excluded = []
    for giant in data.get("巨大WI", []):
        approval = giant.get("承認元")
        if approval and approval["対象WI"] != giant["WI"]:
            issues.append(f"{giant['WI']}: 承認元の対象WIが一致しない")
        elif approval and approval["判断"] == "承認":
            excluded.append(giant["WI"])
    work = {
        resource: sum(node.seconds for node in nodes if node.resource == resource and node.seconds is not None)
        for resource in _RESOURCES
        if all(node.seconds is not None for node in nodes if node.resource == resource)
    }
    if not math.isfinite(sum(work.values())):
        return Forecast(None, None, [*issues, "工程の処理秒数の合計が有限数ではない"], excluded, {}, {}, None)
    if any(node.seconds is None for node in nodes):
        return Forecast(None, None, issues, excluded, work, {}, None)
    durations = {node.id: typing.cast(float, node.seconds) for node in nodes}
    capacities = {"モデル": data["並列数"]["モデル同時数"], "検証": 2, "直列": 1}
    total, wait, dependency = _schedule(nodes, order, durations, capacities)
    normal_durations = {node.id: 0.0 if node.wis and set(node.wis) <= set(excluded) else durations[node.id] for node in nodes}
    normal, _, _ = _schedule(nodes, order, normal_durations, capacities)
    return Forecast(total, normal, issues, excluded, work, wait, dependency)


def _path_issues(
    nodes: list[_Node],
    ancestors: dict[str, set[str]],
    wi_lanes: Mapping[str, str] | None,
    prior_lanes: Mapping[str, Sequence[str]] | None,
) -> list[str]:
    issues = []
    for phase in sorted(_PHASES - {"準備"}):
        if not any(node.phase == phase for node in nodes):
            issues.append(f"完成経路: {phase}工程が必要")
    target = {wi for node in nodes for wi in node.wis}
    if wi_lanes is not None and target != set(wi_lanes):
        issues.append("完成経路: 工程のWI集合と選定の対象WI集合が一致しない")
    for wi in sorted(target | set(wi_lanes or {})):
        for phase in ("実装", "レビュー", "検証", "統合"):
            if not any(node.phase == phase and wi in node.wis for node in nodes):
                issues.append(f"{wi}: {phase}工程が必要")
    for node in nodes:
        if node.phase == "実装":
            if not any(prior.phase == "選定" and prior.id in ancestors[node.id] for prior in nodes):
                issues.append(f"{node.id}: 選定が実装の先行工程にない")
            for wi in node.wis:
                for phase in ("レビュー", "検証"):
                    if not any(
                        middle.phase == phase
                        and wi in middle.wis
                        and node.id in ancestors[middle.id]
                        and any(end.phase == "統合" and wi in end.wis and middle.id in ancestors[end.id] for end in nodes)
                        for middle in nodes
                    ):
                        issues.append(f"{node.id}: {wi}の{phase}を経た統合への経路がない")
        if node.phase in ("レビュー", "検証"):
            for wi in node.wis:
                if not any(end.phase == "統合" and wi in end.wis and node.id in ancestors[end.id] for end in nodes):
                    issues.append(f"{node.id}: {wi}の統合への経路がない")
        prior_phase = {"公開": "統合", "後始末": "公開", "報告": "後始末"}.get(node.phase)
        if prior_phase and any(prior.phase == prior_phase and prior.id not in ancestors[node.id] for prior in nodes):
            issues.append(f"{node.id}: 全{prior_phase}の完了を待つ経路がない")
        if node.phase == "実装" and wi_lanes is not None and prior_lanes is not None:
            for wi in node.wis:
                lane = wi_lanes.get(wi)
                prior_names = prior_lanes.get(lane, ()) if lane is not None else ()
                if any(
                    prior.phase == "統合"
                    and any(wi_lanes.get(member) in prior_names for member in prior.wis)
                    and prior.id not in ancestors[node.id]
                    for prior in nodes
                ):
                    issues.append(f"{node.id}: 先行レーンの全統合を待つ経路がない")
    return issues


def _schedule(
    nodes: list[_Node],
    order: tuple[str, ...],
    durations: dict[str, float],
    capacities: dict[str, int],
) -> tuple[float, dict[str, float], float]:
    by_id = {node.id: node for node in nodes}
    downstream = dict(durations)
    ends: dict[str, float] = {}
    for identifier in reversed(order):
        for prior in by_id[identifier].predecessors:
            downstream[prior] = max(downstream[prior], durations[prior] + downstream[identifier])
    for identifier in order:
        ends[identifier] = durations[identifier] + max((ends[p] for p in by_id[identifier].predecessors), default=0.0)
    dependency = max(ends.values(), default=0.0)
    pending = set(order)
    completed: dict[str, float] = {}
    running: dict[str, float] = {}
    wait: dict[str, float] = dict.fromkeys(_RESOURCES, 0.0)
    now = 0.0
    while pending or running:
        finished = [identifier for identifier, end in running.items() if end <= now]
        for identifier in finished:
            completed[identifier] = running.pop(identifier)
        ready = sorted(
            (identifier for identifier in pending if set(by_id[identifier].predecessors) <= completed.keys()),
            key=lambda identifier: (-downstream[identifier], identifier),
        )
        started = False
        for identifier in ready:
            node = by_id[identifier]
            if durations[identifier] == 0:
                completed[identifier] = now
            elif sum(by_id[item].resource == node.resource for item in running) < capacities[node.resource]:
                running[identifier] = now + durations[identifier]
                wait[node.resource] += now - max((completed[p] for p in node.predecessors), default=0.0)
            else:
                continue
            pending.remove(identifier)
            started = True
        if started:
            continue
        if running:
            now = min(running.values())
    return now, wait, dependency
