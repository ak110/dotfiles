"""Claude Code agent-toolkit: Stop hook 共通ゲートモジュール。

判定はtranscript JSONLの内容のみを根拠とする。
本モジュールは構造的な継続判定（`is_pending_async_work`）と
スキル起動履歴の検出（`has_session_review_skill_invoked`）を提供する。
完了文言・質問・待機語など言語面の判定はLLM側（スキル本体の起動方針節）へ委譲する。
"""

import json
import pathlib
import re
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


def is_pending_async_work(transcript_path: str) -> bool:
    """セッションが構造的に継続中の場合に真を返す。

    以下のいずれかの場合に真を返す。
    - 直前アシスタントターンの最後のtool_useが非同期待機系（`Agent`・`ScheduleWakeup`・
      `Monitor`、または`Bash`かつ`input.run_in_background == true`）
    - 未完了のbackground task（Agent・Bash双方）が存在する

    後者はtranscript全体を走査して判定する。
    メイン側（`isSidechain`が真でない）userエントリのうち、
    `toolUseResult.status == "async_launched"`（背景Agent起動）または
    `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）ものから
    起動済み`tool_use_id`集合を抽出し、
    後続のメイン側userエントリのtext content内に含まれる`<task-notification>`要素から
    `<tool-use-id>`を抽出して除外する。残差が1件以上で「未完了background taskあり」と判断する。

    transcriptを読み取れない異常系では偽を返す（Stopを抑止しない方向で動作する）。
    """
    _wait_for_end_turn(transcript_path)
    if _last_tool_use_is_async_wait(transcript_path):
        return True
    return _has_pending_background_tasks(transcript_path)


def has_session_review_skill_invoked(transcript_path: str, skill_name: str) -> bool:
    """メイン側assistantエントリの`Skill`ツール呼び出しで指定スキルが起動された痕跡を検出する。

    対象はメインのtranscript（`isSidechain`が真でないエントリ）に限定する。
    `Skill`ツールの`input.skill`が`skill_name`と完全一致する呼び出しを1件でも検出すれば真を返す。

    transcriptを読み取れない異常系では偽を返す（Stopを抑止しない方向で動作する）。
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
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use" or block.get("name") != "Skill":
                continue
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                continue
            if tool_input.get("skill") == skill_name:
                return True
    return False


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


def _last_tool_use_is_async_wait(transcript_path: str) -> bool:
    """直前アシスタントターンの最後のtool_useが非同期待機系の場合に真を返す。

    `_ASYNC_WAIT_TOOLS`に含まれるツール名、または`Bash`かつ
    `input.run_in_background == true`の場合に真を返す。
    バックグラウンド処理中のStop hook誤発動を防ぐためのゲート。
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
        # 最初に得た（最新の）メッセージのtool_useのみ対象。
        # ターン内で最後に出現したtool_useを使うため、
        # メッセージをまたいで探さない。
        if last_tool_use is not None:
            break
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


def _has_pending_background_tasks(transcript_path: str) -> bool:
    r"""transcript全体を走査して未完了のbackground task（Agent・Bash双方）が存在する場合に真を返す。

    検出スコープはメイン側（`isSidechain`が真でない）userエントリに限定する。
    foreground起動のAgentはメインターン内で同期完了するため対象外。

    起動の記録: 次のいずれかを持つuserエントリ。
    - `toolUseResult.status == "async_launched"`（背景Agent起動）
    - `toolUseResult.backgroundTaskId`が文字列として存在する（背景Bash起動）

    いずれの場合も`message.content`配列内の`tool_result`ブロックから`tool_use_id`を取得する。

    完了の記録: メイン側userエントリのtext content内に含まれる`<task-notification>`要素から
    `<tool-use-id>(toolu_[\\w]+)</tool-use-id>`を抽出する。
    `<status>`の値（`completed`・`failed`・`cancelled`等）は問わず終了扱いとする。
    Agent・Bashとも同一機構で通知されるため共通の抽出処理を用いる。

    起動集合から完了集合を差し引いて1件以上残れば真。
    transcript読み取り失敗時は偽を返す（安全側に倒し、既存条件で抑止または通過させる）。
    """
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    launched: set[str] = set()
    completed: set[str] = set()
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
        tool_use_result = entry.get("toolUseResult")
        if isinstance(tool_use_result, dict) and (
            tool_use_result.get("status") == "async_launched" or isinstance(tool_use_result.get("backgroundTaskId"), str)
        ):
            tool_use_id = _extract_tool_result_id(message)
            if tool_use_id is not None:
                launched.add(tool_use_id)
        completed.update(_extract_task_notification_ids(message))
    return bool(launched - completed)


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
