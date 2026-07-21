"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_common.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。

不変条件: フィードバック保存リポジトリ（`private_notes`）へのgit操作・ファイル変更は、
`_repo_lock(private_notes)`保持下でのみ行う。複数プロセスが同一クローンへ並行アクセスする
運用（`atk fb process-loop`の複数常駐等）を前提とし、当該不変条件を破ると
pullとファイル操作・commitの交錯によるfast-forward失敗を招く。
"""

import argparse
import datetime
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator

import filelock
import platformdirs
from _atk_fb_formatters import _display_width, _parse_target_repo, _tbd_body_summary

# フィードバック管理repoの4状態フォルダ名（`feedback/<name>`直下）。
# - `inbox`: 未処理の投入直後
# - `processing`: `start-processing`で処理中に移動された途中状態
# - `adopted`: 採用として最終処理された状態
# - `rejected`: 不採用として最終処理された状態
FEEDBACK_STATE_INBOX = "inbox"
FEEDBACK_STATE_PROCESSING = "processing"
FEEDBACK_STATE_ADOPTED = "adopted"
FEEDBACK_STATE_REJECTED = "rejected"

_SPACE_SEPARATED_OPTION_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "fb": frozenset(("adopt", "reject")),
    "tb": frozenset(("adopt",)),
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
    for cmd in ("fb", "tb"):
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
    """feedback/配下の指定サブディレクトリパスを返す。必要時に作成する。"""
    path = private_notes / "feedback" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _flag_path(home: pathlib.Path) -> pathlib.Path:
    """feedback-inboxの有効化フラグファイルの絶対パスを返す。"""
    return home / ".config" / "agent-toolkit" / "feedback-inbox.enabled"


def _private_notes_path(home: pathlib.Path) -> pathlib.Path:
    """フィードバック保存ディレクトリのroot絶対パスを返す。

    環境変数`AGENT_TOOLKIT_PRIVATE_NOTES`が設定されていれば当該値を優先し、
    未設定時は`~/private-notes/`へフォールバックする。
    """
    override = os.environ.get("AGENT_TOOLKIT_PRIVATE_NOTES")
    if override:
        return pathlib.Path(override).expanduser()
    return home / "private-notes"


def _check_environment(home: pathlib.Path) -> tuple[int, str]:
    """feedback-inboxの有効状態を判定し、(exit_code, message)を返す。

    正常時は(0, 有効案内)、フラグファイル不在・フィードバック保存ディレクトリ不在時は(1, 原因案内)。
    """
    if not _flag_path(home).exists():
        return 1, "feedback-inbox機能が無効です（フラグファイルが存在しません）。"
    root = _private_notes_path(home)
    if not root.exists():
        return 1, f"フィードバック保存ディレクトリが見つかりません: {root}"
    return 0, "feedback-inboxは有効です。"


def _ensure_environment(home: pathlib.Path) -> pathlib.Path:
    """フラグファイルとフィードバック保存ディレクトリの存在を確認し、rootパスを返す。"""
    code, message = _check_environment(home)
    if code != 0:
        print(message, file=sys.stderr)
        sys.exit(code)
    return _private_notes_path(home)


def _run_git(args: list[str], cwd: pathlib.Path) -> None:
    """gitコマンドをcwdで実行し、失敗時は例外を送出する。"""
    subprocess.run(["git", *args], cwd=cwd, check=True)


def _pull(private_notes: pathlib.Path) -> None:
    """フィードバック保存リポジトリで`git pull --ff-only`を実行する。

    不変条件表明: `_repo_lock`保持下でのみ呼び出す。
    """
    _assert_repo_lock_held(private_notes)
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


def _repo_lock_path(private_notes: pathlib.Path) -> pathlib.Path:
    """`private_notes`に対応するロックファイルの絶対パスを返す。

    配置先は`platformdirs.user_state_dir("agent-toolkit")`配下`locks/`ディレクトリとし、
    ファイル名は`private_notes.resolve()`のSHA-1ハッシュ値とする（`.git/`配下を選択しない理由は
    計画の`### 却下した代替案`参照）。取得時にロック用ディレクトリを自動作成する。
    """
    resolved = str(private_notes.resolve())
    digest = hashlib.sha1(resolved.encode("utf-8"), usedforsecurity=False).hexdigest()
    lock_dir = pathlib.Path(platformdirs.user_state_dir("agent-toolkit")) / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{digest}.lock"


class _RepoLock(filelock.FileLock):
    """`_repo_lock`が返すロック。保持区間を`_LOCK_HELD_PATHS`へ登録・解除する。"""

    def __init__(self, private_notes: pathlib.Path) -> None:
        self._target = private_notes.resolve()
        super().__init__(str(_repo_lock_path(private_notes)))

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


def _repo_lock(private_notes: pathlib.Path) -> filelock.FileLock:
    """フィードバック保存リポジトリのgit操作・ファイル変更を排他するプロセス間ロックを返す。

    `filelock.FileLock`は同一インスタンス内で再入可能（スレッドローカル＋カウンタ管理）だが、
    本計画のロック区間分割設計では同一関数内のネスト`with`は発生しない。
    タイムアウトは指定せず、取得できるまで無期限に待機する
    （常駐ループはclaudeセッション実行中にロックを保持しない設計であり、
    臨界区間はgit操作前後の短時間に限るため）。
    """
    return _RepoLock(private_notes)


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
    """
    _assert_repo_lock_held(private_notes)
    rel_list = list(rel_paths)
    _run_git(["add", *rel_list], cwd=private_notes)
    _run_git(["commit", "-m", message], cwd=private_notes)
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


