"""条件別に移した規範の本文と、入口からの取得経路を一緒に検証する。"""

import pathlib

from agent_toolkit._common import read_triggers


def read_contract(source: pathlib.Path, plugin_root: pathlib.Path, *materials: tuple[str, str]) -> str:
    """指定した条件・資料の直接経路を確かめ、入口と選択した全文だけを返す。

    呼出側が検証する判断単位を列挙する。参照先の再帰探索や全規範の連結は行わない。
    """
    rows = read_triggers.parse_tables(source, plugin_root, strict=True)
    bodies = [source.read_text(encoding="utf-8")]
    for relative, condition in materials:
        target = (source.parent / relative).resolve()
        routes = [row for row in rows if condition in row.condition and target in row.documents]
        assert routes, f"条件別の取得経路がない: {source} -> {target}: {condition}"
        assert all(row.kind == "判断" for row in routes), (source, target)
        assert any(item.path == target and not item.heading for row in routes for item in row.targets)
        body = target.read_text(encoding="utf-8")
        assert body.strip(), target
        bodies.append(body)
    return "\n".join(bodies)
