"""agents_serverの観測の記録の書き込みと読み取り。

記録はセッション状態の`agents_server_sessions`（委譲先sessionごとの公開状態、観測待ち`pending_observation`と
呼出主体`owner_agent_id`）と`agents_server_cwd_by_session`（委譲先sessionごとの`cwd`）である。
PostToolUseがagents_serverのMCPツールと`atk agents wait`の実行結果を本モジュールで記録し、
Stopの未観測作業の警告（`agents_server_session_advisor`）・Stopの継続判定（`background_tasks`）・
終了工程の証拠（`termination_evidence`）・PreToolUseの続行判定（`pretooluse/agent_checks`）が同じ記録を本モジュールで読む。
記録の形を知るモジュールを1つにし、書き手と読み手の食い違いを防ぐ。
"""

import dataclasses
import json
import os
import pathlib

from agent_toolkit._agents_server import launch_prompts, shared_layout, shared_roots, wait_targets
from agent_toolkit._agents_server import state as _agents_server_state
from agent_toolkit._agents_server import tool_names as _agents_server_tool_names
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._common.shell_segments import extract_execution_segments
from agent_toolkit._common.shell_tokens import is_agents_wait_command
from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
from agent_toolkit._hooks.tracked_model_types import TRACKED_MODEL_TYPES as _TRACKED_MODEL_TYPES


@dataclasses.dataclass(frozen=True)
class ObservationWarning:
    """記録できなかった項目の警告。PostToolUseが通知へ整形する。"""

    body: str
    fix: str


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


