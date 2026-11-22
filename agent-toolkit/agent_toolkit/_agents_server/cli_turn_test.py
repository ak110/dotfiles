"""CLIの正しい長文JSONLを、イベント全量と順序を保って受け取る。"""

import asyncio
import json
import os
import pathlib
import sys
import typing
from asyncio.subprocess import Process
from collections.abc import Callable

import psutil
import pytest

from agent_toolkit._agents_server import antigravity, cli_turn, opencode, session_errors
from agent_toolkit._agents_server.cli_turn import CliTurnManager
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._common import process_tree
from agent_toolkit._testing import late_descendant


@pytest.mark.asyncio
@pytest.mark.parametrize("manager_type", [antigravity.AntigravityManager, opencode.OpenCodeManager], ids=["agy", "opencode"])
@pytest.mark.parametrize("final_lf", [True, False], ids=["lf", "eof"])
async def test_large_jsonl_event_is_preserved_through_backend(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_type: type[CliTurnManager],
    final_lf: bool,
) -> None:
    """64 KiBを超える日本語のツール結果も、後続の終了とともに保存する。"""
    full_body = "資料の全文開始\n" + "日本語の規範本文。\n" * 10_000 + "資料の全文終了"
    if manager_type is antigravity.AntigravityManager:
        events = [
            {"event": "init", "conversation_id": "large-conversation"},
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "step_type": "tool",
                    "state": "ERROR",
                    "tool_name": "read_file",
                    "tool_info": {"error": {"message": full_body}},
                },
            },
            {"event": "result", "result": {"status": "SUCCESS", "response": "全文を確認した"}},
        ]
    else:
        events = [
            {"type": "step_start", "sessionID": "large-conversation"},
            {
                "type": "tool_use",
                "sessionID": "large-conversation",
                "part": {"tool": "read_file", "state": {"status": "completed", "output": full_body}},
            },
            {"type": "text", "sessionID": "large-conversation", "part": {"text": "全文を確認した"}},
        ]
    lines = [json.dumps(event, ensure_ascii=False).encode() for event in events]
    assert len(lines[1]) > 64 * 1024
    data = tmp_path / "events.jsonl"
    data.write_bytes(b"\n".join(lines) + (b"\n" if final_lf else b""))
    emitter = tmp_path / "emit.py"
    emitter.write_text(
        "import pathlib, sys\nsys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())\n", encoding="utf-8"
    )
    monkeypatch.setattr(manager_type, "build_command", staticmethod(lambda *_args: [sys.executable, str(emitter), str(data)]))
    monkeypatch.setattr(manager_type, "system_prompt", staticmethod(lambda _kind: "受入の固定入力"))
    condition = asyncio.Condition()
    log_directory = tmp_path / "logs"
    async with manager_type(condition=condition, log_directory=log_directory) as manager:
        session = await manager.start("固定入力", str(tmp_path), "test-model", "low")
        async with condition:
            await asyncio.wait_for(condition.wait_for(lambda: session.terminal), timeout=10)
        assert session.status == "completed", session.error
        assert session.agent_message == "全文を確認した"
    records = [json.loads(line) for line in (log_directory / "large-conversation.jsonl").read_text().splitlines()]
    # OpenCodeの入力補足記録を保持し、CLIが出力した3イベントの全fieldを比較する。
    observed = [record for record in records if record.get("type") != "agent_toolkit_input"]
    assert observed == events


