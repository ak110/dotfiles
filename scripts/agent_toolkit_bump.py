#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""agent-toolkitプラグインのバージョンbumpツール。

`agent-toolkit/.claude-plugin/plugin.json`と`.claude-plugin/marketplace.json`の
`version`を同時に更新する。

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
    upstream = _read_upstream_version()
    existing = infer_bump_kind(upstream, current) if upstream is not None else None

    if args.kind is None:
        _print_status(current, upstream, existing)
        return 0

    requested: BumpKind = args.kind
    upstream_str = upstream if upstream is not None else "取得不可"

    if existing is None:
        new_version = compute_new_version(current, requested)
        _write_version(new_version)
        print(f"bump: {current} -> {new_version} ({requested}, 上流: {upstream_str})")
        return 0

    if BUMP_RANKS[requested] <= BUMP_RANKS[existing]:
        print(f"既存の未プッシュbump種別（{existing}）が指定種別（{requested}）と同等以上のため何もしない。")
        print(f"  上流: {upstream_str}")
        print(f"  現在: {current}（既存bump: {existing}）")
        return 0

    # 上書き格上げ。基準は上流時点のバージョン。
    assert upstream is not None
    new_version = compute_new_version(upstream, requested)
    _write_version(new_version)
    print(f"upgrade bump: {current} -> {new_version} ({existing} -> {requested}, 上流: {upstream})")
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


def _read_upstream_version() -> str | None:
    """上流ブランチ(@{u})上のplugin.jsonから`version`を取得する。

    上流ブランチが未設定など取得できない場合は`None`を返す。
    """
    rel = _PLUGIN_MANIFEST.relative_to(_REPO_ROOT)
    result = subprocess.run(
        ["git", "show", f"@{{u}}:{rel.as_posix()}"],
        capture_output=True,
        text=True,
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
    _update_plugin_manifest(new_version)
    _update_marketplace_manifest(new_version)


def _update_plugin_manifest(new_version: str) -> None:
    data = json.loads(_PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    data["version"] = new_version
    _PLUGIN_MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _update_marketplace_manifest(new_version: str) -> None:
    data = json.loads(_MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))
    matched = [entry for entry in data["plugins"] if entry.get("name") == _PLUGIN_NAME]
    if len(matched) != 1:
        raise RuntimeError(f"marketplace.jsonに{_PLUGIN_NAME}のエントリが1件ではない（{len(matched)}件）")
    matched[0]["version"] = new_version
    _MARKETPLACE_MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _print_status(current: str, upstream: str | None, existing: BumpKind | None) -> None:
    print(f"current version: {current}")
    if upstream is None:
        print("upstream version: 取得できなかった（上流ブランチが未設定の可能性）")
    else:
        print(f"upstream version: {upstream}")
    if existing is None:
        print("未プッシュbump: なし")
    else:
        print(f"未プッシュbump: あり（{existing}）")


if __name__ == "__main__":
    sys.exit(main())
