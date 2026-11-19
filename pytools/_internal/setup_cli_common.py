"""公式インストーラーを利用するCLI導入処理の共通機能。"""

import contextlib
import dataclasses
import enum
import json
import locale
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable
from pathlib import Path, PureWindowsPath

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import httpx
lazy import psutil

from pytools._internal import common, log_format

logger = logging.getLogger(__name__)

# post-applyは互いの成果を使わない工程を並列に実行する。プロセス全体のPATHを読んで書き戻す処理
# （本モジュールの`prepend_path`とWindowsの`setup_mise`のshims追加）は、同時に実行すると
# 一方の追加が失われるため、このロックで直列化する。
PROCESS_PATH_LOCK = threading.Lock()
# 複数のCLI工程が同じnpmのグローバル領域を同時に変更しないよう、旧npm版の削除を直列化する。
NPM_GLOBAL_LOCK = threading.Lock()

_MAX_NPM_LAUNCHER_BYTES = 64 * 1024
"""npmの起動スクリプト（数百バイト）より十分大きい上限。超えるファイルは実行ファイル本体などとみなして読まない。"""


def installer_ssl_verify(*, windows: bool) -> ssl.SSLContext | bool:
    """`SSL_CERT_FILE`か`SSL_CERT_DIR`で指定したCAを優先し、指定がないWindowsではOSの信頼済みCAを使う。"""
    explicit_ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR")
    return ssl.create_default_context() if windows and not explicit_ca else True


def find_powershell() -> str:
    """利用できるPowerShell 7またはWindows PowerShellを返す。"""
    for name in ("pwsh", "powershell"):
        executable = shutil.which(name)
        if executable is not None:
            return executable
    raise RuntimeError("公式インストーラーを実行できるPowerShellが見つからない")


def run_official_installer(
    client: httpx.Client | None,
    *,
    posix_url: str,
    windows_url: str,
    tag: str,
    timeout: float,
    http_timeout: float = 30.0,
    env_overrides: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str] | None, str]:
    """公式インストーラーを取得して実行し、結果と失敗の理由を返す。

    取得も実行もCLIごとに同じ手順であり、異なるのは取得先URLと記録用のタグだけである。
    失敗の扱い（例外にするか警告に留めるか）は呼び出し側で決める。
    """
    windows = sys.platform == "win32"
    temp_path: Path | None = None
    with contextlib.ExitStack() as resources:
        try:
            if client is None:
                # 明示CAはHTTPXの環境変数処理へ渡し、それ以外のWindowsではOSの信頼済みCAを使う。
                active_client = resources.enter_context(
                    httpx.Client(timeout=http_timeout, follow_redirects=True, verify=installer_ssl_verify(windows=windows))
                )
            else:
                active_client = client
            response = active_client.get(windows_url if windows else posix_url)
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".ps1" if windows else ".sh", delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(response.content)
            if windows:
                powershell = find_powershell()
                command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(temp_path)]
                # Windows PowerShell 5.1の出力はロケールのコードページで受け取る。
                output_encoding = (
                    locale.getpreferredencoding(False) if Path(powershell).stem.lower() == "powershell" else "utf-8"
                )
            else:
                command = ["bash", str(temp_path)]
                output_encoding = "utf-8"
            return common.run_subprocess(
                command, timeout=timeout, tag=tag, env_overrides=env_overrides, encoding=output_encoding
            ), ""
        except (httpx.HTTPError, OSError) as error:
            reason = f"公式インストーラーの取得に失敗: {error}"
            if windows and "CERTIFICATE_VERIFY_FAILED" in str(error):
                reason += "。Windowsの信頼済みルート証明書またはSSL_CERT_FILE/SSL_CERT_DIRを確認してください"
            return None, reason
        finally:
            if temp_path is not None:
                with contextlib.suppress(OSError):
                    temp_path.unlink()


def prepend_path(path: Path) -> None:
    """ディレクトリを現プロセスのPATH先頭へ重複なく追加する。"""
    value = str(path)
    normalized = os.path.normcase(os.path.abspath(value))
    with PROCESS_PATH_LOCK:
        entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
        remaining = [entry for entry in entries if os.path.normcase(os.path.abspath(entry)) != normalized]
        os.environ["PATH"] = os.pathsep.join([value, *remaining])


