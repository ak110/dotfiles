"""観測を試みていないagents_serverの作業をStop時に警告する。

`start`（全mode）と新しいturnを起こす`send_message`は、委譲先に
新しい作業を発生させる。実行中turnへの`steered`配送は新しい作業の発生に含めない。
PostToolUseがその応答と呼出主体を`agents_server_sessions`へ記録し、
本フックは`pending_observation`が真で、呼出主体が一致する記録だけを警告対象にする。

判定対象は観測を試みていない作業の有無である。
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

from agent_toolkit._hooks.notice import _WARN_TAG, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common.session_state import read_state
lazy from agent_toolkit._hooks import agents_server_observations as _observations
lazy from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
lazy from agent_toolkit._hooks.host import is_antigravity_payload
lazy from agent_toolkit._hooks.stop_session import parse_stop_session

_HOOK_ID = "agents_server_session_advisor"
_WARNING_BODY = (
    "`agents_server`の`session`に、観測を試みていない作業が残っている。"
    "観測しないまま終了すると、その作業の成果を回収する主体が残らない。"
)
_WARNING_FIX = (
    "実行ホストの`atk agents wait`で観測するか、結果が不要なら`kill(session_id)`で破棄してから終了せよ。"
    "`send_message`は新しい作業を配送するだけで観測しないため、この警告は解消しない。"
    "ホストが待機コマンドを停止し再開にユーザーの指示を要する場合は、結果を保持して再開の可否をユーザーへ確認せよ。"
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
    pending_session_ids = _observations.pending_session_ids(read_state(session_id), owner_agent_id)
    observed = _observations.actively_waited_session_ids(pending_session_ids)
    if _continuation_owned(payload):
        observed |= _declared_waiting_target_ids(payload.get("last_assistant_message")) & set(pending_session_ids)
    pending_session_ids = [item for item in pending_session_ids if item not in observed]
    if not pending_session_ids:
        return "approve", ""

    body = f"{_WARNING_BODY}\n対象session: {', '.join(pending_session_ids)}"
    return "notify", _notice(body, fix=_WARNING_FIX, removable_cause=True, summary=body)
