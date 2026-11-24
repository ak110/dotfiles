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

from agent_toolkit._atk import (
    config,
    config_settings,
    managed_temp,
    orchestrator,
    outcome,
    research_sources,
    run_script,
    run_skill_batch_evidence,
    weekly_schedule,
)
from agent_toolkit._atk.managed_temp import paths as managed_temp_paths
from agent_toolkit._atk.managed_temp.sweep_lock import sweep_access
from agent_toolkit._atk.wi import add, frontmatter, layout, repo, sync
from agent_toolkit._atk.wi.constants import (
    WI_STATE_ADOPTED,
    WI_STATE_HOLD,
    WI_STATE_INBOX,
    WI_STATE_PROCESSING,
    WI_STATE_REJECTED,
    WI_TYPE_AWI,
    WI_TYPE_UWI,
)
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import (
    delegated_session,
    diagnostic_process,
    markdown_headings,
    plugin_environment,
    private_notes,
    session_activity,
    session_record_paths,
    session_registry,
    session_review,
    skill_modes,
)
from agent_toolkit._common.diagnostic_redaction import redact_secrets
from agent_toolkit._common.wi_layout import DATED_NAME
from agent_toolkit._git import command as git_command
lazy from agent_toolkit._atk import run_skill_state
lazy from agent_toolkit._common import windows_job

_LOG = logging.getLogger(__name__)
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PREPARE_TEMP_PREFIX = "review-batch"


def hook_request(payload: dict[str, Any], *, host: str) -> str | None:
    if delegated_session.is_delegated(os.environ) or payload.get("agent_id"):
        return None
    event = payload.get("hook_event_name")
    target_repo = None
    cwd = payload.get("cwd")
    if event in {"SessionStart", "UserPromptSubmit"} and isinstance(cwd, str):
        with contextlib.suppress(WebInputError):
            target_repo = repo.resolve_repo_id_or_raise(None, cwd=pathlib.Path(cwd))
    session_review.record(payload, host=host, target_repo=target_repo)
    if session_review.is_review_run():
        return None
    if event not in {"SessionStart", "SessionEnd"}:
        return None
    notice = session_review.launch_notice(host) if event == "SessionStart" else None
    pending = session_review.pending()
    count = len(pending)
    if notice is not None:
        return f"{notice}\n振り返り待ち{count}件（活動中・失敗抑止中を含む）。" if count else notice
    batch_enabled = config.resolve_mutable_setting("session_review_auto_start") != "false"
    hosted = session_activity.host_process() is not None
    now = time.time()
    # 週次の作業は作業ごとの設定を共通の判定で読み、batchの自動起動の設定では止めない。  # pyfltr: allow-comment
    weekly = next((work for work in weekly_schedule.WORKS if weekly_auto_start_due(work, now=now)), None) if hosted else None
    if weekly is None and not batch_enabled:
        if event == "SessionStart" and count:
            waiting = (
                f"振り返り待ち{count}件（活動中・失敗抑止中を含む）。"
                "まとめて振り返る場合は、起動の失敗があれば先に原因を是正する。"
                "未処理の対象を確認できた場合だけ、"
                f"`{skill_modes.entry_command(host, 'review-session batch')}`を実行する。"
            )
            return waiting
        return notice
    if not hosted:
        return notice
    if weekly is not None:
        mode = weekly
    elif (
        count
        and session_review.auto_start_due(now=now)
        and any(not target.get("review_batch") and not _waiting_for_rollout(target) for target in pending)
    ):
        mode = "batch"
    else:
        return notice
    env = plugin_environment.direct_environment(PLUGIN_ROOT, orchestrator.child_env(drop=(session_review.BATCH_ENV,)))

    launch_id = uuid.uuid4().hex
    env[session_review.LAUNCH_ENV] = launch_id
    program = (
        ["-m", "agent_toolkit._atk.session_review_launch"]
        if sys.platform == "win32"
        else [str(PLUGIN_ROOT / "agent_toolkit" / "atk.py")]
    )
    launch_path = session_review.directory() / "launches" / f"{launch_id}.json"
    log_path = launch_path.with_suffix(".log")
    with session_review.locked():
        owner = session_registry.ProcessIdentity.of(os.getpid())
        value = session_review.LaunchAttempt.model_validate(
            {
                "host": host,
                "event": event,
                "mode": mode,
                "status": "starting",
                "log_path": str(log_path),
                "owner": owner.payload() if owner else None,
            }
        )
        value.save(launch_path)
        with log_path.open("ab") as log:
            try:
                process = windows_job.start_independent_process(
                    diagnostic_process.command(
                        [
                            sys.executable,
                            *program,
                            "run-skill",
                            "--args",
                            mode,
                            "agent-toolkit:review-session",
                        ]
                    ),
                    cwd=PLUGIN_ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    windows=sys.platform == "win32",
                )
            except OSError as error:
                log.write(redact_secrets(f"独立起動に失敗: {error}\n").encode("utf-8"))
                value.attach(None).save(launch_path)
                return notice
        identity = session_registry.ProcessIdentity.of(process.pid)
        value.attach(identity).save(launch_path)
    threading.Thread(target=process.wait, daemon=True).start()
    return notice