@pytest.mark.skipif(os.name == "nt", reason="POSIXのTERM無視と別process groupの実子孫を観測する")
@pytest.mark.parametrize("manager_type", [antigravity.AntigravityManager, opencode.OpenCodeManager], ids=["agy", "opencode"])
@pytest.mark.asyncio
async def test_close_reclaims_descendants_when_kill_wait_stalls(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_type: type[CliTurnManager],
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """KILL後のwaitが返らなくても実CLIと保存済み子孫を回収する。"""
    script = tmp_path / "runtime.py"
    event = (
        {"event": "init", "conversation_id": "shutdown"}
        if manager_type is antigravity.AntigravityManager
        else {"type": "step_start", "sessionID": "shutdown"}
    )
    script.write_text(
        "import json, os, pathlib, signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "directory = pathlib.Path(sys.argv[1])\n"
        "leaf = len(sys.argv) > 2\n"
        "(directory / ('leaf.pid' if leaf else 'runtime.pid')).write_text(str(os.getpid()))\n"
        "if not leaf:\n"
        "    subprocess.Popen([sys.executable, __file__, str(directory), 'leaf'],\n"
        "        stdout=subprocess.DEVNULL, start_new_session=True)\n"
        f"    print({json.dumps(json.dumps(event))}, flush=True)\n" + late_descendant.held_source(tmp_path / "runtime.release"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        manager_type, "build_command", staticmethod(lambda *_args: [sys.executable, str(script), str(tmp_path)])
    )
    monkeypatch.setattr(manager_type, "system_prompt", staticmethod(lambda _kind: "停止試験"))
    monkeypatch.setattr(cli_turn, "_STOP_TIMEOUT", 0.05)
    monkeypatch.setattr(cli_turn, "_child_env", lambda _owner: host_environ())
    targets: list[psutil.Process] = []
    async with manager_type(condition=asyncio.Condition(), log_directory=tmp_path / "logs") as manager:
        try:
            await manager.start("停止試験", str(tmp_path), "test-model", "low")

            async def ready() -> None:
                while not (tmp_path / "leaf.pid").exists():
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(ready(), 10)
            runtime_pid = int((tmp_path / "runtime.pid").read_text())
            targets = [psutil.Process(runtime_pid), *process_tree.collect_descendants(runtime_pid)]
            wait = Process.wait
            calls: list[int] = []

            async def stalled_wait(process: Process) -> int:
                if process.pid == runtime_pid:
                    calls.append(process.pid)
                    await asyncio.Event().wait()
                return await wait(process)

            monkeypatch.setattr(Process, "wait", stalled_wait)
            await asyncio.wait_for(manager.close(), 15)
            # 停止処理が子孫ごと終了させた後は、終了状態の取得が返らなくてもcloseは上限内に戻る。
            assert all(
                not target.is_running() or target.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD) for target in targets
            )
        finally:
            await asyncio.to_thread(process_tree.reclaim_processes, targets, timeout=0.5)


@pytest.mark.cpu_limit(
    6.5, reason="実OSで列挙後に生まれた子孫の回収とファイル解放を確認する最小の親子構成（Windows CI CPU合計2.985秒）"
)
@pytest.mark.windows
@pytest.mark.asyncio
async def test_close_reclaims_descendant_started_after_first_listing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, host_environ: Callable[[], dict[str, str]]
) -> None:
    """終了処理の最初の列挙の後に委譲先CLIの子孫が起動しても、終了させてファイルを解放する。"""
    runtime = tmp_path / "runtime.py"
    runtime.write_text(
        f"print({json.dumps({'type': 'step_start', 'sessionID': 'late'})!r}, flush=True)\n"
        + late_descendant.held_source(tmp_path / "runtime.release"),
        encoding="utf-8",
    )
    command = late_descendant.cli_command(tmp_path, str(runtime))
    monkeypatch.setattr(opencode.OpenCodeManager, "build_command", staticmethod(lambda *_args: command))
    monkeypatch.setattr(opencode.OpenCodeManager, "system_prompt", staticmethod(lambda _kind: "停止試験"))
    monkeypatch.setattr(cli_turn, "_child_env", lambda _owner: host_environ())
    async with opencode.OpenCodeManager(condition=asyncio.Condition(), log_directory=tmp_path / "logs") as manager:
        await manager.start("停止試験", str(tmp_path), "test-model", "low")
        await asyncio.to_thread(late_descendant.wait_spawner, tmp_path)
        late_descendant.arm(tmp_path, monkeypatch.setattr)
        await asyncio.wait_for(manager.close(), 60)
    late_descendant.assert_released(tmp_path)


@pytest.mark.asyncio
async def test_start_returns_failed_session_when_bookkeeping_after_init_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """initの後の記録処理が失敗しても、開始は初期化の上限まで待たずに原因を持つ失敗のsessionを返す。"""
    emitter = tmp_path / "emit.py"
    emitter.write_text(
        f"print({json.dumps({'event': 'init', 'conversation_id': 'after-init'})!r}, flush=True)\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        antigravity.AntigravityManager, "build_command", staticmethod(lambda *_args: [sys.executable, str(emitter)])
    )
    monkeypatch.setattr(antigravity.AntigravityManager, "system_prompt", staticmethod(lambda _kind: "開始試験"))

    async def failing_attach(_session: SessionState, _pid: int | None) -> None:
        raise OSError("実行プロセスを記録できない")

    monkeypatch.setattr(SessionState, "attach_executor_async", failing_attach)
    async with antigravity.AntigravityManager() as manager, asyncio.timeout(10):
        session = await manager.start("開始試験", str(tmp_path), "test-model", "low")
    assert session.session_id == "after-init"
    assert session.status == "failed"
    assert session.error is not None and "実行プロセスを記録できない" in session.error["message"]


