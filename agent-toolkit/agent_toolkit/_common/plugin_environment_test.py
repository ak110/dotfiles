"""ホストの保存先と版別依存環境を、配布rootへ書かずに解決する契約を検証する。"""

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

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


def test_dependency_environments_separate_roots_locks_and_versions(tmp_path: Path) -> None:
    """同じrootと版では同じ環境を返し、rootの違い、lockfileの更新、配布版の更新ではそれぞれ別の環境を返す。"""
    root = _root(tmp_path / "first")
    other = _root(tmp_path / "second")
    environment = {subject.RUNTIME_DATA_ENV: str(tmp_path / "data")}
    first = subject.project_environment(root, environment)
    assert first == subject.project_environment(root, environment)
    assert first != subject.project_environment(other, environment)
    (root / "uv.lock").write_text("changed lock", encoding="utf-8")
    locked = subject.project_environment(root, environment)
    assert locked != first
    (root / ".claude-plugin/plugin.json").write_text('{"version":"2.0.0"}', encoding="utf-8")
    assert subject.project_environment(root, environment) not in {first, locked}
    assert not first.exists()
    assert first.is_relative_to(tmp_path / "data")


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