def _edit_and_commit_via_editor(
    private_notes: pathlib.Path,
    path: pathlib.Path,
    snapshot: bytes,
    *,
    editor: str,
    commit_message: str,
    retry_hint: str,
) -> None:
    """スナップショット取得済みの`path`を`$EDITOR`で編集し、差分があれば競合検知のうえcommit・pushする。

    呼び出し側は`_repo_lock`保持下でpull・frontmatter検証・対象存在確認・
    スナップショット取得（`path.read_bytes()`）まで完了させたうえで本関数を呼び出す。
    本関数はエディタ起動（対話的入力を待つためロック非保持）から、再ロック内での
    他プロセスによる競合変更検知・書き込み・commit・pushまでを担う。
    `fb edit`（`_atk_fb_mutations._cmd_edit`）と`tb edit`（`_atk_fb_tbd._cmd_tbd_edit`）が
    同一の編集ワークフローを必要とするため集約する。
    `retry_hint`には競合検知時の再実行案内文（例:「再度atk fb editを実行してください。」）を渡す。
    """
    tmp_path = _copy_to_tempfile(snapshot)
    subprocess.run([editor, str(tmp_path)], check=True)
    edited = tmp_path.read_bytes()
    if edited == snapshot:
        tmp_path.unlink(missing_ok=True)
        print("差分なし。")
        return
    with _repo_lock(private_notes):
        _pull(private_notes)
        if not path.exists() or path.read_bytes() != snapshot:
            print(
                f"編集中に他プロセスが対象を変更しました: {path.name}。編集内容は{tmp_path}に残しています。{retry_hint}",
                file=sys.stderr,
            )
            sys.exit(1)
        path.write_bytes(edited)
        rel = str(path.relative_to(private_notes))
        _commit_and_push(private_notes, commit_message, [rel])
    tmp_path.unlink(missing_ok=True)
    print(f"編集反映: {path.name}")


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

    outcomeは`adopted`・`rejected`・`tbd-adopted`のいずれかを受け取る。
    commit・note・categoryは省略可能で、指定時のみ対応する箇条書き項目を追加する。
    categoryは採用フィードバックの再発防止分類ラベルを受け取る。
    値は`atk fb adopt --category`由来とする。
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


