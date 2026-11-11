"""非同期起動の識別子収集。"""

lazy from agent_toolkit._hooks.background_tasks_constants import (
    _AGENT_ASYNC_LAUNCH_MARKER,
    _AGENT_ASYNC_LAUNCH_STATUS,
    _AGENT_SYNC_COMPLETION_STATUSES,
    _MCP_BACKGROUND_NOTICE_RE,
    _SENDMESSAGE_BG_RESUME_MARKER,
    _TRAILING_PUNCTUATION,
)
lazy from agent_toolkit._hooks.transcript_scan import (
    entry_in_scan_scope,
    extract_tool_result_id,
    iter_assistant_blocks,
    tool_result_text_blocks,
)


def _collect_sendmessage_tool_use_ids(entries: list[dict], *, include_sidechain: bool = False) -> set[str]:
    """走査範囲内のassistantエントリからSendMessage tool_use id集合を返す。"""
    ids: set[str] = set()
    for block in iter_assistant_blocks(entries, include_sidechain=include_sidechain):
        if block.get("type") != "tool_use" or block.get("name") != "SendMessage":
            continue
        block_id = block.get("id")
        if isinstance(block_id, str):
            ids.add(block_id)
    return ids


def _collect_agent_tool_use_ids(entries: list[dict], *, include_sidechain: bool = False) -> set[str]:
    """走査範囲内のassistantエントリからAgent・Task tool_use id集合を返す。"""
    ids: set[str] = set()
    for block in iter_assistant_blocks(entries, include_sidechain=include_sidechain):
        if block.get("type") != "tool_use" or block.get("name") not in {"Agent", "Task"}:
            continue
        block_id = block.get("id")
        if isinstance(block_id, str):
            ids.add(block_id)
    return ids


def _collect_mcp_tool_use_ids(entries: list[dict], *, include_sidechain: bool = False) -> set[str]:
    """走査範囲内のassistantエントリからMCP tool_use id集合を返す。"""
    ids: set[str] = set()
    for block in iter_assistant_blocks(entries, include_sidechain=include_sidechain):
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        block_id = block.get("id")
        if isinstance(name, str) and name.startswith("mcp__") and isinstance(block_id, str):
            ids.add(block_id)
    return ids


def _collect_mcp_background_task_id_tool_use_ids(
    entries: list[dict],
    mcp_ids: set[str],
    *,
    include_sidechain: bool = False,
) -> dict[str, set[str]]:
    """MCPタイムアウト通知のバックグラウンドタスクIDと起動`tool_use` IDの対応を全`tool_result`から収集する。"""
    result: dict[str, set[str]] = {}
    for entry in entries:
        if entry.get("type") != "user" or not entry_in_scan_scope(
            entry,
            include_sidechain=include_sidechain,
        ):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str) or tool_use_id not in mcp_ids:
                continue
            for text in tool_result_text_blocks(block.get("content")):
                task_id = background_task_id_from_notice(text)
                if task_id is not None:
                    result.setdefault(task_id, set()).add(tool_use_id)
    return result


def async_agent_launch_id(tool_response: object) -> str | None:
    """Agent・Task起動の応答が背景起動を示す場合に`agentId`を返す。

    前景で完了した起動と`agentId`を欠く応答は、停止の対象が残らないため`None`を返す。
    """
    if not isinstance(tool_response, dict) or tool_response.get("status") != _AGENT_ASYNC_LAUNCH_STATUS:
        return None
    agent_id = tool_response.get("agentId")
    return agent_id if isinstance(agent_id, str) and agent_id else None


def background_task_id_from_notice(value: object) -> str | None:
    """ホストのMCP移行通知からタスクIDを返す。

    入れ子を再帰的に走査し、文字列が`_MCP_BACKGROUND_NOTICE_RE`の形で始まる場合だけ識別子を返す。
    本文の途中に同じ文言がある文字列は通知とみなさない。
    識別子の直後に続く文末の句読点は除く。
    通知本文は文として書かれるため、句読点を含めた値は`<task-id>`要素の値とも
    停止対象の識別子とも一致しない。
    """
    if isinstance(value, str):
        match = _MCP_BACKGROUND_NOTICE_RE.match(value)
        if match is None:
            return None
        return match.group(1).rstrip(_TRAILING_PUNCTUATION) or None
    if isinstance(value, dict):
        nested_values = value.values()
    elif isinstance(value, list):
        nested_values = value
    else:
        return None
    for nested in nested_values:
        task_id = background_task_id_from_notice(nested)
        if task_id is not None:
            return task_id
    return None


def _collect_task_id_tool_use_ids(entries: list[dict], *, include_sidechain: bool = False) -> dict[str, set[str]]:
    """transcript全行のuserエントリから、agentId（task-id）→tool_use_id集合マップを構築する。

    起動を記録した`toolUseResult`に`agentId`（バックグラウンドタスクの`task-id`）が含まれる場合、
    task-notification本文の`<task-id>`要素経由での完了突合をフォールバックとして提供する。
    `<tool-use-id>`要素が通知形式変動で欠落した場合に解決する手段として用いる。
    """
    result: dict[str, set[str]] = {}
    for entry in entries:
        if entry.get("type") != "user" or not entry_in_scan_scope(
            entry,
            include_sidechain=include_sidechain,
        ):
            continue
        tool_use_result = entry.get("toolUseResult")
        if not isinstance(tool_use_result, dict):
            continue
        agent_id = tool_use_result.get("agentId")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        tool_use_id = extract_tool_result_id(message)
        if tool_use_id is None:
            continue
        result.setdefault(agent_id, set()).add(tool_use_id)
    return result


