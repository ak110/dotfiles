"""規範の重複しない累積と、公開hookを通す1回のeffort案内を検証する。"""

import json

import pytest

from agent_toolkit._common import session_registry, session_state
from agent_toolkit._hooks import document_reads, effort_notice, posttooluse


def _receipt(*, version="v1", start=1, end=3, sizes=(4, 6, 10), actor="main"):
    metadata = {
        "logical": "norm",
        "version": version,
        "heading": "",
        "path": "/norm.md",
        "ranges": [[start, end]],
        "line_chars": list(sizes),
        "parts": [],
    }
    return {document_reads.receipt_key(metadata): [actor]}


def test_cumulative_range_union_survives_compact_and_versions():
    state = {"operation_skill_delivered_agents": _receipt(end=2)}
    assert effort_notice.accumulate(state, "main") == 10
    state["operation_skill_delivered_agents"] = _receipt(start=2)
    assert effort_notice.accumulate(state, "main") == 20
    state["operation_skill_delivered_agents"] = {}
    assert effort_notice.accumulate(state, "main") == 20
    state["operation_skill_delivered_agents"] = _receipt()
    assert effort_notice.accumulate(state, "main") == 20
    state["operation_skill_delivered_agents"] = _receipt(version="v2")
    assert effort_notice.accumulate(state, "main") == 40
    state["operation_skill_delivered_agents"] = _receipt(version="v3", actor="child")
    assert effort_notice.accumulate(state, "main") == 40


@pytest.mark.parametrize(
    ("launch_kind", "model_type", "level", "notified"),
    [
        ("delegate", "review", "low", True),
        ("delegate", "orchestrate", "low", False),
        ("write", "write", "low", False),
        ("delegate", "review", "max", False),
    ],
)
def test_public_posttooluse_threshold_once(capsys, launch_kind, model_type, level, notified):
    session_registry.publish(
        "volume-child",
        state=session_registry.PublicationState.PENDING_RUNNING,
        engine="claude",
        cwd="/work",
        model="opus",
        effort=level,
        model_type=model_type,
        launch_kind=launch_kind,
    )
    assert session_state.update_state(
        "volume-child",
        lambda state: {
            **state,
            "operation_skill_delivered_agents": _receipt(sizes=(effort_notice.THRESHOLD, 0, 0)),
        },
    )
    payload = {"session_id": "volume-child", "tool_name": "Noop", "tool_input": {}, "hook_event_name": "PostToolUse"}
    assert posttooluse.main(json.dumps(payload)) == 0
    assert "atk agents effort up" not in capsys.readouterr().out
    assert session_state.update_state(
        "volume-child",
        lambda state: {
            **state,
            "operation_skill_delivered_agents": _receipt(version="v2", sizes=(1, 0, 0)),
        },
    )
    assert posttooluse.main(json.dumps(payload)) == 0
    assert ("atk agents effort up" in capsys.readouterr().out) is notified
    assert posttooluse.main(json.dumps(payload)) == 0
    assert "atk agents effort up" not in capsys.readouterr().out
