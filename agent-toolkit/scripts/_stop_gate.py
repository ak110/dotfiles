"""Claude Code agent-toolkit: Stop hook 共通ゲートモジュール。

判定はtranscript JSONLの内容のみを根拠とする。
本モジュールは構造的な継続判定（`is_pending_async_work`）を提供する。
完了文言・質問・待機語など言語面の判定はLLM側（スキル本体の起動方針節）へ委譲する。

background task起動の検出条件は次の4種を統合して扱う。
- `toolUseResult.status == "async_launched"`（背景Agent初回起動）
- `toolUseResult.status == "teammate_spawned"`（`name`付きteammate並列起動）
- `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）
- `tool_result`ブロックの`tool_use_id`がSendMessage呼び出し由来かつtext本文に
  `_SENDMESSAGE_BG_RESUME_MARKER`を含む（SendMessageによるサブエージェント背景再開）

SendMessage背景再開は前3者と異なり`toolUseResult`側に識別子を持たないため、
テキストマーカー判定でSendMessage呼び出し由来のtool_resultに限定して識別する。

`name`付きteammateの完了通知は`<task-notification>`要素ではなく
`<teammate-message>`要素内のidle_notification(idleReason=available)として記録される。
そのため完了検知でも当該経路を追加し、teammate_id→tool_use_id集合マップで解決する。

`<task-notification>`要素に`<tool-use-id>`が含まれない通知形式では、
`<task-id>`要素とagentId→tool_use_id集合マップ（`_collect_task_id_tool_use_ids`）による
フォールバック解決を行う。両者で解決できない通知は`task_notification_unresolved`として
常時ログへ明示出力し、通知形式変動による幽霊pendingの発生を検出可能にする。

常時ログ（`append_stop_log`）と詳細stderr出力（`_emit_debug`）は責務を分離する。
常時ログはINFO相当（呼び出し側が渡す最終判定ラベルと主要フラグ）を
`{tempdir}/claude-agent-toolkit-stop-{session_id}.log`へ1行ずつ追記し、
1MB超過時に`.log.1`へ1世代ローリングする。詳細stderr出力は
環境変数`AGENT_TOOLKIT_STOP_GATE_DEBUG`が真値の場合のみ発火するDEBUG相当
（last_tool・launched・pending・pending_ids）で、原因切り分け用途に限定する。
"""

import collections.abc
import json
import os
import pathlib
import re
import sys
import tempfile
import time

from _file_lock import rotate_if_needed as _rotate_if_needed
from _transcript import iter_assistant_content_blocks as _iter_assistant_content_blocks
from _transcript import iter_latest_assistant_messages as _iter_latest_assistant_messages

# 非同期待機系ツール名。これらのtool_useで直前アシスタントターンが終端している場合は
# セッション継続中と判断する。
# Bashはrun_in_backgroundフラグで別途判定するため、ここには含めない。
_ASYNC_WAIT_TOOLS: frozenset[str] = frozenset({"Agent", "ScheduleWakeup", "Monitor"})

# `<task-notification>...</task-notification>`要素を非貪欲に切り出す正規表現。
# `re.DOTALL`で本文中の改行も拾う。
_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)

# `<task-notification>`要素内の`<tool-use-id>toolu_xxx</tool-use-id>`から
# `toolu_xxx`を抽出する正規表現。
_TOOL_USE_ID_RE = re.compile(r"<tool-use-id>(toolu_[\w]+)</tool-use-id>")

# `<task-id>...</task-id>`要素からagentId（task-id）を抽出する正規表現。
# task-notification本文に`<tool-use-id>`が含まれない形式のフォールバック解決に用いる。
_TASK_ID_RE = re.compile(r"<task-id>([^<]+)</task-id>")

# SendMessage背景再開時のtool_result text先頭に現れる固有マーカー。
# `Agent ... resumed from transcript in the background with your message.`形式で
# 出力されるため、`resumed from transcript in the background`を一致条件とする。
# 同期SendMessage応答は本文言を含まないため、背景再開ケースのみ加算される。
_SENDMESSAGE_BG_RESUME_MARKER = "resumed from transcript in the background"

