r"""成果作業で`agent-toolkit:completion-report`の起動を忘れた終了をStopフックで検出する。

成果作業（成功した`agent-toolkit:process-wi`・`agent-toolkit:add-awi-by-user`の開始、リポジトリへの自編集、
計画の保存、WIの保存、commit・pushの実行）を行った作業に、同じ作業の開始以後の起動証拠が無ければ、
初回Stopからcompletion-reportの起動と残工程への続行を案内して遮断する。
起動証拠はClaude Codeでは成功したSkill呼び出し、Codexでは実際の入力と結果が示す`SKILL.md`の読取とし、
`termination_evidence`が観測した記録だけを使う。報告本文の見出し・区切り・区分・候補IDは判定に使わない。
起動後の工程の完遂と報告の内容はcompletion-reportの規範が扱い、本フックは確かめない。

中止・置換・技術的不成立と記録した作業と、確認の回答や委譲先の結果を待つ作業は遮断しない。
成果作業が待つ非同期対象が生存している場合も遮断しない。待機対象は作業が起動したagents_serverのsessionと、
作業の開始以後に起動した結果待ちの背景Bash（`atk agents wait`・`wait_ci.py`）・背景Agent・
MCPのバックグラウンドタスク・未配送の完了通知とする。作業の開始より前から動く無関係なタスクと、
作業内で起動した待機コマンドでない背景Bash（開発サーバーなどの常駐コマンド）は待機として扱わない。
証拠やセッション記録（transcript）を読み取れない場合は遮断せず、Stop判定ログへ確かめられないことを記録する。

委譲先での実行可否: 委譲先はcompletion-reportの適用対象ではないため、hook入力と環境印で除外する。
"""

# 関数の実行時までだけ使う依存を、Python 3.15では使用時まで読み込まない。旧Pythonでは通常のimportとなる。
__lazy_modules__ = {
    "agent_toolkit._atk",
    "agent_toolkit._common.shell_segments",
    "agent_toolkit._common.shell_tokens",
    "agent_toolkit._hooks",
    "agent_toolkit._hooks.agent_id",
    "agent_toolkit._hooks.background_tasks",
    "agent_toolkit._hooks.host",
    "agent_toolkit._hooks.stop_session",
    "agent_toolkit._hooks.transcript_scan",
}

import pathlib

from agent_toolkit._atk import run_script as _run_script
from agent_toolkit._common.shell_segments import extract_execution_segments
from agent_toolkit._common.shell_tokens import is_agents_wait_command
from agent_toolkit._hooks import termination_evidence
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.background_tasks import async_launch_offsets, pending_async_task_ids
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter
from agent_toolkit._hooks.stop_session import append_stop_log
from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session
from agent_toolkit._hooks.transcript_scan import entry_in_scan_scope, iter_assistant_blocks, read_transcript_entries_cached

_HOOK_ID = "termination_order_advisor"

_block_notice = _block_notice_formatter(_HOOK_ID)


def _work_order(work_id: str, work: dict) -> tuple[int, int]:
    """作業を開始位置と記録順で並べる鍵を返す。"""
    offset = work.get("offset")
    sequence = work_id.removeprefix("work-")
    return (
        offset if isinstance(offset, int) and not isinstance(offset, bool) else 0,
        int(sequence) if sequence.isdigit() else 0,
    )


def _is_wait_command(command: str) -> bool:
    """Bashのコマンドが、結果を待つ公開の待機コマンド（`atk agents wait`・`wait_ci.py`）を含むかを返す。"""
    return any(
        segment.resolved
        and (
            is_agents_wait_command(segment.tokens)
            or any(pathlib.PurePath(token.replace("\\", "/")).name == "wait_ci.py" for token in segment.tokens)
        )
        for segment in extract_execution_segments(command)
    )


