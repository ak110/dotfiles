"""エージェント環境の`atk agents wait`が標準出力へ直接書く、通知・終端の内訳と本文の要約。

終端行ごとに、本文を運ぶ`agent_message`と件数を別の行で示す`notices`を除く全項目を1行ずつ示す。
先頭は`session_id`、`label`、`status`、`agent_message_path`の順とし、`error`と`next_action`を含む残りの項目を続ける。
呼び出し元は保存物を開かずに、どの依頼が終端したか、結果本文のファイルの所在、失敗の原因と次の操作を得られる。
終端行へ項目が加わった場合も、本モジュールを変えずに要約へ現れる。
"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from agent_toolkit._agents_server import resume_waits, state

TERMINAL_LINE_PREFIX = "終端行: "
WAIT_BODY_START_PREFIX = "本文開始: session_id="
"""要約が委譲先の本文の前に置く行の先頭。この行から同じsession識別子の終了の行までは委譲先の本文である。"""
WAIT_BODY_END_PREFIX = "本文終了: session_id="
"""要約が委譲先の本文の後に置く行の先頭。"""
BODY_BUDGET_BYTES = 16 * 1024
"""要約の標準出力全体の上限（UTF-8バイト数）。超える本文は表示せず、`agent_message_path`から読ませる。

`atk`の共通の自動保存の境界と同じ値にし、要約自体が保存へ回らないようにする。
"""

# 要約行の先頭に固定の順序で置く項目と、要約行から除く項目。
# `agent_message`は`agent_message_path`の本文として、`notices`は`通知:`の行として別に示す。
_SUMMARY_LEADING_KEYS = ("session_id", "label", "status", "agent_message_path")
_SUMMARY_EXCLUDED_KEYS = frozenset({"agent_message", "notices"})
_TERMINAL_STATUS_RE = re.compile(r"\sstatus=([a-z_]+)(?:\s|$)")


def render(rows: Iterable[Mapping[str, Any]]) -> str:
    """待機結果の行から、通知・終端の内訳と上限内の本文を持つ要約を返す。

    保留した結果を待機対象が残ったまま確定した終端結果は、`unfinished_waits`へ残った待機対象の件数を加える。
    結果本文はその時点の待機表明であり再開したturnの結果ではないことを、委譲元が終端行だけから判別できるようにする。
    本文は`本文開始:`と`本文終了:`の行で囲み、委譲元が待機の出力を1回読むだけで本文まで受け取れるようにする。
    要約全体が上限を超える本文と、読めない本文ファイルは表示せず、`agent_message_path`から読ませる。
    """
    notice_count = 0
    notice_session_ids: set[str] = set()
    notice_bodies: list[tuple[str, str]] = []
    terminal_lines: list[tuple[str, tuple[str, str] | None]] = []
    for result in rows:
        notices = result.get("notices")
        if isinstance(notices, list) and notices:
            notice_count += len(notices)
            session_id = result.get("session_id")
            if isinstance(session_id, str):
                notice_session_ids.add(session_id)
                notice_bodies.extend(
                    (session_id, notice["body"])
                    for notice in notices
                    if isinstance(notice, dict) and isinstance(notice.get("body"), str)
                )
        if result.get("status") in state.TERMINAL_STATUSES:
            fields = [
                f"session_id={result.get('session_id')}",
                f"label={result.get('label') or 'なし'}",
                f"status={result.get('status')}",
                f"agent_message_path={result.get('agent_message_path') or 'なし'}",
                *(
                    f"{key}={_summary_value(value)}"
                    for key, value in result.items()
                    if key not in _SUMMARY_LEADING_KEYS and key not in _SUMMARY_EXCLUDED_KEYS
                ),
            ]
            unfinished_waits = _unfinished_wait_count(result.get("error"))
            if unfinished_waits:
                fields.append(f"unfinished_waits={unfinished_waits}")
            terminal_lines.append((f"{TERMINAL_LINE_PREFIX}{' '.join(fields)}", _terminal_body(result)))
    notice_line = f"通知: {notice_count}件（session_id: {', '.join(sorted(notice_session_ids))}）\n" if notice_count else ""
    terminal_header = f"終端: {len(terminal_lines)}件\n" if terminal_lines else ""
    remaining = (
        BODY_BUDGET_BYTES
        - _utf8_size(notice_line + terminal_header)
        - sum(_utf8_size(f"{line}\n") for line, _body in terminal_lines)
    )
    output = [notice_line]
    for session_id, body in notice_bodies:
        block, remaining = _body_within(session_id, body, remaining)
        output.append(block)
    output.append(terminal_header)
    for line, body_entry in terminal_lines:
        output.append(f"{line}\n")
        if body_entry is not None:
            block, remaining = _body_within(*body_entry, remaining)
            output.append(block)
    return "".join(output)


def terminal_session_ids(text: str) -> set[str]:
    """要約の`終端行:`から、終端を回収したsession識別子を返す。"""
    collected: set[str] = set()
    for line in text.splitlines():
        if not line.startswith(f"{TERMINAL_LINE_PREFIX}session_id="):
            continue
        session_id = line.removeprefix(f"{TERMINAL_LINE_PREFIX}session_id=").split(" ", 1)[0]
        status = _TERMINAL_STATUS_RE.search(line)
        if session_id and status is not None and status.group(1) in state.TERMINAL_STATUSES:
            collected.add(session_id)
    return collected


def _summary_value(value: object) -> str:
    """要約行の項目の値を、空白で区切った他の項目と区別できる1行の表記へ変換する。

    空白を含まない空でない文字列はそのまま示し、それ以外（空白や改行を含む文字列、辞書、数値など）は
    改行を含まないJSONで示す。
    """
    if isinstance(value, str) and value and not value.startswith('"') and not any(char.isspace() for char in value):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _terminal_body(result: Mapping[str, Any]) -> tuple[str, str] | None:
    """終端行の直後に表示する本文を`agent_message_path`のファイルから読み、session識別子と組にして返す。

    本文ファイルが無いか読めない場合は`None`を返し、終端行は`agent_message_path`だけを示す。
    """
    session_id = result.get("session_id")
    body_path = result.get("agent_message_path")
    if not isinstance(session_id, str) or not isinstance(body_path, str):
        return None
    try:
        with pathlib.Path(body_path).open(encoding="utf-8", newline="") as stream:
            return session_id, stream.read()
    except OSError, UnicodeError:
        return None


def _body_within(session_id: str, body: str, remaining: int) -> tuple[str, int]:
    """本文を区切りの行で囲んで表示できる場合だけ本文の塊を返し、残りのバイト数とともに返す。"""
    block = f"{WAIT_BODY_START_PREFIX}{session_id}\n{body}"
    if body and not body.endswith("\n"):
        block += "\n"
    block += f"{WAIT_BODY_END_PREFIX}{session_id}\n"
    size = _utf8_size(block)
    if size > remaining:
        return "", remaining
    return block, remaining - size


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _unfinished_wait_count(error: object) -> int:
    """保留した結果の確定時に残ったバックグラウンドタスクと子sessionの件数を返す。

    `unobservedSessions`は再開したturnの終端など保留と無関係な処理でも記録されるため、
    保留の確定が付ける`heldResultFinalized`を持つ結果だけを数える。
    """
    if not isinstance(error, dict) or error.get(resume_waits.HELD_RESULT_FINALIZED_KEY) is not True:
        return 0
    return sum(
        len(identifiers)
        for key in (resume_waits.UNFINISHED_BACKGROUND_TASKS_KEY, resume_waits.UNOBSERVED_SESSIONS_KEY)
        if isinstance(identifiers := error.get(key), list)
    )
