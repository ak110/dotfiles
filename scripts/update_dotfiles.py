#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["filelock>=3.30", "platformdirs>=4.0"]
# ///
r"""dotfilesリポジトリを最新化するPEP 723スクリプト。

`chezmoi git pull --rebase` → `chezmoi init`（テンプレート再展開） →
`chezmoi status`（apply予定ファイルの表示） → `chezmoi apply`の4段を、
プロセス間排他ロック下で直列実行する。
`--force`指定時はapply直前に`chezmoi diff --no-pager`を追加し、差分表示後に
`chezmoi apply --force`で確認入力を待たずに反映する。

複数の`update-dotfiles`起動（`atk mq process-loop`の複数常駐・手動実行との重複等）が
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
Gitが進捗を標準エラー出力へ書く場合も、Git更新段が正常終了した場合は
`update-dotfiles`の標準出力へ転送する。失敗時はGitの標準エラー出力を維持する。
各段のサブプロセスへ`MISE_AUTO_INSTALL=0`を渡し、miseのshimが呼び出したコマンドと
無関係なツールを自動導入して更新処理を停止させる経路を抑止する。
取得したchezmoi出力は、プラットフォームの既定値に依存せずUTF-8として厳格に復号する。
"""

import argparse
import os
import pathlib
import subprocess
import sys

import filelock
import platformdirs

_DOTFILES_ROOT = pathlib.Path(__file__).resolve().parent.parent
_LOCK_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "locks" / "update-dotfiles.lock"
_LOCK_TIMEOUT_SEC = 600.0


def _child_env() -> dict[str, str]:
    """各工程のサブプロセスへ渡す環境を構成する。

    `MISE_AUTO_INSTALL=0`は、実行ファイル名で起動したコマンドがmiseのshimへ解決された場合に、
    呼び出したコマンドと無関係なツールの自動導入が実行されるのを防ぐ。当該導入が失敗すると
    shimが非ゼロ終了し、更新処理が最初の工程で止まる。
    post-apply工程が実行する明示的な`mise install`は当該設定の影響を受けないため、
    ツールの導入自体は従来どおり行われる。
    """
    env = os.environ.copy()
    env["MISE_AUTO_INSTALL"] = "0"
    return env


def _run_step(step_no: int, total: int, title: str, argv: list[str], *, capture: bool = False) -> tuple[int, str]:
    """1段を実行し見出しを表示する。`capture=True`時のみ標準出力を文字列で返す。"""
    print(f"=== [{step_no}/{total}] {title} ===")
    result = subprocess.run(
        argv,
        cwd=_DOTFILES_ROOT,
        check=False,
        capture_output=capture,
        encoding="utf-8" if capture else None,
        env=_child_env(),
    )
    if capture and result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode, (result.stdout if capture else "")


def _run_git_pull(step_no: int, total: int) -> int:
    """Git更新段を実行し、正常終了時の出力を標準出力へ正規化する。"""
    print(f"=== [{step_no}/{total}] git pull ===")
    result = subprocess.run(
        ["chezmoi", "git", f"--source={_DOTFILES_ROOT}", "--", "pull", "--rebase", "--quiet"],
        cwd=_DOTFILES_ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        env=_child_env(),
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        stream = sys.stdout if result.returncode == 0 else sys.stderr
        stream.write(result.stderr)
    return result.returncode


def _filter_apply_pending(status_output: str) -> list[str]:
    """`chezmoi status`出力から2列目（apply予定を表す列）が空白以外の行のみ抽出する。

    `chezmoi status`は2列構成。1列目=前回chezmoiが書いた状態vs現在のdestination実ファイル、
    2列目=現在の実ファイルvsターゲット状態（＝これからapplyで起きる変更）。
    2文字未満の行はスライスが空文字列を返すため常に対象外とする。
    """
    return [line for line in status_output.splitlines() if len(line) > 1 and line[1] != " "]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(description="dotfilesを取得し、chezmoiで反映する")
    parser.add_argument(
        "--force",
        action="store_true",
        help="適用前の差分を表示し、destination側の変更を確認なしで上書きする",
    )
    return parser.parse_args([] if argv is None else argv)


def main(argv: list[str] | None = None) -> int:
    """更新処理を排他ロック下で直列実行し、最終exit codeを返す。"""
    args = _parse_args(argv)
    total = 5 if args.force else 4
    lock_dir = _LOCK_PATH.parent
    lock_dir.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.FileLock(str(_LOCK_PATH), timeout=_LOCK_TIMEOUT_SEC):
            returncode = _run_git_pull(1, total)
            if returncode != 0:
                return returncode

            returncode, _ = _run_step(
                2,
                total,
                "chezmoi init (テンプレート再展開)",
                ["chezmoi", "init", f"--source={_DOTFILES_ROOT}"],
            )
            if returncode != 0:
                return returncode

            returncode, status_output = _run_step(
                3,
                total,
                "chezmoi status (apply予定のファイル)",
                ["chezmoi", "status", "-x", "scripts"],
                capture=True,
            )
            if returncode != 0:
                return returncode
            for line in _filter_apply_pending(status_output):
                print(line)

            if args.force:
                returncode, diff_output = _run_step(
                    4,
                    total,
                    "chezmoi diff (上書き前の差分)",
                    ["chezmoi", "diff", "--no-pager"],
                    capture=True,
                )
                if diff_output:
                    sys.stdout.write(diff_output)
                if returncode != 0:
                    return returncode

            apply_argv = ["chezmoi", "apply", "--force"] if args.force else ["chezmoi", "apply"]
            returncode, _ = _run_step(total, total, "chezmoi apply (post-apply実行)", apply_argv)
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
    raise SystemExit(main(sys.argv[1:]))
