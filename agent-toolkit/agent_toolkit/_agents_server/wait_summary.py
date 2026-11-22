"""エージェント環境の`atk agents wait`が標準出力へ直接書く、通知・終了の内訳と本文の要約。

終了行ごとに、本文を運ぶ`agent_message`と件数を別の行で示す`notices`を除く全項目を1行ずつ示す。
先頭は`session_id`、`label`、`status`、`agent_message_path`の順とし、`error`と`next_action`を含む残りの項目を続ける。
呼び出し元は保存物を開かずに、どの依頼が終了したか、結果本文のファイルの所在、失敗の原因と次の操作を得られる。
終了行へ項目が加わった場合も、本モジュールを変えずに要約へ現れる。
"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from agent_toolkit._agents_server import resume_waits, state, turn_state

TERMINAL_LINE_PREFIX = "終了行: "
WAIT_BODY_START_PREFIX = "本文開始: session_id="
"""要約が委譲先の本文の前に置く行の先頭。この行から同じsession識別子の終了の行までは委譲先の本文である。"""
WAIT_BODY_END_PREFIX = "本文終了: session_id="
"""要約が委譲先の本文の後に置く行の先頭。"""
BODY_BUDGET_BYTES = 16 * 1024
"""要約の標準出力全体の上限（UTF-8バイト数）。超える本文は表示せず、`agent_message_path`から読ませる。

`atk`の共通の自動保存の境界と同じ値にし、要約自体が保存へ回らないようにする。
"""


_SUMMARY_LEADING_KEYS = ("session_id", "label", "status", "agent_message_path")
_SUMMARY_EXCLUDED_KEYS = frozenset({"agent_message", "notices"})
_TERMINAL_STATUS_RE = re.compile(r"\sstatus=([a-z_]+)(?:\s|$)")


def render(rows: Iterable[Mapping[str, Any]]) -> str:
    """待機結果の行から、通知・終了の内訳と上限内の本文を持つ要約を返す。

    待機表明を確定した結果は、`unfinished_waits`へ残った待機対象の件数を加える。対象が残っていなければ0とする。
    結果本文はその時点の待機表明であり再開したturnの結果ではないことを、委譲元が終了行だけから判別できるようにする。
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
            if unfinished_waits is not None:
                fields.append(f"unfinished_waits={unfinished_waits}")
            terminal_lines.append((f"{TERMINAL_LINE_PREFIX}{' '.join(fields)}", _terminal_body(result)))
    notice_line = f"通知: {notice_count}件（session_id: {', '.join(sorted(notice_session_ids))}）\n" if notice_count else ""
    terminal_header = f"終了: {len(terminal_lines)}件\n" if terminal_lines else ""
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
    if isinstance(value, str) and value and not value.startswith('"') and not any(char.isspace() for char in value):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _terminal_body(result: Mapping[str, Any]) -> tuple[str, str] | None:
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


def _unfinished_wait_count(error: object) -> int | None:
    if not isinstance(error, dict) or error.get(turn_state.HELD_RESULT_FINALIZED_KEY) is not True:
        return None
    return sum(
        len(identifiers)
        for key in (resume_waits.UNFINISHED_BACKGROUND_TASKS_KEY, resume_waits.UNOBSERVED_SESSIONS_KEY)
        if isinstance(identifiers := error.get(key), list)
    )
