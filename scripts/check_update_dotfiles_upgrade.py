#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""約3日前のdotfilesから現行HEADへの実更新経路を隔離環境で検証する。"""

from __future__ import annotations

import argparse
import importlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import typing
from collections.abc import Callable, Mapping, Sequence

_BRANCH = "upgrade-check"
_AGE_HOURS = 72


class UpgradeCheckError(RuntimeError):
    """更新経路の検証が成立しなかった場合の例外。"""


Runner = Callable[..., subprocess.CompletedProcess[str]]
RegistryValues = dict[str, tuple[object, int]]
"""値の名前から、値と値型の組への対応。"""
ProfileSnapshot = dict[pathlib.Path, frozenset[str] | None]
"""監視ディレクトリから直下の項目名の集合への対応。ディレクトリが無い場合は`None`とする。"""


class UserEnvironmentRegistry(typing.Protocol):
    """ユーザー環境変数を保持するレジストリキーの読み書き。テストは偽の実装を渡す。"""

    def read(self) -> RegistryValues:
        """全ての値を返す。"""
        ...

    def write(self, name: str, value: object, kind: int) -> None:
        """値を値型とともに書き込む。"""
        ...

    def delete(self, name: str) -> None:
        """値を削除する。"""
        ...


def _import_winreg() -> typing.Any:
    """`winreg`を`typing.Any`として読み込む。

    `winreg`はWindowsにだけ存在し、Linux上の型チェッカーが属性を解決できないため、`importlib`経由で読み込む。
    """
    return importlib.import_module("winreg")


class WindowsUserEnvironment:
    r"""`HKCU\Environment`を標準ライブラリ`winreg`で読み書きする。

    Windowsのユーザー環境変数はこのキーへ保存され、環境変数を付け替えても書込先は変わらない。
    `winreg`はWindowsにだけ存在するため、使う時点で読み込む。
    """

    _KEY = "Environment"

    def read(self) -> RegistryValues:
        """全ての値を返す。"""
        winreg = _import_winreg()
        values: RegistryValues = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._KEY, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                values[name] = (value, kind)
                index += 1
        return values

    def write(self, name: str, value: object, kind: int) -> None:
        """値を値型とともに書き込む。"""
        winreg = _import_winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, kind, value)

    def delete(self, name: str) -> None:
        """値を削除する。"""
        winreg = _import_winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)


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
    """ユーザー環境から状態を分離し、ランチャーが参照するuvを配置する。"""
    uv_name = "uv.exe" if platform_name == "windows" else "uv"
    uv_target = home / ".local" / "bin" / uv_name
    uv_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(uv_executable, uv_target)
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    # Windowsのinstallerは可視binのdirectory全体をjunctionへ置き換えるため、uvの共有binと分ける。
    # 専用binはinstallerが作成するため、ここでは作成しない。
    codex_bin = home / ".local" / "share" / "codex" / "bin" if platform_name == "windows" else uv_target.parent
    env = os.environ.copy()
    # 可視binの上書きを継承すると、installerが検証home外のjunctionを作成・更新する。
    env.pop("HERDR_INSTALL_DIR", None)
    visible_bins = [str(codex_bin), str(uv_target.parent)]
    if platform_name == "windows":
        # Windowsの消費側は可視binをAppData配下へ置くため、HOMEと同じ検証homeへそろえる。
        # 通常profileのAppDataを継承すると、検証homeの回収後も通常profileに検証用の配置が残る。
        local_app_data = home / "AppData" / "Local"
        roaming_app_data = home / "AppData" / "Roaming"
        local_app_data.mkdir(parents=True, exist_ok=True)
        roaming_app_data.mkdir(parents=True, exist_ok=True)
        env["LOCALAPPDATA"] = str(local_app_data)
        env["APPDATA"] = str(roaming_app_data)
        visible_bins.append(str(local_app_data / "Programs" / "Herdr" / "bin"))
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "HERDR_HOME": str(home / ".herdr"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "UV_TOOL_BIN_DIR": str(uv_target.parent),
            "UV_TOOL_DIR": str(home / ".local" / "share" / "uv" / "tools"),
            "CODEX_HOME": str(codex_home),
            "CODEX_INSTALL_DIR": str(codex_bin),
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "PYTHONIOENCODING": "utf-8",
        }
    )
    env["PATH"] = os.pathsep.join((*dict.fromkeys(visible_bins), env.get("PATH", "")))
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


def profile_watch_dirs(profile_env: Mapping[str, str], platform_name: str) -> list[pathlib.Path]:
    """検証を起動したプロセスの環境変数が指す通常profile側の監視ディレクトリを返す。

    過去に隔離が漏れた書込先（uv toolの配置、Codexの状態root、Herdrの可視bin）の親を対象とする。
    """
    home_value = profile_env.get("USERPROFILE" if platform_name == "windows" else "HOME")
    dirs: list[pathlib.Path] = []
    if home_value:
        home = pathlib.Path(home_value)
        dirs.extend((home, home / ".local" / "bin", home / ".local" / "share"))
    if platform_name == "windows":
        if local_app_data := profile_env.get("LOCALAPPDATA"):
            dirs.extend((pathlib.Path(local_app_data), pathlib.Path(local_app_data) / "Programs"))
        if app_data := profile_env.get("APPDATA"):
            dirs.append(pathlib.Path(app_data))
    return list(dict.fromkeys(dirs))


