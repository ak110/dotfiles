"""公開CLIの相対操作だけを受理し、会話IDと環境を所有要求へ渡す。"""

import json

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import commands


@pytest.mark.parametrize(("arguments", "direction", "target"), [(["up"], "up", None), (["down", "child"], "down", "child")])
def test_cli_effort_dispatch_preserves_relative_request(monkeypatch, capsys, arguments, direction, target):
    calls = []

    def request(selected_direction, selected_target, environment, **_kwargs):
        calls.append((selected_direction, selected_target, environment))
        return {"session_id": selected_target or "self", "effort": "medium", "changed": True}

    monkeypatch.setattr(commands.effort_requests, "request", request)
    monkeypatch.setenv("CODEX_THREAD_ID", "self")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "effort", *arguments])
    result = json.loads(capsys.readouterr().out)
    assert result["effort"] == "medium"
    assert calls[0][:2] == (direction, target)
    assert calls[0][2]["CODEX_THREAD_ID"] == "self"


@pytest.mark.parametrize("arguments", [["high"], ["up", "child", "high"], ["up", "--model", "other"]])
def test_cli_effort_rejects_absolute_levels_and_model_changes(arguments):
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "effort", *arguments])


def test_cli_effort_request_failure_reports_next_action(monkeypatch, capsys):
    """本人や受理段を確認できない失敗は、終了1と具体的な次操作を返す。"""

    def fail(*_args, **_kwargs):
        raise ValueError("本人を確認できない")

    monkeypatch.setattr(commands.effort_requests, "request", fail)
    with pytest.raises(SystemExit, match="1"):
        atk.main(["agents", "effort", "up"])
    output = capsys.readouterr()
    assert "本人を確認できない" in output.err and "次の操作:" in output.err
    assert "atk agents show" in output.err
