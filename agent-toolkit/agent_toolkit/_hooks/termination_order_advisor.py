r"""成果作業で完了報告の手順書（入口`atk-mode`の`playbooks/report-completion.md`）の読取を忘れた終了をStopフックで検出する。

成果作業（入口`atk-mode`の作業`process-wi`と`add-awi-by-user`の開始、リポジトリへの自編集、
計画の保存、WIの保存、commit・pushの実行）を行った作業で、最後の会話圧縮以降の読込証拠が無く、
有効なプロジェクト規範による省略も無い場合は、手順書の読取と残工程への続行を一度通知する。
読込証拠は、主体別の共通取得記録にある手順書の取得（Claude Codeの`Read`、Codexの読取）と、
Claude Codeの入口の起動（引数の先頭語`report-completion`）の記録とする。
受理する証拠は`skills/atk-mode/references/termination-evidence.md`「受理する証拠」が定める。
報告本文の見出し・区切り・区分・候補IDは判定に使わない。
工程の完遂と報告の送達・内容は、完了報告の手順書に従ってメインが元要求と実際の結果から判断する。
CodexのStopは非遮断通知を配送できないため対象外とし、手順書の要否も同じ文書で判断する。

中止・置換・技術的不成立と記録した作業と、確認の回答や委譲先の結果を待つ作業は遮断しない。
成果作業が待つ非同期対象が生存している場合も遮断しない。待機対象は作業が起動したagents_serverのsessionと、
作業の開始以後に起動した背景Bash・背景Agent・MCPのバックグラウンドタスク・未配送の完了通知とする。
有限の背景Bashは終了すると完了通知でターンが再開し、その時点で改めて判定する。
既知のサーバー・watchの常駐起動は、完了通知を待てないため待機対象から外す。
作業の開始より前から動く無関係なタスクは待機として扱わない。
証拠やセッション記録（transcript）を読み取れない場合は遮断せず、Stop判定ログへ確かめられないことを記録する。

委譲先での実行可否: 委譲先は完了報告の手順書の適用対象ではないため、hook入力と環境印で除外する。
"""

import json
import pathlib

from agent_toolkit._hooks.notice import _WARN_TAG, resolve_notice, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common import read_triggers, session_state, transcript
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_atk_command
lazy from agent_toolkit._hooks import document_reads, rules_context, termination_evidence
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context
lazy from agent_toolkit._hooks.background_tasks import async_launch_offsets, pending_async_task_ids
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session

_HOOK_ID = "termination_order_advisor"

_notice = _notice_formatter(_HOOK_ID, default_tag=_WARN_TAG)


def _resident_bash_ids(transcript_path: str) -> set[str]:
    """終了通知を待てないサーバー・watchの起動IDと、その応答のタスクIDを返す。"""
    identifiers: set[str] = set()
    for line in transcript.read_transcript_lines(transcript_path) or []:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") in ATK_SHELL_TOOLS:
                tool_input = block.get("input")
                command = tool_input.get("command") if isinstance(tool_input, dict) else None
                identifier = block.get("id")
                if isinstance(identifier, str) and isinstance(command, str) and _is_resident_command(command):
                    identifiers.add(identifier)
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in identifiers:
                response = entry.get("toolUseResult")
                task_id = response.get("backgroundTaskId") if isinstance(response, dict) else None
                if isinstance(task_id, str):
                    identifiers.add(task_id)
    return identifiers


def _is_resident_command(command: str) -> bool:
    """実行位置にある既知の常駐コマンドを判別し、引用や有限のtimeoutを除く。"""
    for segment in extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        tokens = segment.tokens
        name = pathlib.PurePath(tokens[0]).name
        if pathlib.PurePath(segment.raw_tokens[0]).name == "timeout":
            continue
        if name in {"uv", "uvx"} and "run" in tokens:
            tokens = tokens[tokens.index("run") + 1 :]
            if not tokens:
                continue
            name = pathlib.PurePath(tokens[0]).name
        if any(arg in {"--help", "--version"} for arg in tokens[1:]):
            continue
        script = tokens[2:3] if tokens[1:2] == ("run",) else tokens[1:2]
        if (
            name in {"uvicorn", "gunicorn", "watch", "nodemon", "http.server", "vite"}
            or is_atk_command(tokens)
            and tokens[1:2] == ("serve",)
            or name in {"npm", "pnpm", "yarn", "bun"}
            and bool(script)
            and script[0] in {"watch", "dev", "start"}
            or name in {"node", "tsc", "webpack", "esbuild", "cargo", "dotnet", "deno"}
            and "--watch" in tokens[1:]
        ):
            return True
    return False


