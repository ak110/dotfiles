"""Claude Code agent-toolkit: transcript JSONLからアシスタント直前ターンを抽出する共通ヘルパー。"""

import collections.abc
import json
import pathlib
import typing

_MAX_ENTRIES = 3


def iter_latest_assistant_messages(transcript_path: str) -> collections.abc.Iterator[dict]:
    """直前のアシスタントターンに属するmessage dictを新しい順に生成する。

    以下の制約で1ターンを画定する。
    - sidechain（subagent）のエントリは除外する
    - 同一`message.id`を持つ複数エントリ（テキストとツール呼び出しが分割される等）は
      1ターンとして統合する
    - アシスタント以外のエントリ・異なる`message.id`のアシスタントエントリが
      間に介在した時点でターン境界とみなして走査を終える
    - 最大3エントリまでさかのぼる

    transcript読み取りに失敗した場合（空文字列パス・存在しないパス・OSエラーを含む）は
    空のイテレーターを返す。
    """
    lines = _read_transcript_lines(transcript_path)
    if lines is None:
        return
    first_msg_id: str | None = None
    checked_count = 0
    saw_non_assistant = False  # 最初のアシスタントエントリ発見後に非アシスタントエントリが出た場合に真
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            if first_msg_id is not None:
                # 別エントリが間に介在 → 同一ターンの探索終了。
                saw_non_assistant = True
            continue
        if saw_non_assistant:
            # 直前のアシスタントエントリとの間に別エントリが介在 → 別ターン。
            return
        message = entry.get("message")
        if not isinstance(message, dict):
            return
        msg_id = message.get("id", "")
        if first_msg_id is None:
            first_msg_id = msg_id
        elif msg_id and first_msg_id and msg_id != first_msg_id:
            # message.idが両方設定されており異なる → 別ターン。
            return
        checked_count += 1
        if checked_count > _MAX_ENTRIES:
            return
        yield message


def latest_main_assistant_entry(transcript_path: str) -> dict | None:
    """末尾（最新側）で最初に見つかる非sidechainのassistantエントリ全体を返す。

    `iter_latest_assistant_messages`がmessage dictのみを返すのに対し、本関数は
    `isApiErrorMessage`などentryトップレベルのフラグを参照できるようentry全体を返す。
    APIエラー停止のassistantエントリ直後にsystemエントリ（turn_duration）が続く配置でも、
    後方から走査して直近のassistantエントリを拾う。

    見つからない場合・読み取り失敗時（空文字列パス・存在しないパス・OSエラーを含む）はNoneを返す。
    """
    lines = _read_transcript_lines(transcript_path)
    if lines is None:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") == "assistant" and not entry.get("isSidechain"):
            return entry
    return None


def assistant_text(message: typing.Any) -> str:
    """Assistant message dictのテキストブロックを連結して返す。

    `content`が文字列ならそのまま返し、ブロックのリストなら`type == "text"`のテキストを連結する。
    テキストを取得できない場合は空文字列を返す。

    引数はtranscript JSON由来の任意値を扱うため`Any`型とする
    （`object`をisinstanceでdictへ限定すると型検査器tyが型引数を`Never`と誤推論するため）。
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    return ""


def _read_transcript_lines(transcript_path: str) -> list[str] | None:
    """Transcript JSONLを行リストとして読み込む。

    読み取りに失敗した場合（空文字列パス・存在しないパス・OSエラーを含む）はNoneを返す。
    """
    try:
        return pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
