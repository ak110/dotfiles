"""全工程の静的見込みと入力境界を、小さい合成DAGで検証する。"""

from __future__ import annotations

import copy
import typing

import pytest

from agent_toolkit._plan import selection_estimate


def _node(
    identifier: str,
    phase: str,
    seconds: float | None,
    priors: list[str],
    wis: list[str] | None = None,
    resource: str = "モデル",
) -> dict[str, typing.Any]:
    return {
        "ID": identifier,
        "工程": phase,
        "WI": wis or [],
        "先行工程": priors,
        "資源": resource,
        "秒数": seconds,
        "算定": "実測",
        "根拠": "計測記録",
        "未確認前提": [],
    }


def _plan(durations: tuple[float, ...] = (10, 10), capacity: int = 2) -> dict[str, typing.Any]:
    nodes = [_node("selection", "選定", 1, [])]
    integrations = []
    for index, duration in enumerate(durations):
        wi = [f"wi{index}"]
        implementation, review, verification, integration = [f"{phase}{index}" for phase in ("i", "r", "v", "m")]
        nodes.extend(
            [
                _node(implementation, "実装", duration, ["selection"], wi),
                _node(review, "レビュー", 0, [implementation], wi),
                _node(verification, "検証", 0, [implementation], wi, "検証"),
                _node(integration, "統合", 0, [review, verification], wi, "直列"),
            ]
        )
        integrations.append(integration)
    nodes.extend(
        [
            _node("publish", "公開", 1, integrations),
            _node("cleanup", "後始末", 1, ["publish"]),
            _node("report", "報告", 1, ["cleanup"]),
        ]
    )
    return {
        "並列数": {
            "モデル同時数": capacity,
            "資源上限": 8,
            "サービス上限": 8,
            "根拠": {"資源": "実測", "サービス": "契約", "費用": "比較記録"},
        },
        "工程": nodes,
    }


def _row(plan: dict[str, typing.Any], identifier: str) -> dict[str, typing.Any]:
    return next(row for row in plan["工程"] if row["ID"] == identifier)


@pytest.mark.parametrize(("capacity", "expected", "waiting"), [(2, 14, 0), (1, 24, 10)])
def test_independent_and_serial_groups(capacity: int, expected: float, waiting: float) -> None:
    result = selection_estimate.forecast(_plan(capacity=capacity))
    assert not result.issues
    assert result.total_seconds == result.normal_seconds == expected
    assert result.dependency_seconds == 14
    assert result.resource_work_seconds == {"モデル": 24, "検証": 0, "直列": 0}
    assert result.resource_wait_seconds["モデル"] == waiting


def test_three_verifications_share_two_slots() -> None:
    plan = _plan((0, 0, 0), 3)
    for index in range(3):
        _row(plan, f"v{index}")["秒数"] = 10
    result = selection_estimate.forecast(plan)
    assert not result.issues
    assert result.total_seconds == 24
    assert result.dependency_seconds == 14
    assert result.resource_work_seconds["検証"] == 30
    assert result.resource_wait_seconds["検証"] == 10


def test_dependency_model_verification_review_and_serial_integrations() -> None:
    plan = _plan((10, 10))
    for index in range(2):
        _row(plan, f"v{index}")["秒数"] = 5
        _row(plan, f"r{index}").update({"先行工程": [f"v{index}"], "秒数": 3})
        _row(plan, f"m{index}")["秒数"] = 2
    result = selection_estimate.forecast(plan)
    assert not result.issues
    assert result.total_seconds == 26
    assert result.dependency_seconds == 24
    assert result.resource_wait_seconds["直列"] == 2


def test_accumulated_duration_must_remain_finite() -> None:
    result = selection_estimate.forecast(_plan((1e308, 1e308)))
    assert result.total_seconds is result.normal_seconds is None
    assert result.issues


def test_zero_duration_does_not_wait_for_busy_resource() -> None:
    plan = _plan((20, 0), 1)
    _row(plan, "v1")["秒数"] = 30
    result = selection_estimate.forecast(plan)
    assert result.total_seconds == 34
    assert result.resource_wait_seconds["モデル"] == 0


@pytest.mark.parametrize("basis", ["条件付き試算", "未確認"])
def test_numeric_unconfirmed_basis_retains_issue(basis: str) -> None:
    plan = _plan()
    _row(plan, "publish")["算定"] = basis
    result = selection_estimate.forecast(plan)
    assert result.total_seconds == 14
    assert any("publish" in issue for issue in result.issues)


