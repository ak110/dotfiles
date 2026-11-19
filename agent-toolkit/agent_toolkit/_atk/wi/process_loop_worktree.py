"""`atk wi process-loop`がセッションを起動する作業ツリーの準備と上流との同期。"""

import hashlib
import json
import os
import pathlib
import subprocess
import sys

from agent_toolkit._atk import config_repositories as _config_repositories
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

# 追随のrebaseを始める直前に、worktree固有のGitディレクトリへ開始状態を書く記録。
# workerごと止められて中断したrebaseが自分の開始したものかを、次の反復が記録と状態ディレクトリの一致で確かめて元へ戻す。
_SYNC_REBASE_RECORD = "atk-process-loop-rebase.json"
# merge backendのrebaseが状態ディレクトリへ書く開始状態のうち、記録と比べるファイル。
_REBASE_STATE_FILES = ("head-name", "orig-head", "onto")

# 途中のまま残る操作を示すGit管理パス、表示名、続行・中止に使うgitの引数。
# `git am`も`rebase-apply`を使うため、`applying`の有無で区別する。
_IN_PROGRESS_OPERATIONS = (
    ("rebase-merge", "rebase", "-c submodule.recurse=false rebase"),
    ("rebase-apply/applying", "am", "am"),
    ("rebase-apply", "rebase", "-c submodule.recurse=false rebase"),
    ("MERGE_HEAD", "merge", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick", "cherry-pick"),
    ("REVERT_HEAD", "revert", "revert"),
    ("BISECT_LOG", "bisect", "bisect"),
)


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
    except subprocess.TimeoutExpired as error:
        # 打ち切ったrebaseを呼び出し元が中止できるよう、例外で抜けずに失敗として返す。
        result = subprocess.CompletedProcess(
            _git_command.command_line(args),
            returncode=124,
            stdout="",
            stderr=f"{error.timeout:g}秒の時間上限に達したため打ち切った",
        )
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


def _failure_text(args: list[str], cwd: pathlib.Path, result: subprocess.CompletedProcess[str]) -> str:
    """成り立たなかったgitの照会を、実行したコマンド・終了コード・標準エラーで説明する。"""
    command = f"git -C {cwd} {' '.join(args)}"
    if result.returncode == 0:
        return f"`{command}`の出力が空だった"
    detail = " ".join((result.stderr or "").split()) or "標準エラーなし"
    return f"`{command}`が終了コード{result.returncode}で失敗した: {detail}"


def _query_git_path(args: list[str], cwd: pathlib.Path) -> pathlib.Path | str:
    """gitが出力したパスをcwd基準で絶対化して返す。照会が成り立たない場合は失敗の説明を返す。"""
    result = _run_worktree_git(args, cwd)
    path = _resolve_git_path(result.stdout.strip() if result.returncode == 0 else "", cwd)
    return _failure_text(args, cwd, result) if path is None else path


def _git_paths(worktree_path: pathlib.Path, names: list[str]) -> list[pathlib.Path] | None:
    """worktreeから見たGit管理パスを絶対パスで返す。照会に失敗した場合は`None`を返す。"""
    args = ["rev-parse", "--path-format=absolute"]
    for name in names:
        args += ["--git-path", name]
    result = _run_worktree_git(args, worktree_path)
    lines = result.stdout.splitlines() if result.returncode == 0 else []
    return [pathlib.Path(line) for line in lines] if len(lines) == len(names) else None


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


def _validate_existing_worktree(
    local_path: pathlib.Path, worktree_path: pathlib.Path, branch: str, upstream_branch: str
) -> bool:
    """既存worktreeが対象リポジトリの専用worktreeとして使えることを検証する。

    失うcommitと変更が無い中断状態（中断した追随のrebase、専用ブランチから外れたHEAD）は元へ戻してから検証する。
    使えない場合は、成り立たなかった条件と状態ごとの次の操作を報告する。
    """
    failure = _existing_worktree_failure(local_path, worktree_path, branch, upstream_branch)
    if failure is None:
        return True
    message, next_action = failure
    _warn_worktree_preparation_failure(message, worktree_path, next_action=next_action)
    return False


def _existing_worktree_failure(
    local_path: pathlib.Path, worktree_path: pathlib.Path, branch: str, upstream_branch: str
) -> tuple[str, str] | None:
    """既存worktreeを使えない理由と次の操作を返す。使える場合は`None`を返す。"""
    status_next_action = _worktree_status_next_action(worktree_path)
    if not worktree_path.is_dir():
        return "worktreeの配置先がディレクトリではない", status_next_action
    try:
        resolved_worktree_path = worktree_path.resolve()
    except OSError, RuntimeError:
        return "既存worktreeの実体パスを解決できない", status_next_action

    worktree_common = _query_git_path(["rev-parse", "--git-common-dir"], worktree_path)
    local_common = _query_git_path(["rev-parse", "--git-common-dir"], local_path)
    worktree_top = _query_git_path(["rev-parse", "--show-toplevel"], worktree_path)
    for answer in (worktree_common, local_common, worktree_top):
        if isinstance(answer, str):
            return (
                f"既存worktreeのGit照会が失敗した（{answer}）",
                f"`git -C {worktree_path} status`で状態を確認する。リポジトリやworktreeを移動した場合は、"
                f"`git -C {local_path} worktree repair {worktree_path}`で管理情報の参照先を直す。"
                "解消後はprocess-loopが次の反復で再試行する",
            )
    registration_next_action = (
        f"`git -C {local_path} worktree list`で登録を確認し、専用worktreeとして登録されていない配置先は退避するか削除する。"
        "解消後はprocess-loopが次の反復で再試行する"
    )
    if worktree_common != local_common:
        return (
            f"既存worktreeのGit共通ディレクトリ{worktree_common}が対象リポジトリの{local_common}と異なる",
            registration_next_action,
        )
    if worktree_top != resolved_worktree_path:
        return f"配置先がworktreeの最上位ではない（最上位: {worktree_top}）", registration_next_action

    _recover_interrupted_sync_rebase(worktree_path)
    markers = [marker for marker, _label, _command in _IN_PROGRESS_OPERATIONS]
    paths = _git_paths(worktree_path, markers)
    if paths is None:
        return "既存worktreeで途中の操作の有無を確認できない", status_next_action
    for path, (_marker, label, command) in zip(paths, _IN_PROGRESS_OPERATIONS, strict=True):
        if path.exists():
            return f"既存worktreeで{label}が途中のまま残っている", _operation_next_action(worktree_path, label, command)

    current = _git_refs.resolve_branch(worktree_path, git_fn=_capture_ref)
    if current is None or current.ref != f"refs/heads/{branch}":
        failure = _return_to_dedicated_branch(worktree_path, branch, upstream_branch, current)
        if failure is not None:
            return failure
    if not _worktree_is_clean(worktree_path):
        return "worktreeに未コミット変更がある", _worktree_dirty_next_action(worktree_path)
    _ensure_upstream_tracking(worktree_path, branch, upstream_branch)
    return None


def _ensure_upstream_tracking(worktree_path: pathlib.Path, branch: str, upstream_branch: str) -> None:
    """専用ブランチに追跡先が無い場合に限り、上流を追跡先に設定する。

    WorktreeCreate hookの`--detach`で作られたworktreeや手作業の`switch -C`は追跡先を持たず、
    主作業ツリーのブランチの公開状態（`# branch.upstream`）を判定できない。設定済みの追跡先は変えない。
    設定に失敗しても準備は続け、警告だけを出す。
    """
    configured = _run_worktree_git(["config", "--get", f"branch.{branch}.remote"], worktree_path)
    if configured.returncode == 0 and configured.stdout.strip():
        return
    result = _run_worktree_git(["branch", f"--set-upstream-to={upstream_branch}", branch], worktree_path)
    if result.returncode != 0:
        print(
            f"専用ブランチ{branch}の追跡先を{upstream_branch}に設定できませんでした: {result.stderr.strip()}", file=sys.stderr
        )


def _operation_next_action(worktree_path: pathlib.Path, label: str, command: str) -> str:
    """途中のまま残る操作を続けるか取りやめる次の操作を返す。"""
    retry = "解消後はprocess-loopが次の反復で再試行する"
    if label == "bisect":
        return f"`git -C {worktree_path} bisect reset`で二分探索を終える。{retry}"
    return (
        f"`git -C {worktree_path} status`で競合と進行状況を確認し、続けるなら競合を解消して"
        f"`git -C {worktree_path} {command} --continue`を、"
        f"取りやめるなら`git -C {worktree_path} {command} --abort`を実行する。{retry}"
    )


def _return_to_dedicated_branch(
    worktree_path: pathlib.Path, branch: str, upstream_branch: str, current: _git_refs.BranchRef | None
) -> tuple[str, str] | None:
    """専用ブランチから外れたHEADを、失うcommitと変更が無い場合に限り専用ブランチへ戻す。

    別ブランチのHEADはそのブランチに残り、切り離されたHEADはローカルブランチか上流に含まれていれば、
    専用ブランチへ切り替えても失うcommitは無い。上流以外のリモート追跡refは、リモートでブランチを消した後の
    fetchで消えうるため、含まれていても切り替えない。未commitの変更があれば切り替えない。
    専用ブランチが無い場合は上流から作り直す。戻せない場合は理由と次の操作を返す。
    """
    branch_exists = (
        _run_worktree_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], worktree_path).returncode == 0
    )
    switch_args = ["switch", branch] if branch_exists else ["switch", "--track", "-c", branch, upstream_branch]
    switch_command = f"git -C {worktree_path} {' '.join(switch_args)}"
    retry = "解消後はprocess-loopが次の反復で再試行する"
    if current is None:
        head = _run_worktree_git(["rev-parse", "--verify", "HEAD"], worktree_path).stdout.strip()
        where = f"切り離されたcommit {head[:12]}"
        local_branch = _run_worktree_git(["for-each-ref", "--count=1", "--contains", "HEAD", "refs/heads"], worktree_path)
        in_upstream = _run_worktree_git(["merge-base", "--is-ancestor", "HEAD", upstream_branch], worktree_path)
        if not (local_branch.returncode == 0 and local_branch.stdout.strip()) and in_upstream.returncode != 0:
            return (
                f"既存worktreeのHEADが専用ブランチ`{branch}`ではなく{where}にあり、"
                "そのcommitがローカルブランチにも上流にも含まれない",
                f"`git -C {worktree_path} branch <退避先の名前> HEAD`でcommitを保存してから、"
                f"`{switch_command}`で専用ブランチへ戻す。{retry}",
            )
    else:
        where = f"`{current.ref}`"
    if not _worktree_is_clean(worktree_path):
        return (
            f"既存worktreeのHEADが専用ブランチ`{branch}`ではなく{where}にあり、未commitの変更がある",
            f"`git -C {worktree_path} status`で変更を確認してcommitするか退避し、"
            f"`{switch_command}`で専用ブランチへ戻す。{retry}",
        )
    switched = _run_worktree_git(["-c", "submodule.recurse=false", *switch_args], worktree_path)
    if switched.returncode != 0:
        failure = _failure_text(switch_args, worktree_path, switched)
        return (
            f"既存worktreeのHEADを専用ブランチ`{branch}`へ戻せなかった（{failure}）",
            f"`git -C {worktree_path} status`と`git -C {worktree_path} worktree list`で状態を確認し、"
            f"`{switch_command}`で専用ブランチへ戻す。{retry}",
        )
    print(f"既存worktreeのHEADが{where}にあったため、専用ブランチ{branch}へ戻しました: {worktree_path}")
    return None