def collect_background_task_id_tool_use_ids(
    entries: list[dict],
    *,
    include_sidechain: bool = False,
) -> dict[str, set[str]]:
    """背景Bashの`backgroundTaskId`から起動`tool_use_id`への対応を返す。"""
    result: dict[str, set[str]] = {}
    for entry in entries:
        if entry.get("type") != "user" or not entry_in_scan_scope(
            entry,
            include_sidechain=include_sidechain,
        ):
            continue
        tool_use_result = entry.get("toolUseResult")
        if not isinstance(tool_use_result, dict):
            continue
        task_id = tool_use_result.get("backgroundTaskId")
        if not isinstance(task_id, str) or not task_id:
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        tool_use_id = extract_tool_result_id(message)
        if tool_use_id is not None:
            result.setdefault(task_id, set()).add(tool_use_id)
    return result


def _collect_monitor_task_ids(entries: list[dict], *, include_sidechain: bool = False) -> set[str]:
    """transcript全行から、Monitorツール起動に一意に対応する`taskId`の集合を返す。

    `toolUseResult.taskId`キーは、Monitorに加えて他のツール
    （`success`・`updatedFields`・`statusChange`キーを伴う形で観測）も同じキー名で使う
    （実transcript調査で確認済み）。そのため`taskId`キーの存在だけでMonitor由来と判定せず、
    対応する`tool_use_id`（`extract_tool_result_id`で解決）がassistant側`tool_use`ブロックの
    `name == "Monitor"`と一致する場合に限りMonitor由来として収集する。
    `<task-notification>`の解決では値（文字列）でしか突合できず`tool_use_id`を参照できないため、
    Monitor由来の値とMonitor以外由来の値が同一transcript内で衝突した場合、
    その値だけでは発生源を一意に特定できない。衝突した値は戻り値の集合から除外し、
    `_resolve_task_notification_ids`側がfail-closed（`task_notification_unresolved`をログする）で
    扱えるようにする。
    Monitorは`_describe_pending_background_tasks`の起動集合（`launched`）に加算されないため、
    Monitorの完了通知は常に`<task-id>`要素のフォールバック解決に失敗し
    `task_notification_unresolved`を誤って発生させる。本関数が返す集合は
    `_resolve_task_notification_ids`がこの完了通知を異常系ログから除外する判定に使う。
    """
    monitor_tool_use_ids: set[str] = set()
    for block in iter_assistant_blocks(entries, include_sidechain=include_sidechain):
        if block.get("type") != "tool_use" or block.get("name") != "Monitor":
            continue
        block_id = block.get("id")
        if isinstance(block_id, str):
            monitor_tool_use_ids.add(block_id)

    monitor_task_ids: set[str] = set()
    non_monitor_task_ids: set[str] = set()
    for entry in entries:
        if entry.get("type") != "user" or not entry_in_scan_scope(
            entry,
            include_sidechain=include_sidechain,
        ):
            continue
        tool_use_result = entry.get("toolUseResult")
        if not isinstance(tool_use_result, dict):
            continue
        task_id = tool_use_result.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        tool_use_id = extract_tool_result_id(message)
        if tool_use_id is not None and tool_use_id in monitor_tool_use_ids:
            monitor_task_ids.add(task_id)
        else:
            non_monitor_task_ids.add(task_id)
    return monitor_task_ids - non_monitor_task_ids


def _extract_agent_launch_id(message: dict, tool_use_result: dict, agent_ids: set[str]) -> str | None:
    """Agent・Task結果が非同期起動を示す場合に`tool_use_id`を返す。"""
    tool_use_id = extract_tool_result_id(message)
    if tool_use_id is None:
        return None
    status = tool_use_result.get("status")
    if status == _AGENT_ASYNC_LAUNCH_STATUS:
        return tool_use_id
    if status in _AGENT_SYNC_COMPLETION_STATUSES or tool_use_id not in agent_ids:
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if block.get("tool_use_id") != tool_use_id:
            continue
        if any(_AGENT_ASYNC_LAUNCH_MARKER in text for text in tool_result_text_blocks(block.get("content"))):
            return tool_use_id
    return None


def _extract_sendmessage_bg_resume_id(message: dict, tool_use_result: object, sendmessage_ids: set[str]) -> str | None:
    """SendMessage由来のtool_resultが背景再開を示す場合に`tool_use_id`を返す。

    判定は`toolUseResult.resumedAgentId`が文字列として存在するかを第一とし、
    このフィールドを欠く結果に限り本文の`_SENDMESSAGE_BG_RESUME_MARKER`との一致判定へフォールバックする。
    ハーネスが返す応答文面は版更新で変わり字面一致では追随できないため、
    同じ結果が持つ構造化フィールドから検出し、文面との一致判定は旧形式の後方互換に限る。
    """
    has_resumed_agent_id = isinstance(tool_use_result, dict) and isinstance(tool_use_result.get("resumedAgentId"), str)
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result":
            continue
        tool_use_id = block.get("tool_use_id")
        if not isinstance(tool_use_id, str) or tool_use_id not in sendmessage_ids:
            continue
        if has_resumed_agent_id:
            return tool_use_id
        inner = block.get("content")
        if isinstance(inner, str):
            if _SENDMESSAGE_BG_RESUME_MARKER in inner:
                return tool_use_id
            continue
        if not isinstance(inner, list):
            continue
        for text_block in inner:
            if not isinstance(text_block, dict):
                continue
            if text_block.get("type") != "text":
                continue
            text = text_block.get("text", "")
            if isinstance(text, str) and _SENDMESSAGE_BG_RESUME_MARKER in text:
                return tool_use_id
    return None
