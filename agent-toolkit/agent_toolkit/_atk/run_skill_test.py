"""`atk run-skill`の公開契約を、偽の`claude`・`codex`実行ファイルをPATHへ置いた起動で検証する。

偽の実行ファイルは可用性判定（`availability-probe`の入力）と本作業の
セッションを引数で見分け、起動ごとに引数、作業ディレクトリと環境印の有無を記録ディレクトリへ書く。
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import re
import select
import stat
import subprocess
import sys
import time
import types
import typing

import psutil
import pytest

from agent_toolkit import atk
from agent_toolkit._atk import orchestrator, run_skill
from agent_toolkit._atk.wi import add as wi_add
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._common import claude_usage_limit
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment

_FAKE_ENGINE = """\
#!{python}
import json, os, pathlib, subprocess, sys, time, uuid
import datetime, re
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
record_dir = pathlib.Path(os.environ["FAKE_RECORD_DIR"])
probe = "availability-probe" in args[-1]
kind = "probe" if probe else "session"
record = {{
    "name": name,
    "kind": kind,
    "argv": [name, *args],
    "cwd": os.getcwd(),
    "env": {{key: os.environ.get(key) for key in (
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_RESTART_SPEC",
        "AGENT_TOOLKIT_RUN_SKILL_ID",
    )}},
}}
(record_dir / f"{{time.time_ns()}}-{{uuid.uuid4().hex}}.json").write_text(json.dumps(record), encoding="utf-8")
if probe:
    counter = record_dir / "probe-count"
    count = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(count + 1))
    if count < int(os.environ.get("FAKE_PROBE_REJECT_COUNT", "0")):
        for event in (
            {{"type": "system", "subtype": "init", "session_id": "probe"}},
            {{
                "type": "rate_limit_event",
                "rate_limit_info": {{"status": "rejected", "rateLimitType": "five_hour", "resetsAt": None}},
            }},
            {{"type": "result", "is_error": True, "result": "You've hit your limit"}},
        ):
            print(json.dumps(event))
        sys.exit(1)
    print(json.dumps({{"type": "result", "is_error": False}}))
    sys.exit(0)
if os.environ.get("FAKE_REPORT_MODE", "saved") != "missing":
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    mode = os.environ.get("FAKE_REPORT_MODE", "saved")
    run_id = os.environ["AGENT_TOOLKIT_RUN_SKILL_ID"]
    scope_file = record_dir / "scope"
    match = re.search(r"スキル`([^`]+)`", args[-1])
    if match:
        scope_file.write_text(match[1])
    scope = scope_file.read_text()
    (notes / "inbox").mkdir(exist_ok=True)
    old = notes / "inbox" / (run_id + ".md")
    body = old.read_text().split("\\n---\\n", 1)[1] + "\\n再開後の報告更新\\n" if old.exists() else "定期実行の報告\\n"
    saved_id = "other-run" if mode == "other" else run_id
    old.write_text("---\\ntype: uwi\\nsource: run-skill\\nscope: " + json.dumps(scope) +
        "\\nrun_skill_report: " + ("false" if mode == "question" else "true") +
        "\\nrun_skill_run_id: " + saved_id + "\\n---\\n" + body)
    subprocess.run(["git", "-C", str(notes), "add", "."], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(notes), "commit", "-qm", "report"], check=True)
    if os.environ.get("FAKE_REPORT_REMOTE"):
        pushed = subprocess.run(["git", "-C", str(notes), "push"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if pushed.returncode and not os.environ.get("FAKE_REPORT_PUSH_FAIL"):
            sys.exit(pushed.returncode)
    if mode in ("other", "unsynced"):
        old.write_text(old.read_text() + "\\n未同期の更新\\n")
        if mode == "other":
            subprocess.run(["git", "-C", str(notes), "add", "."], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(notes), "commit", "-qm", "other report"], check=True)
if os.environ.get("FAKE_SESSION_LIMIT_COUNT"):
    counter = record_dir / "session-count"
    count = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(count + 1))
    rejected = count < int(os.environ["FAKE_SESSION_LIMIT_COUNT"])
    if name == "claude":
        assert args[args.index("--output-format") + 1] == "stream-json" and "--verbose" in args
        if rejected:
            print(json.dumps({{"type": "rate_limit_event", "rate_limit_info": {{
                "status": "rejected", "rateLimitType": os.environ["FAKE_LIMIT_TYPE"], "resetsAt": 1010
            }}}}))
    else:
        assert "--json" in args
        thread = "01234567-89ab-cdef-0123-456789abcdef"
        print(json.dumps({{"type": "thread.started", "thread_id": thread}}))
        directory = pathlib.Path(os.environ["CODEX_HOME"]) / "sessions/2026/10/16"
        directory.mkdir(parents=True, exist_ok=True)
        events = [{{"type": "event_msg", "payload": {{"type": "token_count", "rate_limits": {{
            "primary": {{"used_percent": 100, "resets_at": 1010}}
        }}}}}}, {{"type": "event_msg", "payload": {{"type": "task_complete", "error": {{
            "codex_error_info": "usage_limit_exceeded" if rejected else "other"
        }}}}}}]
        (directory / ("rollout-test-" + thread + ".jsonl")).write_text(
            "\\n".join(json.dumps(event) for event in events), encoding="utf-8")
    sys.exit(1 if rejected else 0)
