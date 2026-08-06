"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_common.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。

不変条件: フィードバック保存リポジトリ（`private_notes`）へのgit操作・ファイル変更は、
`_repo_lock(private_notes)`保持下でのみ行う。複数プロセスが同一クローンへ並行アクセスする
運用（`atk mq process-loop`の複数常駐等）を前提とし、当該不変条件を破ると
pullとファイル操作・commitの交錯によるfast-forward失敗を招く。
`_repo_lock`はロックファイル名を対象パスから導出するため、フィードバック保存リポジトリ以外の
git作業コピー（`atk mq process-loop`が上流差分を確認するdotfilesチェックアウト等）にも適用する。

TBDの回答判定`_is_tbd_answered`は`_tbd_scan`が実体を持つ。PostToolUseフックが
依存パッケージなしで同じ判定を利用するため、本モジュールは再エクスポートのみを行う。
"""

import argparse
import dataclasses
import datetime
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Literal

import _git_remote
import filelock
import platformdirs
from _atk_mq_formatters import (
    _display_width,
    _parse_target_repo,
    _target_repo_budget,
    _tbd_body_summary,
    _truncate_target_repo,
)
from _atk_mq_frontmatter import parse_frontmatter, serialize_frontmatter
from _tbd_scan import _ACTIVE_STATES as MQ_ACTIVE_STATES
from _tbd_scan import _TBD_TYPE as MQ_TYPE_TBD
from _tbd_scan import is_tbd_answered as _is_tbd_answered

# フィードバック管理repoの4状態フォルダー名（管理repoのroot直下）。
# - `inbox`: 未処理の投入直後
# - `processing`: `start-processing`で処理中に移動された途中状態
# - `adopted`: 採用として最終処理された状態
# - `rejected`: 不採用として最終処理された状態
MQ_STATE_INBOX = "inbox"
MQ_STATE_PROCESSING = "processing"
MQ_STATE_ADOPTED = "adopted"
MQ_STATE_REJECTED = "rejected"
MQ_STATES = (MQ_STATE_INBOX, MQ_STATE_PROCESSING, MQ_STATE_ADOPTED, MQ_STATE_REJECTED)
MQ_TYPE_FEEDBACK = "feedback"
MQ_TYPES = (MQ_TYPE_FEEDBACK, MQ_TYPE_TBD)
type RepairKind = Literal["frontmatter", "missing-plan-file"]


@dataclasses.dataclass(frozen=True)
class QueueEntry:
    """readiness判定に必要なキュー項目の現行状態。"""

    filename: str
    text: str
    kind: str | None
    state: str
    target_repo: str | None
    tbd_answered: bool | None
    frontmatter_broken: bool
    plan_file: str | None
    depends_on: tuple[str, ...]
    legacy_dependency: dict[str, object] | None
    repair_target_filename: str | None
    repair_kind: RepairKind | None


@dataclasses.dataclass(frozen=True)
class ReadinessResult:
    """対象リポジトリのactive項目に対するreadinessと修復診断。"""

    ready: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    frontmatter_broken: tuple[str, ...] = ()
    frontmatter_broken_needs_tbd: tuple[str, ...] = ()
    missing_plan_file: tuple[str, ...] = ()
    missing_plan_file_needs_tbd: tuple[str, ...] = ()
    invalid_dependencies: tuple[str, ...] = ()
    missing_dependencies: tuple[str, ...] = ()
    self_dependencies: tuple[str, ...] = ()
    cyclic_dependencies: tuple[str, ...] = ()

    @property
    def actionable_count(self) -> int:
        """処理セッションを起動すべき項目数を返す。"""
        repair_targets = {
            *self.frontmatter_broken_needs_tbd,
            *self.missing_plan_file_needs_tbd,
            *self.invalid_dependencies,
            *self.missing_dependencies,
            *self.self_dependencies,
            *self.cyclic_dependencies,
        }
        return len(set(self.ready) | repair_targets)


_SPACE_SEPARATED_OPTION_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "mq": frozenset(("adopt", "reject", "rm")),
}
_SPACE_SEPARATED_OPTIONS = frozenset(("--note", "--commit"))


def is_existing_dir(path: pathlib.Path) -> bool:
    """パスが実在ディレクトリかどうかを判定する（OSレベルの`OSError`はFalse扱い）。

    自由記述のMESSAGE文字列をパス候補として`is_dir()`へ渡す呼び出し元があり、
    長大な文字列は`OSError: File name too long`を送出しうるため、ここで吸収する。
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def warn_space_separated_option(argv: list[str]) -> None:
    """後始末サブコマンドの値付きオプションが空白区切りの場合に警告する。"""
    top_command = None
    top_index = None
    for cmd in ("mq",):
        try:
            top_index = argv.index(cmd)
            top_command = cmd
            break
        except ValueError:
            continue
    if top_command is None or top_index is None:
        return
    subcommand_index = top_index + 1
    try:
        subcommand = argv[subcommand_index]
    except IndexError:
        return
    if subcommand not in _SPACE_SEPARATED_OPTION_SUBCOMMANDS.get(top_command, frozenset()):
        return
    for index, arg in enumerate(argv[subcommand_index + 1 :], start=subcommand_index + 1):
        if arg not in _SPACE_SEPARATED_OPTIONS or index + 1 >= len(argv):
            continue
        value = argv[index + 1]
        if not value.startswith("--") and "=" not in value:
            print(f"警告: {arg}は{arg}=VALUE形式で渡すことを推奨します。", file=sys.stderr)


def _subdir(private_notes: pathlib.Path, name: str) -> pathlib.Path:
    """管理repo直下の指定サブディレクトリパスを返す。必要時に作成する。"""
    path = private_notes / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _private_notes_path(home: pathlib.Path) -> pathlib.Path:
    """フィードバック保存ディレクトリのroot絶対パスを返す。

    環境変数`AGENT_TOOLKIT_PRIVATE_NOTES`が設定されていれば当該値を優先する。
    未設定時は`~/private-notes/`へフォールバックし、当該パスが不在の場合は
    `platformdirs.user_data_dir("agent-toolkit")`配下のローカル管理用パスへさらにフォールバックする
    （`_ensure_environment`が当該パスへ実体のgitリポジトリを自動生成する）。
    `appauthor=False`はWindowsでappnameが二重階層になる挙動を防ぐ。
    """
    override = os.environ.get("AGENT_TOOLKIT_PRIVATE_NOTES")
    if override:
        return pathlib.Path(override).expanduser()
    default = home / "private-notes"
    if default.exists():
        return default
    return pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False)) / "private-notes"