def _validate_filename(filename: str, base_dir: pathlib.Path) -> pathlib.Path:
    r"""ファイル名が基準ディレクトリ直下の単純名であることを検証して絶対パスを返す。

    `/`・`\`・`..`・絶対パス・空文字列・カレント参照は早期に拒否する。
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


# `--status=active`が指す集合: feedbackは`inbox`・`processing`、tbdは`answered`。
FEEDBACK_ACTIVE_STATES = (FEEDBACK_STATE_INBOX, FEEDBACK_STATE_PROCESSING)


def _iter_feedback_entries_with_state(
    private_notes: pathlib.Path,
    states: Iterable[str],
    filter_repo: str | None,
) -> Iterator[tuple[pathlib.Path, str, str, str]]:
    """指定状態フォルダのfeedbackエントリを`(path, target_repo, text, state)`形式で列挙する。"""
    for state in states:
        state_dir = private_notes / "feedback" / state
        for path, target_repo, text in _iter_inbox_entries(state_dir, filter_repo):
            yield path, target_repo, text, state


def _is_tbd_answered(text: str) -> bool:
    """TBD本文の`## 回答`節にHTMLコメント以外の非空内容があれば真。"""
    marker = "\n## 回答\n"
    idx = text.find(marker)
    if idx < 0:
        return False
    body = text[idx + len(marker) :]
    next_h2 = body.find("\n## ")
    if next_h2 >= 0:
        body = body[:next_h2]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        return True
    return False


def notify_unanswered_tbds_if_any(private_notes: pathlib.Path, target_repo: str | None) -> None:
    """未回答TBDが存在する場合に種別ヘッダ付きの1件1行形式で通知する。"""
    tbd_dir = private_notes / "tbd" / FEEDBACK_STATE_INBOX
    entries = [
        (path, entry_repo, text)
        for path, entry_repo, text in _iter_inbox_entries(tbd_dir, target_repo)
        if not _is_tbd_answered(text)
    ]
    if not entries:
        return
    print("# tbd", file=sys.stderr)
    for path, entry_repo, text in entries:
        prefix = f"{path.name}: {entry_repo} [unanswered] "
        available_width = shutil.get_terminal_size().columns - _display_width(prefix)
        print(f"{prefix}{_tbd_body_summary(text, available_width)}", file=sys.stderr)


def _count_pending_entries(
    private_notes: pathlib.Path,
    target_repo: str | None = None,
) -> int:
    """`process-loop`常駐ループ専用: feedback件数とTBD回答済み件数の合計を返す。

    `--type`・`--status`フィルタは持たず、常駐ループの反復判定に必要な合計のみを返す
    （`_list.py`の`_cmd_list`が持つフィルタ分岐との共通化は行わない）。
    """
    # inbox・processingの両状態を未処理として合算する（`start-processing`で移動済みの
    # 途中状態は`adopt`・`reject`未完了のため次反復での再処理対象に含める）。
    feedback_inbox = private_notes / "feedback" / FEEDBACK_STATE_INBOX
    feedback_processing = private_notes / "feedback" / FEEDBACK_STATE_PROCESSING
    feedback_count = sum(1 for _ in _iter_inbox_entries(feedback_inbox, target_repo)) + sum(
        1 for _ in _iter_inbox_entries(feedback_processing, target_repo)
    )
    tbd_dir = private_notes / "tbd" / FEEDBACK_STATE_INBOX
    tbd_count = sum(1 for _, _, text in _iter_inbox_entries(tbd_dir, target_repo) if _is_tbd_answered(text))
    return feedback_count + tbd_count


def _count_feedback(feedback_dir: pathlib.Path) -> int:
    """指定ディレクトリ配下の`*.md`ファイル件数を返す。"""
    if not feedback_dir.exists():
        return 0
    return sum(1 for p in feedback_dir.iterdir() if p.suffix == ".md")


def _max_existing_seq(feedback_dir: pathlib.Path, timestamp_prefix: str) -> int:
    """同一タイムスタンププレフィックスを持つinboxファイルの最大連番を返す。

    例えば`{prefix}-001.md`と`{prefix}-003.md`が存在する場合は3を返す。
    非連続連番でも新規生成側で既存ファイルへ衝突しないよう最大値を基準にする。
    """
    if not feedback_dir.exists():
        return 0
    max_seq = 0
    for p in feedback_dir.iterdir():
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
    if not messages:
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
