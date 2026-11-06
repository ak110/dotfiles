"""`atk wi process-loop`の待機中のmiseのツールの更新。"""

import pathlib
import subprocess

from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import process_tree

# `latest`指定ツールを外部の登録簿に対して再評価する間隔と、導入処理の実行上限。
MISE_REFRESH_INTERVAL_SEC = 24 * 60 * 60


_MISE_INSTALL_TIMEOUT_SEC = 600


# dotfilesの作業ツリーの`mise.lock`を書き戻さないよう、プロジェクトのlockfileに対してlockedモードで導入する。
# global設定はlockにURLを持たないツールを含むため対象外とする。
_MISE_LOCKED_ENV = {"MISE_LOCKED": "1", "MISE_LOCKED_SCOPES": "project"}


def _mise_output_detail(output: str | bytes | None) -> str:
    """miseの標準出力または標準エラー出力を警告用の一行へ整形する。"""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="backslashreplace")
    return output.strip() if isinstance(output, str) and output.strip() else "出力なし"


_UNRECLAIMED: list[process_tree.ReclaimError] = []
"""前回の導入で停止を確認できなかった子プロセス。回収するまで次の導入を始めない。"""


def refresh_mise_tools(dotfiles_root: pathlib.Path) -> bool:
    """dotfilesのlatest指定ツールをログインシェルを使わずに再評価し、失敗後も呼び出し元を継続させる。

    導入が起動した子プロセスを回収できない場合は、同じ所有対象を保持して次の呼び出しで回収し直し、
    回収できるまで新しい導入を始めない。残った導入と新しい導入が同じツールの導入先を奪い合わないためである。
    """
    for pending in list(_UNRECLAIMED):
        if not pending.retry():
            _UNRECLAIMED.remove(pending)
    if _UNRECLAIMED:
        _report_unreclaimed(_UNRECLAIMED[0], dotfiles_root)
        return False
    executable = _pl_env.resolve_executable("mise")
    if executable is None:
        return False
    try:
        result = process_tree.run_with_timeout(
            [executable, "install", "--quiet"],
            cwd=dotfiles_root,
            env=_pl_env.child_env() | _MISE_LOCKED_ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_MISE_INSTALL_TIMEOUT_SEC,
        )
    except process_tree.ReclaimError as error:
        if error.retry():
            _UNRECLAIMED.append(error)
            _report_unreclaimed(error, dotfiles_root)
            return False
        _next_action.report(
            f"mise install --quietが{_MISE_INSTALL_TIMEOUT_SEC}秒でタイムアウトし、子プロセスは再試行で回収しました。"
            "process-loopを継続します。",
            next_action=f"対応不要（process-loopは継続した）。原因は`mise install`を{dotfiles_root}で手作業で実行して確認する",
        )
        return False
    except subprocess.TimeoutExpired as exc:
        detail = _mise_output_detail(exc.stderr or exc.stdout)
        _next_action.report(
            f"mise install --quietが{_MISE_INSTALL_TIMEOUT_SEC}秒でタイムアウトしました"
            f"（{detail}）。process-loopを継続します。",
            next_action=(
                f"対応不要（process-loopは継続した）。ツールの不足で子セッションが失敗する場合は`mise install`を"
                f"{dotfiles_root}で手作業で実行して原因を確認する"
            ),
        )
        return False
    finally:
        _console_title.set_console_title("atk wi process-loop")
    if result.returncode != 0:
        detail = _mise_output_detail(result.stderr or result.stdout)
        _next_action.report(
            f"mise install --quietに失敗しました（exit code {result.returncode}: {detail}）。process-loopを継続します。",
            next_action=(
                f"対応不要（process-loopは継続した）。ツールの不足で子セッションが失敗する場合は`mise install`を"
                f"{dotfiles_root}で手作業で実行して原因を確認する"
            ),
        )
        return False
    return True


def _report_unreclaimed(error: process_tree.ReclaimError, dotfiles_root: pathlib.Path) -> None:
    """回収できない導入の子プロセスと、次の導入を止めていることを伝える。"""
    if error.unresolved:
        # 生成の停止を確認できない回収不能は再試行で解消しないため、保持した所有対象を破棄する再起動で解除する。
        resume = (
            "「生成の停止を確認できない」は一覧に無い子孫が残り得ることを示し、再試行では解消しない。"
            "`ps -o pid,ppid,lstart,args -e`などで導入が起動した不要なプロセスも探して終了させ、"
            "`atk wi process-loop`を再起動すると導入を再開する。"
        )
    else:
        resume = "回収後は次の更新周期で導入を再開する。"
    _next_action.report(
        f"mise install --quietの子プロセスを回収できないため、次の導入を止めています: {error.residual}。",
        next_action=(
            f"表示されたPIDと起動コマンドを確認し、不要なら終了させる（POSIXは`kill -KILL <PID>`）。{resume}"
            f"原因は`mise install`を{dotfiles_root}で手作業で実行して確認する"
        ),
    )
