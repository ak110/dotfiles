"""振り返り失敗の入力メタ情報と、実行ログから抜き出した有限の診断を、移送できる本文へ変換する。"""

from __future__ import annotations

import collections
import datetime
import json
import pathlib
import platform
import stat
import sys
from typing import Any

from agent_toolkit._common import diagnostic_process, session_review
from agent_toolkit._common.diagnostic_redaction import ByteDiagnosticLine, newest_diagnostic_lines, redact_secrets

_EXCERPT_BYTES = 8192
"""実行条件・診断イベント・末尾を合わせた抜粋の上限。末尾だけを抜き出していた以前の上限と同じ量に保つ。"""
_HEADER_BYTES = 2048
_EVENT_BYTES = 4096
_LINE_BYTES = 2000
"""1行の上限。実行条件と診断イベントの各上限より小さくし、どの区分にも少なくとも1行が入るようにする。"""
_HEADER_LINES = 20
_EVENT_LIMIT = 20
_TARGET_FIELDS = (
    "host",
    "session_id",
    "transcript_path",
    "cwd",
    "target_repo",
    "started_at",
    "updated_at",
    "generation",
    "review_batch",
)


def plugin_version(root: pathlib.Path) -> str:
    return json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]


def _record_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {"kind": "所在未記録"}
    try:
        path = pathlib.Path(raw)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            try:
                info = path.stat()
            except FileNotFoundError:
                return {"kind": "その他", "detail": "参照先の無いシンボリックリンク"}
    except FileNotFoundError:
        return {"kind": "存在しない"}
    except OSError as error:
        return {"kind": "照会不能", "error": str(error)}
    if stat.S_ISREG(info.st_mode):
        return {
            "kind": "ファイル",
            "size_bytes": info.st_size,
            "modified_at": datetime.datetime.fromtimestamp(info.st_mtime, datetime.UTC).isoformat(),
        }
    return {"kind": "ディレクトリ" if stat.S_ISDIR(info.st_mode) else "その他"}


def _log_diagnostics(raw: Any) -> dict[str, Any]:
    if not raw:
        return {"status": "実行ログなし（モデル未起動、または所在未記録）"}
    header: list[str] = []
    header_bytes = 0
    header_open = True
    events: collections.deque[ByteDiagnosticLine] = collections.deque(maxlen=_EVENT_LIMIT)
    event_count = 0
    tail: collections.deque[ByteDiagnosticLine] = collections.deque()
    tail_bytes = 0
    tail_truncated = False
    omitted = 0
    try:
        path = pathlib.Path(raw)
        if not path.is_file():
            return {"status": "読取不能", "error": "実行ログが通常ファイルでない、または存在しない"}
        size = path.stat().st_size
        with path.open(encoding="utf-8", errors="replace") as stream:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n")
                if line.startswith(("[stdout] ", "[stderr] ")):
                    header_open = False
                if line.startswith("[stdout] "):
                    payload = line.removeprefix("[stdout] ")

                    if diagnostic_process.is_error_event(payload):
                        event_count += 1
                        events.append(ByteDiagnosticLine(payload, line_bytes=_LINE_BYTES))
                    else:
                        omitted += 1
                    continue
                clipped = ByteDiagnosticLine(line, line_bytes=_LINE_BYTES)

                header_open = header_open and len(header) < _HEADER_LINES and header_bytes + clipped.size <= _HEADER_BYTES
                if header_open:
                    header.append(clipped.text)
                    header_bytes += clipped.size
                    continue
                tail.append(clipped)
                tail_bytes += clipped.size
                while tail_bytes > _EXCERPT_BYTES:
                    tail_bytes -= tail.popleft().size
                    tail_truncated = True
    except OSError as error:
        return {"status": "読取不能", "error": str(error)}
    kept_events = newest_diagnostic_lines(list(events), total_bytes=_EVENT_BYTES)
    event_bytes = sum(len(event.encode()) + 1 for event in kept_events)
    kept_tail = newest_diagnostic_lines(list(tail), total_bytes=_EXCERPT_BYTES - header_bytes - event_bytes)
    return {
        "status": "取得済み",
        "total_bytes": size,
        "header": header,
        "error_events": kept_events,
        "error_events_dropped": event_count - len(kept_events),
        "conversation_lines_omitted": omitted,
        "excerpt_limit_bytes": _EXCERPT_BYTES,
        "line_limit_bytes": _LINE_BYTES,
        "tail_truncated": tail_truncated or len(kept_tail) < len(tail),
        "tail": "\n".join(kept_tail),
    }


def _previous_scope(target: dict[str, Any]) -> dict[str, Any] | None:
    if not target.get("host") or not target.get("session_id"):
        return None
    path = session_review.previous_scope(target)
    try:
        return session_review.read(path) if path is not None else None
    except (OSError, ValueError) as error:
        return {"読取不能": str(error)}


def failure_details(manifest: dict[str, Any], root: pathlib.Path) -> str:
    """会話ファイルを読まず、失敗時の入力・診断をJSONとして本文に固定する。"""
    targets = [
        {
            **{field: target.get(field) for field in _TARGET_FIELDS},
            "record_state": _record_state(target.get("transcript_path")),
            "previous_scope": _previous_scope(target),
        }
        for target in manifest["targets"]
    ]
    details = {
        "hostname": platform.node(),
        "os": sys.platform,
        "plugin_version": manifest.get("failure_version") or plugin_version(root),
        "targets": targets,
        "error": redact_secrets(manifest["error"]) if manifest.get("error") else manifest.get("error"),
        "prepared_count": len(manifest.get("prepared", [])),
        "prepared_session_ids": [item.get("session_id") for item in manifest.get("prepared", [])],
        "exit_code": manifest.get("exit_code"),
        "log": _log_diagnostics(manifest.get("log_path")),
    }

    return (
        "### 失敗時の入力と診断\n\n"
        + "\n".join("    " + line for line in json.dumps(details, ensure_ascii=False, indent=2).splitlines())
        + "\n"
    )
