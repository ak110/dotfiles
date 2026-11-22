"""公式インストーラーを利用するCLI導入処理の共通機能。"""

import codecs
import contextlib
import dataclasses
import enum
import functools
import json
import locale
import logging
import os
import posixpath
import re
import shlex
import shutil
import ssl
import stat
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

_MAX_NPM_LAUNCHER_BYTES = 64 * 1024
"""npmの起動スクリプト（数百バイト）より十分大きい上限。超えるファイルは実行ファイル本体などとみなして読まない。"""
_NPM_SHIM_FORMS_FILE = Path(__file__).with_name("npm_cmd_shim_forms.json")
"""Windowsのnpm（同梱のcmd-shim）が生成する起動スクリプトの既知の形。`scripts/gen_npm_cmd_shim_forms.py`が生成する。"""


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


def format_user_command(executable: Path, arguments: str) -> str:
    """ユーザーへ実行を案内するコマンドを、表示する文に埋め込む形で返す。

    Windowsでは推奨するPowerShell 7でそのまま実行できるよう、PowerShellのコマンドと明記し、
    引用した実行パスの前に呼び出し演算子`&`を付ける。
    """
    if sys.platform == "win32":
        # 二重引用符の中ではパスの`$`と`` ` ``を展開するため、単一引用符で囲み、引用符そのものは重ねる。
        quoted = "'" + str(executable).replace("'", "''") + "'"
        return f"PowerShellで`& {quoted} {arguments}`"
    quoted = "".join("\\" + char if char in '\\"$`' else char for char in str(executable))
    return f'`"{quoted}" {arguments}`'


LEGACY_NPM_CLIS = {"@openai/codex": "codex", "@anthropic-ai/claude-code": "claude"}
LEGACY_NPM_LAUNCHERS = frozenset(
    f"{name}{suffix}" for name in LEGACY_NPM_CLIS.values() for suffix in ("", ".cmd", ".ps1", ".exe")
)
"""旧npm版の起動ファイルの名前（小文字）。npmが生成する形と、ユーザーが同名で置くラッパーの拡張子を含む。"""
RERUN_AFTER_REPAIR = "直した後に`update-dotfiles`を再実行すると旧npm版かを再確認し、旧npm版なら削除コマンドを表示する"
"""削除対象を確定できず削除コマンドを示せない警告の末尾に置く、再確認の条件。"""


def format_command(command: list[str]) -> str:
    """引数を含むコマンドを、ユーザーがそのまま実行できる表記で返す。"""
    # 引数も引用し、npmの実行経路にmiseのNode版などの外部入力が入っても展開させない。
    if sys.platform == "win32":
        arguments = " ".join(
            part if re.fullmatch(r"[A-Za-z0-9_@./:+-]+", part) else "'" + part.replace("'", "''") + "'" for part in command[1:]
        )
    else:
        arguments = shlex.join(command[1:])
    return format_user_command(Path(command[0]), arguments)


def protected_npm_package(installed_name: str, metadata: dict[object, object]) -> bool:
    """旧npm版か、同名の起動ファイルをnpmが削除するパッケージを判定する。"""
    return (
        installed_name in LEGACY_NPM_CLIS
        or any(metadata.get("name") == name for name in LEGACY_NPM_CLIS)
        or bool(LEGACY_NPM_LAUNCHERS & {name.lower() for name in normalize_npm_package_bins(metadata)})
    )


def warn_about_retained_npm_package(
    package: Path, command: list[str], canonical: Path, *, minimal: bool = False, unconfirmed: str = ""
) -> None:
    """旧npm版の保持と、ユーザーが選べる対処を案内する。"""
    removal = format_command(command)
    availability = "正規版はfullモードで導入する。" if minimal else ""
    logger.warning(
        "旧npm版に関係するパッケージを保持: %s。%s%s。%s"
        "不要なら、旧版を終了し、パッケージ内や同名の起動先から自分で配置・編集したファイルを退避してから、%sを実行する",
        package,
        unconfirmed,
        _held_launcher_guidance(canonical),
        availability,
        removal,
    )


