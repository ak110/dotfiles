"""Antigravity CLI backendのコマンド構築とstream-jsonの消費を検証する。"""

import asyncio
import json
import os
import pathlib
import stat
import sys
from typing import Any

import pytest

from agent_toolkit._agents_server import antigravity
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError

_FAKE_AGY = """#!{python}
import json
import sys

args = sys.argv[1:]
conversation = args[args.index("--conversation") + 1] if "--conversation" in args else "conv-1"
prompt = args[args.index("-p") + 1]
print(json.dumps({{"event": "init", "conversation_id": conversation, "init": {{"model": "gemini-3.8-flash"}}}}), flush=True)
print(json.dumps({{"event": "step_update", "step_update": {{"text_delta": "調査中"}}}}), flush=True)
print(json.dumps({{"event": "result", "result": {{"status": "SUCCESS", "response": prompt[-20:]}}}}), flush=True)
"""


def _install_fake_agy(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """PATHの先頭へstream-jsonを返す`agy`のスタブを置く。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agy"
    fake.write_text(_FAKE_AGY.format(python=sys.executable), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return fake


def test_build_command_passes_model_effort_and_conversation() -> None:
    """モデル、effort、会話識別子と事前承認の指定をコマンド列へ渡す。"""
    command = antigravity.build_command("推敲して", "gemini-3.8-flash", "medium", "conv-1")

    assert command[0] == "agy"
    assert command[command.index("-p") + 1] == "推敲して"
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert command[command.index("--model") + 1] == "gemini-3.8-flash"
    assert command[command.index("--effort") + 1] == "medium"
    assert command[command.index("--conversation") + 1] == "conv-1"
    assert "--dangerously-skip-permissions" in command
    # 非対話実行で上限を指定しない場合は5分で終了するため、1turnを完了できる上限を明示する。
    assert command[command.index("--print-timeout") + 1] == "3600s"


def test_build_command_omits_absent_options() -> None:
    """モデル、effort、会話識別子を指定しない起動ではこれらのオプションを渡さない。"""
    command = antigravity.build_command("推敲して", None, None, None)

    assert "--model" not in command
    assert "--effort" not in command
    assert "--conversation" not in command


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"status": "completed", "response": "整えた"}, "completed"),
        ({"status": "SUCCESS", "response": "整えた"}, "completed"),
        ({"status": "interrupted"}, "interrupted"),
        ({"status": "failed", "error": {"message": "利用上限"}}, "failed"),
        ({"status": "completed"}, "failed"),
    ],
)
def test_result_values_maps_status(payload: dict[str, Any], expected_status: str) -> None:
    """`result`イベントの状態を終端状態へ対応付け、本文の無い完了は失敗として扱う。"""
    session = shared_state.SessionState(session_id="conv-1", cwd=".", engine="agy")

    assert antigravity.result_values(session, payload)["status"] == expected_status


def test_decode_event_ignores_non_json_lines() -> None:
    """JSONでない行と空行は無視する。"""
    assert antigravity.decode_event(b"\n") is None
    assert antigravity.decode_event(b"loading...\n") is None
    assert antigravity.decode_event(b'{"type": "init"}\n') == {"type": "init"}


def test_start_consumes_stream_and_finalizes_turn(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`init`でsessionを生成し、`result`で終端結果を確定する。"""
    _install_fake_agy(tmp_path, monkeypatch)

    async def scenario() -> shared_state.SessionState:
        manager = antigravity.AntigravityManager()
        session = await manager.start("原稿を推敲して", str(tmp_path), "gemini-3.8-flash", "medium")
        for _ in range(200):
            if session.terminal:
                break
            await asyncio.sleep(0.02)
        await manager.close()
        return session

    session = asyncio.run(scenario())

    assert session.session_id == "conv-1"
    assert session.engine == "agy"
    assert session.status == "completed"
    assert session.agent_message.endswith("原稿を推敲して")


def test_start_wraps_missing_cli_as_backend_error(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLIが未導入の起動失敗を委譲先CLIの失敗として送出し、導入と認証の確認へ分類されるようにする。"""
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    async def scenario() -> None:
        manager = antigravity.AntigravityManager()
        try:
            await manager.start("原稿を推敲して", str(tmp_path), "gemini-3.8-flash", "medium")
        finally:
            await manager.close()

    with pytest.raises(shared_state.DelegateBackendError, match="failed to start Antigravity CLI"):
        asyncio.run(scenario())


def test_delegate_prompt_carries_language_condition(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """通常委譲の本文先頭に、完了報告を日本語で書き英語の挿入指示を引き継がない条件が届く。

    Antigravityは規範とプロジェクト規範を読み込まないため、この条件が本文に無いと委譲先へ言語の定めが届かない。
    """
    fake = _install_fake_agy(tmp_path, monkeypatch)
    captured = tmp_path / "prompt.txt"
    fake.write_text(
        _FAKE_AGY.format(python=sys.executable).replace(
            'prompt = args[args.index("-p") + 1]\n',
            f'prompt = args[args.index("-p") + 1]\nopen({str(captured)!r}, "w", encoding="utf-8").write(prompt)\n',
        ),
        encoding="utf-8",
    )

    async def scenario() -> None:
        manager = antigravity.AntigravityManager()
        session = await manager.start("原稿を推敲して", str(tmp_path), "gemini-3.8-flash", "medium")
        for _ in range(200):
            if session.terminal:
                break
            await asyncio.sleep(0.02)
        await manager.close()

    asyncio.run(scenario())

    prompt = captured.read_text(encoding="utf-8")
    assert "日本語" in prompt
    assert "応答言語として引き継がない" in prompt
    assert prompt.endswith("原稿を推敲して")


def test_send_message_starts_a_new_turn_on_the_same_conversation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """継続は同じ会話識別子の新しい実行として開始する。"""
    _install_fake_agy(tmp_path, monkeypatch)

    async def scenario() -> tuple[dict[str, Any], shared_state.SessionState]:
        manager = antigravity.AntigravityManager()
        session = await manager.start("原稿を推敲して", str(tmp_path))
        for _ in range(200):
            if session.terminal:
                break
            await asyncio.sleep(0.02)
        delivery = await manager.send_message(session, "続けて短くして")
        for _ in range(200):
            if session.terminal:
                break
            await asyncio.sleep(0.02)
        await manager.close()
        return delivery, session

    delivery, session = asyncio.run(scenario())

    assert delivery["delivery"] == "reply_started"
    assert session.session_id == "conv-1"
    assert session.turn_seq == 2
    assert session.agent_message.endswith("続けて短くして")


def test_stream_events_are_saved_across_turns(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開JSON出力を保存し、継続したturnを同じ記録へ追記する。"""
    _install_fake_agy(tmp_path, monkeypatch)
    log_directory = tmp_path / "logs"

    async def scenario() -> None:
        manager = antigravity.AntigravityManager(log_directory=log_directory)
        session = await manager.start("最初の依頼", str(tmp_path))
        while not session.terminal:
            await asyncio.sleep(0.02)
        await manager.send_message(session, "次の依頼")
        while not session.terminal:
            await asyncio.sleep(0.02)
        await manager.close()

    asyncio.run(scenario())

    records = [json.loads(line) for line in (log_directory / "conv-1.jsonl").read_text().splitlines()]
    assert [record["event"] for record in records] == ["init", "step_update", "result"] * 2
    assert records[2]["result"]["response"].endswith("最初の依頼")
    assert records[5]["result"]["response"].endswith("次の依頼")
    assert stat.S_IMODE(log_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((log_directory / "conv-1.jsonl").stat().st_mode) == 0o600


def test_event_log_write_failure_does_not_fail_session(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """イベントログの書込失敗を警告し、resultの終端処理を続ける。"""
    _install_fake_agy(tmp_path, monkeypatch)
    log_directory = tmp_path / "logs"

    original_mkdir = pathlib.Path.mkdir

    def fail_mkdir(
        path: pathlib.Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path == log_directory:
            raise OSError("disk full")
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(pathlib.Path, "mkdir", fail_mkdir)

    async def scenario() -> shared_state.SessionState:
        manager = antigravity.AntigravityManager(log_directory=log_directory)
        session = await manager.start("依頼", str(tmp_path))
        while not session.terminal:
            await asyncio.sleep(0.02)
        await manager.close()
        return session

    with caplog.at_level("WARNING", logger="agent-toolkit.agents-server.antigravity"):
        session = asyncio.run(scenario())

    assert session.status == "completed"
    assert "イベントログへの書き込みに失敗" in caplog.text


def test_send_message_rejects_an_unfinished_turn() -> None:
    """実行中のturnへの継続は受理せず、終端の観測後に再送する操作を示す。"""
    session = shared_state.SessionState(session_id="conv-1", cwd=".", engine="agy", status="running")

    async def scenario() -> None:
        manager = antigravity.AntigravityManager()
        with pytest.raises(ValueError, match="has not finished") as raised:
            await manager.send_message(session, "続けて")
        assert isinstance(raised.value, ActionableError)
        assert f"{NEXT_ACTION_PREFIX}`atk agents wait`" in raised.value.message

    asyncio.run(scenario())


_GATED_AGY = """#!{python}
import json
import pathlib
import sys
import time

gate_dir = pathlib.Path({gate_dir!r})


def emit(payload):
    print(json.dumps(payload), flush=True)


def wait_for(name):
    while not (gate_dir / name).exists():
        time.sleep(0.01)


emit({{"event": "init", "conversation_id": "conv-1", "init": {{"model": "gemini-3.8-flash"}}}})
for step_type in ("user_input", "system_message", "error_message"):
    emit({{"event": "step_update", "step_update": {{"conversation_id": "conv-1", "state": "DONE", "step_type": step_type}}}})
wait_for("output")
emit({{"event": "step_update", "step_update": {{"conversation_id": "conv-1", "state": "DONE", "step_type": "agent_response"}}}})
wait_for("finish")
emit({{"event": "result", "result": {{"status": "SUCCESS", "response": "完了"}}}})
"""


def test_model_output_step_is_observed_and_notified(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """可用性失敗の前に届く種別は出力に数えず、本文キーの無い`agent_response`で待機側へ通知する。"""
    gate_dir = tmp_path / "gates"
    gate_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agy"
    fake.write_text(_GATED_AGY.format(python=sys.executable, gate_dir=str(gate_dir)), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    log_directory = tmp_path / "logs"

    async def scenario() -> tuple[bool, bool]:
        condition = asyncio.Condition()
        manager = antigravity.AntigravityManager(condition=condition, log_directory=log_directory)
        session = await manager.start("調査して", str(tmp_path))
        log_file = log_directory / "conv-1.jsonl"
        # 記録の追記とその行の処理は同じ同期区間で行われるため、4行目の出現は3件の`step_update`の処理済みを示す。
        while not log_file.exists() or len(log_file.read_text(encoding="utf-8").splitlines()) < 4:
            await asyncio.sleep(0.01)
        before_output = session.model_output_observed
        (gate_dir / "output").touch()
        async with condition:
            await asyncio.wait_for(condition.wait_for(lambda: session.model_output_observed), timeout=10)
        after_output = session.model_output_observed
        (gate_dir / "finish").touch()
        while not session.terminal:
            await asyncio.sleep(0.02)
        await manager.close()
        return before_output, after_output

    before_output, after_output = asyncio.run(scenario())

    assert not before_output
    assert after_output


@pytest.mark.parametrize("launch_kind", shared_state.LAUNCH_SYSTEM_PROMPTS)
def test_system_prompt_does_not_promise_auto_resume(launch_kind: shared_state.LaunchKind) -> None:
    """自動再開を確かめていないAntigravity backendは、同じsessionの自動再開と待機表明を委譲先へ約束しない。"""
    prompt = antigravity._system_prompt(launch_kind)  # pylint: disable=protected-access

    assert prompt == shared_state.LAUNCH_SYSTEM_PROMPTS[launch_kind]
    assert shared_state.AUTO_RESUME_NOTICE not in prompt
