"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib
import shutil
from typing import Any

import pytest

import agent_toolkit.agents_server_mcp as entry_script
from agent_toolkit._agents_server import launch_requests, mcp_tools
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import (
    _REAL_PLUGIN_PREFLIGHT,
    _actionable_message,
    _manager_with_fake,
    _recording_candidates,
    _use_real_plugin_preflight,
    _write_failing_uv,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def test_preflight_covers_plugin_launch_commands() -> None:
    """事前確認は、プラグインのMCP設定とhookが委譲先で起動するコマンドの先頭語を全て含む。

    起動コマンドを追加した変更で事前確認を追加しないと、未trustの設定などで起動が失敗する状態を
    子の起動前に検出できず、ホスト共通の接続失敗記録を生じさせる。
    """
    plugin_root = pathlib.Path(entry_script.__file__).resolve().parent.parent
    launch_words: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            command = node.get("command")
            if isinstance(command, str) and command.strip():
                launch_words.add(command.split()[0])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    for name in (".mcp.json", ".mcp.codex.json", "mcp.json", "hooks/hooks.json"):
        collect(json.loads((plugin_root / name).read_text(encoding="utf-8")))

    assert launch_words
    assert launch_words <= {command[0] for command in launch_requests.PREFLIGHT_COMMANDS}


@pytest.mark.asyncio
async def test_start_rejects_cwd_where_plugin_commands_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """起動コマンドが失敗する作業ディレクトリでは子を起動せず、cwdと標準エラーを含む例外を返す。"""
    _use_real_plugin_preflight(monkeypatch)
    _recording_candidates(monkeypatch)
    bin_dir = tmp_path / "bin"
    _write_failing_uv(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir))
    workdir = tmp_path / "work"
    workdir.mkdir()
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

        with pytest.raises(ValueError) as raised:
            await mcp_tools.start(str(workdir), mode="explore", prompt="調査")

        message = _actionable_message(raised.value)
        assert f"cwd={workdir}" in message
        assert "command=uv --version" in message
        assert "exit_code=1" in message
        assert "not trusted" in message
        assert "`mise trust`" in message
        assert not backend.start_calls
        assert not manager.sessions


@pytest.mark.asyncio
async def test_start_reports_path_check_when_plugin_commands_are_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """起動コマンドの実行ファイルが無い場合は、mise以外の原因としてPATHの確認を次の操作に示す。

    miseの案内だけを返すと、委譲元は無関係な`mise trust`を試して同じ失敗を繰り返す。
    """
    _use_real_plugin_preflight(monkeypatch)
    _recording_candidates(monkeypatch)
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

        with pytest.raises(ValueError) as raised:
            await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査")

        message = _actionable_message(raised.value)
        assert "実行ファイルが見つからない" in message
        assert "PATH" in message.split(NEXT_ACTION_PREFIX, 1)[1]
        assert not backend.start_calls


def test_preflight_passes_where_plugin_commands_succeed(tmp_path: pathlib.Path) -> None:
    """起動コマンドが成功する作業ディレクトリでは事前確認を通過する。"""
    if shutil.which("uv") is None or shutil.which("uvx") is None:
        pytest.skip("uvまたはuvxが未導入")
    _REAL_PLUGIN_PREFLIGHT(str(tmp_path))


def test_every_parent_contract_has_one_input_section() -> None:
    """全ての共有親契約が、拒否時に案内できる入力節を1つだけ持つ。"""
    contracts = sorted(launch_requests._SHARE_DIRECTORY.glob("*.parent.md"))
    assert contracts
    for contract in contracts:
        assert launch_requests._input_section(contract) in {"入力", "渡す入力"}, contract
