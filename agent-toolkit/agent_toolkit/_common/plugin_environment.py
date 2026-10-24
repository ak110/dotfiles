"""配布物へ書き込まず、CLI・hook・MCPが同じ版の依存環境を使う起動条件を解決する。

依存導入前にscripts/launch.pyが使うため、標準ライブラリだけに依存する。
環境の指定はuv子プロセスに限り、Bashや対象リポジトリのuvへ永続化しない。
"""

import hashlib
import json
import os
import platform
import shlex
import sys
import sysconfig
import tomllib
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from agent_toolkit._common import inherited_venv
from agent_toolkit._common.shell_tokens import atk_launcher_name

RUNTIME_DATA_ENV = "AGENT_TOOLKIT_RUNTIME_DATA"
RUNTIME_ENVIRONMENT_ENV = inherited_venv.PLUGIN_RUNTIME_ENVIRONMENT_KEY
_READY_FILE = ".agent-toolkit-dependencies-ready"


def runtime_data(environment: Mapping[str, str]) -> Path:
    """明示指定を優先し、次にOSのユーザーデータ領域、書き込めない場合はホストのplugin dataを使う。

    端末・Claude Code・Codexと導入後のウォームアップが同じ版の環境を共用できるよう、
    ホストごとに異なるplugin dataはユーザーデータ領域へ書き込めない場合だけ使う。
    """
    if value := environment.get(RUNTIME_DATA_ENV):
        return Path(value).expanduser().resolve()
    home = Path(environment.get("USERPROFILE" if sys.platform == "win32" else "HOME") or Path.home())
    if sys.platform == "win32":
        base = Path(environment.get("LOCALAPPDATA", str(home / "AppData/Local")))
    elif sys.platform == "darwin":
        base = home / "Library/Application Support"
    else:
        base = Path(environment.get("XDG_DATA_HOME", str(home / ".local/share")))
    candidates = [base / "agent-toolkit"]
    candidates.extend(Path(value) for name in ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA") if (value := environment.get(name)))
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if _writable_directory(resolved):
            return resolved
    return candidates[0].expanduser().resolve()


def _writable_directory(path: Path) -> bool:
    """ディレクトリを作成でき、書き込み可能なら真を返す。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK)


def project_environment(root: Path, environment: Mapping[str, str]) -> Path:
    """依存解決とPython実行条件が一致する実行だけが共有する環境を返す。"""
    root = root.resolve()
    payload = {
        "dependency_project": _dependency_project(root / "pyproject.toml"),
        "lock": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "python": {
            "implementation": sys.implementation.name,
            "version": list(sys.version_info),
            "implementation_version": list(sys.implementation.version),
            "abi": sysconfig.get_config_var("SOABI"),
        },
        "platform": {"os": sys.platform, "machine": platform.machine()},
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded)
    return runtime_data(environment) / "environments" / digest.hexdigest()


def _dependency_project(path: Path) -> dict[str, object]:
    """依存導入に影響するpyprojectだけを、JSON化できる形で取り出す。"""
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    project = parsed.get("project", {})
    build_system = parsed.get("build-system", {})
    tool = parsed.get("tool", {})
    return {
        "project": {
            key: project[key] for key in ("requires-python", "dependencies", "optional-dependencies") if key in project
        },
        "dependency-groups": parsed.get("dependency-groups", {}),
        "build-system": {key: build_system[key] for key in ("requires", "build-backend") if key in build_system},
        "uv": tool.get("uv", {}) if isinstance(tool, dict) else {},
    }


def launch_environment(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """準備・直接起動の子プロセス環境を組み立てる。"""
    result = dict(environment)
    inherited_venv.strip_inherited_venv(result)
    caller = dict(result)
    if "UV_PROJECT_ENVIRONMENT" in result:
        result[inherited_venv.CALLER_PROJECT_ENVIRONMENT_KEY] = result["UV_PROJECT_ENVIRONMENT"]
    selected = str(project_environment(root, environment))
    result[RUNTIME_DATA_ENV] = str(runtime_data(environment))
    result[RUNTIME_ENVIRONMENT_ENV] = selected
    result["UV_PROJECT_ENVIRONMENT"] = selected
    inherited_venv.record_launch_changes(result, caller)
    return result


def python_path(environment: Path) -> Path:
    """共有依存環境のPython実行ファイルを返す。"""
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepared_python(root: Path, environment: Mapping[str, str]) -> Path | None:
    """完了を公開済みの共有環境のPythonだけを返す。"""
    selected = project_environment(root, environment)
    executable = python_path(selected)
    if (selected / _READY_FILE).is_file() and executable.is_file():
        return executable
    return None


def direct_environment(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """rootのコードを共有依存環境のPythonで解決する環境を返す。"""
    result = launch_environment(root, environment)
    caller = dict(result)
    inherited_venv.strip_inherited_venv(caller)
    root_text = str(root.resolve())
    previous = result.get("PYTHONPATH")
    result["PYTHONPATH"] = root_text + (os.pathsep + previous if previous else "")
    inherited_venv.record_launch_changes(result, caller)
    return result


@contextmanager
def prepare_lock(environment: Path) -> Generator[None]:
    """OSファイルロックで同一共有環境のprepareを直列化する。"""
    lock = environment.with_name(environment.name + ".prepare-lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        _lock_file(stream)
        try:
            yield
        finally:
            _unlock_file(stream)


def _lock_file(stream: BinaryIO) -> None:
    """POSIXとWindowsでプロセス終了時にも解放される排他ロックを取得する。"""
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock_file(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def publish_prepared_environment(root: Path, environment: Mapping[str, str]) -> Path:
    """最終パスの同期完了をready markerの原子的配置で公開する。"""
    target = project_environment(root, environment)
    temporary = target / f"{_READY_FILE}.tmp-{os.getpid()}"
    temporary.write_text("ready\n", encoding="utf-8")
    temporary.replace(target / _READY_FILE)
    return target


def launch_command(root: Path, target: Path) -> list[str]:
    """配布root内のスクリプトを、配布root外の依存環境で起動するargvを返す。

    `uv run --project <root>`を直接案内すると配布rootへ仮想環境が作成されるため、案内文もこの形を使う。
    """
    launcher = root.resolve() / "scripts" / "launch.py"
    return ["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python", str(launcher), str(target)]


def cli_environment(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """委譲先が保持したrootのCLIを名前で呼べるよう、PATHとデータ領域の環境変数を加えた環境を返す。"""
    result = dict(environment)
    scripts = str(root.resolve() / "scripts")
    path = result.get("PATH", "")
    if scripts not in path.split(os.pathsep):
        result["PATH"] = scripts + (os.pathsep + path if path else "")
    result[RUNTIME_DATA_ENV] = str(runtime_data(environment))
    return result


def persist_claude_environment(root: Path, environment: Mapping[str, str]) -> None:
    """提供されたBash環境ファイルへ追記し、別hookの設定と後続のPATHを保持する。"""
    if not (value := environment.get("CLAUDE_ENV_FILE")):
        return
    scripts = shlex.quote(str(root.resolve() / "scripts"))
    data = shlex.quote(str(runtime_data(environment)))
    with Path(value).open("a", encoding="utf-8", newline="\n") as output:
        output.write(f'\nexport PATH={scripts}:"$PATH"\nexport {RUNTIME_DATA_ENV}={data}\n')


def cli_instructions(root: Path, *, windows: bool | None = None) -> str:
    """直接起動と委譲の文脈へ、同じrootのCLIとホストに合う呼び方を示す。"""
    is_windows = os.name == "nt" if windows is None else windows
    entry = root / "scripts" / atk_launcher_name(windows=is_windows)
    command = "& '" + str(entry).replace("'", "''") + "'" if is_windows else shlex.quote(str(entry))
    return (
        f"agent-toolkitのCLI: {entry}\n"
        f"起動部分: {command}\n"
        "この起動部分へサブコマンドと引数を続ける。CLIはこの配布物と、その版の依存環境を使う。"
    )