_FAILURE_SOURCE = "session-review"
"""失敗報告AWIの由来。失敗キーによる旧保存記録の抑止も維持する。"""

_ACTIVE_FAILURE_STATES = frozenset({WI_STATE_INBOX, WI_STATE_PROCESSING, WI_STATE_HOLD})
"""失敗報告AWIが未完了で、同じ原因の対象の自動再試行を止める状態。"""


def _failure_entry(failure_key: str, states: frozenset[str]) -> tuple[pathlib.Path, dict[str, Any]] | None:
    """同じ原因の失敗報告AWIを指定状態から返す。"""
    root = private_notes.default_private_notes()
    for path in layout.iter_entry_paths(root):
        if path.is_file():
            parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
            if parsed is not None:
                data, _body = parsed
                if (
                    data.get("type") == WI_TYPE_AWI
                    and data.get("session_review_failure_key") == failure_key
                    and data.get("state") in states
                ):
                    return path, data
    return None


def _blocked(target: dict[str, Any]) -> bool:
    key = target.get("failure_key")
    if not isinstance(key, str) or not key:
        return False
    version = run_skill_batch_evidence.plugin_version(PLUGIN_ROOT)
    failed_version = session_review.failure_version(target, version)
    return _failure_blocks(key, same_version=failed_version == version)


def _failure_blocks(key: str, *, same_version: bool) -> bool:
    if _failure_entry(key, _ACTIVE_FAILURE_STATES) is not None:
        return True
    if _failure_entry(key, frozenset({WI_STATE_ADOPTED, WI_STATE_REJECTED})) is not None:
        session_review.failure_reported(key, reported=False)
        return False
    return same_version


def weekly_auto_start_due(work: str, *, now: float) -> bool:
    if not weekly_schedule.enabled(work) or session_review.consultation_open():
        return False
    slot = weekly_schedule.latest_slot(datetime.datetime.fromtimestamp(now, datetime.UTC))
    identifier = weekly_schedule.slot_id(work, slot)
    if weekly_schedule.completed(work, slot):
        if work == "research":
            research_sources.commit_positions(identifier)
        return False
    record = weekly_schedule.read_record(work)
    if work == "research":
        # 失敗した回は次の判定で再実行する。連続失敗の報告の有無は再実行の判定に使わない。  # pyfltr: allow-comment
        return True
    key = record.get("failure_key")
    if not isinstance(key, str) or record.get("report_synced") is False:
        return True
    version = run_skill_batch_evidence.plugin_version(PLUGIN_ROOT)
    return not _failure_blocks(key, same_version=record.get("failure_version") == version)


def _lacks_record(target: dict[str, Any]) -> bool:
    """Claudeの対象の記録ファイルが存在しないかを返す。記録の所在を別の識別子で解決するhostは対象外とする。"""
    if target["host"] != "claude":
        return False
    path = target.get("transcript_path")
    if not isinstance(path, str) or not path:
        return False
    try:
        pathlib.Path(path).lstat()
    except FileNotFoundError:
        return True
    except OSError:
        # 読取権限や形式の問題は、不在として待ちを消さずprepareの診断へ渡す。
        return False
    return False


def _waiting_for_rollout(target: dict[str, Any]) -> bool:
    return (
        target["host"] == "codex"
        and not target.get("transcript_path")
        and not session_record_paths.find_codex_records(target["session_id"])
    )


def _targets(*, manual: bool, attempted: set[str]) -> list[dict[str, Any]]:
    selected = []
    for target in session_review.pending():
        # 同じ呼び出しで準備に失敗した対象は、manualでも再選定せず同じ失敗の反復を避ける。
        if target["key"] in attempted:
            continue
        if session_activity.review_activity({target["session_id"]}).has_activity:
            continue
        if _lacks_record(target):
            # 発話せずに終了した会話は開始時に待ちへ登録されるが、記録ファイルは作られない。
            # 準備へ渡すと不足の失敗として報告と抑止の対象になるため、振り返る内容の無い対象として待ちから外す。
            if session_review.discard_pending(target):
                print(f"記録ファイルの無い対象を振り返りの待ちから外した: {target['session_id']}", file=sys.stderr)
            continue
        blocked = _blocked(target)
        if not manual and (blocked or target.get("failure_unreported")):
            continue
        if _waiting_for_rollout(target):
            continue
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


