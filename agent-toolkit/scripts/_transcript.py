"""Claude Code agent-toolkit: transcript JSONLからアシスタント直前ターンを抽出する共通ヘルパー。"""

import collections.abc
import json
import pathlib

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
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
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
