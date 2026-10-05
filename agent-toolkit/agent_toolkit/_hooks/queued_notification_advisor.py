"""キューに残る完了通知について、起動したツールの種別に適した結果の受領を案内するStopフック。

バックグラウンドタスクがターンの終了より前に完了すると、その完了通知は`queue-operation`の`enqueue`として
最上位transcriptのキューへ入る。Stopフックや`/goal`の目標評価がターンを継続させても、
継続した応答がツールを呼ばない限り通知は配送されない。配送はツール呼び出し時の取り込み
（`remove`の`absorbed_mid_turn`）か、ターン終了後の`dequeue`で起きる。
実行主体が完了通知の到着を待ってツールを呼ばずにターンを終えると、継続のたびに同じ状態が続く。
Agent・Taskの起動記録に対応する通知には返却メッセージの利用を案内する。
Bashと種別を判別できない通知には`<task-id>`と`<output-file>`を示し、出力ファイルの読取を促す。
ファイル読取はツール呼び出しであるため、通知の配送と結果の受領が同じターンで起きる。

キューの解析規則は`stop_gate.queued_task_notification_contents`を`stop_gate.is_pending_async_work`と共有する。
ユーザーが入力欄へ取り戻した入力（`popAll`・`popOne`）は同じ本文の要素1件として除く。
`dequeue`で取り出された要素は、その後に配送された`user`エントリの本文と比べて決め、
該当する要素が無い場合だけ先頭とする。ホストは先頭以外の要素を先に取り出すことがあるためである。
発火条件はキューの状態だけとし、`/goal`の有無、待機コマンドの種類および実行主体を条件に含めない。

遮断せず`notify`で返す。出力ファイルの読取は同じターンで実行できるが、読むかどうかと読んだ後の工程は
実行主体が決めるためである。`stop_hook_active`では抑止しない。未配送の通知が残る場面の多くは、
既に別の判定か目標評価がターンを継続させた後の再呼び出しであり、抑止すると目的の場面で案内が出ない。
同じ通知への案内は1回に限り、案内済みの識別子をセッション状態へ保持する。
`/goal`の無いセッションで案内を繰り返すと、本フック自体がツールを呼ばない継続を反復させるためである。
1回に限れば、実行主体がツールを呼ばなくても次のStopでターンが終わり、ホストが通知を配送する。
通知の識別には`<task-id>`、無い場合は`<tool-use-id>`、いずれも無い場合は通知本文を用いる。

委譲先での実行可否: 出力ファイルの読取は委譲先も実行でき、委譲先のStopにも同じキューが現れるため、
除外せず案内する。Codexのtranscriptは`queue-operation`を持たないため、Codexでは発火しない。
"""

import re

from agent_toolkit._hooks import stop_gate
from agent_toolkit._hooks.notice import _WARN_TAG, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter
from agent_toolkit._hooks.session_state import read_state, update_state
from agent_toolkit._hooks.stop_gate import parse_stop_session

_HOOK_ID = "queued_notification_advisor"
_SESSION_STATE_KEY = "queued_notification_notified_ids"

_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)
_TASK_ID_RE = re.compile(r"<task-id>([^<]+)</task-id>")
_TOOL_USE_ID_RE = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>")
_OUTPUT_FILE_RE = re.compile(r"<output-file>([^<]+)</output-file>")

_NOTICE_BODY = (
    "完了済みのバックグラウンドタスクの通知が、配送されないままキューに残っている。結果は次の出力ファイルに保存済みである。"
)
_NOTICE_FIX = (
    "完了通知を待つためにターンを終えず、このターンのうちに出力ファイルを読んで結果を受け取り、工程を進める。"
    "同じ結果を得る目的で待機コマンドを再発行しない。"
    "`atk agents wait`は回収した結果を削除するため、再発行しても同じ結果は返らない。"
)
_AGENT_NOTICE_BODY = "完了済みの`Agent`または`Task`の返却通知が、配送されないままキューに残っている。"
_AGENT_NOTICE_FIX = "通知の配送後、返却メッセージの本文を結果として使って工程を進める。別の結果取得操作は不要である。"

_notice = _notice_formatter(_HOOK_ID, default_tag=_WARN_TAG)


def _notification_elements(content: str) -> list[str]:
    """キュー項目の本文から`<task-notification>`要素を取り出す。

    閉じタグを持たない本文は、本文全体を1件の通知として扱う。
    """
    elements = _TASK_NOTIFICATION_RE.findall(content)
    return elements if elements else [content]


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    """最初の一致の値を前後の空白を除いて返す。"""
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _notification_key(notification: str) -> str:
    """案内済みかを判定する通知の識別子を返す。"""
    task_id = _first(_TASK_ID_RE, notification)
    if task_id:
        return f"task:{task_id}"
    tool_use_id = _first(_TOOL_USE_ID_RE, notification)
    if tool_use_id:
        return f"tool_use:{tool_use_id}"
    return f"content:{notification}"


def _describe(notification: str, *, include_output_file: bool = True) -> str:
    """通知1件の`<task-id>`と`<output-file>`を1行で示す。"""
    task_id = _first(_TASK_ID_RE, notification) or _first(_TOOL_USE_ID_RE, notification) or "不明"
    output_file = _first(_OUTPUT_FILE_RE, notification) if include_output_file else None
    if output_file is None:
        return f"- task-id: {task_id}"
    return f"- task-id: {task_id} 出力ファイル: {output_file}"


def _notified_keys(state: dict) -> set[str]:
    """セッション状態から案内済みの識別子を返す。"""
    value = state.get(_SESSION_STATE_KEY)
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _record_notified(session_id: str, keys: list[str]) -> None:
    """案内した識別子をセッション状態へ追記する。"""

    def _mutator(state: dict) -> dict:
        known = state.get(_SESSION_STATE_KEY)
        recorded = [item for item in known if isinstance(item, str)] if isinstance(known, list) else []
        recorded.extend(key for key in keys if key not in recorded)
        state[_SESSION_STATE_KEY] = recorded
        return state

    update_state(session_id, _mutator)


def evaluate(payload_text: str) -> tuple[str, str]:
    """未配送の完了通知の案内要否と、案内する場合の本文を返す。"""
    resolved = parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return "approve", ""
    set_warning_session_id(session_id)

    notified = _notified_keys(read_state(session_id))
    pending: dict[str, str] = {}
    entries = stop_gate.read_transcript_entries_cached(transcript_path)
    for content in stop_gate.queued_task_notification_contents(entries):
        for notification in _notification_elements(content):
            key = _notification_key(notification)
            if key not in notified and key not in pending:
                pending[key] = notification
    if not pending:
        return "approve", ""

    _record_notified(session_id, list(pending))
    agent_notifications = [
        notification for notification in pending.values() if stop_gate.is_agent_task_notification(notification, entries)
    ]
    file_notifications = [notification for notification in pending.values() if notification not in agent_notifications]
    notices: list[str] = []
    if agent_notifications:
        body = "\n".join(
            [_AGENT_NOTICE_BODY, *(_describe(notification, include_output_file=False) for notification in agent_notifications)]
        )
        notices.append(_notice(body, fix=_AGENT_NOTICE_FIX, removable_cause=True, summary=body))
    if file_notifications:
        body = "\n".join([_NOTICE_BODY, *(_describe(notification) for notification in file_notifications)])
        notices.append(_notice(body, fix=_NOTICE_FIX, removable_cause=True, summary=body))
    return "notify", "\n".join(notices)
