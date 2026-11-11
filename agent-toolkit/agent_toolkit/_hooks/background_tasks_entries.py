"""記録を走査した起動と完了集合の構築。"""

import json
import pathlib
lazy import collections.abc

lazy from agent_toolkit._common import transcript as _transcript
lazy from agent_toolkit._hooks.background_tasks_constants import _TASK_NOTIFICATION_RE
lazy from agent_toolkit._hooks.background_tasks_launches import (
    _collect_agent_tool_use_ids,
    _collect_mcp_background_task_id_tool_use_ids,
    _collect_mcp_tool_use_ids,
    _collect_monitor_task_ids,
    _collect_sendmessage_tool_use_ids,
    _collect_task_id_tool_use_ids,
    _extract_agent_launch_id,
    _extract_sendmessage_bg_resume_id,
    collect_background_task_id_tool_use_ids,
)
lazy from agent_toolkit._hooks.background_tasks_notifications import (
    _extract_queue_operation_notification_ids,
    _extract_task_notification_ids,
    _extract_task_stop_ids,
    _resolve_task_notification_ids,
)
lazy from agent_toolkit._hooks.transcript_scan import entry_in_scan_scope, extract_tool_result_id


def _read_child_transcript_entries(transcript_path: str) -> list[dict] | None:
    """子transcriptを全行検証し、破損があれば子全体を無効として返す。"""
    lines = _transcript.read_transcript_lines(transcript_path)
    if lines is None:
        return None
    entries: list[dict] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError, ValueError:
            return None
        if not isinstance(entry, dict):
            return None
        entries.append(entry)
    return entries