_LOCAL_ONLY_MARKER = ".agent-toolkit-local-only"
"""`_init_local_private_notes_repo`生成のローカル限定リポジトリ直下に置くマーカーファイル名。

remote未設定であることを`git remote`の実行結果に頼らずファイル存在のみで判定するための目印。
通常運用（既存クローン済みリポジトリ・テストの一時ディレクトリ）にはこのファイルが存在しないため、
既存のgit呼び出し経路（`subprocess.run`のフェイク差し替え等）に影響を与えない。
"""


def _has_remote(private_notes: pathlib.Path) -> bool:
    """`private_notes`がremote設定済みの通常リポジトリか判定する。

    `_LOCAL_ONLY_MARKER`が存在する場合のみFalse（`_init_local_private_notes_repo`が
    生成したremote未設定のローカル管理リポジトリ）とみなし、`_pull`・`_commit_and_push`は
    この判定でpull・push操作をスキップする。マーカー不在時は`git remote`実行結果を問わず
    Trueとして扱う（通常運用のリポジトリを対象とする既存の呼び出し経路を変えないため）。
    """
    return not (private_notes / _LOCAL_ONLY_MARKER).exists()


def _init_local_private_notes_repo(root: pathlib.Path) -> None:
    """ローカル管理用のgitリポジトリを`root`へ自動生成する。

    `AGENT_TOOLKIT_PRIVATE_NOTES`未設定かつ既定パス`~/private-notes/`が不在の場合に、
    `root`（`platformdirs.user_data_dir("agent-toolkit")`配下）へ生成する。
    remoteは設定せず`_LOCAL_ONLY_MARKER`を配置する（`_has_remote`がFalseを返し、
    以後の`_pull`・`_commit_and_push`はpush・pullをスキップしてローカルコミットのみで完結する）。
    """
    root.mkdir(parents=True, exist_ok=True)
    _run_git(["init"], cwd=root)
    (root / _LOCAL_ONLY_MARKER).write_text(
        "このファイルはprivate-notesリポジトリがローカル限定自動生成であることを示すマーカーである。\n"
        "削除するとpull/pushの自動スキップが解除され、remote未設定のままgit操作が失敗しうる。\n",
        encoding="utf-8",
    )
    for name in MQ_STATES:
        state_dir = root / name
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / ".gitkeep").touch()
    _run_git(["add", "-A"], cwd=root)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=agent-toolkit@localhost",
            "-c",
            "user.name=agent-toolkit",
            "commit",
            "-m",
            "chore: initialize local private-notes repository",
        ],
        cwd=root,
        check=True,
    )


def _ensure_environment(home: pathlib.Path) -> pathlib.Path:
    """フィードバック保存ディレクトリの存在を確認し、rootパスを返す。

    `AGENT_TOOLKIT_PRIVATE_NOTES`で明示指定されたパスが不在の場合はexit 1で原因を案内する。
    未指定かつ既定パスも不在の場合は`_init_local_private_notes_repo`でローカルリポジトリを自動生成する。
    旧2階層レイアウトが残るリポジトリは`_migrate_legacy_layout`が平坦レイアウトへ移行する。
    """
    root = _private_notes_path(home)
    if not root.exists():
        if os.environ.get("AGENT_TOOLKIT_PRIVATE_NOTES"):
            print(f"フィードバック保存ディレクトリが見つかりません: {root}", file=sys.stderr)
            sys.exit(1)
        _init_local_private_notes_repo(root)
    _migrate_legacy_layout(root)
    return root


def _with_type_frontmatter(text: str, entry_type: str) -> str | None:
    """先頭frontmatterへ`type`キーを補った本文を返す。"""
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    data, body = parsed
    if data.get("type") is not None:
        return text
    ordered = {"target_repo": data["target_repo"]} if "target_repo" in data else {}
    ordered["type"] = entry_type
    ordered.update((key, value) for key, value in data.items() if key not in ordered)
    return serialize_frontmatter(ordered, body)


