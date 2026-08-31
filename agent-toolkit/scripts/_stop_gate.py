"""Claude Code agent-toolkit: Stop hook 共通ゲートモジュール。

Claude CodeのStop入力に完全に有効な`background_tasks`一覧が含まれる場合は、現在のtask申告を正本とする。
フィールドが無い旧ホスト、無効な申告及びCodexでは、transcript JSONLから復元した判定へフォールバックする。
本モジュールは構造的な継続判定（`is_pending_async_work`）を提供する。
完了文言・質問・待機語など言語面の判定はLLM側（スキル本体の起動方針節）へ委譲する。

transcript上のbackground task起動の検出条件は次の4種を統合して扱う。
- `toolUseResult.status == "async_launched"`、又は対応するAgent・Taskの`tool_result`本文に
  `_AGENT_ASYNC_LAUNCH_MARKER`を含み、同期完了statusでない（背景Agent初回起動）
- `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）
- `tool_result`ブロックの`tool_use_id`がSendMessage呼び出し由来かつ、`toolUseResult.resumedAgentId`が
  文字列で存在するか、text本文に`_SENDMESSAGE_BG_RESUME_MARKER`を含む
  （SendMessageによるサブエージェント背景再開）
- 非sidechainのMCP tool_useに対応するtool_result本文に`moved to the background as task`を含む
  （MCP背景タスク）

SendMessage背景再開は前2者と異なり`toolUseResult`側に起動状態を示すstatusを持たないため、
SendMessage呼び出し由来のtool_resultへ限定したうえで`resumedAgentId`の有無で識別する。
当該フィールドを持たない旧形式に限りテキストマーカー判定へフォールバックする。

完了集合は`<task-notification>`による完了通知、最上位transcriptの
`queue-operation`に含まれる完了通知及びTaskStopの停止成功結果から構成する。
停止成功は`toolUseResult`が文字列の`task_id`と`_TASK_STOP_SUCCESS_PREFIX`で始まる`message`を持ち、
対応する`tool_result`の`is_error`が真でない場合に限る。
走査範囲は呼び出し経路ごとに切り替え、メインのStop判定では非sidechainに限定し、
SubagentStop判定ではsidechainを含める。

`<task-notification>`要素に`<tool-use-id>`が含まれない通知形式では、
`<task-id>`要素とagentId→tool_use_id集合マップ（`_collect_task_id_tool_use_ids`）による
フォールバック解決を行う。両者で解決できない通知のうち、対応するassistant側`tool_use`の
`name`が`"Monitor"`と突合できたもの（`_collect_monitor_task_ids`）は追跡対象外の既知通知として
黙って無視する。それ以外で解決できない通知は`task_notification_unresolved`として常時ログへ明示出力し、
通知形式変動による幽霊pendingの発生を検出可能にする。

本モジュールへfail-closedのゲート判定関数を追加する場合は次の2点を守る。
完了突合は複数キー経路のフォールバック解決とし、いずれの経路でも解決できない通知又は停止結果は
永続ログへ明示出力して未解決状態を可視化する（起動時に記録した全background taskが
完了通知又は停止成功結果のいずれかで完了集合へ解決できることを不変条件として維持する）。
判定は起動集合の非空ではなく`launched - completed`のremainder非空で行う
（起動集合の非空判定では完了通知の消化後も真を返し続け、以後の素の状態表明がすべてbypassされる）。

常時ログ（`append_stop_log`）と詳細stderr出力（`_emit_debug`）は責務を分離する。
常時ログはINFO相当（呼び出し側が渡す最終判定ラベルと主要フラグ）を
`{tempdir}/claude-agent-toolkit-stop-{session_id}.log`へ1行ずつ追記し、
1MB超過時に`.log.1`へ1世代ローリングする。詳細stderr出力は
環境変数`AGENT_TOOLKIT_STOP_GATE_DEBUG`が真値の場合のみ発火するDEBUG相当
（last_tool・launched・pending・pending_ids・payload task件数・判定源）で、原因切り分け用途に限定する。
"""

import collections.abc
import json
import os
import pathlib
import re
import sys
import tempfile
import time

from _file_lock import locked_rotate_and_append as _locked_rotate_and_append

