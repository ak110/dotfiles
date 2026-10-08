r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態の記録とplan file書式の判定。

Bash / Write / Edit / MultiEdit / apply_patch / Skill / Agent / Task / AskUserQuestion / agents_server MCPの実行後に
イベントを検出し、セッション状態ファイルに記録する。
PreToolUse、UserPromptSubmitおよびStopフックが参照して判定に使う。
本モジュールは実行後の観測と警告だけを行い、操作を遮断しない。

編集入力は`_hook_tool_input`が共通の操作記録へ正規化する。
Codexから届く正規化済みの入力も同じ操作記録を使う。

検出対象:

1. plan file（`~/.claude/plans/` または
   保存済み計画root `$(atk config get private_notes)/plans/` 配下）書式の判定 (Write / Edit / MultiEdit / apply_patch)
2. plan-modeスキル呼び出し検出 (Skill)
3. 計画実行系`model_type`の`agents_server` sessionの起動時刻と終了時刻の`_process_loop_log`記録
4. agents_server MCP呼び出しと`atk agents wait`実行後のsession状態記録、開始・再開したsessionの待機対象登録
5. exit-session起動検知による`autonomous_exit_invoked`の記録と
   `process_wi_skill_invoked`のリセット (Skill)、
   `agent-toolkit:user-confirmation-and-report`起動による`user_confirmation_skill_pending`の解除 (Skill)、
   操作を起動の契機とするスキルの起動による`operation_skill_ready_agents`の記録 (Skill)
6. 現在の計画ファイルパス記録 (Write / Edit / MultiEdit / apply_patch、plan file判定時)
   （UserPromptSubmitの`sessionTitle`出力が計画名の解決に使用）
7. Bashの背景実行、MCP呼び出しの移行通知およびAgent・Taskの背景起動が返した識別子を、
   バックグラウンドタスクの所有記録へ記録 (Bash / MCP / Agent / Task)
   PostToolUseFailure: Bashのバックグラウンドタスク識別子を同じ所有記録へ保存し、その他は変更せず終了
8. PermissionDenied: 状態を変更せず終了
9. 対象リポジトリで新たに回答されたUWIファイルの通知（全ツール共通）
10. このセッションで作成または編集した計画ファイル（メイン）の絶対パス蓄積
    （編集ツールの操作記録と`create_plan_files.py`または`atk run-script plan-create`のBash標準出力）
11. `AskUserQuestion`の自由記述の回答へ、UserPromptSubmitと同じ現物確認の注記を返す (AskUserQuestion)
12. ホストの上限を超えて退避した出力の抜粋を、未読と保存先、次の操作を示す本文へ置き換える
    `updatedToolOutput` (Bash / PowerShell。`persisted_output`が判定する)
