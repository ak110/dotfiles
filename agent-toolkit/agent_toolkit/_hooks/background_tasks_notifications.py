"""完了通知と配送キューの解決。"""

import contextlib
lazy from collections import deque

lazy from agent_toolkit._hooks.background_tasks_constants import (
    _TASK_ID_RE,
    _TASK_NOTIFICATION_RE,
    _TASK_STOP_SUCCESS_PREFIX,
    _TOOL_USE_ID_RE,
)
lazy from agent_toolkit._hooks.background_tasks_launches import _collect_agent_tool_use_ids, _collect_task_id_tool_use_ids
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.transcript_scan import entry_in_scan_scope


def _resolve_task_notification_ids(
    notification_text: str,
    task_id_map: dict[str, set[str]] | None,
    session_id: str | None,
    monitor_task_ids: set[str] | None = None,
) -> set[str]:
    """`<task-notification>`要素本文から完了`tool_use_id`集合を解決する。

    `<tool-use-id>`要素を優先して解決し、含まれない場合は`<task-id>`要素と
    `task_id_map`によるフォールバック解決を試みる。両者で解決できない場合、
    `<task-id>`が`monitor_task_ids`に含まれる場合（Monitorツール由来と突合済みの既知通知）は
    ログへ記録せず無視する。含まれない場合のみ、`session_id`が与えられていれば
    `append_stop_log`で明示ログ出力する。
    旧形式（メインuserエントリの`<task-notification>`）・新形式
    （`type=="attachment"`の`<task-notification>`）の両方の解決処理が共有する。
    """
    ids = set(_TOOL_USE_ID_RE.findall(notification_text))
    if ids:
        if task_id_map is not None:
            for task_id, mapped_ids in task_id_map.items():
                if ids & mapped_ids:
                    ids.add(task_id)
        return ids
    resolved: set[str] = set()
    if task_id_map is not None:
        for task_id in _TASK_ID_RE.findall(notification_text):
            resolved.update(task_id_map.get(task_id, set()))
    if resolved:
        return resolved
    if monitor_task_ids is not None:
        notification_task_ids = set(_TASK_ID_RE.findall(notification_text))
        if notification_task_ids and notification_task_ids <= monitor_task_ids:
            return resolved
    _log_unresolved_completion(session_id, notification_text)
    return resolved


def _extract_queue_operation_notification_ids(
    entry: dict,
    task_id_map: dict[str, set[str]] | None,
    *,
    session_id: str | None = None,
    monitor_task_ids: set[str] | None = None,
) -> set[str]:
    """最上位`queue-operation`の完了通知から完了`tool_use_id`集合を解決する。

    Claude Codeの多段委譲では、子エージェントの完了通知が最上位transcriptの
    `queue-operation`へ記録される。`enqueue`と`remove`はいずれも同じ完了通知を
    表すため処理し、その他のキュー操作は対象外とする。LLM実行主体へ通知を配送する
    契約とは別に、Stop hookが生transcriptを観測するために使う。
    """
    if entry.get("type") != "queue-operation" or entry.get("operation") not in {"enqueue", "remove"}:
        return set()
    content = entry.get("content")
    if not isinstance(content, str):
        return set()
    result: set[str] = set()
    for notification in _TASK_NOTIFICATION_RE.findall(content):
        result.update(
            _resolve_task_notification_ids(
                notification,
                task_id_map,
                session_id,
                monitor_task_ids=monitor_task_ids,
            )
        )
    return result


def _has_queued_task_notification(entries: list[dict]) -> bool:
    """最上位transcriptのキューに配送待ちの完了通知が残る場合に真を返す。"""
    return bool(queued_task_notification_contents(entries))


def queued_task_notification_contents(entries: list[dict]) -> list[str]:
    """最上位transcriptのキューに残る配送待ちの完了通知の`content`を投入順に返す。

    `enqueue`で投入し、`remove`・`popAll`・`popOne`で同じ本文の要素を1件取り除く。
    `dequeue`は取り出した要素を記録しないため、その後に最初に現れる最上位の`user`エントリのうち
    `tool_result`を含まないものの本文と比べて決める。本文と等しい要素、無ければ本文に含まれる要素を
    1件取り除き、どちらも無い場合と比べる対象の`user`エントリが無い場合は先頭を取り除く。
    ホストは先頭以外の要素を先に取り出すことがあり、常に先頭を除くと配送済みの通知を残し、
    未配送の通知を配送済みと誤る。
    Stop判定の入力待ち判定と、未配送の完了通知を案内する判定が同じ規則を共有する。
    """
    scoped = [entry for entry in entries if entry_in_scan_scope(entry, include_sidechain=False)]
    delivered_bodies = _next_delivered_user_bodies(scoped)
    queue: deque[object] = deque()
    for index, entry in enumerate(scoped):
        if entry.get("type") != "queue-operation":
            continue
        operation = entry.get("operation")
        if operation == "enqueue":
            queue.append(entry.get("content"))
        elif operation == "dequeue":
            _dequeue_delivered(queue, delivered_bodies[index])
        elif operation in {"remove", "popAll", "popOne"}:
            with contextlib.suppress(ValueError):
                queue.remove(entry.get("content"))
    return [content for content in queue if isinstance(content, str) and "<task-notification>" in content]


