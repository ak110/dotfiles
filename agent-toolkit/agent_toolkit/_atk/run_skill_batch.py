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
    run_script,
    run_skill_batch_evidence,
)
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
lazy from agent_toolkit._atk import run_skill_state
lazy from agent_toolkit._common import windows_job

_LOG = logging.getLogger(__name__)
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PREPARE_TEMP_PREFIX = "review-batch"


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
    if session_review.is_review_run():
        return None
    if event not in {"SessionStart", "SessionEnd"}:
        return None
    notice = session_review.launch_notice(host) if event == "SessionStart" else None
    pending = session_review.pending()
    count = len(pending)
    if config.resolve_mutable_setting("session_review_auto_start") == "false":
        if event == "SessionStart" and count:
            waiting = (
                f"振り返り待ち{count}件（活動中・失敗抑止中を含む）。"
                f"まとめて振り返る場合は`{skill_modes.entry_command(host, 'review-session batch')}`を実行せよ。"
            )
            return "\n".join(part for part in (notice, waiting) if part)
        return notice
    if session_activity.host_process() is None:
        # ホスト外で再生したhook入力は記録だけにとどめ、実在するホストの開始・終了から起動する。
        return notice
    now = time.time()
    if _meta_auto_start_due(now=now):
        mode = "meta"
    elif (
        count
        and session_review.auto_start_due(now=now)
        and any(not target.get("review_batch") and not _waiting_for_rollout(target) for target in pending)
    ):
        mode = "batch"
    else:
        return notice
    env = plugin_environment.direct_environment(PLUGIN_ROOT, orchestrator.child_env(drop=(session_review.BATCH_ENV,)))
    # セッションのstdin/stdoutを保持せず、受付前の試行と受付後の実行記録で責任を分ける。
    # Windowsのhook本体はlaunch.pyが所有するJob（KILL_ON_JOB_CLOSE）の中で動く。
    # Jobから離脱しない子は、受付前にhookの終了と一緒に回収される。
    attempts: list[dict[str, Any]]
    if sys.platform == "win32":
        attempts = [
            {"creationflags": windows_job.HIDDEN_DETACHED_CREATIONFLAGS | windows_job.CREATE_BREAKAWAY_FROM_JOB},
            # 離脱を許さないJobの中では離脱指定の起動が失敗するため、所属を継承して起動する。
            {"creationflags": windows_job.HIDDEN_DETACHED_CREATIONFLAGS},
        ]
    else:
        attempts = [{"start_new_session": True}]
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
            # hook終了後も生存確認できるようPIDを保存し、親が存続する間はスレッドで回収する。
            # 時間上限なし: 長時間の子セッションの実行そのものであり、終了は監督の仕組みが扱う。
            failures: list[str] = []
            for flags in attempts:
                try:
                    process = subprocess.Popen(  # pylint: disable=consider-using-with
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
                        **flags,
                    )
                    break
                except OSError as error:
                    failures.append(redact_secrets(f"{flags}: {error}"))
            else:
                log.write("\n".join(failures).encode("utf-8"))
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


def _meta_auto_start_due(*, now: float) -> bool:
    if not session_review.meta_start_due(now=now):
        return False
    try:
        last = session_review.read(session_review.directory() / "meta-start.json")
    except FileNotFoundError:
        return True
    key = last.get("failure_key")
    if not isinstance(key, str) or last.get("report_synced") is False:
        return True
    version = run_skill_batch_evidence.plugin_version(PLUGIN_ROOT)
    return not _failure_blocks(key, same_version=last.get("failure_version") == version)


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
    """開始記録だけのCodex会話は、rolloutが後から届くまで待ちに残す。"""
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
    """比較期間や前回からの繰越を除き、今回の入力WIの投入先で報告先を決める。"""
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
    # batchは欠測を結果へ明示した部分分析を行う。単発prepareは明示指定時だけ部分読込を選ぶ。
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
        # 成功時の警告は捕捉前と同じく呼び出し元のstderrへ残す。
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
                # 末尾欠測も観測済み範囲に含め、次回の新規追記と区別する。digestは読めた完全行だけを表す。
                entry["observed_through_line"] = max(entry.get("observed_through_line", entry["through_line"]), event["line"])
        session_review.write(pathlib.Path(prepared["scope_path"]), scope)
    # 待ち記録を作った識別子と世代で確定する。元ファイル名から作った識別子は使わない。
    prepared.update(host=target["host"], session_id=target["session_id"], generation=target["generation"])
    return prepared


