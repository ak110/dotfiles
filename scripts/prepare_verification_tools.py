"""統合候補の検証が選んだmiseツールを用意し、同じ環境で検証する。pyfltr: allow-comment"""

import argparse
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

from agent_toolkit._common.process_tree import run_with_timeout

from pytools._internal.common import format_cli_error

_TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]*")
_BINARY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _run(
    argv: list[str], cwd: pathlib.Path, environment: dict[str, str], *, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    return run_with_timeout(
        argv,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _resolve(mise: str, tool: str, binary: str, cwd: pathlib.Path, environment: dict[str, str]) -> pathlib.Path | None:
    resolved = _run([mise, "which", binary], cwd, environment)
    listing = _run([mise, "ls", "--current", "--json", tool], cwd, environment)
    if listing.returncode != 0:
        raise ValueError(
            f"{tool}の導入状態を照会できない: {format_cli_error(listing)}\nmise which: {format_cli_error(resolved)}"
        )
    rows = json.loads(listing.stdout)
    if not isinstance(rows, list):
        raise ValueError(f"{tool}のmise lsが配列を返さなかった")
    # --currentには未導入の版も含まれる。その版はactive=falseでも候補に残す。  # pyfltr: allow-comment
    candidates = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(source := row.get("source"), dict)
        and isinstance(source_path := source.get("path"), str)
        and pathlib.Path(source_path) == cwd / "mise.toml"
    ]
    if len(candidates) != 1:
        raise ValueError(f"{tool}の現在の版を1件に確定できない: mise which: {format_cli_error(resolved)}")
    candidate = candidates[0]
    install_path = candidate.get("install_path")
    if not isinstance(install_path, str) or not pathlib.Path(install_path).is_absolute():
        raise ValueError(f"{tool}の導入先をこの候補のmise.tomlから確定できない")
    if candidate.get("installed") is False:
        return None
    if candidate.get("installed") is not True or resolved.returncode != 0:
        raise ValueError(f"{tool}は導入済みだが{binary}を解決できない: {format_cli_error(resolved)}")
    executable = pathlib.Path(resolved.stdout.strip())
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or not executable.resolve().is_relative_to(pathlib.Path(install_path).resolve())
    ):
        raise ValueError(f"{tool}の導入先とmise which {binary}が一致しない: {format_cli_error(resolved)}")
    return executable


def _prepare(specs: list[tuple[str, str]], cwd: pathlib.Path, environment: dict[str, str]) -> None:
    environment.update(MISE_LOCKED="1", MISE_LOCKED_SCOPES="project", MISE_AUTO_INSTALL="false")
    if not (cwd / "mise.lock").is_file():
        raise ValueError("追随後のmise.lockが無い")
    if (mise := shutil.which("mise", path=environment.get("PATH"))) is None:
        raise ValueError("検証と同じPATHにmiseが無い")
    resolved = {spec: _resolve(mise, *spec, cwd, environment) for spec in specs}
    missing = list(dict.fromkeys(tool for (tool, _), path in resolved.items() if path is None))
    if missing:
        install = _run([mise, "install", *missing], cwd, environment, timeout=600)
        if install.returncode != 0:
            raise ValueError(f"{', '.join(missing)}をロック付きで導入できない: {format_cli_error(install)}")
        for tool, binary in specs:
            resolved[tool, binary] = _resolve(mise, tool, binary, cwd, environment)

    executable_paths: list[tuple[str, pathlib.Path]] = []
    for (tool, binary), path in resolved.items():
        if path is None:
            raise ValueError(f"{tool}は導入後も{binary}を解決できない")
        executable_paths.append((binary, path))
    directories = list(dict.fromkeys(str(path.parent) for _, path in executable_paths))
    environment["PATH"] = os.pathsep.join([*directories, environment.get("PATH", os.defpath)])

    for binary, expected in executable_paths:
        chosen = shutil.which(binary, path=environment["PATH"])
        if chosen is None or pathlib.Path(chosen).resolve() != expected.resolve():
            raise ValueError(f"検証時のPATHが選ぶ{binary}がmiseの選択と異なる: {chosen} / {expected}")
        probe = _run([chosen, "--version"], cwd, environment)
        if probe.returncode != 0:
            raise ValueError(f"{binary}の起動を確認できない ({chosen}): {format_cli_error(probe)}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", action="append", nargs=2, metavar=("MISE名", "実行名"), default=[])
    parser.add_argument("verification", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    verification = args.verification
    if len(verification) < 2 or verification[0] != "--":
        parser.error("-- の後に検証コマンドを指定する")
    specs: list[tuple[str, str]] = []
    for tool, binary in args.tool:
        if not _TOOL_NAME.fullmatch(tool) or not _BINARY.fullmatch(binary):
            parser.error("--toolはmiseの設定名とPATHで探す実行名を指定する")
        specs.append((tool, binary))
    cwd = pathlib.Path.cwd()
    environment = dict(os.environ)
    command = verification[1:]
    try:
        if specs:
            _prepare(specs, cwd, environment)
            # Windowsではsubprocessに渡す環境のPATHは親が実行ファイルを探す際に使われない。  # pyfltr: allow-comment
            executable = shutil.which(command[0], path=environment["PATH"])
            if executable is None:
                raise ValueError(f"検証の実行ファイルがPATHに無い: {command[0]}")
            command[0] = str(pathlib.Path(executable).absolute())
        code = subprocess.run(command, cwd=cwd, env=environment, check=False).returncode  # noqa: S603
        return code if code >= 0 else 128 - code
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"統合候補の検証 ({cwd}): {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