def _next_delivered_user_bodies(entries: list[dict]) -> list[str | None]:
    """各位置より後に最初に現れる、`tool_result`を含まない`user`エントリの本文を返す。"""
    result: list[str | None] = [None] * len(entries)
    following: str | None = None
    for index in range(len(entries) - 1, -1, -1):
        result[index] = following
        body = _delivered_user_body(entries[index])
        if body is not None:
            following = body
    return result


def _delivered_user_body(entry: dict) -> str | None:
    """キューから配送された入力として比べる対象の`user`エントリの本文を返す。

    `message.content`が文字列ならそのまま、リストなら`text`要素を連結する。
    `tool_result`を含むエントリはツールの応答であり配送された入力ではないため対象外とする。
    """
    if entry.get("type") != "user":
        return None
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    blocks = [block for block in content if isinstance(block, dict)]
    if any(block.get("type") == "tool_result" for block in blocks):
        return None
    texts = [block["text"] for block in blocks if block.get("type") == "text" and isinstance(block.get("text"), str)]
    return "".join(texts) if texts else None


def _dequeue_delivered(queue: deque[object], delivered_body: str | None) -> None:
    """`dequeue`で取り出された要素を配送された本文から決めてキューから除く。"""
    if not queue:
        return
    if delivered_body is not None:
        candidates = [item for item in queue if isinstance(item, str)]
        delivered = next((item for item in candidates if item == delivered_body), None)
        if delivered is None:
            delivered = next((item for item in candidates if item and item in delivered_body), None)
        if delivered is not None:
            queue.remove(delivered)
            return
    queue.popleft()


def is_agent_task_notification(notification: str, entries: list[dict]) -> bool:
    """通知の起動記録がAgentまたはTaskへ一意に対応する場合に真を返す。"""
    agent_ids = _collect_agent_tool_use_ids(entries)
    tool_ids = set(_TOOL_USE_ID_RE.findall(notification))
    if tool_ids:
        return tool_ids <= agent_ids
    task_map = _collect_task_id_tool_use_ids(entries)
    mapped_ids: set[str] = set()
    for task_id in _TASK_ID_RE.findall(notification):
        mapped_ids.update(task_map.get(task_id, set()))
    return bool(mapped_ids) and mapped_ids <= agent_ids


def _log_unresolved_completion(session_id: str | None, detail: str) -> None:
    """起動記録へ対応付けられなかった完了通知または停止結果を、既存の通知未解決ログ形式で記録する。"""
    if session_id is None:
        return
    append_stop_log(
        session_id,
        "task_notification_unresolved",
        {"notification": detail[:500]},
    )


def _extract_task_stop_ids(
    message: dict,
    tool_use_result: dict,
    background_task_id_map: dict[str, set[str]],
    task_id_map: dict[str, set[str]],
    session_id: str | None,
) -> set[str]:
    """TaskStop成功結果から完了した起動`tool_use_id`集合を解決する。"""
    task_id = tool_use_result.get("task_id")
    result_message = tool_use_result.get("message")
    if not isinstance(task_id, str) or not task_id:
        return set()
    if not isinstance(result_message, str) or not result_message.startswith(_TASK_STOP_SUCCESS_PREFIX):
        return set()
    if not _message_has_non_error_tool_result(message):
        return set()
    resolved = set(background_task_id_map.get(task_id, set()))
    resolved.update(task_id_map.get(task_id, set()))
    if resolved:
        return resolved
    _log_unresolved_completion(session_id, result_message)
    return resolved


def _message_has_non_error_tool_result(message: dict) -> bool:
    """messageが`is_error`真でないtool_resultを持つ場合に真を返す。"""
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error") is not True
        for block in content
    )


def _extract_task_notification_ids(
    message: dict,
    task_id_map: dict[str, set[str]] | None = None,
    *,
    session_id: str | None = None,
    monitor_task_ids: set[str] | None = None,
) -> set[str]:
    """userメッセージの`content`内の`<task-notification>`要素から完了`tool_use_id`を抽出する。

    `content`が文字列（旧フォーマット）でも配列（実transcriptフォーマット）でも処理する。
    `task_id_map`はagentId（task-id要素の値）から`tool_use_id`集合へのマップで、
    `<tool-use-id>`要素で解決できない場合に解決する代替手段として用いる。
    `monitor_task_ids`はMonitorツール由来と突合済みの`task-id`値を示す。
    両者で解決できない`<task-notification>`は`session_id`が与えられていれば
    `append_stop_log(..., "task_notification_unresolved", ...)`で明示ログ出力する。
    """
    result: set[str] = set()

    def _resolve_notification(notification_text: str) -> None:
        result.update(_resolve_task_notification_ids(notification_text, task_id_map, session_id, monitor_task_ids))

    content = message.get("content")
    if isinstance(content, str):
        for notification in _TASK_NOTIFICATION_RE.findall(content):
            _resolve_notification(notification)
        return result
    if not isinstance(content, list):
        return result
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text", "")
        if not isinstance(text, str):
            continue
        for notification in _TASK_NOTIFICATION_RE.findall(text):
            _resolve_notification(notification)
    return result
