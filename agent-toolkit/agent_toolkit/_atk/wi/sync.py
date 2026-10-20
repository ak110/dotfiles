"""WI保存リポジトリ（private-notes）の準備、remote同期、排他ロックとcommit・push。

不変条件: WI保存リポジトリへのgit操作・ファイル変更は、`repo_lock(private_notes)`保持下でのみ行う。
複数プロセスが同一クローンへ並行アクセスする運用（`atk wi process-loop`の複数常駐等）を前提とし、
この不変条件を破るとremote同期とファイル操作・commitの交錯によるfast-forward失敗を招く。
`repo_lock`はロックファイル名を対象パスから導出するため、WI保存リポジトリ以外の
git作業コピー（`atk wi process-loop`が上流差分を確認するdotfilesチェックアウト等）にも適用する。
計画ロックの除外設定はGitの版管理の対象外へ書くため、この不変条件の対象には当たらない。

不変条件: `list`のような読み取り専用のサブコマンドは、WI保存リポジトリの版管理を
書き換えない。前段の環境準備は対話シェルの起動ごとにも実行されるため、この不変条件を破ると
ユーザーの操作と無関係なcommitとpushが起動のたびに発生し、他cloneとの競合を招く。

旧形式の移行: `ensure_environment`は毎回`migrate_legacy_layout`を、`pull`は毎回`migrate_legacy_reservations`を呼ぶ。
"""

import contextlib
import datetime
import pathlib
import re
import subprocess
import sys
import time
from collections.abc import Iterable

import filelock

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import legacy as _atk_wi_legacy
from agent_toolkit._atk.wi.constants import WI_ACTIVE_STATES
from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
from agent_toolkit._atk.wi.layout import iter_entry_paths
from agent_toolkit._common import file_lock as _file_lock
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._git import command as _git_command


def has_remote(private_notes: pathlib.Path) -> bool:
    """現在branchのupstreamを解決できるか、共有Git同期層で判定する。"""
    return _atk_git_sync.has_remote(private_notes)


def init_local_private_notes_repo(root: pathlib.Path) -> None:
    """ローカル管理用のgitリポジトリを`root`へ自動生成する。

    `AGENT_TOOLKIT_PRIVATE_NOTES`が未設定で、未指定時に使う`~/private-notes/`も存在しない場合に、
    `root`（`platformdirs.user_data_dir("agent-toolkit")`配下）へ生成する。
    remoteとupstreamは設定せず、以後の同期はローカルcommitだけで完了する。
    生成した保存先はWIも計画も持たないため、`ensure_environment`が保存先と設定方法を警告する。
    """
    root.mkdir(parents=True, exist_ok=True)
    run_git(["init"], cwd=root)
    _file_lock.ensure_plan_lock_ignored(root / "plans" / ".agent-toolkit-plan-create.lock")
    run_git(["add", "-A"], cwd=root)
    run_git(
        [
            "-c",
            "user.email=agent-toolkit@localhost",
            "-c",
            "user.name=agent-toolkit",
            "commit",
            "--allow-empty",
            "-m",
            "chore: initialize local private-notes repository",
        ],
        cwd=root,
    )


def ensure_environment(home: pathlib.Path) -> pathlib.Path:
    """WI保存ディレクトリの存在を確認し、rootパスを返す。

    `AGENT_TOOLKIT_PRIVATE_NOTES`で明示指定されたパスが不在の場合はexit 1で原因を案内する。
    未指定かつ省略時に使うパスも不在の場合は`init_local_private_notes_repo`でローカルリポジトリを自動生成する。
    旧2階層レイアウトが残るリポジトリは`migrate_legacy_layout`が平坦レイアウトへ移行する。

    環境変数が未設定で`~/private-notes/`も無く、保存先にWIも計画も無い場合は、保存先の場所と既存のprivate-notesを
    使う設定方法を標準エラーへ警告する。既存のキューが別の場所にあるエンドユーザーが、空の保存先の結果を
    キューが空であることと取り違えないためである。終了コードと標準出力は変えない。
    """
    root = _private_notes.default_private_notes(home)
    if not root.exists():
        if _private_notes.private_notes_override() is not None:
            _outcome.report_failure(
                f"WI保存ディレクトリが見つからない: {root}",
                next_action="環境変数AGENT_TOOLKIT_PRIVATE_NOTESの値を実在するディレクトリへ直すか、"
                "未設定にして省略時の保存先を使ってから再実行する",
            )
            sys.exit(1)
        init_local_private_notes_repo(root)
    _file_lock.ensure_plan_lock_ignored(root / "plans" / ".agent-toolkit-plan-create.lock")
    migrate_legacy_layout(root)
    if _private_notes.private_notes_override() is None and not (home / "private-notes").exists() and _has_no_items(root):
        _outcome.report_warning(
            f"省略時の保存先へ自動生成したローカル管理用のprivate-notesを使っている（WIと計画は0件）: {root}",
            next_action="既存のprivate-notesを使う場合は、環境変数AGENT_TOOLKIT_PRIVATE_NOTESへそのパスを設定するか"
            "~/private-notesへ配置して再実行する。この保存先を使い続ける場合は対応不要で、処理は継続した。"
            f"AGENT_TOOLKIT_PRIVATE_NOTES={root}を設定すると、この警告は出力されなくなる",
        )
    return root


