#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""約3日前のdotfilesから現行HEADへの実更新経路を隔離環境で検証する。"""

from __future__ import annotations

import argparse
import importlib
import io
import os
import pathlib
import re
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
        raise UpgradeCheckError(
            f"子プロセスを起動できなかった: {command!r}: {error}。"
            "先頭の実行ファイルの導入状況・PATHと作業ディレクトリの権限を修正して検証を再実行する。"
        ) from error
    if result.returncode == 0:
        return result
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    raise UpgradeCheckError(
        f"子プロセスが終了コード{result.returncode}で失敗した: {command!r}。"
        "直前の標準出力・標準エラーにあるGit・依存導入・chezmoi適用の問題を解消して検証を再実行する。"
    )


def _git_value(repo: pathlib.Path, *arguments: str, runner: Runner = subprocess.run) -> str:
    """Gitの標準出力を取得し、空値を拒否する。"""
    value = _run(("git", "-C", repo, *arguments), runner=runner).stdout.strip()
    if not value:
        raise UpgradeCheckError(
            f"Gitが値を返さなかった: {arguments!r}。git logで対象commitと履歴を確認し、"
            "浅いcloneならgit fetch --unshallowで履歴を取得して検証を再実行する。"
        )
    return value


def resolve_old_commit(repo: pathlib.Path, current_oid: str, *, runner: Runner = subprocess.run) -> str:
    """現行commitの時刻から72時間前以前で最も新しい祖先を返す。"""
    timestamp = int(_git_value(repo, "show", "-s", "--format=%ct", current_oid, runner=runner))
    cutoff = timestamp - (_AGE_HOURS * 60 * 60)
    return _git_value(repo, "rev-list", "-1", f"--before=@{cutoff}", current_oid, runner=runner)


def old_python_pin(repo: pathlib.Path, old_oid: str, *, runner: Runner = subprocess.run) -> str | None:
    """旧commitの`.python-version`が指定するPythonを返す。旧commitが持たない場合は`None`を返す。"""
    listed = _run(("git", "-C", repo, "ls-tree", "--name-only", old_oid, "--", ".python-version"), runner=runner)
    if not listed.stdout.strip():
        return None
    return _git_value(repo, "show", f"{old_oid}:.python-version", runner=runner)


def platform_entrypoint(repo: pathlib.Path, platform_name: str) -> list[str]:
    """旧checkout内で起動するプラットフォーム別の公開ランチャーを返す。"""
    if platform_name == "windows":
        return ["cmd.exe", "/d", "/c", str(repo / "bin" / "update-dotfiles.cmd")]
    if platform_name == "linux":
        return ["bash", str(repo / "bin" / "update-dotfiles")]
    raise UpgradeCheckError(f"未対応のプラットフォームである: {platform_name}")


def _parent_mise_data_dirs(environ: Mapping[str, str], platform_name: str) -> list[pathlib.Path]:
    """親プロセスの環境変数からmiseのデータディレクトリの候補を返す。

    順序と既定値はmise公式文書のディレクトリ表（https://mise.jdx.dev/directories.html ）に合わせる。
    """
    if mise_data_dir := environ.get("MISE_DATA_DIR"):
        dirs = [pathlib.Path(mise_data_dir)]
    elif xdg_data_home := environ.get("XDG_DATA_HOME"):
        dirs = [pathlib.Path(xdg_data_home) / "mise"]
    else:
        dirs = [_parent_home(environ, platform_name) / ".local" / "share" / "mise"]
    if platform_name == "windows" and (local_app_data := environ.get("LOCALAPPDATA")):
        dirs.append(pathlib.Path(local_app_data) / "mise")
    return dirs


def _without_mise_entries(path_value: str, mise_dirs: Sequence[pathlib.Path]) -> str:
    """`PATH`の値から、miseのデータディレクトリの`shims`と`installs`配下の要素を除く。"""
    excluded = [
        os.path.normcase(os.path.abspath(directory / name)) for directory in mise_dirs for name in ("shims", "installs")
    ]

    def is_excluded(entry: str) -> bool:
        normalized = os.path.normcase(os.path.abspath(entry))
        return any(normalized == base or normalized.startswith(base + os.sep) for base in excluded)

    return os.pathsep.join(entry for entry in path_value.split(os.pathsep) if not (entry and is_excluded(entry)))


