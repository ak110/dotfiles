"""ホストの保存先と版別依存環境を、配布rootへ書かずに解決する契約を検証する。"""

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_toolkit._common import inherited_venv
from agent_toolkit._common import plugin_environment as subject


def _root(directory: Path) -> Path:
    directory.mkdir()
    (directory / "pyproject.toml").write_text(
        '[project]\nname="bootstrap-probe"\nversion="1.0.0"\nrequires-python=">=3.15"\ndependencies=[]\n',
        encoding="utf-8",
    )
    (directory / "uv.lock").write_text("lock", encoding="utf-8")
    manifest = directory / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"version":"1.0.0"}', encoding="utf-8")
    return directory


def _user_data(directory: Path) -> dict[str, str]:
    """LinuxとWindowsのユーザーデータ領域を同じ一時ディレクトリへ向ける。"""
    return {
        "HOME": str(directory),
        "USERPROFILE": str(directory),
        "XDG_DATA_HOME": str(directory),
        "LOCALAPPDATA": str(directory),
    }


def _prepared(root: Path, environment: dict[str, str]) -> None:
    """実際の同期をせず、隔離したテスト環境を準備済みとして公開する。"""
    temporary = subject.project_environment(root, environment)
    executable = subject.python_path(temporary)
    executable.parent.mkdir(parents=True)
    shutil.copyfile(sys.executable, executable)
    executable.chmod(0o755)
    shutil.copyfile(Path(sys.prefix) / "pyvenv.cfg", temporary / "pyvenv.cfg")
    (temporary / "lib").symlink_to(Path(sys.prefix) / "lib", target_is_directory=True)
    subject.publish_prepared_environment(root, environment)


def test_dependency_environments_share_roots_and_ignore_non_dependency_metadata(tmp_path: Path) -> None:
    """root・配布版・説明・lint設定は共有を妨げず、lockfileの更新は分離する。"""
    root = _root(tmp_path / "first")
    other = _root(tmp_path / "second")
    environment = {subject.RUNTIME_DATA_ENV: str(tmp_path / "data")}
    first = subject.project_environment(root, environment)
    assert first == subject.project_environment(root, environment)
    assert first == subject.project_environment(other, environment)
    (other / "pyproject.toml").write_text(
        '[project]\nname="bootstrap-probe"\nversion="2.0.0"\ndescription="別の説明"\nrequires-python=">=3.15"\ndependencies=[]\n\n[tool.ruff]\nline-length=88\n',
        encoding="utf-8",
    )
    assert first == subject.project_environment(other, environment)
    (root / "uv.lock").write_text("changed lock", encoding="utf-8")
    locked = subject.project_environment(root, environment)
    assert locked != first
    (root / ".claude-plugin/plugin.json").write_text('{"version":"2.0.0"}', encoding="utf-8")
    assert subject.project_environment(root, environment) == locked
    assert not first.exists()
    assert first.is_relative_to(tmp_path / "data")


def test_prepared_environment_is_published_only_after_completion(tmp_path: Path) -> None:
    """ready markerを持つ原子的に公開済み環境だけが通常起動で選ばれる。"""
    root = _root(tmp_path / "plugin")
    environment = {subject.RUNTIME_DATA_ENV: str(tmp_path / "data")}
    temporary = subject.project_environment(root, environment)
    executable = subject.python_path(temporary)
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    assert subject.prepared_python(root, environment) is None
    published = subject.publish_prepared_environment(root, environment)
    assert subject.prepared_python(root, environment) == subject.python_path(published)


def test_direct_environment_puts_current_root_before_existing_pythonpath(tmp_path: Path) -> None:
    """共有依存環境でも各配布rootのコードを先に解決する。"""
    root = _root(tmp_path / "plugin")
    environment = {subject.RUNTIME_DATA_ENV: str(tmp_path / "data"), "PYTHONPATH": "existing"}
    direct = subject.direct_environment(root, environment)
    assert direct["PYTHONPATH"].split(os.pathsep) == [str(root.resolve()), "existing"]
    assert Path(direct["UV_PROJECT_ENVIRONMENT"]).is_relative_to(tmp_path / "data" / "environments")


