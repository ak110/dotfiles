"""振り返り失敗の入力メタ情報と、実行ログから抜き出した有限の診断を、移送できる本文へ変換する。"""

from __future__ import annotations

import collections
import datetime
import json
import pathlib
import re
import stat
import sys
from typing import Any

from agent_toolkit._atk import run_skill_progress, run_skill_state
from agent_toolkit._common import diagnostic_process, host_homes, session_record_paths, session_review
from agent_toolkit._common.diagnostic_redaction import (
    ByteDiagnosticLine,
    diagnostic_excerpt,
    newest_diagnostic_lines,
    redact_diagnostic_values,
    redact_secrets,
)
from agent_toolkit._git import command as git_command

_EXCERPT_BYTES = 8192
"""実行条件・診断イベント・末尾を合わせた抜粋の上限。末尾だけを抜き出していた以前の上限と同じ量に保つ。"""
_HEADER_BYTES = 2048
_EVENT_BYTES = 4096
_LINE_BYTES = 2000
"""1行の上限。実行条件と診断イベントの各上限より小さくし、どの区分にも少なくとも1行が入るようにする。"""
_HEADER_LINES = 20
_EVENT_LIMIT = 20
_TARGET_FIELDS = ("host", "session_id", "target_repo", "started_at", "updated_at", "generation", "review_batch")
_STOP_LOG_LINES = 20
_LOCAL_PATH_START = r"(?:/[^\s/'\\\"]|[A-Za-z]:[/\\]|\\\\)"
_QUOTED_LOCAL_PATH = re.compile(r"""(['"])(?=""" + _LOCAL_PATH_START + r""")([^\r\n]*?)\1""")
_LOCAL_PATH = re.compile(r"""(?<![\w:/\\])""" + _LOCAL_PATH_START + r"""[^\s'"<>,;|)\]}]*""")


def _portable_text(text: str) -> str:
    safe = redact_secrets(text)
    safe = _QUOTED_LOCAL_PATH.sub(r"\1<ローカルパス>\1", safe)
    return _LOCAL_PATH.sub("<ローカルパス>", safe)


def _portable_values(value: Any) -> Any:
    if isinstance(value, str):
        return _portable_text(value)
    if isinstance(value, dict):
        return {_portable_text(key): _portable_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_portable_values(item) for item in value]
    return value


def plugin_version(root: pathlib.Path) -> str:
    return json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]


def plugin_source(root: pathlib.Path, version: str) -> dict[str, Any]:
    """実行したagent-toolkitの配布元commitを、別環境で同じ版を取り出せる形で返す。  pyfltr: allow-comment

    Claude Codeの導入記録で導入先が一致する項目、Gitで管理された作業ツリー、導入記録でバージョンが一致する項目の順に探す。
    """
    resolved = root.resolve()
    entries: list[dict[str, Any]] = []
    try:
        installed = json.loads(
            (host_homes.claude_config_dir() / "plugins" / "installed_plugins.json").read_text(encoding="utf-8")
        )
        plugins = installed.get("plugins") if isinstance(installed, dict) else None
        if isinstance(plugins, dict):
            entries = [
                item
                for name, items in plugins.items()
                if isinstance(name, str) and name.startswith("agent-toolkit@") and isinstance(items, list)
                for item in items
                if isinstance(item, dict) and isinstance(item.get("gitCommitSha"), str)
            ]
    except OSError, ValueError:
        entries = []
    for item in entries:
        install = item.get("installPath")
        if isinstance(install, str) and pathlib.Path(install).resolve() == resolved:
            return {"version": version, "commit": item["gitCommitSha"], "source": "Claude Codeの導入記録（導入先が一致）"}
    if git_command.optional_stdout(["ls-files", "--error-unmatch", ".claude-plugin/plugin.json"], root) is not None:
        head = git_command.optional_stdout(["rev-parse", "HEAD"], root)
        if head:
            return {"version": version, "commit": head.strip(), "source": "Gitの作業ツリーのHEAD"}
    commits = sorted({item["gitCommitSha"] for item in entries if item.get("version") == version})
    if len(commits) == 1:
        return {"version": version, "commit": commits[0], "source": "Claude Codeの導入記録（バージョンが一致）"}
    return {"version": version, "commit": None, "source": f"特定できない（バージョンが一致する導入記録: {len(commits)}件）"}


