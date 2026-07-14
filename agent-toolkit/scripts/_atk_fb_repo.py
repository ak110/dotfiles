"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_repo.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import pathlib
import re
import subprocess
import sys

from _atk_fb_formatters import _parse_target_repo


def _normalize_remote_url(url: str) -> str:
    """リモートURLを`host/owner/repo`形式（またはネスト配下`host/group/.../repo`）へ正規化して返す。

    HTTPS形式・SSH短縮形式・SSH URI形式・既に正規化済みの`host/path...`形式（`host`直下に
    2要素以上の`/`区切りパスを持つ）の4種を受理する。ネスト配下のリポジトリ（GitLabサブグループ等）も
    含む。受理外はValueErrorを送出する。出力は全体小文字化し`.git`サフィックスを除去する。
    """
    # HTTPS: https://github.com/owner/repo[.git]
    m = re.match(r"https?://([^/:]+)/(.+)", url)
    if m:
        host = m.group(1)
        path = m.group(2)
        path = re.sub(r"\.git$", "", path)
        return f"{host}/{path}".lower()

    # SSH URI: ssh://git@github.com[:port]/owner/repo[.git]
    m = re.match(r"ssh://[^@]+@([^/:]+)(?::\d+)?/(.+)", url)
    if m:
        host = m.group(1)
        path = m.group(2)
        path = re.sub(r"\.git$", "", path)
        return f"{host}/{path}".lower()

    # SSH shorthand: git@github.com:owner/repo[.git]
    m = re.match(r"[^@]+@([^:]+):(.+)", url)
    if m:
        host = m.group(1)
        path = m.group(2)
        path = re.sub(r"\.git$", "", path)
        return f"{host}/{path}".lower()

    # Already normalized: host/owner/repo or host/group.../repo (2+ slashes, no scheme, no @)
    if re.match(r"[^/]+(?:/[^/]+){2,}$", url) and "://" not in url and "@" not in url:
        return re.sub(r"\.git$", "", url).lower()

    raise ValueError(f"リモートURLとして解析できません: {url!r}")


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
    """
    if expected is None:
        return
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