# `<teammate-message teammate_id="X" ...>BODY</teammate-message>`要素を切り出す正規表現。
# `name`付きで並列起動したサブエージェント（teammate）の完了通知はattachmentの
# `task-notification`ではなくuserエントリのtext本文へ`<teammate-message>`要素として
# 記録される。BODYはJSON文字列で、`{"type":"idle_notification", "idleReason":"available", ...}`
# 形式のとき当該teammateが待機（呼び出し元による続行可能）状態へ移行したことを表す。
_TEAMMATE_MESSAGE_RE = re.compile(
    r'<teammate-message[^>]*teammate_id="([^"]+)"[^>]*>(.*?)</teammate-message>',
    re.DOTALL,
)

# `AGENT_TOOLKIT_STOP_GATE_DEBUG`環境変数の真値集合。小文字一致で判定する。
_DEBUG_TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def is_pending_async_work(transcript_path: str, session_id: str) -> bool:
    """セッションが構造的に継続中の場合に真を返す。

    以下のいずれかの場合に真を返す。
    - 直前アシスタントターンの最後のtool_useが非同期待機系（`Agent`・`ScheduleWakeup`・
      `Monitor`、または`Bash`かつ`input.run_in_background == true`）
    - 未完了のbackground task（Agent・Bash・SendMessage背景再開・named teammate）が存在する

    後者はtranscript全体を走査して判定する。
    起動集合は非sidechainの`type=="user"`エントリのうち、次のいずれかを持つものから抽出する。
    - `toolUseResult.status == "async_launched"`（背景Agent起動）
    - `toolUseResult.status == "teammate_spawned"`（`name`付きteammate並列起動）
    - `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）
    - `message.content`内の`tool_result`ブロックの`tool_use_id`がSendMessage呼び出し由来かつ
      text本文に`_SENDMESSAGE_BG_RESUME_MARKER`を含む（SendMessageによるサブエージェント背景再開）

    完了集合は後続エントリの`<task-notification>`要素から`<tool-use-id>`を抽出し、
    さらに`<teammate-message>`要素内のidle_notification(available)から
    teammate_id→tool_use_id集合マップで解決したidを追加する。
    完了通知エントリは次の3形式が併存する。
    - 旧形式: 非sidechainの`type=="user"`エントリのtext content内に含まれる`<task-notification>`要素
    - 新形式: `type=="attachment"`かつ`attachment.commandMode=="task-notification"`のエントリの
      `attachment.prompt`文字列に含まれる`<task-notification>`要素（Claude Code 2.1系以降）
    - teammate形式: 非sidechainの`type=="user"`エントリのtext content内に含まれる
      `<teammate-message>`要素body部（JSON）で`type=="idle_notification"`かつ
      `idleReason=="available"`のもの
    `<task-notification>`要素に`<tool-use-id>`が含まれない場合は`<task-id>`要素と
    agentId→tool_use_id集合マップによるフォールバック解決を試み、それでも解決できない通知は
    `task_notification_unresolved`として常時ログへ明示出力する。
    起動集合から完了集合を差し引いて1件以上残れば「未完了background taskあり」と判断する。

    transcriptを読み取れない異常系では偽を返す（Stopを抑止しない方向で動作する）。
    `session_id`は常時ログ（`append_stop_log`）の宛先ファイル特定にのみ使う。
    """
    _wait_for_end_turn(transcript_path)
    last_async = _last_tool_use_is_async_wait(transcript_path)
    launched, completed = _describe_pending_background_tasks(transcript_path, session_id)
    remainder = launched - completed
    pending = last_async or bool(remainder)
    _emit_debug(transcript_path, pending)
    append_stop_log(
        session_id,
        "is_pending_async_work_result",
        {
            "result": pending,
            "last_tool": _describe_last_tool_use(transcript_path),
            "launched": len(launched),
            "pending": len(remainder),
            "pending_ids": ",".join(sorted(remainder)[:3]) if remainder else "-",
        },
    )
    return pending


