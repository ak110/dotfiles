from __future__ import annotations

import contextlib
import json
import pathlib
import re
from collections.abc import Mapping

from agent_toolkit._agents_server.launch_requests_inputs import _PLUGIN_ROOT_VARIABLE, named_input_lines
from agent_toolkit._common import runtime_environment, task_documents
from agent_toolkit._common.next_action import ActionableError
lazy from agent_toolkit._atk import review_table

_SUPPLEMENT_BOUNDARY = "\n\n## 補足\n\n"


def named_followup_body(prompt: str) -> str:
    return prompt.partition(_SUPPLEMENT_BOUNDARY)[0]


def _continuation_recovery(declaration: task_documents.TaskDocumentDeclaration | str | None) -> str:
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
    supplement: str | None = None,
) -> str:
    first_line = launch_prompt.split("\n", 1)[0]
    documents = task_documents.find_task_documents(first_line)
    declaration = task_documents.read_declaration(documents[0]) if documents else None
    if declaration is None and ".subagent.md" in first_line:
        declaration = "起動記録の役割文書へ到達できません"
    if supplement is not None and (instruction is None or prompt is not None):
        raise ActionableError(
            "supplementは宣言されたinstructionにだけ添えられます。追送は未配送です。",
            next_action="定型指示と名前付き入力にsupplementを添えるか、自由な指示なら補足もpromptに入れる",
        )
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
                first_line = followup.partition("\n")[0]
                body = (
                    named_followup_body(followup) if any(item.instruction == first_line for item in continuations) else followup
                )
                known_lines.update(body.splitlines())
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

        if documents and text.lstrip().startswith(("#", "---")):
            with contextlib.suppress(OSError, UnicodeError):
                root = str(task_documents.document_plugin_root(documents[0]))
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
    if contract.instruction == "再レビューせよ" and documents[0].name == "exec-review.subagent.md":
        _check_review_responses(launch_prompt, params["round"])
    body = "\n".join([contract.instruction, *named_input_lines(params)])
    return body if supplement is None else f"{body}{_SUPPLEMENT_BOUNDARY}{supplement}"


def _check_review_responses(launch_prompt: str, round_input: str) -> None:
    inputs = task_documents.task_prompt_inputs(launch_prompt)
    if plan := inputs.get("計画"):
        first = pathlib.Path(json.loads(plan)[0] if plan.lstrip().startswith("[") else plan)
        if not first.is_absolute():
            return
        table = first.with_suffix(".exec-review.tsv")
    elif inputs.get("CI記録"):
        oid = inputs.get("修正系列の開始時のHEAD", "")
        if not re.fullmatch("[0-9a-f]{7,64}", oid):
            return
        table = runtime_environment.home() / ".claude" / "plans" / f"ci-{oid}.exec-review.tsv"
    else:
        return
    try:
        table.stat()
    except FileNotFoundError:
        return
    try:
        round_value = int(round_input)
        if round_value < 1:
            raise ValueError
    except ValueError as error:
        raise ActionableError(
            "roundは1以上の整数で指定する。再レビューは未配送",
            next_action="今回のラウンド番号をroundに指定して再レビューを送り直す",
        ) from error
    review_table.require_recheck_responses(table, round_value)
