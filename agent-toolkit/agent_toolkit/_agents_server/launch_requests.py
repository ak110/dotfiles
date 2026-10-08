"""`start`の入力の検証、`<役割名>.subagent.md`の解決と委譲本文の組み立て、委譲先の起動前の事前確認。"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
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
    "`model_type`へ段位名（例: `high_tier`）か`<claude|codex|agy>:<model>[/<effort>]`の候補列を指定する。"
    "段位名に対応する候補は`atk config get <段位名>_model`で確かめる"
)


# `<役割名>.subagent.md`の本文がプラグインルートを参照するときの変数名。
_PLUGIN_ROOT_VARIABLE = "${CLAUDE_PLUGIN_ROOT}"


def _is_agent_toolkit_task_document(path: pathlib.Path) -> bool:
    """agent-toolkit pluginのshare直下にある`<役割名>.subagent.md`だけを受理する。"""
    return task_documents.is_agent_toolkit_task_document(path)


def _accepted_role_names() -> list[str]:
    """サーバー自身のplugin rootの`share/`直下にある`<役割名>.subagent.md`の役割名を返す。"""
    return sorted(path.name.removesuffix(_TASK_DOCUMENT_SUFFIX) for path in _SHARE_DIRECTORY.glob(f"*{_TASK_DOCUMENT_SUFFIX}"))


def _resolve_task_document_path(subagent_md_path: str) -> pathlib.Path:
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


_HANDOFF_INPUT_NAME = "引き継ぎ記録先"
"""サーバーが省略時の値を用意する必須入力の名前。値は呼び出し元の判断を含まず一意に決まる。"""


_HANDOFF_TEMP_PREFIX = "handoff"
"""委譲元のセッションのmanaged-tempを解決できない場合に作成するmanaged-tempの接頭辞。"""


def _default_handoff_path() -> pathlib.Path | None:
    """`（新規）`の引き継ぎ記録先として、委譲元のセッションのmanaged-temp直下の未使用のファイルパスを返す。

    セッションのmanaged-tempは`AGENT_TOOLKIT_OWNER_SESSION`か`CLAUDE_CODE_SESSION_ID`が示すsessionのものを作成せずに解決する。
    解決できない場合（Codex CLIが直接起動したMCPサーバーなど）は新しいmanaged-tempを作成する。
    どちらも得られない場合は`None`を返し、呼び出し元は従来どおり欠落として拒否する。
    ファイル自体は作成しない。`（新規）`の記録先は委譲先が作成するためである。
    """
    directory: pathlib.Path | None = None
    session_id = shared_roots.resolve_root_session_id(os.environ)
    if session_id is not None:
        try:
            entries = _managed_temp.list_managed_temp(_managed_temp.SESSION_TEMP_PREFIX, session_id=session_id)
        except (_managed_temp.ManagedTempError, OSError):
            entries = []
        recorded = entries[-1].get("path") if entries else None
        if isinstance(recorded, str) and pathlib.Path(recorded).is_dir():
            directory = pathlib.Path(recorded)
    if directory is None:
        try:
            directory = _managed_temp.create_managed_temp(_HANDOFF_TEMP_PREFIX)
        except (_managed_temp.ManagedTempError, OSError):
            _LOG.warning("省略された引き継ぎ記録先の代わりの記録先を用意できません", exc_info=True)
            return None
    while True:
        candidate = directory / f"handoff-{uuid.uuid4().hex[:12]}.md"
        if not candidate.exists():
            return candidate


def task_document_request(
    subagent_md_path: str,
    extra_params: Mapping[str, str],
) -> tuple[str, str, LaunchKind, pathlib.Path | None]:
    """`<役割名>.subagent.md`と名前付き入力からmodel種別、委譲プロンプト、`mode:`の値およびサーバーが用意した引き継ぎ記録先を返す。

    `<役割名>.subagent.md`が`引き継ぎ記録先`を必須入力とし、`extra_params`がこれを持たない場合は、
    拒否せずに`（新規）`の記録先を用意して委譲プロンプトへ加え、その絶対パスを4要素目で返す。
    委譲元が値を渡した場合と、宣言を読めない場合の4要素目は`None`とする。
    """
    task_document = _resolve_task_document_path(subagent_md_path).resolve()
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
    if extra_params:
        prompt_lines.append("入力:")
        prompt_lines.extend(f"{name}: {value}" for name, value in extra_params.items())
    return model_type, "\n".join(prompt_lines), launch_kind, handoff_path
