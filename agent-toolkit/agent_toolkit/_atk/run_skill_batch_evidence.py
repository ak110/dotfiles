"""振り返り失敗の入力メタ情報と、実行ログから抜き出した有限の診断を、移送できる本文へ変換する。"""

from __future__ import annotations

import collections
import collections.abc
import datetime
import json
import pathlib
import platform
import stat
import sys
from typing import Any

from agent_toolkit._common import diagnostic_process, session_review
from agent_toolkit._common.diagnostic_redaction import redact_secrets

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


def _clip(line: str) -> str:
    """秘匿値を伏せてから1行を上限まで切り詰める。切り詰めた後に伏せると、切り詰めた位置をまたぐ資格情報が規則に一致せず、先頭が残る。"""
    return redact_secrets(line).encode()[:_LINE_BYTES].decode(errors="ignore")


def _size(line: str) -> int:
    return len(line.encode()) + 1


def _newest_within(lines: collections.abc.Iterable[str], budget: int) -> list[str]:
    kept: list[str] = []
    for line in reversed(list(lines)):
        if _size(line) > budget:
            break
        budget -= _size(line)
        kept.append(line)
    return kept[::-1]


def _log_diagnostics(raw: Any) -> dict[str, Any]:
    """実行ログ全体から、会話本文を除いた診断を有限の量で抜き出す。

    末尾だけを切り出すと、会話出力が末尾を占めた回に診断イベントと実行条件が失われるため、全行を走査して種類ごとに上限を設ける。
    末尾は、実行条件と診断イベントが使った残りの量に収める。
    """
    if not raw:
        return {"status": "実行ログなし（モデル未起動、または所在未記録）"}
    header: list[str] = []
    header_bytes = 0
    header_open = True
    events: collections.deque[str] = collections.deque(maxlen=_EVENT_LIMIT)
    event_count = 0
    tail: collections.deque[str] = collections.deque()
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
                    # 構造化イベントには会話本文やツール入力が含まれるため、診断のイベントだけを転載する。
                    if diagnostic_process.is_error_event(payload):
                        event_count += 1
                        events.append(_clip(payload))
                    else:
                        omitted += 1
                    continue
                clipped = _clip(line)
                # 子の出力より前の行は、run-skillが書く実行条件（対象、引数、候補）である。
                header_open = header_open and len(header) < _HEADER_LINES and header_bytes + _size(clipped) <= _HEADER_BYTES
                if header_open:
                    header.append(clipped)
                    header_bytes += _size(clipped)
                    continue
                tail.append(clipped)
                tail_bytes += _size(clipped)
                while tail_bytes > _EXCERPT_BYTES:
                    tail_bytes -= _size(tail.popleft())
                    tail_truncated = True
    except OSError as error:
        return {"status": "読取不能", "error": str(error)}
    kept_events = _newest_within(events, _EVENT_BYTES)
    kept_tail = _newest_within(tail, _EXCERPT_BYTES - header_bytes - sum(_size(event) for event in kept_events))
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
