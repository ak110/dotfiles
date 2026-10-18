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
    task_documents,
)
from agent_toolkit._agents_server.state import (
    LaunchKind,
)
from agent_toolkit._agents_server.tool_descriptions import START_MODE_EXAMPLES, START_MODE_INPUTS
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common.message_format import auto_message
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import command as git_command

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


_REQUIRED_INPUT_NAME_PATTERN = task_documents.INPUT_NAME_PATTERN


_SHARE_DIRECTORY = plugin_root.SERVER_PLUGIN_ROOT / "share"


_TASK_MODEL_TYPES = launch_prompts.TASK_MODEL_TYPES
_TASK_USAGES = launch_prompts.TASK_USAGES


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
    "`model_type`へ段位名（例: `high_tier`）か`<claude|codex|agy>:<model>[/<effort>]`の候補列を指定する。"
    "段位名に対応するモデルは`atk config get <engine>_<段位名>_model`（例: `claude_high_tier_model`）、"
    "engine順は`atk config show`で確かめる"
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
    return task_documents.read_declaration_unchecked(task_document, document_text)


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
        if documents and text.lstrip().startswith("#"):
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
            "絶対パスで補完して`start`を再発行する。reader-fit-reviewの`比較資料`は"
            '`{"比較元本文":"/保存先/旧本文.md","今回差分":"/保存先/差分.patch","現行本文":"/成果物.md"}`'
            "とする。複数成果物では同じ3項目のオブジェクト配列を渡す。"
            "新規成果物だけ比較元本文へ`新規`、今回差分へ`全文`を指定できる。"
            "再レビューの`未解決事項`は保存ファイルの絶対パスか`なし`を渡す"
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
    """再レビューで必要な未解決事項の保存物を確かめる。"""
    unresolved = params.get("未解決事項", "なし" if review_kind == "初回レビュー" else "")
    if unresolved != "なし":
        _material_paths("未解決事項", unresolved)


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
        if isinstance(materials, dict) and set(materials) == {"repository", "base", "head", "paths"}:
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
                or set(material) != keys
                or any(not isinstance(value, str) for value in material.values())
            ):
                raise _material_error("比較資料", "比較元本文・今回差分・現行本文の3項目が必要")
            actual = _material_paths("現行本文", material["現行本文"])
            if len(actual) != 1 or actual[0] not in current:
                raise _material_error("現行本文", "成果物のいずれか1件と同じパスが必要")
            if actual[0] in covered:
                raise _material_error("比較資料", f"現行本文の対応が重複している: {actual[0]}")
            covered.add(actual[0])
            is_new = material["比較元本文"] == "新規"
            if review_kind != "初回レビュー" and is_new:
                raise _material_error("比較元本文", "再レビューでは前回確認版の本文が必要")
            if not is_new:
                _material_paths("比較元本文", material["比較元本文"])
            if not (is_new and material["今回差分"] == "全文"):
                _material_paths("今回差分", material["今回差分"])
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
            if len({path for path in paths if path != current[0]}) < 2:
                raise _material_error("比較資料", "比較元本文と今回差分の保存物が必要")
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


def _default_handoff_path() -> pathlib.Path | None:
    """`（新規）`の引き継ぎ記録先として、委譲元のセッションのmanaged-temp直下の未使用のファイルパスを返す。

    会話を特定できる場合はセッションのmanaged-tempを直接解決して再利用する。
    会話を特定できないMCP起動では、個別のhandoff領域を作成する。
    どちらも得られない場合は`None`を返し、呼び出し元は従来どおり欠落として拒否する。
    ファイル自体は作成しない。`（新規）`の記録先は委譲先が作成するためである。
    """
    directory: pathlib.Path | None = None
    session_id = (
        _HANDOFF_SESSION[0]
        if _HANDOFF_SESSION is not None
        else (shared_roots.resolve_root_session_id(os.environ) or os.environ.get("CODEX_THREAD_ID"))
    )
    if session_id is not None and not session_id.startswith("mcp-"):
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


def delegate_request(prompt: str) -> tuple[str, pathlib.Path]:
    """自由本文を保ち、通常委譲へ専用の現行記録先を配送する。"""
    path = _default_handoff_path()
    if path is None:
        raise ActionableError(
            "通常委譲の作業記録先を用意できません。委譲先は起動していない。",
            next_action="managed-tempの診断を確認し、記録先を確保してからstartを再発行する",
        )
    return prompt + "\n\n" + handoff_notice(path) + "\n\n" + whole_contract_reference(), path


