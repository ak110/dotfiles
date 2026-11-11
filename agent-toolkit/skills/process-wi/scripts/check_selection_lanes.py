"""レーンのモデル・段階・重なりと所要見込みの検査。"""

from __future__ import annotations

import collections.abc
import itertools
import pathlib
import re
import typing

import selection_contract
from check_selection_common import (
    _IMPLEMENTATION_SECONDS_KEY,
    _INTEGRATION_SECONDS_KEY,
    _LANE_NONE,
    _LEGACY_SECONDS_KEYS,
    Violation,
    _is_integrated,
    _string_list,
)
from check_selection_paths import _covers

from agent_toolkit._plan import selection as _selection

_OBSERVATION_PLAN_RE = re.compile(r"計画:\s*([^）]+)")


_ABSOLUTE_PLAN_RE = re.compile(r"(?<!\S)(/\S+?\.md)(?=$|[\s、。）])")


_NEXT_STAGED = "比較した段階案の秒数か`成立する段階案なし`を最上位の`段階案の完了見込み秒数`へ書く"


_NEXT_CORRECTION = (
    "最上位の`見込みの補正`へ実装係数・統合係数・共有待ち秒数・参照した記録・除外した記録を、"
    "各行へ`補正前の実装秒数`・`補正前の統合秒数`を書き、実装秒数・統合秒数を"
    "補正前の値×対応する工程の係数の1秒以内へそろえる。係数が1以外なら参照した記録を1件以上挙げる"
)


_NEXT_SHARED_WAIT = (
    "`見込みの補正`の`共有待ちの算定`を、過去の実行記録から測った値なら`実測`と参照した記録へ、"
    "測っていない値なら`推定`と、共有の検証枠・統合の順番の待ちを見積もった0より大きい秒数へ直し、根拠をレーンの`根拠`へ書く"
)


_NEXT_TARGET_OVERRUN = (
    "段階の組み直し、独立した準備の先行、準備と検証の集約などの配分の代替案を目標所要秒数との差で比べ、"
    "採った案と比べた見込みを最上位の`目標超過時の配分比較`へ書く。見込みの超過だけを理由に項目を外さず、レーン数の上限を保つ"
)


_NEXT_LANE_MODEL = "同じレーンの項目の`担当モデル`の`実装担当`を1つの値へそろえるか、指定の異なる項目を別のレーンへ割り当てる"


_NEXT_RESUME_PLAN = "同じ再開計画を指す項目を同じレーンへまとめる"


_NEXT_STAGE_SEQUENCE = "`レーンの所要時間`の`段階`を1から欠番なく連続する値へ直す"


_NEXT_PRIOR_MISSING = "段階2以上のレーンの`先行レーン`へ、開始前に統合する前段のレーンを挙げる"


_NEXT_PRIOR_INVALID = "`先行レーン`を、選定に存在し自レーンより前の段階にあるレーンへ直す"


_NEXT_OVERLAP_DUPLICATE = "`レーン間の重なり`の記録を共通パスごとに1件へ直す"


_NEXT_OVERLAP_SURPLUS = "`レーン間の重なり`の記録を、実際に`書込対象`を共有するレーンの組と共通パスへ直す"


_NEXT_OVERLAP_SAME_DEFINITION = "`判定`を`交わる`へ直すか、両レーンの定義を互いに独立した値へ直す"


_NEXT_OVERLAP_SAME_STAGE = "定義が交わる2つのレーンを同じレーンへまとめるか、`段階`と`先行レーン`で順に処理する"


_NEXT_OVERLAP_MISSING = "共通パスごとに双方の定義と判定を`レーン間の重なり`へ1件記録する"


_NEXT_PRIOR_UNFOUNDED = "`先行レーン`から、別段階の書込予定の重複とその連鎖に基づかないレーンを除く"


_NEXT_PRIOR_REQUIRED = "別段階の書込予定が重なる前段へ直接または推移的に到達するよう`先行レーン`を記録する"