def _parent_home(environ: Mapping[str, str], platform_name: str) -> pathlib.Path:
    """差し替える前の親プロセスの実ホームを返す。"""
    home_value = environ.get("USERPROFILE" if platform_name == "windows" else "HOME")
    return pathlib.Path(home_value) if home_value else pathlib.Path.home()


def _without_entries_under(path_value: str, base: pathlib.Path, kept: Sequence[pathlib.Path] = ()) -> str:
    """`PATH`の値から、`base`配下（`base`自身を含む）の要素を除く。`kept`と一致する要素は残す。"""
    excluded = os.path.normcase(os.path.abspath(base))
    kept_entries = {os.path.normcase(os.path.abspath(path)) for path in kept}

    def is_excluded(entry: str) -> bool:
        normalized = os.path.normcase(os.path.abspath(entry))
        if normalized in kept_entries:
            return False
        return normalized == excluded or normalized.startswith(excluded.rstrip(os.sep) + os.sep)

    return os.pathsep.join(entry for entry in path_value.split(os.pathsep) if not (entry and is_excluded(entry)))


def _isolated_env(
    home: pathlib.Path, uv_executable: pathlib.Path, platform_name: str, verification_root: pathlib.Path
) -> dict[str, str]:
    """ユーザー環境から状態を分離し、ランチャーが参照するuvとchezmoiを配置する。

    検証homeの中の処理は、実ホームのユーザー設定とツール配置を読み書きせず、
    一時ディレクトリの置き場所や実ホームの中身によらず同じ結果になる必要がある。
    そのため、実ホームを探索起点・参照先とする経路（祖先探索、親から引き継ぐ`PATH`、配置先を示す環境変数）を遮断する。

    `XDG_DATA_HOME`の差し替えで、子の中のmiseは親が導入したツールを認識しない。
    親のmiseのshimやツールを子が名前で起動すると、shimと検証homeの実行ファイルの間でexecが往復して止まらないため、
    子へ渡す`PATH`から親のmiseのデータディレクトリ配下を除く。
    """
    uv_name = "uv.exe" if platform_name == "windows" else "uv"
    uv_target = home / ".local" / "bin" / uv_name
    uv_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(uv_executable, uv_target)
    # 更新処理は`uvx`も名前で起動する。実ホーム配下を`PATH`から外すと見つからなくなるため、uv本体と同じ配布物の`uvx`も複製する。
    uvx_name = "uvx.exe" if platform_name == "windows" else "uvx"
    if (uvx_source := uv_executable.with_name(uvx_name)).is_file():
        shutil.copy2(uvx_source, uv_target.parent / uvx_name)
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    # Windowsのinstallerは可視binのdirectory全体をjunctionへ置き換えるため、uvの共有binと分ける。
    # 専用binはinstallerが作成するため、ここでは作成しない。
    codex_bin = home / ".local" / "share" / "codex" / "bin" if platform_name == "windows" else uv_target.parent
    env = os.environ.copy()
    # 差し替える前の親の環境変数から、親のmiseのデータディレクトリと実ホームを求める。
    mise_dirs = _parent_mise_data_dirs(env, platform_name)
    parent_home = _parent_home(env, platform_name)
    # 更新処理はWindowsでmise本体の導入にwingetを名前で起動する。wingetなどのapp execution aliasは実ホームの
    # `%LOCALAPPDATA%\Microsoft\WindowsApps`にあり、OSが管理する起動口でユーザーのツール配置ではないため、
    # 実ホーム配下を`PATH`から除く対象から外す。
    kept_path_entries = (
        [pathlib.Path(parent_local_app_data) / "Microsoft" / "WindowsApps"]
        if platform_name == "windows" and (parent_local_app_data := env.get("LOCALAPPDATA"))
        else []
    )
    # 可視binの上書きを継承すると、installerが検証home外のjunctionを作成・更新する。
    env.pop("HERDR_INSTALL_DIR", None)
    # 親の値（実ホームの`~/.nvm`）を継承すると、更新処理のランチャー整理が実ホームのnvmのbinを走査し、
    # 条件次第では実ホームのnpmでグローバルパッケージを削除する。外すと消費側は検証homeの`~/.nvm`を見る。
    env.pop("NVM_DIR", None)
    # 親が全体導入先を指定していると、検証homeのnpmも実ホーム配下へ導入する。
    for name in ("NPM_CONFIG_PREFIX", "npm_config_prefix"):
        env.pop(name, None)
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
        # Windowsのnpmは全体導入先の直下へランチャーを置く。親の導入先を外すと既定の`%APPDATA%\npm`（検証home内）になるが、
        # 通常のユーザー環境と異なり`PATH`に無いため、導入したツールを名前で起動できない。
        # 導入先を明示して可視binへ加え、Nodeの導入時にユーザーの`PATH`へ加わる通常の配置を再現する。
        npm_prefix = roaming_app_data / "npm"
        env["NPM_CONFIG_PREFIX"] = str(npm_prefix)
        visible_bins.append(str(npm_prefix))
    else:
        # npmの全体導入先は、実ホーム配下のNode（nvmなど）を`PATH`から外すとシステムのNodeの`/usr`などになり、
        # 書き込めずに導入が失敗する。実ホームのNodeのままでは実ホームへ導入する。検証homeの可視binへ導入先を向ける。
        env["NPM_CONFIG_PREFIX"] = str(home / ".local")
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
            # miseは作業ディレクトリの祖先から設定を探す。一時ディレクトリが実ホーム配下にあると、
            # 祖先の`~/.config/mise/config.toml`を未信頼のプロジェクト設定として読み終了コード1になるため、
            # 探索を検証rootで止める。グローバル設定は`XDG_CONFIG_HOME`経由で検証home内を読むため影響しない。
            "MISE_CEILING_PATHS": str(verification_root),
        }
    )
    parent_path = _without_mise_entries(env.get("PATH", ""), mise_dirs)
    # 検証自身が名前で起動するchezmoiは実ホームの`~/.local/bin`に導入されることが多いため、
    # 実ホーム配下を`PATH`から外す前に、uvと同じく検証homeへ複製する。
    # 解決にmise配下を除いた`PATH`を使うのは、shimを複製すると検証homeとの間でexecが往復するためである。
    chezmoi = shutil.which("chezmoi", path=parent_path)
    if chezmoi is None:
        raise UpgradeCheckError(
            "chezmoiが見つからない（親のmise配下を除いた`PATH`で解決した）。"
            "chezmoiを導入し、`PATH`から起動できることを確かめて検証を再実行する。"
            "miseでだけ導入している場合は、chezmoiの実体があるディレクトリを`PATH`へ加えて再実行する。"
            "CIでは`.github/actions/install-chezmoi`の導入ステップが成功し、導入先が`PATH`にあることを確かめる。"
        )
    shutil.copy2(chezmoi, uv_target.parent / pathlib.Path(chezmoi).name)
    # 実ホームの`~/.local/bin`や`~/.nvm/...`を残すと、更新処理が実ホームのツール配置を走査する。
    parent_path = _without_entries_under(parent_path, parent_home, kept_path_entries)
    env["PATH"] = os.pathsep.join((*dict.fromkeys(visible_bins), parent_path))
    return env