def has_pending_background_launches(transcript_path: str, session_id: str) -> bool:
    """ハーネスが追跡するbackgroundタスクのうち、完了未消化のものが1件以上あれば真を返す。

    `is_pending_async_work`と同じ`launched - completed`のremainder非空判定を用いる
    （起動記録の存在だけで判定すると完了消化後も真を返し続けるため、消化状態を必ず反映する）。
    stop_advisor側でasync-waitカテゴリ検出時の除外判定に使う。
    起動集合・完了集合の抽出条件は`is_pending_async_work`docstringに定義済み
    （`toolUseResult.status`・`backgroundTaskId`・SendMessage背景再開マーカー・
    `<task-notification>`・idle_notification(available)の各条件）。

    transcript読み取り失敗時は偽を返す（fail-closed。ブロック維持側で動作する）。
    """
    launched, completed = _describe_pending_background_tasks(transcript_path, session_id)
    return bool(launched - completed)


def has_command_invocation(transcript_path: str, pattern: re.Pattern[str]) -> bool:
    """transcript内のユーザーターンに`pattern`一致のスラッシュコマンド起動痕跡があるか確認する。

    スラッシュコマンド起動はUserPromptSubmit hook（`user_prompt_submit.py`）が
    `session_review_invoked`辞書へ記録する。
    本関数はUserPromptSubmit hookがfail-openで記録漏れした場合のsafety netとして、
    transcript走査による代替検出手段を提供する。
    非sidechainの`type=="user"`エントリの`message.content`を対象に走査する。
    transcript読み取り失敗時は偽を返す。
    """
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "user" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        if pattern.search(text):
            return True
    return False


def _stop_log_path(session_id: str) -> pathlib.Path:
    """常時ログの出力先パスを返す。

    `{tempdir}/claude-agent-toolkit-stop-{session_id}.log`形式とする。
    セッション状態ファイル（`_session_state.py`）と同じtempdir配下に置き、
    hostごとに衝突しないようsession_idで分離する。
    """
    return pathlib.Path(tempfile.gettempdir()) / f"claude-agent-toolkit-stop-{session_id}.log"


def append_stop_log(session_id: str, decision: str, context: dict, *, max_bytes: int = 1_000_000) -> None:
    """Stop hookの最終判定根拠を常時ログへ1行追記する。

    `decision`は呼び出し側が渡す最終判定ラベル（`approve_no_pyfltr`・
    `approve_pending_async`・`approve_review_invoked`・`approve_stop_hook_active`・
    `block_session_review`など）。`context`は任意のkey-valueの辞書で、
    `last_tool`・`launched`・`pending`・`pending_ids`・`session_review_invoked`・
    `command_detected`等を呼び出し側が任意で埋める。

    出力形式: `{ISO8601時刻} decision={...} k1=v1 k2=v2 ...`（1行）。
    `session_id`が空の場合はログ書き込みをスキップする。
    書き込み失敗（権限不足等）はStop hook本体の動作へ影響させないため無視する。
    `max_bytes`はローテーション閾値の注入点で、テストから小さい値を渡してローテーション動作を検証できる。
    """
    if not session_id:
        return
    path = _stop_log_path(session_id)
    _rotate_if_needed(path, max_bytes)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    fields = " ".join(f"{key}={value}" for key, value in context.items())
    line = f"{timestamp} decision={decision}" + (f" {fields}" if fields else "") + "\n"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        return


