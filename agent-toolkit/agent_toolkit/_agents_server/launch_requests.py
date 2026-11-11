"""`start`の入力の検証、`<役割名>.subagent.md`の解決と委譲本文の組み立て、委譲先の起動前の事前確認。"""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import uuid
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import (
    launch_prompts,
    plugin_root,
    shared_roots,
)
from agent_toolkit._agents_server.launch_requests_continuation import continuation_prompt
from agent_toolkit._agents_server.launch_requests_inputs import (
    _PLUGIN_ROOT_VARIABLE,
    _check_declared_inputs,
    _input_section,
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
)
from agent_toolkit._agents_server.tool_descriptions import START_MODE_EXAMPLES, START_MODE_INPUTS
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import delegated_session, read_triggers, session_registry, task_documents
from agent_toolkit._common.markdown_headings import parse_headings
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message, opening_tag
from agent_toolkit._common.next_action import ActionableError

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

_SHARE_DIRECTORY = plugin_root.SERVER_PLUGIN_ROOT / "share"

_TASK_MODEL_TYPES = launch_prompts.TASK_MODEL_TYPES

_TASK_DOCUMENT_SUFFIX = task_documents.TASK_DOCUMENT_SUFFIX


# 委譲元がツールのエラー本文や状態値だけで次の行動を決められるよう、応答へ載せる次の操作の文面。
# 同じ状況を複数の処理が返すため、処理ごとに書き分けず1か所へ置く。
_TASK_DOCUMENT_PATH_NEXT_ACTION = (
    "`subagent_md_path`へ役割名（`share/<役割名>.subagent.md`の`<役割名>`。例: `add-wi`）か、"
    "agent-toolkitの`share/*.subagent.md`の絶対パスを渡す。"
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
    """agent-toolkit pluginのshare直下にある`<役割名>.subagent.md`だけを受理する。"""
    return task_documents.is_agent_toolkit_task_document(path)


def _accepted_role_names() -> list[str]:
    """サーバー自身のplugin rootの`share/`直下にある`<役割名>.subagent.md`の役割名を返す。"""
    return sorted(path.name.removesuffix(_TASK_DOCUMENT_SUFFIX) for path in _SHARE_DIRECTORY.glob(f"*{_TASK_DOCUMENT_SUFFIX}"))


def resolve_task_document_path(subagent_md_path: str) -> pathlib.Path:
    """`subagent_md_path`の絶対パスはそのまま、役割名はサーバー自身のplugin rootの`share/`直下の文書へ解決する。

    委譲元は自身のシェルでplugin rootを解決できないことがあり、サーバーは自身のplugin rootを一意に持つため、
    役割名を受理して委譲元と同じ版の文書へ解決する。区切り文字か接尾辞を含む相対の値は役割名として扱わずに拒否する。
    """
    task_document = pathlib.Path(subagent_md_path)
    if task_document.is_absolute():
        root = plugin_root.resolve_stable_plugin_root(task_document.parent.parent)
        return root / task_document.parent.name / task_document.name
    is_role_name = not any(part in subagent_md_path for part in ("/", "\\", _TASK_DOCUMENT_SUFFIX))
    candidate = _SHARE_DIRECTORY / f"{subagent_md_path}{_TASK_DOCUMENT_SUFFIX}"
    if is_role_name and subagent_md_path and candidate.is_file():
        return candidate
    reason = "役割名に区切り文字か`.subagent.md`を含む" if not is_role_name else "役割名に対応する文書が無い"
    raise ActionableError(
        f"subagent_md_path is neither an absolute path nor an accepted role name: {subagent_md_path}（{reason}）; "
        f"受理する役割名: {', '.join(_accepted_role_names())}。委譲先は起動していない。",
        next_action=_TASK_DOCUMENT_PATH_NEXT_ACTION,
    )


def require_start_cwd() -> None:
    """通常起動のcwdが欠けた場合に、受理する起動形を示す。"""
    raise ActionableError(
        "通常起動にはcwdが必要です。委譲先は起動していない。",
        next_action="`cwd`へ既存ディレクトリの絶対パスを渡す。引き継ぎ起動では`handoff_from`だけを指定する",
    )


def validate_handoff_inputs(handoff_from: str, **inputs: Any) -> None:
    """引き継ぎ起動と通常起動の入力を排他的に受理する。"""
    mixed = sorted(name for name, value in inputs.items() if value is not None)
    if not handoff_from.strip() or mixed:
        raise ActionableError(
            f"handoff_fromには前任IDだけを指定します。混在した入力: {', '.join(mixed) or 'なし'}。",
            next_action='`start`へ`{"handoff_from": "<前任session_id>"}`だけを渡す',
        )


RESERVED_FREEFORM_MODEL_TYPES = frozenset({"execute", "orchestrate"})


def _reject_reserved_freeform_model_type(mode: str, model_type: Any) -> None:
    """自由本文の起動が役割文書専用の工程名を指定した場合に、起動前に拒否する。"""
    if mode == "task" or not isinstance(model_type, str) or model_type.strip() not in RESERVED_FREEFORM_MODEL_TYPES:
        return
    raise ActionableError(
        f"mode={mode}では`model_type={model_type.strip()}`を指定できない。委譲先は起動していない。",
        next_action=(
            "自分の判断で起動する委譲は`model_type`に`review`・`review_fast`・`chores`・`chores_fast`・`write`の"
            "いずれかを指定して再発行する。役割文書（`<役割名>.subagent.md`）を持つ定型作業は`mode`を省略したtaskで"
            "`subagent_md_path`を指定して起動する"
        ),
    )


def validate_start_inputs(mode: str, **inputs: Any) -> None:
    """modeが必要とする入力の欠落と、modeが受理しない入力の混在を、委譲先の起動前に拒否する。"""
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
        _reject_reserved_freeform_model_type(mode, inputs.get("model_type"))
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


def task_document_label(subagent_md_path: str, extra_params: Mapping[str, str]) -> str:
    """`start`のlabel省略時の識別名を役割名とレーン識別子から組み立てる。"""
    name = pathlib.PurePath(subagent_md_path).name.removesuffix(_TASK_DOCUMENT_SUFFIX)
    lane = extra_params.get("レーン識別子", "").strip()
    return f"{lane}-{name}" if lane else name


def shell_default_label(command: str) -> str:
    """shellのlabel省略時の識別名を、コマンドの最初の語のbasenameから組み立てる。"""
    words = command.split()
    return f"shell-{pathlib.PurePath(words[0]).name}" if words else "shell"


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
    _check_declared_inputs(task_document, declaration, extra_params)
    return None


def _task_document_declaration(
    task_document: pathlib.Path,
    document_text: str | None,
) -> task_documents.TaskDocumentDeclaration | str:
    """`<役割名>.subagent.md`の宣言を読む。共有規則の判定は`_is_agent_toolkit_task_document`を経由する。"""
    if not _is_agent_toolkit_task_document(task_document):
        return f"必須入力を確認できません: `<役割名>.subagent.md`がshare配下ではありません: {task_document}"
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


def builtin_task_prompt(subagent_md_path: str, extra_params: Mapping[str, str]) -> str:
    """組み込み委譲へ渡す、役割参照と宣言済み入力だけの本文を生成する。"""
    document = resolve_task_document_path(subagent_md_path)
    warning = _validate_required_prompt_inputs(document, extra_params)
    if warning is not None:
        _LOG.warning("%s", warning)
    return "\n".join([f"{document}の手順を実行せよ。", *named_input_lines(extra_params)])


_HANDOFF_INPUT_NAME = "引き継ぎ記録先"

_HANDOFF_TEMP_PREFIX = "handoff"

_HANDOFF_SESSION: tuple[str | None] | None = None


def capture_handoff_session() -> None:
    """会話contextの除去前に、引き継ぎ記録を置く委譲元sessionを確定する。"""
    global _HANDOFF_SESSION  # pylint: disable=global-statement
    if _HANDOFF_SESSION is None:
        _HANDOFF_SESSION = ((shared_roots.resolve_root_session_id(os.environ) or os.environ.get("CODEX_THREAD_ID")),)


def _owner_conversation(launcher_session_id: str | None) -> str | None:
    """全体記録と専用記録先を置く会話の識別子を返す。

    起動時に保持した識別子が会話を表す場合はそれを使う。
    プロセス専用ルート（`mcp-`形式）か識別子なしの場合は、Managerが解決した実際の委譲元会話を使う。
    共有状態の名前空間は会話と一致しないことがあるため、名前空間の識別子を会話の代わりに使わない。
    """
    captured = (
        _HANDOFF_SESSION[0]
        if _HANDOFF_SESSION is not None
        else (shared_roots.resolve_root_session_id(os.environ) or os.environ.get("CODEX_THREAD_ID"))
    )
    for candidate in (captured, launcher_session_id):
        if candidate and not candidate.startswith(shared_roots.PROCESS_ROOT_PREFIX):
            return candidate
    return None


def _conversation_temp(session_id: str) -> pathlib.Path | None:
    """会話の既存managed-tempを作成せずに返す。解決できなければ`None`を返す。"""
    with contextlib.suppress(_managed_temp.ManagedTempError, OSError):
        return _managed_temp.find_session_temp(session_id)
    return None


def _default_handoff_path(launcher_session_id: str | None = None) -> pathlib.Path | None:
    """`（新規）`の引き継ぎ記録先として、委譲元のセッションのmanaged-temp直下の未使用のファイルパスを返す。

    所有会話（`_owner_conversation`）を特定できる場合はそのmanaged-tempを直接解決して再利用する。
    会話を特定できない起動では、個別のhandoff領域を作成する。
    どちらも得られない場合は`None`を返し、呼び出し元は従来どおり欠落として拒否する。
    ファイル自体は作成しない。`（新規）`の記録先は委譲先が作成するためである。
    """
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


_WHOLE_CONTRACT_KIND = "work-contract-reference"

_WHOLE_CONTRACT_LINE_PREFIX = "全体作業契約: "

_WHOLE_CONTRACT_OPENING = opening_tag(AUTO_INSERTED_ELEMENT, {"source": "agents-server", "kind": _WHOLE_CONTRACT_KIND})

_WHOLE_CONTRACT_REISSUE = "`whole_contract_path`へ既存の全体作業契約の絶対パスを指定して`start`を再発行する"


def _delivered_whole_contract(prompt: str) -> pathlib.Path | None:
    """配送済みの起動本文から、サーバーが加えた全体作業契約の絶対パスを取り出す。

    依頼本文が同じ語を含んでも取り違えないよう、サーバーが付けた要素の内側だけを読む。
    旧形式の未解決の通知と要素の不在は`None`とする。
    """
    start = prompt.rfind(_WHOLE_CONTRACT_OPENING)
    if start < 0:
        return None
    first_line = prompt[start + len(_WHOLE_CONTRACT_OPENING) :].lstrip("\n").split("\n", 1)[0]
    if not first_line.startswith(_WHOLE_CONTRACT_LINE_PREFIX):
        return None
    path = pathlib.Path(first_line.removeprefix(_WHOLE_CONTRACT_LINE_PREFIX).strip())
    return path if path.is_absolute() else None


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
        if info is not None and (path := _delivered_whole_contract(info.launch_info.prompt)) is not None:
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
                "`share/work-contract.md`に従ってそのパスへ記録を作成し、同じ入力で`start`を再発行する"
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
            f"全体作業契約が未作成です: {expected}。委譲先は起動していない。",
            next_action=(
                f"`share/work-contract.md`に従って期待パスへ全体作業契約を作成し、`start`を再発行する。"
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
        f"{_WHOLE_CONTRACT_LINE_PREFIX}{path}\n"
        "担当工程の開始時に読み、割当が全体の要求を満たす範囲と、維持する成果・検収に使う証拠を確かめる。"
        "全体記録の更新は委譲元が所有し、委譲先は自身の引き継ぎ記録へ判断を残す。"
    )
    target = read_triggers.ReadTarget(path)
    delivered = read_triggers.startup_documents([target], plugin_root.SERVER_PLUGIN_ROOT, {target: {"input:作業契約"}})
    return auto_message(body, source="agents-server", kind=_WHOLE_CONTRACT_KIND) + "\n\n" + delivered


def handoff_notice(path: pathlib.Path | None, root: pathlib.Path) -> str:
    """サーバーが所有する共通入力を、役割文書の入力宣言から分離して加える。"""
    record_line = f"引き継ぎ記録先: {path}（新規）\n" if path is not None else ""
    return auto_message(
        record_line + f"{root / 'share/work-contract.md'}と{root / 'skills/delegation/references/handoff-record.md'}に従い、"
        "委譲先自身が新規・継続の指定に応じて記録を作成・更新する。"
        "記録は割当範囲を所有し、委譲元は終了後の検収で読む。",
        source="agents-server",
        kind="work-contract",
    )


def task_document_request(
    subagent_md_path: str,
    extra_params: Mapping[str, str],
    *,
    whole_contract_path: str | None = None,
    launcher_session_id: str | None = None,
    host: str = "claude",
    project: pathlib.Path | str | None = None,
    include_startup: bool = True,
) -> tuple[str, str, LaunchKind, pathlib.Path | None]:
    """役割と名前付き入力からmodel種別、プロンプト、mode、サーバーが用意した引き継ぎ記録先を返す。

    `<役割名>.subagent.md`が`引き継ぎ記録先`を必須入力とし、`extra_params`がこれを持たない場合は、
    `（新規）`の記録先を用意してプロンプトへ加え、絶対パスを4要素目で返す。
    宣言しない通常起動にも共通入力として専用の記録先を加える。
    委譲元が値を渡した場合と軽量起動の4要素目は`None`とする。
    `whole_contract_path`と`launcher_session_id`は通常起動の全体作業契約の解決（`whole_contract_reference`）へ渡す。
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
    document_text = document_text.replace(_PLUGIN_ROOT_VARIABLE, str(task_document.parent.parent))
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
    # 共通前提の不足は記録先の生成より先に起動元へ返す。
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
        _check_declared_inputs(task_document, declaration, extra_params)
    # 起動時の資料は、受領した役割の判断が始まる前に同じ会話へ届ける。
    initial_documents = (
        plugin_root.SERVER_RUNTIME_CODE.check_role(
            "起動時資料の検査",
            task_document,
            lambda: read_triggers.startup_context(
                task_document, task_document.parent.parent, dict(extra_params), host=host, project=project
            ),
        )
        if launch_kind == "delegate" and include_startup
        else ""
    )
    # 宣言された入力の検証を終えてから、サーバー所有の共通入力を加える。
    # 軽量起動のtaskは独自の記録を持たない。
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
    # 入力宣言内のYAMLコメントを役割の主題として扱わない。
    heading_body = document_text.partition("\n---\n")[2] if document_text.startswith("---\n") else document_text
    title = next((title for token, title in parse_headings(heading_body, 1) if token.level == 0), "")
    role_name = f"{' '.join(title.splitlines())}（{role}）" if title else role
    prompt_lines = [
        task_documents.task_prompt_header(task_document),
        f"役割: {role_name}",
        "入力:",
        *named_input_lines(inputs),
        task_documents.TASK_PROMPT_REFERENCES.strip("\n"),
    ]
    if initial_documents:
        prompt_lines.append(initial_documents)
    prompt_lines.append(document_text)
    if launch_kind == "delegate":
        notice_path = handoff_path if _HANDOFF_INPUT_NAME not in extra_params else None
        prompt_lines.append(handoff_notice(notice_path, task_document.parent.parent))
        assert whole_contract is not None
        prompt_lines.append(whole_contract)
    return model_type, "\n".join(prompt_lines), launch_kind, handoff_path
