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
5. exit-session起動検知による`autonomous_exit_invoked`の記録と
   `process_wi_skill_invoked`のリセット (Skill)、
   読込案内の対象資料の取得の`operation_skill_delivered_agents`への記録
   （内容ハッシュによる現行形式。判定は`pretooluse/read_delivery.py`の`observe`が行う）
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
"""

import json
import re

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import asyncio
lazy import shlex

from agent_toolkit._hooks.bugfix_gate import QUOTED_MATERIAL_RE, observe_verification
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
lazy from agent_toolkit._hooks import codex_read_observation as _codex_read_observation
lazy from agent_toolkit._hooks import command_timing, effort_notice, session_title, termination_evidence
lazy from agent_toolkit._hooks import persisted_output as _persisted_output
lazy from agent_toolkit._hooks import progress_self_check as _progress_self_check
lazy from agent_toolkit._hooks import tool_input as _hook_tool_input
lazy from agent_toolkit._hooks import uwi_completion as _uwi_completion
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context, resolve_hook_agent_id
lazy from agent_toolkit._hooks.background_tasks_constants import _TASK_STOP_SUCCESS_PREFIX
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.pretooluse import read_delivery as _read_delivery
lazy from agent_toolkit._plan.path_kinds import is_plan_main_file

# このスクリプトの hook 識別子。
_HOOK_ID = "posttooluse"

_llm_notice = _notice_formatter(_HOOK_ID)

# modの装着（`CronList`・`atk wait-schedule`・`CronCreate`）を待つ上限。
# modのツール呼び出しにも設定hookが実行されるため、2026年11月18日の観測では約5秒かかった。
# 上限を過ぎた後の装着失敗はmodが次のターンの入力として届ける。
_PERIODIC_RECHECK_WAIT_SECONDS = 20


def _executable_name(token: str) -> str:
    """実行トークンからディレクトリ部分を除いた実行ファイル名を返す。"""
    return token.replace("\\", "/").rsplit("/", 1)[-1]


# --- スキル呼び出し検出 ---

# --- ユーザーが書いた文への注記 ---

VERIFICATION_NOTICE_BODY = (
    "応答の前に、`agent-toolkit:atk-mode/playbooks/user-utterance.md`"
    "「割り込み・訂正・中止」に従い、現物との比較の要否を判定せよ。"
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
    """例示の語を含む発話へ、原文の対象と例示の関係を判断する節の所在を示す注記を返す。

    指摘・提案・実行指示のどれに当たるか、例示が上位の対象の一部かは受領側が判断し、本フックは字面の一致だけで返す。
    調査を求める語も併せ持つ発話には、独立した調査を委ねる手段の所在も加える。
    """
    text = QUOTED_MATERIAL_RE.sub("", prompt)
    # 「AとかBとか」の開いた列挙も例示として扱う。単独の「とか」は意味が多いため、反復した場合に限る。
    if not (any(word in text for word in _EXAMPLE_WORDS) or text.count("とか") >= 2):
        return None
    body = (
        "発話に例示の語がある。応答・具体化の前に、"
        "`agent-toolkit:atk-mode/playbooks/user-utterance.md`"
        "「原文の保持と取り込み」に従い、原文の対象と例示の関係を判断せよ。"
        "例示が上位の対象の一部なら、上位の対象を言葉で定め、例示以外の候補を探してから範囲を確定せよ。"
    )
    if any(word in text for word in _INVESTIGATION_WORDS):
        body += (
            "調査へ着手する前の委譲判断は`agent-toolkit:delegation`の`references/routing.md`"
            "「コンテキスト消費が大きい調査の切り出し」に従え。"
        )
    return _notice_formatter(source)(body, tag="notice", subject=subject)


def content_notices(prompt: str, source: str, *, subject: str = "") -> list[str]:
    """通常発話と質問への自由記述に共通の、内容に応じた注記を返す。"""
    example = _example_scope_notice(prompt, source, subject=subject or prompt)
    return [] if example is None else [example]


_ASK_USER_QUESTION_TOOL = "AskUserQuestion"
_FREE_TEXT_ANSWER_PROCEDURE = "自由記述の回答も同節に従え。"
"""`AskUserQuestion`の自由記述の回答への注記で、共通の本文に続ける手順。"""
# Claude Codeが選択肢を選ばずに注記だけを残した質問へ入れる`answers`の値（2.1.283の記録で観測）。
_NOTES_ONLY_ANSWER = "(notes only)"
# 複数選択の質問の`answers`は、選んだ`label`と自由記述を`, `で連結した1つの文字列で届く（2.1.285の記録で観測）。
_MULTI_SELECT_SEPARATOR = ", "

_AUTONOMOUS_EXIT_STATE_KEY = "autonomous_exit_invoked"


# --- plan fileの書式を判定する定数 ---


def _reset_process_wi_invoked(state: dict) -> dict | None:
    """`process_wi_skill_invoked`を偽へ戻す。既に偽ならNoneを返す（冪等）。"""
    if not state.get("process_wi_skill_invoked", False):
        return None
    state["process_wi_skill_invoked"] = False
    return state


def _record_exit_session_invoked(state: dict) -> dict | None:
    """exit-session呼び出しを記録し、`process_wi_skill_invoked`をリセットする。"""
    changed = False
    if state.get(_AUTONOMOUS_EXIT_STATE_KEY) is not True:
        state[_AUTONOMOUS_EXIT_STATE_KEY] = True
        changed = True
    if _reset_process_wi_invoked(state) is not None:
        changed = True
    return state if changed else None


def _response_texts(value: object) -> list[str]:
    """Bash応答から標準出力相当の文字列を再帰的に抽出する。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for nested in value.values() for text in _response_texts(nested)]
    if isinstance(value, list):
        return [text for nested in value for text in _response_texts(nested)]
    return []


