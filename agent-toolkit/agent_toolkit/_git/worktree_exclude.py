"""対象リポジトリの`.claude/worktrees/`をGitの状態へ未追跡として現さないための除外設定。

`atk wi process-loop`の親worktreeと`atk lane create`の固定配置のレーンworktreeは、どちらも対象リポジトリの
`.claude/worktrees/`配下へ作成する。配置先と`info/exclude`への追記規則をここだけに置き、
失敗時の扱い（警告して起動を止めるか、例外として返すか）は呼び出し元が決める。
"""

import pathlib
import subprocess

from agent_toolkit._git import command as _command

WORKTREE_PARENT = pathlib.PurePosixPath(".claude/worktrees")
IGNORE_PATTERN = "/.claude/worktrees/"
_TIMEOUT = 120


class ExcludeError(Exception):
    """除外を確かめられなかった段階と対象のパスを持つ失敗。

    `stage`は`check`（除外判定の失敗）、`path`（`info/exclude`のパスを解決できない）、
    `write`（`info/exclude`へ書き込めない）、`verify`（追記後も除外を確認できない）のいずれかとする。
    """

    def __init__(self, stage: str, path: pathlib.Path, detail: str = "") -> None:
        super().__init__(f"{stage}: {path}" + (f" ({detail})" if detail else ""))
        self.stage = stage
        self.path = path
        self.detail = detail


def _check_ignored(repo: pathlib.Path) -> subprocess.CompletedProcess[str]:
    args = ["check-ignore", "-q", f"{WORKTREE_PARENT}/"]
    try:
        return _command.run(args, repo, capture_output=True, text=True, check=False, timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(_command.command_line(args), returncode=127, stdout="", stderr=str(error))


def ensure_excluded(repo: pathlib.Path) -> None:
    """`repo`で`.claude/worktrees/`が除外されていなければ`info/exclude`へ追記し、除外を確かめる。

    Raises:
        ExcludeError: 除外を判定・追記・確認できなかった場合。
    """
    check = _check_ignored(repo)
    if check.returncode == 0:
        return
    if check.returncode != 1:
        raise ExcludeError("check", repo, check.stderr.strip())
    try:
        path_text = _command.optional_output(
            ["rev-parse", "--path-format=absolute", "--git-path", "info/exclude"], repo, timeout=_TIMEOUT
        )
    except OSError as error:
        raise ExcludeError("path", repo, str(error)) from error
    if not path_text:
        raise ExcludeError("path", repo)
    exclude = pathlib.Path(path_text)
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if IGNORE_PATTERN not in existing.splitlines():
            exclude.parent.mkdir(parents=True, exist_ok=True)
            prefix = "" if not existing or existing.endswith(("\n", "\r")) else "\n"
            with exclude.open("a", encoding="utf-8") as stream:
                stream.write(f"{prefix}{IGNORE_PATTERN}\n")
    except (OSError, UnicodeError) as error:
        raise ExcludeError("write", exclude, str(error)) from error
    if _check_ignored(repo).returncode != 0:
        raise ExcludeError("verify", repo)
