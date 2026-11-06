"""独立受付の公開操作と実プロセス境界を、隔離notesで確認する。"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import io
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any

import filelock
import pytest

from agent_toolkit import atk, hook
from agent_toolkit._atk import orchestrator, run_skill
from agent_toolkit._atk.wi import add, layout, submit
from agent_toolkit._common import process_tree, recorded_process, session_registry, state_paths
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment

_REQUEST: dict[str, Any] = {
    "request_id": "request-1",
    "target_repo": "github.com/example/product",
    "source": "agent",
    "origin": {"kind": "agent", "text": "登録する確定要求の原文"},
    "requirements": ["動作を改善する"],
    "observations": ["再現した事象"],
    "evidence": ["観測の実本文"],
    "update_targets": ["新規登録する。更新対象なし"],
}

_ENGINE = """\
#!{python}
import datetime, json, os, pathlib, re, sys
if "availability-probe" in sys.argv[-1]:
    print(json.dumps({{"type": "result", "is_error": False}}))
    sys.exit(0)
from agent_toolkit._atk.wi import add, submit
from agent_toolkit._common import private_notes
startup = json.loads(os.environ["AGENT_TOOLKIT_SESSION_START_INPUT"])
path = pathlib.Path(re.search(r"独立WI受付: (.*?)。", startup["arguments"]).group(1))
run = json.loads(path.read_text(encoding="utf-8"))
assert pathlib.Path.cwd() == pathlib.Path(run["work_dir"])
assert not any(name in os.environ for name in (
    "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID", "AGENT_TOOLKIT_LANE_SESSION_ID",
    "AGENT_TOOLKIT_RESTART_SPEC", "AGENT_TOOLKIT_HANDOFF_PATH", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
    "GIT_INDEX_FILE", "GIT_WORK_TREE"
))
notes = private_notes.default_private_notes()
primary = min(item["request_id"] for item in run["requests"])
binding = json.dumps(dict([(startup["run_id"], [primary])]))
body = "---\\nwi_submit_bindings: " + binding + "\\n---\\n# 確認\\n確定要求を確認した。"
saved = add.add_entries(notes, messages=[body], target_repo=run["target_repo"],
    source="agent", now=datetime.datetime.now(datetime.UTC), entry_type="uwi", question_type="yes-no")
result = submit.Result(run_id=startup["run_id"], results=[submit.Disposition(
    request_id=item["request_id"], status="reused" if item["request_id"] == primary else "unnecessary",
    files=saved if item["request_id"] == primary else [], reason="隔離した要求ごとの判定"
) for item in run["requests"]])
result_path = path.parent / "finish-input.json"
result_path.write_text(result.model_dump_json(), encoding="utf-8")
from agent_toolkit import atk
try:
    atk.main(["wi", "submit", "--finish", str(result_path)])
except SystemExit as error:
    assert error.code == 0
add.add_entries(notes, messages=["---\\nrun_skill_report: true\\n---\\n\\n投入結果を確認した。\\n" + result.model_dump_json()],
    target_repo=run["target_repo"], source="run-skill", now=datetime.datetime.now(datetime.UTC),
    entry_type="uwi", scope=submit.SKILL, question_type="yes-no")
print(json.dumps({{"type": "result", "is_error": False}}))
"""

_EXECUTOR = """\
import datetime, json, os, pathlib, sys, time
from agent_toolkit._atk.wi import add, submit
from agent_toolkit._common import private_notes
mode, run_id = sys.argv[1:]
path = pathlib.Path(os.environ["XDG_STATE_HOME"]) / "agent-toolkit/wi-submit/runs" / run_id / "run.json"
run = json.loads(path.read_text(encoding="utf-8"))
count_path = path.parent / "attempts"
count = int(count_path.read_text()) + 1 if count_path.exists() else 1
count_path.write_text(str(count))
print("executor stdout", flush=True)
print("executor stderr", file=sys.stderr, flush=True)
(path.parent / "ready").write_text("ready")
if mode == "timeout":
    time.sleep(30)
