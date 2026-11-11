"""名前付き入力の契約検査と配送本文の組み立て。"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server.launch_requests_materials import _check_explore_materials
from agent_toolkit._common import read_triggers, task_documents
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
    委譲先の解釈は`agent-toolkit/share/rules/common/01-agent.md`「方針が衝突する場合の優先順位」が定める。
    """
    return auto_message(body, source="agents-server", kind="delivery")


def with_launcher_record(body: str, context: Mapping[str, Any]) -> str:
    """taskの起動本文へ、サーバーが解決した直接の委譲元情報を添える。"""
    notice = auto_message(json.dumps(dict(context), ensure_ascii=False), source="agents-server", kind="launcher-record")
    return _with_task_reference(body, notice)


def with_startup_documents(body: str, documents: str) -> str:
    """役割の作業指示と入力を先頭に保ち、配送資料を参照資料の冒頭へ挿入する。"""
    if not documents:
        return body
    return _with_task_reference(body, documents)


def _with_task_reference(body: str, reference: str) -> str:
    """生成側が確定した区切りへ挿入し、入力値の本文から境界を推測しない。"""
    instructions, boundary, remainder = body.partition(task_documents.TASK_PROMPT_REFERENCES)
    assert boundary
    return instructions + boundary + reference + "\n\n" + remainder


def _check_declared_inputs(
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
    """拒否した役割と同じ版の、入力説明を所有する契約と節を案内する。"""
    try:
        targets = read_triggers.parent_contract_targets(task_document.name, task_document.parent.parent)
        inputs = tuple(
            read_triggers.ReadTarget(target.path, target.heading or _input_section(target.path)) for target in targets
        )
        for target in inputs:
            target.body()
    except (OSError, UnicodeError, ValueError) as error:
        return (
            f"入力説明の起動・受領契約を解決できません: {error}。"
            "指定した役割文書と同じplugin rootの共有parentまたはparent-read-source宣言・参照先を修復し、"
            "入力説明を確認する。"
        )
    return "入力の意味・制約は" + "、".join(f"`{target.path}`「{target.heading}」" for target in inputs) + "で確認する。"


_INPUT_SECTIONS = ("入力", "渡す入力")


def _input_section(contract: pathlib.Path) -> str:
    """見出しを指定しない親契約から、入力を説明するH2節を1つ選ぶ。

    親契約の入力節名は役割ごとに`入力`と`渡す入力`がある。固定名を補うと他方の役割で
    見出し解決エラーになり、拒否の修正に必要な説明へ到達できない。
    """
    names = [
        line.removeprefix("## ").strip()
        for _number, line, visible in read_triggers.document_lines(contract.read_text(encoding="utf-8"))
        if visible and line.startswith("## ")
    ]
    found = [name for name in names if name in _INPUT_SECTIONS]
    if len(found) != 1:
        raise ValueError(f"{contract}: 入力を説明するH2節（{'・'.join(_INPUT_SECTIONS)}）が{len(found)}件ある")
    return found[0]


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
