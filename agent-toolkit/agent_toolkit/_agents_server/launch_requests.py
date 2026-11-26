"""`start`の入力の検証、`<役割名>.subagent.md`の解決と委譲本文の組み立て、委譲先の起動前の事前確認。"""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import re
import unicodedata
import uuid
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import (
    launch_prompts,
    plugin_root,
    shared_roots,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.launch_requests_continuation import continuation_prompt
from agent_toolkit._agents_server.launch_requests_inputs import (
    _PLUGIN_ROOT_VARIABLE,
    _input_section,
    check_declared_inputs,
    named_input_lines,
    with_launcher_record,
    with_startup_documents,
    wrap_delivery_body,
)
from agent_toolkit._agents_server.launch_requests_preflight import (
    PREFLIGHT_COMMANDS,
    _check_plugin_commands_sync,
    check_plugin_commands,
)
from agent_toolkit._agents_server.state import (
    LaunchKind,
    SessionResumeState,
    SessionState,
)
from agent_toolkit._agents_server.tool_descriptions import HANDOFF_MODEL_INPUTS, START_MODE_EXAMPLES, START_MODE_INPUTS
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import delegated_session, runtime_environment, session_registry, startup_documents, task_documents
from agent_toolkit._common.markdown_headings import parse_headings, without_literal_blocks
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message, opening_tag
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import remote

__all__ = [
    "PREFLIGHT_COMMANDS",
    "_check_plugin_commands_sync",
    "_input_section",
    "check_plugin_commands",
    "continuation_prompt",
    "with_launcher_record",
    "with_startup_documents",
    "wrap_delivery_body",
]

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")

_REQUIRED_INPUT_NAME_PATTERN = task_documents.INPUT_NAME_PATTERN

_TASK_MODEL_TYPES = launch_prompts.TASK_MODEL_TYPES

_TASK_DOCUMENT_SUFFIX = task_documents.TASK_DOCUMENT_SUFFIX


_TASK_DOCUMENT_PATH_NEXT_ACTION = (
    "`subagent_md_path`へ役割名（`<役割名>.subagent.md`の`<役割名>`。例: `add-wi`）か、"
    "agent-toolkitの`<役割名>.subagent.md`の絶対パスを渡す。"
    "役割文書の指定を直し、`mode`を省略したtaskで`start`を再発行する"
)

_TASK_DOCUMENT_DEFECT_NEXT_ACTION = (
    "役割文書の読取を妨げた原因を解消し、同じ`subagent_md_path`を指定したtaskで`start`を再発行する。"
    "自身で修復できなければ、文書の所在と診断を委譲元（委譲元が無ければユーザー）へ返す"
)

MODEL_TYPE_NEXT_ACTION = (
    "`model_type`へ工程名（例: `chores`）か`<claude|codex|agy>:<model>[/<effort>]`、"
    "`opencode:<provider>/<model>[@<effort>]`の候補列を指定する。"
    "工程に対応する候補列は`atk config get <工程>_model`（例: `chores_model`）で確かめる"
)


def _is_agent_toolkit_task_document(path: pathlib.Path) -> bool:
    """agent-toolkit pluginの役割文書の置き場所の直下にある`<役割名>.subagent.md`だけを受理する。"""
    return task_documents.is_agent_toolkit_task_document(path)


def _accepted_role_names() -> list[str]:
    """サーバー自身のplugin rootにある`<役割名>.subagent.md`の役割名を返す。"""
    return [
        path.name.removesuffix(_TASK_DOCUMENT_SUFFIX) for path in task_documents.role_documents(plugin_root.SERVER_PLUGIN_ROOT)
    ]


def resolve_task_document_path(subagent_md_path: str) -> pathlib.Path:
    """`subagent_md_path`の絶対パスはそのまま、役割名はサーバー自身のplugin rootの役割文書へ解決する。

    置き場所は`task_documents.ROLE_DOCUMENT_DIRECTORY`が定める。

    委譲元は自身のシェルでplugin rootを解決できないことがあり、サーバーは自身のplugin rootを一意に持つため、
    役割名を受理して委譲元と同じ版の文書へ解決する。区切り文字か接尾辞を含む相対の値は役割名として扱わずに拒否する。
    """
    task_document = pathlib.Path(subagent_md_path)
    if task_document.is_absolute():
        if (
            task_document.parent.name == "share"
            and task_document.name.endswith(_TASK_DOCUMENT_SUFFIX)
            and task_documents.is_agent_toolkit_plugin_root(task_document.parent.parent)
        ):
            current = task_documents.find_role_document(plugin_root.SERVER_PLUGIN_ROOT, task_document.name)
            reason = f"subagent_md_pathがagent-toolkit旧版のshare/を参照しています: {task_document}"
            if current is not None:
                reason += f"。現在の役割文書: {current}"
            raise ActionableError(reason, next_action="プラグイン更新後は会話を再起動し、新しいスキル本文を読み直す")
        owner = task_documents.role_document_plugin_root(task_document)
        if owner is None:
            return task_document
        return plugin_root.resolve_stable_plugin_root(owner) / task_document.relative_to(owner)
    is_role_name = not any(part in subagent_md_path for part in ("/", "\\", _TASK_DOCUMENT_SUFFIX))
    if is_role_name and subagent_md_path:
        candidate = task_documents.find_role_document(
            plugin_root.SERVER_PLUGIN_ROOT, f"{subagent_md_path}{_TASK_DOCUMENT_SUFFIX}"
        )
        if candidate is not None:
            return candidate
    reason = "役割名に区切り文字か`.subagent.md`を含む" if not is_role_name else "役割名に対応する文書が無い"
    raise ActionableError(
        f"subagent_md_path is neither an absolute path nor an accepted role name: {subagent_md_path}（{reason}）; "
        f"受理する役割名: {', '.join(_accepted_role_names())}。委譲先は起動していない。",
        next_action=_TASK_DOCUMENT_PATH_NEXT_ACTION,
    )


def require_start_cwd() -> None:
    raise ActionableError(
        "通常起動にはcwdが必要です。委譲先は起動していない。",
        next_action="`cwd`へ既存ディレクトリの絶対パスを渡す。引き継ぎには`handoff_from`を使う。" + HANDOFF_MODEL_INPUTS,
    )


def validate_handoff_inputs(handoff_from: str, **inputs: Any) -> None:
    mixed = sorted(name for name, value in inputs.items() if value is not None and name != "model_type")
    if not handoff_from.strip() or mixed:
        raise ActionableError(
            f"handoff_fromの引き継ぎに前任IDとモデル指定以外の入力を混ぜません。混在した入力: {', '.join(mixed) or 'なし'}。",
            next_action=(
                "`start`の`handoff_from`へ前任session_idを渡し、cwdやmodeなど通常起動の引数を省く。" + HANDOFF_MODEL_INPUTS
            ),
        )


def validate_model_type_for_mode(mode: str, model_type: Any) -> None:
    if mode == "task" or not isinstance(model_type, str):
        return
    reserved = {"orchestrate"} if mode == "delegate" else {"execute", "orchestrate"}
    if model_type.strip() not in reserved:
        return
    raise ActionableError(
        f"mode={mode}では`model_type={model_type.strip()}`を指定できない。委譲先は起動していない。",
        next_action=(
            "実装・修正は`mode=delegate, model_type=execute`か、所定の`exec`の役割で起動する。"
            "その他の自律委譲は`review`・`review_fast`・`chores`・`chores_fast`から作業に合う工程を選ぶ。"
            "定型作業は`mode`を省略し、`subagent_md_path`に所定の役割を指定する"
        ),
    )


def validate_start_inputs(mode: str, **inputs: Any) -> None:
    if mode not in START_MODE_INPUTS:
        raise ActionableError(
            f"未知のmodeです: {mode}; 受理する値: {', '.join(START_MODE_INPUTS)}。",
            next_action="`mode`を省略してtaskで起動するか、受理する値のいずれかを指定する",
        )
    required, accepted = START_MODE_INPUTS[mode]
    given = {name for name, value in inputs.items() if value is not None and name != "model_type"}
    missing = [name for name in required if inputs.get(name) is None]
    unexpected = sorted(given - accepted)
    if not missing and not unexpected:
        validate_model_type_for_mode(mode, inputs.get("model_type"))
        return
    details = []
    if missing:
        details.append(f"mode={mode}に必要な入力が欠けています: {', '.join(missing)}")
    if unexpected:
        details.append(f"mode={mode}が受理しない入力が指定されています: {', '.join(unexpected)}")
    raise ActionableError(
        f"{'; '.join(details)}; mode={mode}の必須入力: {', '.join(required)}。委譲先は起動していない。",
        next_action=f"入力を直して`start`を再発行する。最小の呼び出し例: {START_MODE_EXAMPLES[mode]}",
    )


_LABEL_KANA = re.compile(r"[ぁ-ゖゝ-ゟァ-ヺー-ヿ々〻〇]")


def _valid_label(label: str) -> bool:
    if not 1 <= len(label) <= 32:
        return False
    return all(
        word
        and all(
            ("a" <= char <= "z" or "0" <= char <= "9")
            or _LABEL_KANA.fullmatch(char)
            or unicodedata.name(char, "").startswith(("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-"))
            for char in word
        )
        for word in label.split("-")
    )


def validate_start_label(label: str | None) -> None:
    if label is not None and not _valid_label(label):
        raise ActionableError(
            "labelは32文字以内の英小文字・数字・ひらがな・カタカナ・漢字で指定し、語をASCIIハイフンで区切ります。"
            "空白・改行・不可視文字や空の語は使えません。",
            next_action="例のようにlabelを直す（`lane-01-exec`、`設計レビュー`）か、省略して`start`を再実行する",
        )


def task_document_label(subagent_md_path: str, extra_params: Mapping[str, str]) -> str:
    name = pathlib.PurePath(subagent_md_path).name.removesuffix(_TASK_DOCUMENT_SUFFIX)
    lane = extra_params.get("レーン識別子", "").strip()
    label = f"{lane}-{name}" if lane else name
    return label if _valid_label(label) else "task"


def shell_default_label(command: str) -> str:
    words = command.split()
    label = f"shell-{pathlib.PurePath(words[0]).name}" if words else "shell"
    return label if _valid_label(label) else "shell"


_ADD_WI_DESTINATION = re.compile(
    r"^(?:[ \t]*[-*][ \t]+)?(?:共通:[ \t]*)?投入先(?:の対象リポジトリ)?(?:[：:]|[ \t])"
    r"[ \t]*(?P<destination>[^\r\n]*)$",
    re.MULTILINE,
)
_ADD_WI_COMMON_INPUT = re.compile(
    r"^(?:[ \t]*[-*][ \t]+)?共通(?:の入力|入力)?[：:][ \t]*(?P<path>[^`\r\n]+|`[^`\r\n]+`)[ \t]*$",
    re.MULTILINE,
)
_ADD_WI_REQUIREMENT = re.compile(r"^##[ \t]+要求(?:[ \t：:]|$)", re.MULTILINE)


def _read_add_wi_input(path: pathlib.Path) -> str:
    try:
        if not path.is_absolute() or not path.is_file():
            raise OSError("要求ファイルは通常のファイルの絶対パスでなければなりません")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ActionableError(
            "投入する要求か共通入力のファイルを読み取れません。委譲先は起動していない。",
            next_action="UTF-8の要求ファイルと共通入力の絶対パスを確認して同じstartを再発行する",
        ) from error


def _add_wi_destination_fields(text: str) -> list[str]:
    return [match["destination"] for match in _ADD_WI_DESTINATION.finditer(text)]


def _check_add_wi_destinations(task_document: pathlib.Path, extra_params: Mapping[str, str]) -> None:
    request = extra_params.get("投入する要求")
    if task_document.name != "add-wi.subagent.md" or request is None:
        return
    text = request.strip()
    if "\n" not in text and pathlib.Path(text).is_absolute():
        text = _read_add_wi_input(pathlib.Path(text))
    text = without_literal_blocks(text).strip()
    preamble, *requirements = _ADD_WI_REQUIREMENT.split(text)
    common = _add_wi_destination_fields(preamble)
    for reference in _ADD_WI_COMMON_INPUT.finditer(preamble):
        reference_path = pathlib.Path(reference["path"].strip().strip("`"))
        if reference_path.is_absolute():
            common.extend(_add_wi_destination_fields(without_literal_blocks(_read_add_wi_input(reference_path))))
    if not common and not requirements:
        parts = text.partition("\n")[0].split("=", maxsplit=3)
        if len(parts) == 4 and parts[2].casefold() in {"awi", "uwi"}:
            common = [parts[1]]
    individual = [_add_wi_destination_fields(section) for section in requirements]
    if (
        all(_add_wi_destination_is_resolved(value) for value in common)
        and (all(fields or common for fields in individual) if individual else bool(common))
        and all(_add_wi_destination_is_resolved(value) for fields in individual for value in fields)
    ):
        return
    raise ActionableError(
        "add-wiの投入先に絶対パスか正規化したリモートURLが必要です。委譲先は起動していない。",
        next_action="委譲元が投入先を確定し、要求ファイルか直接本文を直して同じstartを再発行する",
    )


def _add_wi_destination_is_resolved(value: str) -> bool:
    location = re.split(r"[（、]", value, maxsplit=1)[0].strip().strip("`")
    with contextlib.suppress(ValueError):
        return pathlib.Path(location).is_absolute() or (
            not any(character.isspace() for character in location) and remote.normalize_remote_url(location) == location
        )
    return False


def _validate_required_prompt_inputs(
    task_document: pathlib.Path,
    extra_params: Mapping[str, str],
    document_text: str | None = None,
) -> str | None:
    """`<役割名>.subagent.md`の宣言と名前付き入力が一致するか確かめ、宣言を読めない場合だけ警告文を返す。

    必須入力の欠落と宣言外の入力名は委譲先を起動せず`ValueError`で拒否する。
    """
    declaration = plugin_root.SERVER_RUNTIME_CODE.check_role(
        "役割宣言の検査", task_document, lambda: _task_document_declaration(task_document, document_text)
    )
    if isinstance(declaration, str):
        return declaration
    check_declared_inputs(task_document, declaration, extra_params)
    _check_add_wi_destinations(task_document, extra_params)
    return None


def _task_document_declaration(
    task_document: pathlib.Path,
    document_text: str | None,
) -> task_documents.TaskDocumentDeclaration | str:
    """`<役割名>.subagent.md`の宣言を読む。共有規則の判定は`_is_agent_toolkit_task_document`を経由する。"""
    if not _is_agent_toolkit_task_document(task_document):
        return (
            f"必須入力を確認できません: `<役割名>.subagent.md`がagent-toolkitの役割文書の置き場所にありません: {task_document}"
        )
    declaration = task_documents.read_declaration_unchecked(task_document, document_text)
    if isinstance(declaration, str):
        with contextlib.suppress(OSError, UnicodeError):
            body = document_text if document_text is not None else task_document.read_text(encoding="utf-8")
            if body.startswith("---"):
                raise ActionableError(
                    declaration,
                    next_action="役割文書のfrontmatterを直して同じstartを再発行する。委譲先は起動していない",
                )
    return declaration


def role_model_was_explicit(session: SessionState | SessionResumeState) -> bool:
    if session.explicit_role_model is not None:
        return session.explicit_role_model
    if not session.model_type:
        return False
    if ":" in session.model_type:
        return True
    original = task_documents.launched_task_document(session.prompt)
    if original is None:
        return False
    _, _, document = session.prompt.partition(task_documents.TASK_PROMPT_REFERENCES)
    launcher_tag = opening_tag(AUTO_INSERTED_ELEMENT, {"source": "agents-server", "kind": "launcher-record"})
    if document.startswith(launcher_tag + "\n"):
        _, _, document = document.partition(f"\n</{AUTO_INSERTED_ELEMENT}>\n\n")
    declared = task_documents.read_declaration_unchecked(original, document)
    return (
        isinstance(declared, task_documents.TaskDocumentDeclaration)
        and declared.model_type is not None
        and declared.model_type != session.model_type
    )


def current_role_model(session: SessionState | SessionResumeState, *, include_explicit: bool = False) -> str | None:
    if not session.model_type or (not include_explicit and role_model_was_explicit(session)):
        return None
    original = task_documents.launched_task_document(session.prompt)
    if original is None:
        return None
    current = task_documents.find_role_document(plugin_root.SERVER_PLUGIN_ROOT, original.name) or original
    declaration = task_documents.read_declaration(current)
    return declaration.model_type if isinstance(declaration, task_documents.TaskDocumentDeclaration) else None


async def reject_stale_role_reply(session: SessionState | SessionResumeState) -> None:
    if session.status != "completed":
        return
    original = task_documents.launched_task_document(session.prompt)
    if original is None:
        return
    current = await run_file_operation(current_role_model, session, include_explicit=True)
    reason = "現在の役割の工程を確認できない" if current is None else None
    if current is not None:
        if current != session.model_type and not role_model_was_explicit(session):
            reason = f"役割のmodel_typeが{session.model_type}から{current}へ変更された"
        else:
            try:
                if not await run_file_operation(_configured_role_contains, session, original, current):
                    reason = f"終了した担当のモデルが現行の工程{current}の候補に含まれない"
            except (OSError, ValueError, RuntimeError) as error:
                reason = f"現行の工程{current}と起動情報を比較できない: {error}"
    if reason is not None and session.status == "completed":
        phase_input = (
            f"`model_type`へ現行の工程名`{current}`を渡す" if current is not None else "役割の現行工程が確定したら引き継ぐ"
        )
        raise ActionableError(
            f"{reason}ため、追送は配送していない: {session.session_id}",
            next_action=(
                f"工程と候補を確認し、`start`の`handoff_from`へ`{session.session_id}`を、"
                + phase_input
                + "。今回だけの本人指定は後任起動で根拠を照合し、後任へ未配送の指示を渡す"
                + ("。`write`は後任への実際のユーザー指示の根拠がある場合だけ指定する" if current == "write" else "")
            ),
        )


def _configured_role_contains(session: SessionState | SessionResumeState, role: pathlib.Path, phase: str) -> bool:
    if session.model is None or session.effort is None:
        raise ValueError("終了した担当のモデルまたはeffortが記録されていない")
    candidate = session.engine, session.model, session.effort
    exec_lane = role.name == "exec.subagent.md" and "担当種別: レーン担当" in session.prompt.splitlines()
    configured = _atk_config.parse_unresolved_model_candidates(phase, exec_lane=exec_lane)
    same_engine = [item for item in configured if item[0] == session.engine]
    return candidate in same_engine or candidate in _atk_config.resolve_catalog(same_engine)


def builtin_task_prompt(subagent_md_path: str, extra_params: Mapping[str, str]) -> str:
    document = resolve_task_document_path(subagent_md_path)
    warning = _validate_required_prompt_inputs(document, extra_params)
    if warning is not None:
        _LOG.warning("%s", warning)
    return "\n".join([f"{document}の手順を実行せよ。", *named_input_lines(extra_params)])


_HANDOFF_INPUT_NAME = "引き継ぎ記録先"

_HANDOFF_TEMP_PREFIX = "handoff"

_HANDOFF_SESSION: tuple[str | None] | None = None


def capture_handoff_session() -> None:
    global _HANDOFF_SESSION  # pylint: disable=global-statement
    if _HANDOFF_SESSION is None:
        _HANDOFF_SESSION = ((shared_roots.resolve_root_session_id(os.environ) or os.environ.get("CODEX_THREAD_ID")),)


def _owner_conversation(launcher_session_id: str | None) -> str | None:
    environment = runtime_environment.environment()
    captured = (
        shared_roots.resolve_root_session_id(environment) or environment.get("CODEX_THREAD_ID")
        if runtime_environment.current() is not None or _HANDOFF_SESSION is None
        else _HANDOFF_SESSION[0]
    )
    for candidate in (captured, launcher_session_id):
        if candidate and not candidate.startswith(shared_roots.PROCESS_ROOT_PREFIX):
            return candidate
    return None


def _conversation_temp(session_id: str) -> pathlib.Path | None:
    with contextlib.suppress(_managed_temp.ManagedTempError, OSError):
        return _managed_temp.find_session_temp(session_id)
    return None


def _default_handoff_path(launcher_session_id: str | None = None) -> pathlib.Path | None:
    directory: pathlib.Path | None = None
    session_id = _owner_conversation(launcher_session_id)
    if session_id is not None:
        with contextlib.suppress(_managed_temp.ManagedTempError, OSError):
            directory = _managed_temp.resolve_session_temp(session_id)
    if directory is None:
        try:
            directory = _managed_temp.create_managed_temp(_HANDOFF_TEMP_PREFIX)
        except _managed_temp.ManagedTempError, OSError:
            _LOG.warning("省略された引き継ぎ記録先の代わりの記録先を用意できません", exc_info=True)
            return None
    while True:
        candidate = directory / f"handoff-{uuid.uuid4().hex[:12]}.md"
        if not candidate.exists():
            return candidate


def delegate_request(
    prompt: str,
    *,
    whole_contract_path: str | None = None,
    launcher_session_id: str | None = None,
) -> tuple[str, pathlib.Path]:
    """自由本文を保ち、通常委譲へ専用の現行記録先と全体作業契約の参照を配送する。"""
    whole_contract = whole_contract_reference(whole_contract_path=whole_contract_path, launcher_session_id=launcher_session_id)
    present, explicit = delegated_session.parse_prompt_handoff_path(prompt)
    if present and explicit is None:
        raise ActionableError(
            "引き継ぎ記録先を絶対パスとして読めません。委譲先は起動していない。",
            next_action="引き継ぎ記録先の行を絶対パスへ直すか、行を削ってstartを再発行する",
        )
    path = pathlib.Path(explicit) if explicit is not None else _default_handoff_path(launcher_session_id)
    if path is None:
        raise ActionableError(
            "通常委譲の作業記録先を用意できません。委譲先は起動していない。",
            next_action="managed-tempの診断を確認し、記録先を確保してからstartを再発行する",
        )
    return prompt + "\n\n" + handoff_notice(
        None if present else path, plugin_root.SERVER_PLUGIN_ROOT
    ) + "\n\n" + whole_contract, path


_WHOLE_CONTRACT_REISSUE = "`whole_contract_path`へ既存の全体作業契約の絶対パスを指定して`start`を再発行する"


def _inherited_whole_contract(launcher_session_id: str | None) -> pathlib.Path | None:
    """直接の委譲元sessionが起動時に受領した全体作業契約の参照を、session登録簿の起動本文から返す。

    委譲先自身の保存領域は全体記録を所有しないため、孫へは委譲元が受領した同じ全体記録を渡す。
    資源を解放した記録は`handoff_info`の起動本文を読む。
    """
    if launcher_session_id is None:
        return None
    try:
        resolution = session_registry.resolve(launcher_session_id)
    except ValueError, OSError:
        return None
    for info in (resolution.resume_info, resolution.handoff_info):
        if info is not None and (path := delegated_session.prompt_whole_contract_path(info.launch_info.prompt)) is not None:
            return path
    return None


def _explicit_whole_contract(value: str) -> pathlib.Path:
    """明示された全体作業契約のパスを、起動前に読める通常ファイルであることを確かめて返す。"""
    path = pathlib.Path(value)
    reason: str | None = None
    if not path.is_absolute():
        reason = "絶対パスではない"
    elif not path.exists():
        reason = "存在しない"
    elif not path.is_file():
        reason = "通常ファイルではない"
    else:
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            reason = f"UTF-8で読めない（{error}）"
    if reason is not None:
        raise ActionableError(
            f"whole_contract_pathの全体作業契約を使えません: {value}: {reason}。委譲先は起動していない。",
            next_action=(
                "既存の全体作業契約の絶対パスを`whole_contract_path`へ指定するか、"
                "`skills/atk-mode/references/work-contract.md`に従い、目的・完遂事項・守る制約をそのパスへ記録して同じ入力で`start`を再発行する"
            ),
        )
    return path


def _resolve_whole_contract(whole_contract_path: str | None, launcher_session_id: str | None) -> pathlib.Path:
    """全体作業契約を、明示入力、委譲元の受領済み参照、所有会話の`work-contract.md`の順に解決する。

    いずれも成立しない場合と、期待パスが確定して未作成の場合は起動前に拒否する。
    未解決のまま担当を起動すると、担当が委譲元へ所在を照会する往復が生じるためである。
    """
    if whole_contract_path is not None:
        return _explicit_whole_contract(whole_contract_path)
    expected = _inherited_whole_contract(launcher_session_id)
    if expected is None:
        owner = _owner_conversation(launcher_session_id)
        directory = _conversation_temp(owner) if owner is not None else None
        if directory is None:
            reason = (
                "全体作業契約を所有する会話を特定できません"
                if owner is None
                else f"全体作業契約を所有する会話の保存領域を解決できません: {owner}"
            )
            raise ActionableError(f"{reason}。委譲先は起動していない。", next_action=_WHOLE_CONTRACT_REISSUE)
        expected = directory / "work-contract.md"
    if not expected.is_file():
        raise ActionableError(
            f"全体作業契約が未作成です: {expected}。"
            "委譲先が作業が長引いた時や圧縮の後に目的を見失わないよう、契約の無い起動は拒否する。委譲先は起動していない。",
            next_action=(
                "`skills/atk-mode/references/work-contract.md`に従い、目的・完遂事項・守る制約を期待パスへ記録して`start`を再発行する。"
                f"別の記録を使う場合は{_WHOLE_CONTRACT_REISSUE}"
            ),
        )
    return expected


def whole_contract_reference(
    *,
    whole_contract_path: str | None = None,
    launcher_session_id: str | None = None,
) -> str:
    """解決済みの全体契約の参照と本文を、担当の判断が始まる前に同じ起動本文へ届ける。"""
    path = _resolve_whole_contract(whole_contract_path, launcher_session_id)
    body = (
        f"{delegated_session.WHOLE_CONTRACT_LINE_PREFIX}{path}\n"
        "直後の要素に全体作業契約の本文がある。同じ版を取り直さず、割当が全体の要求を満たす範囲と、維持する成果・検収に使う証拠を確かめる。"
        "全体記録の更新は委譲元が所有し、委譲先は自身の引き継ぎ記録へ判断を残す。"
    )
    return (
        auto_message(body, source="agents-server", kind=delegated_session.WHOLE_CONTRACT_KIND)
        + "\n\n"
        + startup_documents.render([path])
    )


def handoff_notice(path: pathlib.Path | None, root: pathlib.Path) -> str:
    record_line = f"引き継ぎ記録先: {path}（新規）\n" if path is not None else ""
    return auto_message(
        record_line + "継続なら最初に自分の引き継ぎ記録を読む。新規で書く値が無ければ作成しない。"
        "未回収の実行、Git管理外の成果物、再開位置、役割固有の項目などを記録する時は、"
        f"{root / 'skills/atk-mode/references/work-contract.md'}「引き継ぎ記録」に従う。",
        source="agents-server",
        kind="work-contract",
    )


def task_document_request(
    subagent_md_path: str,
    extra_params: Mapping[str, str],
    *,
    whole_contract_path: str | None = None,
    launcher_session_id: str | None = None,
) -> tuple[str, str, LaunchKind, pathlib.Path | None]:
    """役割と名前付き入力からmodel種別、プロンプト、mode、サーバーが用意した引き継ぎ記録先を返す。

    `<役割名>.subagent.md`が`引き継ぎ記録先`を必須入力とし、`extra_params`がこれを持たない場合は、
    `（新規）`の記録先を用意してプロンプトへ加え、絶対パスを4要素目で返す。
    宣言しない通常起動にも共通入力として専用の記録先を加える。
    委譲元が値を渡した場合と軽量起動の4要素目は`None`とする。
    `whole_contract_path`と`launcher_session_id`は通常起動の全体作業契約の解決（`whole_contract_reference`）へ渡す。
    起動の本文へ入れる資料（`startup_documents`）は、起動の候補ごとのhostとcwdで解決するため、`start`が加える。
    """
    task_document = resolve_task_document_path(subagent_md_path).resolve()
    if not task_document.is_file() or not task_document.name.endswith(".subagent.md"):
        raise ActionableError(
            f"subagent_md_path is not an existing .subagent.md file: {task_document}",
            next_action=_TASK_DOCUMENT_PATH_NEXT_ACTION,
        )
    if not _is_agent_toolkit_task_document(task_document):
        raise ActionableError(
            f"subagent_md_path is not an agent-toolkit task document: {task_document}",
            next_action=_TASK_DOCUMENT_PATH_NEXT_ACTION,
        )
    if any(not isinstance(name, str) or not _REQUIRED_INPUT_NAME_PATTERN.fullmatch(name) for name in extra_params):
        raise ActionableError(
            "extra_params contains an invalid input name",
            next_action=(
                "呼び元の起動・受領契約から入力名を確定して`extra_params`のキーへ渡す。"
                "キーには"
                "空白・バッククォート・読点を含めず、"
                "先頭と末尾をコロンにしない"
            ),
        )
    if any(not isinstance(value, str) for value in extra_params.values()):
        raise ActionableError("extra_params values must be strings", next_action="`extra_params`の値を全て文字列で渡す")
    try:
        document_text = task_document.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ActionableError(
            f"`<役割名>.subagent.md`をUTF-8で読めません: {task_document}: {error}",
            next_action=_TASK_DOCUMENT_DEFECT_NEXT_ACTION,
        ) from error
    # 委譲先は配送された本文だけを読むため、プラグインルートの変数を配送側で解決する。
    # 未展開のまま渡すと、委譲先は変数の値を推測して参照先を探す。
    document_text = document_text.replace(_PLUGIN_ROOT_VARIABLE, str(task_documents.document_plugin_root(task_document)))
    declaration = plugin_root.SERVER_RUNTIME_CODE.check_role(
        "役割宣言の検査", task_document, lambda: _task_document_declaration(task_document, document_text)
    )
    model_type = declaration.model_type if isinstance(declaration, task_documents.TaskDocumentDeclaration) else None
    # 旧版の文書と保存済みsessionだけは、移行前の役割設定で継続できるようにする。
    if model_type is None:
        model_type = _TASK_MODEL_TYPES.get(task_document.name)
    if model_type is None:
        raise ActionableError(
            f"subagent task has no model_type declaration: {task_document.name}",
            next_action="役割文書のfrontmatterへmodel_typeを宣言して同じstartを再発行する",
        )
    launch_kind: LaunchKind = declaration.launch_kind if not isinstance(declaration, str) else "delegate"

    whole_contract = (
        whole_contract_reference(whole_contract_path=whole_contract_path, launcher_session_id=launcher_session_id)
        if launch_kind == "delegate"
        else None
    )
    handoff_path: pathlib.Path | None = None
    if isinstance(declaration, str):
        _LOG.warning("%s", declaration)
    else:
        if _HANDOFF_INPUT_NAME in declaration.required and _HANDOFF_INPUT_NAME not in extra_params:
            handoff_path = _default_handoff_path(launcher_session_id)
            if handoff_path is not None:
                extra_params = {**extra_params, _HANDOFF_INPUT_NAME: f"{handoff_path}（新規）"}
        check_declared_inputs(task_document, declaration, extra_params)
        _check_add_wi_destinations(task_document, extra_params)

    if launch_kind == "delegate" and _HANDOFF_INPUT_NAME not in extra_params:
        handoff_path = _default_handoff_path(launcher_session_id)
        if handoff_path is None:
            raise ActionableError(
                "通常委譲の作業記録先を用意できません。委譲先は起動していない。",
                next_action="managed-tempの診断を確認し、記録先を確保してからstartを再発行する",
            )
    inputs = dict(extra_params)
    if handoff_path is not None and _HANDOFF_INPUT_NAME not in inputs:
        inputs[_HANDOFF_INPUT_NAME] = f"{handoff_path}（新規）"
    role = task_document.name.removesuffix(_TASK_DOCUMENT_SUFFIX)

    heading_body = document_text.partition("\n---\n")[2] if document_text.startswith("---\n") else document_text
    title = next((title for token, title in parse_headings(heading_body, 1) if token.level == 0), "")
    role_name = f"{' '.join(title.splitlines())}（{role}）" if title else role
    prompt_lines = [
        task_documents.task_prompt_header(task_document),
        f"役割: {role_name}",
        "入力:",
        *named_input_lines(inputs),
        task_documents.TASK_PROMPT_REFERENCES.strip("\n"),
        document_text,
    ]
    if launch_kind == "delegate":
        notice_path = handoff_path if _HANDOFF_INPUT_NAME not in extra_params else None
        prompt_lines.append(handoff_notice(notice_path, task_documents.document_plugin_root(task_document)))
        assert whole_contract is not None
        prompt_lines.append(whole_contract)
    return model_type, "\n".join(prompt_lines), launch_kind, handoff_path
