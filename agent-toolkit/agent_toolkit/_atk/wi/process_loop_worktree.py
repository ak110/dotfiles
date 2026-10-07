"""`atk wi process-loop`がセッションを起動する作業ツリーの準備と上流との同期。"""

import pathlib
import subprocess

from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._git import command as _git_command

# `--worktree`指定時またはgithub.com/ak110/dotfiles編集時は、影響範囲の大きい作業ツリー直接編集を避けるため
# git worktreeを作成してセッションのcwdにする。worktree名は反復ごとに固定値とし、常駐ループの再起動を経ても
# 同一worktreeを継続利用させる。
DOTFILES_REPO_ID = "github.com/ak110/dotfiles"


_DEFAULT_WORKTREE_NAME = "process-loop"


# process-loopが作成するworktreeの配置先（対象リポジトリのroot相対）。
_WORKTREE_PARENT_REL = pathlib.PurePosixPath(".claude/worktrees")


_WORKTREE_IGNORE_PATTERN = "/.claude/worktrees/"


def _git_output(args: list[str], cwd: pathlib.Path) -> str:
    """gitコマンドの標準出力を返す。失敗時は空文字を返す。"""
    try:
        return _git_command.output(args, cwd)
    except (OSError, subprocess.CalledProcessError):
        return ""
    finally:
        _console_title.set_console_title("atk wi process-loop")


def _worktree_is_clean(worktree_path: pathlib.Path) -> bool:
    """index・追跡済み差分・未追跡ファイルが全て空か判定する。"""
    checks = (
        ["diff", "--quiet"],
        ["diff", "--cached", "--quiet"],
    )
    if any(_git_command.run(command, worktree_path, check=False).returncode != 0 for command in checks):
        return False
    untracked = _git_command.run(
        ["ls-files", "--others", "--exclude-standard"], worktree_path, capture_output=True, text=True, check=False
    )
    return untracked.returncode == 0 and not untracked.stdout.strip()


def _run_worktree_git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """worktree準備用のgitコマンドを実行し、コンソールタイトルを復元する。"""
    try:
        result = _git_command.run(args, cwd, capture_output=True, text=True, check=False)
    except OSError as error:
        result = subprocess.CompletedProcess(_git_command.command_line(args), returncode=127, stdout="", stderr=str(error))
    finally:
        _console_title.set_console_title("atk wi process-loop")
    return result


def _resolve_git_path(output: str, cwd: pathlib.Path) -> pathlib.Path | None:
    """Gitのパス出力をコマンド実行時のcwd基準で絶対化する。"""
    if not output:
        return None
    path = pathlib.Path(output)
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _worktree_status_next_action(path: pathlib.Path) -> str:
    """worktree準備の失敗で、状態の確認から始める次の操作を返す。"""
    return (
        f"`git -C {path} status`と`git -C {path} worktree list`で状態を確認して原因を解消する。"
        "解消後はprocess-loopが次の反復で再試行する"
    )


def _fetch_failure_next_action(path: pathlib.Path, remote: str) -> str:
    """worktree準備のfetchが失敗したときの次の操作を返す。"""
    return (
        f"`git -C {path} fetch {remote}`を手作業で実行して認証とネットワークを確認する。"
        "解消後はprocess-loopが次の反復で再試行する"
    )


def _worktree_dirty_next_action(path: pathlib.Path) -> str:
    """worktreeに未コミット変更があるときの次の操作を返す。"""
    return f"`git -C {path} status`で未コミット変更を確認し、commitするか退避する。解消後はprocess-loopが次の反復で再試行する"


def _warn_worktree_preparation_failure(message: str, path: pathlib.Path, *, next_action: str) -> None:
    """worktree準備を停止する警告を共通形式で出力する。"""
    _next_action.report(f"{message}ため実装セッションを起動しません: {path}", next_action=next_action)


def _ensure_worktree_excluded(local_path: pathlib.Path) -> bool:
    """worktree配置先の除外を確認し、必要な場合だけ`info/exclude`へ追加する。"""
    check = _run_worktree_git(["check-ignore", "-q", f"{_WORKTREE_PARENT_REL}/"], local_path)
    if check.returncode == 0:
        return True
    if check.returncode != 1:
        _warn_worktree_preparation_failure(
            "worktree配置先の除外判定に失敗した", local_path, next_action=_worktree_status_next_action(local_path)
        )
        return False

    exclude_output = _git_output(["rev-parse", "--git-path", "info/exclude"], cwd=local_path)
    exclude_path = _resolve_git_path(exclude_output, local_path)
    if exclude_path is None:
        _warn_worktree_preparation_failure(
            "Gitの除外設定のパスを解決できなかった", local_path, next_action=_worktree_status_next_action(local_path)
        )
        return False
    try:
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        if _WORKTREE_IGNORE_PATTERN not in existing.splitlines():
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            prefix = "" if not existing or existing.endswith(("\n", "\r")) else "\n"
            with exclude_path.open("a", encoding="utf-8") as exclude_file:
                exclude_file.write(f"{prefix}{_WORKTREE_IGNORE_PATTERN}\n")
    except (OSError, UnicodeError):
        _warn_worktree_preparation_failure(
            "Gitの除外設定を更新できなかった",
            exclude_path,
            next_action=f"{exclude_path}の書き込み権限を確認するか、`{_WORKTREE_IGNORE_PATTERN}`の行を手作業で追記する",
        )
        return False

    check = _run_worktree_git(["check-ignore", "-q", f"{_WORKTREE_PARENT_REL}/"], local_path)
    if check.returncode != 0:
        _warn_worktree_preparation_failure(
            "worktree配置先の除外を確認できなかった", local_path, next_action=_worktree_status_next_action(local_path)
        )
        return False
    return True


