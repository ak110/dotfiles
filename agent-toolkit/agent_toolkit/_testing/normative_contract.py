"""条件別に移した規範の本文と、入口の読込表からの経路を一緒に検証する。"""

import pathlib
import re

from agent_toolkit._common.markdown_headings import parse_headings

REFERENCE = re.compile(r"`([^`]+\.md(?:#[^`]*)?)`")


def read_table_rows(text: str) -> list[tuple[str, str]]:
    """読込表（見出し行に「時点または条件」と「全文読む資料」を持つ表）の各行の条件と資料の欄を返す。"""
    rows: list[tuple[str, str]] = []
    columns: dict[str, int] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            columns = {}
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if "時点または条件" in cells and "全文読む資料" in cells:
            columns = {"condition": cells.index("時点または条件"), "documents": cells.index("全文読む資料")}
        elif columns and not set(cells[0]) <= {"-", ":"}:
            rows.append((cells[columns["condition"]], cells[columns["documents"]]))
    return rows


def resolve_reference(reference: str, source: pathlib.Path, plugin_root: pathlib.Path) -> pathlib.Path:
    """読込表の資料の参照（`<plugin名>:<スキル名>/<相対パス>`か、資料からの相対パス）を実体のパスへ解決する。"""
    plugin, separator, rest = reference.partition(":")
    if not separator:
        return (source.parent / reference).resolve()
    root = plugin_root if plugin == plugin_root.name else plugin_root.parent / plugin
    rest = rest.lstrip("/")
    return (root / rest if rest.startswith(("skills/", "share/")) else root / "skills" / rest).resolve()


def read_contract(source: pathlib.Path, plugin_root: pathlib.Path, *materials: tuple[str, str]) -> str:
    """指定した条件・資料の行が入口の読込表にあることを確かめ、入口と選択した全文だけを返す。

    呼出側が検証する判断単位を列挙する。参照先の再帰探索や全規範の連結は行わない。
    節（`#見出し`）を指す参照は資料の全文の経路に数えない。
    """
    text = source.read_text(encoding="utf-8")
    rows = read_table_rows(text)
    bodies = [text]
    for relative, condition in materials:
        target = (source.parent / relative).resolve()
        routes = [
            cell
            for row_condition, cell in rows
            if condition in row_condition
            and any(
                "#" not in reference and resolve_reference(reference, source, plugin_root) == target
                for reference in REFERENCE.findall(cell)
            )
        ]
        assert routes, f"条件別の取得経路がない: {source} -> {target}: {condition}"
        body = target.read_text(encoding="utf-8")
        assert body.strip(), target
        bodies.append(body)
    return "\n".join(bodies)


def section_text(path: pathlib.Path, heading: str) -> str:
    """資料のうち、名前が`heading`の見出しの直後から、同じか上の階層の次の見出しの前までの本文を返す。"""
    text = path.read_text(encoding="utf-8")
    headings = sorted(
        (token.map[0], level, name)
        for level in range(1, 7)
        for token, name in parse_headings(text, level)
        if token.map is not None
    )
    found = [(line, level) for line, level, name in headings if name == heading]
    assert len(found) == 1, f"{path}: 見出し「{heading}」が{len(found)}件ある"
    start, level = found[0]
    end = next((line for line, other, _name in headings if line > start and other <= level), None)
    return "\n".join(text.splitlines()[start + 1 : end])


def line_references(line: str, source: pathlib.Path, plugin_root: pathlib.Path) -> list[pathlib.Path]:
    """1行が名指しする資料（バッククォートで囲んだ`.md`の参照。節の指定は除く）の実体のパスを返す。"""
    return [resolve_reference(reference.partition("#")[0], source, plugin_root) for reference in REFERENCE.findall(line)]


def document_references(source: pathlib.Path, plugin_root: pathlib.Path) -> set[pathlib.Path]:
    """資料の全行（読込表と本文）が名指しする資料の実体のパスを返す。"""
    return {
        path for line in source.read_text(encoding="utf-8").splitlines() for path in line_references(line, source, plugin_root)
    }


def named_document(source: pathlib.Path, plugin_root: pathlib.Path, relative: str) -> str:
    """`source`の本文が名指しする資料（`source`からの相対パス）の全文を返す。名指しが無ければ失敗する。"""
    target = (source.parent / relative).resolve()
    assert target in document_references(source, plugin_root), f"本文が名指ししない: {source} -> {target}"
    return target.read_text(encoding="utf-8")
