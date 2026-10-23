"""再発防止セルフチェックの観点収集と入力準備を1回で行う。"""

from __future__ import annotations

import argparse
import functools
import json
import pathlib
import re
import subprocess
import sys

from agent_toolkit._atk import run_script
from agent_toolkit._common import atomic_file, next_action, read_triggers
from agent_toolkit._git import command

PLUGIN_ROOT = run_script.PLUGIN_ROOT
_TITLE = "# 再発防止セルフチェック観点"
_FIELDS = frozenset({"対象条件", "確認内容", "根拠規範"})
_FIELD = re.compile(r"^\s*(?:[-*+]\s+)?(対象条件|確認内容|根拠規範):\s*\S")


def _paths(result: subprocess.CompletedProcess[bytes], scope: str, root: pathlib.Path) -> list[pathlib.Path]:
    """一致なしと収集失敗を分け、NUL区切りの結果を絶対パスへ変換する。"""
    if result.returncode not in (0, 1):
        raise ValueError(
            f"{scope}の収集に失敗した（終了{result.returncode}）: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return sorted({(root / name.decode("utf-8")).resolve() for name in result.stdout.split(b"\0") if name})


def _plugin_name(root: pathlib.Path) -> str | None:
    manifest = root / ".claude-plugin/plugin.json"
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else None


def _item_presence(path: pathlib.Path) -> bool | None:
    """認識した項目は真、空・見出し・コードだけなら偽、他の本文は未確定として返す。"""
    fields: set[str] = set()
    in_collection = False
    has_content = False
    for _number, line, visible in read_triggers.document_lines(path.read_text(encoding="utf-8")):
        if not visible:
            continue
        if line.startswith("# "):
            in_collection = line.startswith(_TITLE)
        if not in_collection:
            continue
        if re.match(r"^#{1,6} ", line):
            fields.clear()
            continue
        if line.strip():
            has_content = True
        match = _FIELD.match(line)
        if match:
            fields.add(match.group(1))
            if fields == _FIELDS:
                return True
    return None if has_content else False


def collect(worktree: pathlib.Path) -> dict[str, object]:
    """配布と全追跡Markdownを収集し、同じ原本の追跡側を優先して記録を返す。"""
    plugin_root = PLUGIN_ROOT.resolve()
    plugin_name = _plugin_name(plugin_root)
    if plugin_name is None:
        raise ValueError(f"配布pluginの名前を取得できない: {plugin_root}")
    repository = command.run(["rev-parse", "--show-toplevel"], worktree, capture_output=True, text=True)
    if repository.returncode:
        raise ValueError(f"対象worktreeのGit rootを取得できない: {repository.stderr.strip()}")
    root = pathlib.Path(repository.stdout.strip()).resolve()
    distributed = _paths(
        subprocess.run(
            ["rg", "-l", "--null", "--glob", "*.md", "^" + _TITLE, str(plugin_root)],
            capture_output=True,
            check=False,
        ),
        "配布観点",
        plugin_root,
    )
    tracked = _paths(
        command.run(["grep", "--null", "-l", "-E", "-e", "^" + _TITLE, "--", "*.md"], root, capture_output=True),
        "追跡観点",
        root,
    )

    @functools.cache
    def original(path: pathlib.Path) -> str | None:
        for directory in path.parents:
            if not directory.is_relative_to(root):
                break
            if _plugin_name(directory) == plugin_name:
                return path.relative_to(directory).as_posix()
        return None

    preferred = []
    selected = set(tracked)
    for distribution in distributed:
        relative = distribution.relative_to(plugin_root).as_posix()
        counterparts = [path for path in tracked if path == distribution or original(path) == relative]
        if counterparts:
            preferred.append(
                {"distributed": str(distribution), "tracked": [str(path) for path in counterparts], "relative": relative}
            )
        else:
            selected.add(distribution)
    selected_files = sorted(selected)
    presence = {path: _item_presence(path) for path in selected_files}
    return {
        "distributed": [str(path) for path in distributed],
        "tracked": [str(path) for path in tracked],
        "prefer_tracked": preferred,
        "selected": [str(path) for path in selected_files],
        "has_items": any(value is not False for value in presence.values()),
        "needs_review": [str(path) for path, value in presence.items() if value is None],
    }


def main(argv: list[str] | None = None) -> int:
    """既存の観点ファイル入力・存在判定・収集記録の所在をJSONで返す。"""
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="項目の記法は限定しない。認識できない本文は要確認として返し、観点なしの省略へ使わない。",
    )
    parser.add_argument("--worktree", type=pathlib.Path, required=True, metavar="DIR", help="対象Git作業ツリーの絶対パス")
    parser.add_argument(
        "--output", type=pathlib.Path, required=True, metavar="PATH", help="managed-temp内の収集記録JSONの絶対パス"
    )
    args = parser.parse_args(argv)
    try:
        if not args.worktree.is_absolute() or not args.output.is_absolute():
            raise ValueError("worktreeとoutputは絶対パスで指定する")
        result = collect(args.worktree)
        atomic_file.atomic_write(args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        next_action.report(
            f"観点収集に失敗: {error}", next_action="対象worktree・配布pluginと収集診断を確認し、同じコマンドを再実行する"
        )
        return 2
    print(
        json.dumps(
            {
                "観点ファイル": json.dumps(result["selected"], ensure_ascii=False),
                "観点あり": result["has_items"],
                "要確認": result["needs_review"],
                "収集記録": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
