#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""配布プラグインの版数を据え置いたまま配布物を変更した公開を検出する。

対象は`.claude-plugin/marketplace.json`の`plugins[]`が`source`で指すプラグイン（agent-toolkit、agent-basics）である。
Codexの導入処理は版数の不一致だけで再導入するため、版数を据え置いた変更は稼働環境へ届かない。
そこでプラグインroot配下の差分は、`*_test.py`だけの差分を除き、コメント・docstring・文書・内部整理も含めて
版数更新を要する変更として扱う。判定基準は`.claude/skills/agent-toolkit-edit/references/version-bump.md`「判定基準」と一致させる。

CIの`plugin-version` jobと、公開担当がpush前のローカル検証で明示的に有効化するpyfltrの`plugin-version`が呼ぶ。

- 比較基点: 環境変数`BASE_SHA`があればそのcommit（CIではpush前のリモート先端`github.event.before`）。
  無ければ現在のブランチのupstreamを`git fetch --no-tags`で取得した先端（公開先の最新の公開済みcommit）とする
- 比較先: 環境変数`CURRENT_SHA`があればそのcommit、無ければ作業ツリー（未commitの変更と未追跡ファイルを含む）とする
- プラグインごとに、差分があり基点と比較先の`.claude-plugin/plugin.json`の`version`が等しければ失敗とする

`develop`は公開ごとに稼働環境へ導入されるため、masterとの合流点ではなく公開先の直前の公開済みcommitと比べる。
全0のOID（新規ブランチのpush）、解決できないOID、upstreamの不在、`git`の失敗は成功扱いにせず、
失敗した操作を標準エラーへ書いて終了コード2で終える。検査を黙って通過させないためである。
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import subprocess
import sys

_MARKETPLACE = ".claude-plugin/marketplace.json"
_MANIFEST = ".claude-plugin/plugin.json"
_ZERO_OID = re.compile(r"0+")


class CheckError(Exception):
    """検査の前提となる操作が失敗したことを表す。"""


def _git(repo_root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, encoding="utf-8", check=False)


def _require(result: subprocess.CompletedProcess[str], operation: str) -> str:
    if result.returncode != 0:
        raise CheckError(
            f"{operation}に失敗した（終了コード{result.returncode}）: {result.stderr.strip()}。"
            "表示された原因（Gitの接続・認証、比較基点の指定、upstreamの設定）を解消して同じ検証コマンドを再実行してください。"
        )
    return result.stdout.strip()


def _plugins(repo_root: pathlib.Path) -> list[tuple[str, str]]:
    """marketplaceに載るプラグインの名前とリポジトリ相対のrootを返す。"""
    try:
        data = json.loads((repo_root / _MARKETPLACE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CheckError(
            f"{_MARKETPLACE}を読めない: {error}。JSONとして読める内容へ直して同じ検証コマンドを再実行してください"
        ) from error
    entries = data.get("plugins", []) if isinstance(data, dict) else None
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) and isinstance(entry.get("name"), str) for entry in entries
    ):
        raise CheckError(
            f"{_MARKETPLACE}の`plugins`が、`name`を持つエントリの配列ではない。"
            "各エントリへ`name`と`source`を書いて同じ検証コマンドを再実行してください"
        )
    plugins = []
    for entry in entries:
        source = entry.get("source")
        if not isinstance(source, str) or not source.startswith("./"):
            raise CheckError(
                f"{_MARKETPLACE}の`{entry.get('name')}`の`source`がリポジトリ内の相対パスではない: {source!r}。"
                f"`source`を`./<プラグインのディレクトリ>`の形へ直して同じ検証コマンドを再実行してください"
            )
        plugins.append((str(entry["name"]), source.removeprefix("./").rstrip("/")))
    if not plugins:
        raise CheckError(
            f"{_MARKETPLACE}に`plugins[]`が無い。配布プラグインのエントリを`plugins[]`へ戻して同じ検証コマンドを再実行してください"
        )
    return plugins


