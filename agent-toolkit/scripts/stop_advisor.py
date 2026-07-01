#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code plugin agent-toolkit: Stop hook。

Claude Codeが停止しようとするタイミングで発火する。
approve・通知の分岐は以下のとおり。

- `stop_hook_active`が真（直前の本hook呼び出しが当該ターンの終了を一度ブロックした再呼び出し）:
  連続ブロック上限到達による強制終了を避けるため、構造判定・通知生成をせず無条件approveのみ返す
- 構造的にセッション継続中（非同期待機ツールまたは未完了background task（Agent・Bash双方）あり）:
  サブエージェント等の継続作業中にノイズを増やさないため、approveのみ返し
  git status表示を抑止する
- 既に`agent-toolkit:session-review`スキルが起動済み:
  再Stop間にユーザーが変更を加える可能性に備え、approveに加えて
  git status表示を付与する
- `session_review_extension_pending`フラグが真（個人フックPostToolUseが拡張章対象の
  作業を観測済み）: 配布物側の振り返り誘導を抑制し`approve`を返す
  （未コミット変更があれば`systemMessage`でgit statusを併記する）。
  拡張章フックが配布物誘導と個人章を統合した誘導文を送出する
- 上記いずれでもない通常終了: `decision: "block"`＋`reason`でセッション振り返り誘導文を出力し、
  未コミット変更があれば`systemMessage`でgit statusを付与する

終了判定の言語的判定基準（完了文言・質問・待機表明の判別）は
`agent-toolkit:session-review`スキル本体の「起動方針」節で定義する。
本hookは誘導文の先頭に同一基準（`_message_format.SESSION_REVIEW_PRECHECK`）を
事前チェックとして埋め込み、質問直後等の終了相当ケースでスキル起動を抑止する。
構造判定（非同期待機ツール残存・未完了background task検出・
`stop_hook_active`）はhook側（`_stop_gate.py`・本ファイル）が担当し、判定レイヤーを分離する。

対象スキルは`session_review_invoked`辞書経由の起動済みフラグに加え、
transcript内のユーザーターンに`<command-name>/agent-toolkit:session-review</command-name>`が
含まれるスラッシュコマンド起動痕跡（`_stop_gate.has_command_invocation`）でも起動済み扱いとする。
PostToolUse側のフラグ記録がスラッシュコマンド起動時のツール呼び出し扱いを取りこぼす場合の代替経路。

各判定分岐の最終判定ラベルと根拠は`_stop_gate.append_stop_log`で常時ログへ記録する。
"""

import json
import pathlib
import re
import subprocess
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _message_format import SESSION_REVIEW_PRECHECK  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    append_stop_log,
    has_command_invocation,
    is_pending_async_work,
)

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/stop_advisor"

# 振り返り誘導の対象スキル名。
_SESSION_REVIEW_SKILL = "agent-toolkit:session-review"

# transcript内のユーザーターンでスラッシュコマンド起動痕跡を検出する正規表現。
_SESSION_REVIEW_COMMAND_RE = re.compile(r"<command-name>/agent-toolkit:session-review</command-name>")


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def _is_tracked_change(line: str) -> bool:
    """Git status --porcelain / --shortの1行がtracked変更かどうかを返す。

    untrackedファイル（`??`）は対象外とする。
    """
    return bool(line) and not line.startswith("??")


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
    return any(_is_tracked_change(line) for line in result.stdout.splitlines())


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
    if not any(_is_tracked_change(line) for line in output.splitlines()):
        return None
    return output


def _approve(cwd: str = "") -> None:
    output: dict[str, str] = {}
    if cwd:
        status = _git_status_for_display(cwd)
        if status:
            output["systemMessage"] = f"[git status]\n{status}"
    print(json.dumps(output, ensure_ascii=False))


def _emit_block_with_status(reason: str, cwd: str = "") -> None:
    """振り返り誘導を`decision: "block"`＋`reason`で出力し、未コミット変更があれば`systemMessage`でgit statusを併記する。

    `reason`をhookの応答に載せることでセッション終端ターンを継続させ、振り返りスキルを当該ターン内で強制起動する。
    `stop_hook_active`保護で1回のみ発火する前提。
    """
    output: dict[str, str] = {"decision": "block", "reason": reason}
    if cwd:
        status = _git_status_for_display(cwd)
        if status:
            output["systemMessage"] = f"[git status]\n{status}"
    print(json.dumps(output, ensure_ascii=False))


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

    # Stop hookが直前のターンで既にブロック済みの再呼び出し。
    # 同一判定を繰り返すと連続ブロック上限に達して強制終了するため、
    # 構造判定・通知生成・git status出力をせず即座にapproveする。
    if payload.get("stop_hook_active") is True:
        append_stop_log(session_id, "approve_stop_hook_active", {"stop_hook_active": True})
        _approve()
        return 0

    # Stopのたびにgit_log_checkedをリセットする。
    # セッション停止中にユーザーがpushしている可能性があるため、
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
    # 非同期待機ツールまたは未完了background task（Agent・Bash双方）が存在するケース。
    if is_pending_async_work(transcript_path, session_id):
        append_stop_log(session_id, "approve_pending_async", {})
        _approve()
        return 0

    # 既に振り返りスキルが起動された痕跡があれば以後のStopは即approve。
    # 観測はPostToolUse(Skill)が`session_review_invoked`辞書へ記録するほか、
    # スラッシュコマンド起動痕跡（transcript走査）でも代替検出する。
    state = read_state(session_id)
    invoked = state.get("session_review_invoked")
    state_invoked = isinstance(invoked, dict) and invoked.get(_SESSION_REVIEW_SKILL) is True
    command_invoked = has_command_invocation(transcript_path, _SESSION_REVIEW_COMMAND_RE)
    if state_invoked or command_invoked:
        append_stop_log(
            session_id,
            "approve_review_invoked",
            {"session_review_invoked": state_invoked, "command_detected": command_invoked},
        )
        _approve(cwd=cwd)
        return 0

    # 拡張章フックの存在を観測した場合、振り返り誘導の重複送出を避けるため
    # 配布物側の誘導を抑制する。
    extension_pending = state.get("session_review_extension_pending") is True
    if extension_pending:
        append_stop_log(session_id, "approve_extension_pending", {})
        _approve(cwd=cwd)
        return 0

    # --- セッション振り返り誘導（毎回提示）---
    # 終了判定の基準・振り返り手順はスキル本体の「起動方針」節に集約する。
    # 誘導文の先頭にSESSION_REVIEW_PRECHECKを付与し、質問直後など終了相当の
    # ケースではスキル起動自体を抑止する。
    reason = _llm_notice(
        f"{SESSION_REVIEW_PRECHECK} If so, invoke the `{_SESSION_REVIEW_SKILL}` Skill via the Skill tool"
        " and follow its activation policy section to decide whether to proceed with the review."
    )
    append_stop_log(session_id, "block_session_review", {})
    _emit_block_with_status(reason, cwd=cwd if isinstance(cwd, str) else "")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- hook自身の異常終了をホスト側プロセスへ波及させないため広範に捕捉（fail-open）
        traceback.print_exc()
        _approve()
        sys.exit(0)
