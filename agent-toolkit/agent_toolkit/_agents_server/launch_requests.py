"""`start`の入力の検証、`<役割名>.subagent.md`の解決と委譲本文の組み立て、委譲先の起動前の事前確認。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import pathlib
import re
import subprocess
import uuid
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import (
    launch_prompts,
    plugin_root,
    shared_roots,
)
from agent_toolkit._agents_server.state import (
    LaunchKind,
)
from agent_toolkit._agents_server.tool_descriptions import START_MODE_EXAMPLES, START_MODE_INPUTS
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import read_triggers, session_registry, task_documents
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message, opening_tag
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import command as git_command

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


_REQUIRED_INPUT_NAME_PATTERN = task_documents.INPUT_NAME_PATTERN


_SHARE_DIRECTORY = plugin_root.SERVER_PLUGIN_ROOT / "share"


_TASK_MODEL_TYPES = launch_prompts.TASK_MODEL_TYPES
_TASK_DOCUMENT_SUFFIX = task_documents.TASK_DOCUMENT_SUFFIX


# 委譲先のClaude Code・Codexがプラグインを起動するコマンド。MCP設定（`.mcp.json`・`.mcp.codex.json`・`mcp.json`）の
# `command`と`hooks/hooks.json`のhookの先頭語、およびCodexのhook起動器が内部で呼ぶ`uv run`を覆う。
# 委譲先は同じPATHと作業ディレクトリからこれらを解決するため、作業ディレクトリの設定（未trustのmise設定など）で
# 失敗する状態を子の起動前に検出する。起動後に失敗すると、Claude Codeはプラグインの接続失敗をホスト共通の記録へ残し、
# 同じ設定のサーバーへの接続を15分間試みない。
PREFLIGHT_COMMANDS: tuple[tuple[str, ...], ...] = (("uv", "--version"), ("uvx", "--version"))


# 事前確認の1コマンドあたりの上限秒数。成功した2コマンドの連続実行に約0.14秒かかることを確認した。
PREFLIGHT_TIMEOUT = 20.0


# 確認処理が受理する行の書式。拒否応答の本文へ添え、委譲元が同じ応答だけで書式を確定できる状態にする。
_REQUIRED_INPUT_LINE_FORMAT = (
    "受理する書式: 必須入力の行は`<項目名>:`で始める。"
    "項目名へ別の語を連結した行はその項目として解決しないため、補足する語は別の行へ書く。"
)


# 委譲元がツールのエラー本文や状態値だけで次の行動を決められるよう、応答へ載せる次の操作の文面。
# 同じ状況を複数の処理が返すため、処理ごとに書き分けず1か所へ置く。
_TASK_DOCUMENT_PATH_NEXT_ACTION = (
    "`subagent_md_path`へ役割名（`share/<役割名>.subagent.md`の`<役割名>`。例: `add-wi`）か、"
    "agent-toolkitの`share/*.subagent.md`の絶対パスを渡す。"
    "自由本文で委譲する場合は`mode`へ`delegate`を指定して`prompt`を渡す"
)


_TASK_DOCUMENT_DEFECT_NEXT_ACTION = (
    "agent-toolkitの不具合としてユーザーへ報告し、同じ依頼を`start`の`mode`へ`delegate`を指定して起動する"
)


MODEL_TYPE_NEXT_ACTION = (
    "`model_type`へ工程名（例: `execute`）か`<claude|codex|agy>:<model>[/<effort>]`、"
    "`opencode:<provider>/<model>[@<effort>]`の候補列を指定する。"
    "工程に対応する候補列は`atk config get <工程>_model`（例: `execute_model`）で確かめる"
)


# `<役割名>.subagent.md`の本文がプラグインルートを参照するときの変数名。
_PLUGIN_ROOT_VARIABLE = "${CLAUDE_PLUGIN_ROOT}"


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


def _run_preflight_command(command: tuple[str, ...], cwd: str) -> tuple[str, str] | None:
    """1件の事前確認コマンドを実行し、失敗時は失敗内容の説明と原因に応じた次の操作を返す。"""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PREFLIGHT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return (
            f"command={' '.join(command)} error=実行ファイルが見つからない",
            "agents_serverを起動したホストのPATHで`uv`と`uvx`を解決できるかを確かめてから再実行する",
        )
    except subprocess.TimeoutExpired:
        return (
            f"command={' '.join(command)} error={PREFLIGHT_TIMEOUT:g}秒以内に終了しない",
            "時間をおいて再実行するか、`cwd`を別の作業ディレクトリへ変えて再実行する",
        )
    if completed.returncode != 0:
        return (
            f"command={' '.join(command)} exit_code={completed.returncode} stderr={completed.stderr.strip()}",
            "未trustのmise設定が原因の場合は、委譲元で`mise trust`の要否を判断してから再実行する。"
            "それ以外はstderrの原因を解消するか、`cwd`を変えて再実行する",
        )
    return None


def _check_plugin_commands_sync(cwd: str) -> None:
    """委譲先の作業ディレクトリでプラグインの起動コマンドが動くことを確かめる。"""
    for command in PREFLIGHT_COMMANDS:
        failure = _run_preflight_command(command, cwd)
        if failure is not None:
            description, next_action = failure
            raise ActionableError(
                "委譲先の作業ディレクトリでプラグインの起動コマンドが失敗したため委譲先を起動しない: "
                f"cwd={cwd} {description}。",
                next_action=next_action,
            )


async def check_plugin_commands(cwd: str) -> None:
    """事前確認をイベントループの外で実行する。"""
    await asyncio.to_thread(_check_plugin_commands_sync, cwd)


def wrap_delivery_body(body: str) -> str:
    """委譲先へ配送する本文を、生成された配送境界で囲む。

    配送境界は最初の開始タグと最後の同名終了タグで確定する。
    委譲先の解釈は`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」が定める。
    """
    return auto_message(body, source="agents-server", kind="delivery")


def with_launcher_record(body: str, context: Mapping[str, Any]) -> str:
    """taskの起動本文へ、サーバーが解決した直接の委譲元情報を添える。"""
    notice = auto_message(json.dumps(dict(context), ensure_ascii=False), source="agents-server", kind="launcher-record")
    source_line, separator, remainder = body.partition("\n")
    return source_line + "\n\n" + notice + separator + remainder


def _validate_required_prompt_inputs(
    task_document: pathlib.Path,
    extra_params: Mapping[str, str],
    document_text: str | None = None,
) -> str | None:
    """`<役割名>.subagent.md`の宣言と名前付き入力が一致するか確かめ、宣言を読めない場合だけ警告文を返す。

    必須入力の欠落と宣言外の入力名は委譲先を起動せず`ValueError`で拒否する。
    """
    declaration = _task_document_declaration(task_document, document_text)
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
            next_action="欠けた入力名をキーとして`extra_params`へ加え、`start`を再発行する",
        )
    undeclared = [name for name in extra_params if name not in declaration.accepted]
    if undeclared:
        raise ActionableError(
            f"`<役割名>.subagent.md`が宣言していない入力です: {', '.join(undeclared)}; "
            f"受理する入力名: {', '.join(sorted(declaration.accepted))}; `subagent_md_path`: {task_document}。",
            next_action=(
                "今回限りの補足を渡す欄は無いため、値を`<役割名>.subagent.md`が宣言した入力へ収めるか、宣言外の値を渡さずに起動する"
            ),
        )
    _check_explore_materials(task_document, extra_params)


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


def parse_named_inputs(lines: list[str]) -> dict[str, str]:
    """定型本文の入力を復元し、説明行・重複項目を拒否する。"""
    params: dict[str, str] = {}
    current: str | None = None
    first_continuation = False
    for number, line in enumerate(lines, 2):
        if current is not None and line.startswith("  "):
            params[current] = line[2:] if first_continuation else params[current] + "\n" + line[2:]
            first_continuation = False
            continue
        if not line.strip():
            continue
        name, separator, value = line.partition(":")
        if (
            not separator
            or (value and not value.startswith(" "))
            or not _REQUIRED_INPUT_NAME_PATTERN.fullmatch(name)
            or name in params
        ):
            raise ActionableError(
                f"入力本文の{number}行目を受理できません: {line}",
                next_action="指示と宣言済みの名前付き入力だけで再実行する。本文生成にはdelegation-promptを使う",
            )
        current = name
        params[name] = value.removeprefix(" ")
        first_continuation = not params[name]
    return params


def builtin_task_prompt(subagent_md_path: str, extra_params: Mapping[str, str]) -> str:
    """組み込み委譲へ渡す、役割参照と宣言済み入力だけの本文を生成する。"""
    document = resolve_task_document_path(subagent_md_path)
    warning = _validate_required_prompt_inputs(document, extra_params)
    if warning is not None:
        _LOG.warning("%s", warning)
    return "\n".join([f"{document}の手順を実行せよ。", *named_input_lines(extra_params)])


def continuation_prompt(
    launch_prompt: str,
    followup_prompts: tuple[str, ...],
    *,
    prompt: str | None = None,
    instruction: str | None = None,
    extra_params: Mapping[str, str] | None = None,
) -> str:
    """既存の起動記録と役割宣言から、継続本文を検査・生成する。"""
    if (prompt is None) == (instruction is None) or (prompt is not None and extra_params is not None):
        raise ActionableError(
            "promptとinstruction/extra_paramsは排他的です。どちらか一方が必要です。",
            next_action='`send_message(session_id, instruction="<指示>", extra_params={<必要入力>})`で再実行する',
        )
    documents = task_documents.find_task_documents(launch_prompt.split("\n", 1)[0])
    declaration = task_documents.read_declaration(documents[0]) if documents else None
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
                next_action="役割文書の継続指示を指定する。新しい要求はpromptで送る",
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
        params = dict(extra_params or {}) if instruction is not None else parse_named_inputs(lines[1:])
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


def _material_error(name: str, reason: str) -> ActionableError:
    """不足資料と、保存して再発行する操作を同じ応答へ載せる。"""
    return ActionableError(
        f"探索資料の入力が不成立です: {name}: {reason}。委譲先は起動していない。",
        next_action=(
            f"`{name}`に必要な本文・差分・証拠をUTF-8の読取可能な通常ファイルへ保存し、"
            "絶対パスで補完して`start`を再発行する。reader-fit-reviewの`比較資料`は、Git管理下では"
            '`{"repository":"/repo","base":"<比較元OID>","head":"<レビューHEAD>","paths":["文書.md"]}`とし、'
            "担当が`git show`・`git diff`で読む。Git管理外では"
            '`{"比較元本文":"/保存先/旧本文.md","今回差分":"/保存先/差分.patch","現行本文":"/成果物.md"}`'
            "とし、複数成果物では同じ3項目のオブジェクト配列を渡す。"
            "新規成果物だけ比較元本文へ`新規`、今回差分へ`全文`を指定できる。"
            "再レビューの`未解決事項`は保存ファイルの絶対パス、その絶対パスのJSON配列か`なし`だけを渡し、説明は保存物へ書く"
        ),
    )


def _material_paths(name: str, value: str) -> list[pathlib.Path]:
    """JSON配列と既存の保存ファイル指定を、共通の読取検査へ渡す。"""
    value = value.strip()
    if value.startswith("["):
        try:
            items = json.loads(value)
        except json.JSONDecodeError as error:
            raise _material_error(name, "パス配列のJSONを読めない") from error
        if not isinstance(items, list) or not items or any(not isinstance(item, str) for item in items):
            raise _material_error(name, "絶対パスの空でないJSON配列が必要")
    else:
        # 全体が実在するパスの場合は、空白・日本語の区切り文字を含む名前もそのまま保つ。
        if pathlib.Path(value).is_absolute() and pathlib.Path(value).is_file():
            items = [value]
        else:
            tokens = re.findall(r"`(/[^`]+)`|(/[^\s`、,;；（）()]+)", value)
            items = [quoted or bare for quoted, bare in tokens]
    if not items:
        raise _material_error(name, "保存ファイルの絶対パスが無い")
    paths = [pathlib.Path(item) for item in items]
    for path in paths:
        if not path.is_absolute() or not path.is_file():
            raise _material_error(name, f"通常ファイルを参照できない: {path}")
        try:
            with path.open(encoding="utf-8") as stream:
                for _line in stream:
                    pass
        except (OSError, UnicodeError) as error:
            raise _material_error(name, f"UTF-8で読めない: {path}: {error}") from error
    return paths


def _check_reader_unresolved(params: Mapping[str, str], review_kind: str) -> None:
    """再レビューで必要な未解決事項の保存物を、パスだけの値として確かめる。

    パスに説明を連結した値は、説明の対象が別の成果物でも探索担当が今回の資料の範囲と誤認するため受理しない。
    """
    unresolved = params.get("未解決事項", "なし" if review_kind == "初回レビュー" else "").strip()
    if unresolved == "なし":
        return
    if not unresolved.startswith("[") and not (pathlib.Path(unresolved).is_absolute() and pathlib.Path(unresolved).is_file()):
        raise _material_error(
            "未解決事項", "値は保存ファイルの絶対パス、その絶対パスのJSON配列か`なし`だけにし、説明は保存物へ書く"
        )
    _material_paths("未解決事項", unresolved)


_HUNK_HEADER = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _apply_unified_diff(before: str, patch: str) -> str | None:
    """1ファイル分の統一差分を比較元本文へ適用した本文を返し、適用できない差分ではNoneを返す。

    行位置を持たない帰属限定差分、複数ファイルの差分、比較元と文脈が一致しない差分は適用できない。
    """
    lines = patch.splitlines(keepends=True)
    if sum(1 for line in lines if line.startswith("+++ ")) > 1:
        return None
    entries: list[tuple[str, str]] = []
    for line in lines:
        if line.startswith("\\"):
            if not entries:
                return None
            tag, text = entries[-1]
            entries[-1] = (tag, text.removesuffix("\n"))
        else:
            entries.append((line[:1], line[1:]))
    source = before.splitlines(keepends=True)
    result: list[str] = []
    position = 0
    index = 0
    while index < len(entries) and not (entries[index][0] == "@" and entries[index][1].startswith("@ ")):
        if entries[index][0] == "@":
            return None
        index += 1
    while index < len(entries):
        header = _HUNK_HEADER.match("@" + entries[index][1])
        if header is None:
            return None
        old_start, old_count = int(header[1]), int(header[2] if header[2] is not None else 1)
        new_count = int(header[4] if header[4] is not None else 1)
        anchor = old_start if old_count == 0 else old_start - 1
        if anchor < position or anchor > len(source):
            return None
        result.extend(source[position:anchor])
        position = anchor
        index += 1
        while old_count or new_count:
            if index >= len(entries):
                return None
            tag, text = entries[index]
            if tag in {" ", "-"}:
                if position >= len(source) or source[position] != text or old_count == 0:
                    return None
                position += 1
                old_count -= 1
            if tag in {" ", "+"}:
                if new_count == 0:
                    return None
                result.append(text)
                new_count -= 1
            if tag not in {" ", "-", "+"}:
                return None
            index += 1
    result.extend(source[position:])
    return "".join(result)


def _patch_target_error(patch: str, current: pathlib.Path) -> str | None:
    """差分の新しい側のファイル見出しが現行本文と異なるファイルを指す場合に理由を返す。"""
    for line in patch.splitlines():
        if not line.startswith("+++ "):
            continue
        target = line[4:].split("\t", 1)[0].strip()
        if target == "/dev/null":
            return None
        relative = target.removeprefix("b/") if not pathlib.Path(target).is_absolute() else target
        if pathlib.Path(relative).is_absolute():
            matched = pathlib.Path(relative) == current
        else:
            matched = current.as_posix().endswith("/" + relative.lstrip("/"))
        return None if matched else f"差分の対象`{target}`が現行本文`{current}`と異なる成果物を指す"
    return None


def _check_patch_consistency(name: str, before: str, patch_path: pathlib.Path, current: pathlib.Path) -> None:
    """比較元本文へ今回差分を適用した結果が現行本文と一致するかを確かめる。

    別成果物の差分、計画別の判定範囲だけの差分、レビューHEADと異なる現行本文は、いずれもこの一致が崩れる。
    """
    patch = patch_path.read_text(encoding="utf-8")
    if error := _patch_target_error(patch, current):
        raise _material_error(name, error)
    expected = current.read_text(encoding="utf-8")
    applied = _apply_unified_diff(before, patch) if patch else before
    if applied != expected:
        raise _material_error(
            name,
            "比較元本文へ今回差分を適用した結果が現行本文と一致しない。今回差分は比較元と現行本文の本文間の完全な差分にし、"
            "計画別の差分は`判定範囲差分`へ分ける。現行本文はレビューHEADの同じ版を渡す",
        )


def _check_reader_materials(params: Mapping[str, str]) -> None:
    """現行本文・比較元・今回差分を区別し、再レビューの証拠も起動前に読む。"""
    current = _material_paths("成果物", params["成果物"])
    if len(set(current)) != len(current):
        raise _material_error("成果物", "現行本文のパスが重複している")
    review_kind = params.get("レビュー種別", "初回レビュー")
    if review_kind not in {"初回レビュー", "再レビュー", "引き継ぎ再レビュー"}:
        raise _material_error("レビュー種別", "初回レビュー・再レビュー・引き継ぎ再レビューのいずれかが必要")
    if "比較資料" in params:
        try:
            materials = json.loads(params["比較資料"])
        except json.JSONDecodeError as error:
            raise _material_error("比較資料", "JSONオブジェクトまたはその配列を読めない") from error
        if isinstance(materials, dict) and set(materials) - {"判定範囲差分"} == {"repository", "base", "head", "paths"}:
            _check_git_reader_materials(materials, current)
            _check_reader_unresolved(params, review_kind)
            return
        if isinstance(materials, dict) and len(current) == 1:
            materials = [materials]
        if not isinstance(materials, list) or not materials:
            raise _material_error("比較資料", "成果物ごとの比較資料の空でない配列が必要")
        keys = {"比較元本文", "今回差分", "現行本文"}
        covered: set[pathlib.Path] = set()
        for material in materials:
            if (
                not isinstance(material, dict)
                or set(material) - {"判定範囲差分"} != keys
                or any(not isinstance(value, str) for value in material.values())
            ):
                raise _material_error("比較資料", "比較元本文・今回差分・現行本文の3項目（任意で判定範囲差分）が必要")
            actual = _material_paths("現行本文", material["現行本文"])
            if len(actual) != 1 or actual[0] not in current:
                raise _material_error("現行本文", "成果物のいずれか1件と同じパスが必要")
            if actual[0] in covered:
                raise _material_error("比較資料", f"現行本文の対応が重複している: {actual[0]}")
            covered.add(actual[0])
            is_new = material["比較元本文"] == "新規"
            if review_kind != "初回レビュー" and is_new:
                raise _material_error("比較元本文", "再レビューでは前回確認版の本文が必要")
            before = ""
            if not is_new:
                before = _material_paths("比較元本文", material["比較元本文"])[0].read_text(encoding="utf-8")
            if not (is_new and material["今回差分"] == "全文"):
                patch_paths = _material_paths("今回差分", material["今回差分"])
                if len(patch_paths) != 1:
                    raise _material_error("今回差分", "成果物ごとに1件の差分ファイルが必要")
                _check_patch_consistency("今回差分", before, patch_paths[0], actual[0])
            if "判定範囲差分" in material:
                scope_paths = _material_paths("判定範囲差分", material["判定範囲差分"])
                if len(scope_paths) != 1:
                    raise _material_error("判定範囲差分", "成果物ごとに1件の差分ファイルが必要")
                if error := _patch_target_error(scope_paths[0].read_text(encoding="utf-8"), actual[0]):
                    raise _material_error("判定範囲差分", error)
        if covered != set(current):
            raise _material_error("比較資料", "全成果物に比較資料を1件ずつ対応付ける必要がある")
    else:
        if len(current) != 1:
            raise _material_error("比較資料", "複数成果物には比較資料の配列が必要")
        # 旧形式も保存ファイルを共通検査へ通す。本文や差分の説明だけでは成立しない。
        scope = params["修正範囲"]
        is_new = scope.startswith("新規") and "全文" in scope
        if not is_new:
            paths = _material_paths("修正範囲（比較元本文・今回差分）", scope)
            saved = list(dict.fromkeys(path for path in paths if path != current[0]))
            if len(saved) < 2:
                raise _material_error("比較資料", "比較元本文と今回差分の保存物が必要")
            # 旧形式は役割を名前で示さないため、差分の形を持つファイルを1件に識別できる場合だけ同じ対応を確かめる。
            patches = [path for path in saved if path.read_text(encoding="utf-8").startswith(("diff --git ", "--- "))]
            if len(patches) == 1 and len(saved) == 2:
                before_path = next(path for path in saved if path != patches[0])
                _check_patch_consistency(
                    "修正範囲（比較元本文・今回差分）", before_path.read_text(encoding="utf-8"), patches[0], current[0]
                )
        if review_kind != "初回レビュー" and is_new:
            raise _material_error("比較元本文", "再レビューでは前回確認版の本文が必要")
    _check_reader_unresolved(params, review_kind)


def _check_explore_materials(task_document: pathlib.Path, params: Mapping[str, str]) -> None:
    """読取と検索だけで動く担当の保存資料を、起動要求の境界で検査する。"""
    role = task_document.name.removesuffix(_TASK_DOCUMENT_SUFFIX)
    if role == "reader-fit-review":
        _check_reader_materials(params)
        return
    required_files = {
        "external-write-review": ("文面ファイル",),
        "bulk-replace-review": ("差分ファイル",),
        "pick-wi-explain": ("説明対象の選定結果の出力先ファイル", "activeなWI本文の保存先"),
    }
    for name in required_files.get(role, ()):
        _material_paths(name, params[name])
    if role == "external-write-review":
        evidence = params["根拠の所在"].strip()
        if evidence.startswith(("/", "`/", "[")) or pathlib.PureWindowsPath(evidence).is_absolute():
            _material_paths("根拠の所在", evidence)


def _check_git_reader_materials(materials: dict[str, Any], current: list[pathlib.Path]) -> None:
    """保存物と同じ比較対象を、Git識別子で受け取る。"""
    repository = materials["repository"]
    paths = materials["paths"]
    if not isinstance(repository, str) or not pathlib.Path(repository).is_absolute():
        raise _material_error("比較資料", "repositoryにGit作業ツリーの絶対パスが必要")
    if not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths):
        raise _material_error("比較資料", "pathsに空でないリポジトリ相対パス配列が必要")
    if len(paths) != len(current) or {pathlib.Path(repository) / path for path in paths} != set(current):
        raise _material_error("比較資料", "pathsが成果物の集合と一致しない")
    # 計画別の判定範囲は、委譲元が`git diff`などで保存した差分ファイルをパスごとに任意で受け取る。
    # 探索担当は読み取り専用のため、契約から帰属を計算させず、保存済みの差分で走査範囲を1通りに決める。
    scopes = materials.get("判定範囲差分", {})
    if not isinstance(scopes, dict) or set(scopes) - set(paths) or any(not isinstance(value, str) for value in scopes.values()):
        raise _material_error(
            "比較資料", "判定範囲差分にはpathsのリポジトリ相対パスから保存ファイルの絶対パスへのオブジェクトが必要"
        )
    for path, scope in scopes.items():
        scope_paths = _material_paths("判定範囲差分", scope)
        if len(scope_paths) != 1:
            raise _material_error("判定範囲差分", "成果物ごとに1件の差分ファイルが必要")
        if error := _patch_target_error(scope_paths[0].read_text(encoding="utf-8"), pathlib.Path(repository) / path):
            raise _material_error("判定範囲差分", error)
    for key in ("base", "head"):
        revision = materials[key]
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", revision):
            raise _material_error("比較資料", f"{key}にcommitの一意なOIDが必要")
        checked = git_command.run(
            ["rev-parse", "--verify", f"{revision}^{{commit}}"],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=PREFLIGHT_TIMEOUT,
            check=False,
        )
        if checked.returncode != 0:
            raise _material_error("比較資料", f"{key}のcommitを参照できない: {revision}")
    for path in paths:
        shown = git_command.run(
            ["show", f"{materials['head']}:{path}"],
            cwd=repository,
            capture_output=True,
            timeout=PREFLIGHT_TIMEOUT,
            check=False,
        )
        if shown.returncode != 0 or shown.stdout != (pathlib.Path(repository) / path).read_bytes():
            raise _material_error("比較資料", f"現行本文がheadの同じ版に一致しない: {path}")


_HANDOFF_INPUT_NAME = "引き継ぎ記録先"
"""サーバーが省略時の値を用意する必須入力の名前。値は呼び出し元の判断を含まず一意に決まる。"""


_HANDOFF_TEMP_PREFIX = "handoff"
"""委譲元のセッションのmanaged-tempを解決できない場合に作成するmanaged-tempの接頭辞。"""


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
    path = _default_handoff_path(launcher_session_id)
    if path is None:
        raise ActionableError(
            "通常委譲の作業記録先を用意できません。委譲先は起動していない。",
            next_action="managed-tempの診断を確認し、記録先を確保してからstartを再発行する",
        )
    return prompt + "\n\n" + handoff_notice(path, plugin_root.SERVER_PLUGIN_ROOT) + "\n\n" + whole_contract, path


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
        "記録は割当範囲を所有し、委譲元は終端後の検収で読む。",
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
    declaration = _task_document_declaration(task_document, document_text)
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
    if launch_kind != "delegate" and whole_contract_path is not None:
        raise ActionableError(
            f"軽量起動（mode: {launch_kind}）の役割はwhole_contract_pathを受理しません: {task_document.name}。"
            "委譲先は起動していない。",
            next_action="`whole_contract_path`を外して`start`を再発行する",
        )
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
        read_triggers.startup_context(task_document, task_document.parent.parent, dict(extra_params))
        if launch_kind == "delegate"
        else ""
    )
    prompt_lines = [task_documents.task_prompt_header(task_document), document_text]
    if initial_documents:
        prompt_lines.append(initial_documents)
    # 宣言された入力の検証を終えてから、サーバー所有の共通入力を加える。
    # 軽量起動のtaskは独自の記録を持たない。
    if launch_kind == "delegate" and _HANDOFF_INPUT_NAME not in extra_params:
        handoff_path = _default_handoff_path(launcher_session_id)
        if handoff_path is None:
            raise ActionableError(
                "通常委譲の作業記録先を用意できません。委譲先は起動していない。",
                next_action="managed-tempの診断を確認し、記録先を確保してからstartを再発行する",
            )
    if launch_kind == "delegate":
        notice_path = handoff_path if _HANDOFF_INPUT_NAME not in extra_params else None
        prompt_lines.append(handoff_notice(notice_path, task_document.parent.parent))
        assert whole_contract is not None
        prompt_lines.append(whole_contract)
    if extra_params:
        prompt_lines.append("入力:")
        prompt_lines.extend(named_input_lines(extra_params))
    return model_type, "\n".join(prompt_lines), launch_kind, handoff_path
