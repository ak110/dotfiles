"""配布する役割定義の静的検査。実行時の読取処理へ依存させない。"""

import dataclasses
import pathlib

import yaml

from agent_toolkit._common.task_documents import (
    INPUT_NAME_PATTERN,
    LAUNCH_KINDS,
    ContinuationDeclaration,
    TaskDocumentDeclaration,
)


def validate_frontmatter(task_document: pathlib.Path, document_text: str) -> TaskDocumentDeclaration | str:
    """配布する定義の静的整合性を試験で検査する。"""
    try:
        lines = document_text.splitlines()
        try:
            end = lines.index("---", 1)
        except ValueError as error:
            raise ValueError("frontmatterの終端がありません") from error
        body = "\n".join(lines[1:end])
        node = yaml.compose(body)
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("frontmatterはオブジェクトで記載する")
        keys = [key.value for key, _value in node.value]
        if len(set(keys)) != len(keys):
            raise ValueError("frontmatterのキーが重複しています")
        values = yaml.safe_load(body)
        required_fields = {"mode", "model_type", "required_inputs", "writes_worktree"}
        allowed_fields = required_fields | {"optional_inputs", "continuations"}
        if set(values) - allowed_fields or required_fields - set(values):
            raise ValueError(
                f"frontmatterの項目が不正です: 必須欠落={sorted(required_fields - set(values))}; "
                f"宣言外={sorted(set(values) - allowed_fields)}"
            )
        mode = values["mode"]
        if mode not in LAUNCH_KINDS:
            raise ValueError(f"modeは{LAUNCH_KINDS}から指定する")
        model_type = values["model_type"]
        if model_type not in ("execute", "review", "review_fast", "chores", "chores_fast", "orchestrate", "write"):
            raise ValueError("model_typeは工程別モデル種別から指定する")
        writes = values["writes_worktree"]
        if not isinstance(writes, bool):
            raise ValueError("writes_worktreeはtrueかfalseで指定する")

        def names(field: str) -> tuple[str, ...]:
            items = values.get(field, [])
            if (
                not isinstance(items, list)
                or any(not isinstance(item, str) or not INPUT_NAME_PATTERN.fullmatch(item) for item in items)
                or len(set(items)) != len(items)
            ):
                raise ValueError(f"{field}は重複しない入力名の配列で指定する")
            return tuple(items)

        required, optional = names("required_inputs"), names("optional_inputs")
        if set(required) & set(optional):
            raise ValueError("必須入力と任意入力が重複しています")
        raw_continuations = values.get("continuations", [])
        if not isinstance(raw_continuations, list):
            raise ValueError("continuationsは配列で指定する")
        continuations: list[ContinuationDeclaration] = []
        for entry in raw_continuations:
            continuation = ContinuationDeclaration(**entry)
            if not isinstance(continuation.instruction, str) or not continuation.instruction:
                raise ValueError("継続指示には空でない文字列が必要")
            if not isinstance(continuation.condition, str):
                raise ValueError("継続条件は文字列で指定する")
            for items in (
                continuation.required,
                continuation.optional,
                continuation.paths,
                continuation.path_lists,
                continuation.references,
            ):
                if not isinstance(items, (list, tuple)) or any(
                    not isinstance(item, str) or not INPUT_NAME_PATTERN.fullmatch(item) for item in items
                ):
                    raise ValueError("継続入力名は配列で指定する")
            if (
                not set(continuation.paths) <= continuation.accepted
                or not set(continuation.path_lists) <= continuation.accepted
                or bool(set(continuation.paths) & set(continuation.path_lists))
                or not set(continuation.references) <= continuation.accepted
                or any(item.instruction == continuation.instruction for item in continuations)
            ):
                raise ValueError("継続入力の指示またはパス入力が不正")
            continuations.append(
                dataclasses.replace(
                    continuation,
                    required=tuple(continuation.required),
                    optional=tuple(continuation.optional),
                    paths=tuple(continuation.paths),
                    path_lists=tuple(continuation.path_lists),
                    references=tuple(continuation.references),
                )
            )
        return TaskDocumentDeclaration(
            required=required,
            optional=optional,
            launch_kind=mode,
            writes_worktree=writes,
            continuations=tuple(continuations),
            model_type=model_type,
        )
    except (TypeError, ValueError, yaml.YAMLError) as error:
        return f"必須入力を確認できません: frontmatterが不正です: {task_document}: {error}"
