"""保持した起動記録と役割宣言に基づく継続入力の検査。"""

from __future__ import annotations

import contextlib
import json
import pathlib
from collections.abc import Mapping

from agent_toolkit._agents_server.launch_requests_inputs import _PLUGIN_ROOT_VARIABLE, named_input_lines
from agent_toolkit._common import task_documents
from agent_toolkit._common.next_action import ActionableError


def _continuation_recovery(declaration: task_documents.TaskDocumentDeclaration | str | None) -> str:
    """保持する役割宣言から、同じsessionへ送れる再実行形を示す。"""
    freeform = json.dumps({"prompt": "<今回の指示と新しい入力>"}, ensure_ascii=False)
    guidance = "同じsession_idのsend_messageへ渡す自由文の再実行形: " + freeform
    if isinstance(declaration, str):
        return guidance + "。役割宣言を取得できないため定型指示は確認できません: " + declaration
    if declaration is None:
        return guidance + "。起動記録に役割文書がなく、宣言された定型指示はありません"
    if not declaration.continuations:
        return guidance + "。この役割に宣言された定型指示はありません"
    forms = [
        {"instruction": item.instruction, "extra_params": {name: f"<{name}>" for name in item.required}}
        for item in declaration.continuations
    ]
    return guidance + "。定型指示の最小の再実行形: " + json.dumps(forms, ensure_ascii=False)


def continuation_prompt(
    launch_prompt: str,
    followup_prompts: tuple[str, ...],
    *,
    prompt: str | None = None,
    instruction: str | None = None,
    extra_params: Mapping[str, str] | None = None,
) -> str:
    """既存の起動記録と役割宣言から、継続本文を検査・生成する。"""
    first_line = launch_prompt.split("\n", 1)[0]
    documents = task_documents.find_task_documents(first_line)
    declaration = task_documents.read_declaration(documents[0]) if documents else None
    if declaration is None and ".subagent.md" in first_line:
        declaration = "起動記録の役割文書へ到達できません"
    if (prompt is None) == (instruction is None) or (prompt is not None and extra_params is not None):
        raise ActionableError(
            "promptとinstruction/extra_paramsは排他的です。どちらか一方が必要です。",
            next_action=_continuation_recovery(declaration),
        )
    text = prompt if prompt is not None else instruction
    assert text is not None
    lines = text.strip().splitlines()
    first = lines[0].strip().removesuffix("。") if lines else ""
    continuations = declaration.continuations if isinstance(declaration, task_documents.TaskDocumentDeclaration) else ()
    contract = next((item for item in continuations if item.instruction == first), None)
    if contract is None:
        if instruction is not None:
            raise ActionableError(
                f"宣言された継続指示ではありません: {instruction}",
                next_action=_continuation_recovery(declaration),
            )
        if isinstance(declaration, task_documents.TaskDocumentDeclaration):
            known_lines = set(launch_prompt.splitlines())
            for followup in followup_prompts:
                known_lines.update(followup.splitlines())
            controls = frozenset(name for item in continuations for name in item.accepted)
            for line in text.splitlines():
                name, separator, value = line.partition(": ")
                if (
                    separator
                    and name in declaration.accepted - controls
                    and line in known_lines
                    and not pathlib.Path(value).is_file()
                ):
                    raise ActionableError(
                        f"保持済みの初回入力が再掲されています: {name}",
                        next_action="新しい要求と変更した入力だけをpromptへ渡す。参照先の更新は定型継続の名前付き入力で指定する",
                    )
        # 同文の自由な指示も新しい操作になり得る。意味上の必要性は送信側へ残す。
        if documents and text.lstrip().startswith(("#", "---")):
            with contextlib.suppress(OSError, UnicodeError):
                root = str(documents[0].parent.parent)
                body = documents[0].read_text(encoding="utf-8").replace(_PLUGIN_ROOT_VARIABLE, root)
                candidate = text.replace(_PLUGIN_ROOT_VARIABLE, root)
                if candidate.strip() == body.strip() and body in launch_prompt:
                    raise ActionableError(
                        "保持済みの役割文書が全文再掲されています。配送していません。",
                        next_action="今回の指示と新しい入力だけをpromptへ渡す",
                    )
        return text
    minimal_form = json.dumps(
        {"instruction": contract.instruction, "extra_params": {name: f"<{name}>" for name in contract.required}},
        ensure_ascii=False,
    )
    try:
        if instruction is not None and len(lines) != 1:
            raise ValueError(f"instructionに説明行が混在しています: {instruction}")
        params = dict(extra_params or {}) if instruction is not None else task_documents.parse_named_inputs(lines[1:])
    except ValueError as error:
        raise ActionableError(str(error), next_action="最小の再実行形: " + minimal_form) from error
    missing = [name for name in contract.required if not params.get(name)]
    undeclared = [name for name in params if name not in contract.accepted]
    minimal = {name: params.get(name, f"<{name}>") for name in contract.required}
    if missing or undeclared:
        raise ActionableError(
            f"継続入力の違反: 必須欠落={missing}; 宣言外の項目・行={undeclared}",
            next_action="最小の再実行形: "
            + json.dumps({"instruction": contract.instruction, "extra_params": minimal}, ensure_ascii=False),
        )
    for name in contract.path_lists:
        if name not in params:
            continue
        value = params[name]
        try:
            items = json.loads(value) if value.lstrip().startswith("[") else [value]
        except json.JSONDecodeError as error:
            raise ActionableError(
                f"パス配列入力{name}のJSONを読めません: {value}",
                next_action="最小の再実行形: " + minimal_form,
            ) from error
        if not isinstance(items, list) or not items:
            raise ActionableError(
                f"パス配列入力{name}には空でない配列が必要です: {value}",
                next_action="最小の再実行形: " + minimal_form,
            )
        for item in items:
            if not isinstance(item, str) or not pathlib.Path(item).is_absolute() or not pathlib.Path(item).is_file():
                corrected = {
                    **minimal,
                    name: json.dumps([f"<{name}の実在するファイルの絶対パス>"], ensure_ascii=False),
                }
                raise ActionableError(
                    f"パス配列入力{name}の要素を受理できません: {item!r}",
                    next_action="実在するファイルの絶対パス1件か、その非空JSON配列を渡す。最小の再実行形: "
                    + json.dumps({"instruction": contract.instruction, "extra_params": corrected}, ensure_ascii=False),
                )
    for name in (*contract.paths, *contract.references):
        if name not in params:
            continue
        value = params[name]
        path = pathlib.Path(value)
        if name in contract.references and not (path.is_absolute() or pathlib.PureWindowsPath(value).is_absolute()):
            continue
        if not path.is_absolute() or not path.is_file():
            raise ActionableError(
                f"パス入力{name}を受理できません: {value}",
                next_action=f"{name}へ実在するファイルの絶対パスだけを渡す。空白を含むファイル名はそのまま指定する",
            )
    return "\n".join([contract.instruction, *named_input_lines(params)])
