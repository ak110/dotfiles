"""`atk wi process-loop`がセッションを起動する作業ツリーの準備と上流との同期。"""

import hashlib
import json
import os
import pathlib
import subprocess

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import atomic_file, state_paths, worktree_hooks
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import refs as _git_refs
from agent_toolkit._git import worktree_exclude as _worktree_exclude
from agent_toolkit._git import worktrees as _git_worktrees

# `--worktree`指定時またはgithub.com/ak110/dotfiles編集時は、影響範囲の大きい作業ツリー直接編集を避けるため
# git worktreeを作成してセッションのcwdにする。worktree名は反復ごとに固定値とし、常駐ループの再起動を経ても
# 同一worktreeを継続利用させる。
DOTFILES_REPO_ID = "github.com/ak110/dotfiles"


_DEFAULT_WORKTREE_NAME = "process-loop"


# process-loopが作成するworktreeの配置先（対象リポジトリのroot相対）。
_WORKTREE_PARENT_REL = _worktree_exclude.WORKTREE_PARENT


def _git_output(args: list[str], cwd: pathlib.Path) -> str:
    """gitコマンドの標準出力を返す。失敗時は空文字を返す。"""
    try:
        return _git_command.output(args, cwd)
    except OSError, subprocess.CalledProcessError:
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


