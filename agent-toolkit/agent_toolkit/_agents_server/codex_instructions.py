"""Codexの起動設定と委譲指示。"""

from __future__ import annotations

import json
import logging
import os
import re
import typing
import uuid
from typing import Any

from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server.launch_prompts import (
    AUTO_RESUME_NOTICE,
    LAUNCH_SYSTEM_PROMPTS,
    LIGHTWEIGHT_LAUNCH_KINDS,
    cli_runtime_instructions,
    python_runtime_instructions,
)
from agent_toolkit._agents_server.state import LaunchKind
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import inherited_venv, mcp_environment, plugin_environment
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.codex_manager_contract import CodexManager

_LOG = logging.getLogger("agent-toolkit.agents-server.codex")


_BOUNDARY_PATH_PATTERN = re.compile(rf'<{AUTO_INSERTED_ELEMENT}\b[^>]*\spath="([^"]*)"')
"""`atk-auto`要素の開始タグから`path`属性の値を取り出す。"""


def _current_service_tier() -> str:
    """turnの開始時の実効設定を読み、Codex側の設定を継承せず速度を明示する。"""
    return "priority" if _atk_config.resolve_mutable_setting("codex_fast_mode") == "true" else "default"


def _embedded_rule_paths() -> set[str]:
    """Codexが読む全体指示ファイルが`atk-auto`要素で埋め込む規範の`path`属性の集合を返す。

    全体指示ファイルは`CODEX_HOME`が設定済みなら`$CODEX_HOME/AGENTS.md`、未設定なら`~/.codex/AGENTS.md`とする。
    ファイルが無いか読めない場合は空集合を返し、呼び出し側は`~/.claude/rules/`配下の全ファイルを委譲先へ渡す。
    """
    agents_md = _host_homes.codex_home() / "AGENTS.md"
    try:
        text = agents_md.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        return set()
    return set(_BOUNDARY_PATH_PATTERN.findall(text))


