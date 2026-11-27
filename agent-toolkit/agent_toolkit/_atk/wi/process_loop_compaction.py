"""Claude統括の対話を起動する前に、同じ会話を圧縮する。  pyfltr: allow-comment"""

import json
import pathlib
import subprocess
from collections.abc import Sequence

import psutil

from agent_toolkit._atk.wi import process_loop_env, process_loop_log
from agent_toolkit._common import (
    host_homes,
    next_action,
    process_tree,
    session_activity,
    session_record_paths,
    session_registry,
)

# 予約を変更するコマンドのロック待ち上限より短くする。  # pyfltr: allow-comment
TIMEOUT = 60


class ResumeBlocked(next_action.ActionableError):
    pass


class CompactionFailed(next_action.ActionableError):
    pass


def _new_boundary_mode(path: pathlib.Path, offset: int) -> str:
    mode = ""
    seen_message = False
    with path.open("rb") as stream:
        if stream.seek(0, 2) < offset:
            return ""
        stream.seek(offset)
        for line in stream:
            try:
                item = json.loads(line)
            except ValueError, UnicodeError:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "system" and item.get("subtype") == "compact_boundary":
                mode, seen_message = "summary", False
            elif mode and not seen_message and item.get("type") in {"user", "assistant"}:
                message = item.get("message")
                seen_message = True
                if isinstance(message, dict):
                    content = json.dumps(message.get("content"), ensure_ascii=False)
                    if "compact-conversation" in content and "圧縮前のやり取りと要約は現在の文脈にない" in content:
                        mode = "original-record"
    return mode


def compact_before_resume(
    session_id: str, argv: Sequence[str], env: dict[str, str], cwd: pathlib.Path, diagnostic: pathlib.Path
) -> None:
    retry = (
        f"対象の会話を閉じて終了を確認し、稼働記録は削除せず、診断ログ {diagnostic} と "
        f"{process_loop_log.log_path()} を確認する。"
        f"`atk wi process-loop resume {session_id}` で予約し、停止中なら `atk wi process-loop` を起動する"
    )
    config_dir = host_homes.claude_config_dir(env)
    failure: type[next_action.ActionableError] = ResumeBlocked
    try:
        resolution = session_registry.resolve(session_id)
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ValueError(f"登録簿を読めません: {session_registry.registry_directory() / (session_id + '.json')}")
        if resolution.state is session_registry.Resolution.RUNNING or resolution.resume_pending:
            raise ValueError(f"登録簿で稼働中です: {session_registry.registry_directory() / (session_id + '.json')}")
        if resolution.state is session_registry.Resolution.MISSING and (
            blocker := session_activity.claude_native_resume_blocker(session_id, config_dir)
        ):
            raise ValueError(blocker)
        failure = CompactionFailed
        offsets = {
            path: path.stat().st_size for path in session_record_paths.find_claude_records(session_id, claude_home=config_dir)
        }
        with diagnostic.open("ab") as output:
            result = process_tree.run_with_timeout(
                [*argv, "-p", "--resume", session_id, "/compact"],
                env=process_loop_env.compaction_env(env),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=TIMEOUT,
                check=False,
            )
        if result.returncode != 0:
            raise ValueError(f"/compact の終了コード: {result.returncode}")
        modes = [
            mode
            for path in session_record_paths.find_claude_records(session_id, claude_home=config_dir)
            if (mode := _new_boundary_mode(path, offsets.get(path, 0)))
        ]
        if len(modes) != 1:
            raise ValueError("新しい compact_boundary を対象の記録で一意に確認できません")
    except (OSError, UnicodeError, ValueError, psutil.Error, subprocess.TimeoutExpired) as error:
        detail = f"{type(error).__name__}（上限 {TIMEOUT} 秒）" if isinstance(error, subprocess.TimeoutExpired) else str(error)
        process_loop_log.append(
            "session_compaction_failed", session_id=session_id, diagnostic=diagnostic, detail=detail, retry=retry
        )
        action = "再開を中止" if failure is ResumeBlocked else "圧縮なしで再開可能"
        raise failure(f"会話 {session_id} の起動前圧縮に失敗（{action}）: {detail}", next_action=retry) from error
    process_loop_log.append("session_compacted", session_id=session_id, mode=modes[0])
