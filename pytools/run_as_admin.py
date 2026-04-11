"""コマンドを管理者権限で起動する (C# runAsAdmin の Python 移植)。

Windows 専用。UAC ダイアログ経由で昇格されたプロセスを `ShellExecuteW`
で起動する。非 Windows では起動直後にエラー終了する。
"""

import argparse
import logging
import sys

logger = logging.getLogger(__name__)


def _main() -> None:
    if sys.platform != "win32":
        print("run-as-admin は Windows 専用です", file=sys.stderr)
        sys.exit(1)
    parser = argparse.ArgumentParser(description="コマンドを管理者権限で起動する")
    parser.add_argument("--wait", action="store_true", help="子プロセスの終了を待機する")
    parser.add_argument("command", help="実行するコマンドの実行可能ファイル")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="コマンド引数")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_elevated(args.command, args.args, wait=args.wait)


def run_elevated(command: str, args: list[str], *, wait: bool = False) -> None:
    """ShellExecuteW("runas") で昇格起動する。"""
    import ctypes

    params = " ".join(f'"{a}"' if " " in a else a for a in args)
    SW_SHOWNORMAL = 1
    # ctypes.windll は Windows 限定のため、型チェックを抜けるよう getattr 経由で参照する
    shell32 = getattr(ctypes, "windll").shell32  # noqa: B009
    result = shell32.ShellExecuteW(None, "runas", command, params, None, SW_SHOWNORMAL)
    if int(result) <= 32:
        raise OSError(f"ShellExecuteW failed: code={int(result)}")
    if wait:
        logger.warning("--wait は未対応 (ShellExecuteW の HINSTANCE から HANDLE を取得できないため)")


if __name__ == "__main__":
    _main()
