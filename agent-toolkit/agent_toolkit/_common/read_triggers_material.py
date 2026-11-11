"""資料列の名指し参照と節指定を解析する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import pathlib
import re
import urllib.parse
from typing import TYPE_CHECKING

from agent_toolkit._common.read_triggers_model import _REFERENCE
from agent_toolkit._common.read_triggers_targets import _scope_selector

if TYPE_CHECKING:
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def reference_context(text: str, source: pathlib.Path, plugin_root: pathlib.Path) -> str:
    """本文の直前の名指しスキルだけを保ち、表・フェンスの宣言を文脈へ混ぜない。"""
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    context = ""
    for _number, line, visible in read_triggers.document_lines(text):
        if not visible or line.startswith("|"):
            continue
        for name in re.findall(r"`((?:agent-toolkit:|agent-basics:)?[\w-]+)`", line):
            candidate = read_triggers.resolve_document(name, source, plugin_root)
            if candidate is not None and candidate.name == "SKILL.md" and candidate.is_file():
                context = f"`{name}`\n"
    return context


def material_targets(
    material: str,
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    host: str = "claude",
    *,
    references_only: bool = False,
    context: str = "",
    project: pathlib.Path | str | None = None,
) -> tuple[read_triggers.ReadTarget, ...]:
    """資料列の修飾スキル・Markdownリンク・日本語の節指定を保って解析する。"""
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    matches = list(_REFERENCE.finditer(material))
    result = []
    owner = source
    scope_end = 0
    for index, match in enumerate(matches):
        if match.start() < scope_end:
            continue
        value = match[1] or match[2]
        local = (
            read_triggers.resolve_document(value, source, plugin_root, host, project=project)
            if "/" not in value and ":" not in value
            else None
        )
        local_skill = value != "SKILL.md" and local is not None and local.name == "SKILL.md" and local.is_file()
        if (
            references_only
            and not local_skill
            and not (local is not None and local.suffix == ".md" and local.is_file())
            and not (value == "SKILL.md" and (owner != source or local is not None and local.is_file()))
            and not value.startswith(("references/", "agent-toolkit:", "agent-basics:", "${CLAUDE_PLUGIN_ROOT}/share/"))
        ):
            continue
        if value.startswith(("agent-toolkit:", "agent-basics:")) or local_skill:
            path = read_triggers.resolve_document(value, source, plugin_root, host, project=project)
            if path is not None:
                owner = path
            # 後ろに具体資料が続く場合、修飾名は所属の指定である。
            gap = material[match.end() : matches[index + 1].start()] if index + 1 < len(matches) else ""
            if (
                index + 1 < len(matches)
                and "。" not in gap
                and (matches[index + 1][1] or matches[index + 1][2]).startswith(("references/", "SKILL.md"))
            ):
                continue
        location = owner if value.startswith(("references/", "SKILL.md")) else source
        if references_only and value.startswith(("references/", "SKILL.md")):
            # 本文の名指しだけを所属として使う。無関係な先行文や同名ファイルから補完しない。
            prefix = context + material[: match.start()]
            named = [
                item
                for item in _REFERENCE.finditer(prefix)
                if re.fullmatch(r"(?:agent-toolkit|agent-basics):[\w-]+", item[1] or item[2])
                or (
                    "/" not in (item[1] or item[2])
                    and ":" not in (item[1] or item[2])
                    and (
                        candidate := read_triggers.resolve_document(
                            item[1] or item[2], source, plugin_root, host, project=project
                        )
                    )
                    is not None
                    and candidate.name == "SKILL.md"
                    and candidate.is_file()
                    and item[1] != "SKILL.md"
                )
            ]
            location = source
            if named:
                last = named[-1]
                gap = prefix[last.end() :]
                connected = re.fullmatch(
                    r"\s*(?:の|を起動し、)\s*(?:`references/[^`]+`(?:の)?(?:「[^」]+」)*\s*(?:と|および|ならびに|、|・)\s*)*",
                    gap,
                )
                if connected or re.search(r"同スキルの[^。]*$", prefix):
                    location = (
                        read_triggers.resolve_document(last[1] or last[2], source, plugin_root, host, project=project) or source
                    )
        if value == "SKILL.md" and index:
            gap = material[matches[index - 1].end() : match.start()]
            if "。" in gap or not (gap.strip().startswith(("の", "を起動")) or "同スキル" in gap):
                location = source
        path = read_triggers.resolve_document(
            value,
            location,
            plugin_root,
            host,
            project=project,
        )
        if path is None:
            continue
        following = material[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(material)]
        immediate = re.match(
            r"\s*(?:の)?(?:冒頭(?:と|、)?)*(?:「[^」]+」(?:から「[^」]+」まで)?(?:[、・]|と|および)?)+",
            material[match.end() :],
        )
        if immediate:
            following = immediate[0]
            scope_end = match.end() + immediate.end()
        symbol_scope = re.match(r"\s*の\s*`#{1,6}\s+([^`\n]+)`", material[match.end() :])
        if symbol_scope:
            following = "の「" + symbol_scope[1] + "」"
            scope_end = match.end() + symbol_scope.end()
        explicit = urllib.parse.unquote(value.split("#", 1)[1]) if "#" in value else ""
        selected = following if "「" in following or "冒頭" in following else ""
        if references_only and not following.lstrip().startswith(("の「", "「", "冒頭", "の冒頭")):
            selected = ""
        elif references_only:
            matched_scope = re.match(
                r"^(?:の)?(?:冒頭(?:と|、)?)*(?:「[^」]+」(?:から「[^」]+」まで)?(?:[、・]|と|および)?)+", following.lstrip()
            )
            selected = (
                matched_scope[0] if matched_scope else ("冒頭" if following.lstrip().startswith(("冒頭", "の冒頭")) else "")
            )
        selector = explicit or _scope_selector(selected)
        if path.name == "Makefile" and not selector:
            target = re.match(r"の`([\w.-]+)` targetと、それが呼び出す子target", material[match.end() :])
            if target:
                selector = "make:" + target[1]
        result.append(read_triggers.ReadTarget(path, selector))
    self_reference = re.search(r"(?:本書|^)(「[^」]+」)", material)
    if self_reference and not result:
        result.append(read_triggers.ReadTarget(source.resolve(), self_reference[1]))
    return tuple(dict.fromkeys(result))
