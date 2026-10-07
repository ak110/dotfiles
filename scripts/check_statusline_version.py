#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""statusline（`rust/claude-statusline/`配下）の版数とタグの判定を1か所で持つ。

`release-statusline.yaml`は`rust/claude-statusline/`配下に差分がある`master`のマージに対して
タグ`statusline-v<version>`とGitHub Releaseを作成する。版数を据え置くと既存タグと衝突するため、
テストコードとテスト入力だけの変更、`Cargo.lock`だけの変更も版数更新の対象とする。

指定なしの起動（版数更新の検査）は、CIの`statusline-version` jobと、公開前のローカル検証で
明示的に有効化するpyfltrの`statusline-version`の双方が呼ぶ。

- 比較基点: 環境変数`BASE_SHA`があればその値、無ければ`git fetch --no-tags origin master`の後の
  `git merge-base FETCH_HEAD <比較先>`とする
- 比較先: 環境変数`CURRENT_SHA`があればそのcommit、無ければ作業ツリー（未commitの変更と未追跡ファイルを含む）とする。
  ローカル検証はcommit前にも実行されるため、作業ツリーを比較先にする
- 差分が無ければ成功とし、差分があり基点と現在の`[package].version`が等しければ失敗とする
- `cargo metadata --locked`で`Cargo.lock`との整合を確かめ、`statusline-v<version>`タグがoriginに既にあれば失敗とする

`release-statusline.yaml`は次の2つの指定で同じ判定を使い、結果を`--github-output`のファイルへ`key=value`行で追記する。
いずれも環境変数`CURRENT_SHA`のcommitを対象にし、マージ済みのcommitを公開するため版数の据え置きは検査しない。

- `--release-prepare`: 比較基点を`CURRENT_SHA`の第一親とし、差分が無ければ`changed=false`を書く。
  差分があれば`cargo metadata --locked`を確かめ、`changed=true`、`version`、`tag`を書く
- `--release-tag-state`: タグの有無を`tag_exists`へ書く。cargoを起動しない

release用の指定では、originから取得したタグが`CURRENT_SHA`以外のcommitを指す場合に失敗とする。
同じcommitを指す既存タグは、Releaseの再実行で作成済みのものとして受け入れる。