def _capture_ref(path: pathlib.Path, args: list[str]) -> str:
    """ref解決用の照会を、タイトル復元を伴う既存のGit実行へ渡す。"""
    return _git_output(args, cwd=path)


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
    except OSError, RuntimeError:
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
    try:
        _worktree_exclude.ensure_excluded(local_path)
    except _worktree_exclude.ExcludeError as error:
        if error.stage == "write":
            _warn_worktree_preparation_failure(
                "Gitの除外設定を更新できなかった",
                error.path,
                next_action=(
                    f"{error.path}の書き込み権限を確認するか、`{_worktree_exclude.IGNORE_PATTERN}`の行を手作業で追記する"
                ),
            )
        else:
            message = {
                "check": "worktree配置先の除外判定に失敗した",
                "path": "Gitの除外設定のパスを解決できなかった",
            }.get(error.stage, "worktree配置先の除外を確認できなかった")
            _warn_worktree_preparation_failure(message, local_path, next_action=_worktree_status_next_action(local_path))
        return False
    finally:
        _console_title.set_console_title("atk wi process-loop")
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
    except OSError, RuntimeError:
        _warn_worktree_preparation_failure(
            "既存worktreeの実体パスを解決できない", worktree_path, next_action=_worktree_status_next_action(worktree_path)
        )
        return False

    worktree_common = _git_output(["rev-parse", "--git-common-dir"], cwd=worktree_path)
    local_common = _git_output(["rev-parse", "--git-common-dir"], cwd=local_path)
    worktree_top = _git_output(["rev-parse", "--show-toplevel"], cwd=worktree_path)
    current_branch = _git_refs.resolve_branch(worktree_path, git_fn=_capture_ref)
    if not all((worktree_common, local_common, worktree_top)) or current_branch is None:
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
        or current_branch.ref != f"refs/heads/{branch}"
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

    上流は現在ブランチの追跡先を優先し、利用不能な場合だけ`refs/remotes/origin/HEAD`を使う。
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
    try:
        project_hook = worktree_hooks.configured(local_path)
    except (OSError, ValueError, TypeError, KeyError) as error:
        _warn_worktree_preparation_failure(str(error), local_path, next_action="対象のWorktreeCreate設定を修正して再試行する")
        return None
    hook_record = None
    common = ""
    if project_hook:
        common = _git_output(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=local_path)
        identifier = hashlib.sha256(json.dumps([common, worktree_name]).encode("utf-8")).hexdigest()
        hook_record = state_paths.state_dir() / "process-loop-worktrees" / f"{identifier}.json"
        if hook_record.exists():
            try:
                stored = json.loads(hook_record.read_text(encoding="utf-8"))
                worktree_path = pathlib.Path(stored["worktree"])
            except (OSError, ValueError, KeyError, TypeError) as error:
                _warn_worktree_preparation_failure(
                    str(error), hook_record, next_action="保存されたhook作成記録を確認し修正する"
                )
                return None
    ref_check = _run_worktree_git(["check-ref-format", "--branch", branch], local_path)
    if ref_check.returncode != 0:
        _warn_worktree_preparation_failure(
            "worktree名から有効なGitブランチ名を作成できない",
            worktree_path,
            next_action="`--worktree`へ英数字とハイフンからなる名前を指定してprocess-loopを再起動する",
        )
        return None
    if not project_hook and not _ensure_worktree_excluded(local_path):
        return None
    upstream = _git_refs.resolve_branch(local_path, git_fn=_capture_ref, upstream=True)
    if upstream is None:
        _next_action.report(
            f"上流ブランチを解決できないため実装セッションを起動しません: {worktree_path}",
            next_action=(
                f"`git -C {local_path} branch -u <remote>/<branch>`で上流を設定するか、"
                f"`git -C {local_path} remote set-head origin -a`でorigin/HEADを設定する。"
                "解消後はprocess-loopが次の反復で再試行する"
            ),
        )
        return None
    upstream_branch = upstream.ref
    assert upstream.remote is not None
    upstream_remote = upstream.remote
    created_worktree = False
    if not worktree_path.exists():
        branch_exists = (
            _run_worktree_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], local_path).returncode == 0
        )
        if branch_exists:
            try:
                registered_branches = {entry.branch for entry in _git_worktrees.list_worktrees(local_path)}
            except _git_worktrees.WorktreeListError:
                registered_branches = set()
            finally:
                _console_title.set_console_title("atk wi process-loop")
            if branch not in registered_branches:
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
        if project_hook:
            try:
                try:
                    existing_paths = {entry.path.resolve() for entry in _git_worktrees.list_worktrees(local_path)}
                except _git_worktrees.WorktreeListError as error:
                    raise ValueError(f"hook実行前のworktree登録を取得できない: {error}") from error
                finally:
                    _console_title.set_console_title("atk wi process-loop")
                if not existing_paths:
                    raise ValueError("hook実行前のworktree登録が空だった")
                owner = os.environ.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "process-loop")
                diagnostics = managed_temp.create_managed_temp("worktree-hook")
                worktree_path = worktree_hooks.create(local_path, worktree_name, owner, diagnostics_directory=diagnostics)
                if worktree_path.resolve() in existing_paths:
                    raise ValueError("hookが既存の作業ツリーを返した")
                assert hook_record is not None
                atomic_file.atomic_write(
                    hook_record,
                    json.dumps(
                        {
                            "worktree": str(worktree_path),
                            "repo": str(local_path),
                            "branch": branch,
                            "session_id": owner,
                            "hook_diagnostics": str(diagnostics),
                        }
                    )
                    + "\n",
                )
                if _git_output(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=worktree_path) != common:
                    raise ValueError("hook作成先のGit共通ディレクトリが対象と異なる")
                current = _git_refs.resolve_branch(worktree_path, git_fn=_capture_ref)
                if current is None or current.ref != f"refs/heads/{branch}":
                    switched = _run_worktree_git(["-c", "submodule.recurse=false", "switch", "-c", branch], worktree_path)
                    if switched.returncode:
                        raise ValueError(f"hook作成先のbranchを接続できない: {switched.stderr.strip()}")
                created = subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            except (OSError, ValueError, KeyError, TypeError, managed_temp.ManagedTempError) as error:
                _warn_worktree_preparation_failure(
                    str(error), worktree_path, next_action="hook設定と途中資源・保存記録を確認し同じ対象で再試行する"
                )
                return None
        else:
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
    rebase = _run_worktree_git(["-c", "submodule.recurse=false", "rebase", upstream_branch], worktree_path)
    if rebase.returncode == 0:
        print(f"worktreeを{upstream_branch}へ追随させました: {worktree_path}")
        if _worktree_is_clean(worktree_path):
            return worktree_path
        _warn_worktree_preparation_failure(
            "追随後のworktreeがdirtyになった", worktree_path, next_action=_worktree_dirty_next_action(worktree_path)
        )
        return None
    _run_worktree_git(["-c", "submodule.recurse=false", "rebase", "--abort"], worktree_path)
    _next_action.report(
        f"worktreeの{upstream_branch}への追随に失敗したため実装セッションを起動しません（{rebase.stderr.strip()}）。",
        next_action=(
            f"rebaseは中止した。`git -C {worktree_path} -c submodule.recurse=false rebase {upstream_branch}`を"
            "手作業で実行して競合を解消する。"
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
        if not local_path.is_dir() or not _git_output(["rev-parse", "--show-toplevel"], cwd=local_path):
            _warn_worktree_preparation_failure(
                "再開先のworktreeを確認できない", local_path, next_action=_worktree_status_next_action(local_path)
            )
            return None
        return local_path, prompt
    if worktree_name is None and target_repo_id != DOTFILES_REPO_ID:
        try:
            if not worktree_hooks.configured(local_path):
                return local_path, prompt
        except (OSError, ValueError, TypeError, KeyError) as error:
            _warn_worktree_preparation_failure(
                str(error), local_path, next_action="対象のWorktreeCreate設定を修正して再試行する"
            )
            return None
    # `--worktree`は使わない。CLIのworktree隔離ガードが、gitへの言及を問わず
    # ANSI-Cクォート・制御構造・コマンド置換など18種のシェル構文を拒否するため。
    effective_name = worktree_name or _DEFAULT_WORKTREE_NAME
    prepared = _sync_worktree_with_upstream(local_path, effective_name)
    if prepared is None:
        return None
    return prepared, prompt