print("session stdout line")
print("session stderr line", file=sys.stderr)
sys.stdout.flush()
if os.environ.get("FAKE_PROGRESS_DIR"):
    progress_dir = pathlib.Path(os.environ["FAKE_PROGRESS_DIR"])
    events = json.loads((progress_dir / "events.json").read_text())
    for index, event in enumerate(events):
        while not (progress_dir / str(index)).exists():
            time.sleep(0.01)
        print(json.dumps(event), flush=True)
if os.environ.get("FAKE_SESSION_STARTED_FILE"):
    pathlib.Path(os.environ["FAKE_SESSION_STARTED_FILE"]).write_text("started")
if os.environ.get("FAKE_SESSION_SPAWN_PID_FILE"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    pathlib.Path(os.environ["FAKE_SESSION_SPAWN_PID_FILE"]).write_text(str(child.pid))
    time.sleep(120)
if os.environ.get("FAKE_SESSION_DETACH_PID_FILE"):
    # 出力のパイプを継承したまま残る子孫を起動し、自身は直ちに終わる。
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    pathlib.Path(os.environ["FAKE_SESSION_DETACH_PID_FILE"]).write_text(str(child.pid))
if os.environ.get("FAKE_SESSION_HOLD_FILE"):
    while not pathlib.Path(os.environ["FAKE_SESSION_HOLD_FILE"]).exists():
        time.sleep(0.05)
sys.exit(int(os.environ.get("FAKE_SESSION_EXIT", "0")))
"""


class _Env(typing.NamedTuple):
    repo: pathlib.Path
    records: pathlib.Path
    state: pathlib.Path
    bin_dir: pathlib.Path


@pytest.fixture(name="engine_env")
def _engine_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> _Env:
    """偽の実行ファイル、隔離した状態ディレクトリと対象リポジトリを用意する。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        path = bin_dir / name
        path.write_text(_FAKE_ENGINE.format(python=sys.executable), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    records = tmp_path / "records"
    records.mkdir()
    state = tmp_path / "state"
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    git_repository.init_repository(repo)
    notes = tmp_path / "notes"
    git_repository.init_repository(notes)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("PYTHONPATH", str(pathlib.Path(run_skill.__file__).resolve().parents[2]))
    monkeypatch.setenv("PATH", os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))))
    monkeypatch.setenv("FAKE_RECORD_DIR", str(records))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    set_orchestrate_environment(monkeypatch, "claude:sonnet/high")
    for name in ("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "AGENT_TOOLKIT_RESTART_SPEC"):
        monkeypatch.delenv(name, raising=False)
    return _Env(repo=repo, records=records, state=state, bin_dir=bin_dir)


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["run-skill", *argv])
    captured = capsys.readouterr()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code, captured.out, captured.err


def _records(env: _Env, kind: str) -> list[dict[str, typing.Any]]:
    return [
        record
        for path in sorted(env.records.glob("*.json"))
        if (record := json.loads(path.read_text(encoding="utf-8")))["kind"] == kind
    ]


def _log_files(env: _Env) -> list[pathlib.Path]:
    return sorted((env.state / "agent-toolkit" / "run-skill").glob("*.log"))