def describe_pending_background_entries(
    entries: list[dict],
    session_id: str | None = None,
    *,
    include_sidechain: bool = False,
    kinds: collections.abc.Collection[str] = ("agent", "bash", "sendmessage", "mcp"),
    transcript_path: str | None = None,
) -> tuple[set[str], set[str], set[str]]:
    r"""transcript全体からバックグラウンドタスクの起動集合と完了集合を抽出する。

    `include_sidechain`が偽の場合はメインのStop判定用に非sidechainエントリへ限定する。
    真の場合はSubagentStop判定用にsidechainエントリも走査する。
    前景起動の`Agent`はメインターン内で同期完了するため対象外。

    起動の記録: 次のいずれかを持つuserエントリ。
    - `toolUseResult.status == "async_launched"`、または対応するAgent・Taskの`tool_result`本文に
      `_AGENT_ASYNC_LAUNCH_MARKER`を含み、同期完了statusでない（背景Agent起動）
    - `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）
    - `message.content`内の`tool_result`ブロックの`tool_use_id`がSendMessage呼び出し由来かつ、
       `toolUseResult.resumedAgentId`が文字列で存在するか、text本文に
       `_SENDMESSAGE_BG_RESUME_MARKER`を含む（SendMessageによるサブエージェント背景再開）
    - 非sidechain assistantの`mcp__` tool_useに対応するuser tool_result本文が
      ホストのMCP移行通知の形で始まる（MCPバックグラウンドタスク）

    完了通知の記録: 次の3形式から`tool_use_id`を抽出する。
    - 旧形式: 非sidechainのメイン側userエントリの`message.content`内テキストブロックの
      `<task-notification>`要素の`<tool-use-id>(toolu_[\\w]+)</tool-use-id>`
    - 新形式: `type=="attachment"`かつ`attachment.commandMode=="task-notification"`のエントリの
      `attachment.prompt`文字列（Claude Code 2.1系以降で観測される形式）
    旧形式・新形式とも`<tool-use-id>`要素が欠落する通知は`<task-id>`要素と
    `_collect_task_id_tool_use_ids`が構築するagentId→tool_use_id集合マップ
    （`task_id_map`）で解決するフォールバック処理を共有ヘルパー
    `_resolve_task_notification_ids`経由で適用する。両者で解決できない通知は
    `task_notification_unresolved`として常時ログへ明示出力する。
    - 最上位transcriptの`type == "queue-operation"`エントリの`content`に含まれる
      `operation == "enqueue"`または`operation == "remove"`の通知（Claude Codeの多段委譲で観測される形式）

    最上位Stop判定では、`transcript_path`が与えられた場合に
    `Path(transcript_path).with_suffix("") / "subagents"`の固定ディレクトリから
    `agent-*.jsonl`を列挙し、metadataの親子関係で直接の子に限定する。直接の子の記録にある
    Agent・Task起動を孫起動として`launched`へ加え、子の`agentId`とtool-use-idの対応を
    `task_id_map`へ追加する。サブエージェント記録の読取失敗、欠落または無関係なmetadataは既存の最上位transcript
    の判定を変えずに無視する。`include_sidechain`が偽の最上位Stop判定だけがこのサブエージェント記録を
    読み取り、SubagentStopの走査範囲を拡張しない。

    TaskStopの停止成功結果も完了集合へ加える。
    `toolUseResult`が文字列の`task_id`と`_TASK_STOP_SUCCESS_PREFIX`で始まる`message`を持ち、
    対応する`tool_result`の`is_error`が真でない場合に停止成功とする。
    `task_id`は背景Bashの`backgroundTaskId`対応表と既存の`task_id_map`で解決し、
    解決できない停止結果は通知と同じ`task_notification_unresolved`形式で常時ログへ出力する。

    起動集合から完了集合を差し引いて1件以上残れば未完了バックグラウンドタスクありと判定する。
    `<status>`の値（`completed`・`failed`・`cancelled`等）は問わず終了扱いとする。
    Agent・Bash・SendMessage背景再開・MCPバックグラウンドタスクとも同一の完了通知機構で通知され共通の抽出処理を用いる。
    `kinds`は起動集合へ含める種別を`agent`・`bash`・`sendmessage`・`mcp`から指定する。
    種別を指定しなければ全種別を対象とし、既存の呼び出し元の挙動を維持する。
    第3要素はStop入力の`background_tasks`が申告する種別の起動集合である。

    走査は2段構成とする。
    第1段でtranscript全行から非sidechain assistantのSendMessage tool_use id集合を構築する。
    第2段ではtranscriptを時系列に走査し、`toolUseResult`の起動条件に該当しないuserエントリに対して
    SendMessage集合を参照した背景再開判定を追加し、背景再開のtool_resultを起動集合へ加算する。
    """
    launched: set[str] = set()
    completed: set[str] = set()
    host_reported_launched: set[str] = set()
    sendmessage_ids = _collect_sendmessage_tool_use_ids(entries, include_sidechain=include_sidechain)
    agent_ids = _collect_agent_tool_use_ids(entries, include_sidechain=include_sidechain)
    mcp_ids = _collect_mcp_tool_use_ids(entries, include_sidechain=include_sidechain)
    mcp_background_tasks = _collect_mcp_background_task_id_tool_use_ids(
        entries,
        mcp_ids,
        include_sidechain=include_sidechain,
    )
    task_id_map = _collect_task_id_tool_use_ids(entries, include_sidechain=include_sidechain)
    background_task_id_map = collect_background_task_id_tool_use_ids(
        entries,
        include_sidechain=include_sidechain,
    )
    for task_id, tool_use_ids in mcp_background_tasks.items():
        task_id_map.setdefault(task_id, set()).update({task_id, *tool_use_ids})
    if "agent" in kinds and not include_sidechain:
        nested_launched, nested_task_id_map = _collect_nested_agent_launches(transcript_path)
        launched.update(nested_launched)
        host_reported_launched.update(nested_launched)
        for task_id, tool_use_ids in nested_task_id_map.items():
            task_id_map.setdefault(task_id, set()).update(tool_use_ids)
    monitor_task_ids = _collect_monitor_task_ids(entries, include_sidechain=include_sidechain)
    for entry in entries:
        if not entry_in_scan_scope(entry, include_sidechain=include_sidechain):
            continue
        entry_type = entry.get("type")
        if entry_type == "user":
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            tool_use_result = entry.get("toolUseResult")
            agent_launch_id = (
                _extract_agent_launch_id(message, tool_use_result, agent_ids)
                if isinstance(tool_use_result, dict) and "agent" in kinds
                else None
            )
            if agent_launch_id is not None:
                launched.add(agent_launch_id)
                host_reported_launched.add(agent_launch_id)
            elif (
                isinstance(tool_use_result, dict)
                and isinstance(tool_use_result.get("backgroundTaskId"), str)
                and "bash" in kinds
            ):
                tool_use_id = extract_tool_result_id(message)
                if tool_use_id is not None:
                    launched.add(tool_use_id)
                    host_reported_launched.add(tool_use_id)
            elif "sendmessage" in kinds:
                resumed_id = _extract_sendmessage_bg_resume_id(message, tool_use_result, sendmessage_ids)
                if resumed_id is not None:
                    launched.add(resumed_id)
                    host_reported_launched.add(resumed_id)
            if isinstance(tool_use_result, dict):
                completed.update(
                    _extract_task_stop_ids(
                        message,
                        tool_use_result,
                        background_task_id_map,
                        task_id_map,
                        session_id,
                    )
                )
            completed.update(
                _extract_task_notification_ids(
                    message,
                    task_id_map,
                    session_id=session_id,
                    monitor_task_ids=monitor_task_ids,
                )
            )
        elif entry_type == "attachment":
            # Claude Code 2.1系以降、background task完了通知はattachmentエントリ経由で記録される。
            # attachment.commandMode == "task-notification"のエントリのみが完了通知本文を持つ。
            attachment = entry.get("attachment")
            if not isinstance(attachment, dict):
                continue
            if attachment.get("commandMode") != "task-notification":
                continue
            prompt = attachment.get("prompt")
            if not isinstance(prompt, str):
                continue
            for notification in _TASK_NOTIFICATION_RE.findall(prompt):
                completed.update(
                    _resolve_task_notification_ids(
                        notification,
                        task_id_map,
                        session_id,
                        monitor_task_ids=monitor_task_ids,
                    )
                )
        else:
            completed.update(
                _extract_queue_operation_notification_ids(
                    entry,
                    task_id_map,
                    session_id=session_id,
                    monitor_task_ids=monitor_task_ids,
                )
            )
    if "mcp" in kinds:
        launched.update(mcp_background_tasks)
    return launched, completed, host_reported_launched


