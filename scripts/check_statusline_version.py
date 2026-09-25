#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""statusline（`rust/claude-statusline/`配下）の変更に版数更新が伴うかを検査する。

`release-statusline.yaml`は`rust/claude-statusline/`配下に差分がある`master`のマージに対して
タグ`statusline-v<version>`とGitHub Releaseを作成する。版数を据え置くと既存タグと衝突するため、
テストコードとテスト入力だけの変更、`Cargo.lock`だけの変更も版数更新の対象とする。

CIの`statusline-version` jobと、pyfltrの`statusline-version`（レーンの近接検証と公開前のローカル検証）の
双方がこのスクリプトを呼び、同じ判定を共有する。

- 比較基点: 環境変数`BASE_SHA`があればその値、無ければ`git fetch --no-tags origin master`の後の
  `git merge-base FETCH_HEAD <比較先>`とする
- 比較先: 環境変数`CURRENT_SHA`があればそのcommit、無ければ作業ツリー（未commitの変更と未追跡ファイルを含む）とする。
  ローカル検証はcommit前にも実行されるため、作業ツリーを比較先にする
- 差分が無ければ成功とし、差分があり基点と現在の`[package].version`が等しければ失敗とする
- `cargo metadata --locked`で`Cargo.lock`との整合を確かめ、`statusline-v<version>`タグがoriginに既にあれば失敗とする

`git fetch`、`git ls-remote`及び`cargo metadata`の失敗は成功扱いにせず、失敗した操作を標準エラーへ書いて
非0で終える。ネットワークに到達できない環境で検査を黙って通過させないためである。
"""

from __future__ import annotations

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


def main() -> int:
    """リポジトリルートで検査を実行し、終了コードを返す。"""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # CIとpyfltrはいずれも対象リポジトリのルートで起動するため、作業ディレクトリのリポジトリを検査する。
    toplevel = _git(pathlib.Path.cwd(), "rev-parse", "--show-toplevel")
    try:
        failure = check(pathlib.Path(_require(toplevel, "git rev-parse --show-toplevel")))
    except CheckError as error:
        print(str(error), file=sys.stderr)
        return 2
    if failure is not None:
        print(failure, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
