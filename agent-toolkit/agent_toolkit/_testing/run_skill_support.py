"""run_skillの分割試験で共有する準備と補助処理。"""

from __future__ import annotations

import html
import io
import json
import os
import pathlib
import re
import select
import stat
import subprocess
import sys
import time
import typing

import psutil
import pytest

from agent_toolkit import atk, hook
from agent_toolkit._atk import run_skill
from agent_toolkit._common import process_identity, process_tree
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment

_FAKE_ENGINE = """\
#!{python}
import json, os, pathlib, subprocess, sys, time, uuid
import datetime, re, psutil
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
def hold(release):
    release = pathlib.Path(release)
    release.with_suffix('.ready').touch()
    deadline = time.monotonic() + 120
    while not release.exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
record_dir = pathlib.Path(os.environ["FAKE_RECORD_DIR"])
probe = "availability-probe" in args[-1]
kind = "child" if args[:1] == ["--hold-child"] else "probe" if probe else "session"
record = {{
    "name": name,
    "kind": kind,
    "argv": [name, *args],
    "cwd": os.getcwd(),
    "pid": os.getpid(),
    "created_at": psutil.Process().create_time(),
    "connection": "subscription" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else
                  "payg" if os.environ.get("ANTHROPIC_API_KEY") else "inherit",
    "env": {{key: os.environ.get(key) for key in (
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_RESTART_SPEC",
        "AGENT_TOOLKIT_RUN_SKILL_ID",
        "AGENT_TOOLKIT_SESSION_START_INPUT",
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS",
    )}},
}}
record_path = record_dir / f"{{time.time_ns()}}-{{uuid.uuid4().hex}}.json"
pending = record_path.with_suffix('.tmp')
pending.write_text(json.dumps(record), encoding="utf-8")
pending.replace(record_path)
if kind == "child":
    hold(sys.argv[2])
    sys.exit(0)
if probe:
    if os.environ.get("FAKE_CONNECTION_LIMIT") and record["connection"] == "subscription" and (record_dir / "limited").exists():
        print(json.dumps({{"type": "rate_limit_event", "rate_limit_info": {{
            "status": "rejected", "rateLimitType": "five_hour", "resetsAt": int(time.time()) + 3600
        }}}}))
        sys.exit(1)
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
if os.environ.get("FAKE_RECORD_THREAD") and name == "codex":
    print(json.dumps({{"type": "thread.started", "thread_id": "01234567-89ab-cdef-0123-456789abcdef"}}), flush=True)
if os.environ.get("FAKE_CONNECTION_LIMIT") and record["connection"] == "subscription":
    (record_dir / "limited").write_text("limited", encoding="utf-8")
    print(json.dumps({{"type": "rate_limit_event", "rate_limit_info": {{
        "status": "rejected", "rateLimitType": "five_hour", "resetsAt": int(time.time()) + 3600
    }}}}), flush=True)
    hold(record_dir / "limit.release")
    sys.exit(1)
if os.environ.get("FAKE_REPORT_MODE", "saved") != "missing":
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    mode = os.environ.get("FAKE_REPORT_MODE", "saved")
    run_id = os.environ["AGENT_TOOLKIT_RUN_SKILL_ID"]
    data = json.loads(os.environ["AGENT_TOOLKIT_SESSION_START_INPUT"])
    scope = data["skill"] + (" " + data["mode"] if data["mode"] else "")
    (notes / "2026/10").mkdir(parents=True, exist_ok=True)
    old = notes / "2026/10" / (run_id + ".md")
    body = old.read_text().split("\\n---\\n", 1)[1] + "\\n再開後の報告更新\\n" if old.exists() else "定期実行の報告\\n"
    saved_id = "other-run" if mode == "other" else run_id
    if os.environ.get("FAKE_REPORT_ANSWER"):
        body += "\\n## 回答\\n\\n<!-- ユーザーはこの行以降に回答を追記する -->\\n"
    old.write_text("---\\nstate: inbox\\ntype: uwi\\nsource: run-skill\\nscope: " + json.dumps(scope) +
        "\\nrun_skill_report: " + ("false" if mode == "question" else "true") +
        "\\nrun_skill_run_id: " + saved_id + "\\n---\\n" + body)
    subprocess.run(["git", "-C", str(notes), "add", "."], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(notes), "commit", "-qm", "report"], check=True)
    if os.environ.get("FAKE_REPORT_REMOTE"):
        pushed = subprocess.run(["git", "-C", str(notes), "push"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if pushed.returncode and not os.environ.get("FAKE_REPORT_PUSH_FAIL"):
            sys.exit(pushed.returncode)
    if os.environ.get("FAKE_REPORT_ANSWER"):
        old.write_text(old.read_text().replace("state: inbox", "state: adopted") + "了解した。\\n")
        subprocess.run(["git", "-C", str(notes), "add", "."], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(notes), "commit", "-qm", "answer"], check=True)
    if mode in ("other", "unsynced"):
        old.write_text(old.read_text() + "\\n未同期の更新\\n")
        if mode == "other":
            subprocess.run(["git", "-C", str(notes), "add", "."], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(notes), "commit", "-qm", "other report"], check=True)
if os.environ.get("AGENT_TOOLKIT_SESSION_REVIEW_BATCH"):
    from agent_toolkit._common import session_review
    manifest_path = pathlib.Path(os.environ["AGENT_TOOLKIT_SESSION_REVIEW_BATCH"])
    session_review.BatchWriter(manifest_path).analyze()
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
        deadline = time.monotonic() + 120
        while not (progress_dir / str(index)).exists():
            if os.environ.get("FAKE_SESSION_HOLD_FILE") and pathlib.Path(os.environ["FAKE_SESSION_HOLD_FILE"]).exists():
                sys.exit(int(os.environ.get("FAKE_SESSION_EXIT", "0")))
            assert time.monotonic() < deadline
            time.sleep(0.01)
        print(json.dumps(event), flush=True)
if os.environ.get("FAKE_SESSION_STARTED_FILE"):
    pathlib.Path(os.environ["FAKE_SESSION_STARTED_FILE"]).write_text("started")
if os.environ.get("FAKE_SESSION_SPAWN_PID_FILE"):
    release = pathlib.Path(os.environ["FAKE_SESSION_SPAWN_PID_FILE"] + '.release')
    child = subprocess.Popen([sys.executable, __file__, '--hold-child', str(release)])
    deadline = time.monotonic() + 30
    while not release.with_suffix('.ready').exists():
        assert child.poll() is None and time.monotonic() < deadline
        time.sleep(0.01)
    pathlib.Path(os.environ["FAKE_SESSION_SPAWN_PID_FILE"]).write_text(str(child.pid))
    child.wait(timeout=120)
if os.environ.get("FAKE_SESSION_DETACH_PID_FILE"):
    # 出力のパイプを継承したまま残る子孫を起動し、自身は直ちに終わる。
    release = pathlib.Path(os.environ["FAKE_SESSION_DETACH_PID_FILE"] + '.release')
    child = subprocess.Popen([sys.executable, __file__, '--hold-child', str(release)])
    deadline = time.monotonic() + 30
    while not release.with_suffix('.ready').exists():
        assert child.poll() is None and time.monotonic() < deadline
        time.sleep(0.01)
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
def _engine_env(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> _Env:
    """偽の実行ファイル、隔離した状態ディレクトリと対象リポジトリを用意する。"""
    request.getfixturevalue("leaf_git_commands")
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
    owned: list[subprocess.Popen[typing.Any]] = []
    stop_owned = process_tree.stop_process_tree

    class OwnedPopen(subprocess.Popen):
        def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
            super().__init__(*args, **kwargs)
            if (
                isinstance(self.args, (list, tuple))
                and self.args
                and pathlib.Path(os.fsdecode(self.args[0])).name in {"claude", "codex"}
            ):
                owned.append(self)

    def reclaim() -> None:
        """失敗位置に依存せず直接のhandleと、別controllerが記録した同世代の偽CLIを回収する。"""
        failures: list[BaseException] = []
        for process in owned:
            try:
                if process.poll() is None:
                    stop_owned(process)
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            except BaseException as error:
                failures.append(error)
        # 正常終了済みはPID単独でも停止せず、親を失ったchildも起動時刻と実体で識別する。
        for record_path in records.glob("*.json"):
            record = json.loads(record_path.read_text(encoding="utf-8"))
            try:
                recorded_process = psutil.Process(record["pid"])
                if not process_identity.matches(recorded_process, record["created_at"]):
                    continue
                command = recorded_process.cmdline()
                # kill後のexit途中ではcmdlineが先に空になり、statusは一時的にrunningのままになる。
                # 所有不明のまま信号を送らず、同世代の資源解放だけを有限に確認する。
                if (
                    not command
                    and not process_tree.wait_until_released([recorded_process], timeout=1)
                    and (
                        not recorded_process.is_running()
                        or recorded_process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
                    )
                ):
                    continue
                assert any(
                    pathlib.Path(item).is_absolute() and pathlib.Path(item).is_relative_to(tmp_path) for item in command[:2]
                ), command[:2]
                result = process_tree.stop_tree(recorded_process)
                assert not result.residual and not result.unresolved, result.descriptions()
            except psutil.NoSuchProcess:
                continue
            except BaseException as error:
                failures.append(error)
        owned.clear()
        if failures:
            raise BaseExceptionGroup(
                f"偽CLIの所有資源を回収できない。次の操作: {records}のPID・created_atと"
                "内側の例外を確認し、同じ世代の残存を回収してから試験を再実行する。",
                failures,
            )

    request.addfinalizer(reclaim)
    monkeypatch.setattr(subprocess, "Popen", OwnedPopen)
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


def _startup_input(record, monkeypatch, capsys, *, source="startup"):
    """実子へ渡された環境をSessionStartへ適用し、消費側のデータと参照先を確かめる。"""
    raw = record["env"]["AGENT_TOOLKIT_SESSION_START_INPUT"]
    with monkeypatch.context() as scoped:
        scoped.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", raw)
        payload = json.dumps({"hook_event_name": "SessionStart", "source": source}).encode("utf-8")
        scoped.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
        assert hook.main(["rules_context_codex" if record["name"] == "codex" else "rules_context"]) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    match = re.search(r'<atk-auto source="run-skill" kind="startup-input">\n起動入力: (.*)\n', context)
    assert match is not None
    data = json.loads(html.unescape(match[1]))
    assert data == json.loads(raw)
    assert "references/answer-transitions.md" in context and "playbooks/report-completion.md" in context
    if data["batch_manifest"]:
        assert "playbooks/review-session-batch.md" in context
    return data


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
