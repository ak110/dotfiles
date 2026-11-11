"""background_tasksの契約テスト。"""

import pathlib

import pytest

from agent_toolkit._hooks.background_tasks import is_pending_async_work, pending_async_task_ids
from agent_toolkit._testing.background_tasks_support import (
    _assistant_entry,
    _bash_no_bg,
    _host_mcp_notice,
    _user_async_launched_entry,
    _user_entry,
    _user_task_notification_entry,
)
from agent_toolkit._testing.helpers import _write_transcript

_BACKGROUND_TASKS_OMITTED = object()


_QUOTED_NOTICE_PHRASES = (
    "moved to the background as task {task_id} and keeps running",
    "Command timed out and is now running in the background. ID: {task_id}",
    "Command did not complete within its 15s timeout and was moved to the background (ID: {task_id}).",
)


_TEXT = "作業途中です。"


class TestIsPendingAsyncWork:
    """`is_pending_async_work` の判定を網羅するテスト。

    tool_use 種別 × {Agent / ScheduleWakeup / CronCreate / Monitor / Bash背景 / Bash前景 / その他 / なし}
    と未完了background task（Agent・Bash双方）× {なし / 起動のみ / 起動と通知ペア} の
    同値分割で組み合わせを検証する。

    SendMessage背景再開テスト群は以下の観点を網羅する。

    - SendMessage呼び出しと背景再開tool_resultが存在する場合に`True`を返す（誤発動防止のコア）
    - 現行形式（`toolUseResult.resumedAgentId`を持ち本文に旧マーカーを含まない）でも`True`を返し、
      同`tool_use_id`の完了通知で`False`へ相殺される
    - `resumedAgentId`を持たない旧形式は本文マーカーとの一致判定へフォールバックして`True`を返す（後方互換）
    - `resumedAgentId`が非文字列の場合は構造化フィールド判定が成立せず本文マーカーの有無で決まる
    - 現行形式でもSendMessage呼び出し由来でない`tool_use_id`は加算しない（誤検知防止）
    - 同`tool_use_id`の旧形式完了通知（user textブロック内`<task-notification>`）で`False`へ相殺される
    - 同`tool_use_id`の新形式完了通知（`type=="attachment"`・`commandMode=="task-notification"`）でも`False`へ相殺される
    - tool_result contentが文字列形式でも`True`を返す（content形式バリエーション）
    - マーカーを含まない同期SendMessage tool_resultのみの場合は`False`を返す（過剰抑止防止）
    - SendMessage呼び出しなしでマーカー文字列が他ツール出力に含まれる場合は`False`を返す（誤検知防止）
    - sidechain内のSendMessage呼び出し・背景再開tool_resultは対象外で`False`を返す
    """

    @pytest.mark.parametrize(
        ("tool_block", "expected"),
        [
            ({"type": "tool_use", "id": "x", "name": "Agent", "input": {}}, True),
            ({"type": "tool_use", "id": "x", "name": "ScheduleWakeup", "input": {}}, True),
            pytest.param(
                {"type": "tool_use", "id": "x", "name": "CronCreate", "input": {}},
                True,
                id="CronCreate",
            ),
            ({"type": "tool_use", "id": "x", "name": "Monitor", "input": {}}, True),
            (
                {
                    "type": "tool_use",
                    "id": "x",
                    "name": "Bash",
                    "input": {"command": "x", "run_in_background": True},
                },
                True,
            ),
            (
                {
                    "type": "tool_use",
                    "id": "x",
                    "name": "Bash",
                    "input": {"command": "x", "run_in_background": False},
                },
                False,
            ),
            (
                {"type": "tool_use", "id": "x", "name": "Read", "input": {"file_path": "/tmp/x"}},
                False,
            ),
            (None, False),
        ],
    )
    def test_tool_use_kinds(self, tmp_path: pathlib.Path, tool_block: dict | None, expected: bool):
        content: list[dict] = [{"type": "text", "text": _TEXT}]
        if tool_block is not None:
            content.append(tool_block)
        t = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry(content)])
        assert is_pending_async_work(str(t), "") is expected

    @pytest.mark.parametrize(
        ("pending_entries", "expected"),
        [
            ([], False),
            ([_user_async_launched_entry("toolu_bg1")], True),
            (
                [
                    _user_async_launched_entry("toolu_bg1"),
                    _user_task_notification_entry("toolu_bg1"),
                ],
                False,
            ),
            (
                [
                    _user_async_launched_entry("toolu_bg1"),
                    _user_async_launched_entry("toolu_bg2"),
                    _user_task_notification_entry("toolu_bg1"),
                ],
                True,
            ),
        ],
    )
    def test_pending_background_agent(self, tmp_path: pathlib.Path, pending_entries: list[dict], expected: bool):
        """直前ターンの最後のtool_useはRead（非同期でない）。

        background Agentの起動・通知の有無のみで結果が決まることを検証する。
        """
        entries: list[dict] = [_user_entry("hello")]
        entries.extend(pending_entries)
        entries.append(_user_entry("続きをお願いします"))
        entries.append(
            _assistant_entry(
                [
                    {"type": "text", "text": _TEXT},
                    {"type": "tool_use", "id": "x", "name": "Read", "input": {"file_path": "/tmp/x"}},
                ]
            )
        )
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is expected

    @pytest.mark.parametrize(
        ("background_tasks", "expected"),
        [
            ([], False),
            ([{"type": "teammate", "id": "team-1"}], False),
            ([{"type": "subagent", "status": "completed"}], True),
            ([{"type": "shell"}, {"type": "monitor"}, {"type": "workflow"}], True),
            ([{"type": "cloud session"}, {"type": "MCP task"}, {"type": "future-type"}], True),
            ([{"type": "teammate"}, {"type": "subagent"}], True),
            ([{}, {"type": ""}, {"type": 1}, "subagent", None], False),
            ({"type": "subagent"}, False),
        ],
        ids=[
            "empty",
            "teammate-only",
            "subagent-status-ignored",
            "known-types",
            "unknown-types",
            "mixed",
            "invalid-elements",
            "non-list",
        ],
    )
    def test_background_tasks_are_used_as_current_pending_state(
        self, tmp_path: pathlib.Path, background_tasks: object, expected: bool
    ) -> None:
        """有効な非`teammate` taskだけをStop時点の未完了根拠として扱う。"""
        transcript = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _TEXT}])])
        assert is_pending_async_work(str(transcript), "", background_tasks=background_tasks) is expected

    @pytest.mark.parametrize(
        ("record", "owner_agent_id", "expected"),
        [
            ({"pending_observation": True, "owner_agent_id": "main"}, "main", True),
            ({"pending_observation": False, "owner_agent_id": "main"}, "main", False),
            ({"pending_observation": True, "owner_agent_id": "child-1"}, "main", False),
            ({"pending_observation": True, "owner_agent_id": "child-1"}, "child-1", True),
        ],
    )
    def test_pending_agents_server_observation_is_owned_by_the_caller(
        self,
        tmp_path: pathlib.Path,
        record: dict[str, object],
        owner_agent_id: str,
        expected: bool,
    ) -> None:
        """呼出主体が未回収のagents_server結果だけを継続中と判定する。"""
        transcript = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _TEXT}])])
        session_state = {"agents_server_sessions": {"remote-session": record}}

        assert (
            is_pending_async_work(
                str(transcript),
                "",
                background_tasks=[],
                session_state=session_state,
                owner_agent_id=owner_agent_id,
            )
            is expected
        )

    @pytest.mark.parametrize(
        ("background_tasks", "payload_pending", "payload_authoritative"),
        [
            pytest.param(_BACKGROUND_TASKS_OMITTED, False, False, id="omitted"),
            pytest.param({"type": "subagent"}, False, False, id="non-list"),
            pytest.param([], False, True, id="empty"),
            pytest.param([{"type": "teammate"}], False, True, id="teammate-only"),
            pytest.param([{}, {"type": ""}, {"type": 1}, "subagent", None], False, False, id="invalid-list"),
            pytest.param([{"type": "subagent"}], True, True, id="non-teammate"),
            pytest.param([{}, {"type": "subagent"}], True, False, id="mixed"),
        ],
    )
    @pytest.mark.parametrize("transcript_pending", [False, True], ids=["transcript-clear", "transcript-pending"])
    @pytest.mark.parametrize("last_tool_pending", [False, True], ids=["last-tool-clear", "last-tool-pending"])
    def test_pending_sources_decision_table(
        self,
        tmp_path: pathlib.Path,
        background_tasks: object,
        payload_pending: bool,
        payload_authoritative: bool,
        transcript_pending: bool,
        last_tool_pending: bool,
    ) -> None:
        """payloadの権威性と3つの未完了根拠を独立した軸として判定する。"""
        entries = [_user_entry("hello")]
        if transcript_pending:
            entries.extend([_user_async_launched_entry("toolu_bg1"), _user_entry("続き")])
        final_content: list[dict[str, object]] = [{"type": "text", "text": _TEXT}]
        if last_tool_pending:
            final_content.append({"type": "tool_use", "id": "x", "name": "Agent", "input": {}})
        else:
            final_content.append(_bash_no_bg())
        entries.append(_assistant_entry(final_content))
        transcript = _write_transcript(tmp_path, entries)

        if background_tasks is _BACKGROUND_TASKS_OMITTED:
            actual = is_pending_async_work(str(transcript), "")
        else:
            actual = is_pending_async_work(str(transcript), "", background_tasks=background_tasks)
        expected = last_tool_pending or payload_pending or (transcript_pending and not payload_authoritative)
        assert actual is expected

    @pytest.mark.parametrize(
        ("background_tasks", "expected_authoritative", "expected_source"),
        [
            pytest.param([{}, {"type": "subagent"}], False, "background_tasks+transcript", id="mixed"),
            pytest.param([{"type": "subagent"}], True, "background_tasks", id="all-valid"),
        ],
    )
    def test_debug_output_reports_payload_authority_and_sources(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        background_tasks: object,
        expected_authoritative: bool,
        expected_source: str,
    ) -> None:
        """混在payloadと全要素有効payloadの権威性を判定源とともに出力する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", "1")
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _user_async_launched_entry("toolu_bg1"),
                _user_entry("続き"),
                _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
            ],
        )
        assert is_pending_async_work(str(transcript), "", background_tasks=background_tasks) is True
        captured = capsys.readouterr()
        assert f"payload_authoritative={expected_authoritative}" in captured.err
        assert f"source={expected_source}" in captured.err

    def test_background_tasks_cover_auto_restart_after_completed_transcript(self, tmp_path: pathlib.Path) -> None:
        """完了通知後に同じIDで再開したtaskをpayloadから未完了として扱う。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _user_async_launched_entry("toolu_bg1"),
                _user_task_notification_entry("toolu_bg1"),
                _user_entry("続き"),
                _assistant_entry([{"type": "text", "text": _TEXT}]),
            ],
        )
        assert is_pending_async_work(str(transcript), "") is False
        assert (
            is_pending_async_work(
                str(transcript),
                "",
                background_tasks=[{"type": "subagent", "id": "agent-restarted", "status": "running"}],
            )
            is True
        )
        assert is_pending_async_work(str(transcript), "", background_tasks=[]) is False

    def test_empty_background_tasks_do_not_hide_mcp_background_remainder(self, tmp_path: pathlib.Path) -> None:
        """申告対象外のMCPツールのバックグラウンドタスクへの移行は空の`background_tasks`でも未完了と判定する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _assistant_entry([{"type": "tool_use", "id": "toolu_mcp", "name": "mcp__agents_server__wait", "input": {}}]),
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_mcp",
                                "content": _host_mcp_notice("task-1"),
                            }
                        ],
                    },
                },
                _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
            ],
        )

        assert is_pending_async_work(str(transcript), "", background_tasks=[]) is True

    @pytest.mark.parametrize("phrase", _QUOTED_NOTICE_PHRASES)
    def test_mcp_result_quoting_notice_is_not_pending(self, tmp_path: pathlib.Path, phrase: str) -> None:
        """MCPの`tool_result`本文の途中に移行通知の文言を含むだけでは未完了の非同期作業にしない。"""
        quoted = f"background_tasks_test.py:1: {phrase.format(task_id='task-1')}"
        transcript = _write_transcript(
            tmp_path,
            [
                _assistant_entry(
                    [{"type": "tool_use", "id": "toolu_grep", "name": "mcp__plugin_agent-toolkit_pyfltr__grep", "input": {}}]
                ),
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_grep",
                                "content": [{"type": "text", "text": quoted}],
                            }
                        ],
                    },
                },
                _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
            ],
        )

        assert is_pending_async_work(str(transcript), "", background_tasks=[]) is False
        assert "task-1" not in pending_async_task_ids(str(transcript), "", background_tasks=[])

    def test_empty_background_tasks_hide_agent_remainder(self, tmp_path: pathlib.Path) -> None:
        """空の`background_tasks`を優先し、記録に残る申告対象の背景Agentは実行中ではないと判定する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_async_launched_entry("toolu_bg1"),
                _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
            ],
        )

        assert is_pending_async_work(str(transcript), "", background_tasks=[]) is False