def verify_updated_oid(actual_oid: str, expected_oid: str) -> None:
    """更新後の完全OIDが検証開始時の現行OIDと一致することを確認する。"""
    if actual_oid != expected_oid:
        raise UpgradeCheckError(
            f"更新後HEADが現行HEADと一致しない: expected={expected_oid} actual={actual_oid}。"
            "成功として扱わず、git pull工程のログと両commit IDを添えてdotfilesの作者へ報告する。"
        )


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
    r"""ローカルremoteを用いて旧版から現行版への更新を終了まで検証する。

    検証はユーザーの永続状態を前後で変えないことも成功条件とする。
    Windowsでは`HKCU\\Environment`を検証前の状態へ戻し、両プラットフォームで通常profile側の監視ディレクトリの
    直下の項目名を前後で比べる。差分があれば、隔離の漏れとして検証の時点で失敗させる。
    """
    # 名前解決は親のmiseのshimを返し得るため、このスクリプトを起動したuv本体を環境変数`UV`から取る。
    # uvは0.6.0以降、起動した全ての子プロセスへ`UV`を渡す（https://docs.astral.sh/uv/reference/environment/ ）。
    uv_path = os.environ.get("UV")
    if not uv_path:
        raise UpgradeCheckError("環境変数UVが無い。uv run --script scripts/check_update_dotfiles_upgrade.py で起動し直す。")
    current_oid = _git_value(source_repo, "rev-parse", "HEAD", runner=runner)
    old_oid = resolve_old_commit(source_repo, current_oid, runner=runner)
    if old_oid == current_oid:
        raise UpgradeCheckError(
            "72時間前以前の別commitを取得できなかった。浅いcloneならgit fetch --unshallowで履歴を取得し、"
            "git log --onelineで対象より72時間以上前の祖先があることを確認して検証を再実行する。"
        )
    print(f"更新検証を開始する: old={old_oid} current={current_oid}")

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
        "`.github/workflows/ci.yaml`の`update-dotfiles-upgrade-windows`などで、検証より前に発生源を止める。"
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
        env = _isolated_env(home, pathlib.Path(uv_path), platform_name, temp_root)
        # 検証を起動したuvが導入したPython（現行の`.python-version`の版）は検証homeからも選択できる。
        # 版を固定しない旧版のuv環境構築はその中で最新の版を選ぶため、旧版のlockがwheelを持たない新しい版で
        # 旧版を構築してしまう。旧版を使っていた時点の状態を再現するため、初期適用だけ旧版の基準版へ固定する。
        initial_env = dict(env)
        if (pin := old_python_pin(source_repo, old_oid, runner=runner)) is not None:
            initial_env["UV_PYTHON"] = pin
        _run(("chezmoi", "init", f"--source={checkout}", "--apply"), cwd=checkout, env=initial_env, runner=runner)
        _run(("git", "--git-dir", bare_repo, "update-ref", f"refs/heads/{_BRANCH}", current_oid), runner=runner)
        # 初期適用した旧版の警告は、現行版の更新の判定から分離する。
        for log in home.rglob("update-dotfiles.log*"):
            if log.is_file():
                log.unlink()
        updated = _run(platform_entrypoint(checkout, platform_name), cwd=checkout, env=env, runner=runner)
        verify_update_logs(home, stdout=updated.stdout, stderr=updated.stderr)
        actual_oid = _git_value(checkout, "rev-parse", "HEAD", runner=runner)
        verify_updated_oid(actual_oid, current_oid)
        print(f"旧版更新経路を確認した: {old_oid} -> {actual_oid}")


