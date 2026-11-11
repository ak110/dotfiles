"""本文の名指し参照を宣言へ補い、参照経路を検査する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import pathlib
from collections.abc import Iterator
from typing import TYPE_CHECKING

from agent_toolkit._common.plugin_roots import plugin_resolution_scope
from agent_toolkit._common.read_triggers_model import _REFERENCE, _REFERENCE_METHOD_HEADING
from agent_toolkit._common.read_triggers_targets import _headings

if TYPE_CHECKING:
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


@plugin_resolution_scope()
def reference_method_targets(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    host: str = "claude",
    *,
    project: pathlib.Path | str | None = None,
) -> tuple[read_triggers.ReadTarget, ...]:
    """共通読込方法の明示参照を、独立した資料全体へ解決する。"""
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    source = source.resolve()
    if not read_triggers.is_normative_document(source):
        return ()
    canonical = read_triggers.resolve_read_source(source, plugin_root, host, project=project)
    if canonical != source:
        return reference_method_targets(canonical, plugin_root, host, project=project)
    text = source.read_text(encoding="utf-8")
    _lines, headings = _headings(text)
    method = (
        read_triggers.ReadTarget(source, _REFERENCE_METHOD_HEADING).body(text)
        if any(name == _REFERENCE_METHOD_HEADING for _number, _level, name in headings)
        else ""
    )
    result: list[read_triggers.ReadTarget] = []
    for _number, line, visible in read_triggers.document_lines(method):
        if not visible or not line.startswith("名指し参照は"):
            continue
        targets = read_triggers.material_targets(line, source, plugin_root, host, project=project)
        if len(targets) != 1 or targets[0].heading:
            raise ValueError(f"{source}: 名指し参照の読込方法はファイル全体で指定する")
        target = targets[0]
        owner = read_triggers.resolve_document(
            "agent-basics:writing-standards/references/read-method.md", source, plugin_root, host, project=project
        )
        if target.path != owner:
            raise ValueError(f"{source}: 名指し参照の読込方法は共通の定義元を指定する: {target}")
        if not target.path.is_file():
            raise ValueError(f"{source}: 名指し参照の読込方法の定義元が存在しない: {target}")
        target.body()
        result.append(target)
    return tuple(dict.fromkeys(result))


def _is_rule_document(path: pathlib.Path, plugin_root: pathlib.Path) -> bool:
    """常時規範と役割別の規範の原本（`share/rules/`配下）かを返す。配送はhookと同期処理が保証する。"""
    return path.resolve().is_relative_to(plugin_root.resolve() / "share" / "rules")


def _named_references(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    host: str = "claude",
    *,
    project: pathlib.Path | str | None = None,
) -> Iterator[tuple[int, read_triggers.ReadTarget]]:
    """本文（表・フェンス・所在参照の行を除く）が名指しする他資料を、行番号とともに返す。"""
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    context = ""
    for number, line, visible in read_triggers.document_lines(source.read_text(encoding="utf-8")):
        if not visible or line.startswith("|") or "所在参照" in line:
            continue
        targets = read_triggers.material_targets(
            line, source, plugin_root, host, references_only=True, context=context, project=project
        )
        context = read_triggers.reference_context(line, source, plugin_root) or context
        for target in targets:
            yield number, target


@plugin_resolution_scope()
def named_reference_rows(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    rows: list[read_triggers.ReadTrigger] | None = None,
    *,
    host: str = "claude",
    project: pathlib.Path | str | None = None,
) -> list[read_triggers.ReadTrigger]:
    """本文が名指しし、読込表に明示の行が無い資料へ補う宣言を返す。

    読込表へ本文の名指しを写していた定型の行の代わりである。条件は`NAMED_REFERENCE_CONDITION`、契機は`判断`、
    配送操作はその資料自身の読取とする。別の経路で届く資料（`share/rules/`配下の規範、「名指し参照の読込方法」の定義元、
    宣言元自身）と、同じ文書に明示の行がある資料、実在しない参照には補わない。
    読込元が別の文書へ解決される転送文書と、hookと同期処理が配送する`share/rules/`配下の規範は宣言元にしない。
    """
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    source = source.resolve()
    if not source.is_file() or _is_rule_document(source, plugin_root):
        return []
    if read_triggers.resolve_read_source(source, plugin_root, host, project=project) != source:
        return []
    if rows is None:
        rows = read_triggers.parse_tables(source, plugin_root, host=host, project=project)
    routed = {target.path for row in rows for target in row.targets} | {source}
    routed.update(method.path for method in reference_method_targets(source, plugin_root, host, project=project))
    result: list[read_triggers.ReadTrigger] = []
    for number, target in _named_references(source, plugin_root, host, project=project):
        path = target.path
        if path in routed or not path.is_file() or _is_rule_document(path, plugin_root):
            continue
        routed.add(path)
        material = f"`{path}`"
        result.append(
            read_triggers.ReadTrigger(
                source,
                number,
                "判断",
                (),
                (path,),
                ("",),
                "",
                read_triggers.NAMED_REFERENCE_CONDITION,
                material,
                derived_read=True,
            )
        )
    return result


def validate_reference_routes(source: pathlib.Path, plugin_root: pathlib.Path, rows: list[read_triggers.ReadTrigger]) -> None:
    """本文が名指しする他資料が実在することを確かめる。

    読む経路は、読込表の明示の行か、`named_reference_rows`が補う宣言で成立する。
    定義の所在を示すだけの行には「所在参照」を明記する。
    """
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    del rows  # 経路は補完で成立するため、明示の行とは比べない。
    if read_triggers.resolve_read_source(source, plugin_root) != source.resolve():
        return
    for number, target in _named_references(source, plugin_root):
        if not target.path.is_file():
            raise ValueError(
                f"{source}:{number}: 参照先が存在しない: {target.path}。"
                "所属スキルを`agent-toolkit:<スキル>`の`references/...`の形で明記してください。"
            )


def validate_plugin_relative_references(source: pathlib.Path) -> None:
    """プラグイン文書の相対パス参照が、所有プラグインのrootの外を指していないことを確かめる。

    リポジトリではagent-toolkitとagent-basicsが隣り合うが、配布キャッシュでは版ごとの別ディレクトリに置かれる。
    rootの外を指す相対パスはリポジトリ内の検査では解決しても配布物では届かないため、論理名表記へ誘導する。
    コードフェンスの内側は例示として扱い、検査しない。
    """
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    source = source.resolve()
    owner = next((parent for parent in source.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
    if owner is None:
        return
    for number, line, visible in read_triggers.document_lines(source.read_text(encoding="utf-8")):
        if not visible:
            continue
        for matched in _REFERENCE.finditer(line):
            value = (matched[1] or matched[2] or "").split("#", 1)[0].strip()
            if not value.startswith("../"):
                continue
            resolved = (source.parent / value).resolve()
            if not resolved.is_relative_to(owner):
                raise ValueError(
                    f"{source}:{number}: 相対パスが所有プラグインのrootの外を指す: {value}。"
                    "配布時はプラグインごとに別ディレクトリへ置かれ届かないため、"
                    "`agent-basics:<スキル>/<パス>`や`agent-toolkit:<スキル>/<パス>`の論理名表記で書く"
                )
