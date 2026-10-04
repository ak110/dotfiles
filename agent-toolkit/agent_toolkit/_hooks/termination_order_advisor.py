r"""多段終了手順の起動順が要求を満たすかStopフックで確かめる。

`agent-toolkit:process-wi`と`agent-toolkit:add-awi-by-user`は、
本体の作業を終える際に固定の終了工程列（`agent-toolkit:completion-report`、
`agent-toolkit:process-wi`だけはこれに続けて`atk agents-exit-session`）を順に実行する契約を持つ。
本フックは対象スキルの最新の起動以後に、要求される終了工程が要求順で実行されたかを
transcriptのSkillの成功結果とBashツール起動記録から判定する。

実際の報告の可視発話・振り返り準備の結果が供給された作業は、初回Stopから不足を判定する。
対象スキルの起動が無く、工程証拠も無いセッションではapproveする。
最新の対象スキル起動より前の終了工程の起動は充足の判定へ流用しない。
終了工程の起動順が要求と逆である場合も未充足として扱う。

Claude Codeの旧版の起動順判定は、工程結果が未供給の場合の再入回へ維持する。
CodexではClaude Code形式のSkill記録を根拠へ使わない。

報告段階が残る作業は、その作業が待つ非同期対象が生存している場合だけ遮断しない。
待機対象は作業が起動したagents_serverのsessionと、作業の開始以後に起動した結果待ちの背景Bash（`atk agents wait`・
`wait_ci.py`）・背景Agent・MCPのバックグラウンドタスク・未配送の完了通知とする。作業の開始より前から動く無関係なタスクと、
作業内で起動した待機コマンドでない背景Bash（開発サーバーなどの常駐コマンド）は報告不足を免除しない。
報告段階の不足が無い場合は`is_pending_async_work`の判定を維持し、継続中の非同期作業があれば起動順を判定しない。
現在の作業を中止・置換・技術的不成立と記録しても、他の作業に残る報告段階は判定する。
セッション記録（transcript）を読み取れない場合は遮断せず、Stop判定ログへ確かめられないことを記録する。

委譲先での実行可否: 委譲先は最上位セッションが起動する終了手順の起動順を確かめる対象ではないため、hook入力と環境印で除外する。
"""

import json
import pathlib

from agent_toolkit._common.shell_tokens import is_agents_exit_session_command, is_agents_wait_command
from agent_toolkit._hooks import termination_evidence
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.bash_command_parser import extract_execution_segments
from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter
from agent_toolkit._hooks.stop_gate import (
    _entry_in_scan_scope,  # noqa: E402  # pylint: disable=protected-access
    _iter_assistant_blocks,  # noqa: E402  # pylint: disable=protected-access
    append_stop_log,
    async_launch_offsets,
    is_pending_async_work,
    pending_async_task_ids,
    read_transcript_entries_cached,
)
from agent_toolkit._hooks.stop_gate import parse_stop_session as _parse_stop_session
from agent_toolkit._hooks.tool_input import is_codex_payload

_HOOK_ID = "termination_order_advisor"

# 表示用の代表名（`agent-toolkit:`修飾つき）と、プレフィックス付き・素の両表記を受理する名前集合の対。
# 代表名はアルファベット順による自動選出ではなく明示指定とする
# （`add-awi-by-user`は`agent-toolkit:add-awi-by-user`より辞書順で先に位置するため、
# 自動選出では修飾を除いた表記を選んでしまう）。
_PROCESS_WI = ("agent-toolkit:process-wi", frozenset({"agent-toolkit:process-wi", "process-wi"}))
_ADD_AWI_BY_USER = (
    "agent-toolkit:add-awi-by-user",
    frozenset({"agent-toolkit:add-awi-by-user", "add-awi-by-user"}),
)
_COMPLETION_REPORT = (
    "agent-toolkit:completion-report",
    frozenset({"agent-toolkit:completion-report", "completion-report"}),
)
_EXIT_SESSION = ("atk agents-exit-session", frozenset({"atk agents-exit-session"}))

# 起動順を確かめるスキルの(代表名, 名前集合)と、その最新起動以後に要求順で起動される必要がある終了工程の列。
_TERMINATION_SEQUENCES: tuple[tuple[tuple[str, frozenset[str]], tuple[tuple[str, frozenset[str]], ...]], ...] = (
    (_PROCESS_WI, (_COMPLETION_REPORT, _EXIT_SESSION)),
    (_ADD_AWI_BY_USER, (_COMPLETION_REPORT,)),
)

_MISSING_STEP_TEMPLATE = "{target}の終了手順が未完了である。次の順で残りの工程を実行する: {remaining}"

_block_notice = _block_notice_formatter(_HOOK_ID)


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


def _successful_tool_use_ids(entries: list[dict]) -> set[str]:
    """非sidechainのuserエントリから成功したツール起動IDを返す。"""
    successful_ids: set[str] = set()
    for entry in entries:
        if entry.get("type") != "user" or not _entry_in_scan_scope(entry, include_sidechain=False):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if isinstance(tool_use_id, str) and block.get("is_error") is not True:
                successful_ids.add(tool_use_id)
    return successful_ids


def _skill_invocations(entries: list[dict]) -> list[str]:
    """成功したSkillとBash終了工程の起動を時系列順で返す。"""
    successful_ids = _successful_tool_use_ids(entries)
    invocations: list[str] = []
    for block in _iter_assistant_blocks(entries):
        if block.get("type") != "tool_use":
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            continue
        if block.get("name") == "Skill":
            tool_use_id = block.get("id")
            if not isinstance(tool_use_id, str) or tool_use_id not in successful_ids:
                continue
            skill_name = tool_input.get("skill")
            if isinstance(skill_name, str) and skill_name:
                invocations.append(skill_name)
            continue
        if block.get("name") != "Bash":
            continue
        command = tool_input.get("command")
        if not isinstance(command, str):
            continue
        if any(
            segment.resolved and is_agents_exit_session_command(segment.tokens)
            for segment in extract_execution_segments(command)
        ):
            invocations.append("atk agents-exit-session")
    return invocations


