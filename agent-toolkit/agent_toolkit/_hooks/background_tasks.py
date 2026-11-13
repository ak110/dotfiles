"""Claude Code agent-toolkit: バックグラウンドタスクと非同期待機の追跡から、セッションが構造的に継続中かを判定する。

Claude CodeのStop入力に完全に有効な`background_tasks`一覧が含まれる場合は、現在のtask申告から実行中のtaskを確定する。
フィールドが無い旧ホスト、無効な申告およびCodexでは、transcript JSONLから復元した判定へフォールバックする。
本モジュールは構造的な継続判定（`is_pending_async_work`）を提供する。
完了文言・質問・待機語など言語面の判定はLLM側（スキル本体の起動方針節）へ委譲する。

transcript上のbackground task起動の検出条件は次の4種を統合して扱う。
- `toolUseResult.status == "async_launched"`、または対応するAgent・Taskの`tool_result`本文に
  `_AGENT_ASYNC_LAUNCH_MARKER`を含み、同期完了statusでない（背景Agent初回起動）
- `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）
- `tool_result`ブロックの`tool_use_id`がSendMessage呼び出し由来かつ、`toolUseResult.resumedAgentId`が
  文字列で存在するか、text本文に`_SENDMESSAGE_BG_RESUME_MARKER`を含む
  （SendMessageによるサブエージェント背景再開）
- 非sidechainのMCP tool_useに対応するtool_result本文がホストのMCP移行通知の形で始まる
  （MCPバックグラウンドタスク。形は`_MCP_BACKGROUND_NOTICE_RE`）

SendMessage背景再開は前2者と異なり`toolUseResult`側に起動状態を示すstatusを持たないため、
SendMessage呼び出し由来のtool_resultへ限定したうえで`resumedAgentId`の有無で識別する。
このフィールドを持たない旧形式に限りテキストマーカー判定へフォールバックする。

完了集合は`<task-notification>`による完了通知、最上位transcriptの
`queue-operation`に含まれる完了通知およびTaskStopの停止成功結果から構成する。
停止成功は`toolUseResult`が文字列の`task_id`と`_TASK_STOP_SUCCESS_PREFIX`で始まる`message`を持ち、
対応する`tool_result`の`is_error`が真でない場合に限る。
走査範囲は呼び出し元に応じて切り替え、メインのStop判定では非sidechainに限定し、
SubagentStop判定ではsidechainを含める。

`<task-notification>`要素に`<tool-use-id>`が含まれない通知形式では、
`<task-id>`要素とagentId→tool_use_id集合マップ（`_collect_task_id_tool_use_ids`）による
フォールバック解決を行う。両者で解決できない通知のうち、対応するassistant側`tool_use`の
`name`が`"Monitor"`と突合できたもの（`_collect_monitor_task_ids`）は追跡対象外の既知通知として
ログへ記録せず無視する。それ以外で解決できない通知は`task_notification_unresolved`として常時ログへ明示出力し、
通知形式が変わって完了を解決できず、起動済みのタスクが未完了として残り続ける事象を検出できるようにする。

本モジュールへ判定不能時に終了を拒否する関数を追加する場合は次の2点を守る。
完了の判定には複数キーによるフォールバック解決を使い、どのキーでも解決できない通知または停止結果は
永続ログへ明示出力して未解決状態を可視化する（起動時に記録した全background taskが
完了通知または停止成功結果のいずれかで完了集合へ解決できることを不変条件として維持する）。
判定は`launched - completed`の残りが空でないことで行う
（起動集合だけで判定すると完了通知を受け取った後も真を返し続け、以後のターンが状態を述べただけで終わっても全て判定を通過する）。

判定根拠の常時ログは`stop_session.append_stop_log`へ書き、詳細stderr出力（`_emit_debug`）は
環境変数`AGENT_TOOLKIT_STOP_GATE_DEBUG`が真値の場合のみ発火するDEBUG相当
（last_tool・launched・pending・pending_ids・payload task件数・判定源）で、原因を特定する用途に限定する。
"""

import io
import json
import os
import pathlib
import sys

