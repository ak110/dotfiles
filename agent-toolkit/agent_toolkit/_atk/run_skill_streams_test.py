"""run skill streamsの契約を検証する。"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import subprocess
import sys
import threading
import typing

import pytest

from agent_toolkit._atk import run_skill
from agent_toolkit._common import process_tree
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment
from agent_toolkit._testing.run_skill_support import _engine_env, _Env, _log_files, _read_terminal_until, _records, _run

__all__ = ["_engine_env"]


@pytest.mark.skipif(os.name == "nt", reason="POSIXのPTYでstderrだけを端末にする結合テスト")
@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize(
    ("skill", "mode"), [("example:check", "normal"), ("other-skill", "failed"), ("timed-skill", "timeout")]
)
def test_tty_progress_arrives_before_child_exits(
    engine_env: _Env, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, skill: str, mode: str
) -> None:
    """公開CLIはstdoutをリダイレクトしても、終了前の開始情報・発言・toolだけをstderrへ到着順に出力する。"""
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")
    progress_dir = tmp_path / "progress"
    progress_dir.mkdir()
    if engine == "claude":
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "途中の発言"}]}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "表示しない思考"},
                        {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"path": "file"}},
                    ]
                },
            },
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "次の発言"}]}},
        ]
        tool_text = "tool: Read"
    else:
        events = [
            {"type": "thread.started", "thread_id": "test-thread"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "途中の発言"}},
            {"type": "item.completed", "item": {"type": "reasoning", "text": "表示しない思考"}},
            {"type": "item.started", "item": {"id": "tool-1", "type": "command_execution", "command": "pwd"}},
            {"type": "item.completed", "item": {"id": "tool-1", "type": "command_execution", "command": "pwd"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "次の発言"}},
        ]
        tool_text = "tool: command_execution"
    (progress_dir / "events.json").write_text(json.dumps(events), encoding="utf-8")
    release = tmp_path / "release"
    env = {**os.environ, "FAKE_PROGRESS_DIR": str(progress_dir), "FAKE_SESSION_HOLD_FILE": str(release)}
    env["FAKE_SESSION_EXIT"] = "7" if mode == "failed" else "0"
    env["PYTHONPATH"] = os.pathsep.join((str(pathlib.Path(run_skill.__file__).resolve().parents[2]), env.get("PYTHONPATH", "")))
    master, slave = os.openpty()
    try:
        with subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; from agent_toolkit import atk; atk.main(sys.argv[1:])",
                "run-skill",
                "--target-repo",
                str(engine_env.repo),
                "--timeout",
                "10",
                skill,
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=slave,
        ) as process:
            try:
                received = _read_terminal_until(master, "ログ: ")
                log_path = _log_files(engine_env)[0]
                received = _read_terminal_until(master, str(log_path.resolve()), received)
                assert log_path.is_file() and process.poll() is None
                if engine == "claude":
                    received = _read_terminal_until(master, "Claudeのセッション識別子:", received)
                else:
                    (progress_dir / "0").touch()
                    received = _read_terminal_until(master, "Codexのthread識別子: test-thread", received)
                assert process.poll() is None
                first_message = 0 if engine == "claude" else 1
                (progress_dir / str(first_message)).touch()
                received = _read_terminal_until(master, "assistant: 途中の発言", received)
                assert process.poll() is None
                for index in range(first_message + 1, len(events)):
                    (progress_dir / str(index)).touch()
                received = _read_terminal_until(master, "assistant: 次の発言", received)
                terminal = received.decode()
                assert (
                    terminal.index("assistant: 途中の発言") < terminal.index(tool_text) < terminal.index("assistant: 次の発言")
                )
                assert terminal.count(tool_text) == 1
                assert "表示しない思考" not in terminal and '"type"' not in terminal
                assert "session stdout line" not in terminal and "session stderr line" not in terminal
                assert process.poll() is None
                if mode != "timeout":
                    release.touch()
                out, _ = process.communicate(timeout=20)
                assert process.returncode == {"normal": 0, "failed": 7, "timeout": 124}[mode]
                assert (out.decode().startswith("成功: ") and len(out.splitlines()) == 1) if mode == "normal" else not out
                if mode != "normal":
                    received = _read_terminal_until(master, "次の操作:", received)
                    assert "失敗:" in received.decode()
            finally:
                release.touch()
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=20)
        log = log_path.read_text(encoding="utf-8")
        for text in ("assistant: 途中の発言", tool_text, "assistant: 次の発言", "[stderr] session stderr line"):
            assert text in log
        for event in events:
            assert f"[stdout] {json.dumps(event)}" in log
        argv = _records(engine_env, "session")[0]["argv"]
        assert "stream-json" in argv if engine == "claude" else "--json" in argv
    finally:
        os.close(slave)
        os.close(master)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("normal", 0),
        ("failed", 7),
        ("timeout", 124),
        ("first-reader", None),
        ("second-reader", None),
        ("interrupt", None),
        ("attach", None),
    ],
)
def test_session_readers_close_both_pipes(
    engine_env: _Env,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request: pytest.FixtureRequest,
    mode: str,
    expected: int | None,
) -> None:
    """公開CLIの正常・非0・timeoutのいずれも、読み終えた実pipeを閉じて結果とログを返す。"""
    real_popen = subprocess.Popen
    sessions: list[subprocess.Popen[typing.Any]] = []
    rescue = contextlib.ExitStack()
    request.addfinalizer(rescue.close)

    def popen(argv: list[str], **kwargs: typing.Any) -> typing.Any:
        # 被検証関数へ所有権を渡し、reader自身によるcloseを観測する。
        process = real_popen(argv, **kwargs)  # pylint: disable=consider-using-with
        if "--session-id" in argv:
            sessions.append(process)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    rescue.callback(stream.close)
            rescue.callback(process_tree.stop_process_tree, process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    if mode in ("first-reader", "second-reader"):
        original_start = threading.Thread.start
        count = 0

        def start(reader: threading.Thread, *args: typing.Any, **kwargs: typing.Any) -> None:
            nonlocal count
            # 対象セッションのreaderだけへ起動失敗を注入する。
            # pylint: disable-next=protected-access
            if getattr(reader, "_target", None) is run_skill._copy_stream:  # noqa: SLF001
                count += 1
                if count == (1 if mode == "first-reader" else 2):
                    raise RuntimeError("reader start failed")
            original_start(reader, *args, **kwargs)

        monkeypatch.setattr(threading.Thread, "start", start)
    elif mode == "attach":

        def attach(*_args: typing.Any, **_kwargs: typing.Any) -> None:
            raise RuntimeError("executor attach failed")

        monkeypatch.setattr(run_skill._run_state.RecordWriter, "attach", attach)  # noqa: SLF001  # pylint: disable=protected-access
    elif mode == "interrupt":
        original_wait = real_popen.wait
        interrupted = False

        def wait(process: subprocess.Popen[typing.Any], *args: typing.Any, **kwargs: typing.Any) -> int:
            nonlocal interrupted
            if isinstance(process.args, (list, tuple)) and "--session-id" in process.args and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original_wait(process, *args, **kwargs)

        monkeypatch.setattr(real_popen, "wait", wait)
    arguments = ["--target-repo", str(engine_env.repo), "pipe-skill"]
    if mode == "failed":
        monkeypatch.setenv("FAKE_SESSION_EXIT", "7")
    elif mode == "timeout":
        monkeypatch.setenv("FAKE_SESSION_HOLD_FILE", str(tmp_path / "never-release"))
        arguments[0:0] = ["--timeout", "3"]
    if expected is None:
        with pytest.raises(KeyboardInterrupt if mode == "interrupt" else RuntimeError):
            _run(arguments, capsys)
    else:
        code, _out, _err = _run(arguments, capsys)
        assert code == expected
    assert len(sessions) == 1
    session = sessions[0]
    assert session.poll() is not None
    assert session.stdout is not None and session.stdout.closed
    assert session.stderr is not None and session.stderr.closed
    log = _log_files(engine_env)[0].read_text(encoding="utf-8")
    if expected is not None:
        assert "session stdout line" in log
        assert "session stderr line" in log
