"""run-skillの非同期振り返り向け前後処理。モデル起動はrun_skillへ委ねる。"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import hashlib
import io
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import filelock
import psutil

from agent_toolkit._atk import config, orchestrator, outcome, run_script
from agent_toolkit._atk.wi import add, frontmatter, layout, repo, sync, uwi_scan
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import (
    delegated_session,
    plugin_environment,
    private_notes,
    session_activity,
    session_registry,
    session_review,
)

_LOG = logging.getLogger(__name__)
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]


def hook_request(payload: dict[str, Any], *, host: str) -> str | None:
    """開始時に登録し、設定に従い既存CLIへ起動要求だけを渡す。"""
    if delegated_session.is_delegated(os.environ) or os.environ.get(session_review.BATCH_ENV) or payload.get("agent_id"):
        return None
    event = payload.get("hook_event_name")
    target_repo = None
    cwd = payload.get("cwd")
    if event in {"SessionStart", "UserPromptSubmit"} and isinstance(cwd, str):
        with contextlib.suppress(WebInputError):
            target_repo = repo.resolve_repo_id_or_raise(None, cwd=pathlib.Path(cwd))
    session_review.record(payload, host=host, target_repo=target_repo)
    if event not in {"SessionStart", "SessionEnd"}:
        return None
    notice = session_review.launch_notice() if event == "SessionStart" else None
    count = len(session_review.pending())
    if not count:
        return notice
    if config.resolve_mutable_setting("session_review_auto_start") == "false":
        if event == "SessionStart":
            waiting = (
                f"振り返り待ち{count}件（活動中・失敗抑止中を含む）。"
                "まとめて振り返るには /agent-toolkit:session-review-batch を実行する。"
            )
            return "\n".join(part for part in (notice, waiting) if part)
        return notice
    if session_activity.host_process() is None:
        # ホスト外で再生したhook入力は記録だけにとどめ、実在するホストの開始・終了から起動する。
        return notice
    env = plugin_environment.direct_environment(PLUGIN_ROOT, orchestrator.child_env(drop=(session_review.BATCH_ENV,)))
    # セッションのstdin/stdoutを保持せず、受付前の試行と受付後の実行記録で責任を分ける。
    flags: dict[str, Any]
    if sys.platform == "win32":
        flags = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        flags = {"start_new_session": True}
    launch_id = uuid.uuid4().hex
    env[session_review.LAUNCH_ENV] = launch_id
    launch_path = session_review.directory() / "launches" / f"{launch_id}.json"
    log_path = launch_path.with_suffix(".log")
    with session_review.locked():
        owner = session_registry.ProcessIdentity.of(os.getpid())
        value: dict[str, Any] = {
            "host": host,
            "event": event,
            "status": "starting",
            "log_path": str(log_path),
            "owner": {"pid": owner.pid, "create_time": owner.create_time} if owner else None,
        }
        session_review.write(launch_path, value)
        with log_path.open("ab") as log:
            # hook終了後も生存確認できるようPIDを保存し、親が存続する間はスレッドで回収する。
            try:
                process = subprocess.Popen(  # pylint: disable=consider-using-with
                    [
                        sys.executable,
                        str(PLUGIN_ROOT / "agent_toolkit" / "atk.py"),
                        "run-skill",
                        "--args",
                        "auto",
                        session_review.BATCH_SKILL,
                    ],
                    cwd=PLUGIN_ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    **flags,
                )
            except OSError as error:
                log.write(str(error).encode("utf-8"))
                value["exited"] = True
                session_review.write(launch_path, value)
                return notice
        identity = session_registry.ProcessIdentity.of(process.pid)
        value["process"] = {"pid": identity.pid, "create_time": identity.create_time} if identity else None
        value["exited"] = identity is None
        session_review.write(launch_path, value)
    threading.Thread(target=process.wait, daemon=True).start()
    return notice


def _reports() -> list[tuple[pathlib.Path, dict[str, Any], str]]:
    root = private_notes.default_private_notes()
    found = []
    for path in layout.iter_entry_paths(root):
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            parsed = frontmatter.parse_frontmatter(content)
            if parsed is not None:
                data, _body = parsed
                if (
                    data.get("type") == "uwi"
                    and data.get("source") == "run-skill"
                    and data.get("scope") == session_review.BATCH_SKILL
                ):
                    found.append((path, data, content))
    return found


def _blocked(target: dict[str, Any]) -> bool:
    key = target.get("failure_key")
    return bool(key) and any(
        data.get("session_review_failure_key") == key and not uwi_scan.is_uwi_answered(text) for _path, data, text in _reports()
    )


def _targets(*, manual: bool) -> list[dict[str, Any]]:
    selected = []
    for target in session_review.pending():
        if not manual and (_blocked(target) or target.get("failure_unreported")):
            continue
        activity = session_activity.review_activity({target["session_id"]})
        if not activity.has_activity:
            selected.append(target)
    return selected


def _report_target(targets: list[dict[str, Any]]) -> tuple[str, pathlib.Path]:
    counts = collections.Counter(target["target_repo"] for target in targets if target.get("target_repo"))
    chosen = (
        min(counts, key=lambda item: (-counts[item], item))
        if counts
        else json.loads((PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["repository"]
    )
    cwd = next(
        (
            pathlib.Path(target["cwd"])
            for target in targets
            if target.get("target_repo") == chosen and target.get("cwd") and pathlib.Path(target["cwd"]).is_dir()
        ),
        PLUGIN_ROOT,
    )
    return chosen, cwd


def _prepare(target: dict[str, Any]) -> dict[str, Any]:
    if session_activity.review_activity({target["session_id"]}).has_activity:
        raise ValueError(f"対象が活動を再開した: {target['session_id']}")
    arguments = (
        ["--transcript", target["transcript_path"]]
        if target["host"] == "claude"
        else ["--codex-thread-id", target["session_id"]]
    )
    if target.get("target_repo") and target.get("cwd") and pathlib.Path(target["cwd"]).is_dir():
        arguments.extend(["--target-repo", target["cwd"]])
    previous = session_review.previous_scope(target)
    if previous is not None:
        arguments.extend(["--after", str(previous)])
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = run_script.dispatch(argparse.Namespace(script_name="session-review-prepare", script_args=arguments))
    if code:
        raise ValueError(f"prepareが終了{code}: {output.getvalue()}")
    prepared = json.loads(output.getvalue())
    if prepared.get("unconfirmed_scope"):
        raise ValueError(f"prepareに未確認範囲がある: {target['session_id']}")
    # 選定後に再登録された世代を成功処理で消さない。
    prepared["generation"] = target["generation"]
    return prepared


def _failure(manifest: dict[str, Any], path: pathlib.Path, reason: str) -> None:
    signature: list[Any] = [reason.split(":", 1)[0]]
    if signature[0].startswith("run-skill-exit-"):
        # 終了コードだけから別の対象で同じ原因が生じたとは推定しない。
        signature.append(sorted(item["key"] for item in manifest["targets"]))
    else:
        normalized = re.sub(r"[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", "<run-id>", reason)
        normalized = re.sub(r"\d{4}-\d\d-\d\dT[0-9:.+-]+Z?", "<timestamp>", normalized)
        signature.append(normalized)
    failure_key = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
    reports = _reports()
    existing = next(
        (
            entry
            for entry in reports
            if entry[1].get("session_review_failure_key") == failure_key and not uwi_scan.is_uwi_answered(entry[2])
        ),
        None,
    )
    # 同一実行の成功報告が保存されていれば、その報告の同期・範囲確定を次回に継続する。
    own = next((entry for entry in reports if entry[1].get("run_skill_run_id") == manifest["run_id"]), None)
    manifest.update(status="failed", failure_key=failure_key, error=reason)
    session_review.write(path, manifest)
    saved = False
    found = existing or own
    if found is not None:
        report_path, _data, text = found
        try:
            if own is not None and existing is None:
                parsed = frontmatter.parse_frontmatter(text)
                assert parsed is not None
                data, body = parsed
                data["session_review_failure_key"] = failure_key
                updated = frontmatter.serialize_frontmatter(data, body + f"\n## 実行失敗\n\n{reason}\n実行記録: {path}\n")
                repo.append_entry(
                    private_notes.default_private_notes(),
                    directory=report_path.parent,
                    filename=report_path.name,
                    content=updated.encode(),
                    target_repo=None,
                    lock_timeout=30,
                    expected_content=text.encode(),
                    commit_message="chore: 振り返りの実行失敗を報告",
                )
            else:
                notes = private_notes.default_private_notes()
                with sync.repo_lock(notes, timeout=30):
                    sync.commit_and_push(notes, "chore: 振り返りの失敗報告を同期", [str(report_path.relative_to(notes))])
            saved = True
        except OSError, ValueError, RuntimeError, subprocess.SubprocessError:
            _LOG.exception("既存の失敗報告を同期できない: %s", report_path)
    else:
        message = (
            "---\nsession_review_failure_key: "
            + json.dumps(failure_key)
            + "\n---\n# 非同期振り返りの失敗\n\n"
            + reason
            + f"\n\n実行記録: {path}\n対象: "
            + ", ".join(item["session_id"] for item in manifest["targets"])
            + "\n"
        )
        saved_details: dict[str, dict[str, object | None]] = {}
        try:
            add.add_entries(
                private_notes.default_private_notes(),
                messages=[message],
                target_repo=manifest["target_repo"],
                source="run-skill",
                now=datetime.datetime.now(datetime.UTC),
                entry_type="uwi",
                scope=session_review.BATCH_SKILL,
                question_type="post-approval",
                saved_details=saved_details,
            )
            saved = True
        except OSError, ValueError, subprocess.SubprocessError:
            _LOG.exception("振り返りの失敗報告を保存できない。次回起動時に同じ報告から再開する: %s", path)
            saved = False
    manifest["report_synced"] = saved
    session_review.write(path, manifest)
    with session_review.locked():
        for target in manifest["targets"]:
            pending_path = session_review.directory() / "pending" / f"{target['key']}.json"
            if pending_path.exists():
                current = session_review.read(pending_path)
                current.update(failure_key=failure_key, failure_unreported=not bool(saved))
                session_review.write(pending_path, current)


def _live(manifest: dict[str, Any]) -> bool:
    for raw in manifest.get("processes", []):
        if session_registry.ProcessIdentity(**raw).exists():
            return True
    # 別会話の環境読取権限には依存せず、起動argvの固有manifestパスで対象の子を特定する。
    marker = f"batch manifest: {manifest['manifest_path']}"
    username = psutil.Process().username()
    for process in psutil.process_iter():
        try:
            if (
                process.username() == username
                and any(marker in part for part in process.cmdline())
                and process.status() != psutil.STATUS_ZOMBIE
            ):
                return True
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            continue
        except psutil.AccessDenied:
            continue
    return False


def record_executor(args: argparse.Namespace, pid: int) -> None:
    """起動した実プロセスを、親の中断後も排他の検収で使えるよう保存する。"""
    value = getattr(args, "batch_manifest", None)
    if value is None:
        return
    path = pathlib.Path(value)
    manifest = session_review.read(path)
    identity = session_registry.ProcessIdentity.of(pid)
    if identity is not None:
        manifest["processes"] = [{"pid": identity.pid, "create_time": identity.create_time}]
        session_review.write(path, manifest)


def dispatch(args: argparse.Namespace, execute: Callable[[argparse.Namespace], int]) -> int:
    """全repo共通排他の下で、既存run-skillによる各回の実行と結果確定を行う。"""
    if args.args not in {None, "auto", "manual"}:
        outcome.report_failure(
            "session-review-batchの引数はautoまたはmanualを指定する",
            next_action="手動実行は--args manual、自動起動は--args autoで再実行する",
        )
        return 2
    if args.timeout <= 0:
        outcome.report_failure("--timeoutは正の秒数で指定する", next_action="--timeoutを省略するか正の秒数で再実行する")
        return 2
    manual = args.args != "auto"
    if not manual and config.resolve_mutable_setting("session_review_auto_start") == "false":
        session_review.launch_result()
        return 0
    root = session_review.directory()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.FileLock(str(root / "batch.lock"), timeout=0):
            session_review.sweep(now=time.time())
            return _run_batches(args, execute, manual=manual)
    except filelock.Timeout:
        session_review.launch_result()
        print(
            "同じOSアカウントでagent-toolkit:session-review-batchが実行中。既存の実行終了後に残る対象を処理する。",
            file=sys.stderr,
        )
        return 1


def _run_batches(args: argparse.Namespace, execute: Callable[[argparse.Namespace], int], *, manual: bool) -> int:
    runs = session_review.directory() / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    for path in sorted(runs.glob("*.json")):
        old = session_review.read(path)
        if old["status"] in {"completed", "resolved"}:
            continue
        if old["status"] == "running" and _live(old):
            session_review.launch_result()
            return 1
        if old.get("analysis_succeeded") and add.has_synced_run_report(
            private_notes.default_private_notes(), run_id=old["run_id"], skill=session_review.BATCH_SKILL
        ):
            for prepared in old["prepared"]:
                session_review.complete(prepared)
            old["status"] = "completed"
            session_review.write(path, old)
        elif old["status"] == "running" or any(
            item.get("failure_unreported") and item.get("failure_key") == old.get("failure_key")
            for item in session_review.pending()
        ):
            _failure(old, path, old.get("error", "interrupted: 実行プロセスが終了し成功を確認できない"))
            session_review.launch_result(manifest=path)
            return 1
    while targets := _targets(manual=manual):
        target_repo, cwd = _report_target(targets)
        run_id = uuid.uuid4().hex
        path = runs / f"{run_id}.json"
        manifest: dict[str, Any] = {
            "run_id": run_id,
            "manifest_path": str(path),
            "status": "running",
            "targets": targets,
            "target_repo": target_repo,
            "prepared": [],
        }
        session_review.write(path, manifest)
        session_review.launch_result(manifest=path)
        try:
            for target in targets:
                prepared = _prepare(target)
                # scopeは行位置とdigestだけなので、未確定manifestと同じ寿命で保つ。
                scope_path = runs / f"{run_id}-{target['key']}.scope"
                session_review.write(scope_path, session_review.read(pathlib.Path(prepared["scope_path"])))
                prepared["scope_path"] = str(scope_path)
                manifest["prepared"].append(prepared)
                session_review.write(path, manifest)
            run_args = argparse.Namespace(**vars(args))
            run_args.target_repo, run_args.work_dir = target_repo, str(cwd)
            run_args.batch_manifest, run_args.run_id = str(path), run_id
            code = execute(run_args)
            manifest = session_review.read(path)
            if code:
                _failure(
                    manifest,
                    path,
                    getattr(run_args, "batch_failure", None)
                    or f"run-skill-exit-{code}: 振り返りを完了できなかった。run-skillのログで原因を確認する",
                )
                return code
            if not add.has_synced_run_report(
                private_notes.default_private_notes(), run_id=run_id, skill=session_review.BATCH_SKILL
            ):
                raise ValueError("report-unsynced: 報告の同期を確認できない")
            if not manifest.get("analysis_succeeded"):
                raise ValueError("analysis-incomplete: session-review-state finishの実行が必要")
            for prepared in manifest["prepared"]:
                session_review.complete(prepared)
            manifest["status"] = "completed"
            session_review.write(path, manifest)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            _failure(manifest, path, f"prepare-or-state: {error}")
            return 1
    session_review.launch_result()
    return 0