def _has_no_items(root: pathlib.Path) -> bool:
    """状態ディレクトリにWIの`.md`が無く、`plans/`にも計画ファイルが無いかを返す。"""
    if next(iter_entry_paths(root), None) is not None:
        return False
    plans = root / "plans"
    return not plans.is_dir() or next(plans.rglob("*.md"), None) is None


def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
    """gitコマンドをcwdで実行し、失敗時は例外を送出する。"""
    _git_command.run_quiet(args, cwd, forward_error_output=forward_error_output)


def migrate_legacy_layout(private_notes: pathlib.Path) -> None:
    """旧配置を専用移行モジュールで年月固定配置へ移行する。"""
    _atk_wi_legacy.migrate_legacy_layout(
        private_notes,
        repo_lock_fn=repo_lock,
        pull_fn=pull,
        commit_fn=commit_and_push,
    )


def migrate_legacy_reservations(private_notes: pathlib.Path) -> int:
    """旧予約形式を専用移行モジュールで通常inboxへ移行する。"""
    return _atk_wi_legacy.migrate_legacy_reservations(
        private_notes,
        assert_lock_fn=assert_repo_lock_held,
        commit_fn=commit_and_push,
    )


PULL_MIN_INTERVAL_SECONDS = 30.0
"""直近のremote同期とみなす時間幅。

直近の同期からの経過時間は`.git/FETCH_HEAD`のmtimeで判定する。
同ファイルは`git fetch`が実行されるたびに更新され、プロセスを跨いで参照できるため、
状態ファイルを別途設けずに済む。
定期バックグラウンド更新の省略と、読み取り操作の同期再利用に共用する。
"""


_TERMINAL_COMMIT_SUBJECT = re.compile(
    r"^chore: process (?P<count>[1-9][0-9]*) (?P<noun>entry|entries) \((?P<outcome>adopted|rejected)\)$"
)


_PROCESSING_TIMESTAMP_PREFIX = "- 処理日時: "


def _normalized_terminal_content(content: str) -> str | None:
    """最後の処理結果にある処理日時だけを比較用の固定値へ置換する。"""
    lines = content.splitlines(keepends=True)
    logical_lines = [line.rstrip("\r\n") for line in lines]
    headings = [index for index, line in enumerate(logical_lines) if line == "## 処理結果"]
    timestamps = [index for index, line in enumerate(logical_lines) if line.startswith(_PROCESSING_TIMESTAMP_PREFIX)]
    if not headings or len(timestamps) != 1 or timestamps[0] <= headings[-1]:
        return None
    timestamp_index = timestamps[0]
    try:
        datetime.datetime.fromisoformat(logical_lines[timestamp_index].removeprefix(_PROCESSING_TIMESTAMP_PREFIX))
    except ValueError:
        return None
    line_ending = lines[timestamp_index][len(logical_lines[timestamp_index]) :]
    lines[timestamp_index] = _PROCESSING_TIMESTAMP_PREFIX + line_ending
    return "".join(lines)


def _git_file_content(private_notes: pathlib.Path, revision_path: str) -> str:
    """Git tree上のファイル内容を前後の空白も保持して返す。"""
    result = _git_command.run(
        ["show", revision_path],
        private_notes,
        check=True,
        capture_output=True,
        text=True,
    )
    assert isinstance(result.stdout, str)
    return result.stdout


