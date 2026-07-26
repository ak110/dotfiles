#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["filelock>=3.30", "platformdirs>=4.0"]
# ///
r"""dotfilesリポジトリを最新化するPEP 723スクリプト。

`chezmoi git pull --rebase` → `chezmoi init`（テンプレート再展開） →
`chezmoi status`（apply予定ファイルの表示） → `chezmoi apply`の4段を、
プロセス間排他ロック下で直列実行する。

複数の`update-dotfiles`起動（`atk fb process-loop`の複数常駐・手動実行との重複等）が
同時に`git pull`・`chezmoi apply`を実行するとpullとファイル操作の競合を招くため、
`filelock`でプロセス間直列化する。ロック取得に失敗（タイムアウト）した場合は
exit code 1で終了し、他プロセスの完了を待って再実行するよう促す。

薄いランチャー`bin/update-dotfiles`・`bin/update-dotfiles.cmd`から
`uv run --no-project --script`形式で起動される。dotfilesルートは本ファイルの配置
（`scripts/update_dotfiles.py`）から`Path(__file__)`起点で解決する
（`Path.home()`起点は`$HOME`と実チェックアウト先の不一致を招くため使わない）。

各段の失敗はexit codeをそのまま伝播し以降の段を実行しない。`chezmoi status`段
（表示専用）も含め全段をfail-fast対象とし、既存bash実装が`set -euo pipefail`で
持っていた「いずれかの段が失敗すれば中断する」挙動をそのまま踏襲する。
"""

import pathlib
import subprocess
import sys

import filelock
import platformdirs

_DOTFILES_ROOT = pathlib.Path(__file__).resolve().parent.parent
_LOCK_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "locks" / "update-dotfiles.lock"
_LOCK_TIMEOUT_SEC = 600.0


def _run_step(step_no: int, total: int, title: str, argv: list[str], *, capture: bool = False) -> tuple[int, str]:
    """1段を実行し見出しを表示する。`capture=True`時のみ標準出力を文字列で返す。"""
    print(f"=== [{step_no}/{total}] {title} ===")
    result = subprocess.run(argv, cwd=_DOTFILES_ROOT, check=False, capture_output=capture, text=capture)
    if capture and result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode, (result.stdout if capture else "")


def _filter_apply_pending(status_output: str) -> list[str]:
    """`chezmoi status`出力から2列目（apply予定を表す列）が空白以外の行のみ抽出する。

    `chezmoi status`は2列構成。1列目=前回chezmoiが書いた状態vs現在のdestination実ファイル、
    2列目=現在の実ファイルvsターゲット状態（＝これからapplyで起きる変更）。
    2文字未満の行はスライスが空文字列を返すため常に対象外とする。
    """
    return [line for line in status_output.splitlines() if len(line) > 1 and line[1] != " "]


def main() -> int:
    """4段を排他ロック下で直列実行し、最終exit codeを返す。"""
    lock_dir = _LOCK_PATH.parent
    lock_dir.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.FileLock(str(_LOCK_PATH), timeout=_LOCK_TIMEOUT_SEC):
            returncode, _ = _run_step(
                1,
                4,
                "git pull",
                ["chezmoi", "git", f"--source={_DOTFILES_ROOT}", "--", "pull", "--rebase"],
            )
            if returncode != 0:
                return returncode

            returncode, _ = _run_step(
                2,
                4,
                "chezmoi init (テンプレート再展開)",
                ["chezmoi", "init", f"--source={_DOTFILES_ROOT}"],
            )
            if returncode != 0:
                return returncode

            returncode, status_output = _run_step(
                3,
                4,
                "chezmoi status (apply予定のファイル)",
                ["chezmoi", "status", "-x", "scripts"],
                capture=True,
            )
            if returncode != 0:
                return returncode
            for line in _filter_apply_pending(status_output):
                print(line)

            returncode, _ = _run_step(4, 4, "chezmoi apply (post-apply実行)", ["chezmoi", "apply"])
            if returncode != 0:
                return returncode
    except filelock.Timeout:
        print(
            f"ロック取得に失敗しました（{_LOCK_TIMEOUT_SEC:.0f}秒待機後もタイムアウト）。"
            "他のupdate-dotfiles実行の完了を待って再実行してください。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
