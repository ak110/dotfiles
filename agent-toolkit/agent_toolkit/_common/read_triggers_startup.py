"""起動時の必須資料を解決して本文を配送する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import TYPE_CHECKING

import yaml

from agent_toolkit._common.plugin_roots import plugin_resolution_scope

if TYPE_CHECKING:
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


@plugin_resolution_scope()
def startup_targets(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    *,
    host: str = "claude",
    project: pathlib.Path | str | None = None,
) -> tuple[read_triggers.ReadTarget, ...]:
    """起動時の配送と読了観測が共有する、入力に依存しない資料集合を返す。"""
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    rows = read_triggers.parse_tables(source, plugin_root, host=host, project=project, defer_material=True)
    targets = list(read_triggers.reference_method_targets(source, plugin_root, host, project=project))
    role = source.name.removesuffix(".subagent.md")
    for row in rows:
        if row.kind != "起動時" and f"role:{role}" not in row.operations:
            continue
        targets.extend(row.targets)
    for target in tuple(targets):
        targets.extend(read_triggers.reference_method_targets(target.path, plugin_root, host, project=project))
    return tuple(
        dict.fromkeys(
            read_triggers.ReadTarget(target.path) if read_triggers.is_normative_document(target.path) else target
            for target in targets
        )
    )


@plugin_resolution_scope()
def startup_context(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    inputs: dict[str, str],
    *,
    host: str = "claude",
    project: pathlib.Path | str | None = None,
) -> str:
    """役割文書の受領と同時に必要な本文を、判断が始まる前に配送する。"""
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    targets = list(startup_targets(source, plugin_root, host=host, project=project))
    input_selectors: dict[read_triggers.ReadTarget, set[str]] = {}
    role = source.name.removesuffix(".subagent.md")
    for row in read_triggers.parse_tables(source, plugin_root, host=host, project=project, defer_material=True):
        if row.kind != "起動時" and f"role:{role}" not in row.operations:
            continue
        if row.selector.startswith("input:"):
            name = row.selector.removeprefix("input:")
            value = inputs.get(name)
            if value is None:
                raise ValueError(f"{source}:{row.line}: 起動時資料の入力が無い: {name}")
            try:
                values = json.loads(value)
            except ValueError:
                values = value
            for path in values if isinstance(values, list) else [values]:
                candidate = pathlib.Path(path) if isinstance(path, str) else None
                if candidate is None or not candidate.is_absolute() or not candidate.is_file():
                    raise ValueError(f"{source}:{row.line}: 入力{name}から実在する絶対資料を解決できない")
                target = read_triggers.ReadTarget(candidate)
                targets.append(target)
                input_selectors.setdefault(target, set()).add(row.selector)
    for target in tuple(targets):
        targets.extend(read_triggers.reference_method_targets(target.path, plugin_root, host, project=project))
    return startup_documents(targets, plugin_root, input_selectors)


def skill_invocation_required(path: pathlib.Path, *, host: str, subject: str) -> bool:
    """Claudeメインの通常スキルだけに、モデルからのSkill起動を要求する。"""
    if host != "claude" or subject != "main" or path.name != "SKILL.md":
        return False
    text = path.read_text(encoding="utf-8").lstrip("\ufeff").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return True
    frontmatter, separator, _body = text[4:].partition("\n---\n")
    if not separator:
        raise ValueError(f"{path}: スキルのfrontmatterが閉じられていない")
    metadata = yaml.safe_load(frontmatter)
    if not isinstance(metadata, dict):
        raise ValueError(f"{path}: スキルのfrontmatterはmappingで指定する")
    return metadata.get("disable-model-invocation") is not True


def startup_documents(
    targets: list[read_triggers.ReadTarget],
    plugin_root: pathlib.Path,
    input_selectors: dict[read_triggers.ReadTarget, set[str]] | None = None,
) -> str:
    """解決済みの実在資料を起動本文と再配送用の入力識別にまとめる。"""
    from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    selectors: dict[read_triggers.ReadTarget, set[str]] = {}
    for target, values in (input_selectors or {}).items():
        normalized = read_triggers.ReadTarget(target.path) if read_triggers.is_normative_document(target.path) else target
        selectors.setdefault(normalized, set()).update(values)
    targets = list(
        dict.fromkeys(
            read_triggers.ReadTarget(target.path) if read_triggers.is_normative_document(target.path) else target
            for target in targets
        )
    )
    rendered = []
    for target in dict.fromkeys(targets):
        owner = next((parent.parent for parent in target.path.parents if parent.name == "skills"), plugin_root)
        raw = target.path.read_bytes()
        body = target.body(raw.decode("utf-8")).replace("${CLAUDE_PLUGIN_ROOT}", str(owner))
        digest = hashlib.sha256(raw).hexdigest()
        metadata = json.dumps(
            {
                "path": str(target.path),
                "heading": target.heading,
                "selectors": sorted(selectors.get(target, set())),
                "sha256": digest,
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            },
            ensure_ascii=False,
        )
        rendered.append(
            '<atk-auto source="read-delivery" kind="startup-document">\n'
            f"資料JSON: {metadata}\n"
            f"資料: {target}\n本文識別子: {digest}\n相対参照の基点: {target.path.parent}\n{body}\n</atk-auto>"
        )
    return "\n\n".join(rendered)
