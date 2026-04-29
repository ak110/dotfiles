#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code plugin agent-toolkit: Stop hook.

Claude Code が停止しようとするタイミングで発火する。

未コミット変更ブロックとセッション振り返り提案ブロックの 2 種類の block を出す。
両ブロックは共通ゲート `_stop_gate.is_real_session_end` で「直前アシスタントターンが
完了文言を含み、かつ質問・待機語・非同期待機ツールがない」場合に限り発火する。
作業途中の一時停止・バックグラウンド待機・ユーザー質問待ちでの誤検出を避けるため、
transcript を解釈できない異常系では block しない。

未コミット変更ブロック:
作業ディレクトリに未コミット変更があれば block し、コミット要否をユーザーに確認させる。

セッション振り返り提案ブロック:
真のセッション終了であれば、プロジェクトドキュメント全般 (CLAUDE.md・README.md・docs/ 配下など)
および agent-toolkit プラグインへの改善提案を促す。
1 セッションにつき 1 回のみ発火する。

exit code: 常に 0。
stdout に JSON (decision: approve | block) を出力する。
"""

import json
import pathlib
import subprocess
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _session_state import read_state, write_state  # noqa: E402  # pylint: disable=wrong-import-position
from _stop_gate import is_real_session_end  # noqa: E402  # pylint: disable=wrong-import-position

# LLM 宛てメッセージの共通プレフィックス / サフィックス。
# 詳細は skills/writing-standards/references/claude-hooks.md を参照。
_MESSAGE_PREFIX = "[auto-generated: agent-toolkit/stop_advisor]"
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM 宛てメッセージを標準プレフィックス / サフィックス付きで整形する。"""
    prefix = f"{_MESSAGE_PREFIX}[{tag}]" if tag else _MESSAGE_PREFIX
    return f"{prefix} {body} {_MESSAGE_SUFFIX}"


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


def _git_status_for_display(cwd: str) -> str | None:
    """ユーザー表示用のgit status --shortを返す。

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
    # untrackedファイルのみの場合は表示しない
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

    # Stop のたびに git_log_checked をリセットする。
    # ユーザーが裏で push している可能性があるため、
    # 再開後の amend / rebase には改めて log 確認を要求する。
    if state.get("git_log_checked", False):
        state["git_log_checked"] = False
        write_state(session_id, state)

    cwd = payload.get("cwd", "")
    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""

    # --- 未コミット変更ブロック ---
    # 共通ゲート `is_real_session_end` で「直前アシスタントターンが完了文言を含み、
    # かつ質問・待機語・非同期待機ツールがない」状態に限って block する。
    # 作業途中の一時停止・探索中・ユーザー確認待ち・バックグラウンド待機等での
    # false positive を避ける。
    # ブロックは 1 回に限る。理由:
    #   - Claude Code は block を受けると再度 Stop を試みるため、
    #     同一メッセージの繰り返しに意味がない
    #   - 2 回目以降は block_count != 0 でブロックせず、
    #     後続の stop_advice_given チェックへフォールスルーする
    #   - 質問検出の transcript flush タイミング問題
    #     （未フラッシュ時の質問検出失敗）も 2 回目で自然に通過する
    if isinstance(cwd, str) and _has_uncommitted_changes(cwd) and is_real_session_end(transcript_path):
        block_count = state.get("uncommitted_block_count", 0)
        if block_count == 0:
            state["uncommitted_block_count"] = block_count + 1
            write_state(session_id, state)
            _block(
                _llm_notice(
                    "uncommitted changes detected."
                    " Ask the user whether to commit the changes, or explain"
                    " why they should not be committed."
                    " Do not commit without user confirmation."
                )
            )
            return 0
        # 2 回目以降は block_count != 0 で何もせず、後続処理へフォールスルーする。

    # --- セッション振り返り提案 ---
    # セッション 1 回制限: block 後に Claude が再度 Stop するため、
    # 2 回目以降は即座に approve する。
    if state.get("stop_advice_given", False):
        _approve(cwd=cwd)
        return 0

    # 共通ゲート: 直前ターンが真のセッション終了でない場合は block を見送る。
    # `stop_advice_given` を記録しないため、ユーザーが応答した後や作業完了後の
    # Stop で改めて評価される。
    if not is_real_session_end(transcript_path):
        _approve(cwd=cwd)
        return 0

    # 発火: block 前に stop_advice_given を記録する。
    # block 後の再 Stop 時に上の早期リターンで即 approve するため。
    state["stop_advice_given"] = True
    write_state(session_id, state)

    body = (
        "session review: list improvement suggestions in Japanese."
        " Each suggestion must stand alone for readers without this session's conversation history"
        " (avoid history references like 'the earlier discussion'; describe the observed phenomenon directly)."
        " (a) project documentation in general (CLAUDE.md, README.md, docs/, etc.)"
        " — only knowledge from this session that helps future Claude work on this project"
        " (observation domains: bash commands, code style/patterns, test approaches, environment quirks,"
        " warnings/pitfalls, repeated user corrections; one concept per line, terse)."
        ' For each candidate, output one line of "<target file> — <one-sentence summary>"; on user'
        " approval, draft and apply the diff via Edit."
        " (b) agent-toolkit plugin (`plugins/agent-toolkit/`) and rules under"
        " `~/.claude/rules/agent-toolkit/`. Apply via Edit only after user approval."
        " Output the suggestions only (no preamble or narration). If none, output '指摘無し'."
    )

    _block(_llm_notice(body))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
