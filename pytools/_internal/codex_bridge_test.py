"""復旧RPC待機中のユーザー操作と、続行判定の照合を検証する。"""

import pathlib
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from pytools._internal import codex_bridge, codex_recovery, codex_transport


@pytest.mark.parametrize(
    ("event", "continues"),
    [
        ("none", True),
        ("server-started", True),
        ("server-completed", True),
        ("turn/start", False),
        ("turn/interrupt", False),
        ("thread/unsubscribe", False),
    ],
)
@pytest.mark.asyncio
async def test_recovery_rechecks_user_revision_and_interruption(tmp_path: pathlib.Path, event: str, continues: bool) -> None:
    """委譲復元中の新入力や中断を、復旧前の実行状態で上書きしない。

    時機の網羅はIPCを代用して行う。公開起動からの実プロセス境界はcodex_launcher_test.pyが担う。
    """
    bridge = codex_bridge.RecoveryBridge("codex", {}, tmp_path)
    bridge.state.observe_client({"method": "initialize", "params": {"clientInfo": {"name": "test"}}})
    bridge.state.observe_client({"id": 1, "method": "thread/start", "params": {}})
    bridge.state.observe_server(
        {
            "id": 1,
            "result": {
                "thread": {"id": "thread", "cwd": "/work"},
                "approvalPolicy": "never",
                "sandbox": {"type": "dangerFullAccess"},
            },
        }
    )
    bridge.state.observe_server({"method": "turn/started", "params": {"threadId": "thread", "turn": {"id": "original"}}})
    sent: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    phases: list[codex_bridge.RecoveryPhase] = []

    async def send(message: dict[str, Any], **_kwargs: Any) -> None:
        sent.append(message)
        phases.append(bridge.phase)
        method = message["method"]
        if method == "initialized":
            return
        result: dict[str, Any] = {}
        if method == "thread/resume":
            result = {"thread": {"id": "thread", "cwd": "/work", "status": {"type": "idle"}}}
        elif method == "thread/turns/list":
            result = {"data": [{"id": "original", "status": "inProgress"}]}
        elif method == "mcpServerStatus/list":
            result = {"data": []}
            if event.startswith("server-"):
                bridge.state.observe_server(
                    {
                        "method": "turn/started" if event == "server-started" else "turn/completed",
                        "params": {
                            "threadId": "thread",
                            "turn": {"id": "resumed" if event == "server-started" else "original"},
                        },
                    }
                )
            elif event != "none":
                # IPC待機中の入力時機を固定し、実プロセスの起動を網羅ケースへ重ねない。
                bridge._from_client({"method": event, "params": {"threadId": "thread"}})  # pylint: disable=protected-access
        responses.append({"id": message["id"], "result": result})

    async def receive(**_kwargs: Any) -> dict[str, Any]:
        return responses.pop(0)

    front = Mock(spec=codex_transport.RpcChannel)
    back = Mock(spec=codex_transport.RpcChannel)
    front.send = AsyncMock()
    back.send = AsyncMock(side_effect=send)
    back.receive = AsyncMock(side_effect=receive)
    # 部分的な復旧の差替えを避け、initializeから続行の送信まで実装を通す。
    await bridge._recover(front, back)  # pylint: disable=protected-access
    assert [message["method"] for message in sent] == [
        "initialize",
        "initialized",
        "thread/resume",
        "thread/turns/list",
        "mcpServerStatus/list",
        *(["turn/start"] if continues else []),
    ]
    assert phases == [
        codex_bridge.RecoveryPhase.INITIALIZING,
        codex_bridge.RecoveryPhase.INITIALIZING,
        codex_bridge.RecoveryPhase.RESUMING,
        codex_bridge.RecoveryPhase.RESUMING,
        codex_bridge.RecoveryPhase.RESTORING_DELEGATIONS,
        *([codex_bridge.RecoveryPhase.CHECKING_CONTINUATION] if continues else []),
    ]
    if event in {"turn/interrupt", "thread/unsubscribe"}:
        assert isinstance(bridge.state.conversation, codex_recovery.InterruptedConversation)
    assert bridge.recovery_count == 1
