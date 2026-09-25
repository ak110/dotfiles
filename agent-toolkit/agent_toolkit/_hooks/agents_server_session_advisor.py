"""観測を試みていないagents_serverの作業をStop時に警告する。

`start`・`start_explore`・`start_shell`と新しいturnを起こす`send_message`は、委譲先に
新しい作業を発生させる。実行中turnへの`steered`配送は新しい作業の発生に含めない。
PostToolUseが当該応答と呼出主体を`agents_server_sessions`へ記録し、
本フックは`pending_observation`が真で、呼出主体が一致する記録だけを警告対象にする。

判定対象は結果の回収状態ではなく、観測を試みていない作業の有無である。
観測義務を解消する条件は次の4つとする。
第1に、`atk agents wait`が待機所有権を保持し、当該sessionを待機対象として登録している間は観測中として扱う。
第2に、同コマンドの終了後はPostToolUseが`pending_observation`を解消する。
第3に、`kill`は結果を意図的に破棄するため同じ状態を解消する。
第4に、直前の応答が`待機中: <当該session識別子>`の形で当該sessionを待機対象として指す場合は、
自動再開が当該sessionの結果を受け取る経路が成立しているため解消する。
本フックは当該sessionの記録を解消済みとして保存し、自動再開で結果を受け取った後のturnの終了でも警告しない。
この4項は、`agent-toolkit/share/rules-subagent.md`「委譲時の厳守事項」が正しい終端として許容する形を被覆する契約を持つ。
当該規範が許容する終端の形を変える改訂では、同じ変更単位で本列挙と`evaluate`の除外条件を追随させる。
実行環境が待機・中断を背景タスクへ移し、
構造化応答を伴わない移行通知だけを返した場合も解消契機に含める。
一度解消したsessionでも、
`send_message`が新しい作業を配送すれば再び警告対象になる。

警告は`hookSpecificOutput.additionalContext`で当該ターンを継続させる。
`stop_hook_active`が真の再呼び出し、payload不正、状態不在・破損時は何も出力せず
終了を許可し、警告の反復で終了不能になることを避ける。

委譲先での実行可否: 委譲先も`wait`と`kill`を実行できるため、除外せず警告できる。
"""

import json
import os
import re

from agent_toolkit._agents_server import status_file
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
from agent_toolkit._hooks.notice import _WARN_TAG, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter
from agent_toolkit._hooks.session_state import read_state, update_state
from agent_toolkit._hooks.stop_gate import parse_stop_session

_HOOK_ID = "agent-toolkit/agents_server_session_advisor"
_SESSION_STATE_KEY = "agents_server_sessions"
_WARNING_BODY = (
    "`agents_server`の`session`に、観測を試みていない作業が残っている。"
    "実行ホストの`atk agents wait`で観測するか、結果が不要なら`kill(session_id)`で破棄してから終了する。"
    "`send_message`は新しい作業を配送するだけで観測しないため、この警告は解消しない。"
    "観測しないまま終了すると、当該作業の成果を回収する主体が残らない。"
)

_notice = _notice_formatter(_HOOK_ID, default_tag=_WARN_TAG)

# 待機表明の1行。行頭が`待機中:`である行のコロン以降を待機対象の記述とする。
_WAITING_DECLARATION_PATTERN = re.compile(r"^待機中:(?P<targets>.*)$", re.MULTILINE)

# session識別子に現れない文字。待機対象の記述を識別子の語へ区切る。
# 前方一致での照合は別のsessionを指す待機表明で当該sessionの警告まで抑止するため用いない。
_TARGET_SEPARATOR_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")


def _approve() -> None:
    """出力せず終了を許可する。"""


def _pending_session_ids(state: dict, owner_agent_id: str) -> list[str]:
    """呼出主体が観測すべき作業の残るsession識別子を返す。"""
    sessions = state.get(_SESSION_STATE_KEY)
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


def _actively_waited_session_ids(session_ids: list[str]) -> set[str]:
    """待機中の主体が待機対象としているsession識別子を返す。

    待機所有権は待機主体を単位とし、`atk agents wait`は
    `wait-locks/<待機主体のstatusファイル名>.lock`を保持して
    `wait-targets/<待機主体のstatusファイル名>/<対象session識別子>.json`へ対象を登録する。
    判定側も同じ単位で読み、対象session識別子を名前とするロックを探さない。
    """
    root_session_id = status_file.resolve_conversation_root_session_id(os.environ)
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
    lock_directory = status_file.status_directory(root_session_id) / "wait-locks"
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

    当該登録簿は`atk agents wait`が所有するため、本フックは読むだけで内容を変更しない。
    """
    directory = status_file.wait_targets_directory(root_session_id, owner_status_file)
    try:
        return {path.stem for path in directory.glob("*.json")}
    except OSError:
        return set()


def _declared_waiting_target_ids(last_assistant_message: object) -> set[str]:
    """直前の応答の待機表明が待機対象として指す識別子を返す。

    Stop payloadの`last_assistant_message`をそのまま受け取る。
    文字列でない場合と待機表明が無い場合は空集合を返す。
    """
    if not isinstance(last_assistant_message, str):
        return set()
    targets: set[str] = set()
    for match in _WAITING_DECLARATION_PATTERN.finditer(last_assistant_message):
        targets.update(token for token in _TARGET_SEPARATOR_PATTERN.split(match.group("targets")) if token)
    return targets


def _resolve_declared_sessions(session_id: str, owner_agent_id: str, declared: set[str]) -> None:
    """待機表明が指すsessionの観測待ちを解消済みとして保存する。

    表明の時点で自動再開が結果を受け取る経路が成立するため、以降のturnの終了では同じsessionを警告しない。
    `send_message`が新しい作業を配送した場合はPostToolUseが観測待ちへ戻す。
    """

    def _mutator(state: dict) -> dict | None:
        sessions = state.get(_SESSION_STATE_KEY)
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


def evaluate(payload_text: str) -> tuple[str, str]:
    """未観測作業の判定結果と、警告する場合の本文を返す。"""
    resolved = parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    set_warning_session_id(session_id)
    if payload.get("stop_hook_active") is True:
        return "approve", ""

    owner_agent_id = resolve_hook_agent_id(payload)
    pending_session_ids = _pending_session_ids(read_state(session_id), owner_agent_id)
    observed = _actively_waited_session_ids(pending_session_ids)
    declared = _declared_waiting_target_ids(payload.get("last_assistant_message")) & set(pending_session_ids)
    if declared:
        _resolve_declared_sessions(session_id, owner_agent_id, declared)
    observed |= declared
    pending_session_ids = [item for item in pending_session_ids if item not in observed]
    if not pending_session_ids:
        return "approve", ""

    body = f"{_WARNING_BODY}\n対象session: {', '.join(pending_session_ids)}"
    return "notify", _notice(body, removable_cause=True, summary=body)


def main(payload_text: str) -> int:
    """Stop payloadとセッション状態から未観測作業を警告する。"""
    decision, body = evaluate(payload_text)
    if decision == "notify":
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "Stop",
                        "additionalContext": body,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0
