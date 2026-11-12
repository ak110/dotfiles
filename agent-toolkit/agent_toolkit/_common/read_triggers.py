"""読込表の宣言を配送と不変条件検査の共通入力へ変換する。"""

from __future__ import annotations

import pathlib
import re

from agent_toolkit._common.plugin_roots import plugin_resolution_scope, resolve_plugin_root
from agent_toolkit._common.read_triggers_material import (
    material_targets,
    reference_context,
)
from agent_toolkit._common.read_triggers_model import (
    _CODE,
    INPUT_SELECTOR_RESOLUTION_SOURCES,
    NAMED_REFERENCE_CONDITION,
    ReadTrigger,
    _scope_cached,
    resolution_scope,
)
from agent_toolkit._common.read_triggers_references import (
    named_reference_rows,
    reference_method_targets,
    validate_plugin_relative_references,
    validate_reference_routes,
)
from agent_toolkit._common.read_triggers_resolution import (
    _direct_document,
    _plugin_manifest,
    canonical_markdown_files,
    document_sources,
    parent_contract_targets,
    project_skill_dirs,
    resolve_document,
    resolve_read_source,
)
from agent_toolkit._common.read_triggers_startup import (
    skill_invocation_required,
    startup_context,
    startup_documents,
    startup_targets,
)
from agent_toolkit._common.read_triggers_targets import (
    ReadTarget,
    document_lines,
    is_normative_document,
)
lazy from agent_toolkit._common import read_triggers_model, read_triggers_resolution

__all__ = [
    "INPUT_SELECTOR_RESOLUTION_SOURCES",
    "NAMED_REFERENCE_CONDITION",
    "ReadTarget",
    "ReadTrigger",
    "canonical_markdown_files",
    "document_lines",
    "document_sources",
    "is_normative_document",
    "material_targets",
    "named_reference_rows",
    "parent_contract_targets",
    "project_skill_dirs",
    "reference_context",
    "reference_method_targets",
    "resolution_scope",
    "resolve_document",
    "resolve_plugin_root",
    "resolve_read_source",
    "skill_invocation_required",
    "startup_context",
    "startup_documents",
    "startup_targets",
    "validate_plugin_relative_references",
    "validate_reference_routes",
]


@plugin_resolution_scope()
def parse_tables(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    *,
    strict: bool = False,
    host: str = "claude",
    project: pathlib.Path | str | None = None,
    defer_material: bool = False,
    named: bool = False,
) -> list[ReadTrigger]:
    """資料列を持つ表から宣言を読む。「契機」「配送操作」を省略した行は、資料自身の読取を契機とする判断の宣言になる。

    要求内（`resolution_scope`）では同じ資料の解析結果を共有する。呼出側が結果を変更しても共有へ及ばないよう複製を返す。
    `defer_material`では資料列の解決を`targets`参照まで遅らせ、`documents`・`headings`は空のまま保持する。
    strict検査はこの指定によらず全資料を解決する。
    `named`では本文の名指しへ補う宣言（`named_reference_rows`）を末尾へ加える。本文の全行を解決するため、
    `defer_material`とは併用せず、全文書を毎回読む呼出しでは指定しない。
    """
    return list(
        _parse_tables(
            source.resolve(),
            plugin_root,
            strict=strict,
            host=host,
            project=project,
            defer_material=defer_material,
            named=named,
        )
    )