def _check_estimate_records(
    selection: dict[str, object], costs: list[dict[str, object]], target_seconds: float | None = None
) -> list[Violation]:
    """段階案の比較と補正の記録を、所要時間の値へ対応付ける。

    `共有待ちの算定`を持つ選定では、未計測の待ちを0秒の推定にした組と、記録の無い実測を拒否し、
    目標所要秒数を超える見込みに配分の代替案の比較記録を求める。算定を持たない保存済みの選定は、
    この区別の前の形式として読み、これらの判定を適用しない。
    """
    errors: list[Violation] = []
    costs = [dict(row) for row in costs]
    for row in costs:
        for legacy, current in _LEGACY_SECONDS_KEYS.items():
            if current not in row and legacy in row:
                row[current] = row[legacy]
    if (
        costs
        and all(row.get(_selection.STAGE_KEY, 1) == 1 for row in costs)
        and any(typing.cast(float, row[_IMPLEMENTATION_SECONDS_KEY]) > 6000 for row in costs)
        and _selection.STAGED_ESTIMATE_KEY not in selection
    ):
        errors.append(Violation(f"6000秒超の単一段階案に`{_selection.STAGED_ESTIMATE_KEY}`がない", next_action=_NEXT_STAGED))
    correction = selection.get(_selection.ESTIMATE_CORRECTION_KEY)
    if not isinstance(correction, dict):
        return [*errors, Violation(f"`{_selection.ESTIMATE_CORRECTION_KEY}`がない", next_action=_NEXT_CORRECTION)]
    coefficients = _selection.correction_coefficients(correction)
    if any(value != 1 for value in coefficients) and not correction["参照した記録"]:
        errors.append(Violation("係数が1以外の`見込みの補正`に参照した記録がない", next_action=_NEXT_CORRECTION))
    for row in costs:
        for (corrected, raw), coefficient in zip(
            (
                (_IMPLEMENTATION_SECONDS_KEY, _selection.UNCORRECTED_IMPLEMENTATION_KEY),
                (_INTEGRATION_SECONDS_KEY, _selection.UNCORRECTED_INTEGRATION_KEY),
            ),
            coefficients,
            strict=True,
        ):
            if raw not in row or abs(typing.cast(float, row[corrected]) - typing.cast(float, row[raw]) * coefficient) > 1:
                errors.append(
                    Violation(
                        f"{row[_selection.LANE_KEY]}: `{raw}`の欠落または`{corrected}`と補正の積の不一致: "
                        f"補正前={row.get(raw)!r}、係数={coefficient}、補正後={row[corrected]}",
                        next_action=_NEXT_CORRECTION,
                    )
                )
    if _selection.SHARED_WAIT_SECONDS_KEY in correction and not selection_contract.is_non_negative_number(
        _completion_estimate(costs, typing.cast(float, correction[_selection.SHARED_WAIT_SECONDS_KEY]))
    ):
        errors.append(Violation("全体の完了見込み秒数が有限数ではない", next_action=_NEXT_CORRECTION))
    basis = correction.get(_selection.SHARED_WAIT_BASIS_KEY)
    if basis is None:
        return errors
    wait = typing.cast(float, correction[_selection.SHARED_WAIT_SECONDS_KEY])
    if basis == _selection.SHARED_WAIT_ESTIMATED and wait == 0:
        errors.append(Violation("未計測の共有待ちを0秒と推定している", next_action=_NEXT_SHARED_WAIT))
    if basis == _selection.SHARED_WAIT_MEASURED and not correction["参照した記録"]:
        errors.append(Violation("実測とした共有待ちに参照した記録がない", next_action=_NEXT_SHARED_WAIT))
    if (
        target_seconds is not None
        and _completion_estimate(costs, wait) > target_seconds
        and _selection.TARGET_OVERRUN_COMPARISON_KEY not in selection
    ):
        estimate = _completion_estimate(costs, wait)
        # 違反時は要約を出さずに終了するため、組み直しの規模を判断できるよう差もここで示す。
        errors.append(
            Violation(
                f"全体の完了見込み秒数{estimate}が目標所要秒数{target_seconds}を超える"
                f"（差 {estimate - target_seconds}）のに`{_selection.TARGET_OVERRUN_COMPARISON_KEY}`がない",
                next_action=_NEXT_TARGET_OVERRUN,
            )
        )
    return errors


def _completion_estimate(costs: list[dict[str, object]], shared_wait: float) -> float:
    """段階ごとの最長実装と直列統合の和へ、全体の共有待ちを一度加える。"""
    stages: dict[int, tuple[float, float]] = {}
    for row in costs:
        stage = typing.cast(int, row.get(_selection.STAGE_KEY, 1))
        longest, integration = stages.get(stage, (0, 0))
        seconds = [typing.cast(float, row.get(key, row.get(legacy))) for legacy, key in _LEGACY_SECONDS_KEYS.items()]
        stages[stage] = max(longest, seconds[0]), integration + seconds[1]
    return sum(longest + integration for longest, integration in stages.values()) + shared_wait