def snapshot_profile(dirs: Sequence[pathlib.Path]) -> ProfileSnapshot:
    """各監視ディレクトリの直下の項目名を記録する。

    比較は項目名だけとし、既存ファイルの内容と更新時刻は比べない。
    検証の外の主体が既存ファイルを更新しても、検証の失敗として扱わないためである。
    """
    return {
        directory: frozenset(entry.name for entry in directory.iterdir()) if directory.is_dir() else None for directory in dirs
    }


def profile_changes(before: ProfileSnapshot, after: ProfileSnapshot) -> list[pathlib.Path]:
    """検証の前後で追加または削除された項目と、作成または削除された監視ディレクトリのパスを返す。"""
    changes: list[pathlib.Path] = []
    for directory, names_before in before.items():
        names_after = after.get(directory)
        if (names_before is None) != (names_after is None):
            changes.append(directory)
            continue
        changes.extend(directory / name for name in sorted((names_before or frozenset()) ^ (names_after or frozenset())))
    return changes


def restore_registry(registry: UserEnvironmentRegistry, saved: RegistryValues) -> list[str]:
    """レジストリの値を退避した状態へ戻し、戻した値の名前を返す。

    検証中に追加された値は削除し、変更または削除された値は退避した値と値型で書き戻す。
    """
    current = registry.read()
    restored: list[str] = []
    for name in sorted(current.keys() - saved.keys()):
        registry.delete(name)
        restored.append(name)
    for name, (value, kind) in sorted(saved.items()):
        if current.get(name) != (value, kind):
            registry.write(name, value, kind)
            restored.append(name)
    return sorted(restored)


def run_upgrade_check(
    source_repo: pathlib.Path,
    platform_name: str,
    *,
    runner: Runner = subprocess.run,
    registry: UserEnvironmentRegistry | None = None,
    profile_env: Mapping[str, str] | None = None,
) -> None:
    r"""ローカルremoteを用いて旧版から現行版への更新を終端まで検証する。

    検証はユーザーの永続状態を前後で変えないことも成功条件とする。
    Windowsでは`HKCU\\Environment`を検証前の状態へ戻し、両プラットフォームで通常profile側の監視ディレクトリの
    直下の項目名を前後で比べる。差分があれば、隔離の漏れとして検証の時点で失敗させる。
    """
    current_oid = _git_value(source_repo, "rev-parse", "HEAD", runner=runner)
    old_oid = resolve_old_commit(source_repo, current_oid, runner=runner)
    if old_oid == current_oid:
        raise UpgradeCheckError("72時間前以前の別commitを取得できなかった。履歴の全量取得を確認する。")
    print(f"更新検証を開始する: old={old_oid} current={current_oid}")
    uv_path = shutil.which("uv")
    if uv_path is None:
        raise UpgradeCheckError("PATH上にuvが見つからない。")

    if platform_name == "windows" and registry is None:
        registry = WindowsUserEnvironment()
    elif platform_name != "windows":
        registry = None
    watch_dirs = profile_watch_dirs(os.environ if profile_env is None else profile_env, platform_name)
    profile_before = snapshot_profile(watch_dirs)
    saved_registry = registry.read() if registry is not None else None
    try:
        _run_isolated_upgrade(source_repo, platform_name, old_oid, current_oid, pathlib.Path(uv_path), runner=runner)
    except BaseException:
        # 検証自体の失敗を優先して返すため、差分は元の例外を保ったまま標準エラーへ書く。
        leaks = _restore_and_compare(registry, saved_registry, watch_dirs, profile_before)
        if leaks:
            print(_leak_message(leaks), file=sys.stderr)
        raise
    leaks = _restore_and_compare(registry, saved_registry, watch_dirs, profile_before)
    if leaks:
        raise UpgradeCheckError(_leak_message(leaks))


def _restore_and_compare(
    registry: UserEnvironmentRegistry | None,
    saved_registry: RegistryValues | None,
    watch_dirs: Sequence[pathlib.Path],
    profile_before: ProfileSnapshot,
) -> list[pathlib.Path]:
    """レジストリを復元して戻した名前を出力し、通常profileの差分を返す。"""
    if registry is not None and saved_registry is not None:
        for name in restore_registry(registry, saved_registry):
            print(f"検証前の値へ戻したユーザー環境変数: {name}")
    return profile_changes(profile_before, snapshot_profile(watch_dirs))


def _leak_message(leaks: Sequence[pathlib.Path]) -> str:
    """通常profileの差分を報告する文を返す。"""
    paths = "\n".join(f"  {path}" for path in leaks)
    return (
        "通常profile側の次の項目が検証の前後で追加または削除された:\n"
        f"{paths}\n"
        "書込主体として次の2つを確認する。\n"
        "  (1) 検証が起動した処理: どの環境変数から書込先を決めたかを調べ、`_isolated_env`で検証homeへ向ける。\n"
        "  (2) 検証と無関係なランナー側の常駐プロセスや予約タスク: 同じ実行者で動くため`_isolated_env`では解消しない。"
        "`.github/workflows/ci.yaml`の`test-windows`などで、検証より前に発生源を止める。"
    )


def _run_isolated_upgrade(
    source_repo: pathlib.Path,
    platform_name: str,
    old_oid: str,
    current_oid: str,
    uv_path: pathlib.Path,
    *,
    runner: Runner,
) -> None:
    """検証homeで旧版を初期適用し、現行版への更新を実行して更新後HEADを確かめる。"""
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