def whole_contract_reference() -> str:
    """所有する全体会話の現行記録への参照を、本文の複製をせず配送する。"""
    session_id = (
        _HANDOFF_SESSION[0]
        if _HANDOFF_SESSION is not None
        else (shared_roots.resolve_root_session_id(os.environ) or os.environ.get("CODEX_THREAD_ID"))
    )
    directory: pathlib.Path | None = None
    if session_id is not None and not session_id.startswith("mcp-"):
        with contextlib.suppress(_managed_temp.ManagedTempError, OSError):
            directory = _managed_temp.find_session_temp(session_id)
    path = directory / "work-contract.md" if directory is not None else None
    if path is not None and path.is_file():
        body = (
            f"全体作業契約: {path}\n"
            "担当工程の開始時に読み、割当が全体の要求を満たす範囲と、維持する成果・検収に使う証拠を確かめる。"
            "全体記録の更新は委譲元が所有し、委譲先は自身の引き継ぎ記録へ判断を残す。"
        )
    else:
        body = (
            "全体作業契約: 未解決。担当の成果が全体の要求を満たすことを確認する工程では、"
            "委譲元へ記録の所在を返却・通知で求め、取得した記録の要求と担当の成果を比べる。"
        )
    return auto_message(body, source="agents-server", kind="work-contract-reference")


def handoff_notice(path: pathlib.Path) -> str:
    """サーバーが所有する共通入力を、役割文書の入力宣言から分離して加える。"""
    return auto_message(
        f"引き継ぎ記録先: {path}（新規）\n"
        "share/work-contract.mdとskills/delegation/references/handoff-record.mdに従い、"
        "委譲先自身が最初の記録を作成する。記録は割当範囲を所有し、委譲元は終端後の検収で読む。",
        source="agents-server",
        kind="work-contract",
    )


def task_document_request(
    subagent_md_path: str,
    extra_params: Mapping[str, str],
) -> tuple[str, str | None, str, LaunchKind, pathlib.Path | None]:
    """`<役割名>.subagent.md`と名前付き入力からmodel種別、用途、委譲プロンプト、`mode:`の値およびサーバーが用意した引き継ぎ記録先を返す。

    用途は役割に対応する値で、対応が無い場合は`None`（段位ごとの用途を使う）とする。
    `<役割名>.subagent.md`が`引き継ぎ記録先`を必須入力とし、`extra_params`がこれを持たない場合は、
    拒否せずに`（新規）`の記録先を用意して委譲プロンプトへ加え、その絶対パスを5要素目で返す。
    宣言しない通常起動にも共通入力として専用の記録先を加え、5要素目で返す。
    委譲元が値を渡した場合と軽量起動の5要素目は`None`とする。
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
    model_type = _TASK_MODEL_TYPES.get(task_document.name)
    if model_type is None:
        raise ActionableError(
            f"subagent task has no model_type mapping: {task_document.name}",
            next_action=_TASK_DOCUMENT_DEFECT_NEXT_ACTION,
        )
    if any(not isinstance(name, str) or not _REQUIRED_INPUT_NAME_PATTERN.fullmatch(name) for name in extra_params):
        raise ActionableError(
            "extra_params contains an invalid input name",
            next_action=(
                "`extra_params`のキーは`<役割名>.subagent.md`の`## 入力`が宣言した入力名にし、"
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
    launch_kind: LaunchKind = "delegate"
    handoff_path: pathlib.Path | None = None
    if isinstance(declaration, str):
        _LOG.warning("%s", declaration)
    else:
        if _HANDOFF_INPUT_NAME in declaration.required and _HANDOFF_INPUT_NAME not in extra_params:
            handoff_path = _default_handoff_path()
            if handoff_path is not None:
                extra_params = {**extra_params, _HANDOFF_INPUT_NAME: f"{handoff_path}（新規）"}
        _check_declared_inputs(task_document, declaration, extra_params)
        launch_kind = declaration.launch_kind
    prompt_lines = [f"次の文書の手順を実行せよ（出所: {task_document}）。", document_text]
    # 宣言された入力の検証を終えてから、サーバー所有の共通入力を加える。
    # 軽量起動のtaskは独自の記録を持たない。
    if launch_kind == "delegate" and _HANDOFF_INPUT_NAME not in extra_params:
        handoff_path = _default_handoff_path()
        if handoff_path is None:
            raise ActionableError(
                "通常委譲の作業記録先を用意できません。委譲先は起動していない。",
                next_action="managed-tempの診断を確認し、記録先を確保してからstartを再発行する",
            )
        prompt_lines.append(handoff_notice(handoff_path))
    if launch_kind == "delegate":
        prompt_lines.append(whole_contract_reference())
    if extra_params:
        prompt_lines.append("入力:")
        prompt_lines.extend(named_input_lines(extra_params))
    return model_type, _TASK_USAGES.get(task_document.name), "\n".join(prompt_lines), launch_kind, handoff_path
