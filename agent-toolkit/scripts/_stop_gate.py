"""Claude Code agent-toolkit: Stop hook 共通ゲートモジュール。

判定はtranscript JSONLの内容のみを根拠とする。
本モジュールは構造的な継続判定（`is_pending_async_work`）を提供する。
完了文言・質問・待機語など言語面の判定はLLM側（スキル本体の起動方針節）へ委譲する。
"""

import json
import os
import pathlib
import re
import sys
import time

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

# `AGENT_TOOLKIT_STOP_GATE_DEBUG`環境変数の真値集合。小文字一致で判定する。
_DEBUG_TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def is_pending_async_work(transcript_path: str) -> bool:
    """セッションが構造的に継続中の場合に真を返す。

    以下のいずれかの場合に真を返す。
    - 直前アシスタントターンの最後のtool_useが非同期待機系（`Agent`・`ScheduleWakeup`・
      `Monitor`、または`Bash`かつ`input.run_in_background == true`）
    - 未完了のbackground task（Agent・Bash双方）が存在する

    後者はtranscript全体を走査して判定する。
    起動集合は非sidechainの`type=="user"`エントリのうち、
    `toolUseResult.status == "async_launched"`（背景Agent起動）または
    `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）ものから抽出する。
    完了集合は後続エントリの`<task-notification>`要素から`<tool-use-id>`を抽出する。
    完了通知エントリは次の2形式が併存する。
    - 旧形式: 非sidechainの`type=="user"`エントリのtext content内に含まれる`<task-notification>`要素
    - 新形式: `type=="attachment"`かつ`attachment.commandMode=="task-notification"`のエントリの
      `attachment.prompt`文字列に含まれる`<task-notification>`要素（Claude Code 2.1系以降）
    起動集合から完了集合を差し引いて1件以上残れば「未完了background taskあり」と判断する。

    transcriptを読み取れない異常系では偽を返す（Stopを抑止しない方向で動作する）。
    """
    _wait_for_end_turn(transcript_path)
    last_async = _last_tool_use_is_async_wait(transcript_path)
    pending = last_async or _has_pending_background_tasks(transcript_path)
    _emit_debug(transcript_path, pending)
    return pending


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


def _has_pending_background_tasks(transcript_path: str) -> bool:
    r"""transcript全体を走査して未完了のbackground task（Agent・Bash双方）が存在する場合に真を返す。

    検出スコープは非sidechainエントリに限定する（`isSidechain`が真のエントリは除外）。
    foreground起動のAgentはメインターン内で同期完了するため対象外。

    起動の記録: 次のいずれかを持つuserエントリ。
    - `toolUseResult.status == "async_launched"`（背景Agent起動）
    - `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）

    いずれの場合も`message.content`配列内の`tool_result`ブロックから`tool_use_id`を取得する。

    完了の記録: 次の2形式の`<task-notification>`要素から
    `<tool-use-id>(toolu_[\\w]+)</tool-use-id>`を抽出する。
    - 旧形式: 非sidechainのメイン側userエントリの`message.content`内テキストブロック
    - 新形式: `type=="attachment"`かつ`attachment.commandMode=="task-notification"`のエントリの
      `attachment.prompt`文字列（Claude Code 2.1系以降で観測される形式）

    `<status>`の値（`completed`・`failed`・`cancelled`等）は問わず終了扱いとする。
    Agent・Bashとも同一機構で通知されるため共通の抽出処理を用いる。

    起動集合から完了集合を差し引いて1件以上残れば真。
    transcript読み取り失敗時は偽を返す（安全側に倒し、既存条件で抑止または通過させる）。
    """
    launched, completed = _describe_pending_background_tasks(transcript_path)
    return bool(launched - completed)


def _describe_pending_background_tasks(transcript_path: str) -> tuple[set[str], set[str]]:
    """transcript全体から背景タスクの起動集合と完了集合を抽出する。

    走査ルールは`_has_pending_background_tasks`のdocstringに記載した条件と同一。
    本関数は集合自体を返し、`_has_pending_background_tasks`は残差判定のみ、
    `_emit_debug`は集合サイズと残差ID列挙に使う。
    transcript読み取り失敗時は空集合のペアを返す。
    """
    launched: set[str] = set()
    completed: set[str] = set()
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return launched, completed
    for line in lines:
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
                tool_use_result.get("status") == "async_launched" or isinstance(tool_use_result.get("backgroundTaskId"), str)
            ):
                tool_use_id = _extract_tool_result_id(message)
                if tool_use_id is not None:
                    launched.add(tool_use_id)
            completed.update(_extract_task_notification_ids(message))
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
                completed.update(_TOOL_USE_ID_RE.findall(notification))
    return launched, completed


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


def _extract_task_notification_ids(message: dict) -> set[str]:
    """userメッセージの`content`内の`<task-notification>`要素から完了`tool_use_id`を抽出する。

    `content`が文字列（旧フォーマット）でも配列（実transcriptフォーマット）でも処理する。
    """
    result: set[str] = set()
    content = message.get("content")
    if isinstance(content, str):
        for notification in _TASK_NOTIFICATION_RE.findall(content):
            result.update(_TOOL_USE_ID_RE.findall(notification))
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
            result.update(_TOOL_USE_ID_RE.findall(notification))
    return result
