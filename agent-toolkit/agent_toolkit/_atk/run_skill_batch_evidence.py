"""振り返り失敗の入力メタ情報と有限のログ抜粋を、移送できる本文へ変換する。"""

from __future__ import annotations

import datetime
import json
import pathlib
import platform
import stat
import sys
from typing import Any

_LOG_TAIL_BYTES = 8192
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


def _log_tail(raw: Any) -> dict[str, Any]:
    if not raw:
        return {"status": "実行ログなし（モデル未起動、または所在未記録）"}
    try:
        path = pathlib.Path(raw)
        if not path.is_file():
            return {"status": "読取不能", "error": "実行ログが通常ファイルでない、または存在しない"}
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - _LOG_TAIL_BYTES))
            tail = stream.read(_LOG_TAIL_BYTES)
    except OSError as error:
        return {"status": "読取不能", "error": str(error)}
    # stdoutの構造化イベントには会話本文やツール入力が含まれるため、診断以外のJSONは転載しない。
    lines = tail.decode("utf-8", errors="replace").splitlines()
    if size > _LOG_TAIL_BYTES:
        lines = lines[1:]
    excerpt = []
    omitted = False
    for line in lines:
        payload = line.removeprefix("[stdout] ")
        if line.startswith("[stdout] "):
            try:
                event = json.loads(payload)
            except ValueError:
                event = None
            if not isinstance(event, dict) or event.get("type") != "error":
                excerpt.append("[stdout] （会話本文を含みうる出力を省略）")
                omitted = True
                continue
        excerpt.append(line)
    return {
        "status": "取得済み",
        "limit_bytes": _LOG_TAIL_BYTES,
        "total_bytes": size,
        "truncated": size > _LOG_TAIL_BYTES,
        "conversation_output_omitted": omitted,
        "stdout_stderr_tail": "\n".join(excerpt),
    }


def failure_details(manifest: dict[str, Any], root: pathlib.Path) -> str:
    """会話ファイルを読まず、失敗時の入力・診断をJSONとして本文に固定する。"""
    targets = [
        {**{field: target.get(field) for field in _TARGET_FIELDS}, "record_state": _record_state(target.get("transcript_path"))}
        for target in manifest["targets"]
    ]
    details = {
        "hostname": platform.node(),
        "os": sys.platform,
        "plugin_version": manifest.get("failure_version") or plugin_version(root),
        "targets": targets,
        "error": manifest.get("error"),
        "prepared_count": len(manifest.get("prepared", [])),
        "prepared_session_ids": [item.get("session_id") for item in manifest.get("prepared", [])],
        "exit_code": manifest.get("exit_code"),
        "log": _log_tail(manifest.get("log_path")),
    }
    # JSONの文字列内に改行やコード囲みがあっても、本文の構造として解釈されないようインデントする。
    return (
        "### 失敗時の入力と診断\n\n"
        + "\n".join("    " + line for line in json.dumps(details, ensure_ascii=False, indent=2).splitlines())
        + "\n"
    )