def _write_sync_rebase_record(worktree_path: pathlib.Path, branch: str, upstream_branch: str) -> pathlib.Path | None:
    """追随のrebaseを始める前に開始状態を記録し、記録のパスを返す。記録できない場合は`None`を返す。"""
    paths = _git_paths(worktree_path, [_SYNC_REBASE_RECORD])
    heads = _run_worktree_git(["rev-parse", "HEAD", f"{upstream_branch}^{{commit}}"], worktree_path).stdout.split()
    if paths is None or len(heads) != 2:
        return None
    record = dict(zip(_REBASE_STATE_FILES, (f"refs/heads/{branch}", *heads), strict=True))
    try:
        atomic_file.atomic_write(paths[0], json.dumps(record) + "\n")
    except OSError:
        return None
    return paths[0]


def _recover_interrupted_sync_rebase(worktree_path: pathlib.Path) -> None:
    """追随のrebaseが中断されたまま残っていれば、失うcommitと変更が無い場合に限り中止して開始前へ戻す。

    常駐の停止はworkerとrebaseをまとめて止めるため、中止の処理が実行されないままrebaseが残る。
    開始時の記録と状態ディレクトリが一致しないrebase（別の主体が開始したもの）、作業ツリーに変更や競合が残るもの、
    競合を解消して続行した（元のcommitと変更内容が異なる）commitを含むものには触れず、後続の検証が報告する。
    """
    paths = _git_paths(worktree_path, [_SYNC_REBASE_RECORD, "rebase-merge"])
    if paths is None or not paths[0].exists():
        return
    record_path, state_dir = paths
    if not state_dir.is_dir():
        # rebaseは完了したか、開始前に止まった。
        record_path.unlink(missing_ok=True)
        return
    try:
        recorded = json.loads(record_path.read_text(encoding="utf-8"))
        state = {name: (state_dir / name).read_text(encoding="utf-8").strip() for name in _REBASE_STATE_FILES}
    except OSError, ValueError:
        return
    branch_tip = _run_worktree_git(["rev-parse", "--verify", "--quiet", state["head-name"]], worktree_path).stdout.strip()
    if recorded != state or branch_tip != state["orig-head"] or not _worktree_is_clean(worktree_path):
        return
    replayed = _run_worktree_git(["cherry", state["orig-head"], "HEAD", state["onto"]], worktree_path)
    if replayed.returncode != 0 or any(line.startswith("+") for line in replayed.stdout.splitlines()):
        return
    if _run_worktree_git(["-c", "submodule.recurse=false", "rebase", "--abort"], worktree_path).returncode != 0:
        return
    record_path.unlink(missing_ok=True)
    print(f"中断されていた追随のrebaseを中止し、開始前の状態へ戻しました: {worktree_path}")