def _collect_nested_agent_launches(
    transcript_path: str | None,
) -> tuple[set[str], dict[str, set[str]]]:
    """直接の子transcriptから孫Agent起動とtask-id対応表を収集する。

    Claude Codeの子transcriptは最上位transcriptと同じファイル名stemの
    `subagents`ディレクトリへ配置される。入力値のagent IDをパスへ連結せず、固定の
    `agent-*.jsonl`列挙とmetadataの親子関係だけで直接の子を選別する。サブエージェント記録の欠落、
    破損または不正なmetadataは、最上位transcriptの既存判定を維持するため無視する。
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return set(), {}
    subagents_dir = pathlib.Path(transcript_path).with_suffix("") / "subagents"
    try:
        child_paths = sorted(subagents_dir.glob("agent-*.jsonl"))
    except OSError:
        return set(), {}

    launched: set[str] = set()
    task_id_map: dict[str, set[str]] = {}
    for child_path in child_paths:
        metadata_path = child_path.with_name(f"{child_path.stem}.meta.json")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue
        if not _is_direct_subagent_metadata(metadata):
            continue
        child_entries = _read_child_transcript_entries(str(child_path))
        if not child_entries:
            continue
        agent_ids = _collect_agent_tool_use_ids(child_entries, include_sidechain=True)
        for entry in child_entries:
            if entry.get("type") != "user":
                continue
            tool_use_result = entry.get("toolUseResult")
            if not isinstance(tool_use_result, dict):
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            agent_launch_id = _extract_agent_launch_id(message, tool_use_result, agent_ids)
            if agent_launch_id is None:
                continue
            launched.add(agent_launch_id)
            agent_id = tool_use_result.get("agentId")
            if isinstance(agent_id, str) and agent_id:
                task_id_map.setdefault(agent_id, set()).add(agent_launch_id)
    return launched, task_id_map


def _is_direct_subagent_metadata(metadata: object) -> bool:
    """metadataが最上位セッションの直接の子を示す場合に真を返す。"""
    if not isinstance(metadata, dict):
        return False
    parent_agent_id = metadata.get("parentAgentId")
    if parent_agent_id not in (None, ""):
        return False
    spawn_depth = metadata.get("spawnDepth")
    return isinstance(spawn_depth, int) and not isinstance(spawn_depth, bool) and spawn_depth == 1
