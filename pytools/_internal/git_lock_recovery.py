"""標準ロックの競合で失敗した`git`の更新系コマンドを、待機と残存ロックの回収の後に再実行する。

`common`はPEP 723の単独スクリプトからも読み込まれ、`agent_toolkit`を解決できない場合があるため、
`agent_toolkit`に依存する本処理は`common`から分けている。
"""

import subprocess
from pathlib import Path

from agent_toolkit._git import stale_lock

from pytools._internal import common


def run_git_with_lock_recovery(
    cmd: list[str], *, repo: Path, tag: str | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess[str] | None:
    """`git`の更新系コマンドを実行し、標準ロックの競合で失敗した場合は待機と残存ロックの回収の後に1回だけ再実行する。

    Gitはロック作成に失敗した時点で変更を始めていないため、再実行は安全である。
    ロックが解消しなかった場合は、元のエラーに判定結果を添えた結果を返す。
    """
    result = common.run_subprocess(cmd, tag=tag, timeout=timeout)
    if result is None or result.returncode == 0 or ".lock" not in result.stderr:
        return result
    recovery = stale_lock.recover_after_failure(result.stderr, repo, wait_seconds=stale_lock.lock_wait_seconds(timeout))
    if recovery.retry:
        return common.run_subprocess(cmd, tag=tag, timeout=timeout)
    if recovery.diagnosis is None:
        return result
    guidance = "所有者が終了していることを確かめ、ロックを手動で削除してから再実行してください。"
    stderr = f"{result.stderr}\n{recovery.diagnosis.text(guidance)}"
    return subprocess.CompletedProcess(cmd, result.returncode, result.stdout, stderr)