lazy from agent_toolkit._common import agy_records
lazy from agent_toolkit._common import transcript as _transcript
lazy from agent_toolkit._hooks import agents_server_observations as _agents_server_observations
lazy from agent_toolkit._hooks.background_tasks_constants import (
    _ASYNC_WAIT_TOOLS,
    _DEBUG_TRUTHY_VALUES,
    _PENDING_ASYNC_WORK_CACHE,
    _TASK_ID_RE,
    _TOOL_USE_ID_RE,
)
lazy from agent_toolkit._hooks.background_tasks_entries import (
    describe_pending_background_entries,
)
lazy from agent_toolkit._hooks.background_tasks_launches import (
    async_agent_launch_id,
    background_task_id_from_notice,
    collect_background_task_id_tool_use_ids,
)
lazy from agent_toolkit._hooks.background_tasks_notifications import (
    _has_queued_task_notification,
    is_agent_task_notification,
    queued_task_notification_contents,
)
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.transcript_scan import (
    entry_in_scan_scope,
    last_tool_use_block,
    read_transcript_entries_cached,
    tool_result_text_blocks,
)

__all__ = ["async_agent_launch_id", "collect_background_task_id_tool_use_ids", "is_agent_task_notification"]


def _pending_cache_key(
    transcript_path: str,
    session_id: str,
    background_tasks: object,
    session_state: object,
) -> tuple[object, ...]:
    """入力とtranscriptの現行状態を表すキャッシュキーを返す。"""
    try:
        stat = pathlib.Path(transcript_path).stat()
        transcript_state: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        transcript_state = None
    try:
        background_state = json.dumps(background_tasks, ensure_ascii=False, sort_keys=True)
    except TypeError, ValueError:
        background_state = repr(background_tasks)
    try:
        stored_state = json.dumps(session_state, ensure_ascii=False, sort_keys=True)
    except TypeError, ValueError:
        stored_state = repr(session_state)
    return transcript_path, session_id, background_state, stored_state, transcript_state


def _valid_background_tasks(background_tasks: object) -> list[dict]:
    """Stop入力の`background_tasks`から、空でない文字列の`type`を持つ有効なtaskを返す。"""
    if not isinstance(background_tasks, list):
        return []
    return [task for task in background_tasks if isinstance(task, dict) and isinstance(task.get("type"), str) and task["type"]]


def active_non_teammate_tasks(background_tasks: object) -> list[dict]:
    """Stop入力の有効taskのうち`teammate`以外を返す。"""
    return [task for task in _valid_background_tasks(background_tasks) if task["type"] != "teammate"]


def _describe_background_tasks(background_tasks: object) -> tuple[int, int, bool]:
    """Stop入力の有効task件数、非`teammate`件数および一覧の権威性を返す。"""
    if not isinstance(background_tasks, list):
        return 0, 0, False
    valid_tasks = _valid_background_tasks(background_tasks)
    non_teammate_tasks = len(active_non_teammate_tasks(background_tasks))
    return len(valid_tasks), non_teammate_tasks, len(valid_tasks) == len(background_tasks)


def is_pending_async_work_from_payload(payload: dict, session_id: str, *, session_state: object) -> bool:
    """Stop入力のtranscriptパスを正規化し、入力と保存状態から非同期待機を判定する。"""
    raw_transcript = payload.get("transcript_path", "")
    return is_pending_async_work(
        raw_transcript if isinstance(raw_transcript, str) else "",
        session_id,
        background_tasks=payload.get("background_tasks"),
        session_state=session_state,
    )