# 非同期待機系ツール名。これらのtool_useで直前アシスタントターンが終端している場合は
# セッション継続中と判断する。
# Bashはrun_in_backgroundフラグで別途判定するため、ここには含めない。
_ASYNC_WAIT_TOOLS: frozenset[str] = frozenset({"Agent", "ScheduleWakeup", "CronCreate", "Monitor"})

# `<task-notification>...</task-notification>`要素を非貪欲に切り出す正規表現。
# `re.DOTALL`で本文中の改行も拾う。
_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)

_MCP_BACKGROUND_TASK_RE = re.compile(r"moved to the background as task\s+(\S+)")

# `<task-notification>`要素内の`<tool-use-id>toolu_xxx</tool-use-id>`から
# `toolu_xxx`を抽出する正規表現。
_TOOL_USE_ID_RE = re.compile(r"<tool-use-id>(toolu_[\w]+)</tool-use-id>")

# `<task-id>...</task-id>`要素からagentId（task-id）を抽出する正規表現。
# task-notification本文に`<tool-use-id>`が含まれない形式のフォールバック解決に用いる。
_TASK_ID_RE = re.compile(r"<task-id>([^<]+)</task-id>")

# SendMessage背景再開時のtool_result text先頭に現れる固有マーカー（旧形式）。
# `Agent ... resumed from transcript in the background with your message.`形式で
# 出力されるため、`resumed from transcript in the background`を一致条件とする。
# 同期SendMessage応答は本文言を含まないため、背景再開ケースのみ加算される。
# 現行形式は本文言を持たず`toolUseResult.resumedAgentId`を持つため、本マーカーの照合は
# `resumedAgentId`を欠く結果に対する後方互換のフォールバックとしてのみ用いる。
_SENDMESSAGE_BG_RESUME_MARKER = "resumed from transcript in the background"

# Agent・Task起動結果の状態値と、statusを欠く実記録で起動成功を示す本文マーカー。
_AGENT_ASYNC_LAUNCH_STATUS = "async_launched"
_AGENT_SYNC_COMPLETION_STATUSES: frozenset[str] = frozenset({"completed", "teammate_spawned"})
_AGENT_ASYNC_LAUNCH_MARKER = "Async agent launched successfully"

# TaskStop成功時の`toolUseResult.message`先頭に現れる固有マーカー。
_TASK_STOP_SUCCESS_PREFIX = "Successfully stopped task"

# `AGENT_TOOLKIT_STOP_GATE_DEBUG`環境変数の真値集合。小文字一致で判定する。
_DEBUG_TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _describe_background_tasks(background_tasks: object) -> tuple[int, int, bool]:
    """Stop入力の有効task件数、非`teammate`件数及び一覧の権威性を返す。"""
    if not isinstance(background_tasks, list):
        return 0, 0, False
    valid_tasks = [
        task for task in background_tasks if isinstance(task, dict) and isinstance(task.get("type"), str) and task["type"]
    ]
    non_teammate_tasks = sum(task["type"] != "teammate" for task in valid_tasks)
    return len(valid_tasks), non_teammate_tasks, len(valid_tasks) == len(background_tasks)