def migrate_npm_launchers(
    cli_name: str,
    package_name: str,
    canonical_launcher: Path,
    canonical_prefix: Path,
    *,
    extra_search_directories: Iterable[Path] = (),
    excluded_launchers: Iterable[Path] = (),
) -> bool:
    """PATHと追加の配置先にある帰属確認済み非正規npmパッケージを削除する。

    `canonical_launcher`はユーザーが使う正規の起動先で、移行の候補から除き、移行を延期した場合に起動先として案内する。
    `excluded_launchers`には呼出側自身が配置した起動管理を渡し、移行の候補と保持警告から除く。
    npmの導入先を照会できないパッケージと、Windowsでパッケージから起動したプロセスがあるパッケージは、その回は削除しない。
    """
    changed = _remove_orphan_npm_launchers(cli_name, package_name, extra_search_directories)
    for launcher in _iter_noncanonical_launchers(
        cli_name,
        canonical_prefix,
        extra_search_directories=extra_search_directories,
        excluded_launchers=(canonical_launcher, *excluded_launchers),
    ):
        try:
            owner = _owning_npm_package(package_name, launcher)
        except _NpmQueryError as error:
            owner = (
                f"npmの導入先を照会できないため旧npm版の移行を見送り: {launcher}（{error}）。"
                "npmを修復してから`update-dotfiles`を再実行する"
            )
        if isinstance(owner, str):
            logger.warning(log_format.format_status(cli_name, owner))
            continue
        npm, package_dir = owner
        if running := find_windows_cli_processes(cli_name, packages=(package_dir,)):
            logger.warning(
                log_format.format_status(
                    cli_name,
                    f"旧npm版の移行を次回へ延期: {package_dir}。{describe_processes(running, '旧npm版')}。"
                    "表示したプロセスが旧npm版なら終了してから、"
                    f'`"{npm}" uninstall --global {package_name}`で旧npm版を削除する。'
                    f"削除するまでは`{canonical_launcher}`で更新済みの版を起動する",
                )
            )
            continue
        with NPM_GLOBAL_LOCK:
            result = common.run_subprocess(
                [str(npm), "uninstall", "--global", package_name],
                timeout=common.COMMAND_TIMEOUT,
                tag=cli_name,
            )
        if result is not None and result.returncode == 0:
            changed = True
        else:
            message = (
                f'旧npm版の削除に失敗: {common.format_cli_error(result)}。`"{npm}" uninstall --global {package_name}`を実行する'
            )
            logger.warning(log_format.format_status(cli_name, message))
            raise RuntimeError(message)
    return changed


class Evidence(enum.Enum):
    """プロセスを書き換える配置からの起動と判定した根拠。

    値は判定をユーザーへ示す文の書式で、PID（`pid`）、読めたパス（`path`）、書き換える版の名前（`target`）を埋める。
    """

    STARTED = "PID {pid}（{path}）で{target}を実行中"
    EXE_UNREADABLE = "PID {pid}（{path}）は実行ファイルの所在を読めず、{target}の実行を否定できない"
    CMDLINE_UNREADABLE = "PID {pid}（{path}）は起動引数を読めず、{target}の実行を否定できない"
    NAMED_ONLY = "PID {pid}（{path}）は名前で起動していて所在を判定できず、{target}の実行を否定できない"


@dataclasses.dataclass(frozen=True)
class CliProcess:
    """書き換える配置から起動したプロセスか、その可能性を否定できないプロセス。"""

    pid: int
    evidence: Evidence
    path: str
    """判定に使った、読めた実行ファイルかスクリプトのパス。どちらも読めない場合は実行名。"""


def describe_processes(processes: Iterable[CliProcess], target: str) -> str:
    """延期の理由として、プロセスごとに判定の根拠、PID、読めたパスを述べる文を返す。"""
    return "。".join(process.evidence.value.format(pid=process.pid, path=process.path, target=target) for process in processes)


