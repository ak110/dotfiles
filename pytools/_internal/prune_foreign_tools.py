r"""dotfilesのmiseが管理するツールと重複する、dotfiles外の導入物を導入元の手順で削除する。

同じ名前の実行ファイルがPATH上に複数あると、PATHの順序や起動経路によって別の版が動き、
dotfilesの更新や設定が反映されない。miseの管理版を実行できる名前に限り、次の導入元を削除する。

- WinGetのportable導入（`%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages`配下とそのLinks）: `winget uninstall --id <ID> --exact`
- npmのグローバル導入（miseのNodeとは別のprefix）: `npm uninstall --global --prefix <prefix> <パッケージ>`
- pnpmのstandalone導入（`PNPM_HOME`直下の`pnpm`）: pnpm公式の削除手順どおり`PNPM_HOME`を削除し、
  Windowsではユーザー環境変数`PNPM_HOME`とユーザーPATHの該当エントリーも除く

管理者権限が必要な導入（Program Filesなど）は自動では削除せず、Windowsでmiseの管理版より先にPATHへ現れる場合だけ削除を案内する。
WindowsはシステムのPATHがユーザーのPATHより前に並ぶため、この順序は対話シェルでも同じである。
Linuxは対話シェルでmiseを有効化してmiseの管理版を先頭に置くため、更新処理のPATHの順序では判定しない。
削除できなかった導入物は警告して次回の`update-dotfiles`で再試行する。
"""

import dataclasses
import json
import logging
import os
import re
import shutil
import stat
import sys
import typing
from pathlib import Path

from pytools._internal import (
    cleanup_user_path,
    common,
    log_format,
    post_apply_outcome,
    setup_cli_common,
    setup_mise_binary,
    winutils,
)

logger = logging.getLogger(__name__)

_TAG = "重複ツールの削除"
_PNPM_NAMES = frozenset({"pnpm", "pnpx"})
_WINGET_SOURCE_MARKER = "_Microsoft.Winget.Source_"


@dataclasses.dataclass(frozen=True)
class Layout:
    mise_installs: Path
    mise_shims: frozenset[Path]
    winget_packages: Path | None
    pnpm_home: Path
    excluded: frozenset[Path] = frozenset()


@dataclasses.dataclass(frozen=True)
class Removal:
    kind: str
    target: str
    location: Path
    manual_only: bool = False
    reason: str = ""
    unreadable: bool = False


