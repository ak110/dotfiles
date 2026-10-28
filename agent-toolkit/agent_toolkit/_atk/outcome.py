"""atkの実行結果を表す行の接頭辞と出力先を決める共通処理。

`atk`の出力を受け取る主体が返却値または結果行の先頭の語で成否を確定し、終了コードの再確認と
状態の再取得を要さない状態にするため、成功・失敗・警告・該当0件の4種の接頭辞を本モジュールへ集約する。
成功結果が値や構造化データに含まれる場合は、その出力を結果通知とする。
状態変更報告型は標準出力の1行目へ成功行を書く。
"""

from __future__ import annotations

import enum
import hashlib
import os
import sys

from agent_toolkit._common import delegated_session, session_state
from agent_toolkit._common import next_action as _next_action

SUCCESS_PREFIX = "成功: "
FAILURE_PREFIX = "失敗: "
WARNING_PREFIX = "警告: "
NO_MATCH_PREFIX = "該当0件: "


class ResultKind(enum.Enum):
    """結果行の出力先を決めるリーフサブコマンドの区分。"""

    STATE_CHANGE = "state-change"
    """標準出力が値・構造化データ・本文の表示を担わない状態変更報告型。成功行は標準出力へ書く。"""

    VALUE_OUTPUT = "value-output"
    """標準出力が値・構造化データの表示を担う値出力型。独立した成功行は書かない。"""


STATE_CHANGE_COMMANDS = frozenset(
    {
        "atk commit",
        "atk agents notify",
        "atk setup-project",
        "atk run-skill",
        "atk wi add",
        "atk wi check-alerts",
        "atk wi start-processing",
        "atk wi hold",
        "atk wi unhold",
        "atk wi return-to-inbox",
        "atk wi adopt",
        "atk wi reject",
        "atk wi rm",
        "atk wi edit",
        "atk wi set-dependencies",
        "atk wi answer",
        "atk wi commit",
        "atk wi pull",
        "atk wi process-loop abort",
        "atk wi process-loop abort-cancel",
        "atk wi process-loop instruct",
        "atk wi process-loop instruct-cancel",
        "atk wi submission begin",
        "atk wi submission end",
        "atk plans checkout",
        "atk plans commit",
        "atk config set",
        "atk config apply-preset",
        "atk review-table add",
        "atk review-table respond",
        "atk review-table validate",
    }
)
"""状態変更報告型のリーフサブコマンド。成功行を標準出力の1行目へ書く。"""

VALUE_OUTPUT_COMMANDS = frozenset(
    {
        "atk worktree-stash save",
        "atk worktree-stash drop",
        "atk review-table init",
        "atk review-audit mark",
        "atk agents-exit-session",
        "atk lane create",
        "atk lane delete",
        "atk lane integrate",
        "atk lane recover",
    }
)
"""値出力型のリーフサブコマンド。標準出力の値と構造化データを結果通知とする。"""

READ_ONLY_COMMANDS = frozenset(
    {
        "atk info",
        "atk wi list",
        "atk wi show",
        "atk wi grep",
        "atk wi process-loop status",
        "atk plans list",
        "atk config",
        "atk config show",
        "atk config get",
        "atk agents wait",
        "atk agents list",
        "atk agents logs",
        "atk agents show",
        "atk review-table show",
        "atk review-audit list",
        "atk review-audit pending",
        "atk wait-schedule",
    }
)
"""読み取り専用型のリーフサブコマンド。成功行を書かない。"""

OUT_OF_SCOPE_COMMANDS = frozenset({"atk wi process-loop", "atk serve", "atk run-script"})
"""結果行の規約の対象外。process-loop、サーバーおよび委譲先scriptが終了状態を表す。"""

NO_MATCH_COMMANDS = frozenset({"atk wi grep"})
"""該当0件で終了コード1を返し、該当0件の行を標準エラーへ書く読み取りのコマンド。"""


def force_utf8_stdio() -> None:
    """結果行を書く前に標準出力と標準エラーをUTF-8へ切り替える。

    接頭辞と本文が日本語のため、Windowsのcp932・cp1252環境では符号化を指定せずに送出すると失敗する。
    結果行を出力する処理はこの関数を呼んでから`report_*`を使う。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def report_success(message: str, kind: ResultKind = ResultKind.STATE_CHANGE) -> None:
    """状態変更の完了を1行で書く。値出力型は返された値を結果通知とする。"""
    if kind is ResultKind.STATE_CHANGE:
        print(f"{SUCCESS_PREFIX}{message}")


def report_failure(message: str, *, next_action: str) -> None:
    """非0で終了する理由を失敗行へ、次に行う操作を続く行へ標準エラーへ書く。

    受信側が理由だけから次の行動を推測せずに済むよう、次の操作を必須の引数とする。
    """
    print(_next_action.with_next_action(f"{FAILURE_PREFIX}{message}", next_action), file=sys.stderr)


def report_warning(message: str, *, next_action: str, to_stderr: bool = True) -> None:
    """警告行と次に行う操作の行を書く。出力先はその警告を発生させた処理の現在の出力先を呼び出し側が指定する。

    警告の出力条件はwriting-standardsのwriting.md「エージェントが読む出力」に従う。
    """
    print(
        _next_action.with_next_action(f"{WARNING_PREFIX}{message}", next_action),
        file=sys.stderr if to_stderr else sys.stdout,
    )


def claim_persistent_warning(message: str, *, session_id: str | None = None) -> bool:
    """同じ会話で同じ持続状態を初めて報告する場合だけ真を返す。"""
    session_id = session_id or next(
        (
            os.environ[key]
            for key in ("AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
            if os.environ.get(key)
        ),
        None,
    )
    if not session_id:
        return True
    fingerprint = hashlib.sha256(message.encode()).hexdigest()
    claimed = True

    def claim(current: dict) -> dict | None:
        nonlocal claimed
        fingerprints = current.setdefault("persistent_warning_fingerprints", [])
        if fingerprint in fingerprints:
            claimed = False
            return None
        fingerprints.append(fingerprint)
        claimed = True
        return current

    session_state.update_state(session_id, claim)
    return claimed


def report_persistent_warning(message: str, *, next_action: str) -> None:
    """共通起動の警告を、委譲先を除いて会話ごとに1回出力する。"""
    if not delegated_session.is_delegated(os.environ) and claim_persistent_warning(message):
        report_warning(message, next_action=next_action)


def report_no_match(message: str) -> None:
    """該当0件で終了コード1を返す読み取りのコマンドが正常に完了したことを標準エラーへ書く。"""
    print(f"{NO_MATCH_PREFIX}{message}", file=sys.stderr)