def find_windows_cli_processes(
    cli_name: str, *, native: Iterable[Path] = (), packages: Iterable[Path] = ()
) -> list[CliProcess]:
    """Windowsで、書き換える配置から起動したプロセスを安全側で列挙する。

    `native`は導入・更新が置き換える実行ファイルの配置、`packages`は移行が削除するnpm版・mise版のパッケージである。
    実行ファイルの所在と、パッケージについてはnode・miseが起動したスクリプトの所在でも判定する。
    ほかの場所から起動した同名の実行体（管理daemon、デスクトップアプリの同梱版、npxのキャッシュなど）は数えない。
    権限が無く所在を読めず、書き換える配置からの起動を否定できないプロセスは数える。
    """
    if sys.platform != "win32":
        return []
    native_paths = _windows_paths(native)
    package_paths = _windows_paths(packages)
    found: list[CliProcess] = []
    # process_iterはPID単位でProcessを再利用し、実行名と実行ファイルを以前の世代のまま保持する。
    # 同じ後処理の中の先行する列挙が残した値を使わないよう、現在のPIDから新しく構築したProcessで判定する。
    for listed in psutil.process_iter():
        try:
            process = psutil.Process(listed.pid)
            # 1つの属性を読めなくても、読めたほかの属性で判定するため、属性ごとに読む。
            name = _readable(process.name)
            exe = _readable(process.exe)
            cmdline = _readable(process.cmdline)
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            continue
        if conflict := _process_conflicts(cli_name, native_paths, package_paths, name, exe, cmdline):
            found.append(CliProcess(listed.pid, *conflict))
    return found


class _NpmQueryError(Exception):
    """npmのグローバル導入先の照会に失敗した。削除するパッケージが無いこととは区別し、移行を見送る。"""


def _owning_npm_package(package_name: str, launcher: Path) -> tuple[Path, Path] | str:
    """ランチャーを導入したnpmと、そのnpmが削除するパッケージのディレクトリを返す。

    帰属を確認できず保持する場合は、保持の理由を述べる警告文を返す。
    npmの導入先の照会に失敗した場合は`_NpmQueryError`を送出する。
    """
    npm = _adjacent_npm(launcher.parent)
    if npm is None:
        return f"同じディレクトリにnpmがないため保持: {launcher}。このランチャーが不要と確認できた場合だけ手動削除する"
    unresolved: list[str] = []
    package_dir = _npm_package_dir(npm, package_name, unresolved)
    if package_dir is None:
        assert unresolved
        return _unconfirmed_launcher_message(launcher, unresolved[0])
    if not _launcher_belongs_to_package(launcher, package_dir, package_name):
        return _unconfirmed_launcher_message(launcher, f"{package_dir}配下の実体ではない: {_launcher_kind(launcher)}")
    return npm, package_dir


def _unconfirmed_launcher_message(launcher: Path, reason: str) -> str:
    """帰属を確認できず保持した対象と、確認が成立しなかった理由を述べる警告文を返す。"""
    return (
        f"npm packageへの帰属を確認できないため保持: {launcher}（{reason}）。"
        "保持したため対応不要。"
        "手動削除する場合はこのランチャーの参照先とpackage.jsonのbinを確認する"
    )


def _launcher_kind(launcher: Path) -> str:
    """帰属判定に用いたランチャーの実体の種別を返す。"""
    if launcher.is_symlink():
        return f"symlink（参照先: {common.safe_resolve(launcher)}）"
    if launcher.is_dir():
        return "ディレクトリ"
    if launcher.is_file():
        return "通常ファイル"
    return "実体を取得できないパス"


def _iter_noncanonical_launchers(
    cli_name: str,
    canonical_prefix: Path,
    *,
    extra_search_directories: Iterable[Path] = (),
    excluded_launchers: Iterable[Path] = (),
) -> Iterable[Path]:
    """PATHと追加の配置先にある、npmが導入しうる非正規ランチャーを列挙する。

    `canonical_prefix`の配下と`excluded_launchers`は除く。
    miseのshimディレクトリにある実行ファイルは除外する。shimはnpmのグローバル導入物では
    ないため帰属判定が成立せず、対象へ含めると保持の警告だけが毎回出力される。
    mise管理版の移行は`setup_codex_cli.py`の`_remove_mise_versions`と`_reshim_mise`が担う。
    """
    seen: set[str] = set()
    names = (cli_name, f"{cli_name}.cmd", f"{cli_name}.exe") if sys.platform == "win32" else (cli_name,)
    excluded = {common.safe_resolve(path) for path in excluded_launchers}
    prefix_real = common.safe_resolve(canonical_prefix)
    shim_directories = common.mise_shim_directories()
    path_directories = (Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry)
    seen_directories: set[str] = set()
    for directory in (*path_directories, *extra_search_directories):
        directory_real = common.safe_resolve(directory)
        directory_key = os.path.normcase(str(directory_real))
        if directory_key in seen_directories:
            continue
        seen_directories.add(directory_key)
        if directory_real in shim_directories:
            continue
        for name in names:
            launcher = directory / name
            if not launcher.exists():
                continue
            key = os.path.normcase(str(common.safe_resolve(launcher)))
            if key in seen:
                continue
            seen.add(key)
            resolved = common.safe_resolve(launcher)
            if resolved in excluded or _is_relative_to(resolved, prefix_real):
                continue
            yield launcher


