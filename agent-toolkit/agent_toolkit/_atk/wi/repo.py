"""agent-toolkitプラグイン配下の`atk wi`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_repo.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import pathlib
import re
import subprocess
import sys
import typing

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi.common import (
    WebInputError,
    _commit_and_push,
    _ensure_mutation_allowed,
    _parse_type,
    _pull,
    _push_pending_commits,
    _repo_lock,
    _require_type,
    _validate_filename,
    web_input_error_from,
)
from agent_toolkit._atk.wi.formatters import _parse_target_repo
from agent_toolkit._git import remote as _git_remote

TARGET_REPO_ALL = "all"
"""`--target-repo`へ指定すると対象リポジトリを限定しない値。"""

_TARGET_REPO_NEXT_ACTION = "`--target-repo`へローカルworktreeのパスかremote URLを指定して再実行する"


def _normalize_remote_url(url: str) -> str:
    """リモートURLを`host/owner/repo`形式（またはネスト配下`host/group/.../repo`）へ正規化して返す。

    HTTPS形式・SSH短縮形式・SSH URI形式・既に正規化済みの`host/path...`形式（`host`直下に
    2要素以上の`/`区切りパスを持つ）の4種を受理する。ネスト配下のリポジトリ（GitLabサブグループ等）も
    含む。受理外はValueErrorを送出する。出力は全体小文字化し`.git`サフィックスを除去する。
    """
    return _git_remote.normalize_remote_url(url)


def _resolve_local_worktree(value: str | None) -> pathlib.Path:
    """ローカル作業ツリーのパスを解決して返す。

    - `value`が実在するローカルパスなら`expanduser().resolve()`した結果を返す
    - `value`が実在しないパスやURL文字列なら、実在するローカルパスの指定を求めるエラーをstderrへ出力してexit 2
    - `value`省略時は`git rev-parse --show-toplevel`の出力を返す。失敗時もexit 2
    """
    if value is not None:
        local_path = pathlib.Path(value).expanduser()
        if not local_path.exists():
            _outcome.report_failure(
                f"ローカルパスとして存在しない: {value}", next_action="URLではなく実在するローカルパスを指定する"
            )
            sys.exit(2)
        return local_path.resolve()

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        _outcome.report_failure(
            "git rev-parse --show-toplevel が失敗した",
            next_action=f"gitリポジトリ内で実行するか、{_TARGET_REPO_NEXT_ACTION}",
        )
        sys.exit(2)
    return pathlib.Path(result.stdout.strip())


def _origin_url(worktree: pathlib.Path) -> str:
    """作業ツリーのoriginのURLを返す。取得できない場合は設定手順を次の操作とする例外を送出する。"""
    result = subprocess.run(
        ["git", "-C", str(worktree), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise WebInputError(
            f"リモートURLを取得できない（git remote get-url origin）: {worktree}",
            next_action=f"`git -C {worktree} remote add origin <URL>`でoriginを設定するか、{_TARGET_REPO_NEXT_ACTION}",
        )
    return result.stdout.strip()


def resolve_repo_id_or_raise(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
    """リポジトリ識別子（正規化リモートURL）を解決して返す。

    - `value`がURLらしい文字列（スキームを持つ・`@`を含む・スラッシュ2個以上の3要素）なら直接正規化する
    - ローカルパスとして判定した場合は`git -C <path> remote get-url origin`の出力を正規化する
    - `value`省略時は`cwd`（省略時は`_resolve_local_worktree`で取得した作業ツリー）を使う
    - パス不在・git未管理・remote未設定は理由と次の操作を持つ`WebInputError`を送出する。
      呼び出し元が自分の失敗行へ包めるよう、ここでは出力しない
    """
    if value is not None:
        # ローカルパスとして実在すればremote URLを取得して正規化、それ以外はURL文字列として正規化を試みる
        local_path = pathlib.Path(value).expanduser()
        if local_path.exists():
            local_path = local_path.resolve()
            try:
                return _normalize_remote_url(_origin_url(local_path))
            except ValueError as exc:
                raise web_input_error_from(exc, next_action=_TARGET_REPO_NEXT_ACTION) from exc
        try:
            return _normalize_remote_url(value)
        except ValueError as exc:
            raise WebInputError(
                f"パスが存在せずリモートURLとしても解析できない: {value}", next_action=_TARGET_REPO_NEXT_ACTION
            ) from exc

    # value省略時: ローカル作業ツリーを特定してからremoteを取得
    if cwd is None:
        cwd = _resolve_local_worktree(None)
    try:
        return _normalize_remote_url(_origin_url(cwd))
    except ValueError as exc:
        raise web_input_error_from(exc, next_action=_TARGET_REPO_NEXT_ACTION) from exc


def _resolve_repo_id(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
    """リポジトリ識別子を解決して返す。解決できない場合は失敗行と次の操作を書いてexit 2で終了する。"""
    try:
        return resolve_repo_id_or_raise(value, cwd=cwd)
    except WebInputError as error:
        _outcome.report_failure(error.reason, next_action=error.next_action)
        sys.exit(2)


def resolve_repo_id(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
    """CLIとWeb APIで共有するリポジトリ識別子を解決する。"""
    return _resolve_repo_id(value, cwd=cwd)


def detect_current_repo_id() -> str | None:
    """カレントディレクトリが属するリポジトリの識別子を返し、解決できない場合はNoneを返す。

    `--target-repo`を省略したときに使う値とする。Gitの作業ツリー外、`origin`未設定、
    リモートURLを正規化できない形式のいずれでもNoneを返す。Noneを受け取った呼び出し元は
    対象を限定せず、全ての対象リポジトリを扱う。
    """
    toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if toplevel.returncode != 0:
        return None
    remote = subprocess.run(
        ["git", "-C", toplevel.stdout.strip(), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if remote.returncode != 0:
        return None
    try:
        return _normalize_remote_url(remote.stdout.strip())
    except ValueError:
        return None


def resolve_add_target(value: str | None) -> tuple[str, pathlib.Path | None]:
    """投入先のリポジトリ識別子と、特定できたローカルworktreeを返す。"""
    if value is None:
        local_worktree = _resolve_local_worktree(None)
        return _resolve_repo_id(None, cwd=local_worktree), local_worktree

    local_path = pathlib.Path(value).expanduser()
    if local_path.exists():
        local_worktree = local_path.resolve()
        result = subprocess.run(
            ["git", "-C", str(local_worktree), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            _outcome.report_failure(
                f"ローカルworktreeではない: {local_worktree}", next_action="Gitの作業ツリーのパスを指定して再実行する"
            )
            sys.exit(2)
        return _resolve_repo_id(str(local_worktree)), local_worktree

    return _resolve_repo_id(value), None


def resolve_head_commit(local_worktree: pathlib.Path) -> str:
    """ローカルworktreeのHEADを40桁または64桁OIDとして返す。"""
    result = subprocess.run(
        ["git", "-C", str(local_worktree), "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip()
        suffix = f": {detail}" if detail else ""
        _outcome.report_failure(
            f"HEADコミットを取得できない（git rev-parse --verify HEAD^{{commit}}）{suffix}",
            next_action=(
                f"`git -C {local_worktree} log -1`でコミットが1件以上あるか確認し、無い場合は最初のコミットを作成してから"
                "再実行する"
            ),
        )
        sys.exit(2)
    commit = result.stdout.strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        _outcome.report_failure(
            f"HEADコミットが40桁または64桁OIDではない: {commit!r}",
            next_action=f"`git -C {local_worktree} rev-parse HEAD`の出力を確認し、解消しない場合はユーザーへ報告する",
        )
        sys.exit(2)
    return commit


def _verify_target_repo_content(path: pathlib.Path, content: str, normalized_expected: str | None) -> None:
    """解決済み実体の`target_repo`が正規化済みの期待値と一致するかを確かめる。"""
    if normalized_expected is None:
        return
    actual = _parse_target_repo(content)
    if actual == "(unknown)":
        _outcome.report_failure(
            f"frontmatterにtarget_repoが無い: {path}",
            next_action=f"`atk wi edit {path.name}`でtarget_repoを追記する。直せない場合はユーザーへ報告する",
        )
        sys.exit(2)
    normalized_actual = _git_remote.resolve_repo_identifier(actual)
    if normalized_actual != normalized_expected:
        _outcome.report_failure(
            f"target_repoが一致しない: 期待={normalized_expected} 実際={normalized_actual} ファイル={path}",
            next_action=f"実際の値で`--target-repo={normalized_actual}`を指定し直すか、別リポジトリの項目か確認する",
        )
        sys.exit(2)


def edit_entry(
    private_notes: pathlib.Path,
    *,
    directory: pathlib.Path,
    filename: str,
    content: str,
    target_repo: str | None,
    lock_timeout: float,
    expected_content: str | None,
    commit_message: str,
    content_validator: typing.Callable[[str, str], None] | None = None,
    content_transformer: typing.Callable[[str, str], str] | None = None,
    finalized_content: dict[str, str] | None = None,
    skip_remote_sync: bool = False,
) -> bool:
    """AWI・UWI共通の平引数編集操作。ロック内でpull・検証・書込み・commitまでを完結する。

    `_atk_wi_mutations.edit_entry_content`が呼び出す。
    編集後の本文frontmatterの`type`が編集前から変更・欠落していないかも検証する
    （`_verify_target_repo_content`と同じくexit 2で拒否する。種別は平坦化後の唯一の
    分類情報であり、編集で書き換わると一覧・集計から静かに脱落するため）。
    `finalized_content`を渡した場合は、変換後の確定本文を`content`キーへ格納する。
    呼び出し元が保存本文との一致判定へ用いる。
    """
    with _repo_lock(private_notes, timeout=lock_timeout):
        _ensure_mutation_allowed(private_notes)
        if not skip_remote_sync:
            _push_pending_commits(private_notes)
            _pull(private_notes)
        path = _validate_filename(filename, directory)
        if not path.is_file():
            raise FileNotFoundError(filename)
        previous = _frontmatter.decode_entry_text(path.read_bytes())
        content = _frontmatter.normalize_newlines(content)
        normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None
        _verify_target_repo_content(path, previous, normalized_target_repo)
        if expected_content is not None and previous != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        if content_validator is not None:
            content_validator(previous, content)
        if content_transformer is not None:
            content = content_transformer(previous, content)
        if content_validator is not None:
            content_validator(previous, content)
        if finalized_content is not None:
            finalized_content["content"] = content
        if previous == content:
            return False
        previous_type = _require_type(path, previous)
        new_type = _parse_type(content)
        if new_type != previous_type:
            _outcome.report_failure(
                f"typeは変更も欠落もできない（現在値: {previous_type}）: {filename}",
                next_action="frontmatterのtypeを元の値へ戻して再実行する",
            )
            sys.exit(2)
        _frontmatter.write_entry_text(path, content)
        _commit_and_push(private_notes, commit_message, [str(path.relative_to(private_notes))], skip_push=skip_remote_sync)
    return True


def append_entry(
    private_notes: pathlib.Path,
    *,
    directory: pathlib.Path,
    filename: str,
    content: bytes,
    target_repo: str | None,
    lock_timeout: float,
    expected_content: bytes | None,
    commit_message: str,
    content_validator: typing.Callable[[str, str], None] | None = None,
    finalized_content: dict[str, str] | None = None,
) -> bool:
    """AWI本文をraw bytesのまま追記し、競合を検出してcommitまで行う。

    `finalized_content`を渡した場合は、追記後の確定本文を`content`キーへ格納する。
    """
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        path = _validate_filename(filename, directory)
        if not path.is_file():
            raise FileNotFoundError(filename)
        previous_bytes = path.read_bytes()
        previous = _frontmatter.decode_entry_text(previous_bytes)
        updated = _frontmatter.decode_entry_text(content)
        normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None
        _verify_target_repo_content(path, previous, normalized_target_repo)
        if expected_content is not None and previous_bytes != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        if content_validator is not None:
            content_validator(previous, updated)
        if finalized_content is not None:
            finalized_content["content"] = updated
        if previous_bytes == content:
            return False
        previous_type = _require_type(path, previous)
        new_type = _parse_type(updated)
        if new_type != previous_type:
            _outcome.report_failure(
                f"typeは変更も欠落もできない（現在値: {previous_type}）: {filename}",
                next_action="frontmatterのtypeを元の値へ戻して再実行する",
            )
            sys.exit(2)
        path.write_bytes(content)
        _commit_and_push(private_notes, commit_message, [str(path.relative_to(private_notes))])
    return True