@_scope_cached
def _parse_tables(
    source: pathlib.Path,
    plugin_root: pathlib.Path,
    *,
    strict: bool,
    host: str,
    project: pathlib.Path | str | None,
    defer_material: bool,
    named: bool,
) -> tuple[ReadTrigger, ...]:
    text = source.read_text(encoding="utf-8")
    result: list[ReadTrigger] = []
    headers: list[str] = []
    for number, line, visible in document_lines(text):
        if not visible:
            headers = []
            continue
        if not line.startswith("|"):
            headers = []
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if "全文読む資料" in cells:
            headers = cells
            continue
        if not headers or all(re.fullmatch(r"[-: ]+", cell) for cell in cells):
            continue
        if len(cells) != len(headers):
            raise ValueError(f"{source}:{number}: 読込表の列数が一致しない")
        row = dict(zip(headers, cells, strict=True))
        kind = row.get("契機", "").strip("` ")
        operations = tuple(_CODE.findall(row.get("配送操作", "")))
        # 両方の省略は、資料を選んで読む判断の宣言として扱う。配送操作は資料自身の読取を導出する。
        derived = not kind and not row.get("配送操作", "").strip()
        if derived:
            kind = "判断"
        if kind not in read_triggers_model.KINDS:
            raise ValueError(f"{source}:{number}: 不明な契機: {kind}")
        if not derived and kind in {"操作", "判断"} and not operations:
            raise ValueError(f"{source}:{number}: 配送操作が無い")
        if any(not read_triggers_resolution.known_operation(value) for value in operations):
            raise ValueError(f"{source}:{number}: 未登録の配送操作: {operations}")
        if strict and any(value in {"bash:atk wi add", "bash:atk wi edit"} for value in operations):
            raise ValueError(f"{source}:{number}: WI保存は`wi-report-save`または`wi-standard-save`で本文の種別を限定せよ")
        if strict:
            for operation in operations:
                if not operation.startswith("skill:"):
                    continue
                name = operation.removeprefix("skill:")
                owner = next((parent for parent in source.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
                if ":" not in name and owner is not None:
                    plugin = _plugin_manifest(owner)["name"]
                    name = f"{plugin}:{name}"
                selected = _direct_document(name, source, plugin_root, host, project=project)
                if selected is None or selected.name != "SKILL.md" or not selected.is_file():
                    raise ValueError(
                        f"{source}:{number}: 配送操作のスキルが存在しない: {operation}（解決名: skill:{name}）。"
                        "次の操作: 同一pluginの実在するスキルは`skill:<スキル名>`、"
                        "他pluginのスキルは完全名`skill:<plugin名>:<スキル名>`へ宣言を修正し、"
                        "対象pluginの`skills/<スキル名>/SKILL.md`が存在することを確認せよ。"
                        "修正後、対象worktreeのルートで`uv run --frozen --no-sync pytest -n 0 "
                        "agent-toolkit/read_triggers_invariant_test.py::test_all_read_tables_have_valid_declarations_and_routes`"
                        "を再実行せよ。"
                    )
                # Skillの実在は転送前に確かめ、全文資料の欠落・循環は共通解決で拒否する。
                resolve_read_source(selected, plugin_root, host, project=project)
        selector = row.get("資料選択子", "").strip("` ")
        if derived and selector:
            raise ValueError(f"{source}:{number}: 資料選択子を持つ行は契機と配送操作を宣言する")
        is_role_document = source.name.endswith(".subagent.md")
        if (
            selector
            and selector not in read_triggers_model.DYNAMIC_SELECTORS
            and not (
                selector.startswith("input:")
                and selector != "input:"
                and (is_role_document or selector in INPUT_SELECTOR_RESOLUTION_SOURCES)
            )
        ):
            raise ValueError(f"{source}:{number}: 未登録の動的資料選択子: {selector}")
        material = row["全文読む資料"]
        if material.startswith("同スキル"):
            owners = [value for value in _CODE.findall(cells[0]) if value.startswith(("agent-toolkit:", "agent-basics:"))]
            if len(owners) == 1:
                material = f"`{owners[0]}`の" + material
        # 資料実体は対象行のtargets参照時に解決し、非該当行のplugin・ファイル解決を省く。
        deferred = defer_material and not strict
        targets = () if deferred else _row_targets(material, source, number, plugin_root, host, project, selector)
        documents = tuple(target.path for target in targets)
        if strict:
            for target in targets:
                if target.heading and is_normative_document(target.path):
                    raise ValueError(
                        f"{source}:{number}: エージェント向け文書の節指定は使えない: {target}。"
                        "読込条件別にファイルを分割し、資料列をファイル全体、配送操作を`read:`に変更せよ。"
                    )
                target.body()
        result.append(
            ReadTrigger(
                source,
                number,
                kind,
                operations,
                documents,
                tuple(target.heading for target in targets),
                selector,
                row.get("時点または条件", row.get("条件", "")),
                # 導出行は配送操作を持たないため、同じ宣言元の行を資料列で区別できるよう保持する。
                material if deferred or derived else "",
                plugin_root if deferred else None,
                host if deferred else "claude",
                project if deferred else None,
                derived,
            )
        )
    if named and not defer_material:
        result.extend(named_reference_rows(source, plugin_root, result, host=host, project=project))
    return tuple(dict.fromkeys(result))


@_scope_cached
def _row_targets(
    material: str,
    source: pathlib.Path,
    line: int,
    plugin_root: pathlib.Path,
    host: str,
    project: pathlib.Path | str | None,
    selector: str,
) -> tuple[ReadTarget, ...]:
    """選択された宣言の参照を解決し、従来と同じ実在条件で診断する。"""
    targets = material_targets(material, source, plugin_root, host, project=project)
    if selector:
        targets = tuple(target for target in targets if "<" not in str(target.path))
    if not targets and not selector:
        raise ValueError(f"{source}:{line}: 実ファイルの資料を宣言する")
    if any(not target.path.is_file() for target in targets):
        raise ValueError(f"{source}:{line}: 資料が存在しない: {tuple(target.path for target in targets)}")
    return targets
