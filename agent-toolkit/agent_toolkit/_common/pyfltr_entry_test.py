"""外部pyfltrへplugin専用のuv環境を渡さないことを検証する。"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import anyio
import mcp
import pytest
from mcp.client.stdio import StdioServerParameters

from agent_toolkit._common import inherited_venv, plugin_environment, pyfltr_entry
from agent_toolkit._testing.isolation import CONFIG_DIRECTORY_ENVIRONMENT_NAMES, HOME_ENVIRONMENT_NAMES


@pytest.mark.parametrize("caller", [None, "", "対象リポジトリの環境"])
def test_pyfltr_restores_caller_project_environment(monkeypatch, caller):
    """MCP内の対象プロジェクト操作が共有plugin環境を更新しないようにする。"""
    monkeypatch.setenv(inherited_venv.PLUGIN_RUNTIME_ENVIRONMENT_KEY, "/plugin/environment")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/plugin/environment")
    monkeypatch.delenv(inherited_venv.CALLER_PROJECT_ENVIRONMENT_KEY, raising=False)
    if caller is not None:
        monkeypatch.setenv(inherited_venv.CALLER_PROJECT_ENVIRONMENT_KEY, caller)

    def invoke():
        assert os.environ.get("UV_PROJECT_ENVIRONMENT") == caller
        assert inherited_venv.PLUGIN_RUNTIME_ENVIRONMENT_KEY not in os.environ
        return 7

    entry = SimpleNamespace(group="console_scripts", name="pyfltr", load=lambda: invoke)
    monkeypatch.setattr(pyfltr_entry.importlib.metadata, "distribution", lambda name: SimpleNamespace(entry_points=[entry]))
    assert pyfltr_entry.main() == 7


@pytest.mark.parametrize("virtual_env", [False, True])
def test_pyfltr_keeps_own_scripts_once_after_launch_restore(monkeypatch, tmp_path, virtual_env):
    """呼出元PATHを復元した後も自身の実行ファイル格納先を優先し、空要素と他の順序を保つ。"""
    scripts = Path(sys.executable).absolute().parent
    caller = {"PATH": os.pathsep.join([str(tmp_path), str(scripts), "", str(scripts), "last"])}
    launched = {**caller, "PATH": "launch-path"}
    inherited_venv.record_launch_changes(launched, caller)
    for key, value in launched.items():
        monkeypatch.setenv(key, value)
    if virtual_env:
        monkeypatch.setenv("VIRTUAL_ENV", str(scripts.parent))

    def invoke():
        assert os.environ["PATH"].split(os.pathsep) == [str(scripts), str(tmp_path), "", "last"]
        assert "VIRTUAL_ENV" not in os.environ
        return 0

    entry = SimpleNamespace(group="console_scripts", name="pyfltr", load=lambda: invoke)
    monkeypatch.setattr(pyfltr_entry.importlib.metadata, "distribution", lambda name: SimpleNamespace(entry_points=[entry]))
    assert pyfltr_entry.main() == 0


@pytest.mark.windows
@pytest.mark.asyncio
async def test_console_cli_and_mcp_use_bundled_tools_and_work_dir_uv(
    tmp_path: Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """失敗shimが先行しても実entry pointのCLI・MCPが同梱ツールを使い、uvはwork_dirを選ぶ。"""
    scripts = Path(sys.executable).absolute().parent
    for tool in ("prek", "typos"):
        assert shutil.which(tool, path=str(scripts)) is not None
    host = host_environ()
    # uvは検証用projectを作るためのnative実行体を使う。mise未登録のshimは準備の入力にしない。
    uv = shutil.which(
        "uv", path=os.pathsep.join(entry for entry in host["PATH"].split(os.pathsep) if Path(entry).name != "shims")
    )
    assert uv is not None
    environment = {**os.environ, "PATH": host["PATH"]}
    # ホストから借りるのは実行ファイル探索だけとし、設定・履歴・uv保存先は試験自身で隔離する。
    for name in (*HOME_ENVIRONMENT_NAMES, *CONFIG_DIRECTORY_ENVIRONMENT_NAMES):
        environment[name] = str(tmp_path / "home" / name.lower())
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    environment.update(
        {
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_PYTHON": sys.executable,
            "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
            "PYFLTR_CACHE_DIR": str(tmp_path / "pyfltr-cache"),
            "PYFLTR_GLOBAL_CONFIG": str(tmp_path / "global.toml"),
            "MISE_CEILING_PATHS": str(tmp_path),
        }
    )
    shims = tmp_path / "mise" / "shims"
    shims.mkdir(parents=True)
    for tool in ("prek", "typos"):
        shim = shims / (tool + ".cmd" if os.name == "nt" else tool)
        shim.write_text(
            "@echo off\r\necho unexpected-mise-shim 1>&2\r\nexit /b 86\r\n"
            if os.name == "nt"
            else "#!/bin/sh\necho unexpected-mise-shim >&2\nexit 86\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)
    environment["PATH"] = os.pathsep.join([str(shims), str(scripts), environment["PATH"]])
    project = tmp_path / "project"
    project.mkdir()
    (project / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    (project / "sample.txt").write_text("hello world\n", encoding="utf-8")
    (project / "probe.py").write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path('observed.json').write_text(json.dumps({'prefix': sys.prefix, 'cwd': os.getcwd(), "
        "'virtual_env': os.environ.get('VIRTUAL_ENV'), 'project_env': os.environ.get('UV_PROJECT_ENVIRONMENT')}))\n",
        encoding="utf-8",
    )
    # 依存の無いprojectなのでuvはネットワークを使わず、自身で選んだ環境のPythonを起動できる。
    (project / "pyproject.toml").write_text(
        '[project]\nname="environment-probe"\nversion="0"\ndependencies=[]\n'
        "[tool.pyfltr]\nrespect-gitignore=false\nprek=true\ntypos=true\n"
        'prek-args=["--version"]\nprek-pass-filenames=false\n'
        '[tool.pyfltr.custom-commands.project-uv]\ntype="linter"\n'
        f'path={json.dumps(uv)}\nargs=["run", "--frozen", "python", "probe.py"]\n'
        'targets="*.txt"\npass-filenames=false\n',
        encoding="utf-8",
    )
    locked = subprocess.run(
        [uv, "lock", "--offline", "--project", str(project)],
        env={**environment, "UV_FROZEN": "0"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert locked.returncode == 0, locked.stderr
    root = Path(pyfltr_entry.__file__).resolve().parents[2]
    environment = plugin_environment.direct_environment(root, environment)
    environment["VIRTUAL_ENV"] = sys.prefix
    plugin_cfg = Path(sys.prefix) / "pyvenv.cfg"
    protected = plugin_cfg.read_bytes()
    argv = [sys.executable, "-m", "agent_toolkit._common.pyfltr_entry"]
    cli = subprocess.run(
        [*argv, "run", "sample.txt", "--commands=prek,typos", "--no-fix", "--no-cache", "--output-format=jsonl"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    cli_commands = {
        record["command"]: record["status"]
        for line in cli.stdout.splitlines()
        if (record := json.loads(line)).get("kind") == "command"
    }
    assert cli_commands == {"prek": "succeeded", "typos": "succeeded"}
    server = StdioServerParameters(command=sys.executable, args=[*argv[1:], "mcp"], cwd=tmp_path, env=environment)
    with anyio.fail_after(45):
        async with mcp.Client(server) as client:
            result = await client.call_tool(
                "run",
                {
                    "paths": ["sample.txt"],
                    "commands": ["prek", "typos", "project-uv"],
                    "work_dir": str(project),
                    "no_fix": True,
                    "no_cache": True,
                },
            )
    assert not result.is_error, result
    assert result.structured_content is not None
    assert result.structured_content["exit_code"] == 0, result.structured_content
    assert {item["command"]: item["status"] for item in result.structured_content["commands"]} == {
        **cli_commands,
        "project-uv": "succeeded",
    }
    observed = json.loads((project / "observed.json").read_text(encoding="utf-8"))
    assert Path(observed["prefix"]) == project / ".venv"
    assert Path(observed["cwd"]) == project
    assert observed["project_env"] is None
    assert Path(observed["virtual_env"]) == project / ".venv"
    assert plugin_cfg.read_bytes() == protected
