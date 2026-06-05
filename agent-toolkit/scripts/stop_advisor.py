#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code plugin agent-toolkit: Stop hook。

Claude Codeが停止しようとするタイミングで発火する。
セッションが構造的に継続中（非同期待機ツールまたは未完了background Agentあり）の場合と、
セッション中に既に`agent-toolkit:session-review`スキルが起動された場合はapproveする。
それ以外では、未コミット変更の有無に応じた通知とセッション振り返り誘導を1blockにまとめて返す。

終了判定の言語的部分（完了文言・質問・待機表明の判別）と振り返り手順は
`agent-toolkit:session-review`スキル本体の「起動方針」節へ全面委譲する。
"""

import json
import pathlib
import subprocess
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    has_session_review_skill_invoked,
    is_pending_async_work,
)

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/stop_advisor"

# 振り返り誘導の対象スキル名。
_SESSION_REVIEW_SKILL = "agent-toolkit:session-review"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def _has_uncommitted_changes(cwd: str) -> bool:
    """作業ディレクトリに未コミットの変更がある場合に真を返す。

    untrackedファイル（`??`）は対象外とする（意図的に未追跡の場合があるため）。
    git未導入・リポジトリ外・コマンド失敗時は偽を返す。
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


def _git_status_for_display(cwd: str) -> str | None:
    """ユーザー表示用の`git status --short`の出力を返す。

    未コミット変更がない場合・untrackedのみの場合・エラー時はNoneを返す。
    """
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    if not output:
        return None
    # untrackedファイルのみの場合は表示しない。
    if all(line.startswith("??") for line in output.splitlines()):
        return None
    return output


def _approve(cwd: str = "") -> None:
    output: dict[str, str] = {"decision": "approve"}
    if cwd:
        status = _git_status_for_display(cwd)
        if status:
            output["systemMessage"] = f"[git status]\n{status}"
    print(json.dumps(output, ensure_ascii=False))


def _block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def main() -> int:
    """Stop hookでセッション終了時通知を出力するエントリポイント。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        _approve()
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        _approve()
        return 0

    # Stopのたびにgit_log_checkedをリセットする。
    # ユーザーが裏でpushしている可能性があるため、
    # 再開後のamend / rebaseには改めてlog確認を要求する。
    # `git_log_checked`はcwd別辞書`{cwd: True}`を採用するため、
    # 全エントリをまとめてクリアする。
    def _reset_git_log_checked(state: dict) -> dict | None:
        if not state.get("git_log_checked"):
            return None
        state["git_log_checked"] = {}
        return state

    update_state(session_id, _reset_git_log_checked)

    cwd = payload.get("cwd", "")
    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""

    # 構造的にセッション継続中ならapprove。
    # 非同期待機ツールまたは未完了background Agentが存在するケース。
    if is_pending_async_work(transcript_path):
        _approve(cwd=cwd)
        return 0

    # 既に振り返りスキルが起動された痕跡があれば以後のStopは即approve。
    if has_session_review_skill_invoked(transcript_path, _SESSION_REVIEW_SKILL):
        _approve(cwd=cwd)
        return 0

    messages: list[str] = []

    # --- 未コミット変更通知（毎回提示）---
    if isinstance(cwd, str) and _has_uncommitted_changes(cwd):
        messages.append(
            _llm_notice(
                "uncommitted changes detected."
                " Ask the user whether to commit the changes, or explain"
                " why they should not be committed."
                " Do not commit without user confirmation."
            )
        )

    # --- セッション振り返り誘導（毎回提示）---
    # 終了判定の基準・振り返り手順はスキル本体の「起動方針」節に集約する。
    messages.append(
        _llm_notice(
            f"Invoke the `{_SESSION_REVIEW_SKILL}` Skill via the Skill tool"
            " and follow its activation policy section to decide whether to proceed with the review."
        )
    )

    _block("\n\n".join(messages))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
