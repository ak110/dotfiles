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
from agent_toolkit._atk.wi import add, frontmatter, layout, repo, sync
from agent_toolkit._atk.wi.constants import (
    WI_STATE_HOLD,
    WI_STATE_INBOX,
    WI_STATE_PROCESSING,
    WI_TYPE_AWI,
)
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
    if delegated_session.is_delegated(os.environ) or payload.get("agent_id"):
        return None
    event = payload.get("hook_event_name")
    target_repo = None
    cwd = payload.get("cwd")
    if event in {"SessionStart", "UserPromptSubmit"} and isinstance(cwd, str):
        with contextlib.suppress(WebInputError):
            target_repo = repo.resolve_repo_id_or_raise(None, cwd=pathlib.Path(cwd))
    session_review.record(payload, host=host, target_repo=target_repo)
    if os.environ.get(session_review.BATCH_ENV):
        return None
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
                "まとめて振り返るには /agent-toolkit:review-session batch を実行する。"
            )
            return "\n".join(part for part in (notice, waiting) if part)
        return notice
    if session_activity.host_process() is None:
        # ホスト外で再生したhook入力は記録だけにとどめ、実在するホストの開始・終了から起動する。
        return notice
    if not session_review.auto_start_due(now=time.time()) or not any(
        not target.get("review_batch") for target in session_review.pending()
    ):
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
                        "batch",
                        "agent-toolkit:review-session",
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


_FAILURE_SOURCE = "session-review"
"""失敗報告AWIの由来。失敗キーによる旧保存記録の抑止も維持する。"""

_ACTIVE_FAILURE_STATES = frozenset({WI_STATE_INBOX, WI_STATE_PROCESSING, WI_STATE_HOLD})
"""失敗報告AWIが未完了で、同じ原因の対象の自動再試行を止める状態。"""


def _active_failure(failure_key: str) -> tuple[pathlib.Path, dict[str, Any]] | None:
    """同じ原因の未完了の失敗報告AWIを返す。"""
    root = private_notes.default_private_notes()
    for path in layout.iter_entry_paths(root):
        if path.is_file():
            parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
            if parsed is not None:
                data, _body = parsed
                if (
                    data.get("type") == WI_TYPE_AWI
                    and data.get("session_review_failure_key") == failure_key
                    and data.get("state") in _ACTIVE_FAILURE_STATES
                ):
                    return path, data
    return None


def _blocked(target: dict[str, Any]) -> bool:
    key = target.get("failure_key")
    # 是正を担うAWIの終了（採用・不採用）までは同じ失敗を繰り返さず、終了後の次回起動で再試行する。
    return isinstance(key, str) and bool(key) and _active_failure(key) is not None


def _targets(*, manual: bool, attempted: set[str]) -> list[dict[str, Any]]:
    selected = []
    for target in session_review.pending():
        # 同じ呼び出しで準備に失敗した対象は、manualでも再選定せず同じ失敗の反復を避ける。
        if target["key"] in attempted:
            continue
        if not manual and (_blocked(target) or target.get("failure_unreported")):
            continue
        activity = session_activity.review_activity({target["session_id"]})
        if not activity.has_activity:
            selected.append(target)
    return selected if any(not target.get("review_batch") for target in selected) else []


def _report_target(targets: list[dict[str, Any]]) -> tuple[str, pathlib.Path]:
    counts = collections.Counter(target["target_repo"] for target in targets if target.get("target_repo"))
    chosen = min(counts, key=lambda item: (-counts[item], item)) if counts else _plugin_repository()
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
    diagnostics = io.StringIO()
    # prepareは成功JSONをstdout、不足項目などの失敗診断をstderrへ出す。分けて捕捉しないと失敗理由が空になる。
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostics):
        code = run_script.dispatch(argparse.Namespace(script_name="session-review-prepare", script_args=arguments))
    if code:
        detail = "\n".join(part for part in (diagnostics.getvalue().strip(), output.getvalue().strip()) if part)
        raise ValueError(f"prepareが終了{code}: {detail}")
    if diagnostics.getvalue():
        # 成功時の警告は捕捉前と同じく呼び出し元のstderrへ残す。
        print(diagnostics.getvalue(), end="", file=sys.stderr)
    prepared = json.loads(output.getvalue())
    if prepared.get("unconfirmed_scope"):
        raise ValueError(f"prepareに未確認範囲がある: {target['session_id']}")
    # 選定後に再登録された世代を成功処理で消さない。
    prepared["generation"] = target["generation"]
    return prepared


