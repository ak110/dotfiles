r"""成果作業で`agent-toolkit:completion-report`の起動を忘れた終了をStopフックで検出する。

成果作業（成功した`agent-toolkit:process-wi`・`agent-toolkit:add-awi-by-user`の開始、リポジトリへの自編集、
計画の保存、WIの保存、commit・pushの実行）を行った作業で、同じ作業の開始以後の起動証拠が無く、
有効なプロジェクト規範による省略も無い場合は、初回のStopから遮断し、起動と残工程への続行を案内する。
起動証拠はClaude Codeでは成功したSkill呼び出し、Codexでは実際の入力と結果が示す`SKILL.md`の読取とし、
`termination_evidence`が観測した記録だけを使う。報告本文の見出し・区切り・区分・候補IDは判定に使わない。
起動済みの成果作業は、最終報告を届けた場合を除き、同じ作業・人間入力につき一度だけ終了判断の点検へ戻す。
工程の完遂と報告の送達・内容は、メインが元要求と実際の結果から判断する。

中止・置換・技術的不成立と記録した作業と、確認の回答や委譲先の結果を待つ作業は遮断しない。
成果作業が待つ非同期対象が生存している場合も遮断しない。待機対象は作業が起動したagents_serverのsessionと、
作業の開始以後に起動した背景Bash・背景Agent・MCPのバックグラウンドタスク・未配送の完了通知とする。
有限の背景Bashは終了すると完了通知でターンが再開し、その時点で改めて判定する。
既知のサーバー・watchの常駐起動は、完了通知を待てないため待機対象から外す。
作業の開始より前から動く無関係なタスクは待機として扱わない。
証拠やセッション記録（transcript）を読み取れない場合は遮断せず、Stop判定ログへ確かめられないことを記録する。

委譲先での実行可否: 委譲先はcompletion-reportの適用対象ではないため、hook入力と環境印で除外する。
"""

import json
import pathlib

from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._atk import run_script as _run_script
lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._common.session_state import read_state
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_atk_command
lazy from agent_toolkit._hooks import termination_evidence
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context
lazy from agent_toolkit._hooks.background_tasks import async_launch_offsets, pending_async_task_ids
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session

_HOOK_ID = "termination_order_advisor"

# 固定文の定義元: skills/completion-report/SKILL.mdの最終報告。
_FINAL_REPORT_ENDING = "以上で、このセッションの作業は全て終わりました"

_block_notice = _block_notice_formatter(_HOOK_ID)


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


def _final_report_work_ids(payload: dict, pending: dict, work_ids: set[str]) -> set[str]:
    """作業と最新の人間入力の後の最後の可視本文が最終報告である作業を返す。"""
    if is_codex_payload(payload):
        text = payload.get("last_assistant_message")
        return work_ids if isinstance(text, str) and text.rstrip().rstrip("。").endswith(_FINAL_REPORT_ENDING) else set()
    data = read_state(payload["session_id"]).get(termination_evidence.STATE_KEY, {})
    reference = data.get("inputs", {}).get(data.get("last_human_input"), {})
    input_offset = reference.get("offset", 0)
    finished: set[str] = set()
    for work_id in work_ids:
        offset = max(pending[work_id].get("offset", 0), input_offset)
        raw = transcript.read_transcript_bytes(payload.get("transcript_path", ""), offset=offset)
        if raw is None:
            continue
        last = ""
        for line in raw.decode("utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or entry.get("isSidechain") or entry.get("type") != "assistant":
                continue
            text = transcript.visible_assistant_text(entry.get("message"))
            # send_to_user後にClaudeのターンを終える`…`は報告本文へ数えない。
            if text.strip().strip("…").strip():
                last = text
        if last.rstrip().rstrip("。").endswith(_FINAL_REPORT_ENDING):
            finished.add(work_id)
    return finished


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

    pending = dict(termination_evidence.pending_work(payload))
    missing = {work_id: kinds for work_id, work in pending.items() if (kinds := termination_evidence.missing_completion(work))}
    review_ids = {work_id for work_id, work in pending.items() if work.get("deliverables") and work.get("completion_report")}
    if not missing and not review_ids:
        append_stop_log(session_id, "approve_completion_report_satisfied", {})
        return "approve", ""

    raw_path = payload.get("transcript_path", "")
    transcript_path = raw_path if isinstance(raw_path, str) else ""
    if transcript_path and not pathlib.Path(transcript_path).is_file():
        append_stop_log(session_id, "approve_transcript_unreadable", {"reason": "作業の待機対象を確認できない"})
        return "approve", ""
    waiting = _waiting_work_ids(payload, session_id, transcript_path, set(missing) | review_ids)
    remaining = {work_id: kinds for work_id, kinds in missing.items() if work_id not in waiting}
    if not remaining:
        final_reports = _final_report_work_ids(payload, pending, review_ids - waiting)
        if final_reports:
            append_stop_log(session_id, "approve_final_report_delivered", {"work_ids": ",".join(sorted(final_reports))})
        candidates = review_ids - waiting - final_reports
        review = termination_evidence.claim_stop_review(payload, candidates) if candidates else []
        if review:
            return "block", _block_notice(
                "起動済みの完了処理について、終了時点の送達と残工程を点検する。対象: " + "、".join(review),
                fix=(
                    "元要求と実際の送達・処置結果から、必要な結果・未達・残工程がユーザーへ届いたか、"
                    "新たな指摘で判明した問題に必要な処置が残っているかを判断する。"
                    "内部の評価や報告用UWIの起草だけを保存検収済みの送達とせず、AWI登録と実装完了を区別する。"
                    "未送達や残工程があれば`agent-toolkit:completion-report`の残る工程へ戻る。"
                    "新しい是正事項は`agent-toolkit:session-review`の工程6と`agent-toolkit:bugfix`へ接続し、"
                    "公開結果の補足後に判明した公開不良も必要な分析・処置を進める。"
                    "充足済みなら報告本文を再出力せず終了する。同じ作業・人間入力にはこの点検通知を繰り返さない。"
                ),
            )
        append_stop_log(session_id, "approve_completion_report_satisfied", {"waiting_count": len(waiting)})
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
            "適用するプロジェクト規範で起動を省く場合は、"
            f"`{_run_script.PLUGIN_ROOT / 'skills/completion-report/references/termination-evidence.md'}`を直接読み、"
            "`omit-completion`を記録して通常の報告を届ける。"
            f"起動が必要な作業を完了して終える場合は{operation}、同スキルの工程の残り（作業完了報告、振り返り、最終報告、"
            "呼び出し元が定める終端工程）へ進む。"
            "ユーザーの中止・置換、確認の回答や委譲先の結果の待機、技術的不成立で終える場合は、"
            "原証拠と対象の`work_id`を`atk run-script termination-evidence -- --decision-file <判断JSONの絶対パス>`へ渡す。"
        ),
    )