def parse_stop_session(raw_stdin: str, approve: collections.abc.Callable[[], None]) -> tuple[str, dict] | None:
    """Stop系hook共通の前段処理。ペイロード解析とsession_id検証を行う。

    JSON解析失敗またはsession_id欠落時は`approve`を呼び出したうえで`None`を返す。
    正常時は`(session_id, payload)`を返す。`stop_hook_active`判定・環境変数判定等の
    後続分岐は呼び出し側ごとに判定順序（`claude_hook_autonomous_exit.py`は環境変数判定を
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


def _emit_debug(transcript_path: str, result: bool) -> None:
    """環境変数`AGENT_TOOLKIT_STOP_GATE_DEBUG`が真値の場合のみstderrへ判定根拠を1行出力する。

    出力形式は`key=value`空白区切りとする。
    Stop hookの誤判定時にlast_tool_use名・launched件数・残差件数・残差ID先頭3件から原因を切り分けるために用いる。
    """
    raw = os.environ.get("AGENT_TOOLKIT_STOP_GATE_DEBUG", "")
    if raw.lower() not in _DEBUG_TRUTHY_VALUES:
        return
    last_tool = _describe_last_tool_use(transcript_path)
    launched, completed = _describe_pending_background_tasks(transcript_path)
    remainder = launched - completed
    head_ids = ",".join(sorted(remainder)[:3]) if remainder else "-"
    print(
        f"_stop_gate result={result} last_tool={last_tool} "
        f"launched={len(launched)} pending={len(remainder)} pending_ids={head_ids}",
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


def _get_last_tool_use_block(transcript_path: str) -> dict | None:
    """最新assistantメッセージ内で最後に現れたtool_useブロックを返す。

    最初に得た（最新の）メッセージのtool_useのみ対象とし、ターン内で最後に出現したtool_useを使う。
    メッセージをまたいで探さない。tool_useが存在しない場合は`None`を返す。
    """
    last_tool_use: dict | None = None
    for message in _iter_latest_assistant_messages(transcript_path):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                last_tool_use = block
        if last_tool_use is not None:
            break
    return last_tool_use


def _last_tool_use_is_async_wait(transcript_path: str) -> bool:
    """直前アシスタントターンの最後のtool_useが非同期待機系の場合に真を返す。

    `_ASYNC_WAIT_TOOLS`に含まれるツール名、または`Bash`かつ
    `input.run_in_background == true`の場合に真を返す。
    バックグラウンド処理中のStop hook誤発動を防ぐためのゲート。
    """
    last_tool_use = _get_last_tool_use_block(transcript_path)
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


def _describe_last_tool_use(transcript_path: str) -> str:
    """最新assistantターン末尾のtool_use名をデバッグ出力向けに整形して返す。

    Bashの場合は`Bash(bg=True)`または`Bash(bg=False)`形式で返す。
    tool_useが存在しない場合は`-`を返す。
    """
    last_tool_use = _get_last_tool_use_block(transcript_path)
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
) -> tuple[set[str], set[str]]:
    r"""transcript全体から背景タスクの起動集合と完了集合を抽出する。

    検出スコープは非sidechainエントリに限定する（`isSidechain`が真のエントリは除外）。
    foreground起動のAgentはメインターン内で同期完了するため対象外。

    起動の記録: 次のいずれかを持つuserエントリ。
    - `toolUseResult.status == "async_launched"`（背景Agent起動）
    - `toolUseResult.status == "teammate_spawned"`（`name`付きteammate並列起動）
    - `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）
    - `message.content`内の`tool_result`ブロックの`tool_use_id`がSendMessage呼び出し由来かつ
      text本文に`_SENDMESSAGE_BG_RESUME_MARKER`を含む（SendMessageによるサブエージェント背景再開）

    完了の記録: 次の3形式から`tool_use_id`を抽出する。
    - 旧形式: 非sidechainのメイン側userエントリの`message.content`内テキストブロックの
      `<task-notification>`要素の`<tool-use-id>(toolu_[\\w]+)</tool-use-id>`
    - 新形式: `type=="attachment"`かつ`attachment.commandMode=="task-notification"`のエントリの
      `attachment.prompt`文字列（Claude Code 2.1系以降で観測される形式）
    - teammate形式: 非sidechainのメイン側userエントリの`message.content`内テキストブロックの
      `<teammate-message teammate_id="X">`要素body部（JSON）で`type=="idle_notification"`かつ
      `idleReason=="available"`のもの。teammate_id `X`をname→tool_use_id集合マップで解決する
    旧形式・新形式とも`<tool-use-id>`要素が欠落する通知は`<task-id>`要素と
    `_collect_task_id_tool_use_ids`が構築するagentId→tool_use_id集合マップ
    （`task_id_map`）で解決するフォールバック経路を共有ヘルパー
    `_resolve_task_notification_ids`経由で適用する。両者で解決できない通知は
    `task_notification_unresolved`として常時ログへ明示出力する。

    起動集合から完了集合を差し引いて1件以上残れば未完了背景タスクありと判定する。
    `<status>`の値（`completed`・`failed`・`cancelled`等）は問わず終了扱いとする。
    Agent・Bash・SendMessage背景再開とも同一の完了通知機構で通知され共通の抽出処理を用いる。
    `name`付きteammateだけteammate-message経路で完了通知が届くため、専用の抽出処理を追加する。
    transcript読み取り失敗時は空集合のペアを返す。

    走査は2段構成とする。
    第1段でtranscript全行から非sidechain assistantのSendMessage tool_use id集合と
    宛先別の行位置、および`name`付きAgent tool_useのname→tool_use_id集合マップを構築する。
    第2段ではtranscriptを時系列に走査し、`toolUseResult`条件に該当しないuserエントリに対して
    SendMessage集合を参照したテキストマーカー判定を追加し、背景再開のtool_resultを起動集合へ加算する。
    `<teammate-message>`要素内のidle_notification(available)を検出した場合は、
    name→tool_use_id集合マップで解決したtool_use_idを完了集合へ加算する。
    その後に同じteammate宛のSendMessageがあれば、対応するtool_use_idを起動集合へ戻して
    完了集合から除去する。再度idle_notification(available)を受信すれば完了集合へ戻す。
    """
    launched: set[str] = set()
    completed: set[str] = set()
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return launched, completed
    sendmessage_ids = _collect_sendmessage_tool_use_ids(lines)
    sendmessage_to_map = _collect_sendmessage_to_map(lines)
    sendmessage_names_by_position: dict[int, set[str]] = {}
    for teammate_name, positions in sendmessage_to_map.items():
        for position in positions:
            sendmessage_names_by_position.setdefault(position, set()).add(teammate_name)
    named_agent_ids = _collect_named_agent_tool_use_ids(lines)
    task_id_map = _collect_task_id_tool_use_ids(lines)
    idle_teammates: set[str] = set()
    for position, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("isSidechain"):
            continue
        entry_type = entry.get("type")
        if entry_type == "user":
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            tool_use_result = entry.get("toolUseResult")
            if isinstance(tool_use_result, dict) and (
                tool_use_result.get("status") == "async_launched"
                or tool_use_result.get("status") == "teammate_spawned"
                or isinstance(tool_use_result.get("backgroundTaskId"), str)
            ):
                tool_use_id = _extract_tool_result_id(message)
                if tool_use_id is not None:
                    launched.add(tool_use_id)
            else:
                resumed_id = _extract_sendmessage_bg_resume_id(message, sendmessage_ids)
                if resumed_id is not None:
                    launched.add(resumed_id)
            completed.update(_extract_task_notification_ids(message, task_id_map, session_id=session_id))
            for teammate_name in _extract_teammate_completion_names(message):
                completed.update(named_agent_ids.get(teammate_name, set()))
                idle_teammates.add(teammate_name)
        elif entry_type == "assistant":
            for teammate_name in sendmessage_names_by_position.get(position, set()):
                if teammate_name not in idle_teammates:
                    continue
                teammate_ids = named_agent_ids.get(teammate_name, set())
                launched.update(teammate_ids)
                completed.difference_update(teammate_ids)
                idle_teammates.remove(teammate_name)
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
                completed.update(_resolve_task_notification_ids(notification, task_id_map, session_id))
    return launched, completed


def _resolve_task_notification_ids(
    notification_text: str,
    task_id_map: dict[str, set[str]] | None,
    session_id: str | None,
) -> set[str]:
    """`<task-notification>`要素本文から完了`tool_use_id`集合を解決する。

    `<tool-use-id>`要素を優先して解決し、含まれない場合は`<task-id>`要素と
    `task_id_map`によるフォールバック解決を試みる。両者で解決できない場合、
    `session_id`が与えられていれば`append_stop_log`で明示ログ出力する。
    旧形式（メインuserエントリの`<task-notification>`）・新形式
    （`type=="attachment"`の`<task-notification>`）の両解決経路が共有する。
    """
    ids = set(_TOOL_USE_ID_RE.findall(notification_text))
    if ids:
        return ids
    resolved: set[str] = set()
    if task_id_map is not None:
        for task_id in _TASK_ID_RE.findall(notification_text):
            resolved.update(task_id_map.get(task_id, set()))
    if resolved:
        return resolved
    if session_id is not None:
        append_stop_log(
            session_id,
            "task_notification_unresolved",
            {"notification": notification_text[:500]},
        )
    return resolved


def _collect_sendmessage_tool_use_ids(lines: list[str]) -> set[str]:
    """transcript全行から非sidechain assistantのSendMessage tool_use idを集合として返す。"""
    ids: set[str] = set()
    for _position, block in _iter_assistant_content_blocks(lines):
        if block.get("type") != "tool_use" or block.get("name") != "SendMessage":
            continue
        block_id = block.get("id")
        if isinstance(block_id, str):
            ids.add(block_id)
    return ids


def _collect_sendmessage_to_map(lines: list[str]) -> dict[str, list[int]]:
    """非sidechain SendMessageの宛先名から行位置のリストへのマップを返す。"""
    result: dict[str, list[int]] = {}
    for position, block in _iter_assistant_content_blocks(lines):
        if block.get("type") != "tool_use" or block.get("name") != "SendMessage":
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            continue
        teammate_name = tool_input.get("to")
        if isinstance(teammate_name, str) and teammate_name:
            result.setdefault(teammate_name, []).append(position)
    return result


def _collect_task_id_tool_use_ids(lines: list[str]) -> dict[str, set[str]]:
    """transcript全行のuserエントリから、agentId（task-id）→tool_use_id集合マップを構築する。

    起動を記録した`toolUseResult`に`agentId`（背景タスクの`task-id`）が含まれる場合、
    task-notification本文の`<task-id>`要素経由での完了突合をフォールバックとして提供する。
    `<tool-use-id>`要素が通知形式変動で欠落した場合の解決経路として用いる。
    """
    result: dict[str, set[str]] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "user" or entry.get("isSidechain"):
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


def _collect_named_agent_tool_use_ids(lines: list[str]) -> dict[str, set[str]]:
    """transcript全行から非sidechain assistantの`name`付きAgent tool_use idをname別集合として返す。

    `name`付きteammate起動は同一名で複数回発行され得るため（並列起動・逐次再起動）、
    値は`set[str]`で保持する。teammate完了通知（idle_notification available）の
    `teammate_id`をキーとしてtool_use_id集合を解決する用途に用いる。
    """
    result: dict[str, set[str]] = {}
    for _position, block in _iter_assistant_content_blocks(lines):
        if block.get("type") != "tool_use" or block.get("name") not in ("Agent", "Task"):
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            continue
        name = tool_input.get("name")
        if not isinstance(name, str) or not name:
            continue
        block_id = block.get("id")
        if isinstance(block_id, str):
            result.setdefault(name, set()).add(block_id)
    return result


def _extract_teammate_completion_names(message: dict) -> set[str]:
    """userメッセージ内の`<teammate-message>`要素からidle_notification(available)のteammate_idを抽出する。

    body部分をJSONとしてパースし、`type == "idle_notification"`かつ
    `idleReason == "available"`のときのみteammate_idを結果へ加算する。
    JSONパース失敗・非該当のnotificationは無視する。
    `content`が文字列（旧フォーマット）でも配列（実transcriptフォーマット）でも処理する。
    """
    result: set[str] = set()

    def _scan_text(text: str) -> None:
        for match in _TEAMMATE_MESSAGE_RE.finditer(text):
            teammate_id = match.group(1)
            body = match.group(2).strip()
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") == "idle_notification" and data.get("idleReason") == "available":
                result.add(teammate_id)

    content = message.get("content")
    if isinstance(content, str):
        _scan_text(content)
        return result
    if not isinstance(content, list):
        return result
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text", "")
        if isinstance(text, str):
            _scan_text(text)
    return result


def _extract_sendmessage_bg_resume_id(message: dict, sendmessage_ids: set[str]) -> str | None:
    """SendMessage由来のtool_resultにSendMessage背景再開マーカーが含まれる場合に`tool_use_id`を返す。"""
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
) -> set[str]:
    """userメッセージの`content`内の`<task-notification>`要素から完了`tool_use_id`を抽出する。

    `content`が文字列（旧フォーマット）でも配列（実transcriptフォーマット）でも処理する。
    `task_id_map`はagentId（task-id要素の値）から`tool_use_id`集合へのマップで、
    `<tool-use-id>`要素で解決できない場合のフォールバック解決経路として用いる。
    両者で解決できない`<task-notification>`は`session_id`が与えられていれば
    `append_stop_log(..., "task_notification_unresolved", ...)`で明示ログ出力する。
    """
    result: set[str] = set()

    def _resolve_notification(notification_text: str) -> None:
        result.update(_resolve_task_notification_ids(notification_text, task_id_map, session_id))

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