def _stop_log(run_id: Any) -> dict[str, Any]:
    """会話本文を含まないStop hookの判定ログの末尾を返す。  pyfltr: allow-comment"""
    if not isinstance(run_id, str) or not run_id:
        return {"status": "実行識別子なし"}
    try:
        session_id = run_skill_state.load(run_id).session_id
    except (OSError, ValueError) as error:
        return {"status": "実行記録を読めない", "error": str(error)}
    if not session_id:
        return {"status": "セッション識別子なし"}
    path = session_record_paths.stop_log_path(session_id)
    lines: collections.deque[ByteDiagnosticLine] = collections.deque(maxlen=_STOP_LOG_LINES)
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for raw_line in stream:
                lines.append(ByteDiagnosticLine(_portable_text(raw_line.rstrip("\r\n")), line_bytes=_LINE_BYTES))
    except FileNotFoundError:
        return {"status": "ログなし", "session_id": session_id}
    except OSError as error:
        return {"status": "読取不能", "session_id": session_id, "error": str(error)}
    return {
        "status": "取得済み",
        "session_id": session_id,
        "tail": newest_diagnostic_lines(list(lines), total_bytes=_EVENT_BYTES),
    }


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


def _log_diagnostics(raw: Any, *, missing: str = "実行ログなし（モデル未起動、または所在未記録）") -> dict[str, Any]:
    if not raw:
        return {"status": missing}
    header: list[str] = []
    header_bytes = 0
    header_open = True
    events: collections.deque[ByteDiagnosticLine] = collections.deque(maxlen=_EVENT_LIMIT)
    event_count = 0
    tail: collections.deque[ByteDiagnosticLine] = collections.deque()
    tail_bytes = 0
    tail_truncated = False
    omitted = 0
    legacy_projection: collections.deque[str] = collections.deque()
    seen_tools: set[str] = set()
    try:
        path = pathlib.Path(raw)
        if not path.is_file():
            return {"status": "読取不能", "error": "実行ログが通常ファイルでない、または存在しない"}
        size = path.stat().st_size
        with path.open(encoding="utf-8", errors="replace") as stream:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n")
                if legacy_projection and line == legacy_projection[0]:
                    legacy_projection.popleft()
                    continue
                legacy_projection.clear()
                if line.startswith(("[stdout] ", "[stderr] ")):
                    header_open = False
                    legacy_projection.extend(run_skill_progress.legacy_projection(line.partition(" ")[2], seen_tools))
                if line.startswith("[stdout] "):
                    payload = line.removeprefix("[stdout] ")

                    if diagnostic_process.is_error_event(payload):
                        event_count += 1
                        events.append(ByteDiagnosticLine(_portable_text(payload), line_bytes=_LINE_BYTES))
                    else:
                        omitted += 1
                    continue
                if not line.strip() or line.startswith(("[progress] ", "スキルへ渡す引数: ", "引数: ", "ログ: ")):
                    continue
                clipped = ByteDiagnosticLine(_portable_text(line), line_bytes=_LINE_BYTES)

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
        return {"status": "読取不能", "error": _portable_text(str(error))}
    kept_events = newest_diagnostic_lines(list(events), total_bytes=_EVENT_BYTES)
    event_bytes = sum(len(event.encode()) + 1 for event in kept_events)
    kept_tail = newest_diagnostic_lines(list(tail), total_bytes=_EXCERPT_BYTES - header_bytes - event_bytes)
    return {
        "status": "取得済み" if header or kept_events or kept_tail else "ログに採用できる診断がない",
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


def _record_location(target: dict[str, Any]) -> str:
    raw = target.get("transcript_path")
    if not isinstance(raw, str) or not raw:
        return "所在未記録"
    parts = raw.replace("\\", "/").split("/")
    if target.get("host") == "codex":
        if "archived_sessions" in parts:
            return "Codexホームの archived_sessions"
        if "sessions" in parts:
            return "Codexホームの sessions"
    if target.get("host") == "claude" and "projects" in parts:
        return "Claude Code設定の projects"
    return "hookが報告した記録の場所"


def _prepare_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        **{field: target[field] for field in _TARGET_FIELDS if target.get(field) is not None},
        "record_location": _record_location(target),
        "record_state": _record_state(target.get("transcript_path")),
        "previous_scope": _previous_scope(target),
    }


def failure_details(manifest: dict[str, Any]) -> str:
    reason = manifest.get("error") or ""
    stage = reason.partition(":")[0] if reason else "不明"
    prepared = {(item.get("host"), item.get("session_id")) for item in manifest.get("prepared", [])}
    details = {
        "os": sys.platform,
        "plugin_version": manifest.get("failure_version") or "不明",
        "plugin_source": manifest.get("failure_source") or {"commit": None, "source": "不明"},
        "stage": stage,
        "error": diagnostic_excerpt(_portable_text(reason), prefix_bytes=_EXCERPT_BYTES) or None,
        "failure_diagnostics": manifest.get("failure_diagnostics"),
        "prepared_count": len(manifest.get("prepared", [])),
        "analysis_succeeded": manifest.get("analysis_succeeded"),
        "exit_code": manifest.get("exit_code"),
        "log": _log_diagnostics(manifest.get("log_path")),
        "stop_log": _stop_log(manifest.get("run_id")) if manifest.get("log_path") else {"status": "モデル未起動"},
    }
    if stage == "prepare-or-state":
        details["targets"] = [
            _prepare_target(target)
            for target in manifest["targets"]
            if (target.get("host"), target.get("session_id")) not in prepared
        ]
    return _details_block(details)