def _commit(repo_root: pathlib.Path, revision: str, operation: str) -> str:
    return _require(_git(repo_root, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"), operation)


def _base_revision(repo_root: pathlib.Path) -> str:
    base_sha = os.environ.get("BASE_SHA", "")
    if base_sha:
        if _ZERO_OID.fullmatch(base_sha):
            raise CheckError(
                "BASE_SHAが全0のOIDで、比較する公開済みcommitが無い（新規ブランチのpushなど）。"
                "BASE_SHAへ公開先の直前の公開済みcommitを指定して再実行してください"
            )
        return _commit(repo_root, base_sha, f"BASE_SHA（{base_sha}）の解決")
    head = _require(_git(repo_root, "symbolic-ref", "--quiet", "HEAD"), "現在のブランチの解決")
    upstream = _require(
        _git(repo_root, "for-each-ref", "--format=%(upstream:remotename)%00%(upstream:remoteref)", head),
        "upstreamの解決",
    )
    remote, _, remote_ref = upstream.partition("\0")
    if not remote or not remote_ref:
        raise CheckError(
            f"{head}にupstreamが設定されていない。`git branch --set-upstream-to=<remote>/<branch>`で公開先を設定するか、"
            "BASE_SHAへ公開先の最新の公開済みcommitを指定して再実行してください"
        )
    _require(_git(repo_root, "fetch", "--no-tags", remote, remote_ref), f"git fetch --no-tags {remote} {remote_ref}")
    return _commit(repo_root, "FETCH_HEAD", "取得したupstreamの先端の解決")


def _changed_files(repo_root: pathlib.Path, base: str, current: str | None, root: str) -> list[str]:
    revisions = [base, current] if current else [base]
    names = _require(_git(repo_root, "diff", "--name-only", "-z", *revisions, "--", root), "git diff").split("\0")
    if not current:
        untracked = _require(_git(repo_root, "ls-files", "-z", "--others", "--exclude-standard", "--", root), "git ls-files")
        names.extend(untracked.split("\0"))
    return sorted({name for name in names if name and not name.endswith("_test.py")})


def _version(text: str, source: str) -> str:
    try:
        return str(json.loads(text)["version"])
    except (ValueError, KeyError, TypeError) as error:
        raise CheckError(
            f"{source}の`version`を読めない: {error}。"
            f"{source}がJSONとして読めて`version`の文字列を持つよう直してから同じ検証コマンドを再実行してください"
        ) from error


def check(repo_root: pathlib.Path) -> list[str]:
    """検査を実行し、版数を据え置いたプラグインごとの失敗理由を返す。

    前提となる操作の失敗は`CheckError`を送出する。
    """
    current = os.environ.get("CURRENT_SHA") or None
    if current is not None:
        current = _commit(repo_root, current, f"CURRENT_SHA（{current}）の解決")
    base = _base_revision(repo_root)
    failures = []
    for name, root in _plugins(repo_root):
        changed = _changed_files(repo_root, base, current, root)
        if not changed:
            continue
        manifest = f"{root}/{_MANIFEST}"
        base_version = _version(_require(_git(repo_root, "show", f"{base}:{manifest}"), f"基点の{manifest}の取得"), manifest)
        if current is None:
            try:
                text = (repo_root / manifest).read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise CheckError(
                    f"作業ツリーの{manifest}を読めない: {error}。"
                    "プラグインのディレクトリを削除・移動した場合は`.claude-plugin/marketplace.json`も合わせて直し、"
                    "同じ検証コマンドを再実行してください"
                ) from error
            current_version = _version(text, manifest)
        else:
            current_version = _version(
                _require(_git(repo_root, "show", f"{current}:{manifest}"), f"比較先の{manifest}の取得"), manifest
            )
        if base_version == current_version:
            listed = "、".join(changed[:5]) + (f"ほか{len(changed) - 5}件" if len(changed) > 5 else "")
            failures.append(
                f"{name}の配布物に差分があるが版数が{base_version}のまま据え置かれている（{listed}）。"
                f"`scripts/agent_toolkit_bump.py --plugin {name} <patch|minor|major>`で版数を更新してから再検査してください。"
            )
    return failures


def main() -> int:
    """リポジトリルートで検査を実行し、終了コードを返す。"""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # CIとpyfltrはいずれも対象リポジトリのルートで起動するため、作業ディレクトリのリポジトリを検査する。
    try:
        repo_root = pathlib.Path(
            _require(_git(pathlib.Path.cwd(), "rev-parse", "--show-toplevel"), "git rev-parse --show-toplevel")
        )
        failures = check(repo_root)
    except CheckError as error:
        print(str(error), file=sys.stderr)
        return 2
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