_ANTIGRAVITY_UPDATED_NOTICE = "Antigravityのプラグインを更新しました。新しいagyセッションで反映を確認してください。"
"""`pytools/_internal/antigravity_plugin.py`の`UPDATED_NOTICE`と同じ文言。

PEP 723スクリプトからpytoolsをimportできないため、文言の一致は試験で確かめる。
"""

_UV_HARDLINK_FALLBACK = "warning: Failed to hardlink files; falling back to full copy. This may lead to degraded performance."
"""uvがhardlinkできず複製へ切り替えたときの診断。導入は完了しており、更新の失敗ではない。"""

_MISSING_PATH_ENTRY = re.compile(r"PATH 整理: ユーザー PATH に存在しないエントリーを検出: \S.* \(展開: (?P<expanded>.+?)\)。")

_MISE_GCLOUD_REGRESSION = "module 'python' not found"
"""mise 2026.10.21の同梱vfox-gcloudがLuaモジュール`python`を読めない回帰の診断（jdx/mise#15034、修正は2026.10.22）。

更新処理はmiseを自己更新し、2026.10.21が選ばれる間は検証homeでgcloudの導入とpruneが必ず失敗する。
miseの出力がgcloudのこの回帰だけを示す警告に限り許容する。自己更新が2026.10.22以降を選ぶようになったら撤去する。
"""

