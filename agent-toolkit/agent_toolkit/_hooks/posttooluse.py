r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態の記録とplan file書式の判定。

Bash / Write / Edit / MultiEdit / apply_patch / Skill / Agent / Task / agents_server MCPの実行後に
イベントを検出し、セッション状態ファイルに記録する。
PreToolUse、UserPromptSubmitおよびStopフックが参照して判定に使う。
本モジュールは実行後の観測と警告だけを行い、操作を遮断しない。

編集入力は`_hook_tool_input`が共通の操作記録へ正規化する。
Codexでは成功した`apply_patch`だけが本フックへ届く。

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
7. Bashの背景実行、バックグラウンドタスクへの移行通知およびAgent・Taskの背景起動が返した識別子を、
   バックグラウンドタスクの所有記録へ記録 (Bash / Agent / Task)
   PostToolUseFailure: Bashのバックグラウンドタスク識別子を同じ所有記録へ保存し、その他は変更せず終了
8. PermissionDenied: 状態を変更せず終了
9. 対象リポジトリで新たに回答されたUWIファイルの通知（全ツール共通）
10. このセッションで作成または編集した計画ファイル（メイン）の絶対パス蓄積
    （編集ツールの操作記録と`create_plan_files.py`または`atk run-script plan-create`のBash標準出力）
