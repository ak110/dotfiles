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
from agent_toolkit._common.diagnostic_redaction import redact_secrets

_LOG_TAIL_BYTES = 8192
_HEADER_LINES = 20
_EVENT_LIMIT = 20
_EVENT_CHARS = 2000
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
    """実行中の配布物に記録された版を返す。"""
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
    """実行ログ全体から、会話本文を除いた診断を有限の量で抜き出す。

    末尾だけを切り出すと、会話出力が末尾を占めた回に診断イベントと実行条件が失われるため、全行を走査して種類ごとに上限を設ける。
    """
    if not raw:
        return {"status": "実行ログなし（モデル未起動、または所在未記録）"}
    header: list[str] = []
    events: collections.deque[str] = collections.deque(maxlen=_EVENT_LIMIT)
    event_count = 0
    tail: collections.deque[str] = collections.deque()
    tail_bytes = 0
    tail_truncated = False
    omitted = 0
    child_output = False
    try:
        path = pathlib.Path(raw)
        if not path.is_file():
            return {"status": "読取不能", "error": "実行ログが通常ファイルでない、または存在しない"}
        size = path.stat().st_size
        with path.open(encoding="utf-8", errors="replace") as stream:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n")
                child_output = child_output or line.startswith(("[stdout] ", "[stderr] "))
                if line.startswith("[stdout] "):
                    payload = line.removeprefix("[stdout] ")
                    # 構造化イベントには会話本文やツール入力が含まれるため、診断のイベントだけを転載する。
                    if diagnostic_process.is_error_event(payload):
                        event_count += 1
                        events.append(redact_secrets(payload[:_EVENT_CHARS]))
                    else:
                        omitted += 1
                    continue
                if not child_output and len(header) < _HEADER_LINES:
                    # 子の出力より前の行は、run-skillが書く実行条件（対象、引数、候補）である。
                    header.append(redact_secrets(line))
                    continue
                tail.append(redact_secrets(line))
                tail_bytes += len(tail[-1].encode()) + 1
                while tail_bytes > _LOG_TAIL_BYTES and len(tail) > 1:
                    tail_bytes -= len(tail.popleft().encode()) + 1
                    tail_truncated = True
    except OSError as error:
        return {"status": "読取不能", "error": str(error)}
    return {
        "status": "取得済み",
        "total_bytes": size,
        "header": header,
        "error_events": list(events),
        "error_events_dropped": event_count - len(events),
        "conversation_lines_omitted": omitted,
        "tail_limit_bytes": _LOG_TAIL_BYTES,
        "tail_truncated": tail_truncated,
        "tail": "\n".join(tail),
    }


def _previous_scope(target: dict[str, Any]) -> dict[str, Any] | None:
    """準備が`--after`で読む前回の処理範囲を返す。行数とdigestだけを持ち、会話本文を含まない。"""
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
    # JSONの文字列内に改行やコード囲みがあっても、本文の構造として解釈されないようインデントする。
    return (
        "### 失敗時の入力と診断\n\n"
        + "\n".join("    " + line for line in json.dumps(details, ensure_ascii=False, indent=2).splitlines())
        + "\n"
    )