def _user_rules_instructions() -> str:
    """`~/.claude/rules/`配下の規範ファイルのうち、全体指示ファイルが埋め込まないものの本文を連結して返す。

    Claude backendの委譲先はユーザー設定の読込元から同じファイル群を受け取る。Codex backendの委譲先へも
    同じ集合を渡し、engineの選択で委譲先が従う規範が変わらないようにする。
    ファイル先頭の`atk-auto`要素の`path`属性が全体指示ファイルの埋め込みと一致するファイルは、同じ本文が
    既に届くため除く。ユーザーの編集を次の起動と再開から反映するため、呼び出しのたびに読み直す。
    対象が無い場合は空文字列を返す。
    """
    rules_dir = _host_homes.claude_config_dir() / "rules"
    if not rules_dir.is_dir():
        return ""
    embedded = _embedded_rule_paths()
    sections: list[str] = []
    for path in sorted(rules_dir.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8").rstrip("\n")
        except (OSError, UnicodeError) as exc:
            _LOG.warning("ユーザー規範を読めないため委譲先へ渡しません: path=%s error=%s", path, exc)
            continue
        first_line = body.split("\n", 1)[0]
        match = _BOUNDARY_PATH_PATTERN.match(first_line)
        if match is not None and match.group(1) in embedded:
            continue
        sections.append(auto_message(body, source="agents-server", kind="user-rules", attributes={"path": str(path)}))
    if not sections:
        return ""
    return (
        "\n\n委譲元のホストで`~/.claude/rules/`に置かれた規範ファイルを次に示す。"
        "各ファイルが定める適用範囲に作業が入る条文に従う。各要素の`path`属性のファイルは読了済みとして扱う。\n"
        + "\n".join(sections)
    )


def _developer_instructions(launch_kind: LaunchKind) -> str:
    """委譲先の役割、このprocessが起動時に確定した配布物root、ユーザーが置いた規範ファイルの本文を1つの指示として返す。"""
    plugin_root = plugin_roots.SERVER_PLUGIN_ROOT
    runtime = f"\n{python_runtime_instructions()}" if launch_kind in LIGHTWEIGHT_LAUNCH_KINDS else ""
    return (
        f"{LAUNCH_SYSTEM_PROMPTS[launch_kind]}\n{AUTO_RESUME_NOTICE}{runtime}\n{cli_runtime_instructions(plugin_root)}\n\n"
        f"agent-toolkit plugin root: {plugin_root}\n"
        "担当自身が使うagent-toolkitのskillと実行基盤のplugin内部資源は、この実在する絶対パスを起点に読む。"
        "`<役割名>.subagent.md`や別hostのcache版数から別のplugin rootを組み立てない。"
        "成果物と、その成果物が参照する実装・文書・CLIの存否と挙動は、"
        "受領した絶対パスと対象worktreeまたは保存版から判定する。"
        "保持版での不在を対象版の不在の根拠にしない。"
        f"{_user_rules_instructions()}"
    )


class CodexInstructionsMixin:
    """Codexの起動設定と委譲指示。

    AppServerManagerへ合成して使うため、各メソッドでは合成後の共有状態と他の責務を参照する。
    """

    def _thread_config(
        self: CodexManager, session_id: str | None = None, *, lightweight: bool, client: Any = None
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """thread開始・再開に必要な設定と書込主体を返す。"""
        config = self._base_thread_config(lightweight=lightweight)
        owner_session_id = self._root_session_id
        if owner_session_id is None:
            return config, None, None
        writer_session_id = self._writer_session_ids.get(session_id) if session_id is not None else None
        if writer_session_id is None:
            writer_session_id = uuid.uuid4().hex
            if session_id is not None:
                self._writer_session_ids[session_id] = writer_session_id
        config.update(self._agents_server_config(owner_session_id, writer_session_id, client))
        return config, owner_session_id, writer_session_id

    @staticmethod
    def _agents_server_config(owner_session_id: str, writer_session_id: str, client: Any = None) -> dict[str, Any]:
        """内側のMCPサーバーへ状態ファイルの書込主体を配送する設定を返す。

        `env_vars`は内側MCPを起動するApp Server（`client`）へ実際に渡した環境から選ぶ。
        """
        plugin_root = plugin_roots.SERVER_PLUGIN_ROOT
        parent_environment = getattr(client, "parent_environment", None)
        if parent_environment is None:
            parent_environment = dict(os.environ)
            inherited_venv.strip_inherited_venv(parent_environment)
        return {
            "mcp_servers": {
                "agents_server": {
                    "command": "uv",
                    "args": [
                        "run",
                        "--no-config",
                        "--no-project",
                        "--python",
                        "3.15",
                        "python",
                        str(plugin_root / "scripts" / "launch.py"),
                        "agent_toolkit/agents_server_mcp.py",
                    ],
                    "env": {
                        _delegated_session.OWNER_SESSION_ENV: owner_session_id,
                        "AGENT_TOOLKIT_STATUS_HOST_SESSION": writer_session_id,
                        _delegated_session.PLUGIN_ROOT_ENV: str(plugin_root),
                        plugin_environment.RUNTIME_DATA_ENV: str(plugin_environment.runtime_data(os.environ)),
                    },
                    "env_vars": list(mcp_environment.runtime_environment_variable_names(parent_environment)),
                    "default_tools_approval_mode": "approve",
                }
            }
        }

    @staticmethod
    def _base_thread_config(*, lightweight: bool) -> dict[str, Any]:
        """thread開始・再開のリクエストへ常に載せる設定を返す。

        `bypass_hook_trust`は、hookの定義が変わった後もcodexが承認済みの記録を要求せずにhookを実行するために渡す。
        `agents_server`が開始する委譲先は対話UIを持たず、承認要求へ応答する主体が存在しないため、
        このキーが無いとプラグインの更新のたびに委譲先が起動しない。
        この制約は`launch_kind`によらず同じであるため、軽量起動と通常の委譲で分けない。
        `bypass_hook_trust`は`codex app-server`のコマンドラインオプションとしても`-c`による設定上書きとしても受理されず、
        `thread/start`系リクエストの`config`だけが受理する。
        """
        # 依頼の保持を通常の会話圧縮へ統一し、要約なし切替を提供するホーム設定を上書きする。
        features: dict[str, Any] = {"context_management": {"experimental_mode": False}}
        config: dict[str, Any] = {
            "bypass_hook_trust": True,
            "features": features,
        }
        if lightweight:
            config["project_doc_max_bytes"] = 0
        return config

    @staticmethod
    def _explore_config(config: dict[str, Any], client: Any) -> dict[str, Any]:
        """スキル非配送を保ち、読取MCPの定義を直接の設定として渡す。"""
        root = plugin_roots.SERVER_PLUGIN_ROOT
        pyfltr = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["pyfltr"]
        pyfltr["args"] = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(root)) for arg in pyfltr.get("args", [])]
        environment = getattr(client, "parent_environment", None) or os.environ
        pyfltr["env_vars"] = list(mcp_environment.runtime_environment_variable_names(environment))
        pyfltr["default_tools_approval_mode"] = "approve"
        # pluginを無効にする探索にも、出力省略の回収案内をPostToolUse（Bash）から届ける。
        # 回収可能な省略を理由に続行不能を返した委譲先に探索担当が含まれていた。資料本文は返さない。
        # shell・writeはpluginのhookをそのまま受けるため、ここで重ねて接続しない。
        groups = json.loads((root / "hooks/hooks.codex.json").read_text(encoding="utf-8"))["hooks"]["PostToolUse"]
        hooks = {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {**handler, "command": handler["command"].replace("${PLUGIN_ROOT}", str(root))}
                        for group in groups
                        for handler in group["hooks"]
                    ],
                }
            ],
        }
        return {
            **config,
            "features": {**config.get("features", {}), "plugins": False, "hooks": True},
            "hooks": hooks,
            "mcp_servers": {**config.get("mcp_servers", {}), "pyfltr": pyfltr},
        }