def _meta_report_target(*, now: datetime.datetime) -> str:
    first = (now - datetime.timedelta(days=7)).strftime("%Y%m%d-%H%M%S")
    last = now.strftime("%Y%m%d-%H%M%S")
    batch_scopes = skill_modes.accepted_scopes("review-session", "batch")
    targets = []
    for path in layout.iter_entry_paths(private_notes.default_private_notes()):
        if DATED_NAME.fullmatch(path.name) is None or not first <= path.name[:15] <= last:
            continue
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        data, body = parsed
        kind = data.get("type")
        scope = data.get("scope")
        if (
            (
                (kind == WI_TYPE_AWI and data.get("source") in (None, "add-awi-by-user", _FAILURE_SOURCE))
                or (kind == WI_TYPE_UWI and (data.get("run_skill_report") != "true" or scope in batch_scopes))
                or any(
                    name in {"ユーザーコメント", "回答", "ユーザー指摘の逐語引用"} and nonempty
                    for name, nonempty in markdown_headings.h2_sections(body)
                )
            )
            and isinstance(target := data.get("target_repo"), str)
            and target
        ):
            targets.append({"target_repo": target})
    return _report_target(targets)[0]


def _prepare(target: dict[str, Any]) -> dict[str, Any] | None:
    if session_activity.review_activity({target["session_id"]}).has_activity:
        return None
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
    try:
        work_dir = managed_temp.create_managed_temp(_PREPARE_TEMP_PREFIX)
        (work_dir / managed_temp.OWNER_FILE).write_text(managed_temp.owner_token(), encoding="ascii")
    except managed_temp.ManagedTempError as error:
        raise ValueError(str(error)) from error
    arguments.extend(["--work-dir", str(work_dir)])
    arguments.append("--allow-partial-records")
    output = io.StringIO()
    diagnostics = io.StringIO()
    # prepareは成功JSONをstdout、不足項目などの失敗診断をstderrへ出す。分けて捕捉しないと失敗理由が空になる。
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostics):
        code = run_script.dispatch(argparse.Namespace(script_name="session-review-prepare", script_args=arguments))
    if code:
        detail = "\n".join(part for part in (diagnostics.getvalue().strip(), output.getvalue().strip()) if part)
        raise ValueError(f"prepareが終了{code}: {detail}")
    if diagnostics.getvalue():
        print(diagnostics.getvalue(), end="", file=sys.stderr)
    prepared = json.loads(output.getvalue())
    prepared["partial_analysis"] = bool(prepared.get("unconfirmed_scope")) or any(
        item.get("method") == "joined-fragment-lost" for item in prepared.get("recovered_record_lines", [])
    )
    if prepared["partial_analysis"]:
        scope = session_review.read(pathlib.Path(prepared["scope_path"]))
        scope.update(
            partial_analysis=True,
            unconfirmed_scope=prepared.get("unconfirmed_scope", []),
            recovered_record_lines=prepared.get("recovered_record_lines", []),
        )
        for event in prepared.get("unconfirmed_scope", []):
            if event.get("kind") == "record-gap" and event.get("record") in scope["records"]:
                entry = scope["records"][event["record"]]

                entry["observed_through_line"] = max(entry.get("observed_through_line", entry["through_line"]), event["line"])
        session_review.write(pathlib.Path(prepared["scope_path"]), scope)

    prepared.update(host=target["host"], session_id=target["session_id"], generation=target["generation"])
    return prepared


