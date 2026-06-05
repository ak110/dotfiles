"""Claude Code agent-toolkit: Stop hook 共通ゲートモジュール。

判定はtranscript JSONLの内容のみを根拠とする。
直前ターン単位の確認（完了文言・質問・待機語・最後のtool_useの種別）に加えて、
`toolUseResult.status == "async_launched"`で起動済みかつ後続の`<task-notification>`内
`<tool-use-id>`で完了通知が確認できないbackground Agentを、transcript全体から検出する。
background起動のサブエージェントが動作中の状態でStop hookが誤発動するのを避けるため、
本走査はターンをまたいで行う。
"""

import json
import pathlib
import re
import time

from _transcript import iter_latest_assistant_messages as _iter_latest_assistant_messages

# 作業完了を示す言い切り文言。
# 誤検出削減を最優先するため検出範囲を狭く限定し、過去形・現在形の言い切りに限定する。
# 「〜を完了します」（未来形）「〜すれば完了です」（条件形）のような文は
# 作業途中でも出現しうるため対象外とする。
_COMPLETION_KEYWORDS: tuple[str, ...] = (
    "push しました",
    "pushした。",
    "pushしました",
    "コミットした。",
    "コミット完了",
    "完了。",
    "完了いたしました",
    "完了した。",
    "完了しています。",
    "完了しました",
    "完了です",
    "反映しました",
    "完了致しました",
    "作業終了。",
)

# バックグラウンド待機中・非同期待機中を示す語。
# これらを含むターンは「真のセッション終了」ではなく一時停止と判断し、
# blockを抑止する。
_WAITING_KEYWORDS: tuple[str, ...] = (
    "待ちます",
    "完了を待",
    "通知を待",
    "バックグラウンド",
    "background",
    "待機します",
    "待機中",
    "完了するまで待",
    "終了を待",
    "起動を待",
)

# 非同期待機系ツール名。これらのtool_useで直前アシスタントターンが終端している場合は
# 「真のセッション終了」ではないと判断する。
# Bashはrun_in_backgroundフラグで別途判定するため、ここには含めない。
_ASYNC_WAIT_TOOLS: frozenset[str] = frozenset({"Agent", "ScheduleWakeup", "Monitor"})

# `<task-notification>...</task-notification>`要素を非貪欲に切り出す正規表現。
# `re.DOTALL`で本文中の改行も拾う。
_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)

# `<task-notification>`要素内の`<tool-use-id>toolu_xxx</tool-use-id>`から
# `toolu_xxx`を抽出する正規表現。
_TOOL_USE_ID_RE = re.compile(r"<tool-use-id>(toolu_[\w]+)</tool-use-id>")


def is_real_session_end(transcript_path: str) -> bool:
    """transcriptを解析して「真のセッション終了」かどうかを判定する。

    以下の条件をすべて満たす場合にTrueを返す。
    - 直前アシスタントターンに作業完了の言い切り文言が含まれる
    - 直前アシスタントターンが質問を含まない
    - 直前アシスタントターンが待機語を含まない
    - 直前アシスタントターンの最後のtool_useが非同期待機系でない
    - 未完了のbackground起動サブエージェント（Agent tool）が存在しない

    最後の条件はtranscript全体を走査して判定する。
    メイン側（`isSidechain`が真でない）userエントリのうち
    `toolUseResult.status == "async_launched"`を持つものから起動済み`tool_use_id`集合を採取し、
    後続のメイン側userエントリのtext content内に含まれる`<task-notification>`要素から
    `<tool-use-id>`を抽出して除外する。残差が1件以上で「未完了サブエージェントあり」と判断する。

    transcriptを読み取れない異常系ではFalseを返す（安全側に倒す）。
    """
    _wait_for_end_turn(transcript_path)
    if not _is_assistant_task_completed(transcript_path):
        return False
    if _is_assistant_asking_question(transcript_path):
        return False
    if _is_assistant_waiting(transcript_path):
        return False
    if _last_tool_use_is_async_wait(transcript_path):
        return False
    return not _has_pending_subagents(transcript_path)


def _wait_for_end_turn(transcript_path: str, *, timeout: float = 0.3) -> None:
    """Stop hook起動とClaude Code側transcriptフラッシュとのレース状態を吸収する。

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


def _is_assistant_task_completed(transcript_path: str) -> bool:
    """直前のアシスタントターンに作業完了の言い切り文言が含まれる場合に真を返す。

    キーワードは`_COMPLETION_KEYWORDS`参照。
    """
    for message in _iter_latest_assistant_messages(transcript_path):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text", "")
            if not isinstance(text, str):
                continue
            if any(keyword in text for keyword in _COMPLETION_KEYWORDS):
                return True
    return False


def _is_assistant_asking_question(transcript_path: str) -> bool:
    """直前のアシスタントターンがユーザーへの質問を含む場合に真を返す。

    以下のいずれかが成立する場合に真を返す。
    - AskUserQuestionツール呼び出しが含まれている
    - テキストに?または？または「ですか。」が含まれている（位置は問わない）

    「ですか。」を含める理由:「この案でよいですか。」のように`?`を付けずに
    ユーザー確認を求めるケースを拾うため。

    末尾判定にしない理由: アシスタントが質問文の後に補足・締めの文を書くケース
    （例:「…どうしますか？ お手数ですがご確認ください。」）で末尾に`?`が来ず、
    false positiveでコミットを強行する挙動を避けるため。

    同一`message.id`を持つ複数エントリ（テキストとツール呼び出しが分割される等）は
    1ターンとして統合して判定する。走査の詳細は`_iter_latest_assistant_messages`を参照。
    """
    for message in _iter_latest_assistant_messages(transcript_path):
        content = message.get("content")
        if not isinstance(content, list):
            return False
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                return True
            if block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
        if texts:
            joined = "\n".join(texts)
            return "?" in joined or "？" in joined or "ですか。" in joined
        # テキストなしエントリ → 同一ターンの前のエントリを確認する（ループ継続）
    return False


def _is_assistant_waiting(transcript_path: str) -> bool:
    """直前のアシスタントターンが待機中を示す語を含む場合に真を返す。

    `_WAITING_KEYWORDS`のいずれかを含むテキストブロックがあれば真を返す。
    バックグラウンド待機・非同期処理待ちの誤発動を防ぐためのゲート。

    大文字小文字を区別しないため、英語キーワードは大小様々な表記に一致する。
    """
    for message in _iter_latest_assistant_messages(transcript_path):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text", "")
            if not isinstance(text, str):
                continue
            if any(keyword.lower() in text.lower() for keyword in _WAITING_KEYWORDS):
                return True
    return False


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


def _has_pending_subagents(transcript_path: str) -> bool:
    r"""transcript全体を走査して未完了のbackground Agentが存在する場合に真を返す。

    検出スコープはメイン側（`isSidechain`が真でない）userエントリに限定する。
    foreground起動のAgentはメインターン内で同期完了するため対象外。

    起動の記録: `toolUseResult.status == "async_launched"`を持つuserエントリ。
    `message.content`配列内の`tool_result`ブロックから`tool_use_id`を取得する。

    完了の記録: メイン側userエントリのtext content内に含まれる`<task-notification>`要素から
    `<tool-use-id>(toolu_[\\w]+)</tool-use-id>`を抽出する。
    `<status>`の値（`completed`・`failed`・`cancelled`等）は問わず終了扱いとする。

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
        if isinstance(tool_use_result, dict) and tool_use_result.get("status") == "async_launched":
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
