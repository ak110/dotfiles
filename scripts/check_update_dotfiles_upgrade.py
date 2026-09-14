#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""約3日前のdotfilesから現行HEADへの実更新経路を隔離環境で検証する。"""

from __future__ import annotations

import argparse
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence

_BRANCH = "upgrade-check"
_AGE_HOURS = 72


class UpgradeCheckError(RuntimeError):
    """更新経路の検証が成立しなかった場合の例外。"""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _configure_standard_streams() -> None:
    """標準出力と標準エラーをUTF-8へ統一する。"""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _run(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """子プロセスを実行し、失敗時は診断出力を保った例外を送出する。"""
    command = [os.fspath(argument) for argument in arguments]
    try:
        result = runner(command, cwd=cwd, env=env, check=False, capture_output=True, text=True, encoding="utf-8")
    except OSError as error:
        raise UpgradeCheckError(f"子プロセスを起動できなかった: {command!r}: {error}") from error
    if result.returncode == 0:
        return result
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    raise UpgradeCheckError(f"子プロセスが終了コード{result.returncode}で失敗した: {command!r}")


def _git_value(repo: pathlib.Path, *arguments: str, runner: Runner = subprocess.run) -> str:
    """Gitの標準出力を取得し、空値を拒否する。"""
    value = _run(("git", "-C", repo, *arguments), runner=runner).stdout.strip()
    if not value:
        raise UpgradeCheckError(f"Gitが値を返さなかった: {arguments!r}")
    return value


def resolve_old_commit(repo: pathlib.Path, current_oid: str, *, runner: Runner = subprocess.run) -> str:
    """現行commitの時刻から72時間前以前で最も新しい祖先を返す。"""
    timestamp = int(_git_value(repo, "show", "-s", "--format=%ct", current_oid, runner=runner))
    cutoff = timestamp - (_AGE_HOURS * 60 * 60)
    return _git_value(repo, "rev-list", "-1", f"--before=@{cutoff}", current_oid, runner=runner)


def platform_entrypoint(repo: pathlib.Path, platform_name: str) -> list[str]:
    """旧checkout内で起動するプラットフォーム別の公開ランチャーを返す。"""
    if platform_name == "windows":
        return ["cmd.exe", "/d", "/c", str(repo / "bin" / "update-dotfiles.cmd")]
    if platform_name == "linux":
        return ["bash", str(repo / "bin" / "update-dotfiles")]
    raise UpgradeCheckError(f"未対応のプラットフォームである: {platform_name}")


def _isolated_env(home: pathlib.Path, uv_executable: pathlib.Path, platform_name: str) -> dict[str, str]:
    """利用者環境から状態を分離し、ランチャーが参照するuvを配置する。"""
    uv_name = "uv.exe" if platform_name == "windows" else "uv"
    uv_target = home / ".local" / "bin" / uv_name
    uv_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(uv_executable, uv_target)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    env["PATH"] = os.pathsep.join((str(uv_target.parent), env.get("PATH", "")))
    return env


def verify_updated_oid(actual_oid: str, expected_oid: str) -> None:
    """更新後の完全OIDが検証開始時の現行OIDと一致することを確認する。"""
    if actual_oid != expected_oid:
        raise UpgradeCheckError(f"更新後HEADが現行HEADと一致しない: expected={expected_oid} actual={actual_oid}")


def create_local_remote(
    source_repo: pathlib.Path,
    bare_repo: pathlib.Path,
    old_oid: str,
    *,
    runner: Runner = subprocess.run,
) -> None:
    """検証対象のobjectと旧commitを指す初期branchをbare remoteへ構成する。"""
    _run(("git", "clone", "--bare", source_repo, bare_repo), runner=runner)
    _run(("git", "--git-dir", bare_repo, "update-ref", f"refs/heads/{_BRANCH}", old_oid), runner=runner)


def run_upgrade_check(source_repo: pathlib.Path, platform_name: str, *, runner: Runner = subprocess.run) -> None:
    """ローカルremoteを用いて旧版から現行版への更新を終端まで検証する。"""
    current_oid = _git_value(source_repo, "rev-parse", "HEAD", runner=runner)
    old_oid = resolve_old_commit(source_repo, current_oid, runner=runner)
    if old_oid == current_oid:
        raise UpgradeCheckError("72時間前以前の別commitを取得できなかった。履歴の全量取得を確認する。")
    print(f"更新検証を開始する: old={old_oid} current={current_oid}")
    uv_path = shutil.which("uv")
    if uv_path is None:
        raise UpgradeCheckError("PATH上にuvが見つからない。")

    with tempfile.TemporaryDirectory(prefix="update-dotfiles-upgrade-") as temp_name:
        temp_root = pathlib.Path(temp_name)
        bare_repo = temp_root / "remote.git"
        home = temp_root / "home"
        checkout = home / "dotfiles"
        home.mkdir(parents=True)
        create_local_remote(source_repo, bare_repo, old_oid, runner=runner)
        _run(("git", "clone", "--branch", _BRANCH, bare_repo, checkout), runner=runner)
        env = _isolated_env(home, pathlib.Path(uv_path), platform_name)
        _run(("chezmoi", "init", f"--source={checkout}", "--apply"), cwd=checkout, env=env, runner=runner)
        _run(("git", "--git-dir", bare_repo, "update-ref", f"refs/heads/{_BRANCH}", current_oid), runner=runner)
        _run(platform_entrypoint(checkout, platform_name), cwd=checkout, env=env, runner=runner)
        actual_oid = _git_value(checkout, "rev-parse", "HEAD", runner=runner)
        verify_updated_oid(actual_oid, current_oid)
        print(f"旧版更新経路を確認した: {old_oid} -> {actual_oid}")


def main() -> int:
    """コマンドライン引数を解析して検証する。"""
    _configure_standard_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("linux", "windows"), required=True)
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
    args = parser.parse_args()
    try:
        run_upgrade_check(args.repo.resolve(), args.platform)
    except (OSError, ValueError, UpgradeCheckError) as error:
        print(f"update-dotfiles旧版更新検証に失敗した: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
