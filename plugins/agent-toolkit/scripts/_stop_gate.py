"""Claude Code agent-toolkit: Stop hook 共通ゲートモジュール。

dotfiles 側の Stop hook スクリプト (`scripts/claude_hook_stop.py`) と
`stop_advisor.py` の両方から import して使う。

公開 API: `is_real_session_end(transcript_path)` のみ。
PEP 723 ヘッダーなし（通常モジュールとして import 可能にするため）。
"""

import collections.abc
import json
import pathlib
import re
import time

# 作業完了を示す言い切り文言。
# 誤検出削減を最優先するため狭く絞り、過去形・現在形の言い切りに限定する。
# 「〜を完了します」（未来形）「〜すれば完了です」（条件形）のような文は
# 作業途中でも出現しうるため対象外とする。
_COMPLETION_KEYWORDS: tuple[str, ...] = (
    "完了しました",
    "完了いたしました",
    "完了致しました",
    "完了です",
    "完了。",
    "完了しています。",
    "コミット完了",
    "pushしました",
    "push しました",
)

# バックグラウンド待機中・非同期待機中を示す語。
# これらを含むターンは「真のセッション終了」ではなく一時停止と判断し、
# block を抑止する。
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

# 非同期待機系ツール名。これらの tool_use で直前アシスタントターンが終端している場合は
# 「真のセッション終了」ではないと判断する。
# Bash は run_in_background フラグで別途判定するため、ここには含めない。
_ASYNC_WAIT_TOOLS: frozenset[str] = frozenset({"Agent", "ScheduleWakeup", "Monitor"})

# Claude Code のハーネスが user turn 内に注入するタグ。
# stop_advisor.py の `_count_keywords` で user turn 本文から除去するために使う。
# ユーザー発話ではないため修正キーワード集計の対象外とする。
_INJECTED_TAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL),
    re.compile(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", re.DOTALL),
    re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.DOTALL),
    re.compile(r"<local-command-caveat>.*?</local-command-caveat>", re.DOTALL),
)


def is_real_session_end(transcript_path: str) -> bool:
    """Transcript を解析して「真のセッション終了」かどうかを判定する。

    以下の条件をすべて満たす場合に True を返す。
    - 直前アシスタントターンに作業完了の言い切り文言が含まれる
    - 直前アシスタントターンが質問を含まない
    - 直前アシスタントターンが待機語を含まない
    - 直前アシスタントターンの最後の tool_use が非同期待機系でない

    transcript を読み取れない異常系では False を返す（安全側に倒す）。
    """
    _wait_for_end_turn(transcript_path)
    if not _is_assistant_task_completed(transcript_path):
        return False
    if _is_assistant_asking_question(transcript_path):
        return False
    if _is_assistant_waiting(transcript_path):
        return False
    return not _last_tool_use_is_async_wait(transcript_path)


def _wait_for_end_turn(transcript_path: str, *, timeout: float = 0.3) -> None:
    """Stop hook 起動と Claude Code 側 transcript flush との race を吸収する。

    Claude Code は assistant 最終メッセージの transcript 書き込みと Stop hook 起動が
    並行することがあり、hook が読んだ時点で最終 assistant エントリが未到着の場合がある。
    末尾走査で最新 assistant エントリ（非 sidechain）の `stop_reason` が `end_turn` であれば
    flush 完了とみなし即時戻る。未到着なら短時間ポーリングし、`timeout` 経過で諦める。
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
            # 最新 assistant エントリが end_turn ではない（tool_use 等）→ race の可能性あり、
            # ポーリングを継続して最終エントリの到着を待つ。
            break
        if time.monotonic() >= deadline:
            return
        time.sleep(poll)


def _is_assistant_task_completed(transcript_path: str) -> bool:
    """直前のアシスタントターンに作業完了の言い切り文言が含まれるか判定する。

    キーワードは `_COMPLETION_KEYWORDS` 参照。
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
    """直前のアシスタントターンがユーザーへの質問を含んでいるかを確認する。

    以下のいずれかが成立する場合 True を返す。
    - AskUserQuestion ツール呼び出しが含まれている
    - テキストに ? または ？ または 「ですか。」が含まれている（位置は問わない）

    「ですか。」を含める理由:「この案でよいですか。」のように `?` を付けずに
    ユーザー確認を求めるケースを拾うため。

    末尾判定にしない理由: アシスタントが質問文の後に補足・締めの文を書くケース
    （例:「…どうしますか？ お手数ですがご確認ください。」）で末尾に `?` が来ず、
    false positive でコミットを強行する挙動を避けるため。

    同一 `message.id` を持つ複数エントリ（テキストとツール呼び出しが分割される等）は
    1 ターンとして統合して判定する。transcript が未フラッシュで最新エントリが
    存在しない場合の対処も含む `_iter_latest_assistant_messages` に走査を委譲する。
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
    """直前のアシスタントターンが待機中を示す語を含んでいるかを確認する。

    `_WAITING_KEYWORDS` のいずれかを含むテキストブロックがあれば True を返す。
    バックグラウンド待機・非同期処理待ちの誤発動を防ぐためのゲート。

    マッチングは大文字小文字を無視する（`keyword.lower() in text.lower()`）。
    英語キーワード「background」が「Background」「BACKGROUND」などにも一致する。
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
    """直前アシスタントターンの最後の tool_use が非同期待機系かを判定する。

    `_ASYNC_WAIT_TOOLS` に含まれるツール名、または `Bash` かつ
    `input.run_in_background == true` の場合に True を返す。
    バックグラウンド処理中の Stop hook 誤発動を防ぐためのゲート。
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
        # 最初に得た（最新の）メッセージの tool_use のみ対象。
        # ターン内で最後に出現した tool_use を使うため、
        # メッセージを跨いで探さない。
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


def _iter_latest_assistant_messages(transcript_path: str) -> collections.abc.Iterator[dict]:
    """直前のアシスタントターンに属する message dict を新しい順に生成する。

    以下の制約で 1 ターンを画定する。
    - sidechain（subagent）のエントリは除外する
    - 同一 `message.id` を持つ複数エントリ（テキストとツール呼び出しが分割される等）は
      1 ターンとして統合する
    - アシスタント以外のエントリ・異なる `message.id` のアシスタントエントリが
      間に挟まった時点でターン境界とみなし走査を終える
    - 最大 3 エントリまでさかのぼる（それ以降は走査しない）

    transcript 読み取りに失敗した場合は空のイテレーターを返す（安全側）。
    """
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return
    first_msg_id: str | None = None
    checked_count = 0
    saw_non_assistant = False  # 最初のアシスタントエントリ発見後に非アシスタントエントリが出たか
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            if first_msg_id is not None:
                # 別エントリが間に挟まった → 同一ターンの探索終了
                saw_non_assistant = True
            continue
        if saw_non_assistant:
            # 直前のアシスタントエントリとの間に別エントリが挟まっている → 別ターン
            return
        message = entry.get("message")
        if not isinstance(message, dict):
            return
        msg_id = message.get("id", "")
        if first_msg_id is None:
            first_msg_id = msg_id
        elif msg_id and first_msg_id and msg_id != first_msg_id:
            # message.id が両方設定されており異なる → 別ターン
            return
        checked_count += 1
        if checked_count > 3:
            return
        yield message
