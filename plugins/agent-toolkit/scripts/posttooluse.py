#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態記録。

Bash / Write / Edit / MultiEdit の実行後にイベントを検出し、
セッション状態ファイルに記録する。
PreToolUse や Stop フックが参照して警告・提案の判定に使う。

検出対象:

1. テスト実行 — pytest / make test / pyfltr / npm test / cargo test 等 (Bash)
2. Git 状態確認 — git status / git log / git diff (Bash)
3. codex exec resume — codex レビューの再実行 (Bash)
4. git log 確認状態のリセット — git commit / rebase / push (Bash),
   ファイル編集 (Write / Edit / MultiEdit) の実行後にリセットし、
   amend / rebase 前に改めて git log を確認させる

状態ファイルのパス: `{tempdir}/claude-agent-toolkit-{session_id}.json`

exit code 契約:

- exit 0: 常に 0（PostToolUse は許可判定に関与しない。サイレント記録のみ）

予期せぬ例外は 0 にフォールバックする。
"""

import contextlib
import json
import pathlib
import re
import sys
import tempfile
import traceback

# --- テスト実行検出パターン ---

_TEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+)?(?:python\s+-m\s+)?pytest\b"),
    re.compile(r"(?:^|[;&|]\s*)make\s+test\b"),
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+)?pyfltr\s+(?:run|ci|fast|agent)\b"),
    re.compile(r"(?:^|[;&|]\s*)(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b"),
    re.compile(r"(?:^|[;&|]\s*)cargo\s+test\b"),
)

# --- Git 状態確認検出パターン ---

_GIT_STATUS_PATTERN = re.compile(r"(?:^|[;&|]\s*)git\s+(?:status|log|diff)\b")

# --- git log 確認パターン ---

_GIT_LOG_PATTERN = re.compile(r"(?:^|[;&|]\s*)git\s+log\b")

# --- git log リセットパターン (commit / rebase / push) ---

_GIT_LOG_RESET_PATTERN = re.compile(r"\bgit\s+(?:commit|rebase|push)\b")

# --- codex exec resume 検出パターン ---

_CODEX_RESUME_PATTERN = re.compile(r"\bcodex\s+exec\s+resume\b")


def _state_path(session_id: str) -> pathlib.Path:
    """セッション状態ファイルのパスを返す。"""
    return pathlib.Path(tempfile.gettempdir()) / f"claude-agent-toolkit-{session_id}.json"


def _read_state(path: pathlib.Path) -> dict:
    """状態ファイルを読む。不在・破損時はデフォルト値を返す。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write_state(path: pathlib.Path, state: dict) -> None:
    """状態ファイルを書く。書き込み失敗は無視する（状態記録は best-effort）。"""
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(state), encoding="utf-8")


def _main() -> int:
    """エントリポイント。常に 0 を返す。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    path = _state_path(session_id)

    # Write / Edit / MultiEdit: ファイル編集は git log 確認状態をリセットする
    if tool_name in ("Write", "Edit", "MultiEdit"):
        state = _read_state(path)
        if state.get("git_log_checked", False):
            state["git_log_checked"] = False
            _write_state(path, state)
        return 0

    # Bash 以外はここで終了
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return 0

    state = _read_state(path)
    changed = False

    # テスト実行検出
    if not state.get("test_executed", False):
        for pattern in _TEST_PATTERNS:
            if pattern.search(command):
                state["test_executed"] = True
                changed = True
                break

    # Git 状態確認検出
    if not state.get("git_status_checked", False) and _GIT_STATUS_PATTERN.search(command):
        state["git_status_checked"] = True
        changed = True

    # git log 確認状態の管理
    if _GIT_LOG_PATTERN.search(command):
        if not state.get("git_log_checked", False):
            state["git_log_checked"] = True
            changed = True
    elif _GIT_LOG_RESET_PATTERN.search(command) and state.get("git_log_checked", False):
        # commit / rebase / push は git log 確認状態をリセットする
        state["git_log_checked"] = False
        changed = True

    # codex exec resume 検出
    if _CODEX_RESUME_PATTERN.search(command):
        state["codex_resume_count"] = state.get("codex_resume_count", 0) + 1
        changed = True

    if changed:
        _write_state(path, state)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001 -- plugin が破損して編集できなくなる事故を避けるため
        traceback.print_exc()
        sys.exit(0)