_UNREGISTERED_WAIT_TARGET_FIX = (
    "`atk agents wait`はこのセッションを待機対象として扱わないため、終端は`agents_server`の`show`で確認する。"
)
"""待機所有者を識別できず待機対象を登録できなかったsessionの次の操作。"""


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
        return launch_prompts.TASK_MODEL_TYPES.get(pathlib.PurePath(task_path).name) if isinstance(task_path, str) else None
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
            or (field == "root_session_id" and not shared_layout.valid_session_id(value))
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
    if _delegated_session.owner_session_id(os.environ) is not None:
        return
    root_session_id = structured.get("root_session_id")
    if not shared_layout.valid_session_id(session_id):
        return
    if not isinstance(root_session_id, str) or not shared_layout.valid_session_id(root_session_id):
        return
    shared_roots.write_root_alias(session_id, root_session_id)


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
            identity = shared_roots.resolve_wait_identity(os.environ, None)
            if (
                identity is None
                and _delegated_session.owner_session_id(os.environ) is not None
                and fallback_status_host_session is not None
                and shared_layout.valid_session_id(fallback_status_host_session)
            ):
                environment = dict(os.environ)
                environment["AGENT_TOOLKIT_STATUS_HOST_SESSION"] = fallback_status_host_session
                identity = shared_roots.resolve_wait_identity(environment, None)
        except ValueError as error:
            return f"agents_serverの待機対象を登録できない: {error}"
        if identity is not None and shared_layout.valid_session_id(remote_session_id):
            wait_targets.retain_wait_targets(
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


def record_wait_observation_attempt(session_id: str, command: str, owner_agent_id: str) -> None:
    """成功したBash入力内の`atk agents wait`を観測の試みとして記録する。"""
    if not any(segment.resolved and is_agents_wait_command(segment.tokens) for segment in extract_execution_segments(command)):
        return
    _clear_agents_server_pending_observation(session_id, owner_agent_id)


def observe_tool(
    payload: dict, session_id: str, tool_name: str, tool_input: dict, *, moved_to_background: bool
) -> list[ObservationWarning]:
    """agents_serverのツールの実行結果を観測の記録へ反映し、記録できなかった項目の警告を返す。

    `moved_to_background`は実行環境が呼び出しをバックグラウンドタスクへ移し、構造化応答を返さなかったことを示す。
    """
    if tool_name not in AGENTS_SERVER_HOOK_TOOL_NAMES:
        return []
    structured = _extract_agents_server_structured_response(payload.get("tool_response", {}))
    # agents_serverの応答が`root_session_id`を持てば、操作名によらずここで1回だけ別名索引へ記録する。
    # 後続の待機対象の登録より前に置き、再起動後の最初の操作が`send_message`でも、
    # 登録と`atk agents wait`が同じルートを使えるようにする。
    _record_agents_server_root_alias(session_id, structured)

    # showの応答が返す稼働中の子sessionは、識別子と`cwd`の対だけを記録する。
    # session記録そのものはそのsessionを起動した主体が持つため、ここでは更新しない。
    if tool_name in _AGENTS_SERVER_SHOW_TOOLS:
        _record_child_session_cwds(session_id, structured)
        return []

    # listはsession状態を変更しない。応答が明示した所有rootの別名索引への記録は前段が行う。
    if tool_name in _AGENTS_SERVER_LIST_TOOLS:
        missing = _agents_server_missing_response_fields(session_id, payload, structured, tool_name)
        if not missing:
            return []
        return [
            ObservationWarning(
                f"warn: `list`の応答で{', '.join(missing)}が欠落しているか不正である。"
                "このセッションは別名索引へ記録されていない。",
                "`show`で同じセッションを再取得する。欠落が続く場合は`agents_server`の不具合としてユーザーへ報告する。",
            )
        ]

    # agents_server応答からsession_id→cwdを保存し、session状態を更新する。
    warnings: list[ObservationWarning] = []
    operation = tool_name.rsplit("__", 1)[-1]
    owner_agent_id = resolve_hook_agent_id(payload)
    if tool_name in _AGENTS_SERVER_DIAGNOSTIC_TOOLS and not moved_to_background:
        missing = _agents_server_missing_response_fields(session_id, payload, structured, tool_name)
        if missing:
            warnings.append(
                ObservationWarning(
                    f"warn: {operation}の応答で{', '.join(missing)}が欠落しているか不正である。"
                    "観測待ちの記録が欠けるため、ターン終了時の未観測警告が出ない。",
                    "起動したセッションは`atk agents wait`で終端を観測するか、結果が不要なら`kill`で破棄する。"
                    "欠落が続く場合は`agents_server`の不具合としてユーザーへ報告する。",
                )
            )
    remote_session_id = _agents_server_remote_session_id(tool_input, structured, tool_name)
    if moved_to_background:
        _record_agents_server_observation_attempt(session_id, tool_input, operation=operation)
        return warnings
    fallback_status_host_session = session_id if tool_name.startswith("mcp__agents_server__") else None
    if tool_name in _AGENTS_SERVER_START_TOOLS:
        cwd_value = _agents_server_recorded_cwd(session_id, payload, structured, tool_name)
        model_type = _agents_server_model_type(tool_input, operation)
        if model_type in _TRACKED_MODEL_TYPES:
            _process_loop_log.append("subagent_start", session_id=session_id, type=model_type)
        failure = _record_agents_server_session_state(
            session_id,
            structured,
            operation=operation,
            owner_agent_id=owner_agent_id,
            cwd=cwd_value if isinstance(cwd_value, str) else None,
            model_type=model_type,
            remote_session_id=remote_session_id,
            fallback_status_host_session=fallback_status_host_session,
        )
    elif operation == "stop":
        _remove_agents_server_session_record(session_id, remote_session_id)
        failure = None
    else:
        if operation in {"wait", "kill"}:
            _log_tracked_session_end(session_id, structured, remote_session_id)
        failure = _record_agents_server_session_state(
            session_id,
            structured,
            operation=operation,
            owner_agent_id=owner_agent_id,
            remote_session_id=remote_session_id,
            fallback_status_host_session=fallback_status_host_session,
        )
    if failure is not None:
        warnings.append(ObservationWarning(failure, _UNREGISTERED_WAIT_TARGET_FIX))
    return warnings


def session_record(state: dict, remote_session_id: object) -> dict | None:
    """観測の記録から委譲先sessionの記録を返す。記録が無ければ`None`を返す。"""
    sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
    if not isinstance(sessions, dict) or not isinstance(remote_session_id, str):
        return None
    record = sessions.get(remote_session_id)
    return record if isinstance(record, dict) else None


def session_cwd(state: dict, remote_session_id: str) -> str | None:
    """観測の記録から委譲先sessionの`cwd`を返す。記録が無ければ`None`を返す。"""
    cwd_map = state.get(_AGENTS_SERVER_SESSION_CWD_KEY)
    cwd = cwd_map.get(remote_session_id) if isinstance(cwd_map, dict) else None
    return cwd if isinstance(cwd, str) else None


def has_pending_owned_observation(state: object, owner_agent_id: str) -> bool:
    """呼出主体がまだ回収していないagents_server結果があれば真を返す。"""
    if not isinstance(state, dict):
        return False
    sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
    if not isinstance(sessions, dict):
        return False
    return any(
        isinstance(record, dict)
        and record.get("pending_observation") is True
        and record.get("owner_agent_id") == owner_agent_id
        for record in sessions.values()
    )


def pending_session_ids(state: dict, owner_agent_id: str) -> list[str]:
    """呼出主体が観測すべき作業の残るsession識別子を返す。"""
    sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
    if not isinstance(sessions, dict):
        return []
    return sorted(
        session_id
        for session_id, record in sessions.items()
        if isinstance(session_id, str)
        and isinstance(record, dict)
        and record.get("pending_observation") is True
        and record.get("owner_agent_id") == owner_agent_id
    )


def actively_waited_session_ids(session_ids: list[str]) -> set[str]:
    """待機中の主体が待機対象としているsession識別子を返す。

    待機所有権は待機主体を単位とし、`atk agents wait`は
    `wait-locks/<待機主体のstatusファイル名>.lock`を保持して
    `wait-targets/<待機主体のstatusファイル名>/<対象session識別子>.json`へ対象を登録する。
    判定側も同じ単位で読み、対象session識別子を名前とするロックを探さない。
    """
    root_session_id = shared_roots.resolve_conversation_root_session_id(os.environ)
    if root_session_id is None:
        return set()
    targets = set(session_ids)
    if not targets:
        return set()
    active: set[str] = set()
    for owner_status_file in _held_wait_lock_owners(root_session_id):
        active |= targets & _registered_wait_targets(root_session_id, owner_status_file)
    return active


def _held_wait_lock_owners(root_session_id: str) -> list[str]:
    """待機所有権のロックを別プロセスが保持している待機主体を返す。"""
    lock_directory = shared_layout.status_directory(root_session_id) / "wait-locks"
    try:
        lock_paths = sorted(lock_directory.glob("*.lock"))
    except OSError:
        return []
    owners: list[str] = []
    for lock_path in lock_paths:
        try:
            with lock_path.open("r+b") as lock_file:
                try:
                    acquire_lock(lock_file, blocking=False)
                except OSError:
                    owners.append(lock_path.name.removesuffix(".lock"))
                else:
                    release_lock(lock_file)
        except OSError:
            continue
    return owners


def _registered_wait_targets(root_session_id: str, owner_status_file: str) -> set[str]:
    """待機主体の登録簿に残る待機対象のsession識別子を返す。

    この登録簿は`atk agents wait`が所有するため、本フックは読むだけで内容を変更しない。
    """
    directory = shared_layout.wait_targets_directory(root_session_id, owner_status_file)
    try:
        return {path.stem for path in directory.glob("*.json")}
    except OSError:
        return set()


def resolve_declared_sessions(session_id: str, owner_agent_id: str, declared: set[str]) -> None:
    """待機表明が指すsessionの観測待ちを解消済みとして保存する。

    表明の時点で自動再開が結果を受け取る仕組みが成立するため、以降のturnの終了では同じsessionを警告しない。
    `send_message`が新しい作業を配送した場合はPostToolUseが観測待ちへ戻す。
    """

    def _mutator(state: dict) -> dict | None:
        sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
        if not isinstance(sessions, dict):
            return None
        changed = False
        for target in declared:
            record = sessions.get(target)
            if (
                isinstance(record, dict)
                and record.get("owner_agent_id") == owner_agent_id
                and record.get("pending_observation") is True
            ):
                record["pending_observation"] = False
                changed = True
        return state if changed else None

    update_state(session_id, _mutator)