def _non_wait_bash_ids(entries: list[dict]) -> set[str]:
    """待機コマンドを含まないBash起動の`tool_use_id`と、その起動結果の`backgroundTaskId`を返す。

    常駐コマンドは終了しないため完了通知による再開が来ず、その生存を作業の待機として扱うと起動不足が残る。
    """
    identifiers: set[str] = set()
    for block in iter_assistant_blocks(entries):
        if block.get("type") != "tool_use" or block.get("name") != "Bash":
            continue
        tool_input = block.get("input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        tool_use_id = block.get("id")
        if isinstance(tool_use_id, str) and not (isinstance(command, str) and _is_wait_command(command)):
            identifiers.add(tool_use_id)
    for entry in entries:
        if entry.get("type") != "user" or not entry_in_scan_scope(entry, include_sidechain=False):
            continue
        tool_use_result = entry.get("toolUseResult")
        task_id = tool_use_result.get("backgroundTaskId") if isinstance(tool_use_result, dict) else None
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(task_id, str) or not isinstance(content, list):
            continue
        if any(isinstance(block, dict) and block.get("tool_use_id") in identifiers for block in content):
            identifiers.add(task_id)
    return identifiers


def _waiting_work_ids(payload: dict, session_id: str, transcript_path: str, work_ids: set[str]) -> set[str]:
    """`work_ids`のうち、その作業が待つ非同期対象が生存している作業の識別子を返す。

    agents_serverのsessionは作業の起動記録で対応付ける。バックグラウンドタスクと未配送の完了通知は、起動を記録した
    transcriptの位置より前に開始した作業のうち最も新しいものへ対応付ける。待機コマンドを含まないBashの起動は対応付けない。
    """
    works = termination_evidence.session_works(payload)
    waiting = {
        work_id
        for work_id, work in works
        if work_id in work_ids and termination_evidence.waits_on_delegated_session(payload, work)
    }
    if waiting == work_ids or not transcript_path:
        return waiting
    live = pending_async_task_ids(transcript_path, session_id, background_tasks=payload.get("background_tasks"))
    if not live:
        return waiting
    offsets = async_launch_offsets(transcript_path)
    resident = _non_wait_bash_ids(read_transcript_entries_cached(transcript_path))
    ordered = sorted(works, key=lambda item: _work_order(*item))
    for identifier in live - resident:
        launched_at = offsets.get(identifier)
        if launched_at is None:
            continue
        owners = [work_id for work_id, work in ordered if _work_order(work_id, work)[0] <= launched_at]
        if owners and owners[-1] in work_ids:
            waiting.add(owners[-1])
    return waiting


def evaluate(payload_text: str) -> tuple[str, str]:
    """completion-reportの起動証拠の判定結果と、遮断する場合の理由を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved

    if not is_main_agent_context(payload):
        append_stop_log(session_id, "approve_delegated_session", {})
        return "approve", ""
    if not termination_evidence.evidence_available(payload):
        return "approve", ""

    missing = {
        work_id: kinds
        for work_id, work in termination_evidence.pending_work(payload)
        if (kinds := termination_evidence.missing_completion(work))
    }
    if not missing:
        append_stop_log(session_id, "approve_completion_report_satisfied", {})
        return "approve", ""

    raw_path = payload.get("transcript_path", "")
    transcript_path = raw_path if isinstance(raw_path, str) else ""
    if transcript_path and not pathlib.Path(transcript_path).is_file():
        append_stop_log(session_id, "approve_transcript_unreadable", {"reason": "作業の待機対象を確認できない"})
        return "approve", ""
    waiting = _waiting_work_ids(payload, session_id, transcript_path, set(missing))
    remaining = {work_id: kinds for work_id, kinds in missing.items() if work_id not in waiting}
    if not remaining:
        append_stop_log(session_id, "approve_pending_async_for_work", {"count": len(waiting)})
        return "approve", ""

    works = dict(termination_evidence.session_works(payload))
    lines = [f"作業 {work_id}: {'、'.join(kinds)}" for work_id, kinds in remaining.items()]
    for work_id in remaining:
        if edits := termination_evidence.unscoped_self_edits(payload, works.get(work_id, {})):
            lines.append(f"作業 {work_id}の公開範囲が未確定の未コミットの編集: {', '.join(edits)}")
    operation = (
        f"`{_run_script.PLUGIN_ROOT / 'skills/completion-report/SKILL.md'}`を読み"
        if is_codex_payload(payload)
        else "Skillで`agent-toolkit:completion-report`を起動し"
    )
    append_stop_log(session_id, "block_completion_report_missing", {"count": len(remaining)})
    return "block", _block_notice(
        "成果作業を行ったが、同じ作業で`agent-toolkit:completion-report`の起動を確認できない。\n"
        + "\n".join(lines)
        + "\n"
        + termination_evidence.decision_hint(payload),
        fix=(
            f"作業を完了して終える場合は{operation}、同スキルの工程の残り（作業完了報告、振り返り、最終報告、"
            "呼び出し元が定める終端工程）へ進む。"
            "ユーザーの中止・置換、確認の回答や委譲先の結果の待機、技術的不成立で終える場合は、"
            "原証拠と対象の`work_id`を`atk run-script termination-evidence -- --decision-file <判断JSONの絶対パス>`へ渡す。"
        ),
    )
