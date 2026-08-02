"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_repo.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import pathlib
import re
import subprocess
import sys
import typing

import _git_remote
from _atk_mq_common import (
    _commit_and_push,
    _normalize_md_filename,
    _parse_type,
    _pull,
    _repo_lock,
    _require_type,
    _validate_filename,
)
from _atk_mq_formatters import _parse_target_repo


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
    - `value`が実在しないパスやURL文字列なら「ローカルパスが必要」旨をstderrへ出力してexit 2
    - `value`省略時は`git rev-parse --show-toplevel`の出力を返す。失敗時もexit 2
    """
    if value is not None:
        local_path = pathlib.Path(value).expanduser()
        if not local_path.exists():
            print(
                f"ローカルパスとして存在しません（URLではなくローカルパスを指定してください）: {value}",
                file=sys.stderr,
            )
            sys.exit(2)
        return local_path.resolve()

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("git rev-parse --show-toplevel が失敗しました。gitリポジトリ内で実行してください。", file=sys.stderr)
        sys.exit(2)
    return pathlib.Path(result.stdout.strip())


def _resolve_repo_id(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
    """リポジトリ識別子（正規化リモートURL）を解決して返す。

    - `value`がURLらしい文字列（スキームを持つ・`@`を含む・スラッシュ2個以上の3要素）なら直接正規化する
    - ローカルパスとして判定した場合は`git -C <path> remote get-url origin`の出力を正規化する
    - `value`省略時は`cwd`（省略時は`_resolve_local_worktree`で取得した作業ツリー）を使う
    - パス不在・git未管理・remote未設定はexit 2で原因を標準エラー出力へ書く
    """
    if value is not None:
        # ローカルパスとして実在すればremote URLを取得して正規化、それ以外はURL文字列として正規化を試みる
        local_path = pathlib.Path(value).expanduser()
        if local_path.exists():
            local_path = local_path.resolve()
            result = subprocess.run(
                ["git", "-C", str(local_path), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                print(
                    f"リモートURLを取得できませんでした（git remote get-url origin）: {local_path}",
                    file=sys.stderr,
                )
                sys.exit(2)
            try:
                return _normalize_remote_url(result.stdout.strip())
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                sys.exit(2)
        try:
            return _normalize_remote_url(value)
        except ValueError:
            print(
                f"パスが存在せずリモートURLとしても解析できません: {value}",
                file=sys.stderr,
            )
            sys.exit(2)

    # value省略時: ローカル作業ツリーを特定してからremoteを取得
    if cwd is None:
        cwd = _resolve_local_worktree(None)
    result = subprocess.run(
        ["git", "-C", str(cwd), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"リモートURLを取得できませんでした（git remote get-url origin）: {cwd}",
            file=sys.stderr,
        )
        sys.exit(2)
    remote_url = result.stdout.strip()
    try:
        return _normalize_remote_url(remote_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)


def resolve_repo_id(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
    """CLIとWeb APIで共有するリポジトリ識別子を解決する。"""
    return _resolve_repo_id(value, cwd=cwd)


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
            check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            print(f"ローカルworktreeではありません: {local_worktree}", file=sys.stderr)
            sys.exit(2)
        return _resolve_repo_id(str(local_worktree)), local_worktree

    return _resolve_repo_id(value), None


def resolve_head_commit(local_worktree: pathlib.Path) -> str:
    """ローカルworktreeのHEADを完全OIDとして返す。"""
    result = subprocess.run(
        ["git", "-C", str(local_worktree), "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip()
        suffix = f": {detail}" if detail else ""
        print(f"HEADコミットを取得できませんでした（git rev-parse --verify HEAD^{{commit}}）{suffix}", file=sys.stderr)
        sys.exit(2)
    commit = result.stdout.strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        print(f"HEADコミットが完全OIDではありません: {commit!r}", file=sys.stderr)
        sys.exit(2)
    return commit


def _verify_frontmatter_target_repo(
    filename: str,
    inbox_paths: list[pathlib.Path],
    expected: str | None,
) -> None:
    """filenameのfrontmatter`target_repo`が`expected`と一致するか検証する。

    `expected`が`None`（`--target-repo`未指定）ならno-op。`inbox_paths`は先頭から順に
    実在確認し、最初に見つかった候補のfrontmatterのみ検証する。frontmatterに
    `target_repo`が無い場合、および正規化後の値が`expected`と不一致の場合はexit 2。
    候補がいずれのパスにも存在しない場合は後続の存在検証に委ねてno-opとする。
    `filename`は拡張子`.md`省略入力も`_validate_filename`と同一規約で正規化してから解決する。
    """
    if expected is None:
        return
    filename = _normalize_md_filename(filename)
    normalized_expected = _resolve_repo_id(expected)
    for base_dir in inbox_paths:
        candidate = base_dir / filename
        if not candidate.exists():
            continue
        text = candidate.read_text(encoding="utf-8")
        actual = _parse_target_repo(text)
        if actual == "(unknown)":
            print(f"frontmatterにtarget_repoがありません: {candidate}", file=sys.stderr)
            sys.exit(2)
        normalized_actual = _normalize_remote_url(actual)
        if normalized_actual != normalized_expected:
            print(
                f"target_repo不一致: 期待={normalized_expected} 実際={normalized_actual} ファイル={candidate}",
                file=sys.stderr,
            )
            sys.exit(2)
        return


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
) -> bool:
    """feedback・TBD共通の平引数編集操作。ロック内でpull・検証・書込み・commitまでを完結する。

    `_atk_mq_mutations.edit_entry_content`が呼び出す。
    編集後の本文frontmatterの`type`が編集前から変更・欠落していないかも検証する
    （`_verify_frontmatter_target_repo`と同じくexit 2で拒否する。種別は平坦化後の唯一の
    分類情報であり、編集で書き換わると一覧・集計から静かに脱落するため）。
    """
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        _verify_frontmatter_target_repo(filename, [directory], target_repo)
        path = _validate_filename(filename, directory)
        if not path.is_file():
            raise FileNotFoundError(filename)
        previous = path.read_text(encoding="utf-8")
        if expected_content is not None and previous != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        if content_validator is not None:
            content_validator(previous, content)
        if content_transformer is not None:
            content = content_transformer(previous, content)
        if content_validator is not None:
            content_validator(previous, content)
        if previous == content:
            return False
        previous_type = _require_type(path, previous)
        new_type = _parse_type(content)
        if new_type != previous_type:
            print(
                f"typeを変更または欠落させることはできません（現在値: {previous_type}）: {filename}",
                file=sys.stderr,
            )
            sys.exit(2)
        path.write_text(content, encoding="utf-8")
        _commit_and_push(private_notes, commit_message, [str(path.relative_to(private_notes))])
    return True