concurrent = mode == "concurrent-always" or mode == "concurrent-once" and count == 1
code = 1 if concurrent else 9 if mode == "abnormal" else 0
if not concurrent and mode in {"normal", "concurrent-once"}:
    os.environ["AGENT_TOOLKIT_RUN_SKILL_ID"] = run_id
    submit.finish(submit.Result(run_id=run_id, results=[submit.Disposition(
        request_id=item, status="unnecessary", files=[], reason="隔離シナリオで不要と確定した"
    ) for item in run["request_ids"]]))
    notes = private_notes.default_private_notes()
    if not add.has_synced_run_report(notes, run_id=run_id, skill=submit.SKILL):
        add.add_entries(notes, messages=["---\\nrun_skill_report: true\\n---\\n\\n処置を完了した。"],
            target_repo=run["target_repo"], source="run-skill", now=datetime.datetime.now(datetime.UTC),
            entry_type="uwi", question_type="yes-no", scope=submit.SKILL)
(path.parent / "exit.json").write_text(json.dumps({"code": code, "concurrent": concurrent}))
sys.exit(code)
"""


@pytest.fixture(name="notes")
def _notes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    notes = tmp_path / "notes"
    git_repository.init_repository(notes)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    return notes


def _call(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "submit", *arguments])
    captured = capsys.readouterr()
    assert isinstance(result.value.code, int)
    return result.value.code, captured.out, captured.err


def _wait_receipt(identifier: str) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = submit.show(identifier)
        if value["run"] and value["run"]["status"] in {"failed", "completed"}:
            owner = session_registry.ProcessIdentity(**value["receipt"]["owner"])
            if not owner.exists():
                return value
        time.sleep(0.02)
    return pytest.fail(f"独立監督が終了しなかった: {submit.show(identifier)}")


@pytest.mark.skipif(os.name == "nt", reason="POSIXのshebangと独立セッションの実境界を確認する")
@pytest.mark.parametrize("batched", [False, True], ids=["single", "batch"])
@pytest.mark.usefixtures("share_package_caches")
def test_parent_exit_resend_ticket_and_synced_finish(notes, tmp_path, monkeypatch, capsys, host_environ, batched):
    """親は受付直後に終了し、子が正式WI・finish・同一実行報告を保存する。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    engine = bin_dir / "claude"
    engine.write_text(_ENGINE.format(python=sys.executable), encoding="utf-8")
    engine.chmod(0o700)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    set_orchestrate_environment(monkeypatch, "claude:sonnet/high")
    monkeypatch.setenv("PYTHONPATH", str(pathlib.Path(submit.__file__).resolve().parents[3]))
    for name in (
        "AGENT_TOOLKIT_OWNER_SESSION",
        "CODEX_THREAD_ID",
        "AGENT_TOOLKIT_LANE_SESSION_ID",
        "AGENT_TOOLKIT_RESTART_SPEC",
        "AGENT_TOOLKIT_HANDOFF_PATH",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "GIT_INDEX_FILE",
        "GIT_WORK_TREE",
    ):
        monkeypatch.setenv(name, "parent")
    source = tmp_path / "product" / "plugin"
    shutil.copytree(
        pathlib.Path(submit.__file__).resolve().parents[3],
        source,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".git", "*_test.py"),
    )
    monkeypatch.setenv("AGENT_TOOLKIT_RUNTIME_DATA", str(tmp_path / "runtime"))
    uv = shutil.which("uv", path=host_environ()["PATH"])
    assert uv is not None
    bootstrap = [uv, "run", "--no-config", "--no-project", "--python", "3.15", "python"]
    prepared = subprocess.run(
        [*bootstrap, str(source / "scripts/launch.py"), "--prepare", "agent_toolkit/atk.py"],
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        timeout=45,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr.decode()
    resolved = subprocess.run(
        [
            *bootstrap,
            "-c",
            "import os,pathlib,sys; from agent_toolkit._common import plugin_environment; "
            "print(plugin_environment.prepared_python(pathlib.Path(sys.argv[1]),os.environ))",
            str(source),
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr.decode()
    shared_python = pathlib.Path(resolved.stdout.decode().strip())
    assert shared_python.is_file()
    assert not shared_python.is_relative_to(source)
    engine.write_text(_ENGINE.format(python=shared_python), encoding="utf-8")
    parent = tmp_path / "parent"
    parent.mkdir()
    request = parent / "request.json"
    requests: list[dict[str, Any]] = [_REQUEST]
    if batched:
        requests.append(dict(_REQUEST, request_id="request-2", origin={"kind": "human", "text": "第二の原文"}))
    payload = requests if batched else requests[0]
    request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(
        [str(source / "scripts/atk"), "wi", "submit", "--request-file", str(request)],
        cwd=parent,
        env=os.environ.copy(),
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    accepted = json.loads(result.stdout)
    receipts = accepted if batched else [accepted]
    assert [submit.validate_ticket(item["ticket"]).request_id for item in receipts] == [item["request_id"] for item in requests]
    value = None
    try:
        request.unlink()
        parent.rmdir()
        shutil.rmtree(source.parent)
        value = _wait_receipt("request-1")
        assert value["run"]["status"] == "completed", value
        assert value["run"]["report_synced"]
        assert {item["request_id"]: item for item in value["run"]["requests"]} == {
            item["request_id"]: item for item in requests
        }
        dispositions = value["run"]["result"]["results"]
        assert {item["request_id"]: item["status"] for item in dispositions} == {
            item["request_id"]: "reused" if index == 0 else "unnecessary" for index, item in enumerate(requests)
        }
        observed = [submit.show(item["request_id"]) for item in requests]
        assert {item["run"]["run_id"] for item in observed} == {value["run"]["run_id"]}
        assert len(value["report_files"]) == 1
        report = next(path for path in layout.iter_entry_paths(notes) if path.name == value["report_files"][0])
        assert all(item["request_id"] in report.read_text(encoding="utf-8") for item in requests)
        assert all(item["status"] in report.read_text(encoding="utf-8") for item in dispositions)
        entries = list(layout.iter_entry_paths(notes))
        assert len(entries) == 2
        resend = tmp_path / "resend.json"
        resend.write_text(json.dumps(payload), encoding="utf-8")
        code, output, error = _call(["--request-file", str(resend)], capsys)
        assert code == 0, error
        replayed = json.loads(output) if batched else [json.loads(output)]
        assert [item["ticket"] for item in replayed] == [item["ticket"] for item in receipts]
        assert {item["run"]["run_id"] for item in replayed} == {value["run"]["run_id"]}
        assert len(list(layout.iter_entry_paths(notes))) == 2
        changed = dict(_REQUEST, requirements=["異なる要求"])
        resend.write_text(json.dumps(changed), encoding="utf-8")
        assert _call(["--request-file", str(resend)], capsys)[0] == 1
        invalid = dict(receipts[0]["ticket"], target_repo="github.com/example/other")
        with pytest.raises(ValueError, match="一致"):
            submit.validate_ticket(invalid)
    finally:
        if value is None:
            receipt = submit.show("request-1")
            identity = session_registry.ProcessIdentity(**receipt["receipt"]["owner"])
            assert recorded_process.stop_recorded_executor(identity) is None


@pytest.mark.parametrize("change", ["empty", "duplicate", "target", "invalid"])
def test_public_batch_rejects_all_invalid_input_before_saving(tmp_path, capsys, change):
    requests = [_REQUEST, dict(_REQUEST, request_id="request-2")]
    if change == "empty":
        requests = []
    elif change == "duplicate":
        requests[1] = dict(_REQUEST)
    elif change == "target":
        requests[1]["target_repo"] = "github.com/example/other"
    else:
        requests[1]["request_id"] = "../invalid"
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(requests), encoding="utf-8")
    assert _call(["--request-file", str(path)], capsys)[0] == 1
    assert not list((state_paths.state_dir() / "wi-submit/requests").glob("*.json"))


@pytest.mark.usefixtures("notes")
def test_public_batch_handoff_failure_preserves_every_request(tmp_path, monkeypatch, capsys):
    _defer(monkeypatch)
    requests = [_REQUEST, dict(_REQUEST, request_id="request-2")]
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(requests), encoding="utf-8")
    assert _call(["--request-file", str(path)], capsys)[0] == 1
    observed = [submit.show(item["request_id"]) for item in requests]
    assert len({item["run"]["run_id"] for item in observed}) == 1
    assert all(item["ticket"] is None and item["run"]["status"] == "failed" for item in observed)
    assert {item["request_id"] for item in observed[0]["run"]["requests"]} == {"request-1", "request-2"}
    assert _call(["--request-file", str(path)], capsys)[0] == 1
    assert submit.show("request-1")["run"]["run_id"] == observed[0]["run"]["run_id"]


@pytest.mark.usefixtures("notes")
def test_orphan_stop_failure_keeps_protection_and_guides_operator(monkeypatch, caplog):
    _defer(monkeypatch)
    with pytest.raises(OSError):
        submit.accept(submit.Request.model_validate(_REQUEST))
    value = submit.show("request-1")
    run_id = value["run"]["run_id"]
    owner = session_registry.ProcessIdentity.of(os.getpid())
    assert owner is not None
    path = pathlib.Path(value["state_file"]).parents[1] / "runs" / run_id / "run.json"
    run = value["run"]
    run.update(status="running", owner=None, executor={"pid": owner.pid, "create_time": owner.create_time})
    path.write_text(json.dumps(run), encoding="utf-8")

    def refuse_stop(_identity, **_kwargs):
        return "fixture: 停止未確認"

    def reject_finalization(*_args, **_kwargs):
        pytest.fail("停止未確認の実行を最終化しない")

    def launch(target, **_kwargs):
        assert submit.worker(target) == 0

    monkeypatch.setattr(submit.recorded_process, "stop_recorded_executor", refuse_stop)
    monkeypatch.setattr(submit, "_finalize", reject_finalization)
    monkeypatch.setattr(submit, "_launch_worker", launch)
    with caplog.at_level(logging.ERROR, logger=submit.__name__):
        submit.reobserve()
    assert "atk wi submit --show request-1" in caplog.text
    assert run_id in caplog.text and "PID・作成時刻" in caplog.text and "再投入しない" in caplog.text
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "running"


def _defer(monkeypatch):
    def deferred(*_args, **_kwargs):
        raise OSError("起動機能が停止している")

    monkeypatch.setattr(submit, "_launch_worker", deferred)


def test_spawn_failure_is_durable_and_report_sync_recovers_without_model(notes, tmp_path, monkeypatch, capsys):
    _defer(monkeypatch)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(_REQUEST), encoding="utf-8")
    assert _call(["--request-file", str(request)], capsys)[0] == 1
    value = submit.show("request-1")
    assert value["ticket"] is None
    assert value["run"]["status"] == "failed"
    original_run = value["run"]["run_id"]
    assert _call(["--request-file", str(request)], capsys)[0] == 1
    assert submit.show("request-1")["run"]["run_id"] == original_run
    target = hashlib.sha256(_REQUEST["target_repo"].encode()).hexdigest()
    original = submit.add.add_entries

    def fail_after_save(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("同期応答を失った")

    with monkeypatch.context() as scoped:
        scoped.setattr(submit.add, "add_entries", fail_after_save)
        assert submit.worker(target) == 0
    assert not submit.show("request-1")["run"]["report_synced"]
    assert len(list(layout.iter_entry_paths(notes))) == 1
    assert submit.worker(target) == 0
    assert submit.show("request-1")["run"]["report_synced"]
    assert len(list(layout.iter_entry_paths(notes))) == 1


def test_report_git_timeout_is_unreported_and_resumes_same_report(notes, monkeypatch):
    _defer(monkeypatch)
    with pytest.raises(OSError):
        submit.accept(submit.Request.model_validate(_REQUEST))
    run_id = submit.show("request-1")["run"]["run_id"]
    target = hashlib.sha256(_REQUEST["target_repo"].encode()).hexdigest()

    def expired(command, *_args, **_kwargs):
        raise subprocess.TimeoutExpired(["git", *command], 0.1)

    with monkeypatch.context() as scoped:
        scoped.setattr(submit.git_command, "run", expired)
        assert submit.worker(target) == 0
    before = submit.show("request-1")["run"]
    assert not before["report_synced"]
    assert "timed out" in before["report_error"]
    assert submit.worker(target) == 0
    after = submit.show("request-1")["run"]
    assert after["run_id"] == run_id
    assert after["report_synced"]
    assert len(list(layout.iter_entry_paths(notes))) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"request_id": "../escape"},
        {"target_repo": "https://github.com/example/product.git"},
        {"requirements": []},
        {"evidence": [" "]},
        {"origin": {"kind": "agent", "text": ""}},
    ],
)
@pytest.mark.usefixtures("notes")
def test_invalid_request_has_no_acceptance(tmp_path, capsys, change):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(dict(_REQUEST, **change)), encoding="utf-8")
    assert _call(["--request-file", str(path)], capsys)[0] == 1
    assert not list((state_paths.state_dir() / "wi-submit" / "requests").glob("*.json"))