`git fetch`、`git ls-remote`および`cargo metadata`の失敗は成功扱いにせず、失敗した操作を標準エラーへ書いて
終了コード2で終える。ネットワークに到達できない環境で検査を黙って通過させないためである。
"""

from __future__ import annotations

import argparse
import collections.abc
import io
import os
import pathlib
import subprocess
import sys
import tomllib

_STATUSLINE_DIR = "rust/claude-statusline"
_MANIFEST = f"{_STATUSLINE_DIR}/Cargo.toml"
_DEFAULT_METADATA_COMMAND = (
    "mise",
    "exec",
    "--",
    "cargo",
    "metadata",
    "--locked",
    "--no-deps",
    "--format-version=1",
    "--manifest-path",
    _MANIFEST,
)


class CheckError(Exception):
    """検査の前提となる操作が失敗したことを表す。"""


def _git(repo_root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, check=False)


def _require(result: subprocess.CompletedProcess[str], operation: str) -> str:
    if result.returncode != 0:
        raise CheckError(f"{operation}に失敗した（終了コード{result.returncode}）: {result.stderr.strip()}")
    return result.stdout.strip()


def _base_revision(repo_root: pathlib.Path, current: str | None) -> str:
    base_sha = os.environ.get("BASE_SHA", "")
    if base_sha:
        return base_sha
    _require(_git(repo_root, "fetch", "--no-tags", "origin", "master"), "git fetch --no-tags origin master")
    return _require(_git(repo_root, "merge-base", "FETCH_HEAD", current or "HEAD"), "git merge-base")


def _has_changes(repo_root: pathlib.Path, base: str, current: str | None) -> bool:
    revisions = [base, current] if current else [base]
    result = _git(repo_root, "diff", "--quiet", *revisions, "--", _STATUSLINE_DIR)
    if result.returncode == 1:
        return True
    _require(result, "git diff")
    if current:
        return False
    untracked = _require(_git(repo_root, "ls-files", "--others", "--exclude-standard", "--", _STATUSLINE_DIR), "git ls-files")
    return bool(untracked)


def _read_version(text: str) -> str:
    return str(tomllib.loads(text)["package"]["version"])


def check(
    repo_root: pathlib.Path, *, metadata_command: collections.abc.Sequence[str] = _DEFAULT_METADATA_COMMAND
) -> str | None:
    """検査を実行し、失敗理由を返す。成功時は`None`を返す。

    前提となる操作の失敗は`CheckError`を送出する。
    """
    current = os.environ.get("CURRENT_SHA") or None
    base = _base_revision(repo_root, current)
    if not _has_changes(repo_root, base, current):
        print("statuslineの変更がないため、版数検査を省略する。")
        return None

    base_version = _read_version(_require(_git(repo_root, "show", f"{base}:{_MANIFEST}"), "基点のCargo.tomlの取得"))
    current_version = _read_version((repo_root / _MANIFEST).read_text(encoding="utf-8"))
    if base_version == current_version:
        return "statuslineの変更にはCargo.tomlの版数更新が必要である。"

    metadata = subprocess.run(list(metadata_command), cwd=repo_root, capture_output=True, text=True, check=False)
    _require(metadata, "cargo metadata --locked")

    tag = f"statusline-v{current_version}"
    tag_result = _git(repo_root, "ls-remote", "--exit-code", "--refs", "origin", f"refs/tags/{tag}")
    if tag_result.returncode == 0:
        return f"statuslineの版数に対応する既存タグがある: {tag}"
    if tag_result.returncode != 2:
        _require(tag_result, f"タグ{tag}の照会")
    return None


def _manifest_version_at(repo_root: pathlib.Path, revision: str) -> str:
    return _read_version(_require(_git(repo_root, "show", f"{revision}:{_MANIFEST}"), f"{revision}のCargo.tomlの取得"))


def _release_tag_failure(repo_root: pathlib.Path, tag: str, current: str) -> tuple[str | None, bool]:
    """originのタグを取得し、`current`以外を指すタグの失敗理由とタグの有無を返す。"""
    _require(_git(repo_root, "fetch", "--tags", "--force", "origin"), "git fetch --tags origin")
    tagged = _git(repo_root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    if tagged.returncode != 0:
        return None, False
    current_commit = _require(_git(repo_root, "rev-parse", "--verify", f"{current}^{{commit}}"), "比較先commitの解決")
    if tagged.stdout.strip() != current_commit:
        return f"既存タグが別のcommitを指している: {tag}", True
    return None, True


def release_prepare(
    repo_root: pathlib.Path,
    current: str,
    *,
    metadata_command: collections.abc.Sequence[str] = _DEFAULT_METADATA_COMMAND,
) -> tuple[str | None, dict[str, str]]:
    """`current`を第一親と比べてリリースの要否と版数を判定し、失敗理由と出力値を返す。"""
    base = _require(_git(repo_root, "rev-parse", "--verify", f"{current}^1"), "比較基点（第一親）の解決")
    if not _has_changes(repo_root, base, current):
        print("statuslineの変更がないため、リリースを省略する。")
        return None, {"changed": "false", "version": "", "tag": ""}
    metadata = subprocess.run(list(metadata_command), cwd=repo_root, capture_output=True, text=True, check=False)
    _require(metadata, "cargo metadata --locked")
    version = _manifest_version_at(repo_root, current)
    tag = f"statusline-v{version}"
    failure, _ = _release_tag_failure(repo_root, tag, current)
    return failure, {"changed": "true", "version": version, "tag": tag}


def release_tag_state(repo_root: pathlib.Path, current: str) -> tuple[str | None, dict[str, str]]:
    """`current`の版数のタグがoriginにあるかを判定し、失敗理由と出力値を返す。"""
    tag = f"statusline-v{_manifest_version_at(repo_root, current)}"
    failure, exists = _release_tag_failure(repo_root, tag, current)
    return failure, {"tag_exists": "true" if exists else "false"}


def main(argv: collections.abc.Sequence[str] | None = None) -> int:
    """リポジトリルートで検査を実行し、終了コードを返す。"""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="statuslineの版数とタグを判定する。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--release-prepare", action="store_true", help="リリースの要否と版数を判定する（release-statusline.yaml用）"
    )
    mode.add_argument(
        "--release-tag-state", action="store_true", help="版数のタグの有無を判定する（release-statusline.yaml用）"
    )
    parser.add_argument("--github-output", type=pathlib.Path, help="release用の判定結果を`key=value`行で追記するファイル")
    args = parser.parse_args(argv)
    release = args.release_prepare or args.release_tag_state
    if release and args.github_output is None:
        parser.error("--release-prepareと--release-tag-stateには--github-outputが必要である")
    # CIとpyfltrはいずれも対象リポジトリのルートで起動するため、作業ディレクトリのリポジトリを検査する。
    toplevel = _git(pathlib.Path.cwd(), "rev-parse", "--show-toplevel")
    try:
        repo_root = pathlib.Path(_require(toplevel, "git rev-parse --show-toplevel"))
        if release:
            current = os.environ.get("CURRENT_SHA", "")
            if not current:
                raise CheckError("release用の判定には環境変数CURRENT_SHAへ対象commitを指定する必要がある")
            if args.release_prepare:
                failure, outputs = release_prepare(repo_root, current)
            else:
                failure, outputs = release_tag_state(repo_root, current)
        else:
            failure, outputs = check(repo_root), {}
    except CheckError as error:
        print(str(error), file=sys.stderr)
        return 2
    if failure is not None:
        print(failure, file=sys.stderr)
        return 1
    if release:
        with args.github_output.open("a", encoding="utf-8") as file:
            file.writelines(f"{key}={value}\n" for key, value in outputs.items())
    return 0


if __name__ == "__main__":
    sys.exit(main())
