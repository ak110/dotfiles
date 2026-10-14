"""配布物へ書き込まず、CLI・hook・MCPが同じ版の依存環境を使う起動条件を解決する。

依存導入前にscripts/launch.pyが使うため、標準ライブラリだけに依存する。
環境の指定はuv子プロセスに限り、Bashや対象リポジトリのuvへ永続化しない。
"""

import hashlib
import os
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path

from agent_toolkit._common import inherited_venv

RUNTIME_DATA_ENV = "AGENT_TOOLKIT_RUNTIME_DATA"
RUNTIME_ENVIRONMENT_ENV = inherited_venv.PLUGIN_RUNTIME_ENVIRONMENT_KEY


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
    """root・依存宣言・lockfile・配布版が一致する実行だけが共有する環境を返す。"""
    root = root.resolve()
    digest = hashlib.sha256(os.fsencode(root))
    for relative in ("pyproject.toml", "uv.lock", ".claude-plugin/plugin.json"):
        digest.update(relative.encode("utf-8"))
        digest.update((root / relative).read_bytes())
    return runtime_data(environment) / "environments" / digest.hexdigest()


def launch_environment(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """uv子プロセスの環境を組み立て、呼出元の環境とPATHを保持する。"""
    result = dict(environment)
    inherited_venv.strip_inherited_venv(result)
    selected = str(project_environment(root, environment))
    result[RUNTIME_DATA_ENV] = str(runtime_data(environment))
    result[RUNTIME_ENVIRONMENT_ENV] = selected
    result["UV_PROJECT_ENVIRONMENT"] = selected
    return result


def launch_command(root: Path, target: Path) -> list[str]:
    """配布root内のスクリプトを、配布root外の依存環境で起動するargvを返す。

    `uv run --project <root>`を直接案内すると配布rootへ仮想環境が作成されるため、案内文もこの形を使う。
    """
    launcher = root.resolve() / "scripts" / "launch.py"
    return ["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python", str(launcher), str(target)]


def cli_environment(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """委譲先が保持済みrootのCLIを名前で呼べるPATHとdataを渡す。"""
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
    entry = root / "scripts" / ("atk.cmd" if is_windows else "atk")
    command = "& '" + str(entry).replace("'", "''") + "'" if is_windows else shlex.quote(str(entry))
    return (
        f"agent-toolkitのCLI: {entry}\n"
        f"起動部分: {command}\n"
        "この起動部分へサブコマンドと引数を続ける。CLIはこの配布物と、その版の依存環境を使う。"
    )
