r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態の記録とplan file書式の判定。

Bash / Write / Edit / MultiEdit / apply_patch / Read / Skill / Agent / Task / AskUserQuestion / agents_server MCPの実行後に
イベントを検出し、セッション状態ファイルに記録する。
PreToolUse、UserPromptSubmitおよびStopフックが参照して判定に使う。
本モジュールは実行後の観測と警告だけを行い、操作を遮断しない。

編集入力は`_hook_tool_input`が共通の操作記録へ正規化する。
Codexから届く正規化済みの入力も同じ操作記録を使う。

検出対象:

1. plan file（`~/.claude/plans/` または
   保存済み計画root `$(atk config get private_notes)/plans/` 配下）書式の判定 (Write / Edit / MultiEdit / apply_patch)
2. write-planスキル呼び出し検出 (Skill)
3. 計画実行系`model_type`の`agents_server` sessionの起動時刻と終了時刻の`_process_loop_log`記録
4. agents_server MCP呼び出しと`atk agents wait`実行後のsession状態記録、開始・再開したsessionの待機対象登録
5. exit-sessionの実行結果に基づく
   `process_wi_skill_invoked`のリセット (Skill)、
   操作の直前に起動を求めるスキルの起動の記録（以後その主体へ起動の案内を出さない。`pretooluse/operation_skills.py`）。
   スキルの入口（`SKILL.md`）の現在の全文を返した読取も、そのスキルの起動として記録する（Read / Bash。`full_reads.py`）。
   Codex・Antigravityはスキルを入口の読取で始めるためである。`agent-basics:bugfix`の起動は検証の失敗後の案内も止める
6. 現在の計画ファイルパス記録 (Write / Edit / MultiEdit / apply_patch、plan file判定時)
   （UserPromptSubmitの命名が計画名の解決に使用）
7. Bashの背景実行、MCP呼び出しの移行通知およびAgent・Taskの背景起動が返した識別子を、
   バックグラウンドタスクの所有記録へ記録 (Bash / MCP / Agent / Task)
   PostToolUseFailure: Bash・PowerShellのバックグラウンドタスク識別子を同じ所有記録へ保存し、その他は変更せず終了
8. PermissionDenied: 承認と実入力の一致を確認するため実際の拒否を記録して終了（再試行は実行しない）
9. この会話が投入したUWIへ新たに付いた回答の通知（全ツール共通）
10. このセッションで作成または編集した計画ファイル（メイン）の絶対パス蓄積
    （編集ツールの操作記録と`create_plan_files.py`または`atk run-script plan-create`のBash標準出力）
11. `AskUserQuestion`の自由記述の回答へ、UserPromptSubmitと同じ現物確認・内容別の注記を返す (AskUserQuestion)
12. ホストの上限を超えて退避した出力の抜粋を、未読と保存先、次の操作を示す本文へ置き換える
    `updatedToolOutput` (Bash / PowerShell。`persisted_output`が判定する)
