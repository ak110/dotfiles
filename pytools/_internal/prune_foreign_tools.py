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

import contextlib
import dataclasses
import json
import logging
import os
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
    """導入元の判定に使う配置。試験では一時ディレクトリへ差し替える。"""

    mise_installs: Path
    mise_shims: frozenset[Path]
    winget_packages: Path | None
    pnpm_home: Path
    excluded: frozenset[Path] = frozenset()


@dataclasses.dataclass(frozen=True)
class Removal:
    """1つの導入物の削除操作。`kind`は`winget`・`npm`・`pnpm-standalone`のいずれか。"""

    kind: str
    target: str
    location: Path


def run() -> post_apply_outcome.PostApplyOutcome:
    """mise管理版と重複するdotfiles外の導入物を削除する。"""
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
        removals.setdefault((removal.kind, removal.target), (removal, name))
    changed = False
    removed_winget_dirs: list[Path] = []
    for removal, name in removals.values():
        # 削除後に同じ名前のコマンドが無くならないよう、miseが同じ名前を解決できる場合だけ削除する。
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
    """PATH上の実行ファイルの導入元を判定し、自動で削除できる場合は削除操作を返す。"""
    resolved = common.safe_resolve(found)
    if layout.winget_packages is not None:
        package_id = _winget_package_id(resolved, layout.winget_packages)
        if package_id is not None:
            return Removal("winget", package_id, resolved)
    if name in _PNPM_NAMES and _same(found.parent, layout.pnpm_home) and _has_pnpm_binary(layout.pnpm_home):
        return Removal("pnpm-standalone", str(layout.pnpm_home), layout.pnpm_home)
    package = _npm_global_package(found, name)
    if package is not None:
        prefix, package_name = package
        return Removal("npm", package_name, prefix)
    return None


def _managed_names(mise: Path) -> set[str]:
    """miseが導入した実行ファイルの名前（拡張子なし、小文字）を返す。共有ランタイムへのリンクは除く。"""
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
    """miseの導入先（`<データディレクトリ>/installs`）を返す。"""
    data_dir = os.environ.get("MISE_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "installs"
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "mise" / "installs"
    return Path.home() / ".local" / "share" / "mise" / "installs"


def _foreign_executables(managed: set[str], path_entries: list[Path], layout: Layout) -> list[tuple[str, Path, bool]]:
    """PATH上でmise管理外にある同名の実行ファイルと、mise shimより先に現れるかを返す。"""
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
    """実行ファイルなら拡張子を除いた小文字の名前を返す。"""
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
    """WinGetのportable導入の配下ならパッケージIDを返す（Linksのリンクは解決済みの実体で判定する）。"""
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


def _npm_global_package(found: Path, name: str) -> tuple[Path, str] | None:
    """npmのグローバルprefixのbinにある実行ファイルなら、prefixと所有パッケージ名を返す。

    Node.js本体の導入先（Program Filesの`nodejs`や`/usr`など）は同梱のnpmを含むが、
    npmのグローバルprefixではなく本体のインストーラーが管理するため対象外とする。
    """
    directory = found.parent
    candidates = [(directory, directory / "node_modules")]
    if directory.name == "bin":
        candidates.append((directory.parent, directory.parent / "lib" / "node_modules"))
    for prefix, modules in candidates:
        if not modules.is_dir() or _is_node_installation(prefix):
            continue
        for manifest in (*modules.glob("*/package.json"), *modules.glob("@*/*/package.json")):
            if name in _package_bins(manifest):
                return prefix, json.loads(manifest.read_text(encoding="utf-8")).get("name") or manifest.parent.name
    return None


def _is_node_installation(prefix: Path) -> bool:
    return any(path.is_file() for path in (prefix / "node.exe", prefix / "node", prefix / "bin" / "node"))


def _package_bins(manifest: Path) -> set[str]:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return set()
    if not isinstance(data, dict):
        return set()
    bins = data.get("bin")
    if isinstance(bins, str) and isinstance(data.get("name"), str):
        return {data["name"].rsplit("/", 1)[-1].lower()}
    if isinstance(bins, dict):
        return {str(key).lower() for key in bins}
    return set()


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
    with setup_cli_common.NPM_GLOBAL_LOCK if removal.kind == "npm" else contextlib.nullcontext():
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