"""

import json
import os
import pathlib
import re
import shlex
import sys

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._agents_server import (
    state as _agents_server_state,
)  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._agents_server import (
    status_file as _agents_server_status_file,
)  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._agents_server import (
    tool_names as _agents_server_tool_names,
)  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._atk.wi import (
    process_loop_log as _process_loop_log,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._common.shell_tokens import is_agents_exit_session_command, is_agents_wait_command  # noqa: E402
from agent_toolkit._hooks import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    background_task_outputs as _background_task_outputs,
)
from agent_toolkit._hooks import stop_gate as _stop_gate  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._hooks import termination_evidence
from agent_toolkit._hooks import (
    tool_input as _hook_tool_input,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    uwi_completion as _uwi_completion,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks.agent_id import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_main_agent_context,
    resolve_hook_agent_id,
)
from agent_toolkit._hooks.bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    ExecutionSegment,
    extract_execution_segments,
)
from agent_toolkit._hooks.notice import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _WARN_TAG,
    set_warning_session_id,
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.pretooluse import operation_skills as _operation_skills  # noqa: E402
from agent_toolkit._hooks.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    read_state,
    update_state,
)
from agent_toolkit._hooks.task_stop_state import consume_completion, target_ids  # noqa: E402

# pylint: disable=wrong-import-position,import-error
from agent_toolkit._hooks.tracked_model_types import TRACKED_MODEL_TYPES as _TRACKED_MODEL_TYPES  # noqa: E402
from agent_toolkit._plan.locations import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_plan_component_file,
    is_plan_main_file,
)

# pylint: enable=wrong-import-position,import-error

# このスクリプトの hook 識別子。
_HOOK_ID = "posttooluse"

_llm_notice = _notice_formatter(_HOOK_ID)
_UNREGISTERED_WAIT_TARGET_FIX = (
    "`atk agents wait`はこのセッションを待機対象として扱わないため、終端は`agents_server`の`show`で確認する。"
)
"""待機所有者を識別できず待機対象を登録できなかったsessionの次の操作。"""


# --- Bashコマンド前処理 ---

# コマンド先頭またはセグメント区切り（`;`・`&`・`|`）直後に並ぶ接頭辞を捕捉する。
# 対象は`KEY=VALUE`の環境変数代入と`timeout <時間>`の時間制限で、両者の混在と連続も1回で除去する。
# `.sub`で接頭辞列を除去し、先頭の区切り文字＋空白は維持する。
_COMMAND_PREFIX_PATTERN = re.compile(r"(\A|[;&|])(\s*)(?:[A-Za-z_]\w*=\S*\s+|timeout\s+\d+(?:\.\d+)?[smhd]?\s+)+")


def _strip_command_prefixes(command: str) -> str:
    """コマンド先頭・セグメント区切り直後の環境変数代入と時間制限の接頭辞を除去する。

    適用範囲: Bashコマンド文字列。`KEY=VALUE`と`timeout <時間>`の単純形式のみを対象とし、
    クォート内に空白を含む値・`env`コマンド経由・行継続バックスラッシュ・
    `timeout`のオプション付き形式（`-k 10s 600`等、引数の境界を字句だけで確定できない）は対象外とする。
    """
    return _COMMAND_PREFIX_PATTERN.sub(r"\1\2", command)


def _executable_name(token: str) -> str:
    """実行トークンからディレクトリ部分を除いた実行ファイル名を返す。"""
    return token.replace("\\", "/").rsplit("/", 1)[-1]


# --- plan-modeスキル呼び出し検出 ---

# `Skill`の`skill`引数として許容するスキル名。
# ユーザーが手動で短縮名を渡すケースに備えてフルネームと短縮名の両方を許容する。
_PLAN_MODE_SKILL_NAMES = frozenset({"agent-toolkit:plan-mode", "plan-mode"})

# process-wiスキル呼び出し検出。フルネームとスラッシュコマンド短縮名の両方を許容する。
_PROCESS_WI_SKILL_NAMES = frozenset({"agent-toolkit:process-wi", "process-wi"})

# 起動でUserPromptSubmitの起動促しを止めるスキル。フルネームと短縮名の両方を許容する。
USER_CONFIRMATION_SKILL_NAMES = frozenset({"agent-toolkit:user-confirmation-and-report", "user-confirmation-and-report"})
USER_CONFIRMATION_PENDING_KEY = "user_confirmation_skill_pending"

_AUTONOMOUS_EXIT_STATE_KEY = "autonomous_exit_invoked"

# Claude CodeとCodexが生成するagents_serverの完全修飾MCP tool名。
_AGENTS_SERVER_NAMESPACES = _agents_server_tool_names.MCP_NAMESPACES
# 統合前の起動ツール名を公開する旧版のサーバーが稼働中でも、同じ観測義務とcwdを記録する。
_AGENTS_SERVER_START_OPERATIONS = _agents_server_tool_names.RECORDED_START_OPERATIONS
_AGENTS_SERVER_START_TOOLS = frozenset(
    f"{namespace}{tool}" for namespace in _AGENTS_SERVER_NAMESPACES for tool in _AGENTS_SERVER_START_OPERATIONS
)
_AGENTS_SERVER_SEND_TOOLS = frozenset(f"{namespace}send_message" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_KILL_TOOLS = frozenset(f"{namespace}kill" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_STOP_TOOLS = frozenset(f"{namespace}stop" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_LIST_TOOLS = frozenset(f"{namespace}list" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_TOOL_NAMES = (
    _AGENTS_SERVER_START_TOOLS | _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS | _AGENTS_SERVER_STOP_TOOLS
)
_AGENTS_SERVER_DIAGNOSTIC_TOOLS = _AGENTS_SERVER_TOOL_NAMES | _AGENTS_SERVER_LIST_TOOLS
# `show`は稼働中の子sessionの識別子と`cwd`の対を返すため、この対の記録だけを目的として受信する。
_AGENTS_SERVER_SHOW_TOOLS = frozenset(f"{namespace}show" for namespace in _AGENTS_SERVER_NAMESPACES)

# hooks.json・hooks.codex.jsonのPostToolUse matcherが被覆すべきagents_serverツール名の全体。
# 集合の一致を確かめるテスト（posttooluse_test.py）が実装側の集合として参照するため、下線接頭辞を付けない。
AGENTS_SERVER_HOOK_TOOL_NAMES = _AGENTS_SERVER_TOOL_NAMES | _AGENTS_SERVER_SHOW_TOOLS | _AGENTS_SERVER_LIST_TOOLS

_AGENTS_SERVER_SESSION_CWD_KEY = "agents_server_cwd_by_session"
_AGENTS_SERVER_SESSION_STATE_KEY = "agents_server_sessions"


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


def _extract_agents_server_structured_response(tool_response: object) -> dict:
    """agents_server応答のdictまたはJSON文字列を状態記録用へ正規化する。"""
    if isinstance(tool_response, dict):
        structured = tool_response.get("structuredContent")
        return structured if isinstance(structured, dict) else tool_response
    if isinstance(tool_response, str):
        try:
            parsed = json.loads(tool_response)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_nonempty_absolute_cwd(value: object) -> bool:
    """cwdが空白でない絶対パスであることを判定する。"""
    return isinstance(value, str) and bool(value.strip()) and pathlib.PurePath(value).is_absolute()


def _agents_server_remote_session_id(tool_input: object, structured: dict, tool_name: str) -> str | None:
    """操作ごとに識別子を保持するフィールドから委譲先session識別子を返す。"""
    source = structured if tool_name in _AGENTS_SERVER_START_TOOLS else tool_input
    value = source.get("session_id") if isinstance(source, dict) else None
    return value if isinstance(value, str) and value else None


def _agents_server_recorded_cwd(session_id: str, payload: dict, structured: dict, tool_name: str) -> object:
    """startの入力cwdまたはsessionごとのcwd mapから応答のcwd候補を取得する。"""
    tool_input = payload.get("tool_input")
    if tool_name in _AGENTS_SERVER_START_TOOLS:
        input_cwd = tool_input.get("cwd") if isinstance(tool_input, dict) else None
        return input_cwd if _is_nonempty_absolute_cwd(input_cwd) else None
    state = read_state(session_id)
    remote_session_id = _agents_server_remote_session_id(tool_input, structured, tool_name)
    cwd_map = state.get(_AGENTS_SERVER_SESSION_CWD_KEY)
    return cwd_map.get(remote_session_id) if isinstance(cwd_map, dict) else None


def _agents_server_model_type(tool_input: dict, operation: str) -> str | None:
    """開始操作の入力から工程別モデル設定の種別を返す。

    種別は`start`の`mode`から決める。統合前の起動ツール名で記録された呼び出しも同じmodeとして扱う。
    """
    model_type = tool_input.get("model_type")
    if isinstance(model_type, str):
        return model_type
    mode = _agents_server_tool_names.start_mode(operation, tool_input)
    if mode == "task":
        task_path = tool_input.get("subagent_md_path")
        return (
            _agents_server_state.TASK_MODEL_TYPES.get(pathlib.PurePath(task_path).name) if isinstance(task_path, str) else None
        )
    if mode is None:
        return None
    return _agents_server_tool_names.START_MODE_MODEL_TYPES.get(mode)


def _agents_server_missing_response_fields(session_id: str, payload: dict, structured: dict, tool_name: str) -> list[str]:
    """成功した応答から状態記録に必要な欠落項目を列挙する。"""
    operation = tool_name.rsplit("__", 1)[-1]
    if operation == "stop":
        return []
    missing: list[str] = []
    if not structured:
        missing.append("response")
    required_fields: tuple[str, ...]
    if tool_name in _AGENTS_SERVER_START_TOOLS:
        required_fields = ("session_id", "status", "root_session_id")
    elif tool_name in _AGENTS_SERVER_LIST_TOOLS:
        required_fields = ("root_session_id",)
    elif operation in {"wait", "kill"}:
        required_fields = ("status",)
    elif operation == "send_message":
        required_fields = ("delivery",)
    else:
        required_fields = ()
    for field in required_fields:
        value = structured.get(field)
        if (
            not isinstance(value, str)
            or not value.strip()
            or (field == "root_session_id" and not _agents_server_status_file.valid_session_id(value))
        ):
            missing.append(field)
    if tool_name in _AGENTS_SERVER_START_TOOLS and not _is_nonempty_absolute_cwd(
        _agents_server_recorded_cwd(session_id, payload, structured, tool_name)
    ):
        missing.append("cwd")
    return missing


def _live_child_session_cwds(structured: dict) -> list[tuple[str, str]]:
    """agents_serverの応答が返す稼働中の子sessionの識別子と`cwd`の対を取り出す。"""
    entries = structured.get("live_child_sessions")
    if not isinstance(entries, list):
        return []
    pairs: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        child_session_id = entry.get("session_id")
        child_cwd = entry.get("cwd")
        if isinstance(child_session_id, str) and child_session_id and isinstance(child_cwd, str) and child_cwd:
            pairs.append((child_session_id, child_cwd))
    return pairs


def _record_child_session_cwds(session_id: str, structured: dict) -> None:
    """応答が返した稼働中の子sessionの識別子と`cwd`の対を、続行判定が読むキーへ記録する。"""
    pairs = _live_child_session_cwds(structured)
    if not pairs:
        return

    def _mutator(state: dict) -> dict | None:
        cwd_map = state.setdefault(_AGENTS_SERVER_SESSION_CWD_KEY, {})
        changed = False
        for child_session_id, child_cwd in pairs:
            if cwd_map.get(child_session_id) != child_cwd:
                cwd_map[child_session_id] = child_cwd
                changed = True
        return state if changed else None

    update_state(session_id, _mutator)


def _record_agents_server_root_alias(session_id: str, structured: dict) -> None:
    """MCP応答が明示した所有rootを現行会話の別名索引へ記録する。"""
    if os.environ.get("AGENT_TOOLKIT_OWNER_SESSION"):
        return
    root_session_id = structured.get("root_session_id")
    if not _agents_server_status_file.valid_session_id(session_id):
        return
    if not isinstance(root_session_id, str) or not _agents_server_status_file.valid_session_id(root_session_id):
        return
    _agents_server_status_file.write_root_alias(session_id, root_session_id)


def _record_agents_server_session_state(
    session_id: str,
    structured: dict,
    *,
    operation: str,
    owner_agent_id: str,
    cwd: str | None = None,
    model_type: str | None = None,
    remote_session_id: str | None = None,
    fallback_status_host_session: str | None = None,
) -> str | None:
    """agents_serverの公開応答をhook側の状態へ記録する。"""
    if remote_session_id is None:
        value = structured.get("session_id")
        remote_session_id = value if isinstance(value, str) and value else None
    if remote_session_id is None:
        return None

    def _mutator(state: dict) -> dict | None:
        sessions = state.setdefault(_AGENTS_SERVER_SESSION_STATE_KEY, {})
        previous = sessions.get(remote_session_id)
        previous = previous if isinstance(previous, dict) else {}
        status = structured.get("status")
        if operation == "send_message":
            delivery = structured.get("delivery")
            status = "running" if delivery in {"reply_started", "reply_ambiguous"} else previous.get("status")
        if not isinstance(status, str):
            return None
        record = dict(previous)
        record.pop("cwd", None)
        record.pop("_".join(("result", "retrieved")), None)
        record.update({"session_id": remote_session_id, "status": status})
        if model_type is not None:
            record["model_type"] = model_type
        if operation in _AGENTS_SERVER_START_OPERATIONS:
            record["pending_observation"] = True
            record["owner_agent_id"] = owner_agent_id
        elif operation == "send_message":
            delivery = structured.get("delivery")
            # steerはturn_seqを変えず、そのturnの終端を既存の観測が待つ。
            if delivery in {"reply_started", "reply_ambiguous"}:
                record["pending_observation"] = True
                record["owner_agent_id"] = owner_agent_id
        elif operation == "kill":
            record["pending_observation"] = False
        kill_requested = structured.get("kill_requested")
        if isinstance(kill_requested, bool):
            record["kill_requested"] = kill_requested
        turn_id = structured.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            record["turn_id"] = turn_id
        if status == "running":
            record.pop("error", None)
        elif structured.get("error") is not None:
            record["error"] = structured["error"]
        if structured.get("agent_message") is not None:
            record["agent_message"] = structured["agent_message"]
        changed = sessions.get(remote_session_id) != record
        if changed:
            sessions[remote_session_id] = record
        cwd_map = state.setdefault(_AGENTS_SERVER_SESSION_CWD_KEY, {})
        if isinstance(cwd, str) and cwd and cwd_map.get(remote_session_id) != cwd:
            cwd_map[remote_session_id] = cwd
            changed = True
        # 応答が返した稼働中の子sessionも、識別子を記録する処理で`cwd`も記録する。
        # 返却する識別子の集合と、追送・打ち切りの許可判定の入力の集合を一致させるためである。
        for child_session_id, child_cwd in _live_child_session_cwds(structured):
            if cwd_map.get(child_session_id) != child_cwd:
                cwd_map[child_session_id] = child_cwd
                changed = True
        return state if changed else None

    update_state(session_id, _mutator)
    starts_reply = operation == "send_message" and structured.get("delivery") in {"reply_started", "reply_ambiguous"}
    if operation in _AGENTS_SERVER_START_OPERATIONS or starts_reply:
        # `atk agents wait`と同じ解決（別名索引を経たルート）で登録し、登録と読み取りの名前空間を一致させる。
        try:
            identity = _agents_server_status_file.resolve_wait_identity(os.environ, None)
            if (
                identity is None
                and os.environ.get("AGENT_TOOLKIT_OWNER_SESSION")
                and fallback_status_host_session is not None
                and _agents_server_status_file.valid_session_id(fallback_status_host_session)
            ):
                environment = dict(os.environ)
                environment["AGENT_TOOLKIT_STATUS_HOST_SESSION"] = fallback_status_host_session
                identity = _agents_server_status_file.resolve_wait_identity(environment, None)
        except ValueError as error:
            return f"agents_serverの待機対象を登録できない: {error}"
        if identity is not None and _agents_server_status_file.valid_session_id(remote_session_id):
            _agents_server_status_file.retain_wait_targets(
                identity.root_session_id,
                identity.file_name,
                [remote_session_id],
            )
    return None


def _remove_agents_server_session_record(session_id: str, remote_session_id: str | None) -> None:
    """破棄したsessionの記録を状態キーから除去する。

    `stop`は実行中turnを持つsessionと非終端のsessionを拒否するため、その成功応答は
    そのsessionが終端済み、期限切れまたは既破棄のいずれかであることを含意する。
    """
    if remote_session_id is None:
        return

    def _mutator(state: dict) -> dict | None:
        sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
        if not isinstance(sessions, dict) or remote_session_id not in sessions:
            return None
        del sessions[remote_session_id]
        return state

    update_state(session_id, _mutator)


def _log_tracked_session_end(session_id: str, structured: dict, remote_session_id: str | None = None) -> None:
    """終端した計画実行系sessionの終了時刻をprocess-loopの観測ログへ記録する。

    `model_type`は`start`応答にだけ現れるため、起動時に保持した記録から取得する。
    """
    if structured.get("status") not in _agents_server_state.TERMINAL_STATUSES:
        return
    if remote_session_id is None:
        value = structured.get("session_id")
        remote_session_id = value if isinstance(value, str) and value else None
    sessions = read_state(session_id).get(_AGENTS_SERVER_SESSION_STATE_KEY)
    record = sessions.get(remote_session_id) if isinstance(sessions, dict) else None
    model_type = record.get("model_type") if isinstance(record, dict) else None
    if model_type in _TRACKED_MODEL_TYPES:
        _process_loop_log.append("subagent_end", session_id=session_id, type=model_type)


def _clear_agents_server_pending_observation(session_id: str, owner_agent_id: str) -> None:
    """呼出主体が所有する全sessionの観測待ちを解消する。

    待機は対象sessionを入力に持たず、呼出主体が保持するsession全体を観測するため、
    所有者が一致する既存記録の全件を対象とする。記録が無いsessionへ新規の記録は作成しない。
    """

    def _mutator(state: dict) -> dict | None:
        sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
        if not isinstance(sessions, dict):
            return None
        changed = False
        for record in sessions.values():
            if (
                isinstance(record, dict)
                and record.get("owner_agent_id") == owner_agent_id
                and record.get("pending_observation") is not False
            ):
                record["pending_observation"] = False
                changed = True
        return state if changed else None

    update_state(session_id, _mutator)


def _record_agents_server_observation_attempt(
    session_id: str,
    tool_input: dict,
    *,
    operation: str,
) -> None:
    """バックグラウンドタスクへ移った`kill`の移行通知から観測の試みだけを記録する。

    実行環境が呼び出しをバックグラウンドタスクへ移すと構造化応答が返らないため、応答の`session_id`と
    `status`を入力とする`_record_agents_server_session_state`は何も更新せずに戻る。
    呼び出しが受理された時点で観測を試みたものとして扱い、応答境界へ到達しない場合でも
    `pending_observation`を偽にする。`tool_input`の`session_id`で解決した既存記録に限り、
    記録が無いsessionへ新規の記録を作成しない。`status`・`turn_id`・`kill_requested`などの
    公開状態は移行通知から確定できないため更新しない。
    """
    if operation != "kill":
        return
    remote_session_id = tool_input.get("session_id")
    if not isinstance(remote_session_id, str) or not remote_session_id:
        return

    def _mutator(state: dict) -> dict | None:
        sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
        if not isinstance(sessions, dict):
            return None
        record = sessions.get(remote_session_id)
        if not isinstance(record, dict) or record.get("pending_observation") is False:
            return None
        record["pending_observation"] = False
        return state

    update_state(session_id, _mutator)


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
            if candidate and is_plan_component_file(candidate):
                _record_plan_file(session_id, candidate)
                return


def _record_agents_wait_observation_attempt(session_id: str, command: str, owner_agent_id: str) -> None:
    """成功したBash入力内の`atk agents wait`を観測の試みとして記録する。"""
    if not any(segment.resolved and is_agents_wait_command(segment.tokens) for segment in extract_execution_segments(command)):
        return
    _clear_agents_server_pending_observation(session_id, owner_agent_id)


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
    実行ホストが背景へ移した通知は`stop_gate.background_task_id_from_notice`が扱う。
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
    記録の契機は、Bashの背景実行が成功した応答、同じ指定で失敗した応答、
    ツール種別を問わないバックグラウンドタスクへの移行通知およびAgent・Taskの背景起動が返した`agentId`の4つとする。
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


def _record_background_task_output(session_id: str, response: object) -> None:
    """バックグラウンドタスクIDとホストが示した出力先を同じsession状態へ記録する。"""
    pair = _background_task_outputs.task_output_from_response(response)
    if pair is None:
        return
    task_id, output_path = pair

    def _record(state: dict) -> dict | None:
        recorded = state.get("background_task_output_paths")
        recorded = dict(recorded) if isinstance(recorded, dict) else {}
        if recorded.get(task_id) == output_path:
            return None
        recorded[task_id] = output_path
        state["background_task_output_paths"] = recorded
        return state

    update_state(session_id, _record)


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


def _handle_edit_tool(
    session_id: str,
    tool_name: str,
    tool_input: dict,
    cwd: str,
    notices: list[str],
) -> None:
    """編集成功後の計画ファイル記録と計画の構造を確かめるコマンドの案内を処理する。

    ClaudeのWrite・Edit・MultiEditとCodexの成功した`apply_patch`を
    `_hook_tool_input`が共通の操作記録へ変換する。
    実ファイルの読み込みを伴う処理は、適用後に存在する対象（追加・更新・移動先）へ限定する。
    """
    operations = _hook_tool_input.parse_operations(tool_name, tool_input, cwd)
    if operations is None:
        return
    plan_mode_invoked = bool(read_state(session_id).get("plan_mode_skill_invoked", False))
    for operation in operations:
        if not operation.exists_after_apply:
            continue
        display_path = operation.display_path
        if is_plan_main_file(display_path):
            _record_plan_file(session_id, display_path)
        if plan_mode_invoked and is_plan_component_file(display_path) and operation.is_whole_write:
            notices.append(_plan_file_check_notice(_plan_main_path_for(display_path), cwd))


def _plan_main_path_for(display_path: str) -> str:
    """現行計画の構成要素から計画ファイル（メイン）の絶対パスを返す。"""
    return display_path


def _plan_file_check_notice(file_path: str, cwd: str) -> str:
    """計画ファイル全文書き込み後に実行する計画の構造を確かめるコマンドの案内文を返す。"""
    project_root = pathlib.Path(__file__).resolve().parents[2]
    check_script = project_root / "skills/plan-mode/scripts/check_plan_file.py"
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


def _handle_bash_tool(session_id: str, command: str, *, owner_agent_id: str) -> None:
    """成功したBashコマンドから`atk agents wait`の観測の試みを記録する。"""
    _record_agents_wait_observation_attempt(session_id, _strip_command_prefixes(command), owner_agent_id)


def _dispatch(payload_text: str, notices: list[str]) -> int:
    """payloadを解析し、通知本文を`notices`へ蓄積する。終了コードは常に0。"""
    parsed = _parse_hook_payload(payload_text)
    if parsed is None:
        return 0
    payload, session_id, tool_name, tool_input, cwd, event_name = parsed
    set_warning_session_id(session_id)

    # 所有の根拠は、自セッションのツール呼び出しの応答がバックグラウンドタスク識別子を返したことである。
    # 起動の成否は所有の有無を変えないため、バックグラウンドタスクへの移行通知はツール種別と成否によらず記録する。
    notice_task_id = _stop_gate.background_task_id_from_notice(payload.get("tool_response"))
    if notice_task_id is not None:
        _record_background_task_id(session_id, notice_task_id)
    if tool_input.get("run_in_background") or notice_task_id is not None:
        _record_background_task_output(session_id, payload.get("tool_response"))

    if event_name == "PostToolUseFailure":
        if tool_input.get("run_in_background"):
            failed_task_id = _background_task_id_from_response(payload.get("tool_response"))
            if failed_task_id is not None:
                _record_background_task_id(session_id, failed_task_id)
        return 0

    # 対象リポジトリで新たに回答されたUWIファイルがある場合に通知する。
    # ツール種別に依らず回答を確認し、ユーザーの回答から通知までの遅延を抑える。
    # この通知が指示する反映と依存作業の再開はメインが所有するため、
    # in-processのサブエージェントと`agents_server`の委譲先セッションでは通知を組み立てない。
    if cwd and is_main_agent_context(payload):
        uwi_notice = _uwi_completion.build_notice(session_id, cwd, resolve_hook_agent_id(payload))
        if uwi_notice is not None:
            notices.append(_llm_notice(uwi_notice, tag="notice"))

    # Skill: plan-modeスキル呼び出し検出とprocess-wi起動検出
    if tool_name == "Skill":
        _record_skill_use(session_id, tool_input.get("skill"))
        _operation_skills.record_skill_ready(session_id, tool_input.get("skill"), resolve_hook_agent_id(payload))
        return 0

    # AgentとTask: 背景起動の応答が返した`agentId`だけをバックグラウンドタスクの所有記録へ残す。後続の分岐は対象としない
    if tool_name in ("Agent", "Task"):
        agent_id = _stop_gate.async_agent_launch_id(payload.get("tool_response"))
        if agent_id is not None:
            _record_background_task_id(session_id, agent_id)
        return 0

    # agents_serverの応答が`root_session_id`を持てば、操作名によらずここで1回だけ別名索引へ記録する。
    # 後続の待機対象の登録より前に置き、再起動後の最初の操作が`send_message`でも、
    # 登録と`atk agents wait`が同じルートを使えるようにする。
    if tool_name in AGENTS_SERVER_HOOK_TOOL_NAMES:
        _record_agents_server_root_alias(
            session_id, _extract_agents_server_structured_response(payload.get("tool_response", {}))
        )

    # showの応答が返す稼働中の子sessionは、識別子と`cwd`の対だけを記録する。
    # session記録そのものはそのsessionを起動した主体が持つため、ここでは更新しない。
    if tool_name in _AGENTS_SERVER_SHOW_TOOLS:
        structured = _extract_agents_server_structured_response(payload.get("tool_response", {}))
        _record_child_session_cwds(session_id, structured)
        return 0

    # listはsession状態を変更しない。応答が明示した所有rootの別名索引への記録は前段が行う。
    if tool_name in _AGENTS_SERVER_LIST_TOOLS:
        structured = _extract_agents_server_structured_response(payload.get("tool_response", {}))
        missing = _agents_server_missing_response_fields(session_id, payload, structured, tool_name)
        if missing:
            notices.append(
                _llm_notice(
                    f"warn: `list`の応答で{', '.join(missing)}が欠落しているか不正である。"
                    "このセッションは別名索引へ記録されていない。",
                    tag=_WARN_TAG,
                    fix="`show`で同じセッションを再取得する。欠落が続く場合は`agents_server`の不具合としてユーザーへ報告する。",
                    removable_cause=False,
                )
            )
        return 0

    # agents_server応答からsession_id→cwdを保存し、session状態を更新する。
    if tool_name in _AGENTS_SERVER_TOOL_NAMES:
        tool_response = payload.get("tool_response", {})
        structured = _extract_agents_server_structured_response(tool_response)
        moved_to_background = notice_task_id is not None
        operation = tool_name.rsplit("__", 1)[-1]
        owner_agent_id = resolve_hook_agent_id(payload)
        if tool_name in _AGENTS_SERVER_DIAGNOSTIC_TOOLS and not moved_to_background:
            missing = _agents_server_missing_response_fields(session_id, payload, structured, tool_name)
            if missing:
                display_name = tool_name.rsplit("__", 1)[-1]
                notices.append(
                    _llm_notice(
                        f"warn: {display_name}の応答で{', '.join(missing)}が欠落しているか不正である。"
                        "観測待ちの記録が欠けるため、ターン終了時の未観測警告が出ない。",
                        tag=_WARN_TAG,
                        fix=(
                            "起動したセッションは`atk agents wait`で終端を観測するか、結果が不要なら`kill`で破棄する。"
                            "欠落が続く場合は`agents_server`の不具合としてユーザーへ報告する。"
                        ),
                        removable_cause=False,
                    )
                )
        remote_session_id = _agents_server_remote_session_id(tool_input, structured, tool_name)
        if moved_to_background:
            _record_agents_server_observation_attempt(
                session_id,
                tool_input,
                operation=operation,
            )
            return 0
        cwd_value = _agents_server_recorded_cwd(session_id, payload, structured, tool_name)
        if tool_name in _AGENTS_SERVER_START_TOOLS:
            model_type = _agents_server_model_type(tool_input, operation)
            if model_type in _TRACKED_MODEL_TYPES:
                _process_loop_log.append("subagent_start", session_id=session_id, type=model_type)
            warning = _record_agents_server_session_state(
                session_id,
                structured,
                operation=operation,
                owner_agent_id=owner_agent_id,
                cwd=cwd_value if isinstance(cwd_value, str) else None,
                model_type=model_type,
                remote_session_id=remote_session_id,
                fallback_status_host_session=session_id if tool_name.startswith("mcp__agents_server__") else None,
            )
            if warning is not None:
                notices.append(_llm_notice(warning, tag=_WARN_TAG, fix=_UNREGISTERED_WAIT_TARGET_FIX, removable_cause=False))
        elif operation == "stop":
            _remove_agents_server_session_record(session_id, remote_session_id)
        else:
            if operation in {"wait", "kill"}:
                _log_tracked_session_end(session_id, structured, remote_session_id)
            warning = _record_agents_server_session_state(
                session_id,
                structured,
                operation=operation,
                owner_agent_id=owner_agent_id,
                remote_session_id=remote_session_id,
                fallback_status_host_session=session_id if tool_name.startswith("mcp__agents_server__") else None,
            )
            if warning is not None:
                notices.append(_llm_notice(warning, tag=_WARN_TAG, fix=_UNREGISTERED_WAIT_TARGET_FIX, removable_cause=False))
        return 0

    if tool_name == "TaskStop":
        consume_completion(session_id, target_ids(tool_input))
        return 0

    if tool_name in ("Write", "Edit", "MultiEdit", _hook_tool_input.CODEX_APPLY_PATCH_TOOL):
        _handle_edit_tool(session_id, tool_name, tool_input, cwd, notices)
        return 0

    # Bash以外はここで終了
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return 0
    if tool_input.get("run_in_background"):
        task_id = _background_task_id_from_response(payload.get("tool_response"))
    else:
        # 実行上限に達した前景のBashを実行ホストが背景へ移した場合も、応答は構造化`backgroundTaskId`を返す。
        # このタスクは自セッションが起動したものであり、TaskStopの所有判定へ含める。
        task_id = _structured_background_task_id(payload.get("tool_response"))
    if task_id is not None:
        _record_background_task_id(session_id, task_id)

    _handle_bash_tool(session_id, command, owner_agent_id=resolve_hook_agent_id(payload))
    _record_bash_response_state(session_id, command, payload.get("tool_response"))
    return 0


def main(payload_text: str) -> int:
    """エントリポイント。終了コードは常に0。

    フック応答はstdout全体を1つのJSONとして解析されるため、蓄積した通知本文を
    改行で連結して1回だけ出力する。分岐ごとの出力は複数JSONの生成につながる。
    """
    notices: list[str] = []
    exit_code = _dispatch(payload_text, notices)
    try:
        termination_evidence.observe_tool(payload_text, after=True)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"終了工程の実行結果を取得できない: {error}", file=sys.stderr)
    if notices:
        try:
            event_name = json.loads(payload_text).get("hook_event_name", "PostToolUse")
        except (json.JSONDecodeError, ValueError, AttributeError):
            event_name = "PostToolUse"
        if event_name not in {"PostToolUse", "PostToolUseFailure"}:
            event_name = "PostToolUse"
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event_name,
                        "additionalContext": "\n".join(notices),
                    }
                },
                ensure_ascii=False,
            )
        )
    return exit_code
