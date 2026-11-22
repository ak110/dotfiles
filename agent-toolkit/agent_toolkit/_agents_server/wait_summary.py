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
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from agent_toolkit._agents_server import resume_waits, state, turn_state
from agent_toolkit._atk import output_file

TERMINAL_LINE_PREFIX = "終了行: "
WAIT_BODY_START_PREFIX = "本文開始: session_id="
"""要約が委譲先の本文の前に置く行の先頭。この行から同じsession識別子の終了の行までは委譲先の本文である。"""
WAIT_BODY_END_PREFIX = "本文終了: session_id="
"""要約が委譲先の本文の後に置く行の先頭。"""
NOTICE_FILE_PREFIX = "通知本文ファイル: "
NOTICE_RUN_PREFIX = "通知本文のrun記録: "
BODY_BUDGET_BYTES = output_file.AUTO_SAVE_THRESHOLD_BYTES


_SUMMARY_LEADING_KEYS = ("session_id", "label", "status", "agent_message_path")
_SUMMARY_EXCLUDED_KEYS = frozenset({"agent_message", "notices"})
_TERMINAL_STATUS_RE = re.compile(r"\sstatus=([a-z_]+)(?:\s|$)")


def render(rows: Iterable[Mapping[str, Any]], *, reserved_bytes: int = 0, notices_reference: str | None = None) -> str:
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
                notice_bodies.extend(_notice_bodies(session_id, notices))
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
    notice_file_line = f"{notices_reference}\n" if notice_bodies and notices_reference is not None else ""
    # 全文のファイルが無い通知の本文は、他に読む手段が無いため必須として扱う。  # pyfltr: allow-comment
    required_notices = "" if notice_file_line else "".join(_body_block(*entry) for entry in notice_bodies)
    remaining = (
        BODY_BUDGET_BYTES
        - reserved_bytes
        - _utf8_size(notice_line + notice_file_line + required_notices + terminal_header)
        - sum(_utf8_size(f"{line}\n") for line, _body in terminal_lines)
    )
    output = [notice_line, notice_file_line, required_notices]
    if notice_file_line:
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


def prepare_notices_reference(
    rows: Iterable[Mapping[str, Any]], write_file: Callable[[str], pathlib.Path], run_path: pathlib.Path | None
) -> tuple[str | None, str | None]:
    text = notice_text(rows)
    if not text:
        return None, None
    try:
        return f"{NOTICE_FILE_PREFIX}{write_file(text)}", None
    except OSError as error:
        if run_path is None:
            return None, (
                f"警告: 通知の本文をファイルへ書けない: {error}\n"
                "次の操作: 通知の本文は標準出力に全文を表示した。managed-tempの権限・空き容量を確認する"
            )
        return f"{NOTICE_RUN_PREFIX}{run_path}", (
            f"警告: 通知の本文をファイルへ書けない: {error}\n"
            "次の操作: 通知の本文は、通知本文のrun記録の行が示すJSONファイルの`output`（JSON Lines）の各行の"
            "`notices`から読む。managed-tempの権限・空き容量を確認する"
        )


def notice_text(rows: Iterable[Mapping[str, Any]]) -> str:
    return "".join(
        _body_block(sender, body)
        for row in rows
        if isinstance(session_id := row.get("session_id"), str) and isinstance(notices := row.get("notices"), list)
        for sender, body in _notice_bodies(session_id, notices)
    )


def _notice_bodies(session_id: str, notices: list[Any]) -> list[tuple[str, str]]:
    return [
        (session_id, notice["body"]) for notice in notices if isinstance(notice, dict) and isinstance(notice.get("body"), str)
    ]


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


def _body_block(session_id: str, body: str) -> str:
    block = f"{WAIT_BODY_START_PREFIX}{session_id}\n{body}"
    if body and not body.endswith("\n"):
        block += "\n"
    return block + f"{WAIT_BODY_END_PREFIX}{session_id}\n"


def _body_within(session_id: str, body: str, remaining: int) -> tuple[str, int]:
    block = _body_block(session_id, body)
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