def _remove_orphan_npm_launchers(cli_name: str, package_name: str, extra_search_directories: Iterable[Path]) -> bool:
    """npmのパッケージ本体が無いのに残った起動スクリプト（`.cmd`・`.ps1`・拡張子なし）を削除する。

    PowerShellはPATH上の`.ps1`を`.cmd`より先に使うため、本体の無い起動スクリプトが残ると
    正規の導入先より前で起動に失敗する。npmが生成する起動スクリプトは自身のディレクトリ
    （`$basedir`・`%dp0%`・`%~dp0`）からの相対で`node_modules/<パッケージ>`を参照するため、
    その参照を含み本体が不在のものだけを対象とする。別の導入先を絶対パスで指すものは対象外とする。
    """
    references = tuple(f"{base}/node_modules/{package_name}/" for base in ("$basedir", "%dp0%", "%~dp0"))
    changed = False
    path_directories = (Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry)
    for directory in {common.safe_resolve(path) for path in (*path_directories, *extra_search_directories)}:
        if (directory / "node_modules").joinpath(*package_name.split("/")).exists():
            continue
        for name in (cli_name, f"{cli_name}.cmd", f"{cli_name}.ps1"):
            launcher = directory / name
            try:
                # 実行ファイル本体（数百MB）を読まないよう、起動スクリプトの大きさのファイルだけを読む。
                if not launcher.is_file() or launcher.stat().st_size > _MAX_NPM_LAUNCHER_BYTES:
                    continue
                normalized = launcher.read_text(encoding="utf-8", errors="replace").replace("\\", "/")
                if not any(reference in normalized for reference in references):
                    continue
                launcher.unlink()
            except OSError as error:
                logger.warning(
                    log_format.format_status(
                        cli_name, f"本体の無いnpm起動スクリプトを削除できない: {launcher}: {error}。手で削除する"
                    )
                )
                continue
            logger.info(log_format.format_status(cli_name, f"本体の無いnpm起動スクリプトを削除しました: {launcher}"))
            changed = True
    return changed


def _adjacent_npm(directory: Path) -> Path | None:
    names = ("npm.cmd", "npm") if sys.platform == "win32" else ("npm",)
    return next((candidate for name in names if (candidate := directory / name).is_file()), None)


def _npm_package_dir(npm: Path, package_name: str, unresolved: list[str] | None = None) -> Path | None:
    """npmのグローバルパッケージのディレクトリを返す。

    解決できない場合は`None`を返す。`unresolved`を渡した場合は、解決できなかった情報を
    そのリストへ1件追加する（呼び出し元がユーザーへ保持の理由を示すために使う）。
    npmの導入先の照会に失敗した場合は`_NpmQueryError`を送出する。
    """
    prefix_result = common.run_subprocess([str(npm), "prefix", "--global"], timeout=common.COMMAND_TIMEOUT, tag=npm.name)
    root_result = common.run_subprocess([str(npm), "root", "--global"], timeout=common.COMMAND_TIMEOUT, tag=npm.name)
    if (
        prefix_result is None
        or prefix_result.returncode != 0
        or not (prefix_result.stdout or "").strip()
        or root_result is None
        or root_result.returncode != 0
        or not (root_result.stdout or "").strip()
    ):
        raise _NpmQueryError(
            " / ".join(
                f'`"{npm}" {subcommand} --global`: {_command_output(result)}'
                for subcommand, result in (("prefix", prefix_result), ("root", root_result))
                if result is None or result.returncode != 0 or not (result.stdout or "").strip()
            )
        )
    prefix = common.safe_resolve(Path(prefix_result.stdout.strip()))
    root = common.safe_resolve(Path(root_result.stdout.strip()))
    if not _is_relative_to(root, prefix):
        _record_unresolved(unresolved, f"npmのroot（{root}）がprefix（{prefix}）の配下ではない")
        return None
    package_dir = root.joinpath(*package_name.split("/"))
    if not package_dir.is_dir():
        _record_unresolved(unresolved, f"packageのディレクトリが不在: {package_dir}")
        return None
    return package_dir