def test_missing_duration_never_becomes_zero() -> None:
    plan = _plan()
    _row(plan, "cleanup").update({"秒数": None, "算定": "未確認"})
    result = selection_estimate.forecast(plan)
    assert result.total_seconds is result.normal_seconds is result.dependency_seconds is None
    assert result.issues
    assert "モデル" not in result.resource_work_seconds
    assert result.resource_work_seconds == {"検証": 0, "直列": 0}
    assert not result.resource_wait_seconds


def test_unknown_assumptions_and_remaining_rate() -> None:
    plan = _plan()
    _row(plan, "i0").update({"算定": "残量/速度", "残量": 21, "速度": 2, "未確認前提": ["速度の継続"]})
    result = selection_estimate.forecast(plan)
    assert result.total_seconds == 14
    assert result.issues
    _row(plan, "i0")["残量"] = 24
    assert selection_estimate.structure_errors(plan)


@pytest.mark.parametrize("approval", [None, "承認", "拒否", "未回答", "別WI"])
def test_giant_approval_and_parallel_normal_projection(approval: str | None) -> None:
    plan = _plan((40000, 4000))
    giant: dict[str, typing.Any] = {"WI": "wi0", "巨大判定根拠": "資源上限と残量の記録"}
    if approval is not None:
        giant["承認元"] = {
            "対象WI": "wi1" if approval == "別WI" else "wi0",
            "判断": "承認" if approval == "別WI" else approval,
            "出所": "今回の回答",
        }
    plan["巨大WI"] = [giant]
    result = selection_estimate.forecast(plan)
    assert result.total_seconds == 40004
    assert result.normal_seconds == (4004 if approval == "承認" else 40004)
    assert result.excluded_wis == (["wi0"] if approval == "承認" else [])
    assert bool(result.issues) == (approval == "別WI")


def test_giant_projection_retains_shared_and_fixed_work() -> None:
    plan = _plan((40, 4))
    _row(plan, "i0")["WI"] = ["wi0", "wi1"]
    plan["巨大WI"] = [{"WI": "wi0", "巨大判定根拠": "記録", "承認元": {"対象WI": "wi0", "判断": "承認", "出所": "回答"}}]
    result = selection_estimate.forecast(plan)
    assert result.total_seconds == result.normal_seconds == 44


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("秒数", True),
        ("秒数", -1),
        ("秒数", float("nan")),
        ("秒数", float("inf")),
        ("秒数", "10"),
        ("ID", " "),
        ("WI", ["wi0", "wi0"]),
        ("WI", "wi0"),
        ("先行工程", ["i0"]),
        ("先行工程", ["absent"]),
        ("工程", []),
        ("資源", "CPU"),
        ("根拠", ""),
        ("算定", "速度"),
        ("未確認前提", [""]),
        ("未知", 1),
    ],
)
def test_invalid_node_fields(key: str, value: object) -> None:
    plan = _plan()
    _row(plan, "i0")[key] = value
    assert selection_estimate.structure_errors(plan)
    with pytest.raises(ValueError):
        selection_estimate.forecast(plan)


@pytest.mark.parametrize(
    ("identifier", "resource"),
    [("i0", "直列"), ("i0", "検証"), ("r0", "直列"), ("r0", "検証"), ("publish", "検証")],
)
def test_resource_cannot_replace_model_capacity(identifier: str, resource: str) -> None:
    plan = _plan()
    _row(plan, identifier)["資源"] = resource
    assert selection_estimate.structure_errors(plan)


@pytest.mark.parametrize("key", ["モデル同時数", "資源上限", "サービス上限"])
@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "2"])
def test_invalid_concurrency(key: str, value: object) -> None:
    plan = _plan()
    plan["並列数"][key] = value
    assert selection_estimate.structure_errors(plan)


@pytest.mark.parametrize("key", ["資源", "サービス", "費用"])
def test_concurrency_evidence_is_required(key: str) -> None:
    plan = _plan()
    plan["並列数"]["根拠"][key] = " "
    assert selection_estimate.structure_errors(plan)


