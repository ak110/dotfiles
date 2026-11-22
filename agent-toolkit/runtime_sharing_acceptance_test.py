"""実uvで準備した共有環境から、複数配布版の公開コマンドをオフライン起動する。"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_toolkit._common import plugin_environment, plugin_environment_cleanup
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

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


def _run(arguments: list[str] | str, environment: dict[str, str], cwd: Path, *, input_text: str | None = None):
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


@pytest.mark.windows
@pytest.mark.usefixtures("share_package_caches")
@pytest.mark.timeout(240)
@pytest.mark.cpu_limit(
    12.0 if os.name == "nt" else 5.75,
    reason=(
        "2rootの共有依存・コード版・生存保護と回収を実hook/MCP/代表CLIで確認し、重複6起動を除く。"
        "Linux実測2.724秒/上限5.75秒、Windows実測6.328秒/上限12秒。"
    ),
)
def test_real_shared_environment_offline_public_entries_and_current_code(tmp_path_factory, host_environ):
    """同じ依存を共有し、更新版と旧版のコードをそれぞれのrootから起動する。"""
    # Windowsの共有環境のhashとDLL名を含むパスも通常のロード上限内に保つ。
    tmp_path = tmp_path_factory.mktemp("r")
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
    # 依存の宣言を持つ起動部分の本文の違いも、共有を妨げない。
    launcher = second / plugin_environment.DEPENDENCY_SCRIPT
    launcher.write_text(launcher.read_text(encoding="utf-8") + "# 別の配布版\n", encoding="utf-8")
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
            "AGENT_TOOLKIT_RUNTIME_DATA": str(tmp_path / "v"),
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
        definitions = json.loads((root / "hooks/hooks.json").read_text())
        command = definitions["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        command = command.replace("${CLAUDE_PLUGIN_ROOT}", str(root))
        shell = shutil.which("cmd" if os.name == "nt" else "sh", path=environment["PATH"])
        assert shell is not None
        # cmdの本文はargv用のlist2cmdlineへ渡さず、登録済みの引用符をそのまま解釈させる。
        arguments = f'{subprocess.list2cmdline([shell])} /d /s /c "{command}"' if os.name == "nt" else [shell, "-c", command]
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
        asyncio.run(asyncio.wait_for(_pyfltr_tools(root, environment, tmp_path), timeout=30))
        assert not (root / ".venv").exists()
    console = selected / ("Scripts/pygmentize.exe" if os.name == "nt" else "bin/pygmentize")
    assert console.is_file()
    _run([str(console), "-V"], environment, tmp_path)
    # 依存の固定内容が進んだrootは既存環境のready状態と混同しない。
    # 依存宣言を変えてlockfileを解決し直すと、オフラインでは取得物の保存先に索引の情報が無い環境（CIなど）で失敗する。
    # そのため解決結果を変えずにlockfileの内容だけを進めて、`uv sync --locked`が成功する未準備のrootを作る。
    lock = plugin_environment.dependency_lock(second)
    lock.write_text(lock.read_text(encoding="utf-8") + "\n# 依存の固定内容が進んだ版\n", encoding="utf-8")
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
    assert str(plugin_environment.base_python()) in missing.stderr.splitlines()[0]
    # dotfilesの更新直後と同じく依存の固定内容だけが進んだ未準備のrootでも、CLIの起動部分は準備してから実CLIを返す。
    # hookなどの起動対象は直前の`missing`のとおり待たずに終了する。
    cli = [str(second / "scripts/atk.cmd")] if os.name == "nt" else [str(second / "scripts/atk")]
    prepared_cli = _run([*cli, "--help"], environment, tmp_path)
    assert "config" in prepared_cli.stdout
    assert "共有依存環境を準備してから起動します" in prepared_cli.stderr
    assert plugin_environment.prepared_python(second, environment) is not None
    # 更新版のキーが変わっても、旧版の環境とコードは残る。
    assert plugin_environment.prepared_python(first, environment) is not None
    assert (
        json.loads(_run([sys.executable, str(first / "scripts/launch.py"), "probe.py"], environment, tmp_path).stdout)["label"]
        == "old"
    )
    # 実launcherを置換した子の寿命を保護し、終了後だけ旧依存を回収する。
    (first / "live_probe.py").write_text("import sys\nprint('ready', flush=True)\nsys.stdin.readline()\n", encoding="utf-8")
    current = plugin_environment.project_environment(second, environment)
    with plugin_environment.prepare_lock(selected):
        plugin_environment.register_source(first, selected, environment, python_executable=Path(sys.executable))
    with subprocess.Popen(
        [sys.executable, str(first / "scripts/launch.py"), "live_probe.py"],
        env=environment,
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
            first.rename(first.with_name("retired"))
            plugin_environment_cleanup.cleanup([current], environment, python_executable=Path(sys.executable))
            assert selected.is_dir() and current.is_dir()
            process.communicate("finish\n", timeout=15)
            assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=15)
    plugin_environment_cleanup.cleanup([current], environment, python_executable=Path(sys.executable))
    assert not selected.exists() and current.is_dir()
    # 回収後の現行環境は再準備なしで公開操作を実行できる。
    current_probe = _run([sys.executable, str(second / "scripts/launch.py"), "probe.py"], environment, tmp_path)
    assert json.loads(current_probe.stdout)["label"] == "new"