def _validate_existing_worktree(local_path: pathlib.Path, worktree_path: pathlib.Path, branch: str) -> bool:
    """既存worktreeが対象リポジトリの専用worktreeであることを検証する。"""
    if not worktree_path.is_dir():
        _warn_worktree_preparation_failure(
            "worktreeの配置先がディレクトリではない", worktree_path, next_action=_worktree_status_next_action(worktree_path)
        )
        return False
    try:
        resolved_worktree_path = worktree_path.resolve()
    except (OSError, RuntimeError):
        _warn_worktree_preparation_failure(
            "既存worktreeの実体パスを解決できない", worktree_path, next_action=_worktree_status_next_action(worktree_path)
        )
        return False

    worktree_common = _git_output(["rev-parse", "--git-common-dir"], cwd=worktree_path)
    local_common = _git_output(["rev-parse", "--git-common-dir"], cwd=local_path)
    worktree_top = _git_output(["rev-parse", "--show-toplevel"], cwd=worktree_path)
    current_branch = _git_output(["symbolic-ref", "--short", "HEAD"], cwd=worktree_path)
    if not all((worktree_common, local_common, worktree_top, current_branch)):
        _warn_worktree_preparation_failure(
            "既存worktreeのGit照会が失敗した", worktree_path, next_action=_worktree_status_next_action(worktree_path)
        )
        return False

    resolved_worktree_common = _resolve_git_path(worktree_common, worktree_path)
    resolved_local_common = _resolve_git_path(local_common, local_path)
    resolved_worktree_top = _resolve_git_path(worktree_top, worktree_path)
    if (
        resolved_worktree_common is None
        or resolved_local_common is None
        or resolved_worktree_top is None
        or resolved_worktree_common != resolved_local_common
        or resolved_worktree_top != resolved_worktree_path
        or current_branch != branch
    ):
        _warn_worktree_preparation_failure(
            "既存worktreeのGit検証条件が成立しなかった", worktree_path, next_action=_worktree_status_next_action(worktree_path)
        )
        return False
    if not _worktree_is_clean(worktree_path):
        _warn_worktree_preparation_failure(
            "worktreeに未コミット変更がある", worktree_path, next_action=_worktree_dirty_next_action(worktree_path)
        )
        return False
    return True