def _response_has_exit_invocation(value: object) -> bool:
    """応答中の単独JSON行に終了CLIの実行証跡がある場合に真を返す。"""
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
    """成功したBash応答を使い、終了CLI起動と計画ファイル作成を記録する。"""
    segments = [segment for segment in extract_execution_segments(command) if segment.resolved and segment.tokens]
    exit_invoked = any(is_agents_exit_session_command(segment.tokens) for segment in segments)
    if exit_invoked and _response_has_exit_invocation(tool_response):
        update_state(session_id, _record_exit_session_invoked)
    _record_created_plan_file(session_id, segments, tool_response)


_PLAN_CREATION_SCRIPT_NAME = "create_plan_files.py"
_PLAN_CREATION_RUN_SCRIPT_PREFIX = ("run-script", "plan-create")


def _is_plan_creation_invocation(tokens: tuple[str, ...]) -> bool:
    """実行トークン列が旧または現行の計画ファイルを作成するコマンドであるかを返す。"""
    if any(_PLAN_CREATION_SCRIPT_NAME in token for token in tokens):
        return True
    return is_atk_command(tokens) and tokens[1:3] == _PLAN_CREATION_RUN_SCRIPT_PREFIX


def _record_created_plan_file(session_id: str, segments: list[ExecutionSegment], tool_response: object) -> None:
    """計画ファイルを作成するコマンドの標準出力から計画ファイル（メイン）の絶対パスを記録し、計画の工程の開始とする。

    このスクリプトは確定したパスを標準出力へ1行ずつ書くため、計画ファイル（メイン）と判定した行だけを抽出する。
    新しい計画のパスで置き換えるため、前の計画名をセッション名へ出し続けない。
    計画の工程の開始（`plan_mode_skill_invoked`）は、以後の計画ファイルの全体書込で構造の確認を案内する条件になる。
    該当が無い場合は記録せず、PostToolUseの応答を変えない。
    """
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
    """処理対象のPostToolUse payloadを検証して共通項目を返す。"""
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
    if not isinstance(tool_input, dict):
        return None
    event_name = payload.get("hook_event_name", "")
    # 後続の対象限定で捨てるイベントも、ホストから到着した元の応答で記録する。
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
    """Bashの背景実行応答からタスクIDを返す。

    応答は文字列と辞書のいずれの形でも届くため、入れ子を再帰的に走査する。
    実行上限や手動の操作で背景へ移ったBashは構造化`backgroundTaskId`で記録し、
    MCP呼び出しの移行通知は`background_tasks.background_task_id_from_notice`が扱う。
    """
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
    """Bash応答の最上位にある構造化`backgroundTaskId`を返す。

    前景実行の出力本文に同じ文言が現れても所有の根拠にしないため、本文の文字列が一致するかは判定しない。
    """
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
    """提示した選択肢と一致しない回答・追加の自由記述を配送欄ごとに集める。"""
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
    """現在の計画ファイルを記録する。"""

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
    """現行計画の構成要素から計画ファイル（メイン）の絶対パスを返す。"""
    return display_path


