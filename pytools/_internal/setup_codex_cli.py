"""Codex CLI公式スタンドアローン版を導入または更新する。"""

import contextlib
import json
import logging
import os
import random
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from agent_toolkit._common import dotfiles_mode

# 他の処理で使わない依存は、使用時まで読み込まない。
lazy import httpx

from pytools._internal import (
    common,
    log_format,
    post_apply_outcome,
    prune_codex_packages,
    setup_bin_path,
    setup_cli_common,
    winutils,
)

logger = logging.getLogger(__name__)

_PACKAGE = "@openai/codex"
_HTTP_TIMEOUT = 30.0
_HTTP_RETRY_ATTEMPTS = 3
_HTTP_RETRY_BASE_DELAY = 0.25
_HTTP_RETRY_JITTER = 0.25
_COMMAND_TIMEOUT = 300.0


def run(client: httpx.Client | None = None) -> post_apply_outcome.PostApplyOutcome:
    """公式スタンドアローン版を導入または更新し、確認後に旧npm版とmise版を移行する。

    取得・導入・更新と確認の失敗は警告を出力してスキップと数え、旧版の撤去と中継の再生成の失敗は失敗と数える。
    """
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
    if setup_cli_common.is_windows_cli_running("codex", _PACKAGE):
        logger.info(
            log_format.format_status(
                "codex",
                "実行中のため導入と移行を次回へ延期。Codexを終了して`update-dotfiles`を再実行する",
            ),
        )
        return post_apply_outcome.PostApplyOutcome()
    result = _run_installer(client)
    if result is None or result.returncode != 0:
        logger.warning(
            log_format.format_status(
                "codex",
                f"導入または更新に失敗: {common.format_cli_error(result)}。`update-dotfiles`を再実行する",
            ),
        )
        return post_apply_outcome.PostApplyOutcome()
    launcher = find_standalone_launcher()
    if launcher is None:
        message = (
            f"管理対象のCodexが見つからないため旧版を保持: {_standalone_root() / 'current'}。`update-dotfiles`を再実行する"
        )
        logger.warning(log_format.format_status("codex", message))
        return post_apply_outcome.PostApplyOutcome()
    verification = common.run_subprocess([str(launcher), "--version"], timeout=30, tag="codex")
    if verification is None or verification.returncode != 0:
        logger.warning(
            log_format.format_status(
                "codex",
                f"正規版を確認できないため旧版を保持: {common.format_cli_error(verification)}。`update-dotfiles`を再実行する",
            ),
        )
        return post_apply_outcome.PostApplyOutcome()
    setup_cli_common.prepend_path(_visible_bin_dir())
    failures, removed = _remove_mise_versions()
    migrated = False
    try:
        migrated = setup_cli_common.migrate_npm_launchers(
            "codex",
            _PACKAGE,
            launcher,
            _standalone_root(),
            extra_search_directories=_nvm_bin_directories(),
        )
    except RuntimeError as error:
        failures.append(str(error))
    orphaned_shims: list[Path] = []
    if not removed and not failures:
        orphan_failures, orphaned_shims = _find_orphaned_mise_shims()
        failures.extend(orphan_failures)
    # 自身の除去履歴だけでなく、miseの申告と中継の実在が一致しない場合も再生成する。
    if removed or migrated or orphaned_shims:
        reshim_failures = _reshim_mise()
        failures.extend(reshim_failures)
        if not reshim_failures:
            for shim in orphaned_shims:
                if shim.is_file():
                    logger.warning(
                        log_format.format_status(
                            "codex",
                            f"mise管理外の中継を保持: {shim}。"
                            "mise管理外のため自動削除しない。"
                            "不要な中継と確認できた場合だけ、このファイルを手動削除する",
                        ),
                    )
    pruning = prune_codex_packages.run()
    if pruning.failure:
        failures.append(pruning.failure)
    try:
        install_recovery_launcher(launcher, windows=sys.platform == "win32")
    except (OSError, RuntimeError) as error:
        failures.append(str(error))
    if failures:
        return post_apply_outcome.PostApplyOutcome(
            changed=True,
            failure=" / ".join(failures),
            next_action="問題を解消した後に`update-dotfiles`を再実行する。",
        )
    return post_apply_outcome.PostApplyOutcome(changed=True)