def _details_block(details: dict[str, Any]) -> str:
    return (
        "### 失敗時の入力と診断\n\n"
        + "\n".join(
            "    " + line
            for line in json.dumps(
                _portable_values(redact_diagnostic_values(details)), ensure_ascii=False, indent=2
            ).splitlines()
        )
        + "\n"
    )


def failure_message(failure_key: str, reason: str, manifest: dict[str, Any], target_repo: str) -> str:
    body = (
        "# 非同期振り返り（review-session batch）の失敗の是正\n\n"
        "非同期振り返りが対象の準備・分析・報告のいずれかを完了できなかった。"
        "同じ原因の未完了AWIがある間は対象の自動振り返りを止めている。"
        "AWIがadopted・rejectedで終了した場合、または未完了AWIが無くagent-toolkitのバージョンが変わった場合に再試行する。"
        "削除だけでは再試行せず、起票済みの同じ失敗は終了AWIを観測するまで再起票しない。\n\n"
        "## 反映内容と反映先\n\n"
        "下記のバージョン・工程・診断から原因を特定し、振り返りの前後処理または準備処理を是正する。"
        f"反映先は`{target_repo}`とする。"
        "是正にユーザーの判断が要る点があれば、UWIで確認する。\n\n"
        "## 調査結果と判断根拠\n\n"
        "下記は失敗時のバージョンと診断である。準備失敗では対象と記録の状態、実行以後では工程と結果を示す。"
        "取得できず調査になお必要な情報だけ、起票元へ照会する。会話本文は転記していない。\n\n"
        "## 完成条件\n\n"
        "下記の診断を使って失敗を再現するテストを作り、是正前の失敗と是正後の成功を確認する。"
        "再現テストで準備から失敗した工程までの成功を確認する。"
        "準備以降に失敗した場合は、子の結果回収、振り返り、報告の同期とsession-review-state finishまで完了する。"
        "起票ホストでの実際の再試行は是正を含むバージョンの配布後に行い、処理担当は観測しなくてよい。"
        "手動で再試行する場合は`atk run-skill agent-toolkit:review-session --args batch`を使う。"
        "このAWIの終了後、`session_review_auto_start=true`で開始条件を満たす場合に限り、"
        "セッション開始・終了時に自動起動できる。"
        "`session_review_auto_start=false`の場合は自動起動しないため、手動実行する。\n\n"
        "## 詳細\n\n"
        f"{failure_details({**manifest, 'error': reason})}"
    )
    metadata = f"session_review_failure_key: {json.dumps(failure_key)}\n"
    return f"---\n{metadata}---\n\n{body}"


def meta_failure_evidence(
    root: pathlib.Path,
    *,
    stage: str,
    detail: str | None,
    log_path: pathlib.Path | None,
    report_target: str | None,
) -> dict[str, Any]:
    try:
        version = plugin_version(root)
        source = plugin_source(root, version)
    except (OSError, ValueError, KeyError) as error:
        version = "不明"
        source = {"commit": None, "source": "バージョン・配布元を取得できない: " + _portable_text(str(error))}
    missing = (
        "実行ログは未作成（モデル実行前の失敗）"
        if stage.startswith(("worktree-", "queue-"))
        else "実行ログの所在が記録されていない"
    )
    return {
        "failure_version": version,
        "failure_source": source,
        "failure_diagnostics": {
            "report_target": report_target,
            "error": diagnostic_excerpt(_portable_text(detail), prefix_bytes=_EXCERPT_BYTES) if detail else None,
            "log": _log_diagnostics(log_path, missing=missing),
        },
    }


def meta_failure_message(record: dict[str, Any]) -> str:
    details = {
        "started_at": record.get("started_at"),
        "stage": record.get("failure_stage"),
        "exit_code": record.get("exit_code"),
        "plugin_version": record.get("failure_version") or "不明（失敗時の記録なし）",
        "plugin_source": record.get("failure_source") or {"commit": None, "source": "失敗時の記録なし"},
        "failure_diagnostics": record.get("failure_diagnostics") or {"status": "失敗時の診断を状態に記録していない"},
    }
    return (
        f"---\nsession_review_failure_key: {json.dumps(record['failure_key'])}\n---\n\n"
        "# 週次メタ分析（review-session meta）の失敗の是正\n\n"
        "週次メタ分析が失敗し、報告を完了できなかった。\n\n"
        "## 反映内容と反映先\n\n"
        "下記のバージョン、工程、判明した診断を使って配布元で原因を是正する。"
        "本文から得られず調査になお必要な情報だけ、起票元へ照会する。\n\n"
        "## 完成条件\n\n"
        "原因を再現する入力で失敗と是正後の報告の保存を確かめる。"
        "手動での確認には `atk run-skill --args meta review-session` を使う。\n\n"
        "## 詳細\n\n" + _details_block(details)
    )