@pytest.mark.asyncio
async def test_send_message_fails_when_next_turn_cannot_launch(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """既存sessionの次のturnのCLIを起動できなければ、送信を失敗として返し、前のturnの結果とturn番号を保つ。

    作業ディレクトリの削除後の追送で、届いていない指示を開始済みと報告しないことを確かめる。
    """
    events = [
        {"event": "init", "conversation_id": "next-turn"},
        {"event": "result", "result": {"status": "SUCCESS", "response": "初回の結果"}},
    ]
    emitter = tmp_path / "emit.py"
    emitter.write_text(
        "".join(f"print({json.dumps(event, ensure_ascii=False)!r}, flush=True)\n" for event in events), encoding="utf-8"
    )
    monkeypatch.setattr(
        antigravity.AntigravityManager, "build_command", staticmethod(lambda *_args: [sys.executable, str(emitter)])
    )
    monkeypatch.setattr(antigravity.AntigravityManager, "system_prompt", staticmethod(lambda _kind: "継続試験"))
    workdir = tmp_path / "work"
    workdir.mkdir()
    condition = asyncio.Condition()
    async with antigravity.AntigravityManager(condition=condition) as manager, asyncio.timeout(20):
        session = await manager.start("初回", str(workdir), "test-model", "low")
        async with condition:
            await condition.wait_for(lambda: session.terminal)
        assert (session.status, session.agent_message) == ("completed", "初回の結果"), session.error
        workdir.rmdir()
        with pytest.raises(session_errors.DelegateBackendError, match="failed to start"):
            await manager.send_message(session, "次の指示")
    assert session.turn_seq == 1
    assert (session.status, session.agent_message, session.error) == ("completed", "初回の結果", None)


@pytest.mark.parametrize("eof", [True, False])
def test_failure_stderr_redacts_before_truncating(monkeypatch: pytest.MonkeyPatch, eof: bool) -> None:
    """標準エラーの読取と記録の上限が資格情報の途中にあっても、保存する診断へ断片を残さない。"""
    from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

    from agent_toolkit._common.diagnostic_redaction import redact_diagnostic_values  # pylint: disable=import-outside-toplevel

    async def read() -> str:
        reader = asyncio.StreamReader()
        reader.feed_data(b"x" * 3960 + b" https://" + b"A" * 45 + b"@example.org\n" + b"normal\n" * (1 if eof else 2000))
        if eof:
            reader.feed_eof()
        process = typing.cast(Process, SimpleNamespace(stderr=reader))
        return await cli_turn._read_stderr(process)  # pylint: disable=protected-access

    captured: dict = {}
    monkeypatch.setattr(cli_turn, "finalize_turn", lambda _session, result: captured.update(result))
    session = typing.cast(
        SessionState, SimpleNamespace(engine="claude", model="synthetic", session_id="S1", agent_message=None)
    )
    cli_turn._record_failure(session, RuntimeError("失敗"), asyncio.run(read()))  # pylint: disable=protected-access

    saved = redact_diagnostic_values(captured["error"])["stderr"]
    assert "A" * 10 not in saved
    assert len(saved) <= cli_turn._STDERR_LIMIT_CHARS  # pylint: disable=protected-access


def test_ignored_non_json_output_log_redacts_before_truncating(caplog: pytest.LogCaptureFixture) -> None:
    """無視した非JSON出力のログも、伏せてから上限の文字数に抜粋する。"""
    from agent_toolkit._common.diagnostic_redaction import redact_secrets  # pylint: disable=import-outside-toplevel

    line = ("x" * 180 + " https://" + "A" * 45 + "@example.org").encode()
    with caplog.at_level("DEBUG", logger=cli_turn._LOG.name):  # pylint: disable=protected-access
        assert cli_turn.decode_event(line) is None
    # 保存時の書式化と同じく、記録の本文を伏せた結果を確かめる。
    assert all("A" * 10 not in redact_secrets(record.getMessage()) for record in caplog.records)
    assert caplog.records


@pytest.mark.parametrize(
    ("data", "eof"),
    [(b"backend initialization failed", False), (b"backend initialization failed", True), (("診" * 2500).encode(), True)],
    ids=["short-open", "short-eof", "multibyte-eof"],
)
def test_read_stderr_keeps_short_output_and_byte_limit(data: bytes, eof: bool) -> None:
    """一時的な短い読取の通常の診断は残し、抜粋は従来の読取上限と同じバイト数に収める。"""
    from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

    async def read() -> str:
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        if eof:
            reader.feed_eof()
        process = typing.cast(Process, SimpleNamespace(stderr=reader))
        return await cli_turn._read_stderr(process)  # pylint: disable=protected-access

    result = asyncio.run(read())
    assert len(result.encode()) <= cli_turn._STDERR_LIMIT_CHARS  # pylint: disable=protected-access
    if len(data) < cli_turn._STDERR_LIMIT_CHARS:  # pylint: disable=protected-access
        assert result == data.decode()


def test_read_stderr_keeps_complete_line_at_read_limit() -> None:
    """EOFまで読めた通常の1行は、読取の上限と同じ長さでも捨てずに上限のバイト数で抜粋する。"""
    from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

    data = b"x" * (cli_turn._STDERR_LIMIT_CHARS * 2)  # pylint: disable=protected-access

    async def read() -> str:
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        process = typing.cast(Process, SimpleNamespace(stderr=reader))
        return await cli_turn._read_stderr(process)  # pylint: disable=protected-access

    assert asyncio.run(read()) == "x" * cli_turn._STDERR_LIMIT_CHARS  # pylint: disable=protected-access
