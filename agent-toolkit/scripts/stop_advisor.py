"""Claude Code plugin agent-toolkit: Stop hook。

Claude Codeが停止しようとするタイミングで発火する。判定分岐は`main()`の各節を参照する。
概要は次のとおり。`stop_hook_active`真時・非同期作業継続中は無条件approve、
直近アシスタント発話にscope-escalationフレーズを検出した場合は`decision: "block"`で
矯正指示を返す（照合カテゴリは`_build_stop_focus_categories`が通常時／
plan-mode等のスキル実行中で切り替える）。`agent-toolkit:session-review`起動済み・
拡張章pending時はapprove、それ以外の通常終了時は振り返り誘導文をblockで返す。
終了判定の言語的基準は`agent-toolkit:session-review`「起動方針」節をSSOTとし、
誘導文冒頭へ同一基準（`_message_format.SESSION_REVIEW_PRECHECK`）を事前チェックとして埋め込む。

各判定分岐の最終判定ラベルと根拠は`_stop_gate.append_stop_log`で常時ログへ記録する。
"""

import functools
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _git_remote  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import SESSION_REVIEW_PRECHECK  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _scope_escalation import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _STOP_FOCUS_CATEGORIES,
    _STOP_FOCUS_CATEGORIES_EXTENDED,
    _match_scope_escalation,
    has_inline_choice_offer,
)
from _session_state import read_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    append_stop_log,
    has_command_invocation,
    is_pending_async_work,
)

# pylint: disable-next=wrong-import-position,import-error
from _stop_gate import parse_stop_session as _parse_stop_session  # noqa: E402
from _transcript import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    assistant_text,
    iter_latest_assistant_messages,
)

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/stop_advisor"

# 振り返り誘導の対象スキル名。
_SESSION_REVIEW_SKILL = "agent-toolkit:session-review"

# 振り返り拡張を提供する既知のリポジトリ識別子。
_SESSION_REVIEW_EXTENSION_REPOSITORY = "github.com/ak110/dotfiles"

# transcript内のユーザーターンでスラッシュコマンド起動痕跡を検出する正規表現。
_SESSION_REVIEW_COMMAND_RE = re.compile(r"<command-name>/agent-toolkit:session-review</command-name>")


def _build_stop_focus_categories(state: dict) -> frozenset[str]:
    """スキル起動フラグに応じてStop経路の照合カテゴリ集合を決定する。

    `plan_mode_skill_invoked`・`process_feedbacks_skill_invoked`・
    `plan_and_add_entries_skill_invoked`のいずれかが真の場合、
    縮退表明を含む可能性が高い文脈と判断し`_STOP_FOCUS_CATEGORIES_EXTENDED`を返す。
    いずれも偽の場合は基本カテゴリ`_STOP_FOCUS_CATEGORIES`を返す。
    """
    if (
        state.get("plan_mode_skill_invoked")
        or state.get("process_feedbacks_skill_invoked")
        or state.get("plan_and_add_entries_skill_invoked")
    ):
        return _STOP_FOCUS_CATEGORIES_EXTENDED
    return _STOP_FOCUS_CATEGORIES


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


@functools.cache
def _has_session_review_extension_repository() -> bool:
    """既知の振り返り拡張リポジトリが`~/dotfiles`に存在する場合に真を返す。"""
    repository = pathlib.Path.home() / "dotfiles"
    return _git_remote.get_normalized_origin(repository) == _SESSION_REVIEW_EXTENSION_REPOSITORY


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

    # 構造的にセッション継続中ならapprove。
    # 非同期待機ツールまたは未完了background task（Agent・Bash双方）が存在するケース。
    if is_pending_async_work(transcript_path, session_id):
        append_stop_log(session_id, "approve_pending_async", {})
        _approve()
        return 0

    # 直近アシスタントターンの応答テキストにscope-escalationフレーズを検出した場合、
    # `decision: "block"`＋`reason`で矯正指示を返す。
    # `stop_hook_active`ガードは既に上流で処理済みのため1回のみ発火する。
    # 振り返りスキル起動済み・拡張章pending等のapprove分岐より前に判定するため、
    # `session_review_invoked`真化以降のセッションでもscope-escalationフレーズを検出時点で矯正できる。
    # transcript読み取り失敗（空パス・存在しないパス・OSエラー等）は
    # `iter_latest_assistant_messages`が空イテレーターを返すため、
    # 本checkは自動的にfail-openとなる。
    state = read_state(session_id)
    focus_categories = _build_stop_focus_categories(state)
    if transcript_path:
        for message in iter_latest_assistant_messages(transcript_path):
            text = assistant_text(message)
            match_result = _match_scope_escalation(text, categories=focus_categories)
            category = match_result[0] if match_result is not None else None
            if category is None and focus_categories == _STOP_FOCUS_CATEGORIES_EXTENDED and has_inline_choice_offer(text):
                category = "approach-confirm"
            if category is not None:
                reason = _llm_notice(
                    f"blocked: matched scope-escalation category `{category}`"
                    " (a phrase that skips normative steps or bypasses required workflow)."
                    " Retract that judgment and continue the mandatory workflow"
                    " (see `agent-toolkit:agent-standards` `references/scope-escalation-phrases.md`).",
                    tag="block",
                )
                append_stop_log(session_id, "block_scope_escalation", {"category": category})
                _emit_block_with_status(reason, cwd=cwd if isinstance(cwd, str) else "")
                return 0

    # 既に振り返りスキルが起動された痕跡があれば以後のStopは即approve。
    # 観測はPostToolUse(Skill)が`session_review_invoked`辞書へ記録するほか、
    # スラッシュコマンド起動痕跡（transcript走査）でも代替検出する。
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

    # 既知の振り返り拡張が導入される環境では、配布物側から重ねて誘導しない。
    if _has_session_review_extension_repository():
        append_stop_log(session_id, "approve_extension_repository", {})
        _approve(cwd=cwd)
        return 0

    # --- セッション振り返り誘導（毎回提示）---
    # 終了判定の基準・振り返り手順はスキル本体の「起動方針」節に集約する。
    # 誘導文の先頭にSESSION_REVIEW_PRECHECKを付与し、質問直後など終了相当の
    # ケースではスキル起動自体を抑止する。
    reason = _llm_notice(
        f"{SESSION_REVIEW_PRECHECK} If so, invoke `{_SESSION_REVIEW_SKILL}` via the Skill tool"
        " per its activation policy section."
    )
    append_stop_log(session_id, "block_session_review", {})
    _emit_block_with_status(reason, cwd=cwd if isinstance(cwd, str) else "")
    return 0