def _run_installer(client: httpx.Client | None) -> subprocess.CompletedProcess[str] | None:
    """公式インストーラーを取得して非対話で実行する。

    公式インストーラーは未導入なら新規導入、導入済みなら更新として同じ処理経路を使うため、
    導入済み判定による分岐を設けない。
    """
    windows = sys.platform == "win32"
    executable = setup_cli_common.find_powershell() if windows else "sh"
    suffix = ".ps1" if sys.platform == "win32" else ".sh"
    url = "https://chatgpt.com/codex/install.ps1" if sys.platform == "win32" else "https://chatgpt.com/codex/install.sh"
    temp_path: Path | None = None
    with contextlib.ExitStack() as resources:
        try:
            active_client = (
                client
                if client is not None
                else resources.enter_context(
                    httpx.Client(
                        timeout=_HTTP_TIMEOUT,
                        follow_redirects=True,
                        verify=setup_cli_common.installer_ssl_verify(windows=windows),
                    )
                )
            )
            response = _get_installer(active_client, url)
            with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(response.content)
            command = (
                [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(temp_path)]
                if sys.platform == "win32"
                else [executable, str(temp_path)]
            )
            # 公式マニュアルはスクリプト化した導入と更新に`CODEX_NON_INTERACTIVE=1`を使うと定める。
            env_overrides = {"CODEX_NON_INTERACTIVE": "1"}
            if sys.platform != "win32":
                env_overrides["PATH"] = _installer_path()
            return common.run_subprocess(
                command,
                timeout=_COMMAND_TIMEOUT,
                tag="codex",
                env_overrides=env_overrides,
            )
        except (httpx.HTTPError, OSError) as error:
            logger.warning(
                log_format.format_status(
                    "codex",
                    f"公式インストーラーの取得に失敗: {error}。`update-dotfiles`を再実行する",
                ),
            )
            return None
        finally:
            if temp_path is not None:
                with contextlib.suppress(OSError):
                    temp_path.unlink()


def _installer_path() -> str:
    """公式インストーラーへ渡すPATHを返す。"""
    visible_bin = str(_visible_bin_dir())
    inherited_entries = os.environ.get("PATH", "").split(os.pathsep)
    # 旧版を競合として検出すると、可視binがPATHにあってもシェル設定を追記するため除外する。
    retained_entries = [
        entry for entry in inherited_entries if entry and entry != visible_bin and shutil.which("codex", path=entry) is None
    ]
    return os.pathsep.join([visible_bin, *retained_entries])


def _get_installer(client: httpx.Client, url: str) -> httpx.Response:
    """一時的なHTTPエラーを有限回再試行して公式インストーラーを取得する。"""
    for attempt in range(_HTTP_RETRY_ATTEMPTS):
        retry_error: httpx.HTTPError
        try:
            response = client.get(url)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            if status_code != 429 and status_code < 500:
                raise
            retry_error = error
        except (httpx.NetworkError, httpx.TimeoutException) as error:
            retry_error = error
        if attempt == _HTTP_RETRY_ATTEMPTS - 1:
            raise retry_error
        delay = _HTTP_RETRY_BASE_DELAY * (2**attempt) + random.uniform(0, _HTTP_RETRY_JITTER)
        time.sleep(delay)
    raise AssertionError("到達不能")


def _codex_home() -> Path:
    """公式インストーラーが使うCodexのホームディレクトリを返す。"""
    return prune_codex_packages.codex_home()


def _standalone_root() -> Path:
    """スタンドアローン版のパッケージキャッシュのルートを返す。"""
    return _codex_home() / "packages" / "standalone"


def find_standalone_launcher() -> Path | None:
    """管理対象のスタンドアローン版ランチャーを返す。

    公式インストーラーが導入済み版の判定に使う`current/bin/codex`と`current/codex`を同じ順で確認する。
    """
    current = _standalone_root() / "current"
    name = "codex.exe" if sys.platform == "win32" else "codex"
    return next((candidate for candidate in (current / "bin" / name, current / name) if candidate.is_file()), None)