def is_pending_async_work(
    transcript_path: str,
    session_id: str,
    *,
    background_tasks: object = None,
    session_state: object,
    owner_agent_id: str = "main",
) -> bool:
    """セッションが構造的に継続中の場合に真を返す。

    以下のいずれかの場合に真を返す。
    - 直前アシスタントターンの最後のtool_useが非同期待機系（`Agent`・`ScheduleWakeup`・
      `CronCreate`・`Monitor`、または`Bash`かつ`input.run_in_background == true`）
    - 未完了のbackground task（Agent・Bash・SendMessage背景再開・MCP）が存在する
    - 最上位transcriptのキューへ投入され、モデルへまだ配送されていない完了通知が存在する
    - 呼出主体がまだ回収していないagents_serverの終了時の結果がセッション状態に存在する

    Stop入力の`background_tasks`に有効な非`teammate` taskがあれば、無効な要素の混在に
    かかわらず真を返す。個別taskの`status`その他の任意フィールドは判定に使わない。
    `background_tasks`がlistで全要素が有効な場合は空listと`teammate`だけの一覧も現在状態の
    権威ある申告とし、この入力が申告する背景Agent、背景BashおよびSendMessage背景再開の
    transcript由来残差を根拠にしない。MCPツールのバックグラウンドタスクへの移行はStop入力へ現れないため、
    transcriptから復元した残差を独立した根拠とする。フィールド欠落、listでない入力、
    無効な要素を含むlistおよびCodexでは、transcriptから復元した全種別の判定を代替入力として用いる。
    直前の非同期待機系tool_useと有効な非`teammate` taskは、一覧の権威性にかかわらず独立した根拠とする。

    transcript由来の後者はtranscript全体を走査して判定する。
    起動集合は非sidechainの`type=="user"`エントリのうち、次のいずれかを持つものから抽出する。
    - `toolUseResult.status == "async_launched"`、または対応するAgent・Taskの`tool_result`本文に
      `_AGENT_ASYNC_LAUNCH_MARKER`を含み、同期完了statusでない（背景Agent起動）
    - `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）
    - `message.content`内の`tool_result`ブロックの`tool_use_id`がSendMessage呼び出し由来かつ、
      `toolUseResult.resumedAgentId`が文字列で存在するか、text本文に
      `_SENDMESSAGE_BG_RESUME_MARKER`を含む（SendMessageによるサブエージェント背景再開）
    - 非sidechainのMCP tool_useに対応するuser tool_result本文がホストのMCP移行通知の形で始まる
      （MCPバックグラウンドタスク）

    完了集合は後続エントリの`<task-notification>`要素、最上位transcriptの
    `queue-operation`に含まれる完了通知およびTaskStopの停止成功結果から構成する。
    停止成功結果は`toolUseResult.task_id`を背景Bashの`backgroundTaskId`対応表または
    agentId→tool_use_id集合マップで解決する。
    完了通知エントリは次の3形式が併存する。
    - 旧形式: 非sidechainの`type=="user"`エントリのtext content内に含まれる`<task-notification>`要素
    - 新形式: `type=="attachment"`かつ`attachment.commandMode=="task-notification"`のエントリの
      `attachment.prompt`文字列に含まれる`<task-notification>`要素（Claude Code 2.1系以降）
    - 多段委譲形式: `type=="queue-operation"`かつ`operation`が`enqueue`または`remove`のエントリの
      `content`文字列に含まれる`<task-notification>`要素
    `<task-notification>`要素に`<tool-use-id>`が含まれない場合は`<task-id>`要素と
    agentId→tool_use_id集合マップによるフォールバック解決を試み、それでも解決できない通知は
    `task_notification_unresolved`として常時ログへ明示出力する。
    停止成功結果を同じ2つのキーで解決できない場合も同形式で常時ログへ明示出力する。
    起動集合から完了集合を差し引いて1件以上残れば「未完了background taskあり」と判断する。

    `transcript_path`が与えられた最上位Stop判定では、同じstemの`subagents`配下にある
    `agent-*.jsonl`をmetadataの親子関係で直接の子に限定して読み、サブエージェント記録から孫Agent起動と
    task-id対応表を起動集合へ加える。サブエージェント記録の不在・破損・無関係なmetadataは無視する。
    この追加走査は非sidechainの最上位Stop判定だけで行い、SubagentStopの走査範囲は拡張しない。

    この処理は非sidechainエントリだけを走査する。

    transcriptを読み取れない異常系ではtranscript由来の根拠を偽とし、
    Stop入力とセッション状態の根拠は独立に判定する。
    `session_id`は常時ログ（`append_stop_log`）の宛先ファイル特定にのみ使う。
    `session_state`は省略できない。省くとagents_serverの未回収の終了結果を根拠にした待機を判定できず、
    呼び出し元ごとの付け忘れが待機中のセッションの誤判定になるため、状態を持たない呼び出しは`None`を明示する。
    """
    cache_key = _pending_cache_key(transcript_path, session_id, background_tasks, session_state)
    if cache_key in _PENDING_ASYNC_WORK_CACHE:
        return _PENDING_ASYNC_WORK_CACHE[cache_key]

    entries = read_transcript_entries_cached(transcript_path)
    cache_key = _pending_cache_key(transcript_path, session_id, background_tasks, session_state)
    last_tool_use = last_tool_use_block(entries)
    last_async = _last_tool_use_is_async_wait(last_tool_use)
    launched, completed, host_reported_launched = describe_pending_background_entries(
        entries,
        session_id,
        transcript_path=transcript_path,
    )
    remainder = launched - completed
    host_reported_remainder = host_reported_launched - completed
    unreported_remainder = remainder - host_reported_remainder
    payload_valid, payload_non_teammate, payload_authoritative = _describe_background_tasks(background_tasks)
    queued_notification = _has_queued_task_notification(entries)
    pending_observation = _agents_server_observations.has_pending_owned_observation(session_state, owner_agent_id)
    pending_sources: list[str] = []
    if payload_non_teammate:
        pending_sources.append("background_tasks")
    if last_async:
        pending_sources.append("last_tool")
    if host_reported_remainder and not payload_authoritative:
        pending_sources.append("transcript")
    if unreported_remainder:
        pending_sources.append("transcript_unreported")
    if queued_notification:
        pending_sources.append("queued_notification")
    if pending_observation:
        pending_sources.append("agents_server_observation")
    source = "+".join(pending_sources) if pending_sources else "none"
    pending = bool(
        last_async
        or payload_non_teammate
        or (host_reported_remainder and not payload_authoritative)
        or unreported_remainder
        or queued_notification
        or pending_observation
    )
    last_tool = _describe_last_tool_use(last_tool_use)
    _emit_debug(
        pending,
        last_tool,
        launched,
        completed,
        payload_valid=payload_valid,
        payload_non_teammate=payload_non_teammate,
        payload_authoritative=payload_authoritative,
        source=source,
    )
    append_stop_log(
        session_id,
        "is_pending_async_work_result",
        {
            "result": pending,
            "last_tool": last_tool,
            "launched": len(launched),
            "pending": len(remainder),
            "pending_unreported": len(unreported_remainder),
            "pending_ids": ",".join(sorted(remainder)[:3]) if remainder else "-",
            "payload_valid": payload_valid,
            "payload_non_teammate": payload_non_teammate,
            "payload_authoritative": payload_authoritative,
            "pending_observation": pending_observation,
            "source": source,
        },
    )
    _PENDING_ASYNC_WORK_CACHE.clear()
    _PENDING_ASYNC_WORK_CACHE[cache_key] = pending
    return pending