def _record_unresolved(unresolved: list[str] | None, reason: str) -> None:
    """解決できなかった対象を、収集先が渡されている場合だけ記録する。"""
    if unresolved is not None:
        unresolved.append(reason)


def _command_output(result: subprocess.CompletedProcess[str] | None) -> str:
    """npmの出力を、実行できなかった場合と非ゼロ終了の場合も含めて1つの文字列で表す。"""
    if result is None:
        return "実行できない"
    if result.returncode != 0:
        return f"終了コード{result.returncode}: {common.format_cli_error(result)}"
    return (result.stdout or "").strip() or "出力なし"


def _launcher_belongs_to_package(launcher: Path, package_dir: Path, package_name: str) -> bool:
    resolved = common.safe_resolve(launcher)
    package_real = common.safe_resolve(package_dir)
    if resolved != launcher.absolute() and _is_relative_to(resolved, package_real):
        return True
    if launcher.suffix.lower() == ".cmd":
        try:
            content = launcher.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        entrypoint = _package_bin_entrypoint(package_dir, launcher.stem)
        if entrypoint is None:
            return False
        entrypoint_real = common.safe_resolve(entrypoint)
        return any(
            common.safe_resolve(candidate) == entrypoint_real for candidate in _cmd_referenced_paths(content, launcher.parent)
        )
    if launcher.suffix.lower() == ".exe":
        # mise shimはmiseの解決情報が対象packageを指す場合だけ対象とする。
        mise_name = shutil.which("mise")
        if mise_name is None:
            return False
        mise = Path(mise_name).absolute()
        result = common.run_subprocess([str(mise), "which", launcher.stem], timeout=common.COMMAND_TIMEOUT, tag="mise")
        output = (result.stdout or "").strip() if result is not None else ""
        normalized_output = output.lower().replace("\\", "/")
        package_slug = package_name.lower().removeprefix("@").replace("/", "-")
        return bool(
            result is not None
            and result.returncode == 0
            and (package_name.lower() in normalized_output or package_slug in normalized_output)
            and _is_relative_to(common.safe_resolve(Path(output)), package_real)
        )
    return False


def _readable[T](read: Callable[[], T]) -> T | None:
    """プロセスの属性を読み、権限が無く読めない場合は`None`を返す。終了したプロセスの例外はそのまま送出する。"""
    try:
        return read()
    except psutil.AccessDenied:
        return None


def _windows_paths(paths: Iterable[Path]) -> tuple[PureWindowsPath, ...]:
    """配置を、指定どおりのパスと実体のパスの両方で比較できる形にする。

    psutilの`exe()`はjunctionとsymlinkを解決した実体のパスを返すため、配置も実体のパスで比較する。
    """
    return tuple(PureWindowsPath(str(candidate)) for path in paths for candidate in (path, common.safe_resolve(path)))


def _located_in(value: str, locations: tuple[PureWindowsPath, ...]) -> bool:
    r"""Windowsのパスが配置のいずれかと一致するかその配下にあるかを、大文字小文字と区切りの表記に依らず判定する。

    command lineに現れる`\\?\`の接頭辞は除いて比較する。
    """
    path = PureWindowsPath(value.removeprefix("\\\\?\\"))
    return any(path.is_relative_to(location) for location in locations)


