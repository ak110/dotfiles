"""Claude Code・Antigravityの実行中の記録の読み取りと、アシスタント直前ターンの抽出。

hookの処理が記録のファイルを開くのは`read_transcript_bytes`だけとし、行・エントリ・バイト位置の各形は
その結果から組み立てる。読み取りの失敗の扱い（`None`か空）をこの1か所で保つためである。
"""

import collections.abc
import json
import os
import pathlib
import typing

from agent_toolkit._common.agy_records import normalize_entries

_MAX_ENTRIES = 3
SEND_TO_USER_TOOL_SUFFIX = "__send_to_user"
"""agent-toolkitのFunction hooks moduleが登録する`send_to_user`ツールの名前の末尾（`mcp__<plugin>__send_to_user`）。"""


def iter_latest_assistant_messages(transcript_path: str) -> collections.abc.Iterator[dict]:
    """直前のアシスタントターンに属するmessage dictを新しい順に生成する。

    以下の制約で1ターンを画定する。
    - sidechain（subagent）のエントリは除外する
    - 同一`message.id`を持つ複数エントリ（テキストとツール呼び出しが分割される等）は
      1ターンとして統合する
    - アシスタント以外のエントリ・異なる`message.id`のアシスタントエントリが
      間に介在した時点でターン境界とみなして走査を終える
    - ハーネスが生成した`isApiErrorMessage`エントリは直前ターンの終端境界とし、
      APIエラー以前のアシスタント本文へは遡らない
    - 最大3エントリまでさかのぼる

    transcript読み取りに失敗した場合（空文字列パス・存在しないパス・OSエラーを含む）は
    空のイテレーターを返す。
    """
    lines = read_transcript_lines(transcript_path)
    if lines is None:
        return
    first_msg_id: str | None = None
    checked_count = 0
    saw_non_assistant = False  # 最初のアシスタントエントリ発見後に非アシスタントエントリが出た場合に真
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError, ValueError:
            continue
        if entry.get("isApiErrorMessage"):
            return
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


def iter_latest_assistant_text_messages(transcript_path: str) -> collections.abc.Iterator[dict]:
    """テキストブロックを持つ直近のアシスタントターンを新しい順に生成する。

    テキストを持たないターンを除いて遡り、最初に見つけたmessage IDと同じエントリを
    最大3件返す。APIエラーは、それ以前の本文を判定対象にしない終端境界とする。
    """
    lines = read_transcript_lines(transcript_path)
    if lines is None:
        return
    target_msg_id: str | None = None
    yielded_count = 0
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError, ValueError:
            continue
        if entry.get("isApiErrorMessage"):
            return
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        msg_id = message.get("id", "")
        if target_msg_id is None:
            if not visible_assistant_text(message):
                continue
            target_msg_id = msg_id if isinstance(msg_id, str) else ""
        elif target_msg_id and msg_id and msg_id != target_msg_id:
            return
        if msg_id != target_msg_id:
            continue
        yielded_count += 1
        if yielded_count > _MAX_ENTRIES:
            return
        yield message


def iter_assistant_content_blocks(lines: list[str]) -> collections.abc.Iterator[tuple[int, dict]]:
    """非sidechain assistantエントリの`content`配下dict要素を`(行位置, block)`で順に返す。

    全行を前方から走査する。`iter_latest_assistant_messages`は末尾から直前ターンだけを走査する。
    """
    for position, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError, ValueError:
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
            if isinstance(block, dict):
                yield position, block


def read_transcript_bytes(transcript_path: str, *, offset: int = 0) -> bytes | None:
    """記録を`offset`のバイト位置から末尾まで読む。

    読み取りに失敗した場合（空文字列パス・存在しないパス・OSエラーを含む）と、記録が`offset`より短い場合は
    `None`を返す。後者は記録の置き換えなどで以前に観測した位置が失われたことを示す。
    """
    try:
        with pathlib.Path(transcript_path).open("rb") as stream:
            if offset:
                stream.seek(0, os.SEEK_END)
                if stream.tell() < offset:
                    return None
                stream.seek(offset)
            return stream.read()
    except OSError:
        return None


def read_transcript_lines(transcript_path: str) -> list[str] | None:
    """記録を行リストとして読み込み、読み取りかUTF-8の解釈に失敗した場合はNoneを返す。"""
    data = read_transcript_bytes(transcript_path)
    if data is None:
        return None
    try:
        lines = data.decode("utf-8").splitlines()
        entries: list[dict] = []
        positions: list[int] = []
        for position, line in enumerate(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                positions.append(position)
                entries.append(entry)
        for position, entry, normalized in zip(positions, entries, normalize_entries(entries, hook_view=True), strict=True):
            if normalized is not entry:
                lines[position] = json.dumps(normalized, ensure_ascii=False)
        return lines
    except UnicodeDecodeError:
        return None


def read_transcript_entries(transcript_path: str) -> list[dict]:
    """記録を読み込み、JSONオブジェクトの行を時系列で返す。読み取れない場合と解釈できない行は除く。"""
    entries: list[dict] = []
    for line in read_transcript_lines(transcript_path) or []:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError, ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def visible_text_blocks(content: typing.Any) -> list[str]:
    """Assistant messageの`content`から、ユーザーへ届いた本文を出現順に返す。

    `text`ブロックに加えて、`send_to_user`の呼び出しの`message`を、その呼び出しの位置の本文として扱う。
    ツール呼び出しより前の地の文はAPIが要約へ置き換えることがあり、原文どおり届ける内容はこのツールの入力で運ぶためである。
    名前の前置部分はmoduleの登録が決めるため、末尾の一致で判定する。
    """
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif block.get("type") == "tool_use" and str(block.get("name", "")).endswith(SEND_TO_USER_TOOL_SUFFIX):
            tool_input = block.get("input")
            message = tool_input.get("message") if isinstance(tool_input, dict) else None
            if isinstance(message, str):
                texts.append(message)
    return texts


def visible_assistant_text(message: typing.Any) -> str:
    """Assistant message dictのうちユーザーへ届いた本文（`text`と`send_to_user`の`message`）を連結して返す。"""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "".join(visible_text_blocks(content))
