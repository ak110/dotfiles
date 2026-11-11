"""Claude SDKの起動設定と親設定の継承。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
import contextlib
import json
import os
import pathlib
import shutil
from collections.abc import Callable
from typing import Any, Literal, cast

from agent_toolkit._agents_server import child_environment, claude_settings
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server.launch_prompts import (
    AUTO_RESUME_NOTICE,
    CLAUDE_DELEGATE_SYSTEM_PROMPT,
    CLAUDE_EXPLORE_SYSTEM_PROMPT,
    LAUNCH_SYSTEM_PROMPTS,
    LIGHTWEIGHT_LAUNCH_KINDS,
    SUBAGENT_BASE_INSTRUCTIONS,
    cli_runtime_instructions,
    python_runtime_instructions,
)
from agent_toolkit._agents_server.state import LaunchKind
from agent_toolkit._common import claude_connection as connection_settings
from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._common import plugin_environment

_ENV_EMIT_SESSION_STATE_EVENTS = "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"


_EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


# 軽量起動の組込ツール。探索の読み取り専用性はプロンプトで指示する。
_LAUNCH_TOOLS: dict[str, list[str]] = {
    "explore": ["Read", "Glob", "Grep", "Bash"],
    "shell": ["Bash", "Read"],
    "write": ["Read", "Write", "Edit", "Glob", "Grep"],
}


# 無確認で使えるMCPは組込ツールの利用可能性と分ける。
_LAUNCH_ALLOWED_TOOLS = {kind: list(tools) for kind, tools in _LAUNCH_TOOLS.items()}
_LAUNCH_ALLOWED_TOOLS["explore"].append("mcp__pyfltr__*")


def _settings_from_cmdline(cmdline: bytes) -> str | None:
    """NUL区切りの起動引数から有効な`--settings`の値を返す。"""
    arguments = cmdline.split(b"\0")
    settings: str | None = None
    for index, argument in enumerate(arguments):
        if argument.startswith(b"--settings="):
            value = argument.removeprefix(b"--settings=")
            settings = os.fsdecode(value) if value else None
        elif argument == b"--settings":
            value = arguments[index + 1] if index + 1 < len(arguments) else b""
            settings = os.fsdecode(value) if value else None
    return settings


def _parent_settings() -> str | None:
    """親Claude Codeの起動引数からsettingsファイルを返す。"""
    try:
        cmdline = pathlib.Path(f"/proc/{os.getppid()}/cmdline").read_bytes()
    except OSError:
        return None
    return _settings_from_cmdline(cmdline)


# CLIのstdoutの1行にはモデルへ渡らないtool_use_result（EditのoriginalFile等）も含まれる。
# CLIが編集できる最大1GiBのファイルと差分が収まる4GiBを指定する。事前のメモリー確保ではない。
_MAX_BUFFER_SIZE = 4 * 1024**3


def _build_options(
    cwd: str,
    model: str | None,
    effort: str | None,
    session_id: str | None = None,
    launch_kind: LaunchKind = "delegate",
    stderr: Callable[[str], None] | None = None,
    debug_file: pathlib.Path | None = None,
    root_session_id: str | None = None,
    api_env_file: str | None = None,
    connection_kind: str = "inherit",
    isolation_env_file: str | None = None,
    handoff_prompt: str = "",
) -> Any:
    """起動区分ごとの自前システム指示と委譲先の印を持つSDKオプションを組む。

    `ClaudeAgentOptions.env`は継承環境へ後から重なるため、process-loopの印を継承したまま
    委譲先の印を追加する。
    共有状態を所有するManagerから受け取ったrootを委譲先へ渡す。
    親の`--settings`層へ同じenvを合成し、ユーザー・project設定のenvより優先させる。
    親を取得・解析できない場合はenvだけのsettingsを使う。
    親の設定がファイルの場合は内容をargvへ展開せず、起動ごとに作成した所有者だけが読めるファイルを渡す。
    軽量起動はSkillを明示除外し、基準の参照にはReadを使う。

    起動区分によらず権限モードをbypass系にしない。Claude Codeのセッション間メッセージの受信方針は、
    受信側がbypass系であり送信側が権限モードを申告していない場合にそのメッセージを保留する。
    委譲元は起動直後にメッセージを送るため、bypass系で起動したセッションは保留のまま
    初期化を完了できない。この保留動作は起動区分によらず同じである。
    """
    from claude_agent_sdk import ClaudeAgentOptions

    plugin_root = plugin_roots.SERVER_PLUGIN_ROOT
    cli_env = plugin_environment.cli_environment(plugin_root, os.environ)
    env = {
        _delegated_session.DELEGATED_SESSION_ENV: "1",
        # 委譲先のhookが軽量起動を判別して、スキルの取得を求めない判定に使う。
        _delegated_session.LAUNCH_KIND_ENV: launch_kind,
        "PATH": cli_env["PATH"],
        plugin_environment.RUNTIME_DATA_ENV: cli_env[plugin_environment.RUNTIME_DATA_ENV],
    }
    env.update(_delegated_session.handoff_environment(handoff_prompt))
    cli_instructions = cli_runtime_instructions(plugin_root)
    launch_prompt = CLAUDE_EXPLORE_SYSTEM_PROMPT if launch_kind == "explore" else LAUNCH_SYSTEM_PROMPTS[launch_kind]
    # CLIのturn状態の報告（`SystemMessage`のsubtype `session_state_changed`）を受け取る。
    # Claude Agent SDKはこの変数が無い場合だけ報告を自身の判定用に要求し、呼び出し側のストリームから除く。
    # 結果の公開の判定に`idle`を使うため、全起動区分で有効にする（`_run`の`ResultMessage`の分岐を参照）。
    env[_ENV_EMIT_SESSION_STATE_EVENTS] = "1"
    if root_session_id is not None:
        env[_delegated_session.OWNER_SESSION_ENV] = root_session_id
    # 委譲先のプロンプトキャッシュ保持期間を`mode`ごとに固定する。評価順序は`_common/wait_schedule.py`のdocstringが定める。
    # 軽量起動（探索委譲とシェル実行委譲）は連続する要求の間隔が短く、5分でも失効しないため、書き込み単価の低い側を選ぶ。
    # 通常起動は配下のサブエージェントへユーザー設定ファイルの指定が届かないため、1時間を明示する。
    # 前提が成立しなくなった場合は、軽量起動で連続する要求の間隔が5分を超える事象、または通常起動の配下サブエージェントが
    # 1時間で書き込む事象を、セッション記録の`message.usage.cache_creation`から観測できる。
    lightweight = launch_kind in LIGHTWEIGHT_LAUNCH_KINDS
    if lightweight:
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        env["CLAUDE_CODE_PROMPT_CACHE_TTL"] = "5m"
    else:
        env["CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"] = "1h"
    options: dict[str, Any] = {
        "cwd": cwd,
        "model": model,
        "effort": cast(_EffortLevel, effort),
        "resume": session_id,
        "permission_mode": "auto",
        # 起動直後の可用性待機をAPIの応答開始（`message_start`）で打ち切るため、部分出力のイベントを受け取る。
        "include_partial_messages": True,
        "env": env,
        "plugins": [{"type": "local", "path": str(plugin_root)}],
        "max_buffer_size": _MAX_BUFFER_SIZE,
        "setting_sources": ["user"] if lightweight else ["user", "project"],
        "system_prompt": (
            f"{launch_prompt}\n{AUTO_RESUME_NOTICE}\n{python_runtime_instructions()}\n{cli_instructions}"
            if lightweight
            else f"{SUBAGENT_BASE_INSTRUCTIONS}\n{CLAUDE_DELEGATE_SYSTEM_PROMPT}\n{AUTO_RESUME_NOTICE}\n{cli_instructions}"
        ),
    }
    if stderr is not None:
        options["stderr"] = stderr
    if debug_file is not None:
        options["extra_args"] = {"debug-file": str(debug_file)}
    if (cli_path := shutil.which("claude")) is not None:
        options["cli_path"] = cli_path
    # 起動時に継承環境から除いた接続値は、選択した接続へだけ明示的に戻す。
    connection = connection_settings.Connection(connection_kind, api_env_file, isolation_env_file)
    isolate_auth = connection.kind != "inherit"
    selected_environment = connection_settings.connection_environment(child_environment.inherited(), connection)
    env.update({key: value for key, value in selected_environment.items() if key not in os.environ})
    env.update(connection_settings.connection_overrides(connection))
    if lightweight:
        options.update(
            skills=[],
            disallowed_tools=["Skill"],
            tools={"type": "preset", "preset": "claude_code"},
            allowed_tools=_LAUNCH_ALLOWED_TOOLS[launch_kind],
        )
    if launch_kind == "explore":
        # 起動定義はpluginのSSOTを使い、検索を担うpyfltrを軽量起動へ渡す。
        # pyfltrの読取用途はプロンプトが定め、個別ツールの許可リストは設けない。
        mcp_config = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
        pyfltr = mcp_config["mcpServers"]["pyfltr"]
        pyfltr["args"] = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)) for arg in pyfltr.get("args", [])]
        options["mcp_servers"] = {"pyfltr": pyfltr}
        options.setdefault("extra_args", {}).update({"permission-prompts": "none"})
    with contextlib.ExitStack() as settings_owner:
        options["settings"] = claude_settings.merge_settings(
            _parent_settings(),
            env,
            root_session_id,
            force_file=api_env_file is not None,
            isolate_auth=isolate_auth,
            isolation_keys=connection_settings.isolation_keys(connection),
            cwd=cwd,
            project_settings=not lightweight,
            drop_parent_env=frozenset({_delegated_session.HANDOFF_PATH_ENV}),
        )
        if isinstance(options["settings"], claude_settings.SettingsFile):
            settings_owner.enter_context(options["settings"])
        result = ClaudeAgentOptions(**options)
        settings_owner.pop_all()
        return result