def _plan_file_check_notice(file_path: str, cwd: str) -> str:
    """計画ファイル全文書き込み後に実行する計画の構造を確かめるコマンドの案内文を返す。"""
    work_dir_option = f" --work-dir {shlex.quote(cwd)}" if cwd else ""
    return _llm_notice(
        f"計画ファイル{file_path}へ書き込んだ。書き込み後に計画の構造を確かめよ:"
        f" 起動部分へ`run-script plan-check -- {shlex.quote(file_path)}{work_dir_option}`を続けよ。"
        "計画の対象リポジトリがこのセッションの作業ディレクトリと異なる場合は、"
        "`--work-dir`を対象リポジトリへ置き換えよ。",
        tag="notice",
    )


def _observe_bash_tool(payload: dict, session_id: str, tool_input: dict) -> None:
    """成功したBashの応答から、バックグラウンドタスクの所有、`atk agents wait`の観測、終了CLIと計画ファイル作成を記録する。"""
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
    """メインが前景で成功させたGit操作の直後に、完了報告の手順書を読む条件を通知する。"""
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
    operation = f"完了報告の手順書`{termination_evidence.completion_playbook_path()}`を全文読め"
    return _llm_notice(
        f"作業完了報告を書く前に{operation}。途中報告・確認・待機には適用しない。",
        tag="notice",
        subject=json.dumps([payload.get("cwd"), command, response], ensure_ascii=False, sort_keys=True),
    )


