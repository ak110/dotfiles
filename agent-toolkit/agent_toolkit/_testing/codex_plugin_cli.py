"""Codexのplugin照会（設定済みmarketplaceの一覧とmarketplaceごとの導入一覧）を差し替える偽CLI。"""

import json
import pathlib
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[str]]


def codex_plugin_cli(
    installed: Sequence[object],
    *,
    marketplaces: Sequence[str] | None = None,
    calls: list[list[str]] | None = None,
    other: Runner | None = None,
) -> Runner:
    """`process_tree.run_with_timeout`の代わりに渡す関数を返す。

    `codex plugin marketplace list --json`には`marketplaces`を返す。省略時は`installed`の`marketplaceName`から作る。
    `codex plugin list --json --marketplace <名前>`には`marketplaceName`が一致する項目を返す。
    どの設定済みmarketplaceにも一致しない項目（値の欠落や不正値）は、Codexの出力の形式異常を模すため最初のmarketplaceの応答に含める。
    marketplaceを指定しない`codex plugin list`はリモートカタログへ接続するため、呼ばれたら失敗させる。
    codex以外の起動は`other`へ渡す。
    """
    items = list(installed)
    names = list(marketplaces) if marketplaces is not None else _marketplace_names(items)

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if pathlib.Path(args[0]).stem != "codex":
            if other is None:
                raise AssertionError(f"想定外の起動: {args}")
            return other(args, **kwargs)
        if calls is not None:
            calls.append(list(args))
        if args[1:] == ["plugin", "marketplace", "list", "--json"]:
            body: object = {"marketplaces": [{"name": name} for name in names]}
        elif args[1:5] == ["plugin", "list", "--json", "--marketplace"] and len(args) == 6:
            body = {"installed": [item for item in items if _belongs(item, args[5], names)]}
        else:
            raise AssertionError(f"marketplaceを指定しないCodexの照会: {args}")
        return subprocess.CompletedProcess(args, 0, json.dumps(body), "")

    return run


def _marketplace_names(items: list[object]) -> list[str]:
    names = [item.get("marketplaceName") for item in items if isinstance(item, dict)]
    return list(dict.fromkeys(name for name in names if isinstance(name, str) and name)) or ["mine"]


def _belongs(item: object, name: str, names: list[str]) -> bool:
    marketplace = item.get("marketplaceName") if isinstance(item, dict) else None
    return marketplace == name or (marketplace not in names and name == names[0])