def _plan_legacy_migration(
    private_notes: pathlib.Path, legacy_dirs: list[pathlib.Path]
) -> list[tuple[pathlib.Path, pathlib.Path, str]]:
    """旧レイアウト配下の移行計画を`(移行元, 移行先, 移行後本文)`の一覧として返す。

    移行を妨げる要素（未知のディレクトリ・エントリ以外のファイル・移行先の衝突・
    frontmatterの破損）を検出した場合は、全件を列挙してからexit 2で終了する。
    ファイルへ触れる前に全件を検証し、中断時に移行途中の状態を残さない。
    """
    planned: list[tuple[pathlib.Path, pathlib.Path, str]] = []
    destinations: set[pathlib.Path] = set()
    errors: list[str] = []
    for legacy_dir in legacy_dirs:
        entry_type = legacy_dir.name
        for path in sorted(legacy_dir.rglob("*")):
            relative = path.relative_to(legacy_dir)
            if path.is_dir():
                if len(relative.parts) != 1 or relative.parts[0] not in MQ_STATES:
                    errors.append(f"未知のディレクトリ: {path}")
                continue
            if path.name == ".gitkeep":
                continue
            if len(relative.parts) != 2 or path.suffix != ".md":
                errors.append(f"エントリとして解釈できないファイル: {path}")
                continue
            destination = private_notes / relative.parts[0] / path.name
            if destination.exists() or destination in destinations:
                errors.append(f"移行先が既に存在: {destination}")
                continue
            migrated = _with_type_frontmatter(path.read_text(encoding="utf-8"), entry_type)
            if migrated is None:
                errors.append(f"frontmatterが不正: {path}")
                continue
            destinations.add(destination)
            planned.append((path, destination, migrated))
    if errors:
        print(f"旧レイアウトの移行を中止しました（{private_notes}）。以下を解消してから再実行してください。", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        sys.exit(2)
    return planned


def _tracked_names(private_notes: pathlib.Path, names: Iterable[str]) -> list[str]:
    """指定名のうち、gitの追跡対象を含むものだけを返す。

    追跡対象を1件も含まないパスを`git add`へ渡すとpathspec不一致で失敗するため、
    削除済みディレクトリをcommit対象へ含める際の限定に用いる。
    """
    name_list = list(names)
    result = subprocess.run(
        ["git", "ls-files", "--", *name_list],
        cwd=private_notes,
        capture_output=True,
        text=True,
        check=True,
    )
    assert isinstance(result.stdout, str)
    tracked = {line.split("/", 1)[0] for line in result.stdout.splitlines()}
    return [name for name in name_list if name in tracked]


def _migrate_legacy_layout(private_notes: pathlib.Path) -> None:
    """旧2階層レイアウトの管理repoを平坦レイアウトへ移行する。

    旧レイアウトは種別ごとのディレクトリ（`<type>/<state>/`）でエントリ種別を表現していた。
    平坦レイアウトは状態ディレクトリ（`<state>/`）のみをrepo直下へ置き、種別は
    frontmatterの`type`で表現する。移行では各エントリへ`type`行を補ってから状態
    ディレクトリへ移し、旧ディレクトリを削除してcommit・pushする。

    旧ディレクトリが無い場合は無動作で戻る（通常運用でのロック取得・pullを避けるため）。
    移行前のバージョンのコマンドが並行稼働していると空の旧ディレクトリが再生成されうるため、
    commit対象が生じない場合はcommitへ進まず削除のみで完結させる。
    """
    if not any((private_notes / name).is_dir() for name in MQ_TYPES):
        return
    with _repo_lock(private_notes):
        _pull(private_notes)
        # 他プロセス・他端末が先に移行済みの場合があるため、pull後の状態で再判定する。
        legacy_dirs = [private_notes / name for name in MQ_TYPES if (private_notes / name).is_dir()]
        if not legacy_dirs:
            return
        planned = _plan_legacy_migration(private_notes, legacy_dirs)
        for source, destination, migrated in planned:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(migrated, encoding="utf-8")
            source.unlink()
        legacy_names = [legacy_dir.name for legacy_dir in legacy_dirs]
        tracked_legacy_names = _tracked_names(private_notes, legacy_names)
        for legacy_dir in legacy_dirs:
            shutil.rmtree(legacy_dir)
        count = len(planned)
        if not count and not tracked_legacy_names:
            print(f"旧レイアウトの空ディレクトリを削除しました: {private_notes}", file=sys.stderr)
            return
        _commit_and_push(
            private_notes,
            f"chore: migrate {count} {'entry' if count == 1 else 'entries'} to flat layout",
            [*(name for name in MQ_STATES if (private_notes / name).is_dir()), *tracked_legacy_names],
        )
        print(f"旧レイアウトの{count}件を平坦レイアウトへ移行しました: {private_notes}", file=sys.stderr)


def _run_git(args: list[str], cwd: pathlib.Path) -> None:
    """gitコマンドをcwdで実行し、失敗時は例外を送出する。"""
    subprocess.run(["git", *args], cwd=cwd, check=True)


_PULL_MIN_INTERVAL_SECONDS = 30.0
"""定期バックグラウンド更新専用の`git pull`レート制限。

直近のpullからの経過時間は`.git/FETCH_HEAD`のmtimeで判定する。
同ファイルは`git pull`（内部のfetch）が実行されるたびに更新され、プロセスを跨いで参照できるため、
状態ファイルを別途設けずに済む。
"""


def _pull(private_notes: pathlib.Path) -> None:
    """フィードバック保存リポジトリで`git pull --ff-only`を実行する。

    不変条件表明: `_repo_lock`保持下でのみ呼び出す。
    remote未設定（`_init_local_private_notes_repo`が生成したローカル管理リポジトリ等）の場合は無動作。
    """
    _assert_repo_lock_held(private_notes)
    if not _has_remote(private_notes):
        return
    _run_git(["pull", "--ff-only"], cwd=private_notes)


class _ThreadLocalHeldPaths(threading.local):
    """現在の実行スレッドが保持中の`_repo_lock`対象パスと保持回数を保持する。"""

    def __init__(self) -> None:
        self.paths: dict[pathlib.Path, int] = {}


# スレッドごとの保持記録。他スレッドの保持を自スレッドの保持と誤認しないよう、
# プロセス共有の`set`ではなく`threading.local`派生で分離する。
_LOCK_HELD_PATHS = _ThreadLocalHeldPaths()


def _assert_repo_lock_held(private_notes: pathlib.Path) -> None:
    """`private_notes`が現在の実行スレッドで`_repo_lock`保持中でなければ`RuntimeError`を送出する（不変条件表明）。"""
    if _LOCK_HELD_PATHS.paths.get(private_notes.resolve(), 0) <= 0:
        raise RuntimeError(
            "不変条件違反: private_notesへのgit操作・ファイル変更は_repo_lock保持下でのみ実行できる。"
            "呼び出し元でwith _repo_lock(private_notes):を用いること。"
        )


def _repo_lock_path(repo_path: pathlib.Path) -> pathlib.Path:
    """`repo_path`に対応するロックファイルの絶対パスを返す。

    配置先は`platformdirs.user_state_dir("agent-toolkit")`配下`locks/`ディレクトリとし、
    ファイル名は`repo_path.resolve()`のSHA-1ハッシュ値とする。
    対象パスからロックファイル名を導出するため、フィードバック保存リポジトリに限らず
    任意のgit作業コピーへ同一の仕組みを適用できる。取得時にロック用ディレクトリを自動作成する。
    `appauthor=False`はWindowsでappnameが二重階層になる挙動を防ぐ。
    """
    resolved = str(repo_path.resolve())
    digest = hashlib.sha1(resolved.encode("utf-8"), usedforsecurity=False).hexdigest()
    lock_dir = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{digest}.lock"


class _RepoLock(filelock.FileLock):
    """`_repo_lock`が返すロック。保持区間を`_LOCK_HELD_PATHS`へ登録・解除する。"""

    def __init__(self, repo_path: pathlib.Path, *, timeout: float = -1) -> None:
        self._target = repo_path.resolve()
        super().__init__(str(_repo_lock_path(repo_path)), timeout=timeout)

    def acquire(
        self,
        timeout: float | None = None,
        poll_interval: float | None = None,
        *,
        poll_intervall: float | None = None,
        blocking: bool | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> filelock.AcquireReturnProxy:
        result = super().acquire(
            timeout,
            poll_interval,
            poll_intervall=poll_intervall,
            blocking=blocking,
            cancel_check=cancel_check,
        )
        _LOCK_HELD_PATHS.paths[self._target] = _LOCK_HELD_PATHS.paths.get(self._target, 0) + 1
        return result

    def release(self, force: bool = False) -> None:
        super().release(force)
        if not self.is_locked:
            _LOCK_HELD_PATHS.paths.pop(self._target, None)


def _repo_lock(repo_path: pathlib.Path, *, timeout: float = -1) -> filelock.FileLock:
    """指定したgit作業コピーへのgit操作・ファイル変更を排他するプロセス間ロックを返す。

    フィードバック保存リポジトリ（`private_notes`）のほか、`atk mq process-loop`が
    上流差分を確認するdotfiles作業コピーも対象とする。
    `filelock.FileLock`は同一インスタンス内で再入可能（スレッドローカル＋カウンタ管理）だが、
    現行のロック区間分割設計では同一関数内のネスト`with`は発生しない。
    CLIは既定値により取得できるまで無期限に待機する
    （常駐ループはclaudeセッション実行中にロックを保持しない設計であり、
    臨界区間はgit操作前後の短時間に限るため）。
    """
    return _RepoLock(repo_path, timeout=timeout)


def _copy_to_tempfile(content: bytes) -> pathlib.Path:
    """バイト列を`.md`拡張子の一時ファイルへ書き込み、そのパスを返す。

    エディター起動をロック外で行う経路（`_cmd_edit`等）が、ロック保持下で取得した
    対象ファイルのスナップショットを一時ファイルへ複製する用途に用いる。
    """
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".md", delete=False) as f:
        f.write(content)
        return pathlib.Path(f.name)


def _commit_and_push(private_notes: pathlib.Path, message: str, rel_paths: Iterable[str]) -> None:
    """指定パスをaddしcommit・pushする。

    不変条件表明: `_repo_lock`保持下でのみ呼び出す。
    push失敗時（他プロセス・他端末による先行pushとの非fast-forward等）は
    `git pull --rebase`を経由してpushを1回だけ再試行する。経由した`pull --rebase`自体が
    失敗した場合は`git rebase --abort`の成否を確認してからリベース開始前の状態への
    復元結果をstderrへ出力し、元の例外を送出する。
    再試行後のpushが失敗した場合はその例外をそのまま送出する。
    remote未設定（`_init_local_private_notes_repo`が生成したローカル管理リポジトリ等）の場合は
    commitのみ実行しpushをスキップする。
    """
    _assert_repo_lock_held(private_notes)
    rel_list = list(rel_paths)
    _run_git(["add", *rel_list], cwd=private_notes)
    _run_git(["commit", "-m", message], cwd=private_notes)
    _push_pending_commits(private_notes)


def _push_pending_commits(private_notes: pathlib.Path) -> None:
    """ローカルcommitをpushし、競合時はrebase後に1回だけ再試行する。"""
    _assert_repo_lock_held(private_notes)
    if not _has_remote(private_notes):
        return
    try:
        _run_git(["push"], cwd=private_notes)
    except subprocess.CalledProcessError:
        try:
            _run_git(["pull", "--rebase"], cwd=private_notes)
        except subprocess.CalledProcessError:
            abort_result = subprocess.run(["git", "rebase", "--abort"], cwd=private_notes, check=False)
            if abort_result.returncode != 0:
                print(
                    "git rebase --abortが失敗しました。rebase中間状態が残存している可能性があり、手動復旧が必要です。",
                    file=sys.stderr,
                )
            else:
                print("git rebase --abortでリベース開始前の状態へ復元しました。", file=sys.stderr)
            raise
        _run_git(["push"], cwd=private_notes)


def _stamp_result(
    path: pathlib.Path,
    *,
    outcome: str,
    now: datetime.datetime,
    commit: str | None = None,
    note: str | None = None,
    category: str | None = None,
) -> None:
    """対象ファイル末尾へ`## 処理結果`節を追記する。

    outcomeは`adopted`・`rejected`のいずれかを受け取る。
    commit・note・categoryは省略可能で、指定時のみ対応する箇条書き項目を追加する。
    categoryは採用フィードバックの再発防止分類ラベルを受け取る。
    値は`atk mq adopt --category`由来とする。
    """
    body = path.read_text(encoding="utf-8")
    if not body.endswith("\n"):
        body += "\n"
    lines = [
        "",
        "## 処理結果",
        "",
        f"- 採否: {outcome}",
        f"- 処理日時: {now.isoformat(timespec='seconds')}",
    ]
    if commit:
        lines.append(f"- 対応commit: {commit}")
    if note:
        lines.append(f"- メモ: {note}")
    if category:
        lines.append(f"- カテゴリ: {category}")
    body += "\n".join(lines) + "\n"
    path.write_text(body, encoding="utf-8")


def _normalize_md_filename(filename: str) -> str:
    """拡張子`.md`が省略されたファイル名を正規形（`.md`付き）へ補完して返す。

    `_validate_filename`と`_verify_frontmatter_target_repo`の双方が同一の正規化規約を
    使うためのSSOT。パス妥当性検証は行わない（呼び出し元が別途`_validate_filename`等で担う）。
    """
    if not filename.endswith(".md"):
        return f"{filename}.md"
    return filename


def _validate_filename(filename: str, base_dir: pathlib.Path) -> pathlib.Path:
    r"""ファイル名が基準ディレクトリ直下の単純名であることを検証して絶対パスを返す。

    `/`・`\`・`..`・絶対パス・空文字列・カレント参照は早期に拒否する。
    拡張子`.md`が省略された入力は正規形（`.md`付き）へ補完する。
    """
    parts = pathlib.Path(filename).parts
    if (
        filename in ("", ".", "..")
        or "/" in filename
        or "\\" in filename
        or ".." in parts
        or pathlib.PurePath(filename).is_absolute()
    ):
        print(f"不正なファイル名: {filename}", file=sys.stderr)
        sys.exit(2)
    filename = _normalize_md_filename(filename)
    path = base_dir / filename
    base_resolved = base_dir.resolve()
    try:
        path.resolve().relative_to(base_resolved)
    except ValueError:
        print(f"ファイル名が基準ディレクトリ外を指しています: {filename}", file=sys.stderr)
        sys.exit(2)
    return path


def _validate_filenames_only(filenames: list[str], base_dir: pathlib.Path) -> None:
    """ファイル名群のみ検証する（pull前の早期拒否用）。"""
    for f in filenames:
        _validate_filename(f, base_dir)


def _dedup_positional_filenames(filenames: list[str], subcommand: str) -> list[str]:
    """位置引数として渡された`filenames`から重複を除去し、除去件数が0より大きい場合はstderrへ警告する。

    正規化後の同一性で重複判定するため、`_normalize_md_filename`で正規化した値をキーに
    順序保存する（例: `name`と`name.md`は同一項目として1件へ集約）。
    呼び出し元は`_atk_mq_mutations.py`の`_cmd_start_processing`・`_cmd_return_to_inbox`・
    `_cmd_adopt`・`_cmd_reject`・`_cmd_rm`の5サブコマンドとする。
    戻り値は正規化前の原文字列のうち初出のものを保持する（正規化は判定にのみ用いる）。
    """
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for name in filenames:
        key = _normalize_md_filename(name)
        if key in seen:
            duplicates.append(name)
        else:
            seen[key] = name
    if duplicates:
        unique_duplicates = list(dict.fromkeys(duplicates))
        print(
            f"警告: {subcommand}の引数リストに重複が含まれます（重複除去して処理を継続）: {', '.join(unique_duplicates)}",
            file=sys.stderr,
        )
    return list(seen.values())


def _iter_inbox_entries(inbox_dir: pathlib.Path, target_repo: str | None = None) -> Iterator[tuple[pathlib.Path, str, str]]:
    """inbox配下の`.md`ファイルを名前順に走査し、`(path, target_repo, text)`を返す。

    `target_repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみ返す。ディレクトリ不在時は何も返さない。
    """
    if not inbox_dir.exists():
        return
    for path in sorted(inbox_dir.iterdir()):
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        entry_repo = _parse_target_repo(text)
        if target_repo is not None and entry_repo != target_repo:
            continue
        yield path, entry_repo, text


def _parse_type(text: str) -> str | None:
    """本文先頭のfrontmatterから`type`を抽出する。"""
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    value = parsed[0].get("type")
    return value if isinstance(value, str) and value else None


def make_filename_completer(
    states: tuple[str, ...],
    entry_type: str | None = None,
) -> Callable[..., list[str]]:
    """argcomplete用のキュー内ファイル名補完候補生成器を返す。

    `states`が指す状態ディレクトリ配下の`.md`をprefix一致で列挙する。
    `entry_type`を指定した場合はfrontmatterの`type`が一致するものだけを返す。
    種別を限定する場合だけ本文を読むため、限定しない場合はディレクトリ走査で完結する。
    """

    def complete(prefix: str, **_: object) -> list[str]:
        private_notes = _private_notes_path(pathlib.Path.home())
        candidates: list[str] = []
        for state in states:
            state_dir = private_notes / state
            if not state_dir.exists():
                continue
            for path in state_dir.iterdir():
                if path.suffix != ".md" or not path.name.startswith(prefix):
                    continue
                if entry_type is not None and _parse_type(path.read_text(encoding="utf-8")) != entry_type:
                    continue
                candidates.append(path.name)
        return sorted(candidates)

    return complete


def _require_type(path: pathlib.Path, text: str) -> str | None:
    """エントリの種別を検証して返す。"""
    if parse_frontmatter(text) is None:
        return None
    entry_type = _parse_type(text)
    if entry_type not in MQ_TYPES:
        print(
            f"frontmatterのtypeが不正または欠落しています（feedback・tbdのいずれかが必要）: {path}",
            file=sys.stderr,
        )
        sys.exit(2)
    return entry_type


def _iter_entries(
    private_notes: pathlib.Path,
    states: Iterable[str],
    filter_repo: str | None,
    entry_type: str = "all",
) -> Iterator[tuple[pathlib.Path, str, str, str, str | None]]:
    """指定状態のエントリをパス・対象repo・本文・状態・種別の順で列挙する。"""
    for state in states:
        state_dir = private_notes / state
        for path, target_repo, text in _iter_inbox_entries(state_dir):
            actual_type = _require_type(path, text)
            if filter_repo is not None and actual_type is not None and target_repo != filter_repo:
                continue
            if entry_type not in ("all", actual_type):
                continue
            yield path, target_repo, text, state, actual_type


def notify_unanswered_tbds_if_any(private_notes: pathlib.Path, target_repo: str | None) -> None:
    """未回答TBDが存在する場合に種別ヘッダ付きの1件1行形式で通知する。"""
    entries = [
        (path, entry_repo, text, state)
        for path, entry_repo, text, state, _ in _iter_entries(private_notes, MQ_ACTIVE_STATES, target_repo, MQ_TYPE_TBD)
        if not _is_tbd_answered(text)
    ]
    if not entries:
        return
    print("# tbd", file=sys.stderr)
    for path, entry_repo, text, state in entries:
        label = f"{state}/unanswered"
        repo_budget = _target_repo_budget(path.name, label)
        display_repo = _truncate_target_repo(entry_repo, max_width=repo_budget)
        prefix = f"{path.name}: {display_repo} [{label}] "
        available_width = shutil.get_terminal_size().columns - _display_width(prefix)
        print(f"{prefix}{_tbd_body_summary(text, available_width)}", file=sys.stderr)


def _normalized_repo_or_none(value: str | None) -> str | None:
    """対象リポジトリを正規化する。解析できない値はNoneを返す。

    frontmatterが破損したエントリや対象リポジトリ未設定のエントリが1件でもあると
    キュー全体の読み込みが失敗するため、当該エントリだけを依存判定の対象外にする。
    """
    if not value:
        return None
    try:
        return _git_remote.normalize_remote_url(value)
    except ValueError:
        return None


def _count_pending_entries(
    private_notes: pathlib.Path,
    target_repo: str | None = None,
) -> int:
    """当該対象リポジトリでこのセッションが着手可能な項目数を返す。"""
    return calculate_readiness(private_notes, target_repo).actionable_count


def _load_queue_entries(
    private_notes: pathlib.Path,
    target_repo: str | None,
    states: tuple[str, ...],
) -> tuple[QueueEntry, ...]:
    """指定状態のfeedback・TBDをreadiness判定用表現へ変換する。"""
    entries: list[QueueEntry] = []
    for path, entry_repo, text, state, entry_type in _iter_entries(private_notes, states, target_repo, "all"):
        parsed = parse_frontmatter(text)
        frontmatter_broken = parsed is None
        data = parsed[0] if parsed is not None else {}
        repair_target = data.get("repair_target") if entry_type == MQ_TYPE_TBD else None
        raw_repair_kind = data.get("repair_kind") if entry_type == MQ_TYPE_TBD else None
        repair_kind: RepairKind | None
        if not isinstance(repair_target, str):
            repair_kind = None
        elif raw_repair_kind is None or raw_repair_kind == "frontmatter":
            repair_kind = "frontmatter"
        elif raw_repair_kind == "missing-plan-file":
            repair_kind = "missing-plan-file"
        else:
            repair_kind = None
        plan_file = data.get("plan_file")
        raw_dependencies = data.get("depends_on", [])
        depends_on = (
            tuple(dict.fromkeys(value for value in raw_dependencies if isinstance(value, str)))
            if isinstance(raw_dependencies, list) and all(isinstance(value, str) for value in raw_dependencies)
            else ()
        )
        schedule = data.get("queue_schedule")
        legacy_dependency = schedule.get("dependency") if isinstance(schedule, dict) else None
        normalized_repo = _normalized_repo_or_none(entry_repo)
        entries.append(
            QueueEntry(
                filename=path.name,
                text=text,
                kind=entry_type,
                state=state,
                target_repo=normalized_repo,
                tbd_answered=_is_tbd_answered(text) if entry_type == MQ_TYPE_TBD else None,
                frontmatter_broken=frontmatter_broken,
                plan_file=plan_file if isinstance(plan_file, str) else None,
                depends_on=depends_on,
                legacy_dependency=legacy_dependency if isinstance(legacy_dependency, dict) else None,
                repair_target_filename=repair_target if isinstance(repair_target, str) else None,
                repair_kind=repair_kind,
            )
        )
    return tuple(entries)


def _effective_dependencies(entry: QueueEntry) -> tuple[str, ...] | None:
    """トップレベル依存を返し、無い場合はlegacy依存を読み取る。"""
    parsed = parse_frontmatter(entry.text)
    if parsed is not None and "depends_on" in parsed[0]:
        raw = parsed[0]["depends_on"]
        if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
            return None
        return tuple(dict.fromkeys(value for value in raw if isinstance(value, str)))
    legacy = entry.legacy_dependency
    if legacy is None or legacy.get("kind") in (None, "none"):
        return ()
    kind = legacy.get("kind")
    if kind in ("entries", "external-repo-entry"):
        filenames = legacy.get("filenames")
        if (
            not isinstance(filenames, list)
            or not filenames
            or any(not isinstance(value, str) or not value for value in filenames)
        ):
            return None
        if kind == "external-repo-entry":
            target_repo = legacy.get("target_repo")
            if not isinstance(target_repo, str) or _normalized_repo_or_none(target_repo) is None:
                return None
        return tuple(dict.fromkeys(value for value in filenames if isinstance(value, str)))
    if kind == "external-user":
        filename = legacy.get("tbd_filename")
        condition = legacy.get("condition")
        return (filename,) if isinstance(filename, str) and filename and isinstance(condition, str) and condition else None
    if kind == "external-upstream":
        recheck_after = legacy.get("recheck_after")
        condition = legacy.get("condition")
        hold_reason = legacy.get("hold_reason")
        if (
            not all(isinstance(value, str) and value for value in (recheck_after, condition, hold_reason))
            or _parse_legacy_recheck_after(recheck_after) is None
        ):
            return None
        return ()
    if kind == "inbox-empty":
        return ()
    return None


def _parse_legacy_recheck_after(value: object) -> datetime.datetime | None:
    """legacy外部条件の再評価時刻をaware datetimeとして返す。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _legacy_dependency_is_satisfied(
    entry: QueueEntry,
    *,
    all_active: tuple[QueueEntry, ...],
    terminal: tuple[QueueEntry, ...],
    target_active: tuple[QueueEntry, ...],
    now: datetime.datetime,
) -> bool | None:
    """legacy固有依存の成立状態を返し、通常のfilename依存ではNoneを返す。"""
    parsed = parse_frontmatter(entry.text)
    if parsed is not None and "depends_on" in parsed[0]:
        return None
    legacy = entry.legacy_dependency
    if legacy is None:
        return None
    kind = legacy.get("kind")
    if kind in (None, "none", "entries", "external-user"):
        return None
    if kind == "external-upstream":
        recheck_after = _parse_legacy_recheck_after(legacy.get("recheck_after"))
        return recheck_after is not None and recheck_after <= now
    if kind == "inbox-empty":
        return not any(candidate.filename != entry.filename for candidate in target_active)
    if kind == "external-repo-entry":
        filenames = legacy.get("filenames")
        target_repo = legacy.get("target_repo")
        dependency_repo = _normalized_repo_or_none(target_repo if isinstance(target_repo, str) else None)
        if not isinstance(filenames, list) or any(not isinstance(value, str) for value in filenames) or dependency_repo is None:
            return False
        terminal_pairs = {(candidate.filename, candidate.target_repo) for candidate in terminal}
        active_pairs = {(candidate.filename, candidate.target_repo) for candidate in all_active}
        return all(
            (filename, dependency_repo) in terminal_pairs and (filename, dependency_repo) not in active_pairs
            for filename in filenames
        )
    return False


def _cycle_members(graph: dict[str, tuple[str, ...]]) -> set[str]:
    """依存グラフ内で循環に属する全項目名を返す。"""
    visiting: set[str] = set()
    visited: set[str] = set()
    cyclic: set[str] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in visiting:
            cyclic.update(path[path.index(node) :])
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, ()):
            if dependency in graph:
                visit(dependency, (*path, dependency))
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, (node,))
    return cyclic


def calculate_readiness(private_notes: pathlib.Path, target_repo: str | None) -> ReadinessResult:
    """最新frontmatterとキュー状態から対象リポジトリのreadinessを算出する。"""
    active = _load_queue_entries(private_notes, target_repo, MQ_ACTIVE_STATES)
    terminal = _load_queue_entries(private_notes, None, (MQ_STATE_ADOPTED, MQ_STATE_REJECTED))
    all_active = _load_queue_entries(private_notes, None, MQ_ACTIVE_STATES)
    existing_repairs = {
        (entry.repair_target_filename, entry.repair_kind)
        for entry in all_active
        if entry.kind == MQ_TYPE_TBD and entry.tbd_answered is False
    }
    active_by_name = {entry.filename: entry for entry in all_active}
    terminal_names = {entry.filename for entry in terminal}
    broken = tuple(sorted(entry.filename for entry in active if entry.frontmatter_broken))
    broken_needs_tbd = tuple(name for name in broken if (name, "frontmatter") not in existing_repairs)
    missing_plan = tuple(
        sorted(
            entry.filename for entry in active if entry.plan_file is not None and not pathlib.Path(entry.plan_file).is_file()
        )
    )
    missing_plan_needs_tbd = tuple(name for name in missing_plan if (name, "missing-plan-file") not in existing_repairs)

    dependency_map = {entry.filename: _effective_dependencies(entry) for entry in active if not entry.frontmatter_broken}
    all_dependency_map = {
        entry.filename: _effective_dependencies(entry) for entry in all_active if not entry.frontmatter_broken
    }
    invalid = tuple(sorted(name for name, dependencies in dependency_map.items() if dependencies is None))
    graph = {name: dependencies for name, dependencies in dependency_map.items() if dependencies is not None}
    self_dependencies = tuple(sorted(name for name, dependencies in graph.items() if name in dependencies))
    missing_dependencies = tuple(
        sorted(
            name
            for name, dependencies in graph.items()
            if any(dependency not in active_by_name and dependency not in terminal_names for dependency in dependencies)
        )
    )
    all_graph = {name: dependencies for name, dependencies in all_dependency_map.items() if dependencies is not None}
    cyclic = tuple(sorted(set(_cycle_members(all_graph)) & set(dependency_map)))
    permanently_blocked = set((*broken, *missing_plan, *invalid, *self_dependencies, *missing_dependencies, *cyclic))
    ready: list[str] = []
    blocked: list[str] = []
    now = datetime.datetime.now(datetime.UTC)
    for entry in active:
        if entry.filename in permanently_blocked or (entry.kind == MQ_TYPE_TBD and entry.tbd_answered is False):
            blocked.append(entry.filename)
            continue
        dependencies = graph.get(entry.filename, ())
        legacy_satisfied = _legacy_dependency_is_satisfied(
            entry,
            all_active=all_active,
            terminal=terminal,
            target_active=active,
            now=now,
        )
        waiting = legacy_satisfied is False or any(
            dependency in active_by_name
            and not (active_by_name[dependency].kind == MQ_TYPE_TBD and active_by_name[dependency].tbd_answered is True)
            for dependency in dependencies
        )
        if waiting:
            blocked.append(entry.filename)
        else:
            ready.append(entry.filename)
    return ReadinessResult(
        ready=tuple(sorted(ready)),
        blocked=tuple(sorted(blocked)),
        frontmatter_broken=broken,
        frontmatter_broken_needs_tbd=broken_needs_tbd,
        missing_plan_file=missing_plan,
        missing_plan_file_needs_tbd=missing_plan_needs_tbd,
        invalid_dependencies=invalid,
        missing_dependencies=missing_dependencies,
        self_dependencies=self_dependencies,
        cyclic_dependencies=cyclic,
    )


def _count_feedback(feedback_dir: pathlib.Path, target_repo: str | None = None) -> int:
    """指定ディレクトリ配下の`*.md`ファイル件数を返す。

    `target_repo`指定時はfrontmatterの`target_repo`が一致するエントリのみ数える。
    未指定時は全リポジトリ分を数える。
    """
    if not feedback_dir.exists():
        return 0
    if target_repo is None:
        return sum(1 for p in feedback_dir.iterdir() if p.suffix == ".md")
    return sum(1 for _ in _iter_inbox_entries(feedback_dir, target_repo))


def _max_existing_seq(private_notes: pathlib.Path, timestamp_prefix: str) -> int:
    """同一タイムスタンププレフィックスを持つファイルの最大連番を、4状態すべてから返す。

    例えば`{prefix}-001.md`と`{prefix}-003.md`が存在する場合は3を返す。
    非連続連番でも新規生成側で既存ファイルへ衝突しないよう最大値を基準にする。
    inboxのみを走査すると、同一秒に採番したエントリが別状態へ遷移した後の再投入で
    連番が再発行され、`adopted`・`rejected`等の既存エントリと同名衝突を起こすため、
    4状態フォルダすべてを走査対象にする。
    """
    max_seq = 0
    for state in MQ_STATES:
        state_dir = private_notes / state
        if not state_dir.exists():
            continue
        for p in state_dir.iterdir():
            if not p.name.startswith(f"{timestamp_prefix}-"):
                continue
            try:
                seq = int(p.stem.rsplit("-", 1)[-1])
            except ValueError:
                continue
            max_seq = max(max_seq, seq)
    return max_seq


def _resolve_repo_path_override(
    args_messages: list[str],
    pre_parse_override: str | None,
) -> tuple[list[str], str | None]:
    """旧REPO_PATH位置引数形式の呼び出しを解決する（`atk.py`の`_extract_legacy_repo_path`の後段）。

    `pre_parse_override`（サブコマンド名直後のトークンをargparse解析前に抽出した結果）が
    設定済みならそれを優先する。未設定の場合、argparseが単一のcontiguousな位置引数群として
    解決できたケース（REPO_PATHがオプションの後ろに置かれた呼び出し等）を対象に、
    messages先頭が実在ディレクトリなら追加でREPO_PATHとして抽出する。
    """
    messages = list(args_messages)
    if pre_parse_override is not None:
        return messages, pre_parse_override
    if not messages or not messages[0]:
        # 空文字列は`Path("").expanduser()`がカレントディレクトリ（常に実在）へ解決され、
        # 本文としての空メッセージ（TBDの空質問等）を誤ってREPO_PATHと誤認するため除外する。
        return messages, None
    candidate = pathlib.Path(messages[0]).expanduser()
    if not is_existing_dir(candidate):
        return messages, None
    return messages[1:], str(candidate)


def _reject_bare_repo_path_override(
    repo_path_override: str | None,
    messages: list[str],
    subparser: argparse.ArgumentParser,
) -> None:
    """先頭引数がディレクトリと解釈されたのに本文が続かない呼び出しをusage表示付きで拒否する。

    対象リポジトリは常にカレントディレクトリから自動判定する。ディレクトリらしき引数の後ろに
    本文が続かない呼び出しは誤指定とみなし、`subparser.error()`でargparse標準のusage行に
    続けて平易な文言を出力しexit 2する。
    """
    if repo_path_override is None or messages:
        return
    subparser.error(
        f"投入する本文の代わりにディレクトリパス（{repo_path_override}）が渡されました。"
        "対象リポジトリはカレントディレクトリから自動判定されるため、パスの指定は不要です。"
    )


def _collect_message_via_editor() -> str | None:
    """$EDITORで一時ファイルを開き、保存内容をstripして返す。

    $EDITOR未設定・エディター非ゼロ終了・保存内容が空のいずれもNoneを返し、
    原因をstderrへ出力する。一時ファイルは終了時に必ず削除する。
    """
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のためエディター経路を利用できません。", file=sys.stderr)
        return None
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as f:
        tmp_path = pathlib.Path(f.name)
    try:
        result = subprocess.run([editor, str(tmp_path)], check=False)
        if result.returncode != 0:
            print(f"エディターが終了コード{result.returncode}で終了しました。", file=sys.stderr)
            return None
        message = tmp_path.read_text(encoding="utf-8").strip()
        if not message:
            print("本文が空のため投入を中止しました。", file=sys.stderr)
            return None
        return message
    finally:
        tmp_path.unlink(missing_ok=True)


class WebInputError(ValueError):
    """Web APIへ安全に公開できる入力エラー。"""


def ensure_environment(home: pathlib.Path) -> pathlib.Path:
    """private-notes環境を検証してパスを返す。"""
    return _ensure_environment(home)


def pull(private_notes: pathlib.Path) -> None:
    """リポジトリをfast-forward更新する。"""
    _pull(private_notes)


def pull_if_stale(private_notes: pathlib.Path) -> bool:
    """定期更新が必要ならpullし、実行したかを返す。

    定期バックグラウンド更新専用とする。利用者の操作に対応する経路
    （変更操作・明示的な同期要求）は`pull`を用い、毎回リモートの最新状態を取得する。
    """
    _assert_repo_lock_held(private_notes)
    if _pulled_recently(private_notes):
        return False
    _pull(private_notes)
    return True


def _pulled_recently(private_notes: pathlib.Path) -> bool:
    """直近のpullから`_PULL_MIN_INTERVAL_SECONDS`未満かを返す。

    `.git`がファイルの場合（worktree形式）は`stat`が失敗し偽を返すため、
    レート制限が無効化されてpullを実行する側へ倒れる。
    フィードバック保存リポジトリは通常のクローンであり該当しない。
    """
    fetch_head = private_notes / ".git" / "FETCH_HEAD"
    try:
        elapsed = time.time() - fetch_head.stat().st_mtime
    except OSError:
        return False
    return elapsed < _PULL_MIN_INTERVAL_SECONDS


def repo_lock(private_notes: pathlib.Path, *, timeout: float = -1) -> filelock.FileLock:
    """private-notesの排他ロックを返す。"""
    return _repo_lock(private_notes, timeout=timeout)


def commit_and_push(private_notes: pathlib.Path, message: str, rel_paths: Iterable[str]) -> None:
    """指定差分をcommitしてpushする。"""
    _commit_and_push(private_notes, message, rel_paths)


def is_tbd_answered(text: str) -> bool:
    """TBD本文が回答済みか判定する。"""
    return _is_tbd_answered(text)


def count_pending_entries(
    private_notes: pathlib.Path,
    target_repo: str | None = None,
) -> int:
    """当該対象リポジトリでこのセッションが着手可能な項目数を返す。"""
    return _count_pending_entries(private_notes, target_repo)


def entry_type_of(path: pathlib.Path, text: str) -> str | None:
    """エントリの種別を返す。frontmatter全体が破損している場合はNone。"""
    return _require_type(path, text)


def iter_entries(
    private_notes: pathlib.Path,
    states: Iterable[str],
    filter_repo: str | None,
    entry_type: str = "all",
) -> Iterator[tuple[pathlib.Path, str, str, str, str | None]]:
    """指定状態のエントリをパス・対象repo・本文・状態・種別の順で列挙する。"""
    return _iter_entries(private_notes, states, filter_repo, entry_type)


def validate_filename(filename: str, base_dir: pathlib.Path) -> pathlib.Path:
    """basenameのMarkdownファイル名を検証し、許可ディレクトリ内へ解決する。"""
    try:
        return _validate_filename(filename, base_dir)
    except SystemExit as error:
        raise WebInputError(f"不正なファイル名です: {filename}") from error


def validate_filenames(filenames: list[str], base_dir: pathlib.Path) -> list[pathlib.Path]:
    """複数ファイル名を全件検証してから解決結果を返す。"""
    if not filenames:
        raise WebInputError("filenamesには1件以上を指定してください")
    return [validate_filename(filename, base_dir) for filename in filenames]


def private_notes_path(home: pathlib.Path) -> pathlib.Path:
    """フィードバック管理repoのrootパスを返す（公開ラッパー）。"""
    return _private_notes_path(home)