def _plugin_repository() -> str:
    return json.loads((PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["repository"]


def _failure_message(failure_key: str, reason: str, path: pathlib.Path, manifest: dict[str, Any]) -> str:
    return run_skill_batch_evidence.failure_message(failure_key, reason, path, manifest, _plugin_repository())


def _failure(
    manifest: dict[str, Any],
    path: pathlib.Path,
    reason: str,
    *,
    exit_code: int | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> None:
    signature: list[Any] = [reason.split(":", 1)[0]]
    if signature[0].startswith("run-skill-exit-"):
        signature.append(sorted(item["key"] for item in manifest["targets"]))
    else:
        normalized = re.sub(r"[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", "<run-id>", reason)
        normalized = re.sub(r"\d{4}-\d\d-\d\dT[0-9:.+-]+Z?", "<timestamp>", normalized)
        signature.append(normalized)
    failure_key = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
    writer = session_review.BatchWriter(path)
    version = run_skill_batch_evidence.plugin_version(PLUGIN_ROOT)
    manifest = writer.fail(
        failure_key,
        reason,
        version=version,
        source=run_skill_batch_evidence.plugin_source(PLUGIN_ROOT, version),
        exit_code=exit_code,
        diagnostics=diagnostics,
    )
    sync_diagnostic = (manifest.get("failure_diagnostics") or {}).get("report_sync", {})
    defer_push = sync_diagnostic.get("reason") in {"報告の回復を保留", "報告の回復に失敗"}
    saved = _save_failure_report(
        failure_key, lambda: _failure_message(failure_key, reason, path, manifest), defer_push=defer_push
    )
    writer.report_failure(synced=saved)


def _save_failure_report(failure_key: str, message: Callable[[], str], *, defer_push: bool = False) -> bool:
    existing = _failure_entry(failure_key, _ACTIVE_FAILURE_STATES)
    saved = False
    if existing is not None:
        report_path, _data = existing
        try:
            # 同じ原因の是正AWIが未完了なら重複投入せず、保存済みの報告の同期だけを確かめる。
            notes = private_notes.default_private_notes()
            with sync.repo_lock(notes, timeout=30):
                relative = report_path.relative_to(notes).as_posix()
                if defer_push:
                    return git_command.optional_stdout(["show", f"@{{u}}:{relative}"], notes) is not None
                sync.commit_and_push(notes, "chore: 振り返りの失敗報告を同期", [relative])
            saved = True
        except OSError, ValueError, RuntimeError, subprocess.SubprocessError:
            _LOG.exception("既存の失敗報告を同期できない。Git同期の診断を直してから次回の起動で再同期する: %s", report_path)
    elif session_review.failure_reported(failure_key):
        saved = True
    else:
        try:
            add.add_entries(
                private_notes.default_private_notes(),
                messages=[message()],
                target_repo=_plugin_repository(),
                source=_FAILURE_SOURCE,
                now=datetime.datetime.now(datetime.UTC),
                storage=sync.Storage(local_only=defer_push),
            )
            saved = not defer_push
        except OSError, ValueError, subprocess.SubprocessError:
            _LOG.exception("振り返りの失敗報告を保存できない。WI保存先を直してから次回の起動で再試行する: %s", failure_key)
            saved = False
    if saved:
        session_review.failure_reported(failure_key, reported=True)
    return saved


def _run_weekly(work: str, args: argparse.Namespace, execute: Callable[[argparse.Namespace], int], *, manual: bool) -> int:
    if not manual and not weekly_auto_start_due(work, now=time.time()):
        session_review.launch_result()
        return 0
    previous = weekly_schedule.read_record(work)
    start = weekly_schedule.record_path(work)
    if work == "meta" and previous.get("report_synced") is False:
        session_review.launch_result()
        if not _save_meta_failure(start, previous):
            outcome.report_failure(
                "前回のメタ分析の失敗報告を同期できないため、今回は起動していない",
                next_action="起票元のWI保存先とGit同期の診断を直し、同じメタ分析を再実行する",
            )
            return 1
        if not manual:
            return 0
    if getattr(args, "resume_record", None) is not None:
        if isinstance(slot := previous.get("slot"), str):
            args.weekly_slot = slot
        return execute(args)
    work_dir = getattr(args, "work_dir", None)
    if manual:
        latest = weekly_schedule.latest_slot(datetime.datetime.fromtimestamp(time.time(), datetime.UTC))
        if weekly_schedule.completed(work, latest):
            if work == "research":
                research_sources.commit_positions(weekly_schedule.slot_id(work, latest))
            session_review.launch_result()
            outcome.report_success(
                f"{_WEEKLY_TITLES[work]}の最新の週枠（{weekly_schedule.slot_id(work, latest)}）は"
                "結果のUWIが保存済みのため実行しなかった。次の週枠は日曜14時以降に実行できる"
            )
            return 0
        work_dir = (
            pathlib.Path(os.path.expanduser(work_dir))
            if work_dir is not None
            else repo.resolve_local_worktree(args.target_repo)
        )
        if not work_dir.is_absolute() or not work_dir.is_dir():
            outcome.report_failure(
                f"{_WEEKLY_TITLES[work]}の実行cwdには実在する絶対ディレクトリが必要",
                next_action="--work-dirに実在する絶対ディレクトリを指定するか、Git作業ツリー内から起動する",
            )
            return 2
    now = datetime.datetime.fromtimestamp(time.time(), datetime.UTC)
    identifier = weekly_schedule.slot_id(work, weekly_schedule.latest_slot(now))
    record: dict[str, Any] = {"slot": identifier, "started_at": now.isoformat()}
    if work == "research" and previous.get("slot") == identifier:
        record.update(
            {key: previous[key] for key in ("failures", "failure_reported", "failed_at", "failure_stage") if key in previous}
        )
    if previous.get("completed_slot"):
        record["completed_slot"] = previous["completed_slot"]
    args.weekly_slot = identifier
    weekly_schedule.write_record(work, record)
    session_review.launch_result()
    stage = "worktree"
    try:
        research_repo = None
        if work == "research":
            targets = research_sources.repositories()
            for target, settings in targets.items():
                if (path := settings.get("path")) and pathlib.Path(path).is_dir():
                    research_repo = target
                    if work_dir is None:
                        work_dir = pathlib.Path(path)
                    break
            if research_repo is None:
                if work_dir is None or not targets:
                    raise ValueError("外部情報の調査には `atk config repos` で登録したGit作業ツリーが必要")
                research_repo = next(iter(targets))
        if work_dir is None:
            source = repo.resolve_repo_id_or_raise(research_repo or _plugin_repository())
            registered = config_settings.repository_settings(check_paths=False).get(source)
            if registered is None or not (work_dir := pathlib.Path(registered["path"])).is_dir():
                raise ValueError(f"{_WEEKLY_TITLES[work]}のGit作業ツリーを `atk config repos` で登録する: {source}")
        stage = "queue"
        args.target_repo = (
            _meta_report_target(now=now) if research_repo is None else repo.resolve_repo_id_or_raise(research_repo)
        )
        args.work_dir = str(work_dir)
        stage = "execution"
        code = execute(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        _LOG.error("%sを完了できなかった: %s", _WEEKLY_TITLES[work], redact_secrets(str(error)))
        code = 1
    stage = (getattr(args, "batch_failure", None) or "").partition(":")[0] or f"{stage}-{code}"
    if not code:
        if work == "research":
            research_sources.commit_positions(identifier)
        weekly_schedule.write_record(
            work, {"slot": identifier, "started_at": record["started_at"], "completed_slot": identifier}
        )
        return code
    if work == "research":
        _research_failed(record, stage, code)
        return code
    failure_key = hashlib.sha256(f"meta:{stage}".encode()).hexdigest()
    log_path = getattr(args, "log_path", None)
    record.update(
        failure_key=failure_key,
        failure_version=run_skill_batch_evidence.plugin_version(PLUGIN_ROOT),
        failure_stage=stage,
        exit_code=code,
        log_name=log_path.name if isinstance(log_path, pathlib.Path) and log_path.is_file() else None,
        report_synced=False,
    )
    weekly_schedule.write_record(work, record)
    _save_meta_failure(start, record)
    return code


_WEEKLY_TITLES = {"meta": "メタ分析", "research": "外部情報の調査"}


def _research_failed(record: dict[str, Any], stage: str, code: int) -> None:
    failures = int(record.get("failures", 0)) + 1
    record.update(
        failures=failures,
        failed_at=datetime.datetime.fromtimestamp(time.time(), datetime.UTC).isoformat(),
        failure_stage=stage,
    )
    if failures >= weekly_schedule.RESEARCH_FAILURE_REPORT_THRESHOLD and not record.get("failure_reported"):
        try:
            add.add_entries(
                private_notes.default_private_notes(),
                messages=[
                    "---\nrun_skill_report: true\n---\n\n"
                    f"# 外部情報の調査（{record['slot']}）が{failures}回続けて失敗した\n\n"
                    f"最後の失敗は段階 `{stage}`、終了{code}である。"
                    "提案はまだ届いておらず、次の判定で同じ回を再実行する。この報告は同じ回で1回だけ届く。"
                    "起票元のagent-toolkitの状態ディレクトリにあるrun-skillのログで原因を確かめる。"
                    "手動での確認には `atk run-skill --args research review-session` を使う。\n"
                ],
                target_repo=_plugin_repository(),
                source="run-skill",
                scope=session_review.RESEARCH_SCOPE,
                entry_type=WI_TYPE_UWI,
                question_type="post-approval",
                now=datetime.datetime.now(datetime.UTC),
            )
            record["failure_reported"] = True
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            _LOG.error("外部情報の調査の失敗を報告できない。次の失敗で再試行する: %s", redact_secrets(str(error)))
    weekly_schedule.write_record("research", record)


def _save_meta_failure(start: pathlib.Path, record: dict[str, Any]) -> bool:
    failure_key = record["failure_key"]
    if log_name := record.get("log_name"):
        locate = f"起票元のagent-toolkitの状態ディレクトリにあるrun-skillのログ `{log_name}` で原因を確かめる。"
    elif record["failure_stage"] == "worktree-1":
        locate = "起票元の `atk config repos list` で配布元のGit作業ツリーの登録と実体を確かめる。実行ログは未作成。"
    elif record["failure_stage"] == "queue-1":
        locate = "起票元で `atk wi list --state=all --target-repo=all --skip-pull` を実行し、読取の診断を確かめる。"
    else:
        locate = "起票元で `atk run-skill --args meta review-session` を実行して起動の診断を確かめる。"
    saved = _save_failure_report(
        failure_key,
        lambda: (
            f"---\nsession_review_failure_key: {json.dumps(failure_key)}\n---\n\n"
            "# 週次メタ分析（review-session meta）の失敗の是正\n\n"
            f"週次メタ分析が終了{record['exit_code']}となり、報告を完了できなかった"
            f"（段階: {record['failure_stage']}、開始: {record['started_at']}）。\n\n"
            "## 反映内容と反映先\n\n"
            f"{locate}配布元で失敗原因を是正する。\n\n"
            "## 完成条件\n\n"
            "原因を再現する入力で失敗と是正後の報告の保存を確かめる。"
            "手動での確認には `atk run-skill --args meta review-session` を使う。\n"
        ),
    )
    if saved:
        session_review.write(start, {**record, "report_synced": True})
    return saved


def _live(manifest: dict[str, Any]) -> bool:
    stable = manifest.get("process_identities", [])
    processes = manifest.get("processes", [])
    if processes:
        return any(
            session_registry.ProcessIdentity.from_payload(raw, stable=stable[index] if index < len(stable) else None).exists()
            for index, raw in enumerate(processes)
        )
    # PIDの保存より前に親が中断した回だけ、別会話の環境読取権限には依存せず、起動argvの固有manifestパスで子を特定する。
    marker = f"batch manifest: {manifest['manifest_path']}"
    username = psutil.Process().username()
    for process in psutil.process_iter():
        try:
            if (
                process.username() == username
                and any(marker in part for part in process.cmdline())
                and process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
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
    identity = session_registry.ProcessIdentity.of(pid)
    if identity is not None:
        writer = session_review.BatchWriter(path)
        _pin_prepared(writer.load().model_dump(mode="json", exclude_unset=True), executor=identity)
        writer.attach(identity)


def _pin_prepared(manifest: dict[str, Any], *, executor: session_registry.ProcessIdentity | None = None) -> None:
    token = managed_temp.owner_token(executor)
    for prepared in manifest["prepared"]:
        if not (value := prepared.get("work_dir")):
            continue
        path = pathlib.Path(value)
        dedicated = path.parent == managed_temp.temp_root_path() and path.name.startswith(f"{_PREPARE_TEMP_PREFIX}-")
        # 旧prepareの親は元記録のstemから決まり、待ちのセッションIDと異なる場合がある。  # pyfltr: allow-comment
        legacy = path.parent.parent == managed_temp.temp_root_path() and path.name.startswith("session-review-")
        if not dedicated and not legacy:
            continue
        try:
            scope = managed_temp.contained_target(path / "bundle/scope.json")
            if not scope.is_file():
                raise ValueError(f"準備資料が見つからない: {path}。元の記録を確かめ、新しいbatchとして準備し直してください")
            # 旧領域はwork-dirの親が掃引単位なので、実行者の表示もその直下に置く。  # pyfltr: allow-comment
            directory = path
            if executor is not None:
                directory = managed_temp.create_session_temp("review-batch-owner", path.parent) if legacy else scope.parent
            owner = managed_temp.contained_target(directory / managed_temp.OWNER_FILE)
            owner.write_text(token, encoding="ascii")
        except managed_temp.ManagedTempError as error:
            raise ValueError(str(error)) from error


def dispatch(args: argparse.Namespace, execute: Callable[[argparse.Namespace], int]) -> int:
    """全repo共通排他の下で、既存run-skillによる各回の実行と結果確定を行う。"""
    if args.timeout <= 0 and getattr(args, "resume_record", None) is None:
        outcome.report_failure("--timeoutは正の秒数で指定する", next_action="--timeoutを省略するか正の秒数で再実行する")
        return 2

    manual = not os.environ.get(session_review.LAUNCH_ENV) and not getattr(args, "scheduled", False)
    weekly = weekly_schedule.work_of_scope(skill_modes.invocation_scope(args.skill, args.args))
    # 週次の作業の可否は_run_weeklyが作業ごとの共通の判定で決める。  # pyfltr: allow-comment
    if not manual and weekly is None and config.resolve_mutable_setting("session_review_auto_start") == "false":
        session_review.launch_result()
        return 0
    root = session_review.directory()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.FileLock(str(root / "batch.lock"), timeout=0):
            if weekly is not None:
                return _run_weekly(weekly, args, execute, manual=manual)
            session_review.sweep(now=time.time())
            if getattr(args, "resume_record", None) is not None:
                return _resume_batch(args, execute)
            return _run_batches(args, execute, manual=manual)
    except filelock.Timeout:
        session_review.launch_result()
        print(
            "同じOSアカウントで振り返りのbatchかmetaが実行中。既存の実行が終わってから再実行する。",
            file=sys.stderr,
        )
        return 1


def _finish_batch(manifest: dict[str, Any], path: pathlib.Path) -> int:
    """保存済み範囲の分析と報告の同期を確認して、同じbatchだけを確定する。"""
    diagnostics: dict[str, Any] | None = None
    try:
        notes = private_notes.default_private_notes()
        gap = add.check_run_report_sync(notes, run_id=manifest["run_id"], scope=session_review.BATCH_SCOPE, sync_report=True)
        if gap is not None:
            # ファイル名やOIDを失敗キーに含めると同じ原因のAWIが重複する。  # pyfltr: allow-comment
            diagnostics = {"report_sync": {"reason": gap.reason, **gap.detail}}
            raise ValueError(f"report-unsynced: 報告の同期を確認できない（{gap.reason}）")
        session_review.BatchWriter(path).complete(report_synced=True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, filelock.Timeout) as error:
        reason = f"finish: {error}"
        _failure(manifest, path, reason, diagnostics=diagnostics)
        outcome.report_failure(
            f"{reason}（実行記録: {path}）",
            next_action=f"分析完了と報告用UWIの同期を確認し、必要なら atk run-skill --resume {manifest['run_id']} で再開する",
        )
        return 1
    run_skill_state.mark_completed(manifest["run_id"])
    if log_path := manifest.get("log_path"):
        outcome.report_success(f"スキルを実行した（ログ: {log_path}）")
    return 0


def _resume_batch(args: argparse.Namespace, execute: Callable[[argparse.Namespace], int]) -> int:
    path = session_review.directory() / "runs" / f"{args.run_id}.json"
    manifest = session_review.BatchWriter(path).load().model_dump(mode="json", exclude_unset=True)
    if str(path) != args.batch_manifest or manifest.get("run_id") != args.run_id:
        outcome.report_failure("再開するbatchの実行識別子と保存先が一致しない", next_action="元のbatch実行記録を確認する")
        return 2
    session_review.launch_result(manifest=path)
    if manifest["status"] in {"completed", "resolved"}:
        run_skill_state.mark_completed(args.run_id)
        outcome.report_success(f"既に完了したbatch: {path}")
        return 0
    try:
        if any(prepared.get("work_dir") for prepared in manifest["prepared"]):
            # 掃引の使用中判定から削除までの間に、期限超過の準備資料を再び使い始めない。  # pyfltr: allow-comment
            with sweep_access(managed_temp_paths.temp_root(), managed_temp_paths.state_root(create=False)) as locked:
                if not locked:
                    outcome.report_failure(
                        "準備資料の領域を掃引中",
                        next_action=f"掃引が終わってから atk run-skill --resume {manifest['run_id']} で再開する",
                    )
                    return 1
                _pin_prepared(manifest)
        if manifest["status"] == session_review.BatchStatus.USAGE_LIMIT_PAUSED:
            args.resume_usage_limited = True
            session_review.BatchWriter(path).resume_usage_limited()
        code = execute(args)
        manifest = session_review.read(path)
        if code:
            if _pause_for_usage_limit(args, path, code):
                return code
            _failure(
                manifest,
                path,
                getattr(args, "batch_failure", None) or f"run-skill-exit-{code}: 元のbatchの再開が完了しなかった",
                exit_code=code,
            )
            return code
        return _finish_batch(manifest, path)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        _failure(manifest, path, f"prepare-or-state: {error}")
        return 1


def _pause_for_usage_limit(args: argparse.Namespace, path: pathlib.Path, code: int) -> bool:
    if (resume_at := getattr(args, "batch_usage_resume_at", None)) is None:
        return False
    session_review.BatchWriter(path).pause_for_usage_limit(resume_at, code)
    return True


_INTERRUPTED_REASON = "interrupted: 実行プロセスが終了し成功を確認できない"


def _interrupted_before(runs: pathlib.Path, manifest: dict[str, Any]) -> bool:
    """同じ対象の同じ世代が、直前にも中断として再試行へ戻されたかを返す。

    中断の回数は実行記録だけから数え、待ち記録へ状態を足さない。対象が活動を再開して世代が変わった後の中断は1回目として扱う。
    """
    current = {(item["key"], item["generation"]) for item in manifest["targets"]}
    for path in runs.glob("*.json"):
        other = session_review.read(path)
        if other.get("run_id") == manifest["run_id"] or not other.get("interrupted_retry"):
            continue
        if current & {(item.get("key"), item.get("generation")) for item in other.get("targets", [])}:
            return True
    return False


def _run_batches(args: argparse.Namespace, execute: Callable[[argparse.Namespace], int], *, manual: bool) -> int:
    runs = session_review.directory() / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    for path in sorted(runs.glob("*.json")):
        old = session_review.BatchWriter(path).load().model_dump(mode="json", exclude_unset=True)
        if old["status"] in {"completed", "resolved"}:
            continue
        if old["status"] == session_review.BatchStatus.USAGE_LIMIT_PAUSED:
            if time.time() < old["usage_resume_at"]:
                session_review.launch_result()
                if manual:
                    resume_at = datetime.datetime.fromtimestamp(old["usage_resume_at"], datetime.UTC).isoformat(
                        timespec="seconds"
                    )
                    print(
                        f"利用上限の解除待ち: {resume_at}。"
                        f"明示的に試す場合は `atk run-skill --resume {old['run_id']}` を実行してください。",
                        file=sys.stderr,
                    )
                return 0
            record = run_skill_state.load(old["run_id"])
            resumed = argparse.Namespace(**vars(args))
            resumed.skill, resumed.args = record.input.skill, record.input.arguments
            resumed.target_repo = record.input.target_repo or str(record.work_dir)
            resumed.work_dir = str(record.work_dir) if record.input.target_repo is not None else None
            resumed.timeout, resumed.run_id = record.remaining, record.input.run_id
            resumed.batch_manifest, resumed.resume_record = str(path), record
            return _resume_batch(resumed, execute)
        if old["status"] == "running" and _live(old):
            session_review.launch_result()
            return 1
        if old.get("analysis_succeeded") and add.has_synced_run_report(
            private_notes.default_private_notes(),
            run_id=old["run_id"],
            scope=old.get("report_scope", session_review.LEGACY_BATCH_SCOPE),
        ):
            session_review.BatchWriter(path).complete(report_synced=True)
            run_skill_state.mark_completed(old["run_id"])
        elif old["status"] == "running" and not old.get("error") and not _interrupted_before(runs, old):
            # 端末の停止などで実行プロセスだけが終了した回は、原因を示す診断を持たない。
            # 1回目は報告と抑止にせず対象を待ちに残し、同じ対象が続けて中断した場合に失敗として報告する。
            session_review.BatchWriter(path).retry_interrupted(_INTERRUPTED_REASON)
            print(f"実行プロセスが終了した振り返りを再試行の対象へ戻した: {path}", file=sys.stderr)
        elif old["status"] == "running" or any(
            item.get("failure_unreported") and item.get("failure_key") == old.get("failure_key")
            for item in session_review.pending()
        ):
            _failure(old, path, old.get("error", _INTERRUPTED_REASON))
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
        writer = session_review.BatchWriter(path)
        manifest = writer.create(manifest)
        session_review.launch_result(manifest=path)
        try:
            failed: list[tuple[dict[str, Any], str]] = []
            deferred: set[str] = set()
            for target in targets:
                try:
                    prepared = _prepare(target)
                except ValueError as error:
                    # 1対象の準備失敗で残りの対象と、回答済みの過去UWIを取り込むモデル起動を止めない。
                    failed.append((target, str(error)))
                    continue
                if prepared is None:
                    # 選定後の活動再開は正常な延期であり、失敗抑止せず次の機会へ残す。
                    deferred.add(target["key"])
                    continue

                scope_path = runs / f"{run_id}-{target['key']}.scope"
                session_review.write(scope_path, session_review.read(pathlib.Path(prepared["scope_path"])))
                prepared["bundle_scope_path"] = prepared["scope_path"]
                prepared["scope_path"] = str(scope_path)
                manifest = writer.prepare(prepared)
            if failed:
                prepare_failed = True
                _prepare_failure(runs, target_repo, failed)
            if failed or deferred:
                omitted = deferred | {target["key"] for target, _ in failed}
                manifest = writer.select([target for target in targets if target["key"] not in omitted], target_repo)
                if not any(not target.get("review_batch") for target in manifest["targets"]):
                    writer.resolve()
                    continue

                target_repo, cwd = _report_target(manifest["targets"])
                manifest = writer.select(manifest["targets"], target_repo)
            run_args = argparse.Namespace(**vars(args))
            run_args.target_repo, run_args.work_dir = target_repo, str(cwd)
            run_args.batch_manifest, run_args.run_id = str(path), run_id
            code = execute(run_args)
            manifest = session_review.read(path)
            if code:
                if _pause_for_usage_limit(run_args, path, code):
                    return code
                _failure(
                    manifest,
                    path,
                    getattr(run_args, "batch_failure", None)
                    or f"run-skill-exit-{code}: 振り返りを完了できなかった。run-skillのログで原因を確認する",
                    exit_code=code,
                )
                return code
            if code := _finish_batch(manifest, path):
                return code
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
    session_review.BatchWriter(path).create(manifest)
    _failure(manifest, path, "prepare-or-state: " + "; ".join(reason for _, reason in failed))