def find_runtime_launcher(*, windows: bool) -> Path | None:
    """設定ホームと独立した可視binからnativeを選び、自身への再帰起動を除く。"""
    root = common.find_dotfiles_root()
    wrappers = {root / "bin" / name for name in ("codex", "codex.cmd")} if root is not None else set()
    directories = [
        _visible_bin_dir(),
        *(Path(entry) for entry in os.environ.get("PATH", "").split(";" if windows else os.pathsep) if entry),
    ]
    for directory in dict.fromkeys(directories):
        reference = directory / ".dotfiles-codex-native"
        candidates = [directory / "codex.exe", directory / "codex.cmd"] if windows else [reference, directory / "codex"]
        for candidate in candidates:
            if candidate == reference and not reference.is_symlink():
                continue
            if candidate.is_file() and not any(candidate.samefile(wrapper) for wrapper in wrappers if wrapper.is_file()):
                return candidate
    candidate = find_standalone_launcher()
    if candidate is not None and not any(candidate.samefile(wrapper) for wrapper in wrappers if wrapper.is_file()):
        return candidate
    return None


def install_recovery_launcher(native: Path, *, windows: bool) -> None:
    """確認済みの公式実行体を保持し、通常の可視コマンドを起動管理へ接続する。"""
    root = common.find_dotfiles_root()
    if root is None:
        raise RuntimeError("Codexの起動管理を配置するdotfilesが見つからない。update-dotfilesを再実行する")
    source = root / "bin" / ("codex.cmd" if windows else "codex")
    if not source.is_file():
        raise RuntimeError(f"Codexの起動管理が見つからない: {source}。dotfilesを更新して再実行する")
    visible = _visible_bin_dir()
    visible.mkdir(parents=True, exist_ok=True)
    target = visible / ("codex.cmd" if windows else "codex")
    if windows:
        # 公式Windowsインストーラーの可視binはpackageのbinへのjunctionである。
        # 内容には書き込まず、Codex専用ディレクトリだけを既存dotfiles/binより後へ移す。
        registered = setup_bin_path.run()
        if registered.failure:
            raise RuntimeError(registered.failure)
        current, kind = winutils.read_user_env_var("Path")
        wrapper = source.parent
        if any(winutils.path_key(entry) == winutils.path_key(str(wrapper)) for entry in (current or "").split(";")):
            updated = _prefer_wrapper_path(current or "", wrapper, visible)
            if updated != current:
                winutils.write_user_env_var("Path", updated, kind)
                winutils.broadcast_environment_change()
        with setup_cli_common.PROCESS_PATH_LOCK:
            os.environ["PATH"] = _prefer_wrapper_path(os.environ.get("PATH", ""), wrapper, visible)
        return
    if target.exists() or target.is_symlink():
        try:
            owned = target.samefile(native) or target.samefile(source)
        except FileNotFoundError:
            owned = False
        if not owned:
            raise RuntimeError(f"管理外のCodex起動コマンドを保持: {target}。導入先を確認してupdate-dotfilesを再実行する")
    reference = visible / ".dotfiles-codex-native"
    if reference.exists() and not reference.is_symlink():
        raise RuntimeError(f"管理外のCodex実体参照を保持: {reference}。導入先を確認してupdate-dotfilesを再実行する")
    # currentへの参照を保ち、公式更新が選んだ版へ追随する。resolveで版別の実体へ固定しない。
    temporary_reference = reference.with_name(f".{reference.name}-{secrets.token_hex(8)}")
    try:
        temporary_reference.symlink_to(native.absolute())
        temporary_reference.replace(reference)
    finally:
        temporary_reference.unlink(missing_ok=True)
    temporary = target.with_name(f".{target.name}-{secrets.token_hex(8)}")
    try:
        temporary.symlink_to(source)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _prefer_wrapper_path(value: str, wrapper: Path, native: Path) -> str:
    """他ディレクトリの相対順序を保ち、専用のnative binだけをwrapperより後へ置く。"""
    entries = value.split(";")
    wrapper_key, native_key = winutils.path_key(str(wrapper)), winutils.path_key(str(native))
    wrapper_index = next((index for index, entry in enumerate(entries) if winutils.path_key(entry) == wrapper_key), None)
    native_indices = [index for index, entry in enumerate(entries) if winutils.path_key(entry) == native_key]
    if wrapper_index is None:
        entries.insert(native_indices[0] if native_indices else len(entries), str(wrapper))
    elif native_indices and min(native_indices) < wrapper_index:
        native_entries = [entries[index] for index in native_indices]
        entries = [entry for entry in entries if winutils.path_key(entry) != native_key]
        wrapper_index = next(index for index, entry in enumerate(entries) if winutils.path_key(entry) == wrapper_key)
        entries[wrapper_index + 1 : wrapper_index + 1] = native_entries
    return ";".join(entries)