def _plugin_repository() -> str:
    """実行中の配布物のリポジトリ識別子を返す。振り返りの仕組みの不具合はこの配布元で直す。"""
    return json.loads((PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["repository"]


def _failure_message(failure_key: str, reason: str, path: pathlib.Path, manifest: dict[str, Any]) -> str:
    """配布元で是正するための失敗報告AWIを組み立てる。ユーザー判断が要る点は処理担当がUWIで確認する。"""
    targets = ", ".join(item["session_id"] for item in manifest["targets"])
    log = f"実行ログ: {manifest['log_path']}\n" if manifest.get("log_path") else ""
    # 準備失敗はモデルの実行ログを持たず、manifestに入力と診断を保持する。
    action = (
        "実行記録のerrorと、実行ログの終了状態・標準出力・標準エラーを比較する。"
        if log
        else "実行記録のerror・targets・preparedから失敗した工程と入力を確認し、"
        "errorにある不足項目と次の操作に従って入力や読取条件を修正する。"
    )
    body = (
        "# 非同期振り返り（review-session batch）の失敗の是正\n\n"
        "非同期振り返りが下記の対象セッションを処理できなかった。"
        "同じ原因の未完了AWIがある間は対象の自動振り返りを止めている。"
        "AWIがadopted・rejectedで終了した場合、または未完了AWIが無くagent-toolkitの版が変わった場合に再試行する。"
        "削除だけでは再試行せず、起票済みの同じ失敗は終了AWIを観測するまで再起票しない。\n\n"
        "## 反映内容と反映先\n\n"
        "失敗理由と実行記録から原因を特定し、振り返りの前後処理または準備処理を是正する。"
        f"反映先は`{_plugin_repository()}`とする。"
        "是正にユーザーの判断が要る点があれば、UWIで確認する。\n\n"
        "## 調査結果と判断根拠\n\n"
        "失敗理由は振り返りの起動元が記録した値である。"
        "下記に入力のメタ情報と診断を埋め込んだ。セッション記録の本文は転記していない。\n\n"
        "## 完成条件\n\n"
        "埋め込まれた入力と記録状態から合成した再現テストを作り、是正前の失敗と是正後の成功を確認する。"
        "再現テストで準備から失敗した工程までの成功を確認する。"
        "準備以降に失敗した場合は、子の結果回収、振り返り、報告の同期とsession-review-state finishまで完了する。"
        "起票ホストでの実際の再試行は是正を含む版の配布後に行い、処理担当は観測しなくてよい。"
        "手動で再試行する場合は`atk run-skill agent-toolkit:review-session --args batch`を使う。"
        "このAWIの終了後、`session_review_auto_start=true`で開始条件を満たす場合に限り、"
        "セッション開始・終了時に自動起動できる。"
        "`session_review_auto_start=false`の場合は自動起動しないため、手動実行する。\n\n"
        "## 詳細\n\n"
        f"失敗理由: {redact_secrets(reason)}\n"
        f"実行記録: {path}\n"
        f"{log}"
        f"次の操作: {action}"
        "本文の埋め込み情報で足りない場合に限り、起票ホストで不足する情報を取得する。\n"
        f"対象: {targets}\n"
        f"\n{run_skill_batch_evidence.failure_details(manifest, PLUGIN_ROOT)}"
    )
    metadata = f"session_review_failure_key: {json.dumps(failure_key)}\n"
    return f"---\n{metadata}---\n\n{body}"


def _failure(manifest: dict[str, Any], path: pathlib.Path, reason: str, *, exit_code: int | None = None) -> None:
    signature: list[Any] = [reason.split(":", 1)[0]]
    if signature[0].startswith("run-skill-exit-"):
        # 終了コードだけから別の対象で同じ原因が生じたとは推定しない。
        signature.append(sorted(item["key"] for item in manifest["targets"]))
    else:
        normalized = re.sub(r"[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", "<run-id>", reason)
        normalized = re.sub(r"\d{4}-\d\d-\d\dT[0-9:.+-]+Z?", "<timestamp>", normalized)
        signature.append(normalized)
    failure_key = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
    writer = session_review.BatchWriter(path)
    manifest = writer.fail(
        failure_key, reason, version=run_skill_batch_evidence.plugin_version(PLUGIN_ROOT), exit_code=exit_code
    )
    saved = _save_failure_report(failure_key, lambda: _failure_message(failure_key, reason, path, manifest))
    writer.report_failure(synced=saved)


def _save_failure_report(failure_key: str, message: Callable[[], str]) -> bool:
    existing = _failure_entry(failure_key, _ACTIVE_FAILURE_STATES)
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
            )
            saved = True
        except OSError, ValueError, subprocess.SubprocessError:
            _LOG.exception("振り返りの失敗報告を保存できない。WI保存先を直してから次回の起動で再試行する: %s", failure_key)
            saved = False
    if saved:
        session_review.failure_reported(failure_key, reported=True)
    return saved


def _run_meta(args: argparse.Namespace, execute: Callable[[argparse.Namespace], int], *, manual: bool) -> int:
    if not manual and not _meta_auto_start_due(now=time.time()):
        session_review.launch_result()
        return 0
    start = session_review.directory() / "meta-start.json"
    if start.is_file():
        previous = session_review.read(start)
        if previous.get("report_synced") is False:
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
        return execute(args)
    work_dir = getattr(args, "work_dir", None)
    if manual:
        work_dir = (
            pathlib.Path(os.path.expanduser(work_dir))
            if work_dir is not None
            else repo.resolve_local_worktree(args.target_repo)
        )
        if not work_dir.is_absolute() or not work_dir.is_dir():
            outcome.report_failure(
                "メタ分析の実行cwdには実在する絶対ディレクトリが必要",
                next_action="--work-dirに実在する絶対ディレクトリを指定するか、Git作業ツリー内から起動する",
            )
            return 2
    now = datetime.datetime.now(datetime.UTC)
    record: dict[str, Any] = {"started_at": now.isoformat()}
    session_review.write(start, record)
    session_review.launch_result()
    stage = "worktree"
    try:
        if work_dir is None:
            source = repo.resolve_repo_id_or_raise(_plugin_repository())
            registered = config_settings.repository_settings(check_paths=False).get(source)
            if registered is None or not (work_dir := pathlib.Path(registered["path"])).is_dir():
                raise ValueError(f"メタ分析のGit作業ツリーを `atk config repos` で登録する: {source}")
        stage = "queue"
        args.target_repo = _meta_report_target(now=now)
        args.work_dir = str(work_dir)
        stage = "execution"
        code = execute(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        _LOG.error("メタ分析を完了できなかった: %s", redact_secrets(str(error)))
        code = 1
    if not code:
        return code
    stage = (getattr(args, "batch_failure", None) or "").partition(":")[0] or f"{stage}-{code}"
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
    session_review.write(start, record)
    _save_meta_failure(start, record)
    return code


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
        # 保存済みの実行プロセスの世代だけで判定し、ホストの全プロセスの起動argvは読まない。
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
    """この版で作成した準備資料だけを、実行主体が存命の間は既存の掃引から保護する。"""
    token = managed_temp.owner_token(executor)
    for prepared in manifest["prepared"]:
        if not (value := prepared.get("work_dir")):
            continue
        path = pathlib.Path(value)
        dedicated = path.parent == managed_temp.temp_root_path() and path.name.startswith(f"{_PREPARE_TEMP_PREFIX}-")
        # 旧prepareの親は元記録のstemから決まり、待ちのセッションIDと異なる場合がある。
        legacy = path.parent.parent == managed_temp.temp_root_path() and path.name.startswith("session-review-")
        if not dedicated and not legacy:
            continue
        try:
            scope = managed_temp.contained_target(path / "bundle/scope.json")
            if not scope.is_file():
                raise ValueError(f"準備資料が見つからない: {path}。元の記録を確かめ、新しいbatchとして準備し直してください")
            # 旧領域はwork-dirの親が掃引単位なので、実行者の表示もその直下に置く。
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
    # hookが発行する起動識別子で区別し、着眼点のauto/manualを制御情報として消費しない。
    manual = not bool(os.environ.get(session_review.LAUNCH_ENV))
    if not manual and config.resolve_mutable_setting("session_review_auto_start") == "false":
        session_review.launch_result()
        return 0
    root = session_review.directory()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.FileLock(str(root / "batch.lock"), timeout=0):
            if skill_modes.invocation_scope(args.skill, args.args) == session_review.META_SCOPE:
                return _run_meta(args, execute, manual=manual)
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
    try:
        report_synced = add.has_synced_run_report(
            private_notes.default_private_notes(), run_id=manifest["run_id"], scope=session_review.BATCH_SCOPE
        )
        session_review.BatchWriter(path).complete(report_synced=report_synced)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        reason = f"finish: {error}"
        _failure(manifest, path, reason)
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
        if manifest["status"] == session_review.BatchStatus.USAGE_LIMIT_PAUSED:
            args.resume_usage_limited = True
            session_review.BatchWriter(path).resume_usage_limited()
        _pin_prepared(manifest)
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
                # 処理範囲と欠測の診断は、抽出物を回収した後もmanifestと同じ寿命で保持する。
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
                    # 通常対象を準備できない回も、batch自身だけの分析を起動しない。
                    writer.resolve()
                    continue
                # 報告先は実際にモデルへ渡す対象から決め直す。
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