def warn_about_npm_launchers(
    cli_name: str,
    package_name: str,
    canonical_launcher: Path,
    canonical_prefix: Path,
    *,
    extra_search_directories: Iterable[Path] = (),
    excluded_launchers: Iterable[Path] = (),
) -> None:
    """非正規の起動先を調べ、PATHの影響と手動での対処を案内する。

    正規の起動先と呼出側が配置した起動管理は検出対象から除く。
    npmが手動削除時に巻き込む可能性のある起動ファイルは、退避の案内に使う。
    """
    left = _find_npm_launchers_left_without_package(cli_name, package_name, extra_search_directories)
    for directory, launchers in left.items():
        logger.warning(
            log_format.format_status(
                cli_name,
                f"パッケージ本体（{directory.joinpath('node_modules', *package_name.split('/'))}）の無い"
                f"npmの起動スクリプトを保持: {'、'.join(map(str, launchers))}。"
                "意図したラッパーなら保持する。"
                f"{_held_launcher_guidance(canonical_launcher)}",
            )
        )
    shown = {launcher for launchers in left.values() for launcher in launchers}
    packages: dict[Path, _NpmPackage | _UnreadableNpmPackage] = {}
    without_npm: dict[Path, list[Path]] = {}
    launched: set[Path] = set()
    for launcher in _iter_noncanonical_launchers(
        cli_name,
        canonical_prefix,
        extra_search_directories=extra_search_directories,
        excluded_launchers=(canonical_launcher, *excluded_launchers),
    ):
        location = common.safe_resolve(launcher.parent) / launcher.name
        if location in shown:
            continue
        npm = _adjacent_npm(launcher.parent)
        if npm is None:
            without_npm.setdefault(launcher.parent, []).append(launcher)
            continue
        try:
            package = _find_npm_package(npm, package_name)
        except _NpmQueryError as error:
            logger.warning(
                log_format.format_status(
                    cli_name,
                    f"npmの導入先を照会できないため旧npm版を確認できない: {launcher}（{error}）。"
                    f"{_held_launcher_guidance(canonical_launcher)}。"
                    f"括弧内の失敗した照会が成功するようnpmを{RERUN_AFTER_REPAIR}",
                )
            )
            continue
        if isinstance(package, str):
            logger.warning(
                log_format.format_status(cli_name, _unconfirmed_launcher_message(launcher, package, canonical_launcher))
            )
            continue
        packages[package.directory] = package
        if isinstance(package, _UnreadableNpmPackage) or location in package.unconfirmed:
            continue
        if location in package.generated or _launcher_belongs_to_package(launcher, package, package_name):
            launched.add(package.directory)
        else:
            reason = f"{package.directory}のbinの実体ではない: {_launcher_kind(launcher)}"
            logger.warning(
                log_format.format_status(cli_name, _unconfirmed_launcher_message(launcher, reason, canonical_launcher))
            )
    for launchers in without_npm.values():
        logger.warning(
            log_format.format_status(
                cli_name,
                f"同じディレクトリにnpmがないため保持: {'、'.join(map(str, launchers))}。"
                f"{_held_launcher_guidance(canonical_launcher)}",
            )
        )
    for package in packages.values():
        guidance = _held_launcher_guidance(canonical_launcher)
        if isinstance(package, _UnreadableNpmPackage):
            logger.warning(
                log_format.format_status(
                    cli_name,
                    f"旧npm版を保持: {package.directory}。"
                    f"`uninstall`が一緒に削除する起動ファイルを確認できない: {package.manifest}（{package.reason}）。"
                    f"削除しない。同じ版のpackage.jsonを復元するか読取権限を直す。{RERUN_AFTER_REPAIR}。{guidance}",
                )
            )
            continue
        if not package.unconfirmed and package.directory not in launched:
            continue
        removal = format_user_command(package.npm, f"uninstall --global {package_name}")
        unconfirmed = ""
        if package.unconfirmed:
            files = "、".join(f"{path}（{reason}）" for path, reason in package.unconfirmed.items())
            unconfirmed = f"`uninstall`が一緒に削除するファイルのうち、npmの生成物と確認できない: {files}。"
        logger.warning(
            log_format.format_status(
                cli_name,
                f"旧npm版を保持: {package.directory}。{unconfirmed}{guidance}。"
                "旧npm版が不要なら、使用中のプロセスを終了し、パッケージ内と同名の起動先にある"
                f"自分で置いた・編集したファイルを別の場所へ移してから、{removal}を実行する",
            )
        )


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