13. メインが成功した前景Bashでcommit・pushを行った後に、completion-reportの起動条件を通知する
"""

import json
import re
import shlex
import sys

from agent_toolkit._atk import run_script as _run_script
from agent_toolkit._common.bash_invocations import extract_bash_invocations
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._common.shell_segments import ExecutionSegment, extract_execution_segments
from agent_toolkit._common.shell_tokens import is_agents_exit_session_command
from agent_toolkit._common.task_stop_state import consume_completion, target_ids
from agent_toolkit._hooks import agents_server_observations as _agents_server_observations
from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._hooks import persisted_output as _persisted_output
from agent_toolkit._hooks import termination_evidence
from agent_toolkit._hooks import tool_input as _hook_tool_input
from agent_toolkit._hooks import uwi_completion as _uwi_completion
from agent_toolkit._hooks.agent_id import is_main_agent_context, resolve_hook_agent_id
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import _WARN_TAG, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter
from agent_toolkit._hooks.pretooluse import operation_skills as _operation_skills
from agent_toolkit._hooks.pretooluse.agent_checks import _PLAN_MODE_SKILL_NAMES
from agent_toolkit._hooks.pretooluse.shell_checks import _git_subcommand_tokens
from agent_toolkit._plan.path_kinds import is_plan_main_file

# このスクリプトの hook 識別子。
_HOOK_ID = "posttooluse"

_llm_notice = _notice_formatter(_HOOK_ID)


def _executable_name(token: str) -> str:
    """実行トークンからディレクトリ部分を除いた実行ファイル名を返す。"""
    return token.replace("\\", "/").rsplit("/", 1)[-1]


# --- スキル呼び出し検出 ---

# process-wiスキル呼び出し検出。フルネームとスラッシュコマンド短縮名の両方を許容する。
_PROCESS_WI_SKILL_NAMES = frozenset({"agent-toolkit:process-wi", "process-wi"})

# 起動でUserPromptSubmitの起動促しを止めるスキル。フルネームと短縮名の両方を許容する。
USER_CONFIRMATION_SKILL_NAMES = frozenset({"agent-toolkit:user-confirmation-and-report", "user-confirmation-and-report"})
USER_CONFIRMATION_PENDING_KEY = "user_confirmation_skill_pending"

# --- ユーザーが書いた文への注記 ---

VERIFICATION_NOTICE_BODY = (
    "直前の発話から、その発話が主張する事実と是正を求めている対象を列挙する。"
    "事実の裏付けを調べる前に、真の場合と偽の場合で次の行動、変更対象、成果物の前提、回答の結論が実質的に変わるか判断する。"
    "変わらない事実は調べずにユーザーが述べた事実として受け取って依頼を進め、自ら確かめた事実として報告しない。"
    "判断を変える事実と是正の対象は、現物（原文・実装・規範・実行結果・対象の目的を定める仕様・設計記録）と比べて確かめてから応答する。"
    "是正を求める対象を含む発話では、対処の前に`agent-toolkit:bugfix`をスキル機能で起動する。"
    "不具合の有無・原因・直し方を述べるか確認で提案する場合も、述べる前に起動する。"
    "対処を委譲先やAWIへ委ねる場合も含む。"
    "判断を変える事実を現物と比べて確かめられない場合は、同意も変更もしない。"
    "確かめる事実も是正の対象も無いと判定した発話では、現物との比較を省いて次の工程へ進む。"
    "稼働中の依頼がある場合は、未完了工程が元の依頼の目的に対応するか確かめてから次に実行する工程を確定する。"
    "同じ論点で修正が続く場合は意図と要件への影響を確かめ、確定できないときだけユーザー確認する。"
)
"""ユーザーが書いた文の内容を現物で確かめる手順を示す注記の本文。