def is_pending_async_work(
    transcript_path: str,
    session_id: str,
    *,
    background_tasks: object = None,
) -> bool:
    """セッションが構造的に継続中の場合に真を返す。

    以下のいずれかの場合に真を返す。
    - 直前アシスタントターンの最後のtool_useが非同期待機系（`Agent`・`ScheduleWakeup`・
      `CronCreate`・`Monitor`、または`Bash`かつ`input.run_in_background == true`）
    - 未完了のbackground task（Agent・Bash・SendMessage背景再開・MCP）が存在する

    Stop入力の`background_tasks`に有効な非`teammate` taskがあれば、無効な要素の混在に
    かかわらず真を返す。個別taskの`status`その他の任意フィールドは判定に使わない。
    `background_tasks`がlistで全要素が有効な場合は空listと`teammate`だけの一覧も現在状態の
    権威ある申告とし、transcript由来の未完了残差を根拠にしない。フィールド欠落、listでない入力、
    無効な要素を含むlist及びCodexでは、transcriptから復元した判定を代替入力として用いる。
    直前の非同期待機系tool_useと有効な非`teammate` taskは、一覧の権威性にかかわらず独立した根拠とする。

    transcript由来の後者はtranscript全体を走査して判定する。
    起動集合は非sidechainの`type=="user"`エントリのうち、次のいずれかを持つものから抽出する。
    - `toolUseResult.status == "async_launched"`、又は対応するAgent・Taskの`tool_result`本文に
      `_AGENT_ASYNC_LAUNCH_MARKER`を含み、同期完了statusでない（背景Agent起動）
    - `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）
    - `message.content`内の`tool_result`ブロックの`tool_use_id`がSendMessage呼び出し由来かつ、
      `toolUseResult.resumedAgentId`が文字列で存在するか、text本文に
      `_SENDMESSAGE_BG_RESUME_MARKER`を含む（SendMessageによるサブエージェント背景再開）
    - 非sidechainのMCP tool_useに対応するuser tool_result本文に`moved to the background as task`を含む
      （MCP背景タスク）

    完了集合は後続エントリの`<task-notification>`要素、最上位transcriptの
    `queue-operation`に含まれる完了通知及びTaskStopの停止成功結果から構成する。
    停止成功結果は`toolUseResult.task_id`を背景Bashの`backgroundTaskId`対応表又は
    agentId→tool_use_id集合マップで解決する。
    完了通知エントリは次の3形式が併存する。
    - 旧形式: 非sidechainの`type=="user"`エントリのtext content内に含まれる`<task-notification>`要素
    - 新形式: `type=="attachment"`かつ`attachment.commandMode=="task-notification"`のエントリの
      `attachment.prompt`文字列に含まれる`<task-notification>`要素（Claude Code 2.1系以降）
    - 多段委譲形式: `type=="queue-operation"`かつ`operation`が`enqueue`又は`remove`のエントリの
      `content`文字列に含まれる`<task-notification>`要素
    `<task-notification>`要素に`<tool-use-id>`が含まれない場合は`<task-id>`要素と
    agentId→tool_use_id集合マップによるフォールバック解決を試み、それでも解決できない通知は
    `task_notification_unresolved`として常時ログへ明示出力する。
    停止成功結果を同じ2経路で解決できない場合も同形式で常時ログへ明示出力する。
    起動集合から完了集合を差し引いて1件以上残れば「未完了background taskあり」と判断する。

    `transcript_path`が与えられた最上位Stop判定では、同じstemの`subagents`配下にある
    `agent-*.jsonl`をmetadataの親子関係で直接の子に限定して読み、子記録から孫Agent起動と
    task-id対応表を起動集合へ加える。子記録の不在・破損・無関係なmetadataは無視する。
    この追加走査は非sidechainの最上位Stop判定だけで行い、SubagentStopの走査範囲は拡張しない。

    本経路は非sidechainエントリだけを走査する。

    transcriptを読み取れない異常系では偽を返す（Stopを抑止しない方向で動作する）。
    `session_id`は常時ログ（`append_stop_log`）の宛先ファイル特定にのみ使う。
    """
    _wait_for_end_turn(transcript_path)
    entries = _read_transcript_entries(transcript_path)
    last_tool_use = _get_last_tool_use_block(entries)
    last_async = _last_tool_use_is_async_wait(last_tool_use)
    launched, completed = _describe_pending_background_entries(
        entries,
        session_id,
        transcript_path=transcript_path,
    )
    remainder = launched - completed
    payload_valid, payload_non_teammate, payload_authoritative = _describe_background_tasks(background_tasks)
    pending_sources: list[str] = []
    if payload_non_teammate:
        pending_sources.append("background_tasks")
    if last_async:
        pending_sources.append("last_tool")
    if remainder and not payload_authoritative:
        pending_sources.append("transcript")
    source = "+".join(pending_sources) if pending_sources else "none"
    pending = bool(last_async or payload_non_teammate or (remainder and not payload_authoritative))
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
            "pending_ids": ",".join(sorted(remainder)[:3]) if remainder else "-",
            "payload_valid": payload_valid,
            "payload_non_teammate": payload_non_teammate,
            "payload_authoritative": payload_authoritative,
            "source": source,
        },
    )
    return pending


def _stop_log_path(session_id: str) -> pathlib.Path:
    """常時ログの出力先パスを返す。

    `{tempdir}/claude-agent-toolkit-stop-{session_id}.log`形式とする。
    セッション状態ファイル（`_session_state.py`）と同じtempdir配下に置き、
    hostごとに衝突しないようsession_idで分離する。
    """
    return pathlib.Path(tempfile.gettempdir()) / f"claude-agent-toolkit-stop-{session_id}.log"


