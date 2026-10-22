"""pyfltr MCPサーバーの共有依存環境を事前構築する。

共有launcherを使う配布版はlockfileのpyfltrを準備し、公開CLIのversionで確認する。
旧版のuvx定義は、その定義が指定する既存のツールキャッシュを準備する。
起動形と要求指定は有効版のMCP定義から読み取る。
"""

import json
import logging
from pathlib import Path

from agent_toolkit._common import dotfiles_mode

from pytools._internal import claude_common, common, log_format, plugin_warmup, post_apply_outcome

logger = logging.getLogger(__name__)

_TAG = "pyfltr MCP warmup"
_SERVER_NAME = "pyfltr"
# Claude Codeは`.mcp.json`、Codexは`.codex-plugin/plugin.json`の`mcpServers`が指す`.mcp.codex.json`を読む。
_CLAUDE_MCP_RELATIVE = Path(".mcp.json")
_CODEX_MCP_RELATIVE = Path(".mcp.codex.json")
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH


def run() -> post_apply_outcome.PostApplyOutcome:
    """Claude CodeとCodexのMCP定義が指すpyfltrのツール環境を構築する。

    ツール環境の構築は導入に当たるため、構築できない場合は警告だけを出力してスキップと数える。
    uvのキャッシュだけへ作用し設定を変えないため、変更なしを返す。
    """
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
    uvx = common.resolve_executable("uvx", preferred_directories=(Path.home() / ".local" / "bin",))
    if uvx is None:
        logger.warning(
            log_format.format_status(
                _TAG,
                "uvx CLI が見つからず環境構築を開始できない。"
                "https://docs.astral.sh/uv/getting-started/installation/ "
                "の手順でuvを導入して`update-dotfiles`を再実行する",
            ),
        )
        return post_apply_outcome.PostApplyOutcome()
    definitions = plugin_warmup.agent_toolkit_targets(
        _INSTALLED_PLUGINS_PATH,
        claude_relative=_CLAUDE_MCP_RELATIVE,
        codex_relative=_CODEX_MCP_RELATIVE,
        tag=_TAG,
    )
    if not definitions:
        logger.warning(
            log_format.format_status(
                _TAG,
                "MCP定義が見つからず環境構築を開始できない。`update-dotfiles`を再実行してagent-toolkitを導入する",
            ),
        )
        return post_apply_outcome.PostApplyOutcome()
    # 同じ起動形は同じツール環境を使うため、定義ファイルをまとめて1回だけ起動する。
    launches: dict[tuple[str, ...], list[Path]] = {}
    for definition in definitions:
        try:
            data = json.loads(definition.read_text(encoding="utf-8"))
            servers = data.get("mcpServers") if isinstance(data, dict) else None
            server = servers.get(_SERVER_NAME) if isinstance(servers, dict) else None
            shared_arguments = server.get("args") if isinstance(server, dict) else None
            if (
                isinstance(server, dict)
                and server.get("command") == "uv"
                and isinstance(shared_arguments, list)
                and "agent_toolkit/_common/pyfltr_entry.py" in shared_arguments
            ):
                entry = definition.parent / "agent_toolkit/_common/pyfltr_entry.py"
                if not entry.is_file():
                    raise ValueError(f"共有pyfltr入口が存在しない: {entry}")
                uv = common.resolve_executable("uv", preferred_directories=(Path.home() / ".local/bin",))
                if uv is None:
                    raise ValueError("共有pyfltr入口に必要なuvが見つからない")
                plugin_warmup.warmup(entry, uv, tag=_TAG, arguments=("--version",))
                continue
            arguments = _warmup_arguments(definition)
        except (OSError, ValueError) as exc:
            logger.warning(
                log_format.format_status(
                    _TAG,
                    f"環境構築を開始できない: {exc}。{definition}と対応する入口ファイルを確認し、`update-dotfiles`を再実行する",
                ),
            )
            continue
        launches.setdefault(arguments, []).append(definition)
    for arguments, sources in launches.items():
        target = f"uvx {' '.join(arguments)} ({', '.join(log_format.home_short(path) for path in sources)})"
        plugin_warmup.run_command([str(uvx), *arguments], target=target, tag=_TAG)
    return post_apply_outcome.PostApplyOutcome()


def _warmup_arguments(definition: Path) -> tuple[str, ...]:
    """MCP定義の`pyfltr`の起動形から、末尾の`mcp`を`--version`へ置き換えた`uvx`の引数列を返す。"""
    short = log_format.home_short(definition)
    try:
        data = json.loads(definition.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"MCP定義を読めない: {short}: {exc}") from exc
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    server = servers.get(_SERVER_NAME) if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        raise ValueError(f"MCP定義に{_SERVER_NAME}が無い: {short}")
    command = server.get("command")
    args = server.get("args")
    if (
        command != "uvx"
        or not isinstance(args, list)
        or len(args) < 2
        or not all(isinstance(arg, str) for arg in args)
        or args[-1] != "mcp"
    ):
        raise ValueError(f"{_SERVER_NAME}の起動形が`uvx ... mcp`ではない: {short}: {command} {args}")
    return (*args[:-1], "--version")
