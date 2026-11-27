from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys
from typing import Protocol

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.next_action import recover_from_failure
from agent_toolkit._git import command as _git_command

_REPORTED_ATTRIBUTE = "_atk_sync_failure_reported"
"""失敗の原因と次の操作を本モジュールが出力済みであることを例外へ記録する属性名。"""

GIT_TIMEOUT_NEXT_ACTION = "リモートへの接続（ネットワーク、VPN、認証）を確かめてから、失敗した操作を再実行する"
"""gitの時間切れの失敗行に続けて案内する次の操作。"""


class GitResultRunner(Protocol):
    def __call__(self, args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]: ...


class RebaseInProgressError(RuntimeError):
    def __init__(self, reason: str, private_notes: pathlib.Path) -> None:
        super().__init__(reason)
        abort = _recovery_command(private_notes, "-c", "submodule.recurse=false", "rebase", "--abort")
        self.next_action = (
            f"現在の競合を解消して{_recovery_rebase_commands(private_notes)}の順に実行するか、"
            f"中止する場合は{abort}を実行し、元の`atk`操作を再実行してください。"
        )


class GitSyncError(RuntimeError):
    """Git同期の前提を満たせない。`str()`は理由だけを返し、`next_action`に次の操作を持つ。"""

    def __init__(self, reason: str, *, next_action: str) -> None:
        _next_action.next_action_line(next_action)
        super().__init__(reason)
        self.reason = reason
        self.next_action = next_action


def git_timeout_reason(error: subprocess.TimeoutExpired) -> str:
    command = " ".join(error.cmd) if isinstance(error.cmd, list | tuple) else str(error.cmd)
    return (
        f"gitが時間上限の{error.timeout:.0f}秒に達したため、子プロセスまで止めて打ち切った: `{command}`"
        f"（作業ディレクトリ: {_git_command.timeout_cwd(error)}）"
    )


def mark_reported(error: subprocess.SubprocessError) -> None:
    """失敗の原因と次の操作を出力済みであることを例外へ記録する。"""
    setattr(error, _REPORTED_ATTRIBUTE, True)


def is_reported(error: subprocess.SubprocessError) -> bool:
    """本モジュールが失敗の原因と次の操作を出力済みの例外か返す。

    呼び出し元のCLIは出力済みの失敗へ汎用の失敗行を重ねず、原因別の失敗行を1本に保つ。
    """
    return bool(getattr(error, _REPORTED_ATTRIBUTE, False))


def divergence_details(
    private_notes: pathlib.Path,
    *,
    result_runner: GitResultRunner,
) -> str:
    count_text = "取得できなかった"
    try:
        counts = result_runner(["rev-list", "--left-right", "--count", "HEAD...@{u}"], private_notes)
        if counts.returncode == 0:
            fields = counts.stdout.split()
            if len(fields) == 2:
                count_text = f"ローカルのみ{fields[0]}件、upstreamのみ{fields[1]}件"
    except OSError, subprocess.SubprocessError:
        pass
    reason_lines = [
        f"Git履歴が分岐している（{count_text}）: {private_notes}。"
        "ローカルの未push commitを残した間に、別のcloneからupstreamが更新された状態である",
    ]
    try:
        differences = result_runner(["diff", "--name-status", "HEAD", "@{u}"], private_notes)
        if differences.returncode == 0 and differences.stdout.strip():
            reason_lines.append("内容差のあるファイル:")
            reason_lines.append(differences.stdout.rstrip())
    except OSError, subprocess.SubprocessError:
        pass
    return "\n".join(reason_lines)


def _recovery_command(private_notes: pathlib.Path, *args: str) -> str:
    if sys.platform == "win32":
        path = str(private_notes).replace("'", "''")
        command = " ".join(f"'{arg}'" if any(char in arg for char in "@<>") else arg for arg in args)
        return f"`git -C '{path}' {command}`"
    return f"`{shlex.join(['git', '-C', str(private_notes), *args])}`"


def report_divergence(
    private_notes: pathlib.Path,
    *,
    result_runner: GitResultRunner,
    dirty: bool = False,
) -> None:
    """分岐による失敗を、未コミット差分の解消順に合わせて案内する。"""
    preparation = (
        f"{_recovery_command(private_notes, 'status')}で無関係な差分を確認し、"
        "作業ツリーをcleanにしてから元の操作を再実行してください。"
        "cleanにしても自動解消できない場合は、"
        if dirty
        else ""
    )
    _outcome.report_failure(
        divergence_details(private_notes, result_runner=result_runner),
        next_action=preparation
        + (
            f"{_recovery_command(private_notes, 'log', '--left-right', '--oneline', 'HEAD...@{u}')}で分岐を確認し、"
            f"{_recovery_command(private_notes, '-c', 'submodule.recurse=false', 'rebase', '@{u}')}を実行してください。"
            f"競合したら解消して{_recovery_rebase_commands(private_notes)}の順に実行してください。\n"
            "WIの回答・コメントが競合したら、現行の本文と状態・処理結果を一緒に確認し、適用できない旧結果は採らないでください。\n"
            "同じ変更がupstreamに存在する重複commitなら、内容を確認して"
            f"{_recovery_command(private_notes, '-c', 'submodule.recurse=false', 'rebase', '--skip')}を実行できます。"
            f"中止する場合は{_recovery_command(private_notes, '-c', 'submodule.recurse=false', 'rebase', '--abort')}"
            "を実行してください。"
        ),
    )


