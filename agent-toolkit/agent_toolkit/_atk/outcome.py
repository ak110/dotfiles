"""atkの実行結果を表す行の接頭辞と出力先を決める共通処理。

`atk`の出力を受け取る主体が結果行の先頭の語だけで成否を確定し、終了コードの再確認と
状態の再取得を要さない状態にするため、成功・失敗・警告・該当0件の4種の接頭辞を本モジュールへ集約する。
成功行の出力先は、当該コマンドの標準出力が値、構造化データ又は本文の表示を担うかで決める。
担う場合は標準エラー、担わない場合は標準出力の1行目とする。
"""

from __future__ import annotations

import enum
import sys

SUCCESS_PREFIX = "成功: "
FAILURE_PREFIX = "失敗: "
WARNING_PREFIX = "警告: "
NO_MATCH_PREFIX = "該当0件: "


class ResultKind(enum.Enum):
    """結果行の出力先を決めるリーフサブコマンドの区分。"""

    STATE_CHANGE = "state-change"
    """標準出力が値・構造化データ・本文の表示を担わない状態変更報告型。成功行は標準出力へ書く。"""

    VALUE_OUTPUT = "value-output"
    """標準出力が値・構造化データの表示を担う値出力型。成功行は標準エラーへ書く。"""


STATE_CHANGE_COMMANDS = frozenset(
    {
        "atk wi add",
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
        "atk plans commit",
        "atk plans rewrite-references",
        "atk config set",
        "atk config apply-preset",
        "atk review-table add",
        "atk review-table respond",
        "atk review-table validate",
        "atk managed-temp cleanup",
    }
)
"""状態変更報告型のリーフサブコマンド。成功行を標準出力の1行目へ書く。"""

VALUE_OUTPUT_COMMANDS = frozenset(
    {
        "atk managed-temp create",
        "atk worktree-stash save",
        "atk worktree-stash drop",
        "atk review-table init",
        "atk review-audit mark",
        "atk agents notify",
        "atk agents-exit-session",
    }
)
"""値出力型のリーフサブコマンド。標準出力は値と構造化データのままとし、成功行を標準エラーへ書く。"""

READ_ONLY_COMMANDS = frozenset(
    {
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
        "atk agents show",
        "atk managed-temp list",
        "atk review-table show",
        "atk review-audit list",
        "atk wait-schedule",
        "atk watch",
    }
)
"""読み取り専用型のリーフサブコマンド。成功行を書かない。"""

OUT_OF_SCOPE_COMMANDS = frozenset({"atk wi process-loop", "atk serve"})
"""結果行の規約の対象外。常駐処理とサーバー起動は終了状態を結果行で表さない。"""

NO_MATCH_COMMANDS = frozenset({"atk wi grep", "atk managed-temp list"})
"""該当0件で終了コード1を返し、該当0件の行を標準エラーへ書く読み取り経路。"""


def force_utf8_stdio() -> None:
    """結果行を書く前に標準出力と標準エラーをUTF-8へ切り替える。

    接頭辞と本文が日本語のため、Windowsのcp932・cp1252環境では既定の符号化で送出に失敗する。
    結果行を書く入口はこの関数を呼んでから`report_*`を使う。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def report_success(message: str, kind: ResultKind = ResultKind.STATE_CHANGE) -> None:
    """完了した外部状態を区分に応じた出力先へ1行で書く。"""
    stream = sys.stderr if kind is ResultKind.VALUE_OUTPUT else sys.stdout
    print(f"{SUCCESS_PREFIX}{message}", file=stream)


def report_failure(message: str) -> None:
    """非0で終了する理由と次に行う操作を標準エラーへ書く。"""
    print(f"{FAILURE_PREFIX}{message}", file=sys.stderr)


def report_warning(message: str, *, to_stderr: bool = True) -> None:
    """警告を書く。出力先は当該警告が属する経路の現行の出力先を呼び出し側が指定する。"""
    print(f"{WARNING_PREFIX}{message}", file=sys.stderr if to_stderr else sys.stdout)


def report_no_match(message: str) -> None:
    """該当0件で終了コード1を返す読み取り経路の正常完了を標準エラーへ書く。"""
    print(f"{NO_MATCH_PREFIX}{message}", file=sys.stderr)