def _check_lane_models(items: list[dict[str, object]]) -> list[Violation]:
    """同一レーンで両立しない実装モデル指定を拒否する。"""
    assigned: dict[str, tuple[str, str]] = {}
    errors: list[Violation] = []
    for item in items:
        models = typing.cast(dict[str, str], item.get(_selection.MODEL_TYPES_KEY, {}))
        lane = item.get(_selection.LANE_KEY)
        model = models.get("実装担当")
        if not isinstance(lane, str) or lane == _LANE_NONE or model is None:
            continue
        previous = assigned.setdefault(lane, (str(item[_selection.WI_KEY]), model))
        if previous[1] != model:
            errors.append(
                Violation(
                    f"{lane}: 実装担当のモデル指定が衝突: {previous[0]}={previous[1]}、{item[_selection.WI_KEY]}={model}",
                    next_action=_NEXT_LANE_MODEL,
                )
            )
    return errors


def _resume_plan_id(value: object) -> str | None:
    """メインが出力した再開位置から同一計画を表す値だけを正規化して返す。"""
    if not isinstance(value, str) or not value.strip() or value.strip() in {_LANE_NONE, "計画なし"}:
        return None
    observation = _OBSERVATION_PLAN_RE.search(value)
    candidate = observation.group(1).strip() if observation is not None else None
    if candidate == "計画なし":
        return None
    if candidate is None:
        matched = _ABSOLUTE_PLAN_RE.search(value)
        candidate = matched.group(1) if matched is not None else None
    if candidate is None:
        return None
    # 通常中断の`~/.claude/plans`と観測のみの`private-notes/plans/`は親ディレクトリが異なるため、
    # 両方が保持する計画ファイル名を同一計画の識別値にする。
    return pathlib.PurePosixPath(candidate).name


def _check_resume_plan_lanes(items: list[dict[str, object]]) -> list[Violation]:
    """同じ再開計画を指す項目が複数レーンへ分かれた選定を拒否する。"""
    assigned: dict[str, tuple[str, str]] = {}
    errors: list[Violation] = []
    for item in items:
        lane = item.get(_selection.LANE_KEY)
        plan = _resume_plan_id(item.get("再開位置"))
        if not isinstance(lane, str) or lane == _LANE_NONE or plan is None:
            continue
        awi = str(item[_selection.WI_KEY])
        previous = assigned.setdefault(plan, (awi, lane))
        if previous[1] != lane:
            errors.append(
                Violation(
                    f"同じ再開計画を別レーンへ割り当てている: {plan}: {previous[0]}（{previous[1]}）と{awi}（{lane}）",
                    next_action=_NEXT_RESUME_PLAN,
                )
            )
    return errors


def _check_lane_stages(items: collections.abc.Sequence[object], costs: collections.abc.Sequence[object]) -> list[Violation]:
    """後段の開始条件が先行レーンの統合順序を守るか確かめる。"""
    lanes = {
        item[_selection.LANE_KEY]
        for item in items
        if isinstance(item, dict) and isinstance(item.get(_selection.LANE_KEY), str) and item[_selection.LANE_KEY] != _LANE_NONE
    }
    rows: dict[str, tuple[int, list[str]]] = {}
    integrated = _selection.integrated_lanes({_selection.LANE_COSTS_KEY: list(costs)})
    errors: list[Violation] = []
    for row in costs:
        if not isinstance(row, dict) or not isinstance(row.get(_selection.LANE_KEY), str):
            continue
        lane = row[_selection.LANE_KEY]
        rows[lane] = (
            typing.cast(int, row.get(_selection.STAGE_KEY, 1)),
            typing.cast(list[str], row.get(_selection.PRIOR_LANES_KEY, [])),
        )
    last_pending_stage = max((stage for lane, (stage, _) in rows.items() if lane not in integrated), default=0)
    # 元配分の完了した前段は残し、完了済みの後段だけを新たな待機条件から外す。
    ordered_stages = sorted({stage for stage, _ in rows.values() if stage <= last_pending_stage})
    for expected, stage in enumerate(ordered_stages, start=1):
        if stage != expected:
            errors.append(Violation(f"段階が1から連続しない: 段階{expected}がない", next_action=_NEXT_STAGE_SEQUENCE))
            break
    for lane, (stage, prior) in rows.items():
        if lane not in integrated and stage > 1 and not prior:
            errors.append(Violation(f"{lane}: 段階{stage}の後段に先行レーンがない", next_action=_NEXT_PRIOR_MISSING))
        for predecessor in prior:
            if (
                predecessor not in lanes
                or predecessor not in rows
                or (lane not in integrated and predecessor not in integrated and rows[predecessor][0] >= stage)
            ):
                errors.append(
                    Violation(
                        f"{lane}: 先行レーン{predecessor}が選定に無いか、前の段階のレーンではない",
                        next_action=_NEXT_PRIOR_INVALID,
                    )
                )
    return errors