def _sync_worktree_with_upstream(local_path: pathlib.Path, worktree_name: str) -> pathlib.Path | None:
    """worktreeを準備して対象リポジトリの上流最新へ追随させる。

    上流は現在ブランチの追跡先を優先し、利用不能な場合だけ`refs/remotes/origin/HEAD`へ後退する。
    解決結果はworktreeのfetch・作成・rebaseだけに用い、公開先を最初のプロンプトへ暗黙に設定しない。

    worktree名は反復間で固定のため、前回反復のworktreeがそのまま再利用される。
    前回反復の成果がpush済みでも、その後に他の作業ツリーが上流へ進めた分は
    worktreeのブランチへ入らない。追随を経ないまま次の反復が始まると、
    上流に既にある変更を未実装と誤認して同一内容を二重に実装し、履歴が分岐する。

    worktree未作成の反復では上流最新から新規作成する。
    追随失敗またはdirty状態では`None`を返し、呼び出し元は実装セッションを起動しない。
    """
    branch = f"worktree-{worktree_name}"
    worktree_path = local_path / _WORKTREE_PARENT_REL / worktree_name
    ref_check = _run_worktree_git(["check-ref-format", "--branch", branch], local_path)
    if ref_check.returncode != 0:
        _warn_worktree_preparation_failure(
            "worktree名から有効なGitブランチ名を作成できない",
            worktree_path,
            next_action="`--worktree`へ英数字とハイフンからなる名前を指定してprocess-loopを再起動する",
        )
        return None
    if not _ensure_worktree_excluded(local_path):
        return None
    upstream_branch = _git_output(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=local_path)
    if not upstream_branch:
        upstream_branch = _git_output(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=local_path)
    if not upstream_branch:
        _next_action.report(
            f"上流ブランチを解決できないため実装セッションを起動しません: {worktree_path}",
            next_action=(
                f"`git -C {local_path} branch -u <remote>/<branch>`で上流を設定するか、"
                f"`git -C {local_path} remote set-head origin -a`でorigin/HEADを設定する。"
                "解消後はprocess-loopが次の反復で再試行する"
            ),
        )
        return None
    remotes = (_git_output(["remote"], cwd=local_path) or "").splitlines()
    upstream_remote = max(
        (remote for remote in remotes if upstream_branch.startswith(f"{remote}/")),
        key=len,
        default=None,
    )
    if upstream_remote is None:
        _next_action.report(
            f"上流remoteを解決できないため実装セッションを起動しません: {worktree_path}",
            next_action=(
                f"`git -C {local_path} branch -u <remote>/<branch>`で上流を設定するか、"
                f"`git -C {local_path} remote set-head origin -a`でorigin/HEADを設定する。"
                "解消後はprocess-loopが次の反復で再試行する"
            ),
        )
        return None
    created_worktree = False
    if not worktree_path.exists():
        branch_exists = (
            _run_worktree_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], local_path).returncode == 0
        )
        if branch_exists:
            registered_worktrees = _run_worktree_git(["worktree", "list", "--porcelain"], local_path)
            branch_line = f"branch refs/heads/{branch}"
            if registered_worktrees.returncode != 0 or branch_line not in registered_worktrees.stdout.splitlines():
                _warn_worktree_preparation_failure(
                    "既存ブランチのworktree登録を確認できない",
                    worktree_path,
                    next_action=_worktree_status_next_action(worktree_path),
                )
                return None
        try:
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            _warn_worktree_preparation_failure(
                "worktreeの親ディレクトリを作成できなかった",
                worktree_path,
                next_action=_worktree_status_next_action(worktree_path),
            )
            return None
        fetch = _run_worktree_git(["fetch", upstream_remote], local_path)
        if fetch.returncode != 0:
            _next_action.report(
                f"worktree作成前のfetchに失敗しました: {fetch.stderr.strip()}",
                next_action=_fetch_failure_next_action(local_path, upstream_remote),
            )
            return None
        command = ["worktree", "add", str(worktree_path), branch]
        if not branch_exists:
            command = ["worktree", "add", "-b", branch, str(worktree_path), upstream_branch]
        created = _run_worktree_git(command, local_path)
        if created.returncode != 0:
            _next_action.report(
                f"worktreeの作成に失敗しました: {created.stderr.strip()}",
                next_action=(
                    f"`git -C {local_path} worktree list`で登録状況を確認し、実体の無い登録は"
                    f"`git -C {local_path} worktree prune`で除く。解消後はprocess-loopが次の反復で再試行する"
                ),
            )
            return None
        created_worktree = True
    elif not _validate_existing_worktree(local_path, worktree_path, branch):
        return None
    if created_worktree:
        if not worktree_path.is_dir():
            _warn_worktree_preparation_failure(
                "worktreeの配置先がディレクトリではない", worktree_path, next_action=_worktree_status_next_action(worktree_path)
            )
            return None
        if not _worktree_is_clean(worktree_path):
            _warn_worktree_preparation_failure(
                "worktreeに未コミット変更がある", worktree_path, next_action=_worktree_dirty_next_action(worktree_path)
            )
            return None
    if not created_worktree:
        fetch = _run_worktree_git(["fetch", upstream_remote], worktree_path)
        if fetch.returncode != 0:
            _next_action.report(
                f"worktreeのfetchに失敗しました: {fetch.stderr.strip()}",
                next_action=_fetch_failure_next_action(worktree_path, upstream_remote),
            )
            return None
    rebase = _run_worktree_git(["rebase", upstream_branch], worktree_path)
    if rebase.returncode == 0:
        print(f"worktreeを{upstream_branch}へ追随させました: {worktree_path}")
        if _worktree_is_clean(worktree_path):
            return worktree_path
        _warn_worktree_preparation_failure(
            "追随後のworktreeがdirtyになった", worktree_path, next_action=_worktree_dirty_next_action(worktree_path)
        )
        return None
    _run_worktree_git(["rebase", "--abort"], worktree_path)
    _next_action.report(
        f"worktreeの{upstream_branch}への追随に失敗したため実装セッションを起動しません（{rebase.stderr.strip()}）。",
        next_action=(
            f"rebaseは中止した。`git -C {worktree_path} rebase {upstream_branch}`を手作業で実行して競合を解消する。"
            "解消後はprocess-loopが次の反復で再試行する"
        ),
    )
    return None


def prepare_session_target(
    local_path: pathlib.Path,
    target_repo_id: str,
    prompt: str,
    *,
    worktree_name: str | None,
    resume_pending: bool,
) -> tuple[pathlib.Path, str] | None:
    """worktreeを必要とする新規セッションの実行先とpromptを返す。"""
    if resume_pending:
        return local_path, prompt
    if worktree_name is None and target_repo_id != DOTFILES_REPO_ID:
        return local_path, prompt
    # `--worktree`は使わない。CLIのworktree隔離ガードが、gitへの言及を問わず
    # ANSI-Cクォート・制御構造・コマンド置換など18種のシェル構文を拒否するため。
    effective_name = worktree_name or _DEFAULT_WORKTREE_NAME
    prepared = _sync_worktree_with_upstream(local_path, effective_name)
    if prepared is None:
        return None
    return prepared, _pl_session.build_process_loop_prompt()