def _terminal_commit_is_redundant(private_notes: pathlib.Path, commit: str) -> bool:
    """単一のローカルcommitがupstream上の同等なMQ終端だけを含むか判定する。"""
    subject = _git_command.output(["show", "-s", "--format=%s", commit], private_notes)
    match = _TERMINAL_COMMIT_SUBJECT.fullmatch(subject)
    if match is None:
        return False
    count = int(match.group("count"))
    if (count == 1) != (match.group("noun") == "entry"):
        return False
    outcome = match.group("outcome")
    output = _git_command.optional_stdout(
        ["diff-tree", "--no-commit-id", "--name-status", "-z", "-r", "--no-renames", commit],
        private_notes,
    )
    if output is None:
        return False
    changes = output.removesuffix("\0").split("\0") if output else []
    if len(changes) % 2:
        return False
    pairs = tuple(zip(changes[::2], changes[1::2], strict=True))
    if len(pairs) == count and all(status == "M" for status, _ in pairs):
        for _status, relative in pairs:
            path = pathlib.PurePosixPath(relative)
            if len(path.parts) != 3 or path.suffix != ".md":
                return False
            before = parse_frontmatter(_git_file_content(private_notes, f"{commit}^:{relative}"))
            local = _git_file_content(private_notes, f"{commit}:{relative}")
            upstream = _git_file_content(private_notes, f"@{{u}}:{relative}")
            local_metadata = parse_frontmatter(local)
            upstream_metadata = parse_frontmatter(upstream)
            if (
                before is None
                or before[0].get("state") not in WI_ACTIVE_STATES
                or local_metadata is None
                or local_metadata[0].get("state") != outcome
                or upstream_metadata is None
                or upstream_metadata[0].get("state") != outcome
            ):
                return False
            normalized = _normalized_terminal_content(local)
            if normalized is None or normalized != _normalized_terminal_content(upstream):
                return False
        return True
    removed: dict[str, str] = {}
    added: dict[str, str] = {}
    for status, relative in zip(changes[::2], changes[1::2], strict=True):
        path = pathlib.PurePosixPath(relative)
        if len(path.parts) != 2 or path.suffix != ".md":
            return False
        state, filename = path.parts
        if status == "D" and state in WI_ACTIVE_STATES:
            removed[filename] = relative
        elif status == "A" and state == outcome:
            added[filename] = relative
        else:
            return False
    if len(changes) != count * 4 or len(removed) != count or removed.keys() != added.keys():
        return False

    for filename, destination in added.items():
        source = removed[filename]
        source_result = _git_command.run(
            ["cat-file", "-e", f"@{{u}}:{source}"],
            private_notes,
            capture_output=True,
            text=True,
        )
        if source_result.returncode == 0:
            return False
        local_content = _git_file_content(private_notes, f"{commit}:{destination}")
        upstream_content = _git_file_content(private_notes, f"@{{u}}:{destination}")
        local_normalized = _normalized_terminal_content(local_content)
        if local_normalized is None or local_normalized != _normalized_terminal_content(upstream_content):
            return False
    return True


def _redundant_terminal_divergence(private_notes: pathlib.Path) -> bool:
    """ローカル側の全commitがupstream反映済みの同等なMQ終端なら真を返す。"""
    try:
        commits = _git_command.lines(["rev-list", "--reverse", "@{u}..HEAD"], private_notes)
        if not commits:
            return False
        return all(_terminal_commit_is_redundant(private_notes, commit) for commit in commits)
    except OSError, subprocess.SubprocessError, UnicodeError:
        return False


def pull(private_notes: pathlib.Path) -> None:
    """WI保存リポジトリを明示したupstreamへfast-forward同期する。

    不変条件表明: `repo_lock`保持下でのみ呼び出す。
    remote未設定（`init_local_private_notes_repo`が生成したローカル管理リポジトリ等）の場合は
    remote同期を省略し、旧予約形式の移行だけを実行する。
    fetchは共有状態の`FETCH_HEAD`を更新しうるが、統合対象は`@{u}`へ固定して
    他プロセスのfetchおよび`pull.rebase`設定から独立させる。
    """
    assert_repo_lock_held(private_notes)
    _pull_remote(private_notes)
    migrate_legacy_reservations(private_notes)
    _atk_wi_legacy.migrate_legacy_layout(
        private_notes,
        repo_lock_fn=lambda _notes: contextlib.nullcontext(),
        pull_fn=lambda _notes: None,
        commit_fn=commit_and_push,
    )