def test_finish_checks_execution_set_real_wi_and_preserves_completed_dispositions(notes, monkeypatch):
    root = state_paths.state_dir() / "wi-submit"
    run_id = "a" * 32
    path = root / "runs" / run_id / "run.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "request_ids": ["request-1"],
                "target_repo": _REQUEST["target_repo"],
                "result": None,
            }
        ),
        encoding="utf-8",
    )
    result = submit.Result(
        run_id=run_id,
        results=[
            submit.Disposition(
                request_id="request-1",
                status="reused",
                files=["20261106-120000-001.md"],
                reason="保存済み",
            )
        ],
    )
    with pytest.raises(ValueError, match="対応するrun-skill"):
        submit.finish(result)
    monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", run_id)
    with pytest.raises(ValueError, match="正式WI"):
        submit.finish(result)
    filename = add.add_entries(
        notes,
        messages=["---\nwi_submit_bindings: " + json.dumps({run_id: ["request-1"]}) + "\n---\n確認用の正式WI"],
        target_repo=_REQUEST["target_repo"],
        source="agent",
        now=datetime.datetime.now(datetime.UTC),
        entry_type="uwi",
        question_type="yes-no",
    )[0]
    result = submit.Result(
        run_id=run_id,
        results=[
            submit.Disposition(
                request_id="request-1",
                status="reused",
                files=[filename],
                reason="保存済み",
            )
        ],
    )
    incomplete = submit.Result(
        run_id=run_id,
        results=[
            submit.Disposition(
                request_id="request-1",
                status="incomplete",
                files=[filename],
                reason="確認UWIの回答待ち",
            )
        ],
    )
    submit.finish(incomplete)
    submit.finish(result)
    submit.finish(result)
    different = result.model_copy(
        update={
            "results": [
                submit.Disposition(
                    request_id="other",
                    status="unnecessary",
                    files=[],
                    reason="不要",
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="受付集合"):
        submit.finish(different)
    changed = result.model_copy(
        update={
            "results": [
                submit.Disposition(
                    request_id="request-1",
                    status="unnecessary",
                    files=[],
                    reason="不要",
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="完了済み"):
        submit.finish(changed)


def _pending(identifier: str) -> None:
    path = state_paths.state_dir() / "wi-submit/requests" / f"{identifier}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "request": dict(_REQUEST, request_id=identifier),
                "run_id": None,
                "owner": None,
                "accepted_at": None,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture(name="executor")
def _executor(tmp_path, monkeypatch):
    """モデル境界だけを実Python子へ置き換え、監督・状態・停止を実行する。"""
    driver = tmp_path / "executor.py"
    driver.write_text(_EXECUTOR, encoding="utf-8")
    original = submit.process_tree.process_scope
    monkeypatch.setattr(submit.plugin_environment, "prepared_python", lambda *_args, **_kwargs: pathlib.Path(sys.executable))

    def install(mode):
        @contextlib.contextmanager
        def launch(argv, **kwargs):
            if argv[1:3] != ["-m", "agent_toolkit._atk.wi.submit"] or argv[-2] != "execute":
                with original(argv, **kwargs) as process:
                    yield process
                return
            with original([sys.executable, str(driver), mode, argv[-1]], **kwargs) as process:
                if mode == "timeout":
                    ready = state_paths.state_dir() / "wi-submit/runs" / argv[-1] / "ready"
                    deadline = time.monotonic() + 15
                    while not ready.exists():
                        assert process.poll() is None
                        assert time.monotonic() < deadline
                        time.sleep(0.01)
                yield process

        monkeypatch.setattr(submit.process_tree, "process_scope", launch)

    return install


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("missing", "finish欠落"),
        ("abnormal", "子の異常終了: 9"),
        ("concurrent-always", "排他待ち上限到達（未実行）"),
        ("timeout", "timeout"),
    ],
)
@pytest.mark.usefixtures("notes")
def test_supervisor_records_failure_and_both_streams(monkeypatch, executor, mode, reason):
    executor(mode)
    _pending("request-1")
    monkeypatch.setattr(submit, "WAIT_SECONDS", 0)
    monkeypatch.setattr(submit, "TIMEOUT_SECONDS", 0.05 if mode == "timeout" else 15)
    target = hashlib.sha256(_REQUEST["target_repo"].encode()).hexdigest()
    assert submit.worker(target) == 0
    value = submit.show("request-1")
    run = value["run"]
    assert run["status"] == "failed"
    assert run["failure"] == reason
    assert run["report_synced"]
    assert not session_registry.ProcessIdentity(**run["executor"]).exists()
    folder = pathlib.Path(value["state_file"]).parents[1] / "runs" / run["run_id"]
    assert "executor stdout" in (folder / "stdout.log").read_text(encoding="utf-8")
    assert "executor stderr" in (folder / "stderr.log").read_text(encoding="utf-8")


def test_same_target_batches_requests_and_retries_only_unstarted_lock_conflict(notes, executor):
    executor("concurrent-once")
    _pending("request-1")
    _pending("request-2")
    target = hashlib.sha256(_REQUEST["target_repo"].encode()).hexdigest()
    assert submit.worker(target) == 0
    one, two = submit.show("request-1"), submit.show("request-2")
    assert one["run"]["run_id"] == two["run"]["run_id"]
    assert one["run"]["status"] == "completed"
    assert {item["request_id"] for item in one["run"]["result"]["results"]} == {"request-1", "request-2"}
    folder = state_paths.state_dir() / "wi-submit/runs" / one["run"]["run_id"]
    assert (folder / "attempts").read_text() == "2"
    assert len(list(layout.iter_entry_paths(notes))) == 1


def test_disappeared_supervisor_is_reobserved_without_resubmission(notes, monkeypatch, capsys):
    _defer(monkeypatch)
    with pytest.raises(OSError):
        submit.accept(submit.Request.model_validate(_REQUEST))
    value = submit.show("request-1")
    path = state_paths.state_dir() / "wi-submit/runs" / value["run"]["run_id"] / "run.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    run.update(status="running", owner={"pid": 99999999, "create_time": 1.0})
    path.write_text(json.dumps(run), encoding="utf-8")

    def recover(target, **_kwargs):
        submit.worker(target)

    monkeypatch.setattr(submit, "_launch_worker", recover)
    code, output, error = _call(["--show", "request-1"], capsys)
    assert code == 0, error
    observed = json.loads(output)
    assert observed["run"]["run_id"] == run["run_id"]
    assert observed["run"]["status"] == "failed"
    assert "監督消失" in observed["run"]["failure"]
    assert observed["run"]["report_synced"]
    assert len(list(layout.iter_entry_paths(notes))) == 1


@pytest.mark.usefixtures("notes")
def test_retention_keeps_incomplete_unreported_and_removes_only_old_completed(monkeypatch, executor):
    executor("normal")
    _pending("request-1")
    target = hashlib.sha256(_REQUEST["target_repo"].encode()).hexdigest()
    assert submit.worker(target) == 0
    value = submit.show("request-1")
    root = state_paths.state_dir() / "wi-submit"
    path = root / "runs" / value["run"]["run_id"] / "run.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    run["updated_at"] = time.time() - run_skill.LOG_RETENTION_SECONDS - 1
    _pending("pending-1")

    def no_launch(*_args, **_kwargs):
        return None

    monkeypatch.setattr(submit, "_launch_worker", no_launch)
    for status, reported in (("failed", True), ("completed", False)):
        run.update(status=status, report_synced=reported)
        path.write_text(json.dumps(run), encoding="utf-8")
        submit.reobserve()
        assert (root / "requests/request-1.json").exists()
        assert path.exists()
    run.update(status="completed", report_synced=True)
    path.write_text(json.dumps(run), encoding="utf-8")
    submit.reobserve()
    assert not (root / "requests/request-1.json").exists()
    assert not path.parent.exists()
    assert (root / "requests/pending-1.json").exists()


@pytest.mark.usefixtures("notes")
def test_popen_success_without_handoff_is_rejected_and_child_reaped(monkeypatch):
    children = []

    def launch(_target, **_kwargs):
        # acceptへ所有権を渡し、所有移管失敗時の停止・回収を検証する。
        child = subprocess.Popen(  # noqa: SIM115  # pylint: disable=consider-using-with
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        children.append(child)
        return child

    monkeypatch.setattr(submit, "_launch_worker", launch)
    monkeypatch.setattr(submit, "HANDOFF_SECONDS", 0)
    try:
        with pytest.raises(TimeoutError, match="所有移管"):
            submit.accept(submit.Request.model_validate(_REQUEST))
        assert children[0].poll() is not None
        value = submit.show("request-1")
        assert value["ticket"] is None
        assert value["run"]["status"] == "failed"
    finally:
        for child in children:
            if child.poll() is None:
                process_tree.stop_process_tree(child)
            child.wait(timeout=10)


@pytest.mark.usefixtures("notes")
def test_stable_cwd_keeps_run_skill_exclusion_for_same_repository(tmp_path, monkeypatch):
    product = tmp_path / "product"
    git_repository.init_repository(product)
    git_repository.run_git(product, "remote", "add", "origin", _REQUEST["target_repo"])
    stable = tmp_path / "stable"
    stable.mkdir()
    args = argparse.Namespace(
        skill=submit.SKILL,
        args=None,
        target_repo=_REQUEST["target_repo"],
        work_dir=str(stable),
        timeout=1,
        lock_timeout=0,
        resume=None,
    )
    monkeypatch.setattr(orchestrator, "resolve_specs", lambda **_kwargs: [("codex", "fake", "low")])
    # 実際の既存ロックを保持した利用側を用意し、公開dispatchの排他結果を確認する。
    with filelock.FileLock(str(run_skill._lock_path(product, submit.SKILL))):  # pylint: disable=protected-access
        assert run_skill.dispatch(args) == run_skill.CONCURRENT_EXIT_CODE
    assert getattr(args, "run_skill_concurrent", False)


@pytest.mark.usefixtures("notes")
def test_run_skill_log_sweep_keeps_submission_until_completed_retention(tmp_path, monkeypatch):
    _defer(monkeypatch)
    with pytest.raises(OSError):
        submit.accept(submit.Request.model_validate(_REQUEST))
    run_id = submit.show("request-1")["run"]["run_id"]
    run_path = state_paths.state_dir() / "wi-submit/runs" / run_id / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    logs = state_paths.state_dir() / "run-skill"
    logs.mkdir()
    old = logs / "protected.log"
    old.write_text("モデル起動前失敗の診断", encoding="utf-8")
    expired = time.time() - run_skill.LOG_RETENTION_SECONDS - 1
    os.utime(old, (expired, expired))
    run["run_skill_logs"] = [str(old)]
    run_path.write_text(json.dumps(run), encoding="utf-8")
    stable = tmp_path / "stable"
    stable.mkdir()
    args = argparse.Namespace(
        skill=submit.SKILL,
        args=None,
        target_repo=_REQUEST["target_repo"],
        work_dir=str(stable),
        timeout=1,
        resume=None,
    )
    monkeypatch.setattr(orchestrator, "resolve_specs", lambda **_kwargs: [("codex", "fake", "low")])
    with filelock.FileLock(
        str(
            run_skill._lock_path(  # pylint: disable=protected-access
                stable,
                submit.SKILL,
                target_repo=_REQUEST["target_repo"],
            )
        )
    ):
        assert run_skill.dispatch(args) == run_skill.CONCURRENT_EXIT_CODE
        assert old.exists()
        run.update(status="completed", report_synced=True, updated_at=expired)
        run_path.write_text(json.dumps(run), encoding="utf-8")
        assert run_skill.dispatch(args) == run_skill.CONCURRENT_EXIT_CODE
    assert not old.exists()


@pytest.mark.parametrize(("event", "module"), [("SessionStart", "rules_context"), ("SessionEnd", "session_end_cleanup")])
def test_existing_hook_events_reobserve_submissions(monkeypatch, event, module):
    calls = []

    def observe(**_kwargs):
        calls.append(event)

    monkeypatch.setattr(submit, "reobserve", observe)
    payload = json.dumps({"hook_event_name": event, "session_id": "test-submission-hook", "source": "startup"}).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    assert hook.main([module]) == 0
    assert calls == [event]


@pytest.mark.usefixtures("notes")
def test_public_resume_of_unstarted_request_keeps_execution_and_accepts_handoff(monkeypatch, capsys, executor):
    _defer(monkeypatch)
    with pytest.raises(OSError):
        submit.accept(submit.Request.model_validate(_REQUEST))
    original = submit.show("request-1")["run"]["run_id"]
    executor("normal")

    class Finished:
        def poll(self):
            return 0

    def launch(target, **_kwargs):
        submit.worker(target)
        return Finished()

    monkeypatch.setattr(submit, "_launch_worker", launch)
    code, output, error = _call(["--resume", "request-1"], capsys)
    assert code == 0, error
    result = json.loads(output)
    assert result["run"]["run_id"] == original
    assert result["run"]["status"] == "completed"
    assert result["ticket_verified"]


def test_public_show_recovers_saved_wi_before_finish_and_same_report(notes, monkeypatch, capsys):
    _defer(monkeypatch)
    with pytest.raises(OSError):
        submit.accept(submit.Request.model_validate(_REQUEST))
    run_id = submit.show("request-1")["run"]["run_id"]
    filename = add.add_entries(
        notes,
        messages=["---\nwi_submit_bindings: " + json.dumps({run_id: ["request-1"]}) + "\n---\n保存済みの処置"],
        target_repo=_REQUEST["target_repo"],
        source="agent",
        now=datetime.datetime.now(datetime.UTC),
        entry_type="uwi",
        question_type="yes-no",
    )[0]
    code, output, error = _call(["--show", "request-1"], capsys)
    assert code == 0, error
    value = json.loads(output)
    assert value["saved_wi_bindings"] == [{"filename": filename, "request_ids": ["request-1"]}]
    assert value["run"]["result"] is None
    assert "起動機能" in value["reobserve_error"]
    code, _output, error = _call(["--resume", "request-1"], capsys)
    assert code == 1
    assert "保存済み処置" in error


def test_incomplete_submission_waits_for_confirmation_answer(notes, monkeypatch, capsys):
    _defer(monkeypatch)
    with pytest.raises(OSError):
        submit.accept(submit.Request.model_validate(_REQUEST))
    run_id = submit.show("request-1")["run"]["run_id"]
    filename = add.add_entries(
        notes,
        messages=["---\nwi_submit_bindings: " + json.dumps({run_id: ["request-1"]}) + "\n---\n確認が必要"],
        target_repo=_REQUEST["target_repo"],
        source="agent",
        now=datetime.datetime.now(datetime.UTC),
        entry_type="uwi",
        question_type="yes-no",
    )[0]
    monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", run_id)
    submit.finish(
        submit.Result(
            run_id=run_id,
            results=[
                submit.Disposition(
                    request_id="request-1",
                    status="incomplete",
                    files=[filename],
                    reason="確認UWI回答待ち",
                )
            ],
        )
    )
    code, _output, error = _call(["--resume", "request-1"], capsys)
    assert code == 1
    assert "未回答" in error
    assert submit.show("request-1")["run"]["result"]["results"][0]["files"] == [filename]
