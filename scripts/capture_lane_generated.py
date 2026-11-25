from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.lane_cleanup import PROJECTION_MANIFEST
from check_function_hooks_types import _bundled_types

_DESTINATIONS = {
    "hypothesis": ".hypothesis",
    "statusline": "rust/claude-statusline/target",
    "claude-types": "agent-toolkit/.claude-plugin/types",
}


def _produce(kind: str, worktree: pathlib.Path, output: pathlib.Path, arguments: list[str]) -> int:
    env = os.environ.copy()
    if kind == "hypothesis":
        env["HYPOTHESIS_STORAGE_DIRECTORY"] = str(output)
        argv = [sys.executable, "-m", "pytest", *arguments]
    elif kind == "statusline":
        if arguments:
            raise ValueError("statuslineは追加引数を取らない")
        env["CARGO_TARGET_DIR"] = str(output)
        argv = [
            "mise",
            "exec",
            "--",
            "cargo",
            "test",
            "--manifest-path",
            _DESTINATIONS[kind].removesuffix("/target") + "/Cargo.toml",
            "--locked",
        ]
    else:
        if arguments:
            raise ValueError("claude-typesは追加引数を取らない")
        bundled, version = _bundled_types()
        if not bundled:
            raise ValueError(f"対象Claude Codeの同梱型がない: {version}。plugin-authoringを起動してから再実行する")
        shutil.copyfile(bundled[0], output / "claude-code.d.ts")
        return 0
    return subprocess.run(argv, cwd=worktree, env=env, check=False).returncode


def _publish(output: pathlib.Path, target: pathlib.Path) -> None:
    files: dict[str, str] = {}
    directories: list[pathlib.Path] = []
    for path in sorted(output.rglob("*")):
        relative = path.relative_to(output)
        if path.is_symlink() or path.is_junction() or ".git" in {part.casefold() for part in relative.parts}:
            raise ValueError(f"所有を確かめられない生成出力: {relative}")
        if path.is_dir():
            directories.append(path)
            continue
        if not path.is_file() or relative == PROJECTION_MANIFEST:
            raise ValueError(f"所有を確かめられない生成出力: {relative}")
        with path.open("rb") as stream:
            files[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    if not files:
        return
    for path in reversed(directories):
        if next(path.iterdir(), None) is None:
            path.rmdir()
    (output / PROJECTION_MANIFEST).write_text(
        json.dumps({"root": target.name, "files": files}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    shutil.copytree(output, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成元を隔離して実行し、レーンで回収できる出力を記録する。")
    parser.add_argument("kind", choices=sorted(_DESTINATIONS))
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="hypothesisのpytestに渡す引数。")
    args = parser.parse_args(argv)
    try:
        worktree = pathlib.Path.cwd().resolve()
        target = worktree / _DESTINATIONS[args.kind]
        if not target.parent.is_dir() or target.parent.resolve() != target.parent:
            raise ValueError(f"生成先の親はリポジトリ内の実ディレクトリを指定する: {target.parent}")
        if os.path.lexists(target):
            raise ValueError(f"既存の出力を保持した: {target}。別のレーンか出力先を空にした後に再実行する")
        root = managed_temp.temp_root_path()
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root, prefix="lane-generated-") as temporary:
            output = pathlib.Path(temporary) / target.name
            output.mkdir()
            code = _produce(args.kind, worktree, output, args.arguments)
            if code:
                return code
            _publish(output, target)
        return 0
    except (OSError, ValueError) as error:
        print(f"生成物を記録できない: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