_MISE_FAILED_TOOLS = re.compile(r"Failed to install tools: (?P<tools>.+)$", re.MULTILINE)


def _is_mise_gcloud_regression(message: str, detail: str) -> bool:
    """miseの導入・pruneの失敗警告で、続く出力がgcloudの上流回帰だけを示すかを返す。"""
    if _MISE_GCLOUD_REGRESSION not in detail:
        return False
    if message.startswith("mise: `install` に失敗"):
        failed = _MISE_FAILED_TOOLS.search(detail)
        return failed is not None and all(
            tool.strip().startswith("vfox:jdx/vfox-gcloud@") for tool in failed["tools"].split(",")
        )
    if message.startswith("mise: `prune -y` に失敗"):
        return "failed to resolve gcloud " in detail
    return False


def _is_expected_log_warning(message: str, home: pathlib.Path, detail: str = "") -> bool:
    """正常な更新でも出る警告と、検証の隔離環境が作り出す警告だけを個別に許容する。

    `detail`は同じログレコードの2行目以降（警告に添えた外部コマンドの出力）である。
    許容しない警告は全て検証失敗とし、警告一般の検出は保つ。
    """
    if _is_mise_gcloud_regression(message, detail):
        return True
    # 旧版から現行版への更新では配布物の版が必ず変わるため、dotfilesユーザー向けの更新案内が毎回出る。
    if message == _ANTIGRAVITY_UPDATED_NOTICE:
        return True
    # 隔離homeへUSERPROFILE等を差し替えると、ランナー本来のユーザーPATHの`%USERPROFILE%`などを含む項目が
    # 隔離home配下へ展開され、実在しない項目として報告される。展開先が隔離home配下の項目だけを許容する。
    if (match := _MISSING_PATH_ENTRY.match(message)) is not None:
        expanded = os.path.normcase(os.path.normpath(match["expanded"]))
        root = os.path.normcase(os.path.normpath(home))
        return expanded == root or expanded.startswith(root + os.sep)
    return False


def verify_update_logs(home: pathlib.Path, *, stdout: str = "", stderr: str = "") -> None:
    """隔離homeの永続ログから、更新が警告として継続した失敗も検出する。"""
    diagnostics: list[str] = []
    logs = [path for path in sorted(home.rglob("update-dotfiles.log*")) if path.is_file()]
    if not logs:
        raise UpgradeCheckError("更新処理の永続ログが作成されず、警告以上の有無を確認できない")
    for path in logs:
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if (match := re.search(r" run=\S+ (?P<level>WARNING|ERROR|CRITICAL) (?P<message>.*)$", line)) is None:
                continue
            # 同じレコードの続き（次のレコードの行の手前まで）を、警告に添えた出力として判定へ渡す。
            detail_lines: list[str] = []
            for following in lines[index + 1 :]:
                if re.search(r" run=\S+ [A-Z]+ ", following):
                    break
                detail_lines.append(following)
            detail = "\n".join(detail_lines)
            if match["level"] == "WARNING" and _is_expected_log_warning(match["message"].strip(), home, detail):
                continue
            diagnostics.append(f"{path}: {line}")
    # severityを明示するCLIの診断だけを拾い、進捗や説明中の単語を誤検出しない。
    for stream, output in (("stdout", stdout), ("stderr", stderr)):
        for line in output.splitlines():
            if line.strip() == _UV_HARDLINK_FALLBACK:
                continue
            if re.match(r"\s*(?:warning|error|critical):\s|\s*\[(?:WARNING|ERROR|CRITICAL)\]\s", line, re.IGNORECASE):
                diagnostics.append(f"{stream}: {line}")
    if diagnostics:
        raise UpgradeCheckError("更新処理が警告以上を記録した:\n" + "\n".join(diagnostics))


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
        print(
            f"update-dotfiles旧版更新検証に失敗した: {error}。上記の対処を行い、"
            f'uv run --script scripts/check_update_dotfiles_upgrade.py --platform {args.platform} --repo "{args.repo}"'
            "を再実行してください。解消しない場合はこの失敗ログをdotfilesの作者へ報告してください。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
