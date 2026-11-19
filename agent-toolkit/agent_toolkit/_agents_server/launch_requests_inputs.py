"""名前付き入力の契約検査と配送本文の組み立て。"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server.launch_requests_materials import _check_explore_materials
from agent_toolkit._common import task_documents
from agent_toolkit._common.markdown_headings import parse_headings
from agent_toolkit._common.message_format import auto_message
from agent_toolkit._common.next_action import ActionableError

# 確認処理が受理する行の書式。拒否応答の本文へ添え、委譲元が同じ応答だけで書式を確定できる状態にする。
_REQUIRED_INPUT_LINE_FORMAT = (
    "受理する書式: 必須入力の行は`<項目名>:`で始める。"
    "項目名へ別の語を連結した行はその項目として解決しないため、補足する語は別の行へ書く。"
)


# `<役割名>.subagent.md`の本文がプラグインルートを参照するときの変数名。
_PLUGIN_ROOT_VARIABLE = "${CLAUDE_PLUGIN_ROOT}"


def wrap_delivery_body(body: str) -> str:
    """委譲先へ配送する本文を、生成された配送境界で囲む。

    配送境界は最初の開始タグと最後の同名終了タグで確定する。
    委譲先の解釈は委譲先の入口（`atk-mode`の`playbooks/subagent.md`）が定める。
    """
    return auto_message(body, source="agents-server", kind="delivery")


def with_launcher_record(body: str, context: Mapping[str, Any]) -> str:
    """taskの起動本文へ、サーバーが解決した直接の委譲元情報を添える。"""
    notice = auto_message(json.dumps(dict(context), ensure_ascii=False), source="agents-server", kind="launcher-record")
    return _with_task_reference(body, notice)


def with_startup_documents(body: str, documents: str) -> str:
    """役割の作業指示と入力を先頭に保ち、配送資料を参照資料の冒頭へ挿入する。役割文書の無い依頼文では末尾へ加える。"""
    if not documents:
        return body
    if task_documents.TASK_PROMPT_REFERENCES not in body:
        return body + "\n\n" + documents
    return _with_task_reference(body, documents)


def _with_task_reference(body: str, reference: str) -> str:
    """生成側が確定した区切りへ挿入し、入力値の本文から境界を推測しない。"""
    instructions, boundary, remainder = body.partition(task_documents.TASK_PROMPT_REFERENCES)
    assert boundary
    return instructions + boundary + reference + "\n\n" + remainder


def check_declared_inputs(
    task_document: pathlib.Path,
    declaration: task_documents.TaskDocumentDeclaration,
    extra_params: Mapping[str, str],
) -> None:
    """必須入力の欠落と宣言外の入力名を拒否する。"""
    missing = [name for name in declaration.required if name not in extra_params]
    if missing:
        raise ActionableError(
            f"必須入力が欠けています: {', '.join(missing)}; `subagent_md_path`: {task_document}; {_REQUIRED_INPUT_LINE_FORMAT}",
            next_action=(
                _input_contract_guidance(task_document) + "欠けた入力名をキーとして`extra_params`へ加え、`start`を再発行する"
            ),
        )
    undeclared = [name for name in extra_params if name not in declaration.accepted]
    if undeclared:
        raise ActionableError(
            f"`<役割名>.subagent.md`が宣言していない入力です: {', '.join(undeclared)}; "
            f"受理する入力名: {', '.join(sorted(declaration.accepted))}; `subagent_md_path`: {task_document}。",
            next_action=(
                _input_contract_guidance(task_document)
                + "今回限りの補足を渡す欄は無いため、値を`<役割名>.subagent.md`が宣言した入力へ収めるか、"
                "宣言外の値を渡さずに起動する"
                "。入力を直して`start`を再発行する"
            ),
        )
    _check_explore_materials(task_document, extra_params)


def _input_contract_guidance(task_document: pathlib.Path) -> str:
    """拒否した役割と同じ版の、入力説明を所有する契約と節を案内する。

    契約は役割文書と同じplugin rootで役割を`起動対象:`に挙げる文書とする。`<役割名>.parent.md`は入力の節、
    手順書は`起動対象:`の直前の見出しの節を示す。
    """
    role = task_document.name.removesuffix(task_documents.TASK_DOCUMENT_SUFFIX)
    try:
        root = task_documents.document_plugin_root(task_document)
        contracts = list(dict.fromkeys(path for path, name in task_documents.launch_contract_documents(root) if name == role))
        if not contracts:
            raise ValueError(f"{root}に{role}.subagent.mdを`起動対象:`に挙げる契約が無い")
        targets = [
            (path, _input_section(path) if path.name == f"{role}.parent.md" else _launch_section(path, role))
            for path in contracts
        ]
    except (OSError, UnicodeError, ValueError) as error:
        return (
            f"入力説明の起動・受領契約を解決できません: {error}。"
            "指定した役割文書と同じplugin rootで、役割を`起動対象:`に挙げる契約と入力の節を修復し、"
            "入力説明を確認する。"
        )
    return "入力の意味・制約は" + "、".join(f"`{path}`「{heading}」" for path, heading in targets) + "で確認する。"


_INPUT_SECTIONS = ("入力", "渡す入力")


def _input_section(contract: pathlib.Path) -> str:
    """`<役割名>.parent.md`から、入力を説明するH2節を1つ選ぶ。

    親契約の入力節名は役割ごとに`入力`と`渡す入力`がある。固定名を補うと他方の役割で
    見出し解決エラーになり、拒否の修正に必要な説明へ到達できない。
    """
    found = [name for _token, name in parse_headings(contract.read_text(encoding="utf-8"), 2) if name in _INPUT_SECTIONS]
    if len(found) != 1:
        raise ValueError(f"{contract}: 入力を説明するH2節（{'・'.join(_INPUT_SECTIONS)}）が{len(found)}件ある")
    return found[0]


def _launch_section(contract: pathlib.Path, role: str) -> str:
    """手順書のうち、役割を`起動対象:`に挙げる行の直前にある見出し（H2・H3）を返す。"""
    text = contract.read_text(encoding="utf-8")
    line = next(
        index
        for index, value in enumerate(text.splitlines())
        if value.startswith("起動対象: ") and f"{role}.subagent.md" in value.removeprefix("起動対象: ").split(",")
    )
    headings = sorted(
        (token.map[0], name)
        for level in (2, 3)
        for token, name in parse_headings(text, level)
        if token.map is not None and token.map[0] < line
    )
    if not headings:
        raise ValueError(f"{contract}: {role}.subagent.mdの`起動対象:`より前に見出しが無い")
    return headings[-1][1]


def named_input_lines(params: Mapping[str, str]) -> list[str]:
    """値の改行を区別して、名前付き入力を最小本文へ直列化する。"""
    lines: list[str] = []
    for name, value in params.items():
        if "\n" in value:
            lines.append(f"{name}:")
            lines.extend(f"  {line}" for line in value.split("\n"))
        else:
            lines.append(f"{name}: {value}")
    return lines