def pending_async_task_ids(transcript_path: str, session_id: str, *, background_tasks: object = None) -> set[str]:
    """`is_pending_async_work`が継続の根拠とする非同期対象を、識別子の集合で返す。

    根拠の採否は`is_pending_async_work`と同じとし、agents_serverの未回収結果だけはセッション全体の
    状態に属して起動の位置を持たないため含めない。識別子は起動`tool_use_id`、背景Bashの`backgroundTaskId`、
    背景Agentの`agentId`、MCPのタスクIDおよびStop入力の`background_tasks`の`id`の混在とし、
    `async_launch_offsets`で起動の位置へ対応付ける。
    """
    entries = read_transcript_entries_cached(transcript_path)
    identifiers: set[str] = set()
    last_tool_use = last_tool_use_block(entries)
    if _last_tool_use_is_async_wait(last_tool_use) and last_tool_use is not None:
        last_id = last_tool_use.get("id")
        if isinstance(last_id, str) and last_id:
            identifiers.add(last_id)
    launched, completed, host_reported_launched = describe_pending_background_entries(
        entries,
        session_id,
        transcript_path=transcript_path,
    )
    remainder = launched - completed
    host_reported_remainder = host_reported_launched - completed
    identifiers.update(remainder - host_reported_remainder)
    _payload_valid, _payload_non_teammate, payload_authoritative = _describe_background_tasks(background_tasks)
    if not payload_authoritative:
        identifiers.update(host_reported_remainder)
    identifiers.update(
        task["id"] for task in active_non_teammate_tasks(background_tasks) if isinstance(task.get("id"), str) and task["id"]
    )
    for content in queued_task_notification_contents(entries):
        identifiers.update(_TOOL_USE_ID_RE.findall(content))
        identifiers.update(_TASK_ID_RE.findall(content))
    return identifiers


