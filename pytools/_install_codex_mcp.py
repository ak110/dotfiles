"""codex MCPサーバーを user scope に自動登録する。

`chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
`claude mcp add --scope=user codex codex mcp-server` を冪等に実行する。

前提条件を満たさない場合は完全にスキップする
(dotfiles apply 全体を失敗させないための安全側動作)。

前提条件:

1. `claude` CLI が PATH にある

冪等動作:

- `claude mcp list` の出力に `codex:` 行があれば既登録としてスキップ
- 無ければ `claude mcp add --scope user codex codex mcp-server` で登録
"""

import logging
import shutil
import subprocess

from pytools import _log_format

logger = logging.getLogger(__name__)

# `claude mcp` コマンドのタイムアウト (秒)
_CLAUDE_TIMEOUT = 60

_CODEX_NAME = "codex"
_CODEX_COMMAND = "codex"
_CODEX_ARGS = ("mcp-server",)


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    logging.basicConfig(format="%(message)s", level="INFO")
    run()


def run() -> bool:
    """Codex MCPサーバーを user scope に登録する。

    Returns:
        新たに登録した場合 True。既登録・CLI不在などでスキップした場合 False。
    """
    if shutil.which("claude") is None:
        logger.info(_log_format.format_status("codex-mcp", "claude CLI 未検出のためスキップ"))
        return False

    if _is_codex_registered():
        logger.info(_log_format.format_status("codex-mcp", "登録済み"))
        return False

    args = ["mcp", "add", "--scope", "user", _CODEX_NAME, _CODEX_COMMAND, *_CODEX_ARGS]
    result = _run_claude(args)
    if result is None or result.returncode != 0:
        # タイムアウトで list が失敗 → 未登録と誤判定 → add が "already exists" で
        # 失敗するケースがある。この場合は実際には登録済みなので成功扱いにする
        stderr = result.stderr.strip() if result else ""
        if result is not None and "already exists" in result.stderr:
            logger.info(_log_format.format_status("codex-mcp", "登録済み (add が already exists を返却)"))
            return False
        logger.info(_log_format.format_status("codex-mcp", f"登録に失敗 (続行): {stderr}"))
        return False
    logger.info(_log_format.format_status("codex-mcp", "user scope に登録しました"))
    return True


def _is_codex_registered() -> bool:
    """`claude mcp list` の出力に codex サーバーが含まれているか判定する。"""
    result = _run_claude(["mcp", "list"])
    if result is None or result.returncode != 0:
        # list が失敗した場合は未登録扱いにし、後続の add 試行で改めて判定する
        # (add は登録済みの場合に非ゼロ終了するため冪等性が保たれる)
        return False
    # 出力の各行は `<name>: <command/url> - <status>` 形式。先頭の name が codex かで判定する
    return any(line.strip().startswith(f"{_CODEX_NAME}:") for line in result.stdout.splitlines())


def _run_claude(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """`claude` CLI を呼び出す共通ヘルパー。

    タイムアウト・例外・非ゼロ終了を全て吸収して呼び出し元に返す。
    """
    try:
        return subprocess.run(
            ["claude", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_CLAUDE_TIMEOUT,
            # Windows では text=True のデフォルトが cp932 になるため UTF-8 を明示する
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(_log_format.format_status("codex-mcp", f"`claude {' '.join(args)}` 実行に失敗: {e}"))
        return None


if __name__ == "__main__":
    _main()