13. メインが成功した前景Bashでcommit・pushを行った後に、report-completionの起動条件を通知する
14. 一定間隔ごとの進捗のセルフチェック通知（全ツール共通。`progress_self_check`が判定する）
15. Codexが省略した出力へ、省略を回収する操作の通知 (Bash。`codex_truncation`が判定する)
"""

import json
import re
lazy import asyncio
lazy import shlex

from agent_toolkit._common.next_action import recover_from_failure
from agent_toolkit._hooks.bugfix_gate import BUGFIX_SKILL, QUOTED_MATERIAL_RE, mark_started, observe_verification
from agent_toolkit._hooks.notice import _WARN_TAG, begin_delivery, emit_response, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter
lazy from agent_toolkit._atk.agents_exit_session import function_hook_paths
lazy from agent_toolkit._atk.wi import operation_permissions
lazy from agent_toolkit._common import function_hook_requests
lazy from agent_toolkit._common.json_lines import split_lines
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._common.shell_segments import ExecutionSegment, extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_agents_exit_session_command, is_atk_command
lazy from agent_toolkit._common.task_stop_state import consume_completion, target_ids
lazy from agent_toolkit._hooks import agents_server_observations as _agents_server_observations
lazy from agent_toolkit._hooks import background_tasks as _background_tasks
lazy from agent_toolkit._hooks import (
    clock_claims,
    codex_truncation,
    command_timing,
    full_reads,
    review_receipt,
    session_title,
    source_review_receipt,
    termination_evidence,
    work_contract,
)
lazy from agent_toolkit._hooks import persisted_output as _persisted_output
lazy from agent_toolkit._hooks import progress_self_check as _progress_self_check
lazy from agent_toolkit._hooks import tool_input as _hook_tool_input
lazy from agent_toolkit._hooks import uwi_completion as _uwi_completion
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context, resolve_hook_agent_id
lazy from agent_toolkit._hooks.background_tasks_constants import _TASK_STOP_SUCCESS_PREFIX
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.pretooluse import operation_skills
lazy from agent_toolkit._hooks.pretooluse.large_reads import session_record_reference_warning
lazy from agent_toolkit._plan.path_kinds import is_plan_main_file

_HOOK_ID = "posttooluse"

_llm_notice = _notice_formatter(_HOOK_ID)

# modの装着（`CronList`・`atk wait-schedule`・`CronCreate`）を待つ上限。
# modのツール呼び出しにも設定hookが実行されるため、2026年11月18日の観測では約5秒かかった。
# 上限を過ぎた後の装着失敗はmodが次のターンの入力として届ける。
_PERIODIC_RECHECK_WAIT_SECONDS = 20


def _executable_name(token: str) -> str:
    return token.replace("\\", "/").rsplit("/", 1)[-1]


VERIFICATION_NOTICE_BODY = (
    "応答の前に、`agent-toolkit:atk-mode/playbooks/user-utterance.md`"
    "「割り込み・訂正・中止」に従い、現物との比較の要否を判定してください。"
)
"""ユーザーが書いた文の内容を現物で確かめる手順の所在を示す注記の本文。