def _observe_tool(payload: dict, session_id: str, tool_name: str, tool_input: dict, cwd: str, event_name: str) -> list[str]:
    """ツールの種類ごとに実行結果をセッション状態へ記録し、コーディングエージェントへ返す通知を返す。"""
    _background_tasks.observe_repl_bash(payload, completed=True)
    # 所有の根拠は、自セッションのツール呼び出しの応答がバックグラウンドタスク識別子を返したことである。
    # 起動の成否は所有の有無を変えないため、ホストのMCP移行通知で始まる応答は成否によらず記録する。
    # 本文の途中に同じ文言を含む前景の出力は、ホストの通知ではないため記録しない。
    notice_task_id = _background_tasks.background_task_id_from_notice(payload.get("tool_response"))
    if notice_task_id is not None:
        _record_background_task_id(session_id, notice_task_id)

    notices: list[str] = []
    try:
        _read_delivery.observe(payload, tool_name, tool_input, session_id)
    except (OSError, UnicodeError, ValueError) as error:
        notices.append(
            _llm_notice(
                f"資料の読取結果を取得済みとして記録できない: {error}。読取自体の成否はツール結果で確認せよ。",
                tag=_WARN_TAG,
                fix="診断に示された資料と読込表を確認せよ。本文が欠けていれば必要な範囲を取得し、"
                "記録の障害が続く場合は診断を委譲元またはユーザーへ報告せよ。",
                removable_cause=True,
                escalate_on_repeat=False,
            )
        )
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
    # この会話が投入したUWIに回答が付いた場合に通知する。
    # ツール種別に依らず回答を確認し、ユーザーの回答から通知までの遅延を抑える。
    # この通知が指示する反映と回答を待っていた作業の再開はメインが所有するため、
    # in-processのサブエージェントと`agents_server`の委譲先セッションでは通知を組み立てない。
    if is_main_agent_context(payload):
        uwi_notice = _uwi_completion.build_notice(session_id)
        if uwi_notice is not None:
            notices.append(_llm_notice(uwi_notice, tag="notice"))

    # AskUserQuestion: ユーザーが書いた文はUserPromptSubmitを経ずにツール結果として届くため、同じ注記をここで返す。
    # 同じ質問・回答の再配送を抑え、別の質問または回答には改めて注記を届ける。
    if tool_name == _ASK_USER_QUESTION_TOOL:
        if answers := _free_text_answers(payload.get("tool_response")):
            subject = json.dumps(
                [payload.get("tool_use_id"), tool_input, payload.get("tool_response")],
                ensure_ascii=False,
                sort_keys=True,
            )
            notices.append(
                _llm_notice(f"{VERIFICATION_NOTICE_BODY}{_FREE_TEXT_ANSWER_PROCEDURE}", tag="notice", subject=subject)
            )
            for answer in answers:
                for content_notice in content_notices(answer, _HOOK_ID, subject=subject):
                    if content_notice not in notices:
                        notices.append(content_notice)
    elif tool_name == "Skill":
        # Skill: 表示名のための作業の起動検出
        skill = tool_input.get("skill")
        if not payload.get("agent_id") and isinstance(skill, str):
            arguments = tool_input.get("args")
            session_title.observe_skill(session_id, skill, arguments if isinstance(arguments, str) else None)
    elif tool_name in ("Agent", "Task"):
        # AgentとTask: 背景起動の応答が返した`agentId`だけをバックグラウンドタスクの所有記録へ残す
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
    """modへ定期再確認のtaskの装着を依頼し、装着できなかった場合の警告を同じ`start`の結果へ添える。

    `$.prompt.submit`による通知は実行中のターンの終了後にしか届かないため、modの応答を待って同じターンの中で届ける。
    作成の成功と既にtaskを持つ場合は空の応答となり、何も添えない。
    modを読み込んでいない会話（読込目印が`ready`でない）では待たない。
    """
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


def main(payload_text: str) -> int:
    """エントリポイント。終了コードは常に0。

    フック応答はstdout全体を1つのJSONとして解析されるため、各記録処理が返した通知本文を
    改行で連結して1回だけ出力する。
    """
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
        observation = _codex_read_observation.collect(payload)
        if observation is not None:
            notices.append(observation.context)
            payload = {**payload, "tool_response": observation.body or ""}
        elif (
            is_codex_payload(payload)
            and tool_name == "Bash"
            and isinstance(payload.get("tool_response"), str)
            and isinstance(tool_input.get("command"), str)
            and _codex_read_observation.read_plan(tool_input.get("command", ""))
        ):
            # nativeの失敗・不明な終了状態を、旧来の識別行だけで読了にしない。
            payload = {**payload, "tool_response": ""}
        if (
            observation is None
            and is_codex_payload(payload)
            and tool_name == "Bash"
            and _codex_read_observation.response_is_truncated(payload.get("tool_response"))
        ):
            notices.append(_llm_notice(_codex_read_observation.TRUNCATED_OUTPUT_NOTICE))
        notices.extend(_observe_tool(payload, session_id, tool_name, tool_input, cwd, event_name))
        if (effort_advice := effort_notice.observe(payload)) is not None:
            notices.append(effort_advice)
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
                fix="元のツール結果で成否と必要な出力を確認せよ。記録の診断を委譲元またはユーザーへ報告せよ。",
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
