"""実uvで準備した共有環境から、複数配布版の公開コマンドをオフライン起動する。"""

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_toolkit._common import plugin_environment

ROOT = Path(__file__).resolve().parent


async def _pyfltr_tools(root: Path, environment: dict[str, str], cwd: Path) -> None:
    """共有環境のpyfltr MCPへ実際に接続し、初期化とツール一覧を確認する。"""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "scripts/launch.py"), "agent_toolkit/_common/pyfltr_entry.py", "mcp"],
        env=environment,
        cwd=str(cwd),
    )
    async with stdio_client(parameters) as (read, write), ClientSession(read, write, read_timeout_seconds=15) as client:
        await client.initialize()
        tools = await client.list_tools()
        assert tools.tools


def _copy(root: Path, label: str) -> None:
    """依存環境を含めず、配布物のコードと資源だけを複製する。"""

    def ignore(_directory: str, names: list[str]) -> list[str]:
        return [name for name in names if name in {".venv", ".git", "__pycache__", "node_modules"} or name.endswith("_test.py")]

    shutil.copytree(ROOT, root, ignore=ignore)
    (root / "root-label.txt").write_text(label, encoding="utf-8")
    (root / "probe.py").write_text(
        "import json, pathlib, sys, agent_toolkit\n"
        "root = pathlib.Path(__file__).parent\n"
        "print(json.dumps({'label': (root/'root-label.txt').read_text(), "
        "'code': agent_toolkit.__file__, 'prefix': sys.prefix}))\n",
        encoding="utf-8",
    )


def _run(arguments: list[str], environment: dict[str, str], cwd: Path, *, input_text: str | None = None):
    result = subprocess.run(
        arguments,
        env=environment,
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, (arguments, result.stdout, result.stderr)
    return result


@pytest.mark.usefixtures("share_package_caches")
@pytest.mark.timeout(240)
def test_real_shared_environment_offline_public_entries_and_current_code(tmp_path, host_environ):
    """同じ依存を共有し、更新版と旧版のコードをそれぞれのrootから起動する。"""
    first = tmp_path / "old-plugin"
    second = tmp_path / "new-plugin"
    _copy(first, "old")
    _copy(second, "new")
    basics = tmp_path / "quality-plugin"
    shutil.copytree(
        ROOT.parent / "agent-basics",
        basics,
        ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", "node_modules", "*_test.py"),
    )
    manifest = second / ".claude-plugin/plugin.json"
    value = json.loads(manifest.read_text())
    value["version"] = "999.1.0"
    manifest.write_text(json.dumps(value))
    pyproject = second / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace('description = "Coding agent workflow toolkit"', 'description = "別の配布版"')
    )
    environment = host_environ()
    # ホストの実uvを一時HOMEからも使う。mise shimの信頼登録は隔離した状態領域へ引き継がれない。
    host_home = environment["USERPROFILE" if os.name == "nt" else "HOME"]
    environment["PATH"] = str(Path(host_home) / ".local/bin") + os.pathsep + environment.get("PATH", "")
    home = tmp_path / "home"
    home.mkdir()
    for key in (
        "HOME",
        "USERPROFILE",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_DATA_HOME",
        "LOCALAPPDATA",
        "APPDATA",
        "TMPDIR",
    ):
        environment[key] = str(home)
    environment.update(
        {
            "AGENT_TOOLKIT_RUNTIME_DATA": str(tmp_path / "runtime"),
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_PYTHON": sys.executable,
            "UV_PYTHON_INSTALL_DIR": str(Path(sys.base_prefix).parent),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
        }
    )
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    registry = home / ".claude/plugins/installed_plugins.json"
    registry.parent.mkdir(parents=True)
    selected = plugin_environment.project_environment(first, environment)
    assert selected == plugin_environment.project_environment(second, environment)
    assert not selected.exists()
    _run(
        [sys.executable, str(first / "scripts/launch.py"), "--prepare", "agent_toolkit/agents_server_mcp.py"],
        environment,
        tmp_path,
    )
    assert plugin_environment.prepared_python(first, environment) is not None
    _run(
        [sys.executable, str(second / "scripts/launch.py"), "--prepare", "agent_toolkit/agents_server_mcp.py"],
        environment,
        tmp_path,
    )
    for root, label in ((first, "old"), (second, "new")):
        # 品質プラグイン分離後の実導入と同じregistryを、一時HOMEへ用意する。
        registry.write_text(
            json.dumps(
                {
                    "plugins": {
                        "agent-toolkit@ak110-dotfiles": [{"installPath": str(root)}],
                        "agent-basics@ak110-dotfiles": [{"installPath": str(basics)}],
                    }
                }
            ),
            encoding="utf-8",
        )
        probe = _run([sys.executable, str(root / "scripts/launch.py"), "probe.py"], environment, tmp_path)
        observed = json.loads(probe.stdout)
        assert observed["label"] == label
        assert Path(observed["code"]).parent == root / "agent_toolkit"
        assert Path(observed["prefix"]) == selected
        if os.name != "nt":
            help_result = _run([str(root / "scripts/atk"), "--help"], environment, tmp_path)
        else:
            help_result = _run([str(root / "scripts/atk.cmd"), "--help"], environment, tmp_path)
        assert "config" in help_result.stdout
        definitions = json.loads((root / "hooks/hooks.json").read_text())
        command = definitions["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        arguments = shlex.split(command.removesuffix(" || exit 1").replace("${CLAUDE_PLUGIN_ROOT}", str(root)))
        _run(
            arguments,
            environment,
            tmp_path,
            input_text=json.dumps(
                {
                    "tool_name": "Read",
                    "tool_input": {"file_path": str(root / "root-label.txt")},
                    "session_id": "runtime-sharing-acceptance",
                    "cwd": str(tmp_path),
                }
            ),
        )
        _run(
            [sys.executable, str(root / "scripts/launch.py"), "agent_toolkit/agents_server_mcp.py", "--check-dependencies"],
            environment,
            tmp_path,
        )
        _run(
            [sys.executable, str(root / "scripts/launch.py"), "agent_toolkit/_common/pyfltr_entry.py", "--version"],
            environment,
            tmp_path,
        )
        asyncio.run(asyncio.wait_for(_pyfltr_tools(root, environment, tmp_path), timeout=30))
        assert not (root / ".venv").exists()
    console = selected / ("Scripts/pygmentize.exe" if os.name == "nt" else "bin/pygmentize")
    assert console.is_file()
    _run([str(console), "-V"], environment, tmp_path)
    # 別の依存宣言は既存環境のready状態と混同しない。
    pyproject.write_text(pyproject.read_text() + '\n[project.optional-dependencies]\nseparate = ["filelock>=4"]\n')
    assert selected != plugin_environment.project_environment(second, environment)
    assert plugin_environment.prepared_python(second, environment) is None
    missing = subprocess.run(
        [sys.executable, str(second / "scripts/launch.py"), "probe.py"],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert missing.returncode == 1
    assert "update-dotfiles" in missing.stderr
    # 更新版のキーが変わっても、旧版の環境とコードは残る。
    assert plugin_environment.prepared_python(first, environment) is not None
    assert (
        json.loads(_run([sys.executable, str(first / "scripts/launch.py"), "probe.py"], environment, tmp_path).stdout)["label"]
        == "old"
    )