@dataclasses.dataclass(frozen=True)
class _NpmPackage:
    """npmがグローバルに導入したパッケージと、その`uninstall`がパッケージと一緒に削除する既存のファイル。

    npmの`uninstall`は、package.jsonのbinの名前ごとに、Windowsではprefix直下の`<bin>`・`<bin>.cmd`・`<bin>.ps1`を、
    POSIXでは`bin/<bin>`を、中身を確かめずに削除する（bin-linksのgetPaths）。
    """

    npm: Path
    directory: Path
    bins: dict[str, str]
    """binの名前と、パッケージから見た実体の相対パス（`/`区切り）。"""
    generated: frozenset[Path]
    """`uninstall`が一緒に削除する、npmの生成物と確かめたファイル。"""
    unconfirmed: dict[Path, str]
    """`uninstall`が一緒に削除するが、npmの生成物と確かめられないファイルと、その理由。"""


@dataclasses.dataclass(frozen=True)
class _UnreadableNpmPackage:
    """package.jsonを読めず、npmの`uninstall`がパッケージと一緒に削除するファイルを確定できないパッケージ。"""

    directory: Path
    manifest: Path
    reason: str


@dataclasses.dataclass(frozen=True)
class _NpmShimForm:
    """npm（cmd-shim）の版が生成する起動スクリプトの1つの形。

    本文は`parts`の間へ、起動スクリプトから見たbinの実体の相対パスを`separator`区切りで差し込んだものである。
    """

    parts: tuple[str, ...]
    separator: str

    def target(self, content: str) -> str | None:
        """本文がファイル全体でこの形と一致する場合に、差し込まれた相対パスを`/`区切りで返す。"""
        slots = len(self.parts) - 1
        size, remainder = divmod(len(content) - sum(map(len, self.parts)), slots)
        if size <= 0 or remainder:
            return None
        target = content[len(self.parts[0]) : len(self.parts[0]) + size]
        # cmd-shimは区切りをそろえて書くため、もう一方の区切りを含む参照は生成しない。
        if ("/" if self.separator == "\\" else "\\") in target or target.join(self.parts) != content:
            return None
        return target.replace(self.separator, "/")


@functools.cache
def _npm_shim_forms() -> dict[str, tuple[_NpmShimForm, ...]]:
    """拡張子ごとの、npmが生成する起動スクリプトの既知の形を返す。"""
    data = json.loads(_NPM_SHIM_FORMS_FILE.read_text(encoding="utf-8"))
    forms: dict[str, list[_NpmShimForm]] = {}
    for form in data["forms"]:
        forms.setdefault(form["suffix"], []).append(_NpmShimForm(tuple(form["parts"]), form["separator"]))
    return {suffix: tuple(items) for suffix, items in forms.items()}


def _npm_shim_targets(launcher: Path) -> set[str]:
    """起動スクリプトがファイル全体でnpmの既知の生成形式と一致する場合の、binの実体の相対パス（`/`区切り）を返す。

    読めないファイル、テキストでないファイル、どの形とも一致しないファイルでは空集合を返す。読み取りの`OSError`は送出する。
    """
    content = _read_npm_launcher(launcher)
    if content is None:
        return set()
    forms = _npm_shim_forms().get(launcher.suffix.lower(), ())
    return {target for form in forms if (target := form.target(content)) is not None}