@pytest.mark.parametrize("caller_pythonpath", [None, "existing"])
def test_direct_environment_restores_callers_pythonpath_for_child(tmp_path: Path, caller_pythonpath: str | None) -> None:
    """直接起動の子は起動層が前置した読込先を継承しない。"""
    root = _root(tmp_path / "plugin")
    environment = {subject.RUNTIME_DATA_ENV: str(tmp_path / "data")}
    if caller_pythonpath is not None:
        environment["PYTHONPATH"] = caller_pythonpath
    child = subject.direct_environment(root, environment)
    inherited_venv.strip_inherited_venv(child)
    assert child.get("PYTHONPATH") == caller_pythonpath


def test_nested_direct_environment_keeps_only_inner_root(tmp_path: Path) -> None:
    """入れ子の直接起動は外側の読込先と復元記録を残さない。"""
    outer = _root(tmp_path / "outer")
    inner = _root(tmp_path / "inner")
    environment = {subject.RUNTIME_DATA_ENV: str(tmp_path / "data"), "PYTHONPATH": "existing"}
    nested = subject.direct_environment(inner, subject.direct_environment(outer, environment))
    assert nested["PYTHONPATH"].split(os.pathsep) == [str(inner.resolve()), "existing"]


def test_launch_preserves_callers_environment_and_cli_preserves_path(tmp_path: Path) -> None:
    """呼出元の環境を変更せず、uvの子プロセスへ専用の保存先を渡し、CLIのPATHには既存の要素を保つ。"""
    root = _root(tmp_path / "plugin")
    incoming = {
        **_user_data(tmp_path / "user-data"),
        "PLUGIN_DATA": str(tmp_path / "codex-data"),
        "PATH": "first::last",
        "OTHER": "keep",
    }
    before = dict(incoming)
    launched = subject.launch_environment(root, incoming)
    assert incoming == before
    assert launched["PATH"] == incoming["PATH"]
    assert launched["OTHER"] == "keep"
    assert launched["UV_PROJECT_ENVIRONMENT"] == launched[subject.RUNTIME_ENVIRONMENT_ENV]
    assert Path(launched["UV_PROJECT_ENVIRONMENT"]).is_relative_to(tmp_path / "user-data" / "agent-toolkit")
    cli = subject.cli_environment(root, incoming)
    assert cli["PATH"].endswith(incoming["PATH"])
    assert cli == subject.cli_environment(root, cli)
    assert "UV_PROJECT_ENVIRONMENT" not in cli
    assert incoming == before


