#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code plugin agent-toolkit: Stop hook.

Claude Code が停止しようとするタイミングで発火する。
transcript を分析してユーザーからの修正指示の多寡を判定し、
閾値を超えた場合に CLAUDE.md 更新を提案する。
codex exec resume が多い場合も同様に提案する。

1 セッションにつき 1 回のみ発火する。
2 回目以降の Stop は即座に approve する。

exit code: 常に 0。
stdout に JSON (decision: approve | block) を出力する。
"""

import contextlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import traceback

# --- 修正キーワード ---

_CORRECTION_KEYWORDS: tuple[str, ...] = (
    "違う",
    "そうじゃ",
    "そうでなく",
    "じゃなく",
    "間違",
    "やり直",
    "ではなく",
    "戻して",
    "さっき言った",
    "指示した通り",
    "指示通り",
)

_KEYWORD_THRESHOLD = 3
_CODEX_RESUME_THRESHOLD = 2
_UNCOMMITTED_BLOCK_LIMIT = 2


def _state_path(session_id: str) -> pathlib.Path:
    """posttooluse.py と共通のパス規則。"""
    return pathlib.Path(tempfile.gettempdir()) / f"claude-agent-toolkit-{session_id}.json"


def _read_state(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write_state(path: pathlib.Path, state: dict) -> None:
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(state), encoding="utf-8")


def _count_keywords(transcript_path: str) -> int:
    """Transcript 内の修正キーワード出現数を返す。"""
    try:
        text = pathlib.Path(transcript_path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return 0
    count = 0
    for keyword in _CORRECTION_KEYWORDS:
        count += len(re.findall(re.escape(keyword), text))
    return count


def _has_uncommitted_changes(cwd: str) -> bool:
    """作業ディレクトリに未コミットの変更があるか判定する。

    untracked ファイル (??) は対象外とする（意図的に未追跡の場合があるため）。
    git 未導入・リポジトリ外・コマンド失敗時は False を返す（安全側）。
    """
    if not cwd:
        return False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return any(line and not line.startswith("??") for line in result.stdout.splitlines())


def _approve() -> None:
    print(json.dumps({"decision": "approve"}))


def _block(message: str) -> None:
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": "session review suggestion",
                "systemMessage": message,
            }
        )
    )


def _main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        _approve()
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        _approve()
        return 0

    state_file = _state_path(session_id)
    state = _read_state(state_file)

    # Stop のたびに git_log_checked をリセットする。
    # ユーザーが裏で push している可能性があるため、
    # 再開後の amend / rebase には改めて log 確認を要求する。
    if state.get("git_log_checked", False):
        state["git_log_checked"] = False
        _write_state(state_file, state)

    # 未コミット変更の検出
    cwd = payload.get("cwd", "")
    if isinstance(cwd, str) and _has_uncommitted_changes(cwd):
        block_count = state.get("uncommitted_block_count", 0)
        if block_count < _UNCOMMITTED_BLOCK_LIMIT:
            state["uncommitted_block_count"] = block_count + 1
            _write_state(state_file, state)
            _block(
                "[agent-toolkit] uncommitted changes detected."
                " Please commit your work before ending the session."
                " If CLAUDE.md or project instructions say otherwise,"
                " you may explain and proceed."
            )
            return 0
        # 閾値到達後は警告のみで通過させる（コミット不能な状況の救済）

    # 2 回目以降は即座に approve
    if state.get("stop_advice_given", False):
        _approve()
        return 0

    # transcript の修正キーワードを集計
    transcript_path = payload.get("transcript_path", "")
    keyword_count = 0
    if isinstance(transcript_path, str) and transcript_path:
        keyword_count = _count_keywords(transcript_path)

    # codex resume の回数を取得
    codex_resume_count = state.get("codex_resume_count", 0)

    keyword_triggered = keyword_count >= _KEYWORD_THRESHOLD
    codex_triggered = codex_resume_count >= _CODEX_RESUME_THRESHOLD

    if not keyword_triggered and not codex_triggered:
        _approve()
        return 0

    # 発火: stop_advice_given を記録して block
    state["stop_advice_given"] = True
    _write_state(state_file, state)

    # 理由に応じたメッセージ構築
    parts: list[str] = []
    parts.append("[agent-toolkit] session review:")
    if keyword_triggered:
        parts.append(f" transcript analysis: {keyword_count} correction indicators detected.")
    if codex_triggered:
        parts.append(f" codex review iterations: {codex_resume_count} resume calls detected.")
    parts.append(
        " Before ending this session, please:"
        " (1) review whether agent.md procedures"
        " (bug-fix 3-step, verify-then-commit) were followed"
        " (2) consider updating CLAUDE.md with lessons learned"
        " (run /claude-md-management:revise-claude-md if appropriate)"
    )

    _block("".join(parts))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