def run() -> post_apply_outcome.PostApplyOutcome:
    mise = setup_mise_binary.find_mise_binary()
    if mise is None:
        logger.info(
            log_format.format_status(_TAG, "miseが未導入のため対象なし。mise セットアップ工程の結果を確認する"),
            extra=log_format.SHOW,
        )
        return post_apply_outcome.PostApplyOutcome()
    managed = _managed_names(mise)
    layout = _host_layout()
    path_entries = [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    removals: dict[tuple[str, str], tuple[Removal, str]] = {}
    warned: set[tuple[str, str, str]] = set()
    for name, found, shadows in _foreign_executables(managed, path_entries, layout):
        removal = classify(found, name, layout)
        if removal is None:
            if shadows and sys.platform == "win32":
                logger.warning(
                    log_format.format_status(
                        _TAG,
                        f"{found}がmise管理の{name}より先にPATHへあるため、mise管理版が使われない。"
                        "導入元のアンインストーラー（Windowsは「設定」の「アプリ」、Linuxはパッケージマネージャー）で削除する",
                    )
                )
            continue
        if removal.manual_only:
            # 旧npm版（reasonが空）の配置と正規の起動先は各CLIの専用工程が案内する。
            if removal.reason and (removal.kind, removal.target, str(removal.location)) not in warned:
                warned.add((removal.kind, removal.target, str(removal.location)))
                _warn_unconfirmed(removal, name, found)
            continue
        removals.setdefault((removal.kind, removal.target), (removal, name))
    changed = False
    removed_winget_dirs: list[Path] = []
    for removal, name in removals.values():
        if not _mise_resolves(mise, name):
            logger.warning(
                log_format.format_status(
                    _TAG,
                    f"miseが{name}を解決できないため保持: {removal.location}。"
                    "`mise install`の後に`update-dotfiles`を再実行する",
                )
            )
            continue
        if _remove(removal, mise):
            changed = True
            if removal.kind == "winget" and layout.winget_packages is not None:
                packages = common.safe_resolve(layout.winget_packages)
                relative = removal.location.relative_to(packages)
                removed_winget_dirs.append(packages / relative.parts[0])
    if removed_winget_dirs:
        try:
            cleanup_user_path.remove_winget_package_paths(removed_winget_dirs)
        except OSError as error:
            return post_apply_outcome.PostApplyOutcome(
                changed=changed,
                failure=f"削除済みWinGetパッケージのユーザーPATHを整理できない: {error}",
                next_action="ユーザーPATHの更新権限を確認してからupdate-dotfilesを再実行する",
            )
    return post_apply_outcome.PostApplyOutcome(changed=changed)


def classify(found: Path, name: str, layout: Layout) -> Removal | None:
    resolved = common.safe_resolve(found)
    if layout.winget_packages is not None:
        package_id = _winget_package_id(resolved, layout.winget_packages)
        if package_id is not None:
            return Removal("winget", package_id, resolved)
    if name in _PNPM_NAMES and _same(found.parent, layout.pnpm_home) and _has_pnpm_binary(layout.pnpm_home):
        # PNPM_HOMEはディレクトリごと削除するため、pnpm本体と付属物と確認できた項目だけを含む場合に限る。
        try:
            unknown = _unknown_pnpm_home_entries(layout.pnpm_home)
        except OSError as error:
            return Removal(
                "pnpm-standalone", str(layout.pnpm_home), layout.pnpm_home, manual_only=True, reason=str(error), unreadable=True
            )
        return Removal(
            "pnpm-standalone", str(layout.pnpm_home), layout.pnpm_home, manual_only=bool(unknown), reason="、".join(unknown)
        )
    return _npm_global_package(found, name)


def _warn_unconfirmed(removal: Removal, name: str, found: Path) -> None:
    if removal.unreadable:
        message = (
            f"standalone版pnpmの{removal.location}の内容を読めないため、このディレクトリを削除せず保持（{removal.reason}）。"
            "PATHでmise管理のpnpmより先に解決される間はこちらのpnpmが起動する。"
            "読取権限と、pnpmを使うプロセスが残っていないことを確認してから`update-dotfiles`を再実行する"
        )
    elif removal.kind == "pnpm-standalone":
        message = (
            f"standalone版pnpmの{removal.location}に、pnpm本体と付属物と確認できない項目（{removal.reason}）があるため、"
            "このディレクトリを削除せず保持。PATHでmise管理のpnpmより先に解決される間はこちらのpnpmが起動する。"
            "列挙した項目が不要と確認できたら別の場所へ移してから`update-dotfiles`を再実行すると、このディレクトリを削除する。"
            "旧npm版のcodex・claudeの起動ファイルは、codex・claudeの警告に従って扱う"
        )
    else:
        message = (
            f"{found}を、npmの導入先{removal.location}のパッケージ{removal.target}の起動ファイルと確認できないため"
            f"削除せず保持（{removal.reason}）。PATHでmise管理の{name}より先に解決される間はこちらが起動する。"
            "不要と確認できた場合だけ、自分で配置・編集したファイルを退避してから手動で削除する"
        )
    logger.warning(log_format.format_status(_TAG, message))


def _managed_names(mise: Path) -> set[str]:
    result = common.run_subprocess([str(mise), "bin-paths"], timeout=common.COMMAND_TIMEOUT, tag="mise", cwd=Path.home())
    if result is None or result.returncode != 0:
        logger.warning(
            log_format.format_status(
                _TAG,
                f"mise bin-pathsに失敗したため省略: {common.format_cli_error(result)}。"
                "`mise doctor`で状態を確認し、`update-dotfiles`を再実行する",
            )
        )
        return set()
    installs = _installs_root()
    names: set[str] = set()
    for line in result.stdout.splitlines():
        directory = Path(line.strip())
        if not line.strip() or not _is_within(directory, installs):
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        names.update(executable for entry in entries if (executable := _executable_name(entry)) is not None)
    return names


def _host_layout() -> Layout:
    local = os.environ.get("LOCALAPPDATA")
    windows = sys.platform == "win32"
    if os.environ.get("PNPM_HOME"):
        pnpm_home = Path(os.environ["PNPM_HOME"])
    elif windows and local:
        pnpm_home = Path(local) / "pnpm"
    else:
        pnpm_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pnpm"
    root = common.find_dotfiles_root()
    return Layout(
        mise_installs=_installs_root(),
        mise_shims=frozenset(common.mise_shim_directories()),
        winget_packages=Path(local) / "Microsoft" / "WinGet" / "Packages" if windows and local else None,
        pnpm_home=pnpm_home,
        excluded=frozenset({root / "bin"} if root is not None else ()),
    )


def _installs_root() -> Path:
    data_dir = os.environ.get("MISE_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "installs"
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "mise" / "installs"
    return Path.home() / ".local" / "share" / "mise" / "installs"


def _foreign_executables(managed: set[str], path_entries: list[Path], layout: Layout) -> list[tuple[str, Path, bool]]:
    shim_index = next(
        (index for index, entry in enumerate(path_entries) if common.safe_resolve(entry) in layout.mise_shims),
        len(path_entries),
    )
    found: list[tuple[str, Path, bool]] = []
    seen: set[Path] = set()
    for index, entry in enumerate(path_entries):
        directory = common.safe_resolve(entry)
        if (
            directory in seen
            or directory in layout.mise_shims
            or _is_within(directory, layout.mise_installs)
            or any(_same(directory, excluded) for excluded in layout.excluded)
        ):
            continue
        seen.add(directory)
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for item in entries:
            name = _executable_name(item)
            if name in managed:
                found.append((name, item, index < shim_index))
    return found


def _executable_name(path: Path) -> str | None:
    if sys.platform == "win32":
        extensions = {ext.lower() for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if ext}
        if path.suffix.lower() not in extensions:
            return None
        return path.stem.lower() if path.is_file() else None
    try:
        return path.name if path.is_file() and os.access(path, os.X_OK) else None
    except OSError:
        return None


def _winget_package_id(resolved: Path, packages: Path) -> str | None:
    root = common.safe_resolve(packages)
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return None
    if not relative.parts:
        return None
    folder = relative.parts[0]
    package_id, marker, _ = folder.partition(_WINGET_SOURCE_MARKER)
    return package_id if marker and package_id else None


def _has_pnpm_binary(home: Path) -> bool:
    return any((home / name).is_file() for name in ("pnpm.exe", "pnpm"))


# standalone版pnpmの`PNPM_HOME`に置かれる、pnpm本体と付属物の名前（小文字）。
# `pnpm add --global`の起動ファイルや`pnpm env`のNodeなど、これ以外の項目がある場合はディレクトリを削除しない。
_PNPM_HOME_ENTRIES = frozenset(
    {
        *(f"{name}{suffix}" for name in ("pnpm", "pnpx") for suffix in ("", ".exe", ".cmd", ".ps1")),
        "store",
        "global",
        "state",
        ".tools",
    }
)


_MAX_LAUNCHER_BYTES = 64 * 1024
_SHIM_PACKAGE = re.compile(r"node_modules/((?:@[^/\"'\s]+/)?[^/\"'\s]+)/")


def _unknown_pnpm_home_entries(home: Path) -> list[str]:
    """`PNPM_HOME`直下のうち、pnpm本体と付属物と確認できない項目の名前を返す。読めない場合はOSErrorを送出する。"""
    names = sorted(entry.name for entry in home.iterdir())
    return [name for name in names if name.lower() not in _PNPM_HOME_ENTRIES]


def _npm_global_root(found: Path) -> tuple[Path, Path] | None:
    """起動ファイルの所在を`bin`に持つnpmのprefixと、`npm --prefix <prefix>`が実際に使うglobal rootを返す。

    npmはPOSIXでは`<prefix>/bin`と`<prefix>/lib/node_modules`、Windowsでは`<prefix>`と`<prefix>/node_modules`を使う。
    削除の可否を確かめるrootと`npm uninstall --prefix`が操作するrootを一致させるため、この規則以外の配置は対象外とする。
    """
    directory = found.parent
    if sys.platform == "win32":
        return directory, directory / "node_modules"
    if directory.name == "bin":
        return directory.parent, directory.parent / "lib" / "node_modules"
    return None


def _launcher_package(found: Path, modules: Path) -> str | None:
    """起動ファイルが指すglobal root配下のパッケージのインストールキーを返す。確認できなければNoneを返す。

    POSIXのnpmはパッケージ内のファイルへのsymlinkを、Windowsのnpm（cmd-shim）はパッケージ内のファイル（`node_modules`配下のキーのディレクトリ）を
    参照するスクリプトを置く。
    """
    root = common.safe_resolve(modules)
    if found.is_symlink():
        try:
            parts = common.safe_resolve(found).relative_to(root).parts
        except ValueError:
            return None
        key = "/".join(parts[:2]) if parts and parts[0].startswith("@") else (parts[0] if parts else "")
    else:
        try:
            if found.stat().st_size > _MAX_LAUNCHER_BYTES:
                return None
            content = found.read_text(encoding="utf-8", errors="replace").replace("\\", "/")
        except OSError:
            return None
        matched = _SHIM_PACKAGE.search(content)
        if matched is None:
            return None
        key = matched.group(1)
    return key if key and (modules / key).is_dir() else None


def _npm_global_package(found: Path, name: str) -> Removal | None:
    located = _npm_global_root(found)
    if located is None:
        return None
    prefix, modules = located
    if not modules.is_dir() or _is_node_installation(prefix):
        return None
    declared: dict[str, dict[object, object]] = {}
    unreadable: list[str] = []
    for manifest in (*modules.glob("*/package.json"), *modules.glob("@*/*/package.json")):
        key = manifest.parent.relative_to(modules).as_posix()
        try:
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except OSError, ValueError:
            unreadable.append(key)
            continue
        if not isinstance(data, dict):
            unreadable.append(key)
            continue
        if name in {Path(bin_name.lower()).stem for bin_name in setup_cli_common.normalize_npm_package_bins(data)}:
            declared[key] = data
    owner = _launcher_package(found, modules)
    if any(setup_cli_common.protected_npm_package(key, data) for key, data in declared.items()) or (
        owner is not None and owner in setup_cli_common.LEGACY_NPM_CLIS
    ):
        return Removal("npm", owner or next(iter(declared)), prefix, manual_only=True)
    if owner is not None and owner in declared:
        return Removal("npm", owner, prefix)
    if owner is not None:
        return Removal("npm", owner, prefix, manual_only=True, reason=f"{owner}のパッケージ情報が{name}のbinを宣言していない")
    if declared or unreadable:
        target = next(iter(declared), None) or unreadable[0]
        return Removal("npm", target, prefix, manual_only=True, reason="起動ファイルが指すパッケージを確認できない")
    return None


def _is_node_installation(prefix: Path) -> bool:
    return any(path.is_file() for path in (prefix / "node.exe", prefix / "node", prefix / "bin" / "node"))


def _mise_resolves(mise: Path, name: str) -> bool:
    result = common.run_subprocess([str(mise), "which", name], timeout=common.COMMAND_TIMEOUT, tag="mise", cwd=Path.home())
    return result is not None and result.returncode == 0


def _remove(removal: Removal, mise: Path) -> bool:
    if removal.kind == "pnpm-standalone":
        return _remove_pnpm_standalone(removal.location)
    if removal.kind == "winget":
        command = [
            "winget",
            "uninstall",
            "--id",
            removal.target,
            "--exact",
            "--silent",
            "--disable-interactivity",
            "--accept-source-agreements",
        ]
    else:
        command = [str(mise), "exec", "--", "npm", "uninstall", "--global", "--prefix", str(removal.location), removal.target]
    result = common.run_subprocess(command, timeout=common.COMMAND_TIMEOUT, tag=_TAG, cwd=Path.home())
    if result is None or result.returncode != 0:
        logger.warning(
            log_format.format_status(
                _TAG,
                f"{removal.location}の削除に失敗: {common.format_cli_error(result)}。"
                "使用中のプロセスを終了してから`update-dotfiles`を再実行する",
            )
        )
        return False
    logger.info(log_format.format_status(_TAG, f"dotfiles外の導入物を削除しました: {removal.target}（{removal.location}）"))
    return True


def _remove_pnpm_standalone(home: Path) -> bool:
    """pnpm公式の削除手順どおり`PNPM_HOME`を削除し、Windowsではその環境変数とユーザーPATHのエントリーも除く。"""
    try:
        shutil.rmtree(home, onexc=_retry_writable)
    except OSError as error:
        logger.warning(
            log_format.format_status(
                _TAG, f"{home}を削除できない: {error}。pnpmを使うプロセスを終了してから`update-dotfiles`を再実行する"
            )
        )
        return False
    if _same(Path(os.environ.get("PNPM_HOME") or home), home):
        os.environ.pop("PNPM_HOME", None)
    if sys.platform == "win32":
        _remove_windows_pnpm_registration(home)
    logger.info(log_format.format_status(_TAG, f"dotfiles外で導入したpnpm（standalone）を削除しました: {home}"))
    return True


def _retry_writable(function: typing.Callable[[str], object], path: str, error: BaseException) -> None:
    """読み取り専用の属性で削除できなかったファイルは、書込可能にして再試行する。"""
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _remove_windows_pnpm_registration(home: Path) -> None:
    """ユーザー環境変数`PNPM_HOME`と、ユーザーPATHの`PNPM_HOME`を指すエントリーを除く。"""
    key = winutils.path_key(str(home))
    registered, _ = winutils.read_user_env_var("PNPM_HOME")
    if registered and winutils.path_key(registered) == key:
        winutils.delete_user_env_var("PNPM_HOME")
    user_path, reg_type = winutils.read_user_env_var("Path")
    if user_path:
        entries = [entry for entry in user_path.split(";") if entry]
        kept = [entry for entry in entries if entry.upper() != "%PNPM_HOME%" and winutils.path_key(entry) != key]
        if kept != entries:
            winutils.write_user_env_var("Path", ";".join(kept), reg_type)
    winutils.broadcast_environment_change()


def _same(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _is_within(path: Path, root: Path) -> bool:
    return common.safe_resolve(path).is_relative_to(common.safe_resolve(root))
