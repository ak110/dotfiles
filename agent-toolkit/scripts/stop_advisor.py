"""Claude Code・Codex plugin agent-toolkit: Stop hook。

Claude Codeが停止しようとするタイミングで発火する。判定分岐は`main()`の各節を参照する。
概要は次のとおり。`stop_hook_active`真時・非同期作業継続中は無条件approve、
`agent-toolkit:session-review`起動済み時はapproveとする。
いずれにも該当しない通常終了時は、transcriptの絶対パスを含む振り返り誘導文をblockで返す。
終了判定の言語的基準は`agent-toolkit:session-review`「起動方針」節をSSOTとし、
誘導文冒頭へ同一基準（`_message_format.SESSION_REVIEW_PRECHECK`）を事前チェックとして埋め込む。

各判定分岐の最終判定ラベルと根拠は`_stop_gate.append_stop_log`で常時ログへ記録する。
"""

import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import SESSION_REVIEW_PRECHECK  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_review_evidence import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    has_session_review_started,
)
from _session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    read_state,
    sweep_stale_states,
)
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    append_stop_log,
    has_command_invocation,
    is_pending_async_work,
)

# pylint: disable-next=wrong-import-position,import-error
from _stop_gate import parse_stop_session as _parse_stop_session  # noqa: E402

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/stop_advisor"

# 振り返り誘導の対象スキル名。
_SESSION_REVIEW_SKILL = "agent-toolkit:session-review"

# Claude Code transcript内のユーザーターンでスラッシュコマンド起動痕跡を検出する正規表現。
_SESSION_REVIEW_COMMAND_RE = re.compile(r"<command-name>/agent-toolkit:session-review</command-name>")


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def _has_uncommitted_changes(cwd: str) -> bool:
    """作業ディレクトリに未コミットの変更がある場合に真を返す。

    untrackedファイル（`??`）は対象外とする（意図的に未追跡の場合があるため）。
    git未導入・リポジトリ外・コマンド失敗時は偽を返す。
    判定は共有ヘルパー`_git_status.has_tracked_dirty`（`git status --porcelain`実行）へ委ねる。
    """
    return bool(_git_status.has_tracked_dirty(cwd))


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
    if not any(_git_status.is_tracked_change(line) for line in output.splitlines()):
        return None
    return output


def _status_summary(cwd: str) -> dict[str, str]:
    """`systemMessage`用のgit statusサマリーを組み立てる（ユーザー表示専用、LLMには渡らない）。

    全文ではなく変更ファイル件数のみを表示し、ユーザー向け通知の分量を抑える。
    """
    if not cwd:
        return {}
    status = _git_status_for_display(cwd)
    if not status:
        return {}
    return {"systemMessage": f"[git status] {len(status.splitlines())} changed file(s)"}


def _approve(cwd: str = "") -> None:
    print(json.dumps(_status_summary(cwd), ensure_ascii=False))


def _emit_block_with_status(reason: str, cwd: str = "") -> None:
    """振り返り誘導を`decision: "block"`＋`reason`で出力し、未コミット変更があれば`systemMessage`で件数を併記する。

    `reason`をhookの応答に載せることでセッション終端ターンを継続させ、振り返りスキルを当該ターン内で強制起動する。
    `stop_hook_active`保護で1回のみ発火する前提。
    """
    output: dict[str, str] = {"decision": "block", "reason": reason}
    output.update(_status_summary(cwd))
    print(json.dumps(output, ensure_ascii=False))


def main(payload_text: str) -> int:
    """Stop hookでセッション終了時通知を出力するエントリポイント。"""
    resolved = _parse_stop_session(payload_text, _approve)
    if resolved is None:
        return 0
    session_id, payload = resolved

    # Stop hookが直前のターンで既にブロック済みの再呼び出し。
    # 同一判定を繰り返すと連続ブロック上限に達して強制終了するため、
    # 構造判定・通知生成・git status出力をせず即座にapproveする。
    if payload.get("stop_hook_active") is True:
        append_stop_log(session_id, "approve_stop_hook_active", {"stop_hook_active": True})
        _approve()
        return 0

    # git_log_checkedのリセットはStop契機から除外する
    # （対象コミットの親子関係が変化する操作＝commit / rebase / resetに限定する。
    # posttooluse.py `_GIT_LOG_RESET_SUBCOMMANDS`が単一のリセット判定箇所である）。

    cwd = payload.get("cwd", "")
    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    is_codex = "model" in payload

    # Codexのhook定義にはSessionEndが無いため、Stopを期限切れ状態の回収契機にも使う。
    # 期限内の状態は残るため、同じsession_idで再開した場合の起動済み記録は維持される。
    if is_codex:
        sweep_stale_states()

    # Claude Codeで構造的にセッション継続中ならapprove。
    # 非同期待機ツールまたは未完了background task（Agent・Bash・MCP）が存在するケース。
    # Codex rolloutは安定した終了ゲートではないため背景作業判定へ渡さない。
    if not is_codex and is_pending_async_work(transcript_path, session_id):
        append_stop_log(session_id, "approve_pending_async", {})
        _approve()
        return 0

    state = read_state(session_id)
    # 既に振り返りスキルが起動された痕跡があれば以後のStopは即approve。
    # 観測はPostToolUse(Skill)が`session_review_invoked`辞書へ記録するほか、
    # スラッシュコマンド起動痕跡（transcript走査）でも代替検出する。
    invoked = state.get("session_review_invoked")
    state_invoked = isinstance(invoked, dict) and invoked.get(_SESSION_REVIEW_SKILL) is True
    command_invoked = not is_codex and has_command_invocation(transcript_path, _SESSION_REVIEW_COMMAND_RE)
    recovered_invocation = is_codex and not state_invoked and has_session_review_started(transcript_path)
    if state_invoked or command_invoked or recovered_invocation:
        append_stop_log(
            session_id,
            "approve_review_invoked",
            {
                "session_review_invoked": state_invoked,
                "command_detected": command_invoked,
                "evidence_detected": recovered_invocation,
            },
        )
        _approve(cwd=cwd)
        return 0

    # --- セッション振り返り誘導（毎回提示）---
    # 終了判定の基準・振り返り手順はスキル本体の「起動方針」節に集約する。
    # 誘導文の先頭にSESSION_REVIEW_PRECHECKを付与し、質問直後など終了相当の
    # ケースではスキル起動自体を抑止する。
    reason = _llm_notice(
        f"{SESSION_REVIEW_PRECHECK} If so, use the `{_SESSION_REVIEW_SKILL}` skill immediately"
        " according to its activation policy. Pass the following values from this Stop payload:"
        f" session_id={session_id}; transcript_path={transcript_path}"
    )
    append_stop_log(session_id, "block_session_review", {})
    _emit_block_with_status(reason, cwd=cwd if isinstance(cwd, str) else "")
    return 0