def _read_terminal_until(fd: int, expected: str, received: bytes = b"") -> bytes:
    """PTYのstderrから指定の進捗が届くまで、期限を付けて読む。"""
    deadline = time.monotonic() + 20
    while expected.encode() not in received:
        remaining = deadline - time.monotonic()
        assert remaining > 0, received.decode(errors="replace")
        ready, _, _ = select.select([fd], [], [], remaining)
        assert ready, received.decode(errors="replace")
        received += os.read(fd, 65536)
    return received


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


@pytest.mark.parametrize(("mode", "expected"), [("normal", 0), ("failed", 7), ("timeout", 124)])
def test_session_readers_close_both_pipes(
    engine_env: _Env,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    expected: int,
) -> None:
    """公開CLIの正常・非0・timeoutのいずれも、読み終えた実pipeを閉じて結果とログを返す。"""
    real_popen = subprocess.Popen
    sessions: list[subprocess.Popen[typing.Any]] = []

    def popen(argv: list[str], **kwargs: typing.Any) -> typing.Any:
        # 被検証関数へ所有権を渡し、reader自身によるcloseを観測する。
        process = real_popen(argv, **kwargs)  # pylint: disable=consider-using-with
        if "--session-id" in argv:
            sessions.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    arguments = ["--target-repo", str(engine_env.repo), "pipe-skill"]
    if mode == "failed":
        monkeypatch.setenv("FAKE_SESSION_EXIT", "7")
    elif mode == "timeout":
        monkeypatch.setenv("FAKE_SESSION_HOLD_FILE", str(tmp_path / "never-release"))
        arguments[0:0] = ["--timeout", "3"]
    code, _out, _err = _run(arguments, capsys)
    assert code == expected
    assert len(sessions) == 1
    session = sessions[0]
    assert session.stdout is not None and session.stdout.closed
    assert session.stderr is not None and session.stderr.closed
    log = _log_files(engine_env)[0].read_text(encoding="utf-8")
    assert "session stdout line" in log
    assert "session stderr line" in log


def test_claude_session_runs_skill_with_goal_and_reports_log(engine_env: _Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Claude候補を採用した起動は非対話の`/goal`付きで対象リポジトリのrootから始まり、成功行でログの場所を示す。

    目的文に報告用UWIの保存と過去の実行のUWIの適用が欠けると、定期実行の結果がユーザーへ届かず、
    回答済みの対応も実施されない。
    """
    code, out, err = _run(
        ["--target-repo", str(engine_env.repo / "sub"), "--args", "対象: web", "example-plugin:check-logs"], capsys
    )

    assert code == 0, err
    sessions = _records(engine_env, "session")
    assert len(sessions) == 1
    argv = sessions[0]["argv"]
    assert argv[:2] == ["claude", "-p"]
    for option, value in (
        ("--permission-mode", "auto"),
        ("--permission-prompts", "none"),
        ("--model", "sonnet"),
        ("--effort", "high"),
    ):
        assert argv[argv.index(option) + 1] == value
    session_id = argv[argv.index("--session-id") + 1]
    goal = argv[-1]
    assert goal.startswith('/goal <atk-auto source="run-skill" kind="goal">')
    for expected in (
        "example-plugin:check-logs",
        "スキルへ渡す引数: 対象: web",
        "`agent-toolkit:completion-report`の報告用UWI",
    ):
        assert expected in goal
    # 目的文が案内する過去の実行のUWIの手順は、`agent-toolkit:user-confirmation-and-report`の
    # 参照文書の節として実在する必要がある。
    # 節を移した後に案内だけが旧い所在を指すと、子セッションは手順へ到達できない。
    reference = re.search(r"`agent-toolkit:user-confirmation-and-report`の`(references/[^`]+)`「([^」]+)」", goal)
    assert reference is not None and "過去の実行のUWI" in reference[2]
    skill_dir = pathlib.Path(__file__).resolve().parents[2] / "skills" / "user-confirmation-and-report"
    assert f"## {reference[2]}" in (skill_dir / reference[1]).read_text(encoding="utf-8").splitlines()
    assert pathlib.Path(sessions[0]["cwd"]).resolve() == engine_env.repo.resolve()
    lines = out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("成功: ")
    log_path = _log_files(engine_env)[0]
    assert str(log_path) in lines[0]
    log = log_path.read_text(encoding="utf-8")
    for expected in (
        "採用した候補: claude:sonnet/high",
        f"Claudeのセッション識別子: {session_id}",
        "[stdout] session stdout line",
        "[stderr] session stderr line",
        "終了状態: exit code 0",
    ):
        assert expected in log


def test_codex_session_receives_goal_without_slash_command(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexは非対話起動で`/goal`を目標として扱わないため、`atk-auto`要素の目的文だけを渡す。"""
    set_orchestrate_environment(monkeypatch, "codex:gpt-5.6-sol/low")

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    sessions = _records(engine_env, "session")
    assert sessions[0]["argv"][:6] == ["codex", "exec", "--model", "gpt-5.6-sol", "-c", "model_reasoning_effort=low"]
    assert sessions[0]["argv"][-1].startswith('<atk-auto source="run-skill" kind="goal">')


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("mode", ["missing", "question", "other", "unsynced"])
def test_normal_exit_requires_same_run_synced_report(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], engine: str, mode: str
) -> None:
    """子の正常終了と報告保存を独立に変え、未保存・途中確認・他実行・未commitの更新を拒否する。"""
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")
    monkeypatch.setenv("FAKE_REPORT_MODE", mode)
    code, out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)
    assert code == 1
    assert not out
    assert "報告用UWI" in err and "同期契約" in err and "次の操作:" in err
    assert str(_log_files(engine_env)[0]) in err


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("push_fails", [False, True])
def test_report_requires_remote_push(
    engine_env: _Env,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
    push_fails: bool,
) -> None:
    """実bare remoteへ報告が到達した場合に成功し、commit後のpush失敗は保存未達になる。"""
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    origin = git_repository.init_bare_repository(tmp_path / "origin.git")
    git_repository.commit_all(notes, "initial")
    git_repository.run_git(notes, "remote", "add", "origin", str(origin))
    git_repository.run_git(notes, "push", "-u", "origin", "HEAD")
    monkeypatch.setenv("FAKE_REPORT_REMOTE", "1")
    if push_fails:
        hook = origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        monkeypatch.setenv("FAKE_REPORT_PUSH_FAIL", "1")
    code, out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)
    assert code == (1 if push_fails else 0), err
    assert bool(out) is not push_fails