def _visible_bin_dir() -> Path:
    """公式インストーラーが可視コマンドを置くディレクトリを返す。"""
    value = os.environ.get("CODEX_INSTALL_DIR")
    if value:
        return Path(value)
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "Programs" / "OpenAI" / "Codex" / "bin"
    return Path.home() / ".local" / "bin"


def _nvm_bin_directories() -> tuple[Path, ...]:
    """nvmがバージョン別にnpmの実行ファイルを置く、実在するディレクトリを返す。"""
    nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))
    versions = nvm_dir / "versions" / "node"
    if not versions.is_dir():
        return ()
    return tuple(sorted(path for path in versions.glob("*/bin") if path.is_dir()))


def _find_mise() -> Path | None:
    """PATH上のmiseの絶対パスを返す。"""
    mise_name = shutil.which("mise")
    return Path(mise_name).absolute() if mise_name is not None else None


def _remove_mise_versions() -> tuple[list[str], bool]:
    """mise管理版が実在する場合だけ全版を除去し、失敗の説明と除去有無を返す。"""
    mise = _find_mise()
    if mise is None:
        return [], False
    target = f"npm:{_PACKAGE}"
    listing = common.run_subprocess([str(mise), "ls", "--json", target], timeout=common.COMMAND_TIMEOUT, tag="mise")
    if listing is None or listing.returncode != 0:
        return [f"mise版の一覧取得に失敗: {common.format_cli_error(listing)}"], False
    try:
        listed = json.loads(listing.stdout or "")
    except json.JSONDecodeError as error:
        return [f"mise版の一覧解析に失敗: {error}"], False
    if not isinstance(listed, list):
        return ["mise版の一覧が配列ではない"], False
    # `mise ls`はmise.toml定義済みで未導入の版も列挙する。
    # 未導入時に`uninstall`を呼ぶと警告が出るため、導入済みの要素だけを数える。
    if not any(isinstance(entry, dict) and entry.get("installed") for entry in listed):
        return [], False
    removal = common.run_subprocess([str(mise), "uninstall", "--all", "--yes", target], timeout=_COMMAND_TIMEOUT, tag="mise")
    if removal is None or removal.returncode != 0:
        return [f"mise版の削除に失敗: {common.format_cli_error(removal)}"], False
    return [], True


def _find_orphaned_mise_shims() -> tuple[list[str], list[Path]]:
    """miseがCodexを提供していない状態で実在するCodex中継を返す。"""
    mise = _find_mise()
    if mise is None:
        return [], []
    listing = common.run_subprocess([str(mise), "ls", "--json"], timeout=common.COMMAND_TIMEOUT, tag="mise")
    if listing is None or listing.returncode != 0:
        return [f"mise全体の一覧取得に失敗: {common.format_cli_error(listing)}"], []
    try:
        listed = json.loads(listing.stdout or "")
    except json.JSONDecodeError as error:
        return [f"mise全体の一覧解析に失敗: {error}"], []
    if not isinstance(listed, dict):
        return ["mise全体の一覧がオブジェクトではない"], []
    if f"npm:{_PACKAGE}" in listed:
        return [], []
    names = ("codex", "codex.exe") if sys.platform == "win32" else ("codex",)
    shims = [shim for directory in common.mise_shim_directories() for name in names if (shim := directory / name).is_file()]
    return [], shims


def _reshim_mise() -> list[str]:
    """mise管理のshimを再生成し、失敗の説明を返す。"""
    mise = _find_mise()
    if mise is None:
        return []
    reshim = common.run_subprocess([str(mise), "reshim", "--force"], timeout=common.COMMAND_TIMEOUT, tag="mise")
    if reshim is None or reshim.returncode != 0:
        return [f"mise reshimに失敗: {common.format_cli_error(reshim)}"]
    return []