def _last_index_matching(invocations: list[str], names: frozenset[str]) -> int | None:
    """`names`のいずれかに一致する最後の起動の位置を返す。一致が無ければ`None`。"""
    for index in range(len(invocations) - 1, -1, -1):
        if invocations[index] in names:
            return index
    return None


def _missing_step_index(
    invocations: list[str],
    target_index: int,
    required_sequence: tuple[tuple[str, frozenset[str]], ...],
) -> int:
    """`target_index`より後で、`required_sequence`を要求順に満たせた個数を返す。

    満たせた個数が`len(required_sequence)`と等しければ全充足を意味する。
    """
    pointer = 0
    for invocation in invocations[target_index + 1 :]:
        if pointer >= len(required_sequence):
            break
        _, names = required_sequence[pointer]
        if invocation in names:
            pointer += 1
    return pointer


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

    常駐コマンドは終了しないため完了通知による再開が来ず、その生存を作業の待機として扱うと報告不足が残る。
    """
    identifiers: set[str] = set()
    for block in _iter_assistant_blocks(entries):
        if block.get("type") != "tool_use" or block.get("name") != "Bash":
            continue
        tool_input = block.get("input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        tool_use_id = block.get("id")
        if isinstance(tool_use_id, str) and not (isinstance(command, str) and _is_wait_command(command)):
            identifiers.add(tool_use_id)
    for entry in entries:
        if entry.get("type") != "user" or not _entry_in_scan_scope(entry, include_sidechain=False):
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
    """終了手順順序の判定結果と、遮断する場合の理由を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved

    if not is_main_agent_context(payload):
        append_stop_log(session_id, "approve_delegated_session", {})
        return "approve", ""

    raw_path = payload.get("transcript_path", "")
    path_for_async = raw_path if isinstance(raw_path, str) else ""
    available = termination_evidence.observe_reports(payload)
    pending = termination_evidence.pending_work(payload) if available else []
    deficient = {
        work_id
        for work_id, work in pending
        if termination_evidence.report_violations(work) or termination_evidence.missing_stages(work)
    }
    if deficient:
        waiting = _waiting_work_ids(payload, session_id, path_for_async, deficient)
        if waiting == deficient:
            append_stop_log(session_id, "approve_pending_async_for_work", {"count": len(waiting)})
            return "approve", ""
        pending = [(work_id, work) for work_id, work in pending if work_id in deficient - waiting]
    elif is_pending_async_work(path_for_async, session_id, background_tasks=payload.get("background_tasks")):
        append_stop_log(session_id, "approve_pending_async", {})
        return "approve", ""

    violations = [
        f"作業 {work_id}: {error}" for work_id, work in pending for error in termination_evidence.report_violations(work)
    ]
    if violations:
        return "block", _block_notice(
            "報告本文の要求を満たしていない。\n" + "\n".join(violations),
            fix="列挙した行の根拠・対策の対応・実際の投入結果を直し、報告を直接発話する。",
        )
    missing_evidence = [
        f"作業 {work_id}: {', '.join(termination_evidence.missing_stages(work))}"
        for work_id, work in pending
        if termination_evidence.missing_stages(work)
    ]
    if missing_evidence:
        append_stop_log(session_id, "block_missing_termination_evidence", {"count": len(missing_evidence)})
        return "block", _block_notice(
            "終了工程の実行結果が不足している。\n"
            + "\n".join(missing_evidence)
            + "\n"
            + termination_evidence.decision_hint(payload),
            fix=(
                "不足する段階の報告を可視の発話本文へ直接書く。"
                "中止・置換・待機・技術的不成立は原証拠と対象の`work_id`を"
                "`atk run-script termination-evidence -- --decision-file <判断JSONの絶対パス>`へ渡す。"
            ),
        )

    if is_codex_payload(payload):
        return "approve", ""

    if payload.get("stop_hook_active") is not True:
        append_stop_log(session_id, "approve_not_reentrant", {})
        return "approve", ""

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if not transcript_path or not pathlib.Path(transcript_path).is_file():
        append_stop_log(session_id, "approve_transcript_unreadable", {"reason": "起動順を確認できない"})
        return "approve", ""

    entries = read_transcript_entries_cached(transcript_path)
    invocations = _skill_invocations(entries)

    missing_bodies: list[str] = []
    for (target_label, target_names), required_sequence in _TERMINATION_SEQUENCES:
        target_index = _last_index_matching(invocations, target_names)
        if target_index is None:
            continue
        pointer = _missing_step_index(invocations, target_index, required_sequence)
        if pointer >= len(required_sequence):
            continue
        remaining = "→".join(label for label, _ in required_sequence[pointer:])
        missing_bodies.append(_MISSING_STEP_TEMPLATE.format(target=target_label, remaining=remaining))

    if not missing_bodies:
        append_stop_log(session_id, "approve_termination_order_satisfied", {})
        return "approve", ""

    append_stop_log(session_id, "block_termination_order", {"count": len(missing_bodies)})
    reason = _block_notice(
        "\n\n".join(missing_bodies),
        fix="列挙した終了工程を指定順で実行してから終了する。",
    )
    return "block", reason


def main(payload_text: str) -> int:
    """終了手順順序の不足・順序違反を検知し再促するエントリポイント。"""
    decision, body = evaluate(payload_text)
    if decision == "block":
        print(json.dumps({"decision": "block", "reason": body}, ensure_ascii=False))
    else:
        _approve()
    return 0