def _process_conflicts(
    cli_name: str,
    native: tuple[PureWindowsPath, ...],
    packages: tuple[PureWindowsPath, ...],
    name: str | None,
    exe: str | None,
    cmdline: list[str] | None,
) -> tuple[Evidence, str] | None:
    """読めた属性から、書き換える配置から起動したプロセスか、その可能性を否定できないプロセスかを判定する。

    数える場合は根拠と判定に使ったパスを返す。
    `name`・`exe`・`cmdline`の`None`は、権限が無く読めなかったことを表す。
    """
    # 実行ファイルを読めない場合も、command lineの先頭の実行パスが書き換える配置にあれば数える。
    executable = exe or (cmdline[0] if cmdline else "")
    if executable and _located_in(executable, (*native, *packages)):
        return Evidence.STARTED, executable
    program_path = exe or name or (cmdline[0] if cmdline else "")
    program = PureWindowsPath(program_path).name.lower()
    if program in {cli_name, f"{cli_name}.exe"}:
        # 実行ファイルの所在を読めない同名の実行体は、書き換える配置から起動した可能性を除けない。
        return None if exe else (Evidence.EXE_UNREADABLE, executable or program_path)
    # node・miseが起動するスクリプトはパッケージにだけあり、置き換える実行ファイルの配置には無い。
    if not packages or program not in {"node", "node.exe", "mise", "mise.exe"}:
        return None
    if cmdline is None:
        # 起動したスクリプトを読めないnode・miseは、削除するnpm版・mise版を実行している可能性を除けない。
        return Evidence.CMDLINE_UNREADABLE, program_path
    entry = _script_entry(program, cmdline)
    if entry is None:
        return None
    if PureWindowsPath(entry).anchor:
        located = _located_in(entry, packages) or _located_in(str(common.safe_resolve(Path(entry))), packages)
        return (Evidence.STARTED, entry) if located else None
    # 名前や相対パスで指定したCLIは、所在で判定できず、削除するnpm版・mise版から起動した可能性を除けない。
    launcher_names = {cli_name, *(f"{cli_name}{suffix}" for suffix in (".exe", ".cmd", ".ps1", ".js"))}
    return (Evidence.NAMED_ONLY, entry) if PureWindowsPath(entry).name.lower() in launcher_names else None


def _script_entry(program: str, cmdline: list[str]) -> str | None:
    """nodeが実行するスクリプトか`mise exec`が起動するコマンドを返し、コードを直接評価する起動では`None`を返す。"""
    command = cmdline[1:]
    if program in {"mise", "mise.exe"}:
        # miseの--より前は導入するtool指定であり、起動するコマンドではない。
        if not command or command[0] not in {"exec", "x"} or "--" not in command:
            return None
        command = command[command.index("--") + 1 :]
        if command and PureWindowsPath(command[0]).name.lower() in {"node", "node.exe"}:
            command = command[1:]
    index = 0
    while index < len(command) and command[index].startswith("-"):
        option = command[index]
        if option == "--":
            index += 1
            break
        if option in {"-e", "--eval", "-p", "--print"} or option.startswith(("--eval=", "--print=")):
            return None
        index += (
            2
            if option in {"-r", "--require", "--import", "--loader", "--experimental-loader", "-C", "--conditions", "--title"}
            else 1
        )
    return command[index] if index < len(command) and not command[index].startswith("-") else None


def _cmd_referenced_paths(content: str, launcher_dir: Path) -> Iterable[Path]:
    for raw in re.findall(r'(?:"([^"]+)"|([^\s"]+))', content):
        token = raw[0] or raw[1]
        normalized = token.replace("%~dp0", str(launcher_dir) + os.sep).replace("%dp0%", str(launcher_dir) + os.sep)
        normalized = normalized.replace("\\", os.sep)
        candidate = Path(normalized)
        if not candidate.is_absolute():
            candidate = launcher_dir / candidate
        yield candidate


def _package_bin_entrypoint(package_dir: Path, cli_name: str) -> Path | None:
    try:
        metadata = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(metadata, dict):
        return None
    bin_value = metadata.get("bin")
    relative: object
    if isinstance(bin_value, str):
        relative = bin_value
    elif isinstance(bin_value, dict):
        relative = bin_value.get(cli_name)
    else:
        return None
    if not isinstance(relative, str) or not relative:
        return None
    entrypoint = common.safe_resolve(package_dir / relative)
    package_real = common.safe_resolve(package_dir)
    return entrypoint if entrypoint.is_file() and _is_relative_to(entrypoint, package_real) else None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
