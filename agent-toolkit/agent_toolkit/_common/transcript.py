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
from agent_toolkit._common.json_lines import split_joined_line, split_lines

_MAX_ENTRIES = 3
SEND_TO_USER_TOOL_SUFFIX = "__send_to_user"
"""agent-toolkitのMCPが登録する`send_to_user`ツールの名前の末尾。ホストごとの前置名は判定に使わない。"""


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
    saw_non_assistant = False
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError, ValueError:
            continue
        if entry.get("isApiErrorMessage"):
            return
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            if first_msg_id is not None:
                saw_non_assistant = True
            continue
        if saw_non_assistant:
            return
        message = entry.get("message")
        if not isinstance(message, dict):
            return
        msg_id = message.get("id", "")
        if first_msg_id is None:
            first_msg_id = msg_id
        elif msg_id and first_msg_id and msg_id != first_msg_id:
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
    return latest_assistant_text_messages(read_transcript_entries(transcript_path))


def latest_assistant_text_messages(entries: collections.abc.Sequence[dict]) -> collections.abc.Iterator[dict]:
    target_msg_id: str | None = None
    yielded_count = 0
    for entry in reversed(entries):
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


class TranscriptSnapshot(typing.NamedTuple):
    lines: list[str]
    entries_by_line: dict[int, dict]


def read_transcript_snapshot(transcript_path: str) -> TranscriptSnapshot | None:
    """記録を物理行の位置を保った行リストとして読み込み、読み取りに失敗した場合はNoneを返す。

    途切れた断片と完全な記録が改行なしで連結された行は、断片を除いた後半の記録を同じ位置に置く。
    断片がUTF-8の多バイト文字の途中で途切れていても他の行を読めるよう、UTF-8の解釈は行ごとに行う。
    解釈できない行は、置換文字で偶然JSONとして成立しないよう空文字列とし、利用側の解析対象から外す。
    """
    data = read_transcript_bytes(transcript_path)
    if data is None:
        return None
    lines: list[str] = []
    entries: list[dict] = []
    positions: list[int] = []
    for position, raw in enumerate(split_lines(data)):
        try:
            line = raw.decode("utf-8")
            entry = json.loads(line)
        except ValueError:
            joined = split_joined_line(raw)
            if joined is None:
                try:
                    lines.append(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    lines.append("")
                continue
            start, entry = joined
            line = raw[start:].decode("utf-8")
        lines.append(line)
        if isinstance(entry, dict):
            positions.append(position)
            entries.append(entry)
    normalized_entries = normalize_entries(entries, hook_view=True)
    for position, entry, normalized in zip(positions, entries, normalized_entries, strict=True):
        if normalized is not entry:
            lines[position] = json.dumps(normalized, ensure_ascii=False)
    return TranscriptSnapshot(lines, dict(zip(positions, normalized_entries, strict=True)))


def read_transcript_lines(transcript_path: str) -> list[str] | None:
    snapshot = read_transcript_snapshot(transcript_path)
    return None if snapshot is None else snapshot.lines


def read_transcript_entries(transcript_path: str) -> list[dict]:
    snapshot = read_transcript_snapshot(transcript_path)
    return [] if snapshot is None else list(snapshot.entries_by_line.values())


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
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "".join(visible_text_blocks(content))