ユーザーが書いた文はUserPromptSubmitの`prompt`と、`AskUserQuestion`の自由記述の回答としてPostToolUseの
`tool_response`の両方に届くため、両hookがこの1か所の定義を使う。`user_prompt_submit.py`は本モジュールを
importするため、循環importを避けて本モジュールに置く。
比べて確かめる対象は発話ごとに異なるため、対象の列挙と、真偽が行動を変えない事実の裏付けを省く判断を受領側の手順として本文に持たせる。
その列挙をフック側の判定で代替しない。フックの入力は発話本文だけであり、
規則で分類を誤ると、現物との比較を最も要する発話で注記が返らず、欠落にも気付けないためである。
"""

_EXAMPLE_WORDS = ("例えば", "たとえば", "例として", "その手の")
_INVESTIGATION_WORDS = ("確認", "調査", "調べ", "見直", "点検", "洗い出", "探し", "チェック", "レビュー")


def _example_scope_notice(prompt: str, source: str, *, subject: str) -> str | None:
    text = QUOTED_MATERIAL_RE.sub("", prompt)

    if not (any(word in text for word in _EXAMPLE_WORDS) or text.count("とか") >= 2):
        return None
    body = (
        "発話に例示の語がある。応答・具体化の前に、"
        "`agent-toolkit:atk-mode/playbooks/user-utterance.md`"
        "「原文の保持と取り込み」に従い、原文の対象と例示の関係を判断してください。"
        "例示が上位の対象の一部なら、上位の対象を言葉で定め、例示以外の候補を探してから範囲を確定してください。"
    )
    if any(word in text for word in _INVESTIGATION_WORDS):
        body += (
            "調査へ着手する前に、読み取り専用の`explore`へ分担する案と自分で分割取得しながら続ける案を、"
            "起動・初回読込・検収・統合を含む総費用で比べてください。"
        )
    return _notice_formatter(source)(body, tag="notice", subject=subject)


def content_notices(prompt: str, source: str, *, subject: str = "") -> list[str]:
    example = _example_scope_notice(prompt, source, subject=subject or prompt)
    return [] if example is None else [example]


UTTERANCE_UNDERSTANDING_NOTICE_BODY = (
    "調査より先に、`agent-toolkit:atk-mode`「ユーザーの方針」の「発話の理解」を伝える。"
    "送達指定のあるメインでは、作業前の最初の`send_to_user`の`message`に置く。"
    "送達指定がない場合は応答の冒頭に置き、送達に失敗した場合は通常本文で伝えられる。"
)

_ASK_USER_QUESTION_TOOL = "AskUserQuestion"
_FREE_TEXT_ANSWER_PROCEDURE = "自由記述の回答も同節に従ってください。"
"""`AskUserQuestion`の自由記述の回答への注記で、共通の本文に続ける手順。"""
# Claude Codeが選択肢を選ばずに注記だけを残した質問へ入れる`answers`の値（2.1.283の記録で観測）。
_NOTES_ONLY_ANSWER = "(notes only)"

_MULTI_SELECT_SEPARATOR = ", "


def _reset_process_wi_invoked(state: dict) -> dict | None:
    if not state.get("process_wi_skill_invoked", False):
        return None
    state["process_wi_skill_invoked"] = False
    return state


def _response_texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for nested in value.values() for text in _response_texts(nested)]
    if isinstance(value, list):
        return [text for nested in value for text in _response_texts(nested)]
    return []


def _response_has_exit_invocation(value: object) -> bool:
    if isinstance(value, dict) and value.get("exit_session_invoked") is True:
        return True
    for text in _response_texts(value):
        for line in split_lines(text):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("exit_session_invoked") is True:
                return True
    return False


def _record_bash_response_state(session_id: str, command: str, tool_response: object) -> None:
    segments = [segment for segment in extract_execution_segments(command) if segment.resolved and segment.tokens]
    exit_invoked = any(is_agents_exit_session_command(segment.tokens) for segment in segments)
    if exit_invoked and _response_has_exit_invocation(tool_response):
        update_state(session_id, _reset_process_wi_invoked)
    _record_created_plan_file(session_id, segments, tool_response)


_PLAN_CREATION_SCRIPT_NAME = "create_plan_files.py"
_PLAN_CREATION_RUN_SCRIPT_PREFIX = ("run-script", "plan-create")


def _is_plan_creation_invocation(tokens: tuple[str, ...]) -> bool:
    if any(_PLAN_CREATION_SCRIPT_NAME in token for token in tokens):
        return True
    return is_atk_command(tokens) and tokens[1:3] == _PLAN_CREATION_RUN_SCRIPT_PREFIX


def _record_created_plan_file(session_id: str, segments: list[ExecutionSegment], tool_response: object) -> None:
    if not any(_is_plan_creation_invocation(segment.tokens) for segment in segments):
        return
    for text in _response_texts(tool_response):
        for line in text.splitlines():
            candidate = line.strip()
            if candidate and is_plan_main_file(candidate):
                _record_plan_file(session_id, candidate)
                update_state(session_id, _set_plan_mode_invoked)
                return


def _set_plan_mode_invoked(state: dict) -> dict | None:
    if state.get("plan_mode_skill_invoked", False):
        return None
    state["plan_mode_skill_invoked"] = True
    return state


def _parse_hook_payload(payload_text: str) -> tuple[dict, str, str, dict, str, str] | None:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError, ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return None
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if tool_name == "functions.exec" and is_codex_payload(payload) and isinstance(tool_input, str):
        tool_input = {"input": tool_input}
    if not isinstance(tool_input, dict):
        return None
    event_name = payload.get("hook_event_name", "")

    if event_name == "PermissionDenied":
        operation_permissions.observe_denial(payload)
    elif event_name in {"PostToolUse", "PostToolUseFailure"}:
        if event_name == "PostToolUse" and tool_name == _ASK_USER_QUESTION_TOOL:
            operation_permissions.observe_answer(payload)
        operation_permissions.observe_result(payload)
        observe_verification(payload)
    if event_name == "PermissionDenied" or (event_name == "PostToolUseFailure" and tool_name not in ATK_SHELL_TOOLS):
        return None
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    return payload, session_id, tool_name, tool_input, cwd, event_name


_BACKGROUND_TASK_ID_RE = re.compile(r"running in background with ID:\s*([\w-]+)")


def _background_task_id_from_response(value: object) -> str | None:
    if isinstance(value, str):
        match = _BACKGROUND_TASK_ID_RE.search(value)
        return match.group(1) if match is not None else None
    if isinstance(value, dict):
        structured_id = value.get("backgroundTaskId")
        if isinstance(structured_id, str) and structured_id:
            return structured_id
        nested_values: list[object] = list(value.values())
    elif isinstance(value, list):
        nested_values = list(value)
    else:
        return None
    for nested in nested_values:
        task_id = _background_task_id_from_response(nested)
        if task_id is not None:
            return task_id
    return None


def _structured_background_task_id(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    task_id = value.get("backgroundTaskId")
    return task_id if isinstance(task_id, str) and task_id else None


def _record_background_task_id(session_id: str, task_id: str) -> None:
    """自セッションのツール呼び出しが返したバックグラウンドタスクのIDを記録する。

    PreToolUse(TaskStop)が、停止対象が自セッションの起動したバックグラウンドタスクかを判定する入力とする。
    記録の契機は、Bashの背景実行が成功した応答と同じ指定で失敗した応答、実行上限または手動の操作で背景へ移ったBashの
    構造化`backgroundTaskId`、ホストのMCP移行通知およびAgent・Taskの背景起動が返した`agentId`とする。
    所有の根拠は自身の呼び出しが識別子を返したことであり、その呼び出しの成否は問わない。
    """

    def _append(state: dict) -> dict | None:
        recorded = state.get("background_task_ids")
        recorded = list(recorded) if isinstance(recorded, list) else []
        if task_id in recorded:
            return None
        recorded.append(task_id)
        state["background_task_ids"] = recorded
        return state

    update_state(session_id, _append)


def _free_text_answers(tool_response: object) -> list[str]:
    if not isinstance(tool_response, dict) or tool_response.get("afkTimeoutMs") is not None:
        return []
    texts: list[str] = []
    response = tool_response.get("response")
    if isinstance(response, str) and response.strip():
        texts.append(response)
    labels_by_question: dict[str, tuple[set[str], bool]] = {}
    questions = tool_response.get("questions")
    for question in questions if isinstance(questions, list) else ():
        if not isinstance(question, dict) or not isinstance(question.get("question"), str):
            continue
        options = question.get("options")
        labels = {
            option["label"]
            for option in (options if isinstance(options, list) else ())
            if isinstance(option, dict) and isinstance(option.get("label"), str)
        }
        labels_by_question[question["question"]] = (labels, question.get("multiSelect") is True)
    answers = tool_response.get("answers")
    for question_text, answer in (answers if isinstance(answers, dict) else {}).items():
        if not isinstance(answer, str) or not answer.strip() or answer == _NOTES_ONLY_ANSWER:
            continue
        labels, multi_select = labels_by_question.get(question_text, (set(), False))
        parts = answer.split(_MULTI_SELECT_SEPARATOR) if multi_select else [answer]
        free_parts = [part for part in parts if part not in labels and part.strip()]
        if free_parts:
            texts.append(_MULTI_SELECT_SEPARATOR.join(free_parts))
    annotations = tool_response.get("annotations")
    for annotation in (annotations if isinstance(annotations, dict) else {}).values():
        notes = annotation.get("notes") if isinstance(annotation, dict) else None
        if isinstance(notes, str) and notes.strip():
            texts.append(notes)
    return texts


def _record_plan_file(session_id: str, file_path: str) -> None:

    def _set_current_plan_file_path(current_state: dict) -> dict | None:
        if current_state.get("current_plan_file_path") == file_path:
            return None
        current_state["current_plan_file_path"] = file_path
        return current_state

    update_state(session_id, _set_current_plan_file_path)


def _observe_edit_tool(session_id: str, tool_name: str, tool_input: dict, cwd: str) -> list[str]:
    """編集成功後の計画ファイルを記録し、計画の構造を確かめるコマンドの案内を返す。

    ClaudeのWrite・Edit・MultiEditとCodexの成功した`apply_patch`を
    `_hook_tool_input`が共通の操作記録へ変換する。
    実ファイルの読み込みを伴う処理は、適用後に存在する対象（追加・更新・移動先）へ限定する。
    """
    operations = _hook_tool_input.parse_operations(tool_name, tool_input, cwd)
    if operations is None:
        return []
    notices: list[str] = []
    plan_mode_invoked = bool(read_state(session_id).get("plan_mode_skill_invoked", False))
    for operation in operations:
        if not operation.exists_after_apply:
            continue
        display_path = operation.display_path
        if is_plan_main_file(display_path):
            _record_plan_file(session_id, display_path)
        if plan_mode_invoked and is_plan_main_file(display_path) and operation.is_whole_write:
            notices.append(_plan_file_check_notice(_plan_main_path_for(display_path), cwd))
    return notices


def _plan_main_path_for(display_path: str) -> str:
    return display_path


def _plan_file_check_notice(file_path: str, cwd: str) -> str:
    work_dir_option = f" --work-dir {shlex.quote(cwd)}" if cwd else ""
    return _llm_notice(
        f"計画ファイル{file_path}へ書き込んだ。書き込み後に計画の構造を確かめてください:"
        f" 起動部分へ`run-script plan-check -- {shlex.quote(file_path)}{work_dir_option}`を続けてください。"
        "計画の対象リポジトリがこのセッションの作業ディレクトリと異なる場合は、"
        "`--work-dir`の値に対象リポジトリの絶対パスを指定してください。",
        tag="notice",
    )


def _observe_bash_tool(payload: dict, session_id: str, tool_input: dict) -> None:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return
    if tool_input.get("run_in_background"):
        task_id = _background_task_id_from_response(payload.get("tool_response"))
    else:
        # 実行上限に達した前景のBashを実行ホストが背景へ移した場合も、応答は構造化`backgroundTaskId`を返す。
        # このタスクは自セッションが起動したものであり、TaskStopの所有判定へ含める。
        task_id = _structured_background_task_id(payload.get("tool_response"))
    if task_id is not None:
        _record_background_task_id(session_id, task_id)
        if is_main_agent_context(payload):
            function_hook_requests.enqueue(session_id, "background_task", {"id": task_id, "command": command})
    _agents_server_observations.record_wait_observation_attempt(session_id, command, resolve_hook_agent_id(payload))
    _record_bash_response_state(session_id, command, payload.get("tool_response"))


def _completion_report_notice(payload: dict, tool_input: dict) -> str | None:
    response = payload.get("tool_response")
    if (
        not is_main_agent_context(payload)
        or tool_input.get("run_in_background")
        or _background_task_id_from_response(response) is not None
        or isinstance(response, dict)
        and (response.get("exit_code", 0) != 0 or response.get("interrupted"))
    ):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not termination_evidence.published_git_operations(command):
        return None
    operation = f"完了報告の手順書`{termination_evidence.completion_playbook_path()}`を全文読んでください"
    return _llm_notice(
        f"作業完了報告を書く前に{operation}。途中報告・確認・待機には適用しない。",
        tag="notice",
        subject=json.dumps([payload.get("cwd"), command, response], ensure_ascii=False, sort_keys=True),
    )


def _observe_tool(payload: dict, session_id: str, tool_name: str, tool_input: dict, cwd: str, event_name: str) -> list[str]:
    _background_tasks.observe_repl_bash(payload, completed=True)

    notice_task_id = _background_tasks.background_task_id_from_notice(payload.get("tool_response"))
    if notice_task_id is not None:
        _record_background_task_id(session_id, notice_task_id)

    notices: list[str] = []
    if event_name == "PostToolUseFailure":
        failed_task_id = (
            _background_task_id_from_response(payload.get("tool_response"))
            if tool_input.get("run_in_background")
            else _structured_background_task_id(payload.get("tool_response"))
        )
        if failed_task_id is not None:
            _record_background_task_id(session_id, failed_task_id)
            if is_main_agent_context(payload) and isinstance(tool_input.get("command"), str):
                function_hook_requests.enqueue(
                    session_id, "background_task", {"id": failed_task_id, "command": tool_input["command"]}
                )
        return notices
    if is_main_agent_context(payload):
        uwi_notice = _uwi_completion.build_notice(session_id)
        if uwi_notice is not None:
            notices.append(_llm_notice(uwi_notice, tag="notice"))

    if tool_name == _ASK_USER_QUESTION_TOOL:
        if answers := _free_text_answers(payload.get("tool_response")):
            subject = json.dumps(
                [payload.get("tool_use_id"), tool_input, payload.get("tool_response")],
                ensure_ascii=False,
                sort_keys=True,
            )
            notices.append(
                _llm_notice(
                    f"{VERIFICATION_NOTICE_BODY}{_FREE_TEXT_ANSWER_PROCEDURE}{UTTERANCE_UNDERSTANDING_NOTICE_BODY}",
                    tag="notice",
                    subject=subject,
                )
            )
            for answer in answers:
                for content_notice in content_notices(answer, _HOOK_ID, subject=subject):
                    if content_notice not in notices:
                        notices.append(content_notice)
    elif tool_name == "Skill" and event_name == "PostToolUse":
        skill = tool_input.get("skill")
        operation_skills.observe_skill(session_id, resolve_hook_agent_id(payload), skill)
        if not payload.get("agent_id") and isinstance(skill, str):
            arguments = tool_input.get("args")
            session_title.observe_skill(session_id, skill, arguments if isinstance(arguments, str) else None)
    elif tool_name in ("Agent", "Task"):
        agent_id = _background_tasks.async_agent_launch_id(payload.get("tool_response"))
        if agent_id is not None:
            _record_background_task_id(session_id, agent_id)
    elif tool_name in _agents_server_observations.AGENTS_SERVER_HOOK_TOOL_NAMES:
        warnings = _agents_server_observations.observe_tool(
            payload, session_id, tool_name, tool_input, moved_to_background=notice_task_id is not None
        )
        notices.extend(_llm_notice(warning.body, tag=_WARN_TAG, fix=warning.fix, removable_cause=False) for warning in warnings)
        if tool_name.endswith("__start") and is_main_agent_context(payload):
            # 同じhookパッケージの受信解析を共用し、応答の解釈を二重に持たない。
            # pylint: disable-next=protected-access
            response = _agents_server_observations._extract_agents_server_structured_response(payload.get("tool_response"))
            if response.get("session_id") and response.get("status") != "failed" and not response.get("isError"):
                notices.extend(_mount_periodic_recheck(session_id))
    elif tool_name == "TaskStop":
        consume_completion(session_id, target_ids(tool_input))
        response = payload.get("tool_response")
        if (
            is_main_agent_context(payload)
            and isinstance(response, dict)
            and isinstance(response.get("task_id"), str)
            and isinstance(response.get("message"), str)
            and response["message"].startswith(_TASK_STOP_SUCCESS_PREFIX)
        ):
            function_hook_requests.enqueue(session_id, "background_complete", {"id": response["task_id"]})
    elif tool_name in ("Write", "Edit", "MultiEdit", _hook_tool_input.CODEX_APPLY_PATCH_TOOL):
        notices.extend(_observe_edit_tool(session_id, tool_name, tool_input, cwd))
    elif tool_name in ATK_SHELL_TOOLS:
        _observe_bash_tool(payload, session_id, tool_input)
        if tool_name == "Bash" and (completion_notice := _completion_report_notice(payload, tool_input)) is not None:
            notices.append(completion_notice)
    return notices


def _mount_periodic_recheck(session_id: str) -> list[str]:
    paths = function_hook_paths(session_id)
    try:
        if paths is None or paths[0].read_text(encoding="utf-8") != "ready":
            return []
        response = asyncio.run(
            function_hook_requests.request(session_id, "periodic_recheck", {}, timeout=_PERIODIC_RECHECK_WAIT_SECONDS)
        )
    except OSError, ValueError:  # 期限切れ（TimeoutError）を含む。警告はmodが次のターンの入力として届ける
        return []
    warning = response.get("reason")
    return [warning] if isinstance(warning, str) and warning else []


def _observe_skill_reads(payload: dict, session_id: str, tool_name: str, tool_input: dict, cwd: str) -> None:
    subject = resolve_hook_agent_id(payload)
    response = payload.get("tool_response")
    for path in full_reads.full_reads(tool_name, tool_input, response, cwd, full_reads.is_skill_entry_name):
        if (entry := full_reads.skill_entry(path)) is None:
            continue
        skill = f"{entry[0]}:{entry[1]}"
        operation_skills.observe_skill(session_id, subject, skill)
        if skill == BUGFIX_SKILL:
            mark_started(session_id, subject)


def main(payload_text: str) -> int:
    begin_delivery()
    notices: list[str] = []
    parsed = _parse_hook_payload(payload_text)
    if parsed is not None:
        payload, session_id, tool_name, tool_input, cwd, event_name = parsed
        function_hook_requests.cleanup(session_id)
        set_warning_session_id(session_id)
        background_task_id = (
            _background_task_id_from_response(payload.get("tool_response"))
            if tool_input.get("run_in_background")
            else _structured_background_task_id(payload.get("tool_response"))
        )
        notices.extend(command_timing.observe(payload, background_task_id=background_task_id))
        if tool_name in {"Bash", "exec_command", "functions.exec"}:
            clock_claims.observe(payload, tool_input.get("command"), session_id)
        if (
            is_codex_payload(payload)
            and tool_name in {"exec_command", "functions.exec_command", "functions.exec"}
            and (record_warning := session_record_reference_warning(tool_name, tool_input, cwd))
        ):
            notices.append(record_warning)
        if (
            is_codex_payload(payload)
            and tool_name == "Bash"
            and (truncated := codex_truncation.truncation_notice(payload.get("tool_response"))) is not None
        ):
            notices.append(_llm_notice(truncated))
        notices.extend(_observe_tool(payload, session_id, tool_name, tool_input, cwd, event_name))
        if event_name == "PostToolUse" and (tool_name in ATK_SHELL_TOOLS or tool_name in {"Read", "Skill"}):
            review_receipt.observe(payload, session_id, tool_name, tool_input, cwd)
            if not is_codex_payload(payload):
                source_review_receipt.observe(payload, session_id, tool_name, tool_input, cwd)
        if event_name == "PostToolUse" and tool_name != "Skill":
            _observe_skill_reads(payload, session_id, tool_name, tool_input, cwd)
            if tool_name == "Read" or tool_name in ATK_SHELL_TOOLS:
                _persisted_output.observe(payload, session_id, tool_name, tool_input, cwd)
        if (
            event_name == "PostToolUse"
            and is_main_agent_context(payload)
            and (quote_warning := work_contract.quote_update_warning(session_id, payload.get("transcript_path")))
        ):
            notices.append(_llm_notice(quote_warning))
        if (self_check_notice := _progress_self_check.claim_notice(payload)) is not None:
            notices.append(self_check_notice)
    try:
        termination_evidence.observe_tool(payload_text, after=True)
    except (OSError, ValueError, TypeError, KeyError) as error:
        notices.append(
            _llm_notice(
                f"ツール呼び出しの結果を、作業終了時の確認に使う記録へ残せなかった: {error}。"
                "元の操作の成否とは別の記録失敗である。",
                tag="warn",
                removable_cause=False,
                fix=recover_from_failure(
                    component="ツール結果の記録",
                    evidence="元のツール結果と記録の診断",
                    first_step="元の結果で成否と必要な出力を確認する",
                    retry="元の操作は再発行せず、記録だけを回復する",
                ),
            )
        )
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError, ValueError:
        payload = None
    if not isinstance(payload, dict):
        payload = {}
    updated_output = _persisted_output.replacement(payload)
    notices = [notice for notice in notices if notice]
    if notices or updated_output is not None:
        event_name = payload.get("hook_event_name", "PostToolUse")
        if event_name not in {"PostToolUse", "PostToolUseFailure"}:
            event_name = "PostToolUse"
        hook_specific: dict[str, object] = {"hookEventName": event_name}
        if notices:
            hook_specific["additionalContext"] = "\n".join(notices)
        if updated_output is not None:
            hook_specific["updatedToolOutput"] = updated_output
        emit_response({"hookSpecificOutput": hook_specific})
    return 0