def test_concurrency_cannot_exceed_either_limit() -> None:
    for key in ("資源上限", "サービス上限"):
        plan = _plan(capacity=3)
        plan["並列数"][key] = 2
        assert selection_estimate.structure_errors(plan)


def test_cycle_and_duplicate_ids() -> None:
    plan = _plan()
    _row(plan, "selection")["先行工程"] = ["report"]
    assert any("循環" in error for error in selection_estimate.structure_errors(plan))
    plan = _plan()
    plan["工程"].append(copy.deepcopy(plan["工程"][0]))
    assert any("重複" in error for error in selection_estimate.structure_errors(plan))


@pytest.mark.parametrize(
    "mutation",
    ["top", "empty", "resource", "rate", "extra-rate", "giant-duplicate", "giant-unknown", "approval"],
)
def test_other_structural_errors(mutation: str) -> None:
    plan = _plan()
    if mutation == "top":
        plan["unknown"] = 1
    elif mutation == "empty":
        plan["工程"] = []
    elif mutation == "resource":
        _row(plan, "v0")["資源"] = "モデル"
    elif mutation == "rate":
        _row(plan, "i0").update({"算定": "残量/速度", "残量": 10, "速度": 0})
    elif mutation == "extra-rate":
        _row(plan, "i0")["残量"] = 10
    elif mutation == "approval":
        plan["巨大WI"] = [{"WI": "wi0", "巨大判定根拠": "記録", "承認元": {}}]
    else:
        plan["巨大WI"] = [{"WI": "wi0" if mutation == "giant-duplicate" else "other", "巨大判定根拠": "記録"}] * 2
    assert selection_estimate.structure_errors(plan)


def test_target_and_prior_lane_paths() -> None:
    plan = _plan()
    lanes = {"wi0": "lane0", "wi1": "lane1"}
    assert not selection_estimate.forecast(plan, lanes).issues
    assert selection_estimate.forecast(plan, {"other": "lane0"}).issues
    assert selection_estimate.forecast(plan, lanes, {"lane1": ["lane0"]}).issues
    _row(plan, "i1")["先行工程"].append("m0")
    assert not selection_estimate.forecast(plan, lanes, {"lane1": ["lane0"]}).issues


@pytest.mark.parametrize("identifier", ["i0", "r0", "v0", "m0", "publish", "cleanup", "report"])
def test_missing_completion_paths_are_issues(identifier: str) -> None:
    plan = _plan()
    _row(plan, identifier)["先行工程"] = []
    assert not selection_estimate.structure_errors(plan)
    assert selection_estimate.forecast(plan).issues


def test_independent_groups_can_review_before_other_implementation_finishes() -> None:
    plan = _plan((1, 100), 2)
    _row(plan, "r0")["秒数"] = 10
    assert not selection_estimate.forecast(plan).issues
    assert selection_estimate.forecast(plan).total_seconds == 104


def test_id_tie_break_and_input_order_independence() -> None:
    plan = _plan((10, 10, 10), 1)
    result = selection_estimate.forecast(plan)
    plan["工程"].reverse()
    assert selection_estimate.forecast(plan) == result


@pytest.mark.parametrize(("verification_seconds", "expected"), [(20, 26), (8, 16)])
def test_downstream_priority_and_id_tie_break(verification_seconds: float, expected: float) -> None:
    plan = _plan((2, 10), 1)
    _row(plan, "v0")["秒数"] = verification_seconds
    result = selection_estimate.forecast(plan)
    assert not result.issues
    assert result.total_seconds == expected


def test_multiple_implementations_need_their_own_review_paths() -> None:
    plan = _plan((1, 100))
    _row(plan, "i1")["WI"] = ["wi0"]
    for identifier in ("r1", "v1", "m1"):
        _row(plan, identifier)["WI"] = ["wi0"]
    assert not selection_estimate.forecast(plan).issues
    _row(plan, "r1")["先行工程"] = ["i0"]
    assert selection_estimate.forecast(plan).issues


@pytest.mark.parametrize("phase", ["選定", "実装", "レビュー", "検証", "統合", "公開", "後始末", "報告"])
def test_all_required_phases_are_checked(phase: str) -> None:
    plan = _plan()
    for row in plan["工程"]:
        if row["工程"] == phase:
            row["工程"] = "準備"
            row["資源"] = "モデル"
    assert not selection_estimate.structure_errors(plan)
    assert selection_estimate.forecast(plan).issues
