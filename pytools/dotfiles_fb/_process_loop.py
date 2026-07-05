"""process-loopサブコマンド実装。"""

import argparse
import subprocess
import sys

from pytools.dotfiles_fb._repo import _resolve_local_worktree


def _cmd_process_loop(args: argparse.Namespace) -> None:
    """process-loopサブコマンド: claudeを単発起動しprocess-feedbacks-loopスキルへ反復制御を委譲する。

    反復判定・件数計算・完了時の自律終了はすべてスキル側（process-feedbacks-loop・exit-session）で
    処理する。CLI側はclaudeの起動と終了コード伝搬のみを担う。
    """
    local_path_str = str(_resolve_local_worktree(args.target_repo))
    print(f"claudeを起動しprocess-feedbacks-loopスキルへ委譲します（対象: {local_path_str}）")
    result = subprocess.run(
        ["claude", "--permission-mode=auto", "/process-feedbacks-loop", local_path_str],
        check=False,
    )
    if result.returncode != 0:
        print(
            f"claudeがexit code {result.returncode}で終了しました。",
            file=sys.stderr,
        )
        sys.exit(result.returncode)