ユーザーが書いた文はUserPromptSubmitの`prompt`と、`AskUserQuestion`の自由記述の回答としてPostToolUseの
`tool_response`の両方に届くため、両hookがこの1か所の定義を使う。`user_prompt_submit.py`は本モジュールを
importするため、循環importを避けて本モジュールに置く。
比べて確かめる対象は発話ごとに異なるため、対象の列挙と、真偽が行動を変えない事実の裏付けを省く判断を受領側の手順として本文に持たせる。
その列挙をフック側の判定で代替しない。フックの入力は発話本文だけであり、
規則による分類の誤りは、現物との比較を最も要する発話で注記を無音のまま欠落させるためである。
"""

_ASK_USER_QUESTION_TOOL = "AskUserQuestion"
_FREE_TEXT_ANSWER_PROCEDURE = (
    "この注記は`AskUserQuestion`への回答のうち、提示した選択肢と一致しない自由記述へ返している。"
    "回答が質問の前提や推奨案の根拠を否定する場合も前段の判断を適用し、"
    "その前提の真偽で推奨案や次の行動が変わる場合だけ、前提を現物（コミット件名、実装、規範、実行結果など）で確かめる。"
    "確かめた結果が回答と一致すれば、前提が誤っていたことと確定した事実だけを伝え、"
    "質問本文と先の報告で述べた前提や懸念を再掲しない。"
    "一致しなければ、確かめた手段と結果を示して回答と一致しない点を伝える。"
    "行動を変えない前提は確かめずに回答を受け取り、回答に沿って進める。"
)
"""`AskUserQuestion`の自由記述の回答への注記で、共通の本文に続ける手順。"""
# Claude Codeが選択肢を選ばずに注記だけを残した質問へ入れる`answers`の値（2.1.283の記録で観測）。
_NOTES_ONLY_ANSWER = "(notes only)"
# 複数選択の質問の`answers`は、選んだ`label`と自由記述を`, `で連結した1つの文字列で届く（2.1.285の記録で観測）。
_MULTI_SELECT_SEPARATOR = ", "

_AUTONOMOUS_EXIT_STATE_KEY = "autonomous_exit_invoked"


# --- plan fileの書式を判定する定数 ---


def _set_process_wi_invoked(state: dict) -> dict | None:
    """process-wiスキル起動フラグを常時Trueへ上書きする。

    新規process-wiラン開始時に前ランの残置フラグを無視して確実にTrueへ強制上書きするため冪等スキップを廃止する。
    フラグのリセットは`_reset_process_wi_invoked`（exit-session起動検知）が担い、本関数と対で使う。
    """
    state["process_wi_skill_invoked"] = True
    return state


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
        for line in text.splitlines():
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
_PLAN_CREATION_RUN_SCRIPT_PREFIX = ("atk", "run-script", "plan-create")


def _is_plan_creation_invocation(tokens: tuple[str, ...]) -> bool:
    """実行トークン列が旧または現行の計画ファイルを作成するコマンドであるかを返す。"""
    if any(_PLAN_CREATION_SCRIPT_NAME in token for token in tokens):
        return True
    normalized = (_executable_name(tokens[0]), *tokens[1:]) if tokens else ()
    return normalized[: len(_PLAN_CREATION_RUN_SCRIPT_PREFIX)] == _PLAN_CREATION_RUN_SCRIPT_PREFIX


def _record_created_plan_file(session_id: str, segments: list[ExecutionSegment], tool_response: object) -> None:
    """計画ファイルを作成するコマンドの標準出力から計画ファイル（メイン）の絶対パスを記録する。

    このスクリプトは確定したパスを標準出力へ1行ずつ書くため、計画ファイル（メイン）と判定した行だけを抽出する。
    該当が無い場合は記録せず、PostToolUseの応答を変えない。
    """
    if not any(_is_plan_creation_invocation(segment.tokens) for segment in segments):
        return
    for text in _response_texts(tool_response):
        for line in text.splitlines():
            candidate = line.strip()
            if candidate and is_plan_main_file(candidate):
                _record_plan_file(session_id, candidate)
                return


def _parse_hook_payload(payload_text: str) -> tuple[dict, str, str, dict, str, str] | None:
    """処理対象のPostToolUse payloadを検証して共通項目を返す。"""
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return None
    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return None
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    event_name = payload.get("hook_event_name", "")
    if event_name == "PermissionDenied" or (event_name == "PostToolUseFailure" and tool_name != "Bash"):
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
    所有の根拠は自身の呼び出しが識別子を返したことであり、その呼び出しの成否に依存しない。
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


def _record_skill_use(session_id: str, skill_name: object) -> None:
    """Skill呼び出しに対応するセッション状態を記録する。"""
    if not isinstance(skill_name, str):
        return
    if skill_name in _PLAN_MODE_SKILL_NAMES:

        def _set_invoked(state: dict) -> dict | None:
            if state.get("plan_mode_skill_invoked", False):
                return None
            state["plan_mode_skill_invoked"] = True
            return state

        update_state(session_id, _set_invoked)
    if skill_name in _PROCESS_WI_SKILL_NAMES:
        update_state(session_id, _set_process_wi_invoked)
    if skill_name in USER_CONFIRMATION_SKILL_NAMES:
        update_state(session_id, clear_user_confirmation_pending)


def _has_free_text_answer(tool_response: object) -> bool:
    """`AskUserQuestion`の回答が、提示した選択肢と一致しないユーザーの文を含むかを返す。

    `answers`の値（複数選択では`, `で区切った各要素）がその質問のどの`label`とも一致しない場合、
    `options`を持たない質問へ空でない回答がある場合、選択肢を選ばずに入力した`response`と
    質問ごとの`annotations`の`notes`が空白以外の文字を持つ場合を自由記述とする。
    ユーザーが応答せずに自動で閉じた結果（`afkTimeoutMs`を持つ）と、想定外の形の応答は対象外とする。
    """
    if not isinstance(tool_response, dict) or tool_response.get("afkTimeoutMs") is not None:
        return False
    response = tool_response.get("response")
    if isinstance(response, str) and response.strip():
        return True
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
        if not labels:
            return True
        parts = answer.split(_MULTI_SELECT_SEPARATOR) if multi_select else [answer]
        if any(part not in labels for part in parts):
            return True
    annotations = tool_response.get("annotations")
    for annotation in (annotations if isinstance(annotations, dict) else {}).values():
        notes = annotation.get("notes") if isinstance(annotation, dict) else None
        if isinstance(notes, str) and notes.strip():
            return True
    return False


def clear_user_confirmation_pending(state: dict) -> dict | None:
    """`agent-toolkit:user-confirmation-and-report`の起動を待つ状態を解除する。既に解除済みならNoneを返す。"""
    if not state.get(USER_CONFIRMATION_PENDING_KEY, False):
        return None
    state[USER_CONFIRMATION_PENDING_KEY] = False
    return state


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
    project_root = _run_script.PLUGIN_ROOT
    check_script = project_root / _run_script.SCRIPT_PATHS["plan-check"]
    work_dir_option = f" --work-dir {shlex.quote(cwd)}" if cwd else ""
    return _llm_notice(
        f"計画ファイル{file_path}へ書き込んだ。書き込み後に計画の構造を確かめる:"
        f" `uv run --project {shlex.quote(str(project_root))} --locked --no-default-groups"
        f" {shlex.quote(str(check_script))}{work_dir_option}"
        f" {shlex.quote(file_path)}`."
        "計画の対象リポジトリがこのセッションの作業ディレクトリと異なる場合は、"
        "`--work-dir`を対象リポジトリへ置き換える。",
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
    _agents_server_observations.record_wait_observation_attempt(session_id, command, resolve_hook_agent_id(payload))
    _record_bash_response_state(session_id, command, payload.get("tool_response"))


def _completion_report_notice(payload: dict, tool_input: dict) -> str | None:
    """メインの成功した前景Git操作の近傍へ、完了報告の起動条件を届ける。"""
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
    if not isinstance(command, str):
        return None
    for invocation in extract_bash_invocations(command):
        parsed = _git_subcommand_tokens(invocation.segment)
        if invocation.background or not invocation.arguments_known or parsed is None or parsed[0] not in {"commit", "push"}:
            continue
        arguments = parsed[1]
        options = iter(arguments)
        excluded = False
        value_options = {
            "-m",
            "--message",
            "-F",
            "--file",
            "-C",
            "--reuse-message",
            "-c",
            "--reedit-message",
            "--author",
            "--date",
            "--cleanup",
            "-t",
            "--template",
            "--trailer",
            "--fixup",
            "--squash",
            "--pathspec-from-file",
            "--repo",
            "--receive-pack",
            "--exec",
            "-o",
            "--push-option",
        }
        for option in options:
            if option == "--":
                break
            if option in value_options:
                next(options, None)
            elif option in {"--dry-run", "--help", "-h"} or parsed[0] == "push" and option == "-n":
                excluded = True
                break
        prefix = invocation.segment.tokens[: len(invocation.segment.tokens) - len(arguments)]
        if excluded or "--help" in prefix:
            continue
        operation = (
            f"`{_run_script.PLUGIN_ROOT / 'skills/completion-report/SKILL.md'}`を読む"
            if is_codex_payload(payload)
            else "Skillで`agent-toolkit:completion-report`を起動する"
        )
        return _llm_notice(
            f"作業を完了してユーザーへ成果を報告するときは、報告を書く前に{operation}。途中報告・確認・待機には適用しない。",
            tag="notice",
        )
    return None


def _observe_tool(payload: dict, session_id: str, tool_name: str, tool_input: dict, cwd: str, event_name: str) -> list[str]:
    """ツールの種類ごとに実行結果をセッション状態へ記録し、コーディングエージェントへ返す通知を返す。"""
    # 所有の根拠は、自セッションのツール呼び出しの応答がバックグラウンドタスク識別子を返したことである。
    # 起動の成否は所有の有無を変えないため、ホストのMCP移行通知で始まる応答は成否によらず記録する。
    # 本文の途中に同じ文言を含む前景の出力は、ホストの通知ではないため記録しない。
    notice_task_id = _background_tasks.background_task_id_from_notice(payload.get("tool_response"))
    if notice_task_id is not None:
        _record_background_task_id(session_id, notice_task_id)

    if event_name == "PostToolUseFailure":
        if tool_input.get("run_in_background"):
            failed_task_id = _background_task_id_from_response(payload.get("tool_response"))
            if failed_task_id is not None:
                _record_background_task_id(session_id, failed_task_id)
        return []

    notices: list[str] = []
    # 対象リポジトリで新たに回答されたUWIファイルがある場合に通知する。
    # ツール種別に依らず回答を確認し、ユーザーの回答から通知までの遅延を抑える。
    # この通知が指示する反映と依存作業の再開はメインが所有するため、
    # in-processのサブエージェントと`agents_server`の委譲先セッションでは通知を組み立てない。
    if cwd and is_main_agent_context(payload):
        uwi_notice = _uwi_completion.build_notice(session_id, cwd, resolve_hook_agent_id(payload))
        if uwi_notice is not None:
            notices.append(_llm_notice(uwi_notice, tag="notice"))

    # AskUserQuestion: ユーザーが書いた文はUserPromptSubmitを経ずにツール結果として届くため、同じ注記をここで返す。
    # 1回の質問につき1回しか生じないため、UserPromptSubmitの経過時間の閾値は適用しない。
    if tool_name == _ASK_USER_QUESTION_TOOL:
        if _has_free_text_answer(payload.get("tool_response")):
            notices.append(_llm_notice(f"{VERIFICATION_NOTICE_BODY}{_FREE_TEXT_ANSWER_PROCEDURE}", tag="notice"))
    elif tool_name == "Skill":
        # Skill: plan-modeスキル呼び出し検出とprocess-wi起動検出
        _record_skill_use(session_id, tool_input.get("skill"))
        _operation_skills.record_skill_ready(session_id, tool_input.get("skill"), resolve_hook_agent_id(payload))
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
    elif tool_name == "TaskStop":
        consume_completion(session_id, target_ids(tool_input))
    elif tool_name in ("Write", "Edit", "MultiEdit", _hook_tool_input.CODEX_APPLY_PATCH_TOOL):
        notices.extend(_observe_edit_tool(session_id, tool_name, tool_input, cwd))
    elif tool_name != "PowerShell":
        # PowerShellは退避した出力の置き換え（`main`）だけを対象とする
        _observe_bash_tool(payload, session_id, tool_input)
        if tool_name == "Bash" and (completion_notice := _completion_report_notice(payload, tool_input)) is not None:
            notices.append(completion_notice)
    return notices


def main(payload_text: str) -> int:
    """エントリポイント。終了コードは常に0。

    フック応答はstdout全体を1つのJSONとして解析されるため、各記録処理が返した通知本文を
    改行で連結して1回だけ出力する。
    """
    notices: list[str] = []
    parsed = _parse_hook_payload(payload_text)
    if parsed is not None:
        payload, session_id, tool_name, tool_input, cwd, event_name = parsed
        set_warning_session_id(session_id)
        notices = _observe_tool(payload, session_id, tool_name, tool_input, cwd, event_name)
    try:
        termination_evidence.observe_tool(payload_text, after=True)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"終了工程の実行結果を取得できない: {error}", file=sys.stderr)
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        payload = {}
    updated_output = _persisted_output.replacement(payload)
    if notices or updated_output is not None:
        event_name = payload.get("hook_event_name", "PostToolUse")
        if event_name not in {"PostToolUse", "PostToolUseFailure"}:
            event_name = "PostToolUse"
        hook_specific: dict[str, object] = {"hookEventName": event_name}
        if notices:
            hook_specific["additionalContext"] = "\n".join(notices)
        if updated_output is not None:
            hook_specific["updatedToolOutput"] = updated_output
        print(json.dumps({"hookSpecificOutput": hook_specific}, ensure_ascii=False))
    return 0