def _work_order(work_id: str, work: dict) -> tuple[int, int]:
    """作業を開始位置と記録順で並べる鍵を返す。"""
    offset = work.get("offset")
    sequence = work_id.removeprefix("work-")
    return (
        offset if isinstance(offset, int) and not isinstance(offset, bool) else 0,
        int(sequence) if sequence.isdigit() else 0,
    )


def _waiting_work_ids(payload: dict, session_id: str, transcript_path: str, work_ids: set[str]) -> set[str]:
    """`work_ids`のうち、その作業が待つ非同期対象が生存している作業の識別子を返す。

    agents_serverのsessionは作業の起動記録で対応付ける。バックグラウンドタスクと未配送の完了通知は、起動を記録した
    transcriptの位置より前に開始した作業のうち最も新しいものへ対応付ける。
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
    live -= _resident_bash_ids(transcript_path)
    ordered = sorted(works, key=lambda item: _work_order(*item))
    for identifier in live:
        launched_at = offsets.get(identifier)
        if launched_at is None:
            continue
        owners = [work_id for work_id, work in ordered if _work_order(work_id, work)[0] <= launched_at]
        if owners and owners[-1] in work_ids:
            waiting.add(owners[-1])
    return waiting


def evaluate(payload_text: str) -> tuple[str, str]:
    """完了報告の手順書の読込証拠が不足する場合に支援通知を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    set_warning_session_id(session_id)

    if is_codex_payload(payload):
        append_stop_log(session_id, "approve_codex_document_guidance", {})
        return "approve", ""
    if not is_main_agent_context(payload):
        append_stop_log(session_id, "approve_delegated_session", {})
        return "approve", ""
    if not termination_evidence.evidence_available(payload):
        return "approve", ""

    pending = dict(termination_evidence.pending_work(payload))
    records = session_state.read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    playbook = termination_evidence.completion_playbook_path()
    loaded = playbook.is_file() and (
        document_reads.acquisition_state(records, read_triggers.ReadTarget(playbook), "main") != "missing"
        or "main"
        in records.get(document_reads.skill_invocation_key(playbook, document_reads.content_fingerprint(playbook)), [])
    )
    missing = {
        work_id: kinds
        for work_id, work in pending.items()
        if (kinds := termination_evidence.missing_completion(work, context_loaded=loaded))
    }
    if not missing:
        resolve_notice(session_id, _HOOK_ID, "evaluate", subject="main")
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
        append_stop_log(session_id, "approve_completion_report_satisfied", {"waiting_count": len(waiting)})
        return "approve", ""

    works = dict(termination_evidence.session_works(payload))
    lines = [f"作業 {work_id}: {'、'.join(kinds)}" for work_id, kinds in remaining.items()]
    for work_id in remaining:
        if edits := termination_evidence.unscoped_self_edits(payload, works.get(work_id, {})):
            lines.append(f"作業 {work_id}の公開範囲が未確定の未コミットの編集: {', '.join(edits)}")
    append_stop_log(session_id, "notify_completion_report_missing", {"count": len(remaining)})
    body = _notice(
        "成果作業を行ったが、最後の会話圧縮以降に完了報告の手順書を読み込んだ証拠を確認できない。\n"
        + "\n".join(lines)
        + "\n"
        + termination_evidence.decision_hint(payload),
        fix=(
            f"完了報告の手順書`{playbook}`を全文読み、終了工程を進めよ。"
            f"受理する証拠は{termination_evidence.termination_evidence_reference()}「受理する証拠」、"
            "読取の省略と終了方法の判断は同じ資料の「終了方法の分岐」に従え。"
        ),
        removable_cause=True,
    )
    return ("notify", body) if body else ("approve", "")