def _check_lane_overlaps(
    items: collections.abc.Sequence[object],
    costs: collections.abc.Sequence[object],
    records: list[dict[str, object]],
    integrated: set[str],
) -> list[Violation]:
    """同段階の独立性と、別段階の重複から導く先行関係を確かめる。"""
    stages = {
        row[_selection.LANE_KEY]: (row.get(_selection.STAGE_KEY, 1), row.get(_selection.PRIOR_LANES_KEY, []))
        for row in costs
        if isinstance(row, dict)
        and isinstance(row.get(_selection.LANE_KEY), str)
        and isinstance(row.get(_selection.STAGE_KEY, 1), int)
        and isinstance(row.get(_selection.PRIOR_LANES_KEY, []), list)
    }
    assigned = [
        item
        for item in items
        if isinstance(item, dict) and isinstance(item.get(_selection.LANE_KEY), str) and item[_selection.LANE_KEY] != _LANE_NONE
    ]
    expected: set[tuple[str, str, str]] = set()
    shared: set[tuple[str, str, str]] = set()
    dependencies: dict[str, set[str]] = {}
    for left, right in itertools.combinations(assigned, 2):
        lanes = sorted((left[_selection.LANE_KEY], right[_selection.LANE_KEY]))
        if lanes[0] == lanes[1]:
            continue
        overlaps = {
            second if _covers(first, second) else first
            for first in _string_list(left, _selection.WRITE_FILES_KEY)
            for second in _string_list(right, _selection.WRITE_FILES_KEY)
            if _covers(first, second) or _covers(second, first)
        }
        shared.update((lanes[0], lanes[1], path) for path in overlaps)
        if _is_integrated(left, integrated) or _is_integrated(right, integrated) or not overlaps:
            continue
        left_stage, right_stage = (stages.get(lane, (1, []))[0] for lane in lanes)
        if left_stage == right_stage:
            expected.update((lanes[0], lanes[1], path) for path in overlaps)
        else:
            earlier, later = lanes if left_stage < right_stage else reversed(lanes)
            dependencies.setdefault(later, set()).add(earlier)
    errors: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        left, right = sorted((str(record["レーン1"]), str(record["レーン2"])))
        path = str(record["共通パス"])
        key = (left, right, path)
        label = f"{left}と{right}: {path}"
        if key in seen:
            errors.append(Violation(f"{label}: 重なりの記録が重複", next_action=_NEXT_OVERLAP_DUPLICATE))
        seen.add(key)
        if key not in shared:
            errors.append(Violation(f"{label}: 余剰の重なりの記録", next_action=_NEXT_OVERLAP_SURPLUS))
            continue
        if key not in expected:
            # 保存済みの詳細記録は履歴として読めるが、直列化の根拠は現在の書込予定から導く。
            continue
        if record["判定"] == "交わらない":
            first = typing.cast(list[str], record["レーン1の定義"])
            second = typing.cast(list[str], record["レーン2の定義"])
            if {value.strip() for value in first} & {value.strip() for value in second}:
                errors.append(Violation(f"{label}: 交わらない判定の定義が同一", next_action=_NEXT_OVERLAP_SAME_DEFINITION))
            continue
        errors.append(Violation(f"{label}: 同じ段階で定義が交わる", next_action=_NEXT_OVERLAP_SAME_STAGE))
    for left, right, path in sorted(expected - seen):
        errors.append(Violation(f"{left}と{right}: 重複パスの記録不足: {path}", next_action=_NEXT_OVERLAP_MISSING))
    for lane, (_, prior) in stages.items():
        if lane in integrated:
            continue
        for predecessor in prior:
            if predecessor in integrated:
                continue
            if not _reachable(lane, predecessor, dependencies):
                errors.append(Violation(f"{lane}: 根拠の無い直列化: {predecessor}", next_action=_NEXT_PRIOR_UNFOUNDED))
    actual = {lane: set(prior) for lane, (_, prior) in stages.items()}
    for lane, predecessors in dependencies.items():
        for predecessor in predecessors:
            if not _reachable(lane, predecessor, actual):
                errors.append(
                    Violation(f"{lane}: 交わる先行レーン{predecessor}への依存がない", next_action=_NEXT_PRIOR_REQUIRED)
                )
    return errors


def _reachable(lane: str, target: str, dependencies: dict[str, set[str]]) -> bool:
    """`lane`から先行関係を直接または推移的にたどって`target`へ到達するかを返す。循環があっても探索は停止する。"""
    pending = list(dependencies.get(lane, ()))
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current not in seen:
            seen.add(current)
            pending.extend(dependencies.get(current, ()))
    return False