def _find_npm_package(npm: Path, package_name: str) -> _NpmPackage | _UnreadableNpmPackage | str:
    """npmがグローバルに導入したパッケージと、その`uninstall`が一緒に削除するファイルの帰属を調べる。

    パッケージを特定できない場合はその理由を返す。npmの導入先の照会に失敗した場合は`_NpmQueryError`を送出する。
    """
    root = _npm_global_root(npm)
    if isinstance(root, str):
        return root
    directory = root.joinpath(*package_name.split("/"))
    if not directory.is_dir():
        return f"packageのディレクトリが不在: {directory}"
    manifest = directory / "package.json"
    bins = _package_bins(manifest)
    if isinstance(bins, str):
        return _UnreadableNpmPackage(directory, manifest, bins)
    # bin-linksと同じく、Windowsはnode_modulesの親（prefix）、POSIXはprefixの`bin`へ置く。
    bin_dir = root.parent if sys.platform == "win32" else root.parent.parent / "bin"
    generated: set[Path] = set()
    unconfirmed: dict[Path, str] = {}
    for name, target in bins.items():
        for path in _existing_launchers(bin_dir, name):
            if reason := _npm_bin_unconfirmed(path, directory, target):
                unconfirmed[path] = reason
            else:
                generated.add(path)
    return _NpmPackage(npm, directory, bins, frozenset(generated), unconfirmed)


def _existing_launchers(directory: Path, name: str) -> list[Path]:
    """配置先にある`name`の起動先を返す。Windowsは拡張子なし・`.cmd`・`.ps1`、POSIXは拡張子なしを見る。"""
    suffixes = ("", ".cmd", ".ps1") if sys.platform == "win32" else ("",)
    return [path for suffix in suffixes if (path := directory / f"{name}{suffix}").exists() or path.is_symlink()]


def _npm_bin_unconfirmed(path: Path, package_dir: Path, target: str | None) -> str | None:
    """`path`を、npmがパッケージのbin（実体はパッケージから見た`target`）として生成したと確かめられない場合に、その理由を返す。

    POSIXのnpmはbinの実体を直接指すシンボリックリンクを作る。
    リンク自身の参照先（途中のディレクトリは実体へ解決する）がbinの実体であるものだけを生成物とし、
    版の切替用のリンクなど、別のリンクを経由してbinの実体へ着くものは生成物とみなさない。
    Windowsのnpmはリンクを作らず起動スクリプトを書くため、リンクとjunctionなどの再解析点は生成物とみなさない。
    起動スクリプトは、ファイル全体がnpmの既知の生成形式と一致し、その形が`target`を参照するものだけを生成物とする。
    """
    entry = package_dir.joinpath(*target.split("/")) if target is not None else None
    if sys.platform != "win32":
        if not path.is_symlink():
            return _launcher_kind(path)
        try:
            direct = _link_target(path)
        except OSError as error:
            return f"読み取りに失敗: {error}"
        if entry is not None and direct == common.safe_resolve(entry.parent) / entry.name:
            return None
        reason = f"package.jsonのbinの実体を直接指さないsymlink（参照先: {direct}）"
        resolved = common.safe_resolve(path)
        if _is_relative_to(resolved, common.safe_resolve(package_dir)):
            return f"{reason}。最終的な参照先の{resolved}もパッケージと一緒に削除される"
        return reason
    if _is_link(path):
        return f"Windowsのnpmが作らないリンク: {_launcher_kind(path)}"
    try:
        targets = _npm_shim_targets(path)
    except OSError as error:
        return f"読み取りに失敗: {error}"
    if entry is None or _relative_reference(path, entry) not in targets:
        return f"npmの既知の生成形式と一致しない{_launcher_kind(path)}"
    return None


def _link_target(link: Path) -> Path:
    """シンボリックリンク自身の参照先を、途中のディレクトリだけを実体へ解決して返す。参照先がリンクでもたどらない。"""
    target = link.parent / link.readlink()
    return common.safe_resolve(target.parent) / target.name