def append_stop_log(session_id: str, decision: str, context: dict, *, max_bytes: int = 1_000_000) -> None:
    """Stop hookの最終判定根拠を常時ログへ1行追記する。

    `decision`は呼び出し側が渡す最終判定ラベル（`approve_no_env`・
    `approve_pending_async`・`approve_exit_invoked`・`approve_stop_hook_active`・
    `block_autonomous_exit`など）。`context`は任意のkey-valueの辞書で、
    `last_tool`・`launched`・`pending`・`pending_ids`等を呼び出し側が任意で埋める。

    出力形式: `{ISO8601時刻} decision={...} k1=v1 k2=v2 ...`（1行）。
    `session_id`が空の場合はログ書き込みをスキップする。
    書き込み失敗（権限不足等）はStop hook本体の動作へ影響させないため無視する。
    `max_bytes`はローテーション閾値の注入点で、テストから小さい値を渡してローテーション動作を検証できる。
    """
    if not session_id:
        return
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    fields = " ".join(f"{key}={value}" for key, value in context.items())
    line = f"{timestamp} decision={decision}" + (f" {fields}" if fields else "") + "\n"
    _locked_rotate_and_append(_stop_log_path(session_id), line, max_bytes)


def parse_stop_session(raw_stdin: str, approve: collections.abc.Callable[[], None]) -> tuple[str, dict] | None:
    """Stop系hook共通の前段処理。ペイロード解析とsession_id検証を行う。

    JSON解析失敗またはsession_id欠落時は`approve`を呼び出したうえで`None`を返す。
    正常時は`(session_id, payload)`を返す。`stop_hook_active`判定・環境変数判定等の
    後続分岐は呼び出し側ごとに判定順序（`autonomous_exit.py`は環境変数判定を
    `stop_hook_active`より先に行う等）が異なるため、本関数には含めず呼び出し側へ委ねる。
    """
    try:
        payload = json.loads(raw_stdin)
    except (json.JSONDecodeError, ValueError):
        approve()
        return None

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        approve()
        return None

    return session_id, payload


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
    Stop hookの誤判定時にlast_tool_use名・transcriptの起動と残差・payloadのtask件数及び判定源から
    原因を切り分けるために用いる。
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


def _wait_for_end_turn(transcript_path: str, *, timeout: float = 0.3) -> None:
    """Stop hook起動とClaude Code側transcriptフラッシュとのレース状態に対処する。

    Claude Codeはassistant最終メッセージのtranscript書き込みとStop hook起動が
    並行することがあり、hookが読んだ時点で最終assistantエントリが未到着の場合がある。
    末尾走査で最新assistantエントリ（非sidechain）の`stop_reason`が`end_turn`であれば
    フラッシュ完了とみなして即時戻る。未到着なら短時間ポーリングし、`timeout`経過で終了する。
    """
    deadline = time.monotonic() + timeout
    poll = 0.05
    p = pathlib.Path(transcript_path)
    while True:
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            return
        for line in reversed(content.splitlines()):
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.get("type") != "assistant" or entry.get("isSidechain"):
                continue
            message = entry.get("message")
            if isinstance(message, dict) and message.get("stop_reason") == "end_turn":
                return
            # 最新assistantエントリがend_turnではない（tool_use等）→レース状態の可能性あり、
            # ポーリングを継続して最終エントリの到着を待つ。
            break
        if time.monotonic() >= deadline:
            return
        time.sleep(poll)


def _read_transcript_entries(transcript_path: str) -> list[dict]:
    """transcriptを1回読み込み、有効なJSONオブジェクトを時系列で返す。"""
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []
    entries: list[dict] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _read_child_transcript_entries(transcript_path: str) -> list[dict] | None:
    """子transcriptを全行検証し、破損があれば子全体を無効として返す。"""
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    entries: list[dict] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(entry, dict):
            return None
        entries.append(entry)
    return entries


def _entry_in_scan_scope(entry: dict, *, include_sidechain: bool) -> bool:
    """エントリが呼び出し経路ごとの走査範囲に含まれる場合に真を返す。"""
    return include_sidechain or entry.get("isSidechain") is not True