def async_launch_offsets(transcript_path: str) -> dict[str, int]:
    """非sidechainの記録に現れる非同期対象の識別子ごとに、最初に現れた行の開始バイト位置を返す。

    対象はassistantの`tool_use`の`id`、`tool_result`の`tool_use_id`、`toolUseResult`の
    `backgroundTaskId`・`agentId`・`resumedAgentId`およびホストのMCP移行通知のタスクIDとする。
    バイト位置はUserPromptSubmit時点のtranscriptの大きさと比べるために返す。読み取れない場合は空を返す。
    """
    data = _transcript.read_transcript_bytes(transcript_path)
    if data is None:
        return {}
    # 開始バイト位置は改行コード込みの長さで進めるため、LF行の末尾をそのまま保持する。
    with io.BytesIO(data) as stream:
        raw_lines = stream.readlines()
    parsed: list[dict] = []
    for raw in raw_lines:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError, UnicodeDecodeError:
            entry = None
        parsed.append(entry if isinstance(entry, dict) else {})
    entries = agy_records.normalize_entries(parsed, hook_view=True)
    offsets: dict[str, int] = {}
    position = 0
    for raw, entry in zip(raw_lines, entries, strict=True):
        start = position
        position += len(raw)
        if not isinstance(entry, dict) or not entry_in_scan_scope(entry, include_sidechain=False):
            continue
        identifiers: list[object] = []
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                identifiers.append(block.get("id"))
            elif block.get("type") == "tool_result":
                identifiers.append(block.get("tool_use_id"))
                identifiers.extend(
                    background_task_id_from_notice(text) for text in tool_result_text_blocks(block.get("content"))
                )
        tool_use_result = entry.get("toolUseResult")
        if isinstance(tool_use_result, dict):
            identifiers.extend(tool_use_result.get(key) for key in ("backgroundTaskId", "agentId", "resumedAgentId"))
        for identifier in identifiers:
            if isinstance(identifier, str) and identifier:
                offsets.setdefault(identifier, start)
    return offsets


def _emit_debug(
    result: bool,
    last_tool: str,
    launched: set[str],
    completed: set[str],
    *,
    payload_valid: int,
    payload_non_teammate: int,
    payload_authoritative: bool,
    source: str,
) -> None:
    """環境変数`AGENT_TOOLKIT_STOP_GATE_DEBUG`が真値の場合のみstderrへ判定根拠を1行出力する。

    出力形式は`key=value`空白区切りとする。
    Stop hookの誤判定時にlast_tool_use名・transcriptの起動と残差・payloadのtask件数および判定源から
    原因を特定するために用いる。
    """
    raw = os.environ.get("AGENT_TOOLKIT_STOP_GATE_DEBUG", "")
    if raw.lower() not in _DEBUG_TRUTHY_VALUES:
        return
    remainder = launched - completed
    head_ids = ",".join(sorted(remainder)[:3]) if remainder else "-"
    print(
        f"_stop_gate result={result} last_tool={last_tool} "
        f"launched={len(launched)} pending={len(remainder)} pending_ids={head_ids} "
        f"payload_valid={payload_valid} payload_non_teammate={payload_non_teammate} "
        f"payload_authoritative={payload_authoritative} source={source}",
        file=sys.stderr,
    )


def _last_tool_use_is_async_wait(last_tool_use: dict | None) -> bool:
    """直前アシスタントターンの最後のtool_useが非同期待機系の場合に真を返す。

    `_ASYNC_WAIT_TOOLS`に含まれるツール名、または`Bash`かつ
    `input.run_in_background == true`の場合に真を返す。
    バックグラウンド処理中のStop hook誤発動を防ぐための判定。
    """
    if last_tool_use is None:
        return False
    name = last_tool_use.get("name", "")
    if name in _ASYNC_WAIT_TOOLS:
        return True
    if name == "Bash":
        tool_input = last_tool_use.get("input")
        if isinstance(tool_input, dict) and tool_input.get("run_in_background") is True:
            return True
    return False


def _describe_last_tool_use(last_tool_use: dict | None) -> str:
    """最新assistantターン末尾のtool_use名をデバッグ出力向けに整形して返す。

    Bashの場合は`Bash(bg=True)`または`Bash(bg=False)`形式で返す。
    tool_useが存在しない場合は`-`を返す。
    """
    if last_tool_use is None:
        return "-"
    name = last_tool_use.get("name", "")
    if name == "Bash":
        tool_input = last_tool_use.get("input")
        bg = isinstance(tool_input, dict) and tool_input.get("run_in_background") is True
        return f"Bash(bg={bg})"
    return name or "-"