def _recovery_rebase_commands(private_notes: pathlib.Path) -> str:
    return "、".join(
        (
            _recovery_command(private_notes, "add", "--", "<競合解消済みパス>"),
            _recovery_command(private_notes, "-c", "submodule.recurse=false", "rebase", "--continue"),
            _recovery_command(private_notes, "push"),
        )
    )


def report_rebase_failure(private_notes: pathlib.Path, *, result_runner: GitResultRunner) -> None:
    """rebase失敗時に状態保持と復旧手順を表示する。"""
    try:
        conflicts = result_runner(
            ["diff", "--name-only", "--diff-filter=U", "-z"],
            private_notes,
        )
        names = [name for name in conflicts.stdout.split("\0") if name]
    except OSError, subprocess.SubprocessError:
        names = []
    _outcome.report_failure(
        "rebaseに失敗したため、rebase状態を保持した。自動abortは行っていない\n"
        "競合ファイル: " + ("、".join(names) if names else "取得できなかった"),
        next_action=(
            f"競合を解消した後、{_recovery_rebase_commands(private_notes)}の順に実行してください。\n"
            "WIの回答・コメントが競合したら、現行の本文と状態・処理結果を一緒に確認し、適用できない旧結果は採らないでください。\n"
            "同じ変更がupstreamへ反映済みの重複commitなら、内容を確認して"
            f"{_recovery_command(private_notes, '-c', 'submodule.recurse=false', 'rebase', '--skip')}を実行できます。"
            f"中止する場合は{_recovery_command(private_notes, '-c', 'submodule.recurse=false', 'rebase', '--abort')}"
            "を実行してください。"
        ),
    )


_AUTH_FAILURE_MARKERS = (
    "Authentication failed",
    "Permission denied",
    "could not read Username",
    "could not read Password",
    "The requested URL returned error: 401",
    "The requested URL returned error: 403",
)
"""gitの出力のうち認証の失敗を示す語句。"""

_NETWORK_FAILURE_MARKERS = (
    "Could not resolve host",
    "Could not resolve hostname",
    "Connection timed out",
    "Connection refused",
    "Network is unreachable",
    "Operation timed out",
    "unable to access",
)
"""gitの出力のうちネットワーク接続の失敗を示す語句。"""

_NON_FAST_FORWARD_MARKERS = ("non-fast-forward", "[rejected]", "fetch first", "Not possible to fast-forward")
"""gitの出力のうちupstreamがローカルより進んでいることを示す語句。"""


def _sync_failure_next_action(resolved: pathlib.Path, error: subprocess.CalledProcessError) -> tuple[str, str]:
    """gitの出力から失敗の原因を分類し、原因の説明と次の操作を返す。"""
    outputs = [output for output in (error.output, error.stderr) if isinstance(output, str)]
    text = "\n".join(outputs)
    retry = "解消後、失敗した`atk`操作を再実行すると同期を完了できる"
    if any(marker in text for marker in _AUTH_FAILURE_MARKERS):
        return (
            "remoteの認証に失敗した",
            f"`git -C {resolved} remote -v`で接続先を確認し、認証情報（SSH鍵または資格情報）を設定する。{retry}。"
            "認証情報を設定できない場合はユーザーへ報告する",
        )
    if any(marker in text for marker in _NETWORK_FAILURE_MARKERS):
        return (
            "remoteへ接続できなかった",
            recover_from_failure(
                component="Gitの接続",
                evidence="remoteの診断と設定",
                first_step=f"ネットワーク接続と `git -C {resolved} remote -v` の接続先を確認し、誤設定なら認可内で直す",
                retry="接続を回復し、先行操作の未適用を確認した場合だけ再実行する",
            ),
        )
    if any(marker in text for marker in _NON_FAST_FORWARD_MARKERS):
        return (
            "upstreamにローカルへ無いcommitがある",
            f"`git -C {resolved} status`と`git -C {resolved} log --left-right --oneline HEAD...@{{u}}`で差分を確認し、"
            f"`atk wi commit`でロック下の取込と送信を行ってから失敗した`atk`操作を再実行する",
        )
    return (
        "直前のgitの出力が失敗理由である。認証、ネットワーク接続、remoteの状態のいずれかを解消する",
        f"確認: `git -C {resolved} status`\n{retry}",
    )


def report_sync_failure(
    private_notes: pathlib.Path,
    operation: str,
    error: subprocess.CalledProcessError,
) -> None:
    """pullまたはpush失敗後に原因別の確認と再実行の手順を表示する。"""
    resolved = private_notes.resolve()
    cause, next_action = _sync_failure_next_action(resolved, error)
    _outcome.report_failure(f"private-notesの{operation}に失敗した: {resolved}。{cause}", next_action=next_action)


def report_sync_timeout(private_notes: pathlib.Path, operation: str, error: subprocess.TimeoutExpired) -> None:
    """pullまたはpushのgitが時間切れになった場合に、同期の失敗として1本の失敗行を出力する。"""
    if is_reported(error):
        return
    _outcome.report_failure(
        f"private-notesの{operation}に失敗した: {private_notes.resolve()}。{git_timeout_reason(error)}",
        next_action=GIT_TIMEOUT_NEXT_ACTION,
    )
    mark_reported(error)