def branch_worktree_name(worktree_name: str, branch: str) -> str:
    """系統ごとの常駐用worktree名を返す。"""
    return f"{worktree_name}-{_config_repositories.branch_slug(branch)}"


def _sync_worktree_with_upstream(
    local_path: pathlib.Path, worktree_name: str, *, remote_branch: str | None = None
) -> pathlib.Path | None:
    """worktreeを準備して対象リポジトリの上流最新へ追随させる。

    上流は現在ブランチの追跡先を優先し、利用不能な場合だけ`refs/remotes/origin/HEAD`を使う。
    系統を登録したrepositoryでは`remote_branch`を受け取り、originのその系統のブランチを上流とする。
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
    upstream = (
        _git_refs.resolve_branch(local_path, git_fn=_capture_ref, upstream=True)
        if remote_branch is None
        else _git_refs.BranchRef(f"refs/remotes/origin/{remote_branch}", remote_branch, "origin")
    )
    if upstream is None:
        _next_action.report(
            f"上流ブランチを解決できないため実装セッションを起動しません: {worktree_path}。"
            "解消後はprocess-loopが次の反復で再試行する。",
            next_action=(
                f"`git -C {local_path} branch -u <remote>/<branch>`で上流を設定するか、"
                f"`git -C {local_path} remote set-head origin -a`でorigin/HEADを設定せよ。"
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
            if remote_branch is not None:
                # 系統のworktreeは追跡先を系統のリモートブランチにし、`--target-branch auto`が系統を解決できるようにする。
                command = ["worktree", "add", "--track", "-b", branch, str(worktree_path), upstream_branch]
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
                if remote_branch is not None:
                    # hookは登録パスの作業ツリーの先端から作るため、系統のリモートブランチから作り直す。
                    switched = _run_worktree_git(
                        ["-c", "submodule.recurse=false", "switch", "-C", branch, "--track", upstream_branch], worktree_path
                    )
                    if switched.returncode:
                        raise ValueError(f"hook作成先のbranchを系統へ接続できない: {switched.stderr.strip()}")
                elif current is None or current.ref != f"refs/heads/{branch}":
                    # hookは`--detach`でブランチを作らないため、系統なしでも上流を追跡先にして専用ブランチを作る。
                    switched = _run_worktree_git(
                        ["-c", "submodule.recurse=false", "switch", "--track", "-c", branch, upstream_branch], worktree_path
                    )
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
                f"worktreeの作成に失敗しました: {created.stderr.strip()}。解消後はprocess-loopが次の反復で再試行する。",
                next_action=(
                    f"`git -C {local_path} worktree list`で登録状況を確認し、実体の無い登録は"
                    f"`git -C {local_path} worktree prune`で除け。"
                ),
            )
            return None
        created_worktree = True
    elif not _validate_existing_worktree(local_path, worktree_path, branch, upstream_branch):
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
    record = _write_sync_rebase_record(worktree_path, branch, upstream_branch)
    if record is None:
        _warn_worktree_preparation_failure(
            "追随のrebaseの開始状態を記録できなかった", worktree_path, next_action=_worktree_status_next_action(worktree_path)
        )
        return None
    # apply backendはHEADを切り離してから状態ディレクトリを作るため、その間に中断されると開始状態を記録と比べられない。
    # merge backendは状態ディレクトリを先に作るので、中断後も次の反復が開始状態を記録と比べて元へ戻せる。
    rebase = _run_worktree_git(
        ["-c", "submodule.recurse=false", "-c", "rebase.backend=merge", "rebase", upstream_branch], worktree_path
    )
    if rebase.returncode == 0:
        record.unlink(missing_ok=True)
        print(f"worktreeを{upstream_branch}へ追随させました: {worktree_path}")
        if _worktree_is_clean(worktree_path):
            return worktree_path
        _warn_worktree_preparation_failure(
            "追随後のworktreeがdirtyになった", worktree_path, next_action=_worktree_dirty_next_action(worktree_path)
        )
        return None
    _run_worktree_git(["-c", "submodule.recurse=false", "rebase", "--abort"], worktree_path)
    remaining = _git_paths(worktree_path, ["rebase-merge"])
    if remaining is None or remaining[0].exists():
        _next_action.report(
            f"worktreeの{upstream_branch}への追随に失敗し、rebaseも中止できなかったため実装セッションを起動しません"
            f"（{rebase.stderr.strip()}）。",
            next_action=_operation_next_action(worktree_path, "rebase", "-c submodule.recurse=false rebase"),
        )
        return None
    record.unlink(missing_ok=True)
    _next_action.report(
        f"worktreeの{upstream_branch}への追随に失敗したため実装セッションを起動しません（{rebase.stderr.strip()}）。"
        "rebaseは中止した。解消後はprocess-loopが次の反復で再試行する。",
        next_action=(
            f"`git -C {worktree_path} -c submodule.recurse=false rebase {upstream_branch}`を手作業で実行して競合を解消せよ。"
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
    branch: str | None = None,
) -> tuple[pathlib.Path, str] | None:
    """worktreeを必要とする新規セッションの実行先とpromptを返す。

    系統（`branch`）を持つ対象は、登録パスの作業ツリーのブランチを切り替えずに処理するため、常に系統ごとのworktreeを用意する。
    """
    if resume_pending:
        if not local_path.is_dir() or not _git_output(["rev-parse", "--show-toplevel"], cwd=local_path):
            _warn_worktree_preparation_failure(
                "再開先のworktreeを確認できない", local_path, next_action=_worktree_status_next_action(local_path)
            )
            return None
        return local_path, prompt
    if branch is not None:
        prepared = _sync_worktree_with_upstream(
            local_path, branch_worktree_name(worktree_name or _DEFAULT_WORKTREE_NAME, branch), remote_branch=branch
        )
        return None if prepared is None else (prepared, prompt)
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