def test_session_start_persists_before_uv_and_keeps_other_hooks_and_user_path(
    tmp_path: Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """uvが無い条件でも先にPATHを保存し、Bashで別hookと既存PATHの保持を観測する。"""
    root = _root(tmp_path / "plugin with 'quote'")
    (root / "probe.py").write_text("", encoding="utf-8")
    env_file = tmp_path / "session-env"
    original = 'export OTHER_HOOK=kept\nexport PATH="other-hook:$PATH"\n'
    env_file.write_text(original, encoding="utf-8")
    environment = host_environ()
    bash = shutil.which("bash", path=environment["PATH"])
    assert bash is not None
    environment.update({"PATH": "", "CLAUDE_ENV_FILE": str(env_file), "CLAUDE_PLUGIN_DATA": str(tmp_path / "data")})
    environment.update(_user_data(tmp_path / "user-data"))
    environment.pop(subject.RUNTIME_DATA_ENV, None)
    bootstrap = Path(subject.__file__).resolve().parents[2] / "scripts/launch.py"
    result = subprocess.run(
        [sys.executable, str(bootstrap), "--project", str(root), "--persist-claude-env", "probe.py"],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    saved = env_file.read_text(encoding="utf-8")
    assert result.returncode == 127
    assert saved.startswith(original)
    assert "UV_PROJECT_ENVIRONMENT" not in saved
    environment["PATH"] = "user-first::user-last"
    observed = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", 'source "$1"; printf "%s\\n%s\\n" "$PATH" "$OTHER_HOOK"', "bash", str(env_file)],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    print(
        json.dumps(
            {
                "bootstrap_exit": result.returncode,
                "bootstrap_stderr": result.stderr,
                "environment_file": saved,
                "bash_exit": observed.returncode,
                "bash_stdout": observed.stdout,
                "bash_stderr": observed.stderr,
            },
            ensure_ascii=False,
        )
    )
    assert observed.returncode == 0, observed.stderr
    assert observed.stdout.splitlines() == [str(root / "scripts") + ":other-hook:user-first::user-last", "kept"]


def test_bootstrap_executes_from_readonly_root_and_preserves_process_contract(
    tmp_path: Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """実際のuvから起動し、root外の環境、argv、cwd、stdin、標準出力と標準エラー、子プロセスの終了コード7が保たれることを確認する。"""
    root = _root(tmp_path / "readonly-plugin")
    environment = host_environ()
    uv = shutil.which("uv", path=environment["PATH"])
    assert uv is not None
    environment["UV_PYTHON"] = sys.executable
    environment["CLAUDE_PLUGIN_DATA"] = str(tmp_path / "host-data")
    environment.update({"XDG_DATA_HOME": str(tmp_path / "writable-data"), "LOCALAPPDATA": str(tmp_path / "writable-data")})
    environment.pop(subject.RUNTIME_DATA_ENV, None)
    environment.pop(subject.RUNTIME_ENVIRONMENT_ENV, None)
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    (root / "uv.lock").unlink()
    locked = subprocess.run(
        [uv, "lock", "--offline", "--project", str(root)],
        # fixtureの新規生成だけ許可し、bootstrapには呼出元の凍結条件を保つ。
        env={**environment, "UV_FROZEN": "0"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=15,
    )
    assert locked.returncode == 0, locked.stderr
    _prepared(root, environment)
    probe = root / "probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'input': sys.stdin.read(), "
        "'environment': os.environ['UV_PROJECT_ENVIRONMENT']}, ensure_ascii=False))\n"
        "print('子の標準エラー', file=sys.stderr)\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    original_files = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    for path in root.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)
    bootstrap = Path(subject.__file__).resolve().parents[2] / "scripts/launch.py"
    try:
        result = subprocess.run(
            [sys.executable, str(bootstrap), "--project", str(root), "probe.py", "空 白", "--value", "literal"],
            cwd=tmp_path,
            env=environment,
            input="標準入力の本文",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        print(
            json.dumps(
                {"argv": result.args, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
                ensure_ascii=False,
            )
        )
        assert result.returncode == 7, result.stderr
        observed = json.loads(result.stdout)
        assert observed["argv"] == ["空 白", "--value", "literal"]
        assert observed["cwd"] == str(tmp_path)
        assert observed["input"] == "標準入力の本文"
        assert Path(observed["environment"]).is_relative_to(tmp_path / "writable-data")
        assert "子の標準エラー" in result.stderr
        assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == original_files
        assert not (root / ".venv").exists()
    finally:
        root.chmod(0o755)
        for path in root.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)


def test_runtime_data_is_shared_by_hosts_and_falls_back_to_host_data(tmp_path: Path) -> None:
    """ホストが異なってもユーザーデータ領域を共用し、書き込めない場合だけホストのplugin dataを使う。"""
    shared = _user_data(tmp_path / "user-data")
    claude = subject.runtime_data({**shared, "CLAUDE_PLUGIN_DATA": str(tmp_path / "claude-data")})
    codex = subject.runtime_data({**shared, "PLUGIN_DATA": str(tmp_path / "codex-data")})
    terminal = subject.runtime_data(shared)
    assert claude == codex == terminal == (tmp_path / "user-data" / "agent-toolkit").resolve()
    blocked = tmp_path / "blocked"
    blocked.write_text("ディレクトリを作成できない位置", encoding="utf-8")
    fallback = subject.runtime_data({**_user_data(blocked), "CLAUDE_PLUGIN_DATA": str(tmp_path / "claude-data")})
    assert fallback == (tmp_path / "claude-data").resolve()
    explicit = subject.runtime_data({**shared, subject.RUNTIME_DATA_ENV: str(tmp_path / "explicit")})
    assert explicit == (tmp_path / "explicit").resolve()


@pytest.mark.parametrize("caller_setting", ["value", "empty", "unset"])
def test_session_start_hook_separates_real_uv_and_restores_child_environment(
    tmp_path: Path, host_environ: Callable[[], dict[str, str]], caller_setting: str
) -> None:
    """実uvのhook起動で専用環境と子への元設定の復元を両立し、分離を外した起動との差を観測する。"""
    root = _root(tmp_path / "hook-plugin")
    environment = host_environ()
    uv = shutil.which("uv", path=environment["PATH"])
    assert uv is not None
    for key in (
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        subject.RUNTIME_ENVIRONMENT_ENV,
        inherited_venv.CALLER_PROJECT_ENVIRONMENT_KEY,
    ):
        environment.pop(key, None)
    environment.update({"UV_PYTHON": sys.executable, subject.RUNTIME_DATA_ENV: str(tmp_path / "runtime-data")})
    caller = tmp_path / "caller-environment"
    created = subprocess.run(
        [uv, "venv", "--python", sys.executable, str(caller)],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    site_packages = Path(sysconfig.get_path("purelib", vars={"base": str(caller), "platbase": str(caller)}))
    marker = site_packages / "caller_package-1.0.0.dist-info" / "METADATA"
    marker.parent.mkdir(parents=True)
    marker.write_text("Name: caller-package\nVersion: 1.0.0\n", encoding="utf-8")
    module = site_packages / "caller_package.py"
    module.write_text("VALUE = 'caller-package'\n", encoding="utf-8")
    protected_paths = (caller / "pyvenv.cfg", marker, module)
    protected = {str(path.relative_to(caller)): path.read_bytes() for path in protected_paths}
    expected = None if caller_setting == "unset" else ("" if caller_setting == "empty" else str(caller))
    expected_pythonpath = environment.get("PYTHONPATH")
    if expected is not None:
        environment["UV_PROJECT_ENVIRONMENT"] = expected
    environment["CLAUDE_ENV_FILE"] = str(tmp_path / "session-start-env.sh")
    (root / "uv.lock").unlink()
    locked = subprocess.run(
        [uv, "lock", "--offline", "--project", str(root)],
        env={**environment, "UV_FROZEN": "0"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert locked.returncode == 0, locked.stderr
    _prepared(root, environment)
    shutil.copyfile(Path(inherited_venv.__file__), root / "inherited_venv.py")
    probe = root / "hook_probe.py"
    probe.write_text(
        "import json, os, subprocess, sys\nimport inherited_venv\n"
        "child_environment = dict(os.environ)\n"
        "inherited_venv.strip_inherited_venv(child_environment)\n"
        "child = subprocess.run([sys.executable, '-c', "
        "\"import json, os; print(json.dumps({'caller': os.environ.get('UV_PROJECT_ENVIRONMENT'), "
        "'pythonpath': os.environ.get('PYTHONPATH')}))\"], "
        "env=child_environment, capture_output=True, text=True, check=True, timeout=5)\n"
        "print(json.dumps({'prefix': sys.prefix, 'environment': os.environ['UV_PROJECT_ENVIRONMENT'], "
        "'child': json.loads(child.stdout), 'input': sys.stdin.read()}))\n",
        encoding="utf-8",
    )
    bootstrap = Path(subject.__file__).resolve().parents[2] / "scripts/launch.py"
    launched = subprocess.run(
        [sys.executable, str(bootstrap), "--project", str(root), "--persist-claude-env", probe.name],
        cwd=tmp_path,
        env=environment,
        input='{"hook_event_name":"SessionStart"}',
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert launched.returncode == 0, launched.stderr
    observed = json.loads(launched.stdout)
    assert Path(observed["environment"]).is_relative_to(tmp_path / "runtime-data" / "environments")
    assert observed["child"] == {"caller": expected, "pythonpath": expected_pythonpath}
    assert json.loads(observed["input"]) == {"hook_event_name": "SessionStart"}
    assert {str(path.relative_to(caller)): path.read_bytes() for path in protected_paths} == protected
    assert "UV_PROJECT_ENVIRONMENT" not in Path(environment["CLAUDE_ENV_FILE"]).read_text(encoding="utf-8")
    control = None
    if caller_setting == "value":
        # 同じ実uvへ呼出元の指定をそのまま渡すと、ツールが呼出元の環境へ同期される。
        result = subprocess.run(
            [uv, "run", "--project", str(root), "--locked", "--no-default-groups", str(probe)],
            cwd=tmp_path,
            env=environment,
            input="{}",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        control = json.loads(result.stdout)
        assert Path(control["prefix"]) == caller
        assert not Path(control["prefix"]).is_relative_to(tmp_path / "runtime-data")
    print(json.dumps({"caller_setting": caller_setting, "hook": observed, "control": control}, ensure_ascii=False))
