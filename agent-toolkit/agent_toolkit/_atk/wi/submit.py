"""自己完結したWI要求を永続受付し、親から独立した監督へ渡す。"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import uuid
from typing import Annotated, Any, Literal

import filelock
import pydantic

from agent_toolkit._atk import orchestrator, outcome, run_skill, run_skill_state
from agent_toolkit._atk.wi import add, frontmatter, layout, sync, uwi_scan
from agent_toolkit._common import (
    plugin_environment,
    plugin_root_leases,
    private_notes,
    process_tree,
    recorded_process,
    session_registry,
    session_review,
    state_paths,
    windows_job,
)
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as git_command
from agent_toolkit._git import remote

_LOG = logging.getLogger(__name__)
SKILL = "agent-toolkit:wi-standards"
_MODULE = "agent_toolkit._atk.wi.submit"
_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_RUN_ID = r"^[0-9a-f]{32}$"
Text = Annotated[str, pydantic.StringConstraints(min_length=1, pattern=r"\S")]
Identifier = Annotated[str, pydantic.Field(pattern=_ID)]
RunId = Annotated[str, pydantic.Field(pattern=_RUN_ID)]
HANDOFF_SECONDS = 15.0
WAIT_SECONDS = 3600.0
TIMEOUT_SECONDS = run_skill.DEFAULT_TIMEOUT_SECONDS
REPORT_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.1


class _Input(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", strict=True, frozen=True)


class Origin(_Input):
    """投入元が確定した由来と逐語原文。"""

    kind: Literal["human", "agent"]
    text: Text


class Request(_Input):
    """親のファイルへ依存しない永続受付入力。"""

    request_id: Identifier
    target_repo: Text
    source: Text
    origin: Origin
    requirements: Annotated[list[Text], pydantic.Field(min_length=1)]
    observations: list[Text]
    evidence: Annotated[list[Text], pydantic.Field(min_length=1)]
    update_targets: Annotated[list[Text], pydantic.Field(min_length=1)]

    @pydantic.field_validator("target_repo")
    @classmethod
    def _canonical_repo(cls, value: str) -> str:
        canonical = remote.normalize_remote_url(value)
        if value != canonical:
            raise ValueError(f"target_repoは正規化URLで指定する: {canonical}")
        return canonical


class Disposition(_Input):
    """要求ごとの処置。未完了のfilesには確認UWIを指定できる。"""

    request_id: Identifier
    status: Literal["registered", "reused", "unnecessary", "incomplete"]
    files: list[Text]
    reason: Text


class Result(_Input):
    """投入セッションから受け取る実行と受付集合の対応。"""

    run_id: RunId
    results: Annotated[list[Disposition], pydantic.Field(min_length=1)]


class Owner(_Input):
    """監督のPID再利用を区別する世代。"""

    pid: Annotated[int, pydantic.Field(gt=0)]
    create_time: Annotated[float, pydantic.Field(gt=0)]


class Ticket(_Input):
    """製品側の待機集合から独立受付を外すための所有移管票。"""

    request_id: Identifier
    target_repo: Text
    accepted_at: Annotated[float, pydantic.Field(gt=0)]
    owner: Owner


def validate_ticket(value: object) -> Ticket:
    """同じホストの永続入力と所有移管を照会し、成立済み受付だけを受理する。"""
    ticket = Ticket.model_validate(value)
    receipt = _read(_receipt_path(ticket.request_id))
    request = Request.model_validate(receipt["request"])
    if (
        ticket.target_repo != request.target_repo
        or ticket.accepted_at != receipt["accepted_at"]
        or ticket.owner.model_dump() != session_registry.ProcessIdentity(**receipt["owner"]).payload(legacy=True)
    ):
        raise ValueError("受付票が保存済みの入力と所有移管記録に一致しない")
    return ticket


def _root() -> pathlib.Path:
    root = state_paths.state_dir() / "wi-submit"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _guard() -> filelock.FileLock:
    return filelock.FileLock(str(_root() / "ledger.lock"), timeout=30)


def _write(path: pathlib.Path, data: dict[str, Any]) -> None:
    saved = dict(data)
    # 旧版はowner/executorをProcessIdentity(**value)で読むため、追加情報を同じobjectへ入れない。
    for name in ("owner", "executor"):
        if isinstance(saved.get(name), dict):
            identity = session_registry.ProcessIdentity(**saved[name])
            saved[name] = identity.payload(legacy=True)
            if identity.start_id is not None:
                saved[f"{name}_identity"] = identity.payload()
            else:
                saved.pop(f"{name}_identity", None)
    atomic_write(path, json.dumps(saved, ensure_ascii=False, sort_keys=True) + "\n", fsync=True)


def _read(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON objectが必要: {path}")
    for name in ("owner", "executor"):
        if isinstance(data.get(name), dict):
            data[name] = session_registry.ProcessIdentity.from_payload(
                data[name], stable=data.get(f"{name}_identity")
            ).payload()
    return data


def _receipt_path(identifier: str) -> pathlib.Path:
    if not isinstance(identifier, str) or not re.fullmatch(_ID, identifier):
        raise ValueError("request_idは英数字で始まる英数字・ハイフン・アンダースコア128文字以内で指定する")
    return _root() / "requests" / f"{identifier}.json"


def _run_path(run_id: str) -> pathlib.Path:
    if not re.fullmatch(_RUN_ID, run_id):
        raise ValueError("run_idは32文字の小文字16進数で指定する")
    return _root() / "runs" / run_id / "run.json"


def _target(request: dict[str, Any]) -> str:
    return hashlib.sha256(request["target_repo"].encode()).hexdigest()


def _identity_live(value: dict[str, Any] | None) -> bool:
    return value is not None and session_registry.ProcessIdentity(**value).exists()


def _environment() -> dict[str, str]:
    env = orchestrator.child_env()
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_PREFIX",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_GRAFT_FILE",
        "GIT_SHALLOW_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_CONFIG",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_REPLACE_REF_BASE",
    ):
        env.pop(key, None)
    for key in tuple(env):
        if key in {
            "AGENT_TOOLKIT_RESTART_SPEC",
            "AGENT_TOOLKIT_HANDOFF_PATH",
            session_review.BATCH_ENV,
            session_review.LAUNCH_ENV,
        } or key.startswith(
            (
                "AGENT_TOOLKIT_PROCESS_LOOP_",
                "AGENT_TOOLKIT_RUN_SKILL_",
                "AGENT_TOOLKIT_LANE_",
            )
        ):
            del env[key]
    return env


def _launch_worker(target: str) -> subprocess.Popen[bytes]:
    cwd = _root() / "work" / target
    cwd.mkdir(parents=True, exist_ok=True)
    env = _environment()
    source = pathlib.Path(__file__).resolve().parents[3]
    executable = plugin_environment.prepared_python(source, env)
    if executable is None:
        raise RuntimeError("共有依存環境が未準備。公開launcherで準備してから独立投入を再実行する")
    env["PYTHONPATH"] = str(source)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = windows_job.HIDDEN_DETACHED_CREATIONFLAGS
    with (cwd / "supervisor.stdout.log").open("ab") as stdout, (cwd / "supervisor.stderr.log").open("ab") as stderr:
        # 時間上限なし: 投入作業を監督する常駐の起動であり、作業の上限は監督自身が持つ。
        return subprocess.Popen(  # noqa: SIM115 -- 所有権は永続受付と独立監督へ移す
            [str(executable), "-m", "agent_toolkit._agents_server.plugin_root", "--wi-submit-worker", target],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=sys.platform != "win32",
            creationflags=creationflags,
        )


def accept_many(requests: list[Request]) -> list[dict[str, Any]]:
    """確定済みの同一対象の要求をまとめて保存し、全件の所有移管を確認する。"""
    if not requests:
        raise ValueError("同時受付には1件以上の要求が必要です")
    if len({request.request_id for request in requests}) != len(requests):
        raise ValueError("同時受付のrequest_idは重複できません")
    if len({request.target_repo for request in requests}) != 1:
        raise ValueError("同時受付のtarget_repoは同じ値にしてください")
    paths = [_receipt_path(request.request_id) for request in requests]
    target = _target(requests[0].model_dump())
    receipt: dict[str, Any]
    receipts: list[dict[str, Any]] = []
    new_receipts: list[tuple[pathlib.Path, dict[str, Any]]] = []
    with _guard():
        for request, path in zip(requests, paths, strict=True):
            if path.exists():
                receipt = _read(path)
                if receipt["request"] != request.model_dump():
                    raise ValueError("同じrequest_idに異なる入力は指定できない")
                if receipt.get("accepted_at") is None and receipt.get("run_id") is not None:
                    raise RuntimeError("保存済み受付の所有移管は成立していない。同じrequest_idの--showで失敗記録を確認する")
            else:
                receipt = {"request": request.model_dump(), "accepted_at": None, "owner": None, "run_id": None}
                new_receipts.append((path, receipt))
            receipts.append(receipt)
        # 監督が途中の集合を取得しないよう、全件を確認した後に同じロック内で保存する。
        for path, receipt in new_receipts:
            _write(path, receipt)
        if all(receipt.get("accepted_at") is not None for receipt in receipts):
            return [show(request.request_id) for request in requests]
        owner_path = _root() / "work" / target / "owner.json"
        owner = _read(owner_path).get("owner") if owner_path.exists() else None
        if _identity_live(owner):
            for path, receipt in zip(paths, receipts, strict=True):
                if receipt.get("accepted_at") is None:
                    receipt["accepted_at"], receipt["owner"] = time.time(), owner
                    _write(path, receipt)
            return [show(request.request_id) for request in requests]
    child = None
    try:
        child = _launch_worker(target)
        deadline = time.monotonic() + HANDOFF_SECONDS
        while time.monotonic() < deadline:
            if all(_read(path).get("accepted_at") is not None for path in paths):
                # 子が既に完了した場合も、保存した所有移管の事実は維持する。
                return [show(request.request_id) for request in requests]
            code = child.poll()
            if code is not None:
                if code != 0:
                    raise RuntimeError(f"監督が所有移管前に終了した: {code}")
                child = _launch_worker(target)
            time.sleep(POLL_SECONDS)
        raise TimeoutError("監督への所有移管を時間内に確認できない")
    except (OSError, RuntimeError, TimeoutError) as error:
        with _guard():
            unaccepted = [(path, receipt) for path in paths if (receipt := _read(path)).get("accepted_at") is None]
            if unaccepted:
                run_id = uuid.uuid4().hex
                _write(
                    _run_path(run_id),
                    {
                        "run_id": run_id,
                        "request_ids": [receipt["request"]["request_id"] for _, receipt in unaccepted],
                        "requests": [receipt["request"] for _, receipt in unaccepted],
                        "target_repo": requests[0].target_repo,
                        "work_dir": str(_root() / "work" / target),
                        "owner": None,
                        "executor": None,
                        "status": "failed",
                        "result": None,
                        "report_synced": False,
                        "failure": f"監督起動失敗: {error}",
                        "updated_at": time.time(),
                    },
                )
                for path, receipt in unaccepted:
                    receipt["handoff_error"], receipt["run_id"] = str(error), run_id
                    _write(path, receipt)
        # 起動済みならその世代を止め、後のhookで同じ受付を再観測する。
        if child is not None and child.poll() is None:
            process_tree.log_residual(process_tree.stop_process_tree(child), context="wi submit handoff")
            child.wait(timeout=10)
        raise


def accept(request: Request) -> dict[str, Any]:
    """単一入力の受付票・照会結果を返す。"""
    return accept_many([request])[0]


def show(identifier: str) -> dict[str, Any]:
    """受付票と、同じ実行の結果・ログ・未報告の所在を返す。"""
    receipt = _read(_receipt_path(identifier))
    run = _read(_run_path(receipt["run_id"])) if receipt.get("run_id") else None
    ticket = None
    if receipt.get("accepted_at") is not None:
        ticket = Ticket(
            request_id=identifier,
            target_repo=receipt["request"]["target_repo"],
            accepted_at=receipt["accepted_at"],
            owner=Owner.model_validate(session_registry.ProcessIdentity(**receipt["owner"]).payload(legacy=True)),
        ).model_dump()
        validate_ticket(ticket)
    bindings, reports = [], []
    if run is not None:
        notes = private_notes.default_private_notes()
        with sync.repo_lock(notes, timeout=30):
            for path in layout.iter_entry_paths(notes):
                parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
                if not parsed:
                    continue
                data = parsed[0]
                association = data.get("wi_submit_bindings")
                if (
                    isinstance(association, dict)
                    and run["run_id"] in association
                    and data.get("target_repo") == run["target_repo"]
                ):
                    bindings.append({"filename": path.name, "request_ids": association[run["run_id"]]})
                if (
                    data.get("run_skill_run_id") == run["run_id"]
                    and data.get("scope") == SKILL
                    and data.get("run_skill_report") == "true"
                    and data.get("source") == "run-skill"
                ):
                    reports.append(path.name)
    return {
        "ticket": ticket,
        "ticket_verified": ticket is not None,
        "receipt": receipt,
        "run": run,
        "saved_wi_bindings": bindings,
        "report_files": reports,
        "state_file": str(_receipt_path(identifier)),
    }


def resume(identifier: str) -> dict[str, Any]:
    """同じ受付・実行の未完了工程を、実行済みと未実行を区別して再移管する。"""
    receipt = _read(_receipt_path(identifier))
    if receipt["run_id"] is None:
        return accept(Request.model_validate(receipt["request"]))
    path = _run_path(receipt["run_id"])
    with _guard():
        run = _read(path)
        if run["status"] == "completed" and run["report_synced"] or _identity_live(run.get("owner")):
            return show(identifier)
        try:
            record = run_skill_state.load(run["run_id"])
        except FileNotFoundError:
            record = None
        if record is not None and (record.session_id is None or record.remaining <= 0):
            raise ValueError("元会話または残時間が不足する。報告UWIへ判断を返し、保存済みWIと未完了要求を引き継ぐ")
        if run.get("result"):
            notes = private_notes.default_private_notes()
            for item in Result.model_validate(run["result"]).results:
                if item.status == "incomplete" and (
                    not item.files
                    or any(
                        not uwi_scan.is_uwi_answered(layout.entry_path(notes, name).read_text(encoding="utf-8"))
                        for name in item.files
                    )
                ):
                    raise ValueError("未完了要求の確認UWIが未回答である。回答と解除条件を満たしてから再開する")
        if record is None and (run.get("result") is not None or show(identifier)["saved_wi_bindings"]):
            raise ValueError("保存済み処置があるが元会話の記録が無い。再投入せず確認UWIへ引き継ぐ")
        run.update(resume_requested=True, resume_mode="conversation" if record else "fresh", updated_at=time.time())
        _write(path, run)
        owner_path = _root() / "work" / _target(receipt["request"]) / "owner.json"
        owner = _read(owner_path).get("owner") if owner_path.exists() else None
        if _identity_live(owner):
            run["resume_owner"] = owner
            _write(path, run)
            return show(identifier)
    try:
        child = _launch_worker(_target(receipt["request"]))
    except (OSError, RuntimeError) as error:
        with _guard():
            current = _read(path)
            current["resume_error"] = str(error)
            _write(path, current)
        raise
    deadline = time.monotonic() + HANDOFF_SECONDS
    while time.monotonic() < deadline:
        current = _read(path)
        if not current.get("resume_requested") and (
            _identity_live(current.get("owner")) or current["status"] in {"completed", "failed"}
        ):
            return show(identifier)
        if child.poll() not in (None, 0):
            raise RuntimeError("再開の監督が所有移管前に終了した。保存記録と報告UWIを確認する")
        time.sleep(POLL_SECONDS)
    if child.poll() is None:
        process_tree.log_residual(process_tree.stop_process_tree(child), context="wi submit resume handoff")
        child.wait(timeout=10)
    raise TimeoutError("再開の所有移管を確認できない。受付を保持したので--showで確認する")


def finish(result: Result) -> None:
    """起動元run ID・受付集合と実在WIを照会し、処置だけを保存する。"""
    if os.environ.get("AGENT_TOOLKIT_RUN_SKILL_ID") != result.run_id:
        raise ValueError("finishは対応するrun-skill実行から行う")
    path = _run_path(result.run_id)
    with _guard():
        run = _read(path)
        actual = [item.request_id for item in result.results]
        if len(actual) != len(set(actual)) or set(actual) != set(run["request_ids"]):
            raise ValueError("finishの受付集合が実行に対応していない")
        if run.get("result") is not None:
            old = {item["request_id"]: item for item in run["result"]["results"]}
            if any(
                old[item.request_id]["status"] != "incomplete" and old[item.request_id] != item.model_dump()
                for item in result.results
            ):
                raise ValueError("同じ実行の完了済み処置を異なる内容で上書きできない")
        _verify_files(result, run["target_repo"])
        run["result"] = result.model_dump()
        run["updated_at"] = time.time()
        _write(path, run)


def _verify_files(result: Result, target_repo: str) -> None:
    notes = private_notes.default_private_notes()
    with sync.repo_lock(notes, timeout=30):
        for item in result.results:
            if item.status in {"registered", "reused"} and not item.files:
                raise ValueError(f"登録・再利用には実在するWIが必要: {item.request_id}")
            for filename in item.files:
                if pathlib.Path(filename).name != filename:
                    raise ValueError("filesはWIのファイル名だけを指定する")
                entry = next((path for path in layout.iter_entry_paths(notes) if path.name == filename), None)
                parsed = frontmatter.parse_frontmatter(entry.read_text(encoding="utf-8")) if entry else None
                if parsed is None or parsed[0].get("target_repo") != target_repo or parsed[0].get("type") not in {"awi", "uwi"}:
                    raise ValueError(f"対応する正式WIを確認できない: {filename}")
                assert entry is not None
                layout.entry_state_of(entry)
                associations = parsed[0].get("wi_submit_bindings")
                identifiers = associations.get(result.run_id) if isinstance(associations, dict) else None
                if not isinstance(identifiers, list) or item.request_id not in identifiers:
                    raise ValueError(f"WIと実行・受付の対応が保存されていない: {filename}")
                if item.status == "incomplete" and parsed[0].get("type") != "uwi":
                    raise ValueError("未完了のfilesには確認UWIを指定する")


def _report(run: dict[str, Any], path: pathlib.Path) -> None:
    with git_command.timeout_scope(REPORT_TIMEOUT_SECONDS):
        _sync_report(run, path)


def _sync_report(run: dict[str, Any], path: pathlib.Path) -> None:
    """保存済み報告を先に照会し、同期不能時は同じ報告の同期だけを再開する。"""
    notes = private_notes.default_private_notes()
    previous_run_id = os.environ.get("AGENT_TOOLKIT_RUN_SKILL_ID")
    # 自動取得される予約メタデータを、専用監督が所有する同じ実行へ結び付ける。
    os.environ["AGENT_TOOLKIT_RUN_SKILL_ID"] = run["run_id"]
    try:
        if not add.has_synced_run_report(notes, run_id=run["run_id"], skill=SKILL):
            with sync.repo_lock(notes, timeout=30):
                reports = []
                for entry in layout.iter_entry_paths(notes):
                    parsed = frontmatter.parse_frontmatter(entry.read_text(encoding="utf-8"))
                    if parsed and (
                        parsed[0].get("run_skill_run_id") == run["run_id"]
                        and parsed[0].get("run_skill_report") == "true"
                        and parsed[0].get("source") == "run-skill"
                        and parsed[0].get("scope") == SKILL
                    ):
                        reports.append(entry)
                if reports:
                    sync.commit_and_push(notes, "chore: 独立WI投入の報告を同期", [str(p.relative_to(notes)) for p in reports])
            if not reports:
                body = (
                    "---\nrun_skill_report: true\n---\n\n"
                    "未完了の処置と再開先を確認したか？\n"
                    f"独立WI投入の処置が未完了である。\n実行: {run['run_id']}\n"
                    f"受付: {', '.join(run['request_ids'])}\n理由: {run.get('failure')}\n"
                    f"記録: {path}\n同じ実行と保存済みWIを照会し、"
                    f"会話がある場合は`atk run-skill --resume {run['run_id']}`で残工程を再開する。\n"
                )
                add.add_entries(
                    notes,
                    messages=[body],
                    target_repo=run["target_repo"],
                    source="run-skill",
                    now=datetime.datetime.now(datetime.UTC),
                    entry_type="uwi",
                    scope=SKILL,
                    question_type="yes-no",
                    storage=sync.Storage(lock_timeout=30),
                )
        run["report_synced"] = add.has_synced_run_report(notes, run_id=run["run_id"], skill=SKILL)
        run["report_error"] = None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, filelock.Timeout) as error:
        run["report_synced"] = False
        run["report_error"] = str(error)
    finally:
        if previous_run_id is None:
            os.environ.pop("AGENT_TOOLKIT_RUN_SKILL_ID", None)
        else:
            os.environ["AGENT_TOOLKIT_RUN_SKILL_ID"] = previous_run_id
    run["updated_at"] = time.time()
    with _guard():
        # 報告同期の間に同じ会話がfinishを保存しても、その要求ごとの結果を保持する。
        run["result"] = _read(path)["result"]
        _write(path, run)


def _finalize(path: pathlib.Path, *, failure: str | None) -> None:
    run = _read(path)
    run["failure"] = failure
    result = Result.model_validate(run["result"]) if run.get("result") else None
    try:
        if result is not None:
            _verify_files(result, run["target_repo"])
        else:
            run["failure"] = failure or "finish欠落"
    except (OSError, ValueError) as error:
        run["failure"] = str(error)
    if result is not None and any(item.status == "incomplete" for item in result.results):
        run["failure"] = run["failure"] or "未完了の要求がある"
    run["status"] = "failed" if run["failure"] else "completed"
    _report(run, path)
    if not run["report_synced"]:
        run["status"] = "failed"
        run["failure"] = run["failure"] or "報告未同期"
        _write(path, run)
    if run["status"] == "failed":
        try:
            record = run_skill_state.load(run["run_id"])
        except FileNotFoundError:
            return
        remaining = min(record.remaining, max(0, record.deadline - time.time())) if record.deadline else record.remaining
        run_skill_state.RecordWriter(record).finish(remaining, completed=False)


def _execute(run_id: str) -> int:
    _register_runtime_use()
    path = _run_path(run_id)
    run = _read(path)
    root = pathlib.Path(__file__).resolve().parents[3]
    args = argparse.Namespace(
        skill=SKILL,
        args=(
            f"独立WI受付: {path}。投入契約: {root / 'share/wi-submission.md'}。"
            f"スキル本文: {root / 'skills/wi-standards/SKILL.md'}。投入セッション契約を適用する。"
        ),
        target_repo=run["target_repo"],
        work_dir=run["work_dir"],
        timeout=TIMEOUT_SECONDS,
        run_id=run_id,
        resume=None,
        lock_timeout=WAIT_SECONDS,
        log_path=None,
    )
    if run.get("resume_mode") == "conversation":
        args = argparse.Namespace(
            skill=None,
            args=None,
            target_repo=None,
            work_dir=None,
            timeout=None,
            resume=run_id,
            lock_timeout=WAIT_SECONDS,
            log_path=None,
        )
    try:
        code = run_skill.dispatch(args)
    except (OSError, ValueError, RuntimeError) as error:
        _write(
            path.parent / "exit.json",
            {
                "code": 1,
                "concurrent": False,
                "error": str(error),
                "log_path": str(args.log_path) if getattr(args, "log_path", None) is not None else None,
            },
        )
        raise
    _write(
        path.parent / "exit.json",
        {
            "code": code,
            "concurrent": getattr(args, "run_skill_concurrent", False),
            "log_path": str(args.log_path) if getattr(args, "log_path", None) is not None else None,
        },
    )
    return code


def _supervise(path: pathlib.Path) -> None:
    run = _read(path)
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        env = _environment()
        root = pathlib.Path(__file__).resolve().parents[3]
        env["PYTHONPATH"] = str(root)
        exit_path = path.parent / "exit.json"
        exit_path.unlink(missing_ok=True)
        try:
            executable = plugin_environment.prepared_python(root, env)
            if executable is None:
                raise OSError("共有依存環境のPythonを解決できない")
            with (
                (path.parent / "stdout.log").open("ab") as stdout,
                (path.parent / "stderr.log").open("ab") as stderr,
                process_tree.process_scope(
                    [str(executable), "-m", _MODULE, "execute", run["run_id"]],
                    cwd=run["work_dir"],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                ) as child,
            ):
                identity = session_registry.ProcessIdentity.of(child.pid)
                run["executor"] = dataclasses.asdict(identity) if identity else None
                run["status"] = "running"
                _write(path, run)
                try:
                    code = child.wait(timeout=TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    failure = recorded_process.stop_recorded_executor(identity) if identity is not None else None
                    if failure is not None:
                        # 子孫が残る間は実行を確定しない。監督の消失を検出する処理が、
                        # 記録した同じ実行者へ停止を再試行してから確定する。
                        _LOG.error("時間上限の子を回収できない: %s。実行 %s の確定を保留する", failure, run["run_id"])
                        return
                    child.wait(timeout=10)
                    _finalize(path, failure="timeout")
                    return
        except OSError as error:
            _finalize(path, failure=f"モデル起動前失敗: {error}")
            return
        exited = _read(exit_path) if exit_path.exists() else {}
        if exited.get("log_path"):
            with _guard():
                saved = _read(path)
                saved["run_skill_logs"] = sorted(set(saved.get("run_skill_logs", [])) | {exited["log_path"]})
                _write(path, saved)
        if exited.get("concurrent"):
            if time.monotonic() >= deadline:
                _finalize(path, failure="排他待ち上限到達（未実行）")
                return
            run = _read(path)
            run["status"] = "waiting"
            _write(path, run)
            time.sleep(min(1.0, max(0, deadline - time.monotonic())))
            continue
        _finalize(path, failure=f"子の異常終了: {code}" if code != 0 else None)
        return


def _recover_runs(target: str, owner: session_registry.ProcessIdentity) -> None:
    for path in (_root() / "runs").glob("*/run.json"):
        run = _read(path)
        if run.get("resume_requested"):
            continue
        if hashlib.sha256(run["target_repo"].encode()).hexdigest() != target or (
            _identity_live(run.get("owner")) and not session_registry.ProcessIdentity(**run["owner"]).compatible_with(owner)
        ):
            continue
        if run["status"] in {"queued", "running", "waiting"}:
            executors = [session_registry.ProcessIdentity(**run["executor"])] if run.get("executor") else []
            try:
                record = run_skill_state.load(run["run_id"])
            except FileNotFoundError:
                record = None
            if record is not None and record.executor is not None:
                executors.append(record.executor)
            failures = [failure for identity in executors if (failure := recorded_process.stop_recorded_executor(identity))]
            if failures:
                _LOG.error(
                    "監督消失後の子を終了できない: %s。次の操作: atk wi submit --show %s で実行 %s の保存記録を照会し、"
                    "保存されたexecutorのPID・作成時刻と現在のプロセスの一致および終了を確認する。"
                    "終了未確認の間は要求を再投入しない",
                    failures,
                    run["request_ids"][0],
                    run["run_id"],
                )
                continue
            _finalize(path, failure="監督消失（同じ実行と保存済みWIの照会が必要）")
        elif not run["report_synced"]:
            _report(run, path)
            if run["report_synced"] and run.get("result") and run.get("failure") == "報告未同期":
                _finalize(path, failure=None)
        elif run["status"] == "failed" and run.get("result"):
            try:
                resumed = run_skill_state.load(run["run_id"])
            except FileNotFoundError:
                continue
            if resumed.status == "completed":
                _finalize(path, failure=None)


def _register_runtime_use() -> None:
    root = pathlib.Path(__file__).resolve().parents[3]
    if (root.parent / ".agent-toolkit-plugin-root.json").is_file():
        with plugin_root_leases.plugin_root_lock(root.parent):
            plugin_root_leases.register_plugin_root_use(root.parent)


def worker(target: str) -> int:
    """対象の受付をまとめて実行し、受付元が終了しても保存・報告まで所有する。"""
    if re.fullmatch(r"[0-9a-f]{64}", target) is None:
        raise ValueError("監督の対象識別子が不正")
    _register_runtime_use()
    work = _root() / "work" / target
    work.mkdir(parents=True, exist_ok=True)
    owner = session_registry.ProcessIdentity.of(os.getpid())
    if owner is None:
        raise RuntimeError("監督のPID世代を取得できない")
    try:
        with filelock.FileLock(str(work / "worker.lock"), timeout=0):
            with _guard():
                _write(work / "owner.json", {"owner": dataclasses.asdict(owner)})
            _recover_runs(target, owner)
            while True:
                with _guard():
                    resumed_path = next(
                        (
                            candidate
                            for candidate in (_root() / "runs").glob("*/run.json")
                            if (saved := _read(candidate)).get("resume_requested")
                            and hashlib.sha256(saved["target_repo"].encode()).hexdigest() == target
                        ),
                        None,
                    )
                    pending = []
                    for path in (_root() / "requests").glob("*.json"):
                        receipt = _read(path)
                        if _target(receipt["request"]) != target:
                            continue
                        if receipt["run_id"] is None:
                            if receipt["accepted_at"] is None:
                                receipt["owner"] = dataclasses.asdict(owner)
                                receipt["accepted_at"] = time.time()
                            pending.append((path, receipt))
                    if not pending and resumed_path is None:
                        _write(work / "owner.json", {"owner": None})
                        break
                    if resumed_path is not None:
                        path = resumed_path
                        run = _read(path)
                        run_id = run["run_id"]
                        run.update(resume_requested=False, status="queued", owner=dataclasses.asdict(owner), failure=None)
                        if run.get("resume_mode") == "fresh":
                            run["runtime_root"] = str(pathlib.Path(__file__).resolve().parents[3])
                        _write(path, run)
                        pending = [
                            (_receipt_path(identifier), _read(_receipt_path(identifier))) for identifier in run["request_ids"]
                        ]
                    else:
                        run_id = uuid.uuid4().hex
                        path = _run_path(run_id)
                        _write(
                            path,
                            {
                                "run_id": run_id,
                                "request_ids": [item[1]["request"]["request_id"] for item in pending],
                                "requests": [item[1]["request"] for item in pending],
                                "target_repo": pending[0][1]["request"]["target_repo"],
                                "work_dir": str(work),
                                "owner": dataclasses.asdict(owner),
                                "executor": None,
                                "runtime_root": str(pathlib.Path(__file__).resolve().parents[3]),
                                "status": "queued",
                                "result": None,
                                "report_synced": False,
                                "failure": None,
                                "updated_at": time.time(),
                            },
                        )
                    for receipt_path, receipt in pending:
                        if receipt["accepted_at"] is None:
                            receipt["accepted_at"], receipt["owner"] = time.time(), dataclasses.asdict(owner)
                        receipt["run_id"] = run_id
                        _write(receipt_path, receipt)
                _supervise(path)
            return 0
    except filelock.Timeout:
        return 0


def reobserve() -> None:
    """既存SessionStart/SessionEndから未完了・未報告と監督消失を再観測する。"""
    if os.environ.get("AGENT_TOOLKIT_RUN_SKILL_ID"):
        return
    root = state_paths.state_dir() / "wi-submit"
    if not root.exists():
        return
    targets = set()
    with _guard():
        for path in (root / "requests").glob("*.json"):
            receipt = _read(path)
            if receipt["run_id"] is None:
                targets.add(_target(receipt["request"]))
                continue
            run_path = _run_path(receipt["run_id"])
            run = _read(run_path)
            if run.get("resume_requested"):
                targets.add(_target(receipt["request"]))
                continue
            if run["status"] == "completed" and run["report_synced"]:
                if time.time() - run["updated_at"] > run_skill.LOG_RETENTION_SECONDS:
                    path.unlink()
                    # 同じrunの受付を全て回収した後にログも回収する。
                    if not any(_read(p).get("run_id") == run["run_id"] for p in (root / "requests").glob("*.json")):
                        shutil.rmtree(run_path.parent)
                    target = _target(receipt["request"])
                    work = root / "work" / target
                    owner_path = work / "owner.json"
                    owner = _read(owner_path).get("owner") if owner_path.exists() else None
                    if (
                        work.exists()
                        and not _identity_live(owner)
                        and not any(_target(_read(p)["request"]) == target for p in (root / "requests").glob("*.json"))
                    ):
                        shutil.rmtree(work)
                continue
            if not _identity_live(run.get("owner")):
                if run["status"] == "failed" and run["report_synced"]:
                    try:
                        resumed = run_skill_state.load(run["run_id"])
                    except FileNotFoundError:
                        continue
                    if resumed.status != "completed":
                        continue
                targets.add(_target(receipt["request"]))
    for target in targets:
        _launch_worker(target)


def dispatch(args: argparse.Namespace) -> int:
    """公開CLIの信頼境界でJSONと識別子を検証する。"""
    value: dict[str, Any] | list[dict[str, Any]]
    try:
        if args.finish:
            result = Result.model_validate_json(args.finish.read_text(encoding="utf-8"))
            finish(result)
            value = {"run_id": result.run_id, "finish_saved": True}
        elif args.show:
            observed_error = None
            try:
                reobserve()
            except (OSError, RuntimeError, ValueError, filelock.Timeout) as error:
                observed_error = str(error)
            value = show(args.show)
            if observed_error:
                value["reobserve_error"] = observed_error
        elif args.resume:
            value = resume(args.resume)
        else:
            request = pydantic.TypeAdapter(Request | list[Request]).validate_json(args.request_file.read_text(encoding="utf-8"))
            value = accept(request) if isinstance(request, Request) else accept_many(request)
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError, filelock.Timeout) as error:
        outcome.report_failure(str(error), next_action="入力JSONと`atk wi submit --show <request_id>`の保存記録を確認する")
        return 1


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in {"worker", "execute"}:
        sys.exit(2)
    sys.exit(worker(sys.argv[2]) if sys.argv[1] == "worker" else _execute(sys.argv[2]))