def _iter_assistant_blocks(entries: list[dict], *, include_sidechain: bool = False) -> collections.abc.Iterator[dict]:
    """走査範囲内のassistantエントリのcontent辞書を時系列で返す。"""
    for entry in entries:
        if entry.get("type") != "assistant" or not _entry_in_scan_scope(entry, include_sidechain=include_sidechain):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        yield from (block for block in content if isinstance(block, dict))


def _get_last_tool_use_block(entries: list[dict]) -> dict | None:
    """最新assistantメッセージ内で最後に現れたtool_useブロックを返す。

    最初に得た（最新の）メッセージのtool_useのみ対象とし、ターン内で最後に出現したtool_useを使う。
    メッセージをまたいで探さない。tool_useが存在しない場合は`None`を返す。
    """
    for entry in reversed(entries):
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        return next(
            (block for block in reversed(content) if isinstance(block, dict) and block.get("type") == "tool_use"),
            None,
        )
    return None


def _last_tool_use_is_async_wait(last_tool_use: dict | None) -> bool:
    """直前アシスタントターンの最後のtool_useが非同期待機系の場合に真を返す。

    `_ASYNC_WAIT_TOOLS`に含まれるツール名、または`Bash`かつ
    `input.run_in_background == true`の場合に真を返す。
    バックグラウンド処理中のStop hook誤発動を防ぐためのゲート。
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


def _describe_pending_background_tasks(
    transcript_path: str,
    session_id: str | None = None,
    *,
    include_sidechain: bool = False,
    kinds: collections.abc.Collection[str] = ("agent", "bash", "sendmessage", "mcp"),
) -> tuple[set[str], set[str]]:
    """transcriptを読み込み、指定した走査範囲の背景タスク起動集合と完了集合を返す。"""
    return _describe_pending_background_entries(
        _read_transcript_entries(transcript_path),
        session_id,
        include_sidechain=include_sidechain,
        kinds=kinds,
        transcript_path=transcript_path,
    )


def _describe_pending_background_entries(
    entries: list[dict],
    session_id: str | None = None,
    *,
    include_sidechain: bool = False,
    kinds: collections.abc.Collection[str] = ("agent", "bash", "sendmessage", "mcp"),
    transcript_path: str | None = None,
) -> tuple[set[str], set[str]]:
    r"""transcript全体から背景タスクの起動集合と完了集合を抽出する。

    `include_sidechain`が偽の場合はメインのStop判定用に非sidechainエントリへ限定する。
    真の場合はSubagentStop判定用にsidechainエントリも走査する。
    前景起動の`Agent`はメインターン内で同期完了するため対象外。

    起動の記録: 次のいずれかを持つuserエントリ。
    - `toolUseResult.status == "async_launched"`、又は対応するAgent・Taskの`tool_result`本文に
      `_AGENT_ASYNC_LAUNCH_MARKER`を含み、同期完了statusでない（背景Agent起動）
    - `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）
    - `message.content`内の`tool_result`ブロックの`tool_use_id`がSendMessage呼び出し由来かつ、
       `toolUseResult.resumedAgentId`が文字列で存在するか、text本文に
       `_SENDMESSAGE_BG_RESUME_MARKER`を含む（SendMessageによるサブエージェント背景再開）
    - 非sidechain assistantの`mcp__` tool_useに対応するuser tool_result本文が
      `moved to the background as task`を含む（MCP背景タスク）

    完了通知の記録: 次の3形式から`tool_use_id`を抽出する。
    - 旧形式: 非sidechainのメイン側userエントリの`message.content`内テキストブロックの
      `<task-notification>`要素の`<tool-use-id>(toolu_[\\w]+)</tool-use-id>`
    - 新形式: `type=="attachment"`かつ`attachment.commandMode=="task-notification"`のエントリの
      `attachment.prompt`文字列（Claude Code 2.1系以降で観測される形式）
    旧形式・新形式とも`<tool-use-id>`要素が欠落する通知は`<task-id>`要素と
    `_collect_task_id_tool_use_ids`が構築するagentId→tool_use_id集合マップ
    （`task_id_map`）で解決するフォールバック経路を共有ヘルパー
    `_resolve_task_notification_ids`経由で適用する。両者で解決できない通知は
    `task_notification_unresolved`として常時ログへ明示出力する。
    - 最上位transcriptの`type == "queue-operation"`エントリの`content`に含まれる
      `operation == "enqueue"`又は`operation == "remove"`の通知（Claude Codeの多段委譲で観測される形式）

    最上位Stop判定では、`transcript_path`が与えられた場合に
    `Path(transcript_path).with_suffix("") / "subagents"`の固定ディレクトリから
    `agent-*.jsonl`を列挙し、metadataの親子関係で直接の子に限定する。直接の子の記録にある
    Agent・Task起動を孫起動として`launched`へ加え、子の`agentId`とtool-use-idの対応を
    `task_id_map`へ追加する。子記録の読取失敗、欠落又は無関係なmetadataは既存の最上位transcript
    の判定を変えずに無視する。`include_sidechain`が偽の最上位Stop判定だけがこの子記録を
    読み取り、SubagentStopの走査範囲を拡張しない。

    TaskStopの停止成功結果も完了集合へ加える。
    `toolUseResult`が文字列の`task_id`と`_TASK_STOP_SUCCESS_PREFIX`で始まる`message`を持ち、
    対応する`tool_result`の`is_error`が真でない場合に停止成功とする。
    `task_id`は背景Bashの`backgroundTaskId`対応表と既存の`task_id_map`で解決し、
    解決できない停止結果は通知と同じ`task_notification_unresolved`形式で常時ログへ出力する。

    起動集合から完了集合を差し引いて1件以上残れば未完了背景タスクありと判定する。
    `<status>`の値（`completed`・`failed`・`cancelled`等）は問わず終了扱いとする。
    Agent・Bash・SendMessage背景再開・MCP背景タスクとも同一の完了通知機構で通知され共通の抽出処理を用いる。
    `kinds`は起動集合へ含める種別を`agent`・`bash`・`sendmessage`・`mcp`から指定する。
    既定値は全種別であり、既存の呼び出し元の挙動を維持する。
    transcript読み取り失敗時は空集合のペアを返す。

    走査は2段構成とする。
    第1段でtranscript全行から非sidechain assistantのSendMessage tool_use id集合を構築する。
    第2段ではtranscriptを時系列に走査し、`toolUseResult`の起動条件に該当しないuserエントリに対して
    SendMessage集合を参照した背景再開判定を追加し、背景再開のtool_resultを起動集合へ加算する。
    """
    launched: set[str] = set()
    completed: set[str] = set()
    sendmessage_ids = _collect_sendmessage_tool_use_ids(entries, include_sidechain=include_sidechain)
    agent_ids = _collect_agent_tool_use_ids(entries, include_sidechain=include_sidechain)
    mcp_ids = _collect_mcp_tool_use_ids(entries, include_sidechain=include_sidechain)
    mcp_background_tasks = _collect_mcp_background_task_id_tool_use_ids(
        entries,
        mcp_ids,
        include_sidechain=include_sidechain,
    )
    task_id_map = _collect_task_id_tool_use_ids(entries, include_sidechain=include_sidechain)
    background_task_id_map = _collect_background_task_id_tool_use_ids(
        entries,
        include_sidechain=include_sidechain,
    )
    for task_id, tool_use_ids in mcp_background_tasks.items():
        task_id_map.setdefault(task_id, set()).update({task_id, *tool_use_ids})
    if "agent" in kinds and not include_sidechain:
        nested_launched, nested_task_id_map = _collect_nested_agent_launches(transcript_path)
        launched.update(nested_launched)
        for task_id, tool_use_ids in nested_task_id_map.items():
            task_id_map.setdefault(task_id, set()).update(tool_use_ids)
    monitor_task_ids = _collect_monitor_task_ids(entries, include_sidechain=include_sidechain)
    for entry in entries:
        if not _entry_in_scan_scope(entry, include_sidechain=include_sidechain):
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
            elif (
                isinstance(tool_use_result, dict)
                and isinstance(tool_use_result.get("backgroundTaskId"), str)
                and "bash" in kinds
            ):
                tool_use_id = _extract_tool_result_id(message)
                if tool_use_id is not None:
                    launched.add(tool_use_id)
            elif "sendmessage" in kinds:
                resumed_id = _extract_sendmessage_bg_resume_id(message, tool_use_result, sendmessage_ids)
                if resumed_id is not None:
                    launched.add(resumed_id)
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
    return launched, completed


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
    ログ出力せず黙って無視する。含まれない場合のみ、`session_id`が与えられていれば
    `append_stop_log`で明示ログ出力する。
    旧形式（メインuserエントリの`<task-notification>`）・新形式
    （`type=="attachment"`の`<task-notification>`）の両解決経路が共有する。
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
    契約とは別に、Stop hookが生transcriptを観測するための経路である。
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


def _collect_nested_agent_launches(
    transcript_path: str | None,
) -> tuple[set[str], dict[str, set[str]]]:
    """直接の子transcriptから孫Agent起動とtask-id対応表を収集する。

    Claude Codeの子transcriptは最上位transcriptと同じファイル名stemの
    `subagents`ディレクトリへ配置される。入力値のagent IDをパスへ連結せず、固定の
    `agent-*.jsonl`列挙とmetadataの親子関係だけで直接の子を選別する。子記録の欠落、
    破損又は不正なmetadataは、最上位transcriptの既存判定を維持するため無視する。
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
        except (OSError, ValueError):
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


def _log_unresolved_completion(session_id: str | None, detail: str) -> None:
    """未解決の完了経路を既存の通知未解決ログ形式で記録する。"""
    if session_id is None:
        return
    append_stop_log(
        session_id,
        "task_notification_unresolved",
        {"notification": detail[:500]},
    )


def _collect_sendmessage_tool_use_ids(entries: list[dict], *, include_sidechain: bool = False) -> set[str]:
    """走査範囲内のassistantエントリからSendMessage tool_use id集合を返す。"""
    ids: set[str] = set()
    for block in _iter_assistant_blocks(entries, include_sidechain=include_sidechain):
        if block.get("type") != "tool_use" or block.get("name") != "SendMessage":
            continue
        block_id = block.get("id")
        if isinstance(block_id, str):
            ids.add(block_id)
    return ids


def _collect_agent_tool_use_ids(entries: list[dict], *, include_sidechain: bool = False) -> set[str]:
    """走査範囲内のassistantエントリからAgent・Task tool_use id集合を返す。"""
    ids: set[str] = set()
    for block in _iter_assistant_blocks(entries, include_sidechain=include_sidechain):
        if block.get("type") != "tool_use" or block.get("name") not in {"Agent", "Task"}:
            continue
        block_id = block.get("id")
        if isinstance(block_id, str):
            ids.add(block_id)
    return ids


def _collect_mcp_tool_use_ids(entries: list[dict], *, include_sidechain: bool = False) -> set[str]:
    """走査範囲内のassistantエントリからMCP tool_use id集合を返す。"""
    ids: set[str] = set()
    for block in _iter_assistant_blocks(entries, include_sidechain=include_sidechain):
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
    """MCPタイムアウト通知の背景タスクIDと起動`tool_use` IDの対応を全`tool_result`から収集する。"""
    result: dict[str, set[str]] = {}
    for entry in entries:
        if entry.get("type") != "user" or not _entry_in_scan_scope(
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
            for text in _tool_result_text_blocks(block.get("content")):
                task_id = background_task_id_from_notice(text)
                if task_id is not None:
                    result.setdefault(task_id, set()).add(tool_use_id)
    return result


def background_task_id_from_notice(value: object) -> str | None:
    """MCP呼び出しの背景移行通知からタスクIDを返す。"""
    if isinstance(value, str):
        match = _MCP_BACKGROUND_TASK_RE.search(value)
        return match.group(1) if match is not None else None
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

    起動を記録した`toolUseResult`に`agentId`（背景タスクの`task-id`）が含まれる場合、
    task-notification本文の`<task-id>`要素経由での完了突合をフォールバックとして提供する。
    `<tool-use-id>`要素が通知形式変動で欠落した場合の解決経路として用いる。
    """
    result: dict[str, set[str]] = {}
    for entry in entries:
        if entry.get("type") != "user" or not _entry_in_scan_scope(
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
        tool_use_id = _extract_tool_result_id(message)
        if tool_use_id is None:
            continue
        result.setdefault(agent_id, set()).add(tool_use_id)
    return result


def _collect_background_task_id_tool_use_ids(
    entries: list[dict],
    *,
    include_sidechain: bool = False,
) -> dict[str, set[str]]:
    """背景Bashの`backgroundTaskId`から起動`tool_use_id`への対応を返す。"""
    result: dict[str, set[str]] = {}
    for entry in entries:
        if entry.get("type") != "user" or not _entry_in_scan_scope(
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
        tool_use_id = _extract_tool_result_id(message)
        if tool_use_id is not None:
            result.setdefault(task_id, set()).add(tool_use_id)
    return result


def _collect_monitor_task_ids(entries: list[dict], *, include_sidechain: bool = False) -> set[str]:
    """transcript全行から、Monitorツール起動に一意に対応する`taskId`の集合を返す。

    `toolUseResult.taskId`キーはMonitor専用ではなく、他のツール
    （`success`・`updatedFields`・`statusChange`キーを伴う形で観測）も同じキー名を使う
    （実transcript調査で確認済み）。そのため`taskId`キーの存在だけでMonitor由来と判定せず、
    対応する`tool_use_id`（`_extract_tool_result_id`で解決）がassistant側`tool_use`ブロックの
    `name == "Monitor"`と一致する場合に限りMonitor由来として収集する。
    `<task-notification>`の解決では値（文字列）でしか突合できず`tool_use_id`を参照できないため、
    Monitor由来の値とMonitor以外由来の値が同一transcript内で衝突した場合、
    その値だけでは発生源を一意に特定できない。衝突した値は戻り値の集合から除外し、
    `_resolve_task_notification_ids`側がfail-closed（`task_notification_unresolved`をログする）で
    扱えるようにする。
    Monitorは`_describe_pending_background_tasks`の起動集合（`launched`）に加算されないため、
    Monitorの完了通知は常に`<task-id>`要素のフォールバック解決に失敗し
    `task_notification_unresolved`を誤って発生させる。本関数が返す集合は
    `_resolve_task_notification_ids`が当該通知を異常系ログから除外する判定に使う。
    """
    monitor_tool_use_ids: set[str] = set()
    for block in _iter_assistant_blocks(entries, include_sidechain=include_sidechain):
        if block.get("type") != "tool_use" or block.get("name") != "Monitor":
            continue
        block_id = block.get("id")
        if isinstance(block_id, str):
            monitor_tool_use_ids.add(block_id)

    monitor_task_ids: set[str] = set()
    non_monitor_task_ids: set[str] = set()
    for entry in entries:
        if entry.get("type") != "user" or not _entry_in_scan_scope(
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
        tool_use_id = _extract_tool_result_id(message)
        if tool_use_id is not None and tool_use_id in monitor_tool_use_ids:
            monitor_task_ids.add(task_id)
        else:
            non_monitor_task_ids.add(task_id)
    return monitor_task_ids - non_monitor_task_ids


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


def _extract_agent_launch_id(message: dict, tool_use_result: dict, agent_ids: set[str]) -> str | None:
    """Agent・Task結果が非同期起動を示す場合に`tool_use_id`を返す。"""
    tool_use_id = _extract_tool_result_id(message)
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
        if any(_AGENT_ASYNC_LAUNCH_MARKER in text for text in _tool_result_text_blocks(block.get("content"))):
            return tool_use_id
    return None


def _extract_sendmessage_bg_resume_id(message: dict, tool_use_result: object, sendmessage_ids: set[str]) -> str | None:
    """SendMessage由来のtool_resultが背景再開を示す場合に`tool_use_id`を返す。

    判定は`toolUseResult.resumedAgentId`が文字列として存在するかを第一とし、
    当該フィールドを欠く結果に限り本文の`_SENDMESSAGE_BG_RESUME_MARKER`照合へフォールバックする。
    ハーネスが返す応答文面は版更新で変わり字面一致では追随できないため、
    同じ結果が持つ構造化フィールドを検出契約の正本とし、文面照合は旧形式の後方互換に限る。
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


def _tool_result_text_blocks(content: object) -> list[str]:
    """tool_result本文を文字列列へ正規化する。"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]


def _extract_tool_result_id(message: dict) -> str | None:
    """userメッセージの`content`配列内の`tool_result`ブロックから`tool_use_id`を抽出する。"""
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result":
            continue
        tool_use_id = block.get("tool_use_id")
        if isinstance(tool_use_id, str):
            return tool_use_id
    return None


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
    `<tool-use-id>`要素で解決できない場合のフォールバック解決経路として用いる。
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