def _pull_remote(private_notes: pathlib.Path) -> None:
    """remoteをfetchし、同等終端の安全な回復を含めてupstreamへ統合する。"""
    assert_repo_lock_held(private_notes)
    _atk_git_sync.pull(
        private_notes,
        run_git=run_git,
        redundant_divergence=_redundant_terminal_divergence,
    )


def pull_with_recent_reuse(private_notes: pathlib.Path, *, force_pull: bool = False) -> None:
    """読み取り専用サブコマンドの同期を直近の成功済み同期結果だけ再利用する。

    直近30秒の同期形跡とupstreamのHEAD祖先判定が成立した場合だけremoteのfetch・mergeを
    省略し、旧予約形式の移行は実行する。状態遷移系サブコマンドは最新状態を要するため
    この処理を使わず、毎回`pull`を実行する。

    `force_pull`が真の場合は再利用判定を行わず、必ず`pull`を実行する。
    不変条件表明: `repo_lock`保持下でのみ呼び出す。
    """
    assert_repo_lock_held(private_notes)
    _atk_git_sync.ensure_not_rebasing(private_notes)
    if force_pull or not pulled_recently(private_notes):
        pull(private_notes)
        return

    migrate_legacy_reservations(private_notes)


def ensure_mutation_allowed(private_notes: pathlib.Path) -> None:
    """共通ロックの取得後、WIの内容・配置を変更する前に、変更を開始できる状態かを確かめる。

    rebase中の作業コピーでは`RebaseInProgressError`を送出する。remote同期を省略する場合（Webの保存）も
    変更前に確かめる。commit時の確認だけに頼ると、WIを書き換えた後に拒否して作業ツリーへ変更が残る。
    `repo_lock`の取得後に呼ぶ。ロックの外で確かめると、確かめた後に同期がrebaseを始め得る。
    """
    _atk_git_sync.ensure_not_rebasing(private_notes)


def assert_repo_lock_held(private_notes: pathlib.Path) -> None:
    """`private_notes`が現在の実行スレッドで`repo_lock`保持中でなければ`RuntimeError`を送出する（不変条件表明）。"""
    _atk_git_sync.assert_repo_lock_held(private_notes)


def repo_lock_path(repo_path: pathlib.Path) -> pathlib.Path:
    """`repo_path`に対応するロックファイルの絶対パスを返す。

    配置先はロックファイルのディレクトリ（`agent_toolkit._common.state_paths.lock_dir`）とし、
    ファイル名は、同じGitリポジトリに属するworktree間で共有されるGit common directoryの
    SHA-1ハッシュ値とする。対象リポジトリからロックファイル名を導出するため、
    WI保存リポジトリに限らず任意のgit作業コピーへ同一の仕組みを適用できる。
    取得時にロック用ディレクトリを自動作成する。
    """
    return _atk_git_sync.repo_lock_path(repo_path)


def repo_lock(repo_path: pathlib.Path, *, timeout: float = -1) -> filelock.FileLock:
    """指定したgit作業コピーへのgit操作・ファイル変更を排他するプロセス間ロックを返す。

    WI保存リポジトリ（`private_notes`）のほか、`atk wi process-loop`が
    上流差分を確認するdotfiles作業コピーも対象とする。
    `filelock.FileLock`は同一インスタンス内で再入可能（スレッドローカル＋カウンタ管理）だが、
    現行のロック区間分割設計では同一関数内のネスト`with`は発生しない。
    CLIは待機時間を指定しなければ取得できるまで無期限に待機する
    （常駐ループはclaudeセッション実行中にロックを保持しない設計であり、
    臨界区間はgit操作前後の短時間に限るため）。
    """
    return _atk_git_sync.repo_lock(repo_path, timeout=timeout)


