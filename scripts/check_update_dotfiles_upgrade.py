#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""約3日前のdotfilesから現行HEADへの実更新経路を隔離環境で検証する。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence

_BRANCH = "upgrade-check"
_AGE_HOURS = 72
_DIAGNOSTIC_TIMEOUT = 30


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
            "UV_TOOL_BIN_DIR": str(uv_target.parent),
            "UV_TOOL_DIR": str(home / ".local" / "share" / "uv" / "tools"),
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "PYTHONIOENCODING": "utf-8",
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


def _diagnostic_command(arguments: list[str], checkout: pathlib.Path, env: dict[str, str], runner: Runner) -> str:
    """診断の各出力と終了状態を区別し、採取失敗でも次の採取を続ける。"""
    print(f"診断コマンド: cwd={checkout} command={arguments!r}")
    try:
        result = runner(
            arguments,
            cwd=checkout,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_DIAGNOSTIC_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"診断採取失敗: {error!r}。元の検証失敗を保持して次の採取へ進む。", file=sys.stderr)
        if isinstance(error, subprocess.TimeoutExpired):
            print(f"診断stdout（時間超過）: {error.stdout!r}")
            print(f"診断stderr（時間超過）: {error.stderr!r}", file=sys.stderr)
        return ""
    print(f"診断終了コード: {result.returncode}")
    print(f"診断stdout: {result.stdout}")
    print(f"診断stderr: {result.stderr}", file=sys.stderr)
    return result.stdout if result.returncode == 0 else ""


def _diagnostic_hook(path: pathlib.Path) -> None:
    """hookの配置と内容ハッシュだけを採取する。"""
    try:
        exists = path.is_file()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if exists else None
        print(f"診断hook: path={path} is_file={exists} sha256={digest}")
    except OSError as error:
        print(f"診断hook採取失敗: path={path} error={error!r}。他の配置を採取する。", file=sys.stderr)


def _diagnostic_source(root: pathlib.Path, codex_home: pathlib.Path) -> None:
    """marketplaceのlocal sourceからsnapshotと検収パスを取得する。"""
    _diagnostic_hook(root / "agent-toolkit" / "agent_toolkit" / "hook.py")
    try:
        manifest = json.loads((root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        entry = next(item for item in manifest["plugins"] if item["name"] == "agent-toolkit")
        source = entry["source"]
        if source["source"] != "local":
            raise ValueError("local sourceではない")
        snapshot = (root / source["path"]).resolve()
        _diagnostic_hook(snapshot / "agent_toolkit" / "hook.py")
        plugin = json.loads((snapshot / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        expected = codex_home / "plugins" / "cache" / manifest["name"] / plugin["name"] / plugin["version"]
        print(f"診断Python検収先: {expected}")
        _diagnostic_hook(expected / "agent_toolkit" / "hook.py")
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        print(f"診断source採取失敗: root={root} error={error!r}。CLIメタデータとcache一覧を確認する。", file=sys.stderr)


def _collect_initial_apply_failure(
    checkout: pathlib.Path,
    env: dict[str, str],
    old_oid: str,
    current_oid: str,
    platform_name: str,
    runner: Runner,
) -> None:
    """検証homeの回収前に、初期適用失敗時のCodex配置を限定採取する。"""
    print(f"初期適用失敗の診断: old={old_oid} current={current_oid} platform={platform_name}")
    keys = ("HOME", "USERPROFILE", "CODEX_HOME", "CODEX_INSTALL_DIR", "APPDATA", "LOCALAPPDATA")
    print(f"診断環境: {json.dumps({key: env.get(key) for key in keys}, ensure_ascii=False)}")
    # post-applyが導入したtool環境を使い、同じ旧版の実行ファイル解決を観測する。
    tool_root = pathlib.Path(env["UV_TOOL_DIR"]) / "pytools"
    interpreter = tool_root / ("Scripts/python.exe" if platform_name == "windows" else "bin/python")
    probe = (
        "import json, pathlib, sys; from pytools._internal import claude_common, install_codex_plugins; "
        "print(json.dumps({'python': sys.version, 'home': str(pathlib.Path.home()), "
        "'codex': str(claude_common.resolve_executable('codex')), "
        "'codex_home': str(install_codex_plugins._codex_home())}))"
    )
    output = _diagnostic_command([str(interpreter), "-c", probe], checkout, env, runner)
    codex_home = pathlib.Path(env.get("CODEX_HOME", str(pathlib.Path(env["HOME"]) / ".codex")))
    roots = {checkout}
    homes = {codex_home, pathlib.Path(env["HOME"]) / ".codex", pathlib.Path(env["USERPROFILE"]) / ".codex"}
    if platform_name == "windows":
        powershell = shutil.which("powershell.exe", path=env["PATH"])
        if powershell is None:
            print("診断OS profile採取失敗: powershell.exeが無い。取得済みhome候補の採取を続ける。", file=sys.stderr)
        else:
            command = (
                "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
                "[Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)"
            )
            profile = _diagnostic_command(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                checkout,
                env,
                runner,
            ).strip()
            if profile and pathlib.Path(profile).is_absolute():
                print(f"診断OS profile: {profile}")
                homes.add(pathlib.Path(profile) / ".codex")
            else:
                print("診断OS profile採取失敗: 絶対パスを取得できない。取得済みhome候補の採取を続ける。", file=sys.stderr)
    try:
        metadata = json.loads(output)
        codex_home = pathlib.Path(metadata["codex_home"])
        homes.update((codex_home, pathlib.Path(metadata["home"]) / ".codex"))
        if metadata["codex"] != "None":
            codex = metadata["codex"]
            _diagnostic_command([codex, "--version"], checkout, env, runner)
            _diagnostic_command([codex, "plugin", "list", "--json"], checkout, env, runner)
            marketplaces = _diagnostic_command([codex, "plugin", "marketplace", "list", "--json"], checkout, env, runner)
            for item in json.loads(marketplaces)["marketplaces"]:
                if item.get("name") == "ak110-dotfiles":
                    roots.add(pathlib.Path(item["root"]))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"診断メタデータ採取失敗: {error!r}。取得済み環境値から配置を採取する。", file=sys.stderr)
    for root in sorted(roots):
        _diagnostic_source(root, codex_home)
    for home in sorted(homes):
        cache = home / "plugins" / "cache"
        plugin_cache = cache / "ak110-dotfiles" / "agent-toolkit"
        try:
            exists = plugin_cache.is_dir()
            versions = sorted(path for path in plugin_cache.iterdir() if path.is_dir()) if exists else []
            print(
                f"診断plugin cache: root={plugin_cache} cache_exists={cache.is_dir()} exists={exists} "
                f"versions={json.dumps([path.name for path in versions], ensure_ascii=False)}"
            )
            for version in versions:
                _diagnostic_hook(version / "agent_toolkit" / "hook.py")
        except OSError as error:
            print(f"診断cache採取失敗: root={cache} error={error!r}。元の検証失敗を報告する。", file=sys.stderr)


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
        try:
            _run(("chezmoi", "init", f"--source={checkout}", "--apply"), cwd=checkout, env=env, runner=runner)
        except UpgradeCheckError:
            try:
                _collect_initial_apply_failure(checkout, env, old_oid, current_oid, platform_name, runner)
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                print(f"初期適用診断の採取失敗: {error!r}。元の検証失敗を報告する。", file=sys.stderr)
            raise
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
