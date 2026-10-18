"""live testの入力が要求するツールを、起動区分ごとのClaude起動設定と比べる課金不要の契約テスト。

live testは通常のpytestから`live_agents`で除外されるため、入力と起動設定の不整合を実backendなしで検出する。
許可ツールの一覧は`claude._build_options`が生成するSDKオプションから読み、テスト側へ複製しない。
"""

import re

import pytest

from agent_toolkit import agents_server_live_test as live
from agent_toolkit._agents_server import claude, claude_settings
from agent_toolkit._agents_server.state import LaunchKind

# 新規起動と保存済みsessionの再開（継続）の両起動条件。
_SESSION_IDS = [None, "saved-session"]
_LIVE_INPUTS = [
    *(pytest.param(value, id=f"background-{mode}") for mode, value in live.BACKGROUND_RESUME_INPUTS.items()),
    pytest.param(live.EXPLORE_READ_INPUT, id="explore-read"),
    pytest.param(live.GRANDCHILD_INPUT, id="grandchild"),
]


def _unavailable_tools(launch_kind: LaunchKind, session_id: str | None, required: frozenset[str]) -> set[str]:
    """起動設定が公開しないか許可しないツールのうち、入力が要求するものを返す。

    `tools`が一覧の場合はその一覧、組込presetの場合は`allowed_tools`を許可された集合とし、
    いずれも指定されない通常起動は組込ツールを制限しないものとして扱う。
    """
    options = claude._build_options("/repo", "model", "medium", session_id, launch_kind)  # pylint: disable=protected-access
    try:
        available: set[str] | None
        if isinstance(options.tools, list):
            available = set(options.tools)
        elif options.allowed_tools:
            available = set(options.allowed_tools)
        else:
            available = None
        missing = set(required) & set(options.disallowed_tools)
        if available is not None:
            missing |= set(required) - available
        return missing
    finally:
        claude_settings.cleanup_settings(options)


@pytest.mark.parametrize("session_id", _SESSION_IDS)
@pytest.mark.parametrize("live_input", _LIVE_INPUTS)
@pytest.mark.usefixtures("agents_server_isolation")
def test_live_inputs_require_only_tools_the_launch_exposes(live_input: live.LiveInput, session_id: str | None) -> None:
    """live testが起動区分へ渡す入力は、その起動区分が新規起動と再開で公開するツールだけを要求する。"""
    assert not _unavailable_tools(live_input.launch_kind, session_id, live_input.required_tools)


@pytest.mark.parametrize("live_input", _LIVE_INPUTS)
def test_live_input_texts_name_only_declared_tools(live_input: live.LiveInput) -> None:
    """入力本文が名指しする組込ツールは、宣言した要求ツールに含まれる。

    本文だけを変えて能力外の操作を要求した場合も、宣言との不一致として失敗させる。
    """
    named = {name for text in live_input.texts for name in re.findall(r"([A-Z][A-Za-z]*)ツール", text)}
    assert named <= live_input.required_tools


@pytest.mark.parametrize("session_id", _SESSION_IDS)
@pytest.mark.usefixtures("agents_server_isolation")
def test_explore_exposes_tools_for_background_resume_input(session_id: str | None) -> None:
    """探索も読取用のBash背景処理に必要なツールを、新規起動と再開の両方で公開する。"""
    required = live.BACKGROUND_RESUME_INPUTS["delegate"].required_tools
    assert not _unavailable_tools("explore", session_id, required)