def commit_and_push(
    private_notes: pathlib.Path,
    message: str,
    rel_paths: Iterable[str],
    *,
    skip_push: bool = False,
) -> int | None:
    """指定パスをaddしcommit・pushする。

    不変条件表明: `repo_lock`保持下でのみ呼び出す。
    push失敗時（他プロセス・他端末による先行pushとの非fast-forward等）は
    `git fetch`後に明示した`@{u}`へrebaseしてpushを1回だけ再試行する。rebase自体が
    失敗した場合はrebase中の状態を保持し、競合解消と`git rebase --continue`の手順を
    stderrへ出力して元の例外を送出する。
    再試行後のpushが失敗した場合はその例外をそのまま送出する。
    remote未設定（`init_local_private_notes_repo`が生成したローカル管理リポジトリ等）の場合は
    commitのみ実行しpushをスキップする。
    `skip_push=True`の場合はcommitだけを実行し、remote設定時は未pushのcommitが残る警告と、
    後続の通常操作または`atk wi commit`でpushする手順を標準エラーへ出力する。
    """
    return _atk_git_sync.commit_and_push(
        private_notes,
        message,
        rel_paths,
        skip_push=skip_push,
        run_git=run_git,
        push_pending_fn=push_pending_commits,
    )


def push_pending_commits(private_notes: pathlib.Path) -> int | None:
    """ローカルcommitをpushし、同等終端の回復またはrebase後に1回だけ再試行する。"""
    return _atk_git_sync.push_pending_commits(
        private_notes,
        run_git=run_git,
        redundant_divergence=_redundant_terminal_divergence,
    )


def notify_unpushed_commits_if_any(private_notes: pathlib.Path) -> bool:
    """未pushのcommitが残る場合に対応手順を表示する。

    件数の取得から報告までを`repo_lock`の排他区間で行う。別プロセスがロック内でcommitしてから
    pushするまでの一時的な状態を、同期未達として報告しないためである。呼び出し元はロックを保持しない。
    """
    with repo_lock(private_notes):
        count = _atk_git_sync.pending_commit_count(private_notes)
        if count is None or count == 0:
            return False
        if _atk_git_sync.push_was_deferred(private_notes):
            return True
        resolved = private_notes.resolve()
        _outcome.report_warning(
            f"private-notesに未pushのcommitが{count}件残る。操作自体は完了している。",
            next_action=f"`git -C {resolved} status`で差分を確認し、cleanにしてから`atk wi commit`でpushする。",
        )
        return True


def pull_if_stale(private_notes: pathlib.Path) -> bool:
    """定期更新が必要ならremote同期し、実行したかを返す。

    定期バックグラウンド更新専用とする。ユーザーの操作によって呼ばれる処理
    （変更操作・明示的な同期要求）は`pull`を用い、毎回リモートの最新状態を取得する。
    """
    assert_repo_lock_held(private_notes)
    if pulled_recently(private_notes):
        return False
    pull(private_notes)
    return True


def synchronize(private_notes: pathlib.Path, *, only_if_stale: bool = False, lock_timeout: float = -1) -> bool:
    """Web操作が残したcommitをpushし、必要なremote差分を取得する。"""
    with repo_lock(private_notes, timeout=lock_timeout):
        push_pending_commits(private_notes)
        if only_if_stale:
            return pull_if_stale(private_notes)
        pull(private_notes)
        return True


def pulled_recently(private_notes: pathlib.Path) -> bool:
    """直近の成功済み同期を再利用できる状態かを返す。

    `.git`がファイルの場合（worktree形式）は`stat`が失敗し偽を返すため、
    同期結果を再利用せずremote同期を実行する。
    WI保存リポジトリは通常のクローンであり該当しない。
    ローカル管理リポジトリは再利用対象外とする。

    `FETCH_HEAD`のmtimeだけではfetch後の統合失敗を検出できないため、
    `@{u}`がHEADの祖先であることも確認する。祖先判定が失敗した場合は再利用せず、
    呼び出し元が通常のremote同期を実行する。
    """
    if not has_remote(private_notes):
        return False
    fetch_head = private_notes / ".git" / "FETCH_HEAD"
    try:
        elapsed = time.time() - fetch_head.stat().st_mtime
    except OSError:
        return False
    if elapsed >= PULL_MIN_INTERVAL_SECONDS:
        return False
    try:
        run_git(["merge-base", "--is-ancestor", "@{u}", "HEAD"], cwd=private_notes)
    except subprocess.CalledProcessError:
        return False
    return True


def sync_exit_code(exit_code: int, private_notes: pathlib.Path, *, should_check: bool) -> int:
    """成功した同期対象操作に未push通知の終了コードを反映する。"""
    if exit_code == 0 and should_check and notify_unpushed_commits_if_any(private_notes):
        return 3
    return exit_code