def _plugin_repository() -> str:
    """実行中の配布物のリポジトリ識別子を返す。振り返りの仕組みの不具合はこの配布元で直す。"""
    return json.loads((PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["repository"]


def _failure_message(failure_key: str, reason: str, path: pathlib.Path, manifest: dict[str, Any]) -> str:
    """配布元で是正するための失敗報告AWIを組み立てる。ユーザー判断が要る点は処理担当がUWIで確認する。"""
    targets = ", ".join(item["session_id"] for item in manifest["targets"])
    body = (
        "# 非同期振り返り（review-session batch）の失敗の是正\n\n"
        "非同期振り返りが下記の対象セッションを処理できなかった。"
        "このAWIが終了するまで、同じ原因で失敗した対象の自動振り返りを止めている。"
        "原因を是正し、対象の振り返りが自動で再開できる状態へ戻す。\n\n"
        "## 反映内容と反映先\n\n"
        "失敗理由と実行記録から原因を特定し、振り返りの前後処理または準備処理を是正する。"
        f"反映先は`{_plugin_repository()}`とする。"
        "是正にユーザーの判断が要る点があれば、UWIで確認する。\n\n"
        "## 調査結果と判断根拠\n\n"
        "失敗理由は振り返りの起動元が記録した値である。"
        "実行記録には固定した対象、準備結果、失敗の記録があり、対象のセッション記録の所在は待ち記録にある。\n\n"
        "## 完成条件\n\n"
        "失敗理由の原因が是正され、対象セッションで準備から失敗した工程までの成功を確認する。"
        "準備以降に失敗した場合は、子の結果回収、振り返り、報告の同期とsession-review-state finishまで完了する。"
        "このAWIの終了後、次のセッション開始・終了時に、止めていた対象の振り返りが自動で再試行される。\n\n"
        "## 詳細\n\n"
        f"失敗理由: {reason}\n"
        f"実行記録: {path}\n"
        f"対象: {targets}\n"
    )
    metadata = f"session_review_failure_key: {json.dumps(failure_key)}\n"
    return f"---\n{metadata}---\n\n{body}"


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
    existing = _active_failure(failure_key)
    manifest.update(status="failed", failure_key=failure_key, error=reason)
    session_review.write(path, manifest)
    saved = False
    if existing is not None:
        report_path, _data = existing
        try:
            # 同じ原因の是正AWIが未完了なら重複投入せず、保存済みの報告の同期だけを確かめる。
            notes = private_notes.default_private_notes()
            with sync.repo_lock(notes, timeout=30):
                sync.commit_and_push(notes, "chore: 振り返りの失敗報告を同期", [str(report_path.relative_to(notes))])
            saved = True
        except OSError, ValueError, RuntimeError, subprocess.SubprocessError:
            _LOG.exception("既存の失敗報告を同期できない: %s", report_path)
    else:
        try:
            add.add_entries(
                private_notes.default_private_notes(),
                messages=[_failure_message(failure_key, reason, path, manifest)],
                target_repo=_plugin_repository(),
                source=_FAILURE_SOURCE,
                now=datetime.datetime.now(datetime.UTC),
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
    if args.timeout <= 0:
        outcome.report_failure("--timeoutは正の秒数で指定する", next_action="--timeoutを省略するか正の秒数で再実行する")
        return 2
    # hookが発行する起動識別子で区別し、着眼点のauto/manualを制御情報として消費しない。
    manual = not bool(os.environ.get(session_review.LAUNCH_ENV))
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
            "同じOSアカウントでagent-toolkit:review-session batchが実行中。既存の実行終了後に残る対象を処理する。",
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
            private_notes.default_private_notes(),
            run_id=old["run_id"],
            skill=old.get("report_scope", session_review.LEGACY_BATCH_SCOPE),
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
    attempted: set[str] = set()
    prepare_failed = False
    while targets := _targets(manual=manual, attempted=attempted):
        # hookから排他取得までの競合と、同じ呼び出し内での連続自動起動も防ぐ。
        if not manual and not session_review.auto_start_due(now=time.time()):
            break
        attempted.update(target["key"] for target in targets)
        target_repo, cwd = _report_target(targets)
        run_id = uuid.uuid4().hex
        path = runs / f"{run_id}.json"
        manifest: dict[str, Any] = {
            "run_id": run_id,
            "report_scope": session_review.BATCH_SCOPE,
            "manifest_path": str(path),
            "status": "running",
            "started_at": session_review.timestamp(),
            "targets": targets,
            "target_repo": target_repo,
            "prepared": [],
        }
        session_review.write(path, manifest)
        session_review.launch_result(manifest=path)
        try:
            failed: list[tuple[dict[str, Any], str]] = []
            for target in targets:
                try:
                    prepared = _prepare(target)
                except ValueError as error:
                    # 1対象の準備失敗で残りの対象と、回答済みの過去UWIを取り込むモデル起動を止めない。
                    failed.append((target, str(error)))
                    continue
                # scopeは行位置とdigestだけなので、未確定manifestと同じ寿命で保つ。
                scope_path = runs / f"{run_id}-{target['key']}.scope"
                session_review.write(scope_path, session_review.read(pathlib.Path(prepared["scope_path"])))
                prepared["scope_path"] = str(scope_path)
                manifest["prepared"].append(prepared)
                session_review.write(path, manifest)
            if failed:
                prepare_failed = True
                _prepare_failure(runs, target_repo, failed)
                failed_keys = {target["key"] for target, _ in failed}
                manifest["targets"] = [target for target in targets if target["key"] not in failed_keys]
                if not any(not target.get("review_batch") for target in manifest["targets"]):
                    # 通常対象を準備できない回も、batch自身だけの分析を起動しない。
                    manifest["status"] = "resolved"
                    session_review.write(path, manifest)
                    continue
                # 報告先は実際にモデルへ渡す対象から決め直す。
                target_repo, cwd = _report_target(manifest["targets"])
                manifest["target_repo"] = target_repo
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
                private_notes.default_private_notes(), run_id=run_id, skill=session_review.BATCH_SCOPE
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
    return 1 if prepare_failed else 0


def _prepare_failure(runs: pathlib.Path, target_repo: str, failed: list[tuple[dict[str, Any], str]]) -> None:
    """準備に失敗した対象群だけを独立した失敗記録へ移し、既存の失敗報告と抑止へ渡す。"""
    run_id = uuid.uuid4().hex
    path = runs / f"{run_id}.json"
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "manifest_path": str(path),
        "status": "running",
        "started_at": session_review.timestamp(),
        "targets": [target for target, _ in failed],
        "target_repo": target_repo,
        "prepared": [],
    }
    session_review.write(path, manifest)
    _failure(manifest, path, "prepare-or-state: " + "; ".join(reason for _, reason in failed))