def test_wi_save_and_edit_bind_report_to_run(
    engine_env: _Env,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """WIの実保存と公開editは実行IDと報告区分を保持し、更新した本文のcommitを照会できる。"""
    del engine_env
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", "run-1")
    names = wi_add.add_entries(
        notes,
        messages=["---\nrun_skill_report: true\n---\n実行結果を確認してください。"],
        target_repo="github.com/example/repo",
        source="run-skill",
        now=datetime.datetime(2026, 10, 18),
        entry_type="uwi",
        scope="my-skill",
        question_type="post-approval",
    )
    path = notes / "inbox" / names[0]
    saved = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert saved is not None and saved[0]["run_skill_run_id"] == "run-1"
    assert wi_add.has_synced_run_report(notes, run_id="run-1", skill="my-skill")
    body_file = tmp_path / "report-update.md"
    body_file.write_text("更新後の実行結果を確認してください。", encoding="utf-8")
    with pytest.raises(SystemExit) as updated:
        atk.main(
            [
                "wi",
                "edit",
                names[0],
                "--body-file",
                str(body_file),
                "--target-repo=github.com/example/repo",
            ],
            home=tmp_path,
        )
    assert updated.value.code == 0, capsys.readouterr().err
    saved = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert saved is not None and saved[0]["run_skill_run_id"] == "run-1"
    assert "更新後の実行結果" in saved[1]
    assert wi_add.has_synced_run_report(notes, run_id="run-1", skill="my-skill")


def test_child_session_does_not_inherit_process_loop_markers(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """起動元がprocess-loopの子でも、本作業のセッションへprocess-loopの環境印と再起動要求の受け渡し先を渡さない。

    環境印が渡ると、対話型のCLI本体を終了させる工程を求めるStopの判定が働き、
    最終応答で終わる非対話セッションが時間上限まで終われなくなる。
    再起動要求の受け渡し先が渡ると、セッションの子孫が親のprocess-loopへ再起動対象を書き込める。
    """
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "parent-session")
    monkeypatch.setenv("AGENT_TOOLKIT_RESTART_SPEC", str(engine_env.records / "restart-spec.json"))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    env = _records(engine_env, "session")[0]["env"]
    assert env["AGENT_TOOLKIT_PROCESS_LOOP_SESSION"] is None
    assert env["AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"] is None
    assert env["AGENT_TOOLKIT_DELEGATED_SESSION"] is None
    assert env["AGENT_TOOLKIT_RESTART_SPEC"] is None


def test_descendant_holding_output_does_not_delay_result(
    engine_env: _Env, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """子セッションの終了後に出力のパイプを保持する子孫が残っても、上限付きの待ちの後に結果を返す。

    出力の終端を待ち続けると、子孫が終わるまで`--timeout`の外で終了コードを返さない。
    """
    monkeypatch.setattr(run_skill, "OUTPUT_DRAIN_SECONDS", 0.5)
    pid_file = tmp_path / "detached.pid"
    monkeypatch.setenv("FAKE_SESSION_DETACH_PID_FILE", str(pid_file))
    try:
        started = time.monotonic()
        code, out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)
        elapsed = time.monotonic() - started

        assert code == 0, err
        assert out.startswith("成功: ")
        assert pid_file.exists(), "出力を保持する子孫が起動しなかった"
        assert psutil.pid_exists(int(pid_file.read_text(encoding="utf-8"))), "子孫が先に終わり、待ちの上限を検証できない"
        assert elapsed < 60
        log = _log_files(engine_env)[0].read_text(encoding="utf-8")
        assert "[stdout] session stdout line" in log
        assert "出力の読み取りを打ち切った" in log
    finally:
        if pid_file.exists():
            with contextlib.suppress(psutil.Error):
                psutil.Process(int(pid_file.read_text(encoding="utf-8"))).kill()


def test_abnormal_session_exit_propagates_code_with_failure_line(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """子セッションの非0終了はその終了コードと、ログの場所を含む失敗行を標準エラーへ返す。"""
    monkeypatch.setenv("FAKE_SESSION_EXIT", "3")

    code, out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 3
    assert not out
    assert err.startswith("失敗: ")
    assert str(_log_files(engine_env)[0]) in err


def test_same_skill_is_not_started_twice_but_other_skill_runs(
    engine_env: _Env, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ対象リポジトリと同じスキルの実行中は起動せず1で終わり、別のスキルは並行して起動する。"""
    started = tmp_path / "started"
    release = tmp_path / "release"
    env = {**os.environ, "FAKE_SESSION_STARTED_FILE": str(started), "FAKE_SESSION_HOLD_FILE": str(release)}
    agent_toolkit_root = pathlib.Path(run_skill.__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join((str(agent_toolkit_root), env.get("PYTHONPATH", "")))
    first = subprocess.Popen(  # pylint: disable=consider-using-with
        [
            sys.executable,
            "-c",
            "import sys; from agent_toolkit import atk; atk.main(sys.argv[1:])",
            "run-skill",
            "--target-repo",
            str(engine_env.repo),
            "busy-skill",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 60
        while not started.exists():
            assert first.poll() is None, first.communicate()
            assert time.monotonic() < deadline, "1回目のセッションが開始しなかった"
            time.sleep(0.05)

        code, out, err = _run(["--target-repo", str(engine_env.repo), "busy-skill"], capsys)
        assert code == run_skill.CONCURRENT_EXIT_CODE
        assert not out
        assert err.startswith("失敗: ")
        assert len(_records(engine_env, "session")) == 1

        release.write_text("release")
        other_code, _other_out, other_err = _run(["--target-repo", str(engine_env.repo), "other-skill"], capsys)
        assert other_code == 0, other_err
        assert len(_records(engine_env, "session")) == 2
    finally:
        release.write_text("release")
        try:
            first.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            first.kill()
            first.wait(timeout=60)
            if os.name != "nt":
                assert first.stdout is not None
                assert first.stderr is not None
                first.stdout.close()
                first.stderr.close()
            raise
    assert first.returncode == 0


def test_timeout_terminates_session_and_descendants(
    engine_env: _Env, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """時間上限を超えると子セッションと子孫を終了させ、124と失敗行で終わる。

    子孫が残ると、定期実行のたびに終わらないプロセスが積み上がる。
    """
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_SESSION_SPAWN_PID_FILE", str(pid_file))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "--timeout", "3", "my-skill"], capsys)

    assert code == run_skill.TIMEOUT_EXIT_CODE
    assert err.startswith("失敗: ")
    assert pid_file.exists(), "子孫プロセスが時間上限より前に起動しなかった"
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while psutil.pid_exists(grandchild) and psutil.Process(grandchild).status() != psutil.STATUS_ZOMBIE:
        assert time.monotonic() < deadline, "子孫プロセスが終了しなかった"
        time.sleep(0.05)
    assert "時間上限（3秒）を超えたため終了させた" in _log_files(engine_env)[0].read_text(encoding="utf-8")


def test_usage_limit_retries_same_claude_candidate(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """可用性判定がClaudeの利用上限を返した場合は次の候補へ進まず、解除を待って同じ候補を判定し直す。"""
    set_orchestrate_environment(monkeypatch, "claude:opus/high,codex:gpt-5.6-sol/low")
    monkeypatch.setenv("FAKE_PROBE_REJECT_COUNT", "1")
    sleeps: list[float] = []
    monkeypatch.setattr(orchestrator, "time", types.SimpleNamespace(sleep=sleeps.append, time=time.time))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert [record["name"] for record in _records(engine_env, "probe")] == ["claude", "claude"]
    assert sleeps == [claude_usage_limit.RECHECK_SECONDS]
    assert _records(engine_env, "session")[0]["name"] == "claude"
    assert "利用上限の解除待ち: claude:opus/high" in _log_files(engine_env)[0].read_text(encoding="utf-8")


def test_logs_older_than_retention_are_removed(engine_env: _Env, capsys: pytest.CaptureFixture[str]) -> None:
    """各実行の開始時に保持期間（30日）を超えたログだけを削除する。"""
    log_dir = engine_env.state / "agent-toolkit" / "run-skill"
    log_dir.mkdir(parents=True)
    now = time.time()
    old = log_dir / "old.log"
    recent = log_dir / "recent.log"
    for path, age_days in ((old, 31), (recent, 29)):
        path.write_text("過去の実行\n", encoding="utf-8")
        mtime = now - age_days * 24 * 60 * 60
        os.utime(path, (mtime, mtime))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert not old.exists()
    assert recent.exists()


@pytest.mark.parametrize("engine,limit_type", [("claude", "five_hour"), ("claude", "seven_day"), ("codex", "")])
@pytest.mark.parametrize("progress", [False, True])
def test_usage_limit_resumes_work_conversation_outside_timeout(
    engine_env: _Env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
    limit_type: str,
    progress: bool,
) -> None:
    """公開CLIは表示設定によらず本作業の上限を待ち、同じ会話と実行時間予算を引き継ぐ。

    CLIイベントとrolloutは20261014-210511-001/002の実現性で観測された形を使う。
    """
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/high")
    monkeypatch.setenv("FAKE_SESSION_LIMIT_COUNT", "2")
    monkeypatch.setenv("FAKE_LIMIT_TYPE", limit_type)
    monkeypatch.setenv("CODEX_HOME", str(engine_env.state / "codex"))
    monkeypatch.setattr(sys.stderr, "isatty", lambda: progress)
    sleeps: list[float] = []
    clock = [1000.0]

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(
        run_skill,
        "time",
        types.SimpleNamespace(
            time=lambda: clock[0],
            monotonic=lambda: clock[0],
            sleep=sleep,
        ),
    )
    code, out, err = _run(["--target-repo", str(engine_env.repo), "--timeout", "2", "resume-skill"], capsys)
    assert code == 0, err
    assert out.startswith("成功: ") and len(out.splitlines()) == 1
    assert sleeps == [10, 300]
    sessions = _records(engine_env, "session")
    assert len(sessions) == 3
    first = sessions[0]["argv"]
    for session in sessions[1:]:
        argv = session["argv"]
        if engine == "claude":
            assert argv[argv.index("--resume") + 1] == first[first.index("--session-id") + 1]
            assert "--session-id" not in argv
        else:
            assert argv[:3] == ["codex", "exec", "resume"]
            assert argv[-2] == "01234567-89ab-cdef-0123-456789abcdef"
        assert argv[-1].startswith('<atk-auto source="run-skill" kind="usage-limit-resume">')
        assert argv[argv.index("--model") + 1] == "test-model"
    log = _log_files(engine_env)[0].read_text(encoding="utf-8")
    assert log.count("利用上限の解除待ち:") == 2
    run_ids = {session["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"] for session in sessions}
    assert len(run_ids) == 1 and None not in run_ids
    reports = list((pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"]) / "inbox").glob("*.md"))
    assert len(reports) == 1
    saved = reports[0].read_text(encoding="utf-8")
    assert next(iter(run_ids)) in saved
    assert saved.count("再開後の報告更新") == 2
