#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code plugin agent-toolkit: Stop hook。

Claude Codeが停止しようとするタイミングで発火する。
未コミット変更通知とセッション振り返り提案の2種類の通知を、
共通ゲート`_stop_gate.is_real_session_end`の判定通過後にまとめて出力する。

利用者環境に他のStop hookやユーザー手動起動の振り返りスキルが並列配置される場合、
本hookは共通基盤として全体に適用される指示
（自己完結性・行フォーマット・空時の「指摘無し」・出力スタイル）をreasonへ集約する。
他のStop hookやユーザー手動起動スキル側は章固有の指示のみ記述し、文面が二重表示されないよう設計することを想定する。
"""

import json
import pathlib
import subprocess
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state, write_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import is_real_session_end  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/stop_advisor"


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

    state = read_state(session_id)

    # Stopのたびにgit_log_checkedをリセットする。
    # ユーザーが裏でpushしている可能性があるため、
    # 再開後のamend / rebaseには改めてlog確認を要求する。
    # `git_log_checked`はcwd別辞書`{cwd: True}`を採用するため、
    # 全エントリをまとめてクリアする。
    if state.get("git_log_checked"):
        state["git_log_checked"] = {}
        write_state(session_id, state)

    cwd = payload.get("cwd", "")
    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""

    # 共通ゲート: 直前アシスタントターンが完了文言を含み、
    # かつ質問・待機語・非同期待機ツールがない状態でのみ通知候補とする。
    # 作業途中の一時停止・探索中・ユーザー確認待ち・バックグラウンド待機等での
    # false positiveを避ける。
    real_end = is_real_session_end(transcript_path)
    if not real_end:
        _approve(cwd=cwd)
        return 0

    # 各通知は1セッション1回までに制限する。理由:
    #   - Claude Codeはblockを受信すると再度Stopを試みるため、
    #     同一メッセージの繰り返しに意味がない。
    #   - 質問検出のtranscriptフラッシュタイミング問題
    #     （未フラッシュ時の質問検出失敗）も2回目以降のStopで自然に通過する。
    messages: list[str] = []

    # --- 未コミット変更通知（1回限り）---
    if isinstance(cwd, str) and _has_uncommitted_changes(cwd):
        block_count = state.get("uncommitted_block_count", 0)
        if block_count == 0:
            state["uncommitted_block_count"] = block_count + 1
            write_state(session_id, state)
            messages.append(
                _llm_notice(
                    "uncommitted changes detected."
                    " Ask the user whether to commit the changes, or explain"
                    " why they should not be committed."
                    " Do not commit without user confirmation."
                )
            )

    # --- セッション振り返り提案（1回限り）---
    if not state.get("stop_advice_given", False):
        # block前にstop_advice_givenを記録する。
        # block後の再Stop時に同フラグで即スキップするため。
        state["stop_advice_given"] = True
        write_state(session_id, state)

        body = (
            "session review: list improvement suggestions in Japanese."
            " Each suggestion must stand alone for readers without this session's conversation history"
            " (avoid history references like 'the earlier discussion'; describe the observed phenomenon directly)."
            " Follow a two-step procedure."
            " Step 1 (gather candidates from observation sources):"
            " scan the session for"
            " (i) user interruption / corrections,"
            " (ii) Edit/Write that did not apply as expected, and"
            " (iii) events blocked by hooks."
            " Step 2 (filter): verify each candidate against the following four checks"
            " and list only items that pass all four:"
            " (a) trace back to the root cause rather than describing the surface symptom;"
            " (b) assess recurrence risk and impact — exclude one-off or incidental events;"
            " (c) confirm the issue is not already addressed by existing CLAUDE.md, rules, or skills;"
            " (d) select the most appropriate target by proximity-to-code priority"
            " (in-code docstring/comment → CLAUDE.md / .claude/rules → .claude/skills),"
            " not the most convenient place to write."
            " Target project documentation in general (CLAUDE.md, README.md, docs/, etc.)"
            " — only knowledge from this session that helps future Claude work on this project"
            " (observation domains: bash commands, code style/patterns, test approaches, environment quirks,"
            " warnings/pitfalls, repeated user corrections,"
            " coding agent behavior improvements"
            " (cases where the agent's judgment, confirmation discipline, or plan granularity"
            " could have been codified into rules to prevent recurrence);"
            " one concept per line, terse)."
            " Output format: start with the heading '## プロジェクトドキュメント改善提案'"
            " and list each item as '- <対象ファイル> — <提案内容>'."
            " If none, write '指摘無し' under the same heading."
            " If the user opts to apply, first present the proposed change as a diff-formatted code block;"
            " apply via Edit only after explicit user approval."
            " Output the suggestions only (no preamble or narration)."
        )
        messages.append(_llm_notice(body))

    if messages:
        # 複数通知は空行で区切って1blockにまとめる。
        # コーディングエージェント側はそれぞれの`[auto-generated: ...]`プレフィックスで通知の境界を識別する。
        _block("\n\n".join(messages))
        return 0

    _approve(cwd=cwd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
