"""観測を試みていないagents_serverの作業をStop時に警告する。

`start`（全mode）と新しいturnを起こす`send_message`は、委譲先に
新しい作業を発生させる。実行中turnへの`steered`配送は新しい作業の発生に含めない。
PostToolUseがその応答と呼出主体を`agents_server_sessions`へ記録し、
本フックは`pending_observation`が真で、呼出主体が一致する記録を警告対象にする。
観測を試みた後にも同じ主体の待機対象登録へ残る実行中sessionは、既存の登録簿から別に解決する。
有効なCLI待機も同sessionの自動再開もなければ、現在の待機を継続する非遮断の案内を返す。

観測の試みと現在の継続主体は別に判定する。
`pending_observation`を解消するのは、実際に観測・破棄した証拠だけとする。
`atk agents wait`の終了と`kill`の受理（実行環境が呼び出しをバックグラウンドタスクへ移し、
構造化応答を伴わない移行通知だけを返した場合を含む）をPostToolUseが記録する。
`send_message`の受理と待機表明は観測ではないため解消しない。
一度解消したsessionでも、`send_message`が新しい作業を配送すれば再び警告対象になる。

保存状態を変えずに、そのStopの判定に限って警告から除く場合は次の2つとする。
第1に、`atk agents wait`が待機所有権を保持し、そのsessionを待機対象として登録している間は観測中として扱う。
第2に、直前の応答が`待機中: <session識別子>`の形でそのsessionを指し、かつ現在のセッションが
agents_serverのClaude・Codex委譲先である場合は除く。agents_serverはこのセッションが起動した子を追跡し、
全終了後に同じセッションを自動再開するため、結果を回収する主体が残る。
継続を担う主体を確認できない起動形態（`atk run-skill`の単一入力起動やメインなど）では、待機表明だけでは除かない。
Antigravityの委譲先は子の起動結果を追跡へ接続しないため、委譲先であっても除かない。

警告は`hookSpecificOutput.additionalContext`でそのターンを継続させる。
`stop_hook_active`が真の再呼び出し、payload不正、状態不在・破損時は何も出力せず
終了を許可し、警告の反復で終了不能になることを避ける。

委譲先での実行可否: 委譲先も`wait`と`kill`を実行できるため、除外せず警告できる。
"""

import os
import re

from agent_toolkit._hooks.notice import _WARN_TAG, resolve_notice, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter
lazy from agent_toolkit._agents_server.continuation_wait import continuation_wait_seconds
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common.session_state import read_state
lazy from agent_toolkit._hooks import agents_server_observations as _observations
lazy from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks.stop_session import parse_stop_session

_HOOK_ID = "agents_server_session_advisor"
_WARNING_BODY = "`agents_server`の`session`に、観測を試みていない作業が残っている。"
_WARNING_FIX = "実行ホストの`atk agents wait`で観測するか、結果が不要なら`kill(session_id)`で破棄してから終了してください。"

_CLAUDE_WARNING_FIX = (
    "バックグラウンド実行が使えるなら、実行ホストで引数なしの `atk agents wait` を単独でバックグラウンド起動し、"
    "起動を確認してからターンを終えてください。バックグラウンド実行が使えないなら同じターンでフォアグラウンド回収してください。"
    "結果が不要なら`kill(session_id)`で破棄してから終了してください。"
)

_notice = _notice_formatter(_HOOK_ID, default_tag=_WARN_TAG)

# 待機表明の1行。行頭が`待機中:`である行のコロン以降を待機対象の記述とする。
_WAITING_DECLARATION_PATTERN = re.compile(r"^待機中:(?P<targets>.*)$", re.MULTILINE)

# session識別子に現れない文字。待機対象の記述を識別子の語へ区切る。
# 前方一致による判定は別のsessionを指す待機表明でそのsessionの警告まで抑止するため用いない。
_TARGET_SEPARATOR_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")


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


def _continuation_owned(payload: dict) -> bool:
    """待機表明の後に、同じセッションを自動再開して子の結果を回収させる主体が起動形態から確かめられるかを返す。"""
    return _delegated_session.is_delegated(os.environ) and not is_antigravity_payload(payload)


