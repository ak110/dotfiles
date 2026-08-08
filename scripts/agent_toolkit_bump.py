#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""agent-toolkitプラグインのバージョンbumpツール。

Claude Code向け正本である`agent-toolkit/.claude-plugin/plugin.json`と
`.claude-plugin/marketplace.json`の`version`を同時に更新する。
Codex向け派生manifestは同期スクリプトで生成する。

使い方:
    scripts/agent_toolkit_bump.py [patch|minor|major]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Literal

_REPO_ROOT = Path(__file__).parent.parent
_PLUGIN_MANIFEST = _REPO_ROOT / "agent-toolkit" / ".claude-plugin" / "plugin.json"
_MARKETPLACE_MANIFEST = _REPO_ROOT / ".claude-plugin" / "marketplace.json"
_PLUGIN_NAME = "agent-toolkit"

BumpKind = Literal["patch", "minor", "major"]
BUMP_RANKS: dict[BumpKind, int] = {"patch": 1, "minor": 2, "major": 3}


def main(argv: list[str] | None = None) -> int:
    """agent-toolkitプラグインのバージョンをbumpする。"""
    parser = argparse.ArgumentParser(description="agent-toolkitプラグインのバージョンbumpツール。")
    parser.add_argument(
        "kind",
        nargs="?",
        choices=["patch", "minor", "major"],
        help="bump種別。省略時は現在の状態を表示するのみで終了する。",
    )
    args = parser.parse_args(argv)

    current = _read_current_version()
    resolved = resolve_base_version()
    if resolved is None:
        print(f"current version: {current}")
        print(
            "基準版を確定できなかった（上流ブランチも origin/HEAD も解決できない）。既往のbumpを検出できないため増分しない。",
            file=sys.stderr,
        )
        return 1
    base, base_ref = resolved
    existing = infer_bump_kind(base, current)

    if args.kind is None:
        _print_status(current, base, base_ref, existing)
        return 0

    requested: BumpKind = args.kind

    if existing is None:
        new_version = compute_new_version(current, requested)
        _write_version(new_version)
        print(f"bump: {current} -> {new_version} ({requested}, 基準: {base} [{base_ref}])")
        return 0

    if BUMP_RANKS[requested] <= BUMP_RANKS[existing]:
        print(f"既存の未プッシュbump種別（{existing}）が指定種別（{requested}）と同等以上のため何もしない。")
        print(f"  基準: {base}（{base_ref}）")
        print(f"  現在: {current}（既存bump: {existing}）")
        return 0

    # 上書き格上げ。基準は公開済み時点のバージョン。
    new_version = compute_new_version(base, requested)
    _write_version(new_version)
    print(f"upgrade bump: {current} -> {new_version} ({existing} -> {requested}, 基準: {base} [{base_ref}])")
    return 0


def parse_version(s: str) -> tuple[int, int, int]:
    """`major.minor.patch`形式の文字列をタプルへ分解する。"""
    parts = s.split(".")
    if len(parts) != 3:
        raise ValueError(f"バージョン文字列は'major.minor.patch'形式である必要がある: {s!r}")
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError as e:
        raise ValueError(f"バージョン文字列の各要素は整数である必要がある: {s!r}") from e
    if any(v < 0 for v in (major, minor, patch)):
        raise ValueError(f"バージョン要素は非負整数である必要がある: {s!r}")
    return major, minor, patch


def format_version(t: tuple[int, int, int]) -> str:
    """バージョンタプルを`major.minor.patch`形式の文字列へ整形する。"""
    return f"{t[0]}.{t[1]}.{t[2]}"


def compute_new_version(current: str, kind: BumpKind) -> str:
    """`current`から`kind`に従って次のバージョン文字列を算出する。"""
    major, minor, patch = parse_version(current)
    match kind:
        case "patch":
            return format_version((major, minor, patch + 1))
        case "minor":
            return format_version((major, minor + 1, 0))
        case "major":
            return format_version((major + 1, 0, 0))


def infer_bump_kind(base: str, current: str) -> BumpKind | None:
    """`base`から`current`への差分が表すbump種別を推定する。

    同一なら`None`を返す。差分がbumpの規則（PATCH/MINOR/MAJOR）に当てはまらなければ`ValueError`を送出する。
    """
    bm, bn, bp = parse_version(base)
    cm, cn, cp = parse_version(current)
    if (bm, bn, bp) == (cm, cn, cp):
        return None
    if cm > bm and cn == 0 and cp == 0:
        return "major"
    if cm == bm and cn > bn and cp == 0:
        return "minor"
    if cm == bm and cn == bn and cp > bp:
        return "patch"
    raise ValueError(f"想定外のバージョン差分: base={base} current={current}")


def _read_current_version() -> str:
    return json.loads(_PLUGIN_MANIFEST.read_text(encoding="utf-8"))["version"]


BASE_VERSION_REFS: tuple[str, ...] = ("@{u}", "origin/HEAD")
"""基準版の解決に試す参照。先頭から順に試し、最初に解決できたものを採用する。

作業用の複製（git worktree等）では追跡先が失われて`@{u}`が解決できないため、
公開済みの既定ブランチを指す`origin/HEAD`を次に試す。
"""


def resolve_base_version() -> tuple[str, str] | None:
    """公開済み時点のplugin.jsonから`version`と、取得に使った参照名を返す。

    `BASE_VERSION_REFS`の順に試し、いずれからも取得できない場合は`None`を返す。
    呼び出し元は`None`を「既往のbumpを検出できない」状態として扱い、増分しない。
    """
    for ref in BASE_VERSION_REFS:
        version = _read_version_at(ref)
        if version is not None:
            return version, ref
    return None


def _read_version_at(ref: str) -> str | None:
    """指定参照上のplugin.jsonから`version`を取得する。解決できない場合は`None`を返す。"""
    rel = _PLUGIN_MANIFEST.relative_to(_REPO_ROOT)
    result = subprocess.run(
        ["git", "show", f"{ref}:{rel.as_posix()}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_REPO_ROOT),
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)["version"]
    except (json.JSONDecodeError, KeyError):
        return None


def _write_version(new_version: str) -> None:
    plugin_data = json.loads(_PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    marketplace_data = json.loads(_MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))
    matched = [entry for entry in marketplace_data["plugins"] if entry.get("name") == _PLUGIN_NAME]
    if len(matched) != 1:
        raise RuntimeError(f"marketplace.jsonに{_PLUGIN_NAME}のエントリが1件ではない（{len(matched)}件）")

    plugin_data["version"] = new_version
    matched[0]["version"] = new_version
    _PLUGIN_MANIFEST.write_text(json.dumps(plugin_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _MARKETPLACE_MANIFEST.write_text(json.dumps(marketplace_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _print_status(current: str, base: str, base_ref: str, existing: BumpKind | None) -> None:
    print(f"current version: {current}")
    print(f"base version: {base}（{base_ref}）")
    if existing is None:
        print("未プッシュbump: なし")
    else:
        print(f"未プッシュbump: あり（{existing}）")


if __name__ == "__main__":
    sys.exit(main())
