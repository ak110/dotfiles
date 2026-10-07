"""`atk wi process-loop`の待機中のmiseのツールの更新。"""

import pathlib
import subprocess

from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action

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


def refresh_mise_tools(dotfiles_root: pathlib.Path) -> bool:
    """dotfilesのlatest指定ツールをログインシェルを使わずに再評価し、失敗後も呼び出し元を継続させる。"""
    executable = _pl_env.resolve_executable("mise")
    if executable is None:
        return False
    try:
        result = subprocess.run(
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