def _is_link(path: Path) -> bool:
    """シンボリックリンクか、junctionなどの再解析点かを返す。"""
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _relative_reference(launcher: Path, entry: Path) -> str | None:
    """起動スクリプトのディレクトリから`entry`への相対パスを`/`区切りで返す。別のドライブにあれば`None`を返す。"""
    try:
        return Path(os.path.relpath(entry, common.safe_resolve(launcher.parent))).as_posix()
    except ValueError:
        return None


def _unconfirmed_launcher_message(launcher: Path, reason: str, canonical_launcher: Path) -> str:
    """帰属を確認できず保持した対象と、確認が成立しなかった理由と、保持した起動先への対処を述べる警告文を返す。"""
    return f"npm packageへの帰属を確認できないため保持: {launcher}（{reason}）。{_held_launcher_guidance(canonical_launcher)}"


def _held_launcher_guidance(canonical_launcher: Path) -> str:
    """自動では削除せず保持した起動先が、PATHで正規の起動先より先に見つかる場合の影響と対処を述べる文を返す。"""
    return (
        "PATHで先に解決される間は、正規の起動先ではなく保持した起動先（旧npm版など）が起動する。"
        f"不要と確認できた場合だけ別の場所へ退避し、正規の起動先`{canonical_launcher}`で起動する"
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
    # PowerShellは`.ps1`を`.cmd`より先に使うため、`.ps1`だけが残った配置も見つける。
    names = (cli_name, f"{cli_name}.cmd", f"{cli_name}.ps1", f"{cli_name}.exe") if sys.platform == "win32" else (cli_name,)
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


def _find_npm_launchers_left_without_package(
    cli_name: str, package_name: str, extra_search_directories: Iterable[Path]
) -> dict[Path, list[Path]]:
    """パッケージ本体の無い配置に残ったnpmの起動スクリプト（Windowsでは拡張子なし・`.cmd`・`.ps1`）を、配置先ごとに返す。

    PowerShellはPATH上の`.ps1`を`.cmd`より先に使うため、本体の無い起動スクリプトが残ると正規の起動先より前で起動に失敗する。
    npmの`uninstall`は起動スクリプトも削除するため、残るのは本体が手で削除された場合などに限られる。
    npmの起動スクリプトは自身のディレクトリ（`$basedir`・`$basedir_win`・`%dp0%`・`%~dp0`）からの相対で
    `node_modules/<パッケージ>`を参照するため、その参照を含み、同じディレクトリに`node_modules/<パッケージ>`が無いものを集める。
    シンボリックリンクは、リンク先の本文をリンクの置き場所から実行するものとして同じく判定する。
    エディターがBOM付きUTF-16で保存し直したもの（PowerShellは`.ps1`として実行する）も読む。
    """
    bases = ("$basedir", "$basedir_win", "%dp0%", "%~dp0")
    references = tuple(f"{base}/node_modules/{package_name}/" for base in bases)
    path_directories = (Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry)
    left: dict[Path, list[Path]] = {}
    for directory in dict.fromkeys(common.safe_resolve(path) for path in (*path_directories, *extra_search_directories)):
        if (directory / "node_modules").joinpath(*package_name.split("/")).exists():
            continue
        for launcher in _existing_launchers(directory, cli_name):
            try:
                content = _read_npm_launcher(launcher, utf16=True)
            except OSError:
                continue
            if content is not None and any(reference in content.replace("\\", "/") for reference in references):
                left.setdefault(directory, []).append(launcher)
    return left


def _read_npm_launcher(launcher: Path, *, utf16: bool = False) -> str | None:
    """起動スクリプトとして読めるファイルの本文を返し、それ以外では`None`を返す。

    起動スクリプトより大きいファイル（数百MBの実行ファイル本体など）と復号できないファイルは、
    実行ファイルなどの起動スクリプトではないものとみなす。npmはUTF-8で書くためUTF-8として復号し、
    `utf16`ではBOM付きUTF-16も復号する。読み取りの`OSError`は送出する。
    """
    if not launcher.is_file() or launcher.stat().st_size > _MAX_NPM_LAUNCHER_BYTES:
        return None
    data = launcher.read_bytes()
    try:
        return data.decode("utf-16" if utf16 and data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8")
    except UnicodeDecodeError:
        return None


def _adjacent_npm(directory: Path) -> Path | None:
    names = ("npm.cmd", "npm") if sys.platform == "win32" else ("npm",)
    return next((candidate for name in names if (candidate := directory / name).is_file()), None)


def _npm_global_root(npm: Path) -> Path | str:
    """npmのグローバルのnode_modulesを返し、prefixの配下でない場合はその理由を返す。

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
                f"{format_command([str(npm), subcommand, '--global'])}: {_command_output(result)}"
                for subcommand, result in (("prefix", prefix_result), ("root", root_result))
                if result is None or result.returncode != 0 or not (result.stdout or "").strip()
            )
        )
    prefix = common.safe_resolve(Path(prefix_result.stdout.strip()))
    root = common.safe_resolve(Path(root_result.stdout.strip()))
    if not _is_relative_to(root, prefix):
        return f"npmのroot（{root}）がprefix（{prefix}）の配下ではない"
    return root


def _command_output(result: subprocess.CompletedProcess[str] | None) -> str:
    """npmの出力を、実行できなかった場合と非ゼロ終了の場合も含めて1つの文字列で表す。"""
    if result is None:
        return "実行できない"
    if result.returncode != 0:
        return f"終了コード{result.returncode}: {common.format_cli_error(result)}"
    return (result.stdout or "").strip() or "出力なし"


def _launcher_belongs_to_package(launcher: Path, package: _NpmPackage, package_name: str) -> bool:
    """`uninstall`が一緒に削除するファイルの外にある起動先が、パッケージから導入したものかを返す。"""
    if launcher.suffix.lower() != ".exe" or launcher.is_symlink():
        return _npm_bin_unconfirmed(launcher, package.directory, package.bins.get(launcher.stem)) is None
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
        and _is_relative_to(common.safe_resolve(Path(output)), common.safe_resolve(package.directory))
    )


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


def _package_bins(manifest: Path) -> dict[str, str] | str:
    """package.jsonのbinの名前と、パッケージから見た実体の相対パス（`/`区切り）を返す。読めない場合はその理由を返す。

    名前と実体はnpm（npm-normalize-package-bin）と同じく正規化する。npmの`uninstall`はこの名前の起動スクリプトを削除し、
    npmが生成する起動スクリプトはこの実体を参照する。
    """
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return "存在しない"
    except OSError as error:
        return f"読み取りに失敗: {error.strerror or error}"
    except ValueError as error:
        return f"JSONとして読めない: {error}"
    if not isinstance(metadata, dict):
        return "JSONのobjectではない"
    return normalize_npm_package_bins(metadata)


def normalize_npm_package_bins(metadata: dict[object, object]) -> dict[str, str]:
    """npmが起動ファイルの名前と参照先に使うbinの正規化を再現する。"""
    bins = metadata.get("bin")
    if isinstance(bins, str):
        bins = {metadata["name"]: bins} if isinstance(metadata.get("name"), str) and metadata["name"] else {}
    elif isinstance(bins, list):
        bins = {item: item for item in bins if isinstance(item, str)}
    elif not isinstance(bins, dict):
        bins = {}
    normalized: dict[str, str] = {}
    for key, value in bins.items():
        name = _npm_normalized_path(posixpath.basename(str(key).replace("\\", "/").replace(":", "/").rstrip("/")))
        target = _npm_normalized_path(value.replace("\\", "/")) if isinstance(value, str) else ""
        if name and target:
            normalized[name] = target
    return normalized


def _npm_normalized_path(value: str) -> str:
    """npmと同じく、`/`へ連結して正規化した表記から先頭の`/`を除く。`..`はパッケージの外へ出ない。"""
    return posixpath.normpath("/" + value.lstrip("/"))[1:]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