def _observation_status(state: dict, owner_agent_id: str) -> tuple[list[str], list[str], dict[str, int]]:
    pending_session_ids = _observations.pending_session_ids(state, owner_agent_id)
    continued = _observations.retained_running_session_ids(state, owner_agent_id)
    pending_session_ids = sorted(set(pending_session_ids) | set(continued))
    sessions = state.get("agents_server_sessions")
    awaiting = {
        identifier: seconds
        for identifier, record in (sessions.items() if isinstance(sessions, dict) else ())
        if isinstance(identifier, str)
        and isinstance(record, dict)
        and record.get("owner_agent_id") == owner_agent_id
        and not record.get("kill_requested")
        and (seconds := continuation_wait_seconds(identifier)) is not None
    }
    return pending_session_ids, continued, awaiting


def _awaiting_summary(awaiting: dict[str, int]) -> str:
    return ", ".join(f"{identifier}（返却から {seconds} 秒）" for identifier, seconds in awaiting.items())


def outstanding_before_completion(session_id: str, owner_agent_id: str) -> str | None:
    pending, _, awaiting = _observation_status(read_state(session_id), owner_agent_id)
    if not pending and not awaiting:
        return None
    lines = [f"未回収: {', '.join(pending)}"] if pending else []
    if awaiting:
        lines.append(f"応答待ち: {_awaiting_summary(awaiting)}")
    return "、".join(lines)


def compact_wait_notice(session_id: str, owner_agent_id: str) -> str:
    """圧縮後に、自分の待機と他の主体の待機を取り違えないようにする。"""
    pending, _, awaiting = _observation_status(read_state(session_id), owner_agent_id)
    if not pending and not awaiting:
        return ""
    wait_targets = [identifier for identifier in pending if identifier not in awaiting]
    own_wait = _observations.own_waited_session_ids(wait_targets)
    missing = [identifier for identifier in wait_targets if identifier not in own_wait]
    lines = []
    if pending:
        lines.append(f"未回収: {', '.join(pending)}")
        if own_wait:
            lines.append(f"自セッションが待機中: {', '.join(sorted(own_wait))}。")
        else:
            lines.append("自セッションに有効な待機なし。別セッションの待機は自分の待機に数えない。")
        if missing:
            lines.append(f"待機の再開が必要: {', '.join(missing)}。自セッションから atk agents wait で回収してください。")
    if not missing:
        lines.append("待機の再開は不要。")
    if awaiting:
        lines.append(f"応答待ち: {_awaiting_summary(awaiting)}。役割が求める指示を send_message で返してください。")
    return "\n".join(lines)


def evaluate(payload_text: str) -> tuple[str, str]:
    resolved = parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    set_warning_session_id(session_id)
    if payload.get("stop_hook_active") is True:
        return "approve", ""

    owner_agent_id = resolve_hook_agent_id(payload)
    state = read_state(session_id)
    pending_session_ids, continued, awaiting = _observation_status(state, owner_agent_id)
    sessions = state.get("agents_server_sessions")
    if not pending_session_ids and not awaiting:
        if isinstance(sessions, dict) and any(
            isinstance(record, dict)
            and record.get("owner_agent_id") == owner_agent_id
            and record.get("pending_observation") is False
            for record in sessions.values()
        ):
            # 観測・破棄の証拠でだけ解除し、下の一時的な観測所有・待機表明では保持する。
            resolve_notice(session_id, _HOOK_ID, "evaluate", subject=owner_agent_id)
        return "approve", ""
    observed = _observations.actively_waited_session_ids(pending_session_ids)
    if _continuation_owned(payload):
        observed |= _declared_waiting_target_ids(payload.get("last_assistant_message")) & set(pending_session_ids)
    pending_session_ids = [item for item in pending_session_ids if item not in observed]
    if not pending_session_ids and not awaiting:
        return "approve", ""

    description = (
        "登録済みの未終了sessionについて、現在有効な待機・自動再開の保証を確認できない。"
        if set(pending_session_ids) & set(continued)
        else _WARNING_BODY
    )
    lines = [f"{description}\n対象session: {', '.join(pending_session_ids)}"] if pending_session_ids else []
    if awaiting:
        lines.append(f"応答待ち: {_awaiting_summary(awaiting)}")
    body = "\n".join(lines)
    fix = "役割が求める指示を`send_message`で返してください。" if awaiting else ""
    if pending_session_ids:
        warning_fix = _WARNING_FIX if is_codex_payload(payload) or is_antigravity_payload(payload) else _CLAUDE_WARNING_FIX
        fix = f"{warning_fix}{fix}"
    return "notify", _notice(body, fix=fix, removable_cause=True, summary=body)
