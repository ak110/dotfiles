"""任意のWeb品質資料を各ホストの公式管理操作で導入する。"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from agent_toolkit._common import dotfiles_mode

from pytools._internal import common

logger = logging.getLogger(__name__)
PLUGIN = "modern-web-guidance@googlechrome"
SOURCE = "GoogleChrome/modern-web-guidance"


def _restore_claude_registration(path: Path, previous: dict) -> None:
    """他pluginと他scopeの現在値を残し、対象user登録だけを元へ戻す。"""
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        current = previous
    if not isinstance(current, dict) or not isinstance(current.get("plugins"), dict):
        current = previous
    plugins = current.setdefault("plugins", {})
    original = previous.get("plugins", {}).get(PLUGIN, [])
    entries = plugins.get(PLUGIN, [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        entries = original
    restored = [entry for entry in entries if entry.get("scope") != "user"]
    restored.extend(entry for entry in original if entry.get("scope") == "user")
    if restored:
        plugins[PLUGIN] = restored
    else:
        plugins.pop(PLUGIN, None)
    if not common.atomic_write_json(path, current, tag="web-guidance"):
        raise OSError(f"検証済みpluginの登録を復元できません: {path}")


def _preserve_cache(host: str, operation: Callable[[], bool]) -> bool:
    """公式CLIが版ディレクトリを置換しても、既存セッションの参照先を保持する。"""
    home_variable = "CODEX_HOME" if host == "codex" else "CLAUDE_CONFIG_DIR"
    home = Path(os.environ.get(home_variable, str(Path.home() / (".codex" if host == "codex" else ".claude"))))
    registry = home / "plugins/installed_plugins.json"
    previous: dict = {"version": 2, "plugins": {}}
    if host == "claude" and registry.exists():
        previous = json.loads(registry.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or not isinstance(previous.get("plugins"), dict):
            raise ValueError("Claude plugin登録を復元できる形式で取得できないため更新を保留します")
        entries = previous["plugins"].get(PLUGIN, [])
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("Claude対象pluginの登録形式が不正なため更新を保留します")
    root = home / "plugins/cache/googlechrome/modern-web-guidance"
    paths = (
        [path for path in root.iterdir() if path.is_dir() and not path.is_symlink()]
        if root.is_dir() and not root.is_symlink()
        else []
    )
    # Codexの公開一覧が示すgit marketplace実体も、upgradeで直接書き換えられる。
    marketplace = home / ".tmp/marketplaces/googlechrome"
    if host == "codex" and marketplace.is_dir() and not marketplace.is_symlink():
        paths.append(marketplace)
    # CLIがcacheやmarketplaceを置換しても、復旧用実体が巻き込まれない場所へ置く。
    saved = Path(tempfile.mkdtemp(prefix=".web-guidance-backup-", dir=home)) if paths else None
    try:
        for number, path in enumerate(paths):
            assert saved is not None
            shutil.copytree(path, saved / str(number), symlinks=True)
        try:
            success = operation()
        except ValueError:
            success = False
        for number, path in enumerate(paths):
            if not success or not path.exists():
                assert saved is not None
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
                shutil.copytree(saved / str(number), path, symlinks=True)
        if not success and host == "claude":
            _restore_claude_registration(registry, previous)
    except OSError as error:
        logger.warning("旧plugin cacheの復旧用実体を保持: %s (%s)。このパスを保持してdotfiles作者へ報告する", saved, error)
        raise
    else:
        if saved is not None:
            shutil.rmtree(saved)
    return success


def _run_agy(executable: Path) -> bool:
    """公式skills CLIの上書きを隔離し、検証できた単一skillだけを配置する。"""
    destination = Path.home() / ".gemini/config/skills/modern-web-guidance"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 配置先と同じfilesystem上でrenameし、失敗中も既存版には触れない。
    with tempfile.TemporaryDirectory(prefix=".web-guidance-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        result = common.run_subprocess(
            [
                str(executable),
                "--yes",
                "skills",
                "add",
                SOURCE,
                "--global",
                "--agent",
                "antigravity",
                "--skill",
                "modern-web-guidance",
                "--yes",
                "--copy",
            ],
            timeout=300,
            tag="web-guidance",
            env_overrides={
                "HOME": str(staging),
                "USERPROFILE": str(staging),
                "DISABLE_TELEMETRY": "1",
                # 隔離homeでも、既存の公開待機と認証設定は元の場所から参照する。
                "NPM_CONFIG_USERCONFIG": os.environ.get("NPM_CONFIG_USERCONFIG", str(Path.home() / ".npmrc")),
            },
        )
        source = staging / ".gemini/config/skills/modern-web-guidance"
        if result is None or result.returncode:
            raise ValueError(f"skills CLIの取得に失敗しました: {common.format_cli_error(result)}")
        if not (source / "SKILL.md").is_file():
            raise ValueError("導入後のskill実体を確認できません")
        backup = staging / "previous"
        had_previous = destination.exists() or destination.is_symlink()
        if had_previous:
            destination.rename(backup)
        try:
            source.rename(destination)
        except OSError:
            if had_previous:
                backup.rename(destination)
            raise
    return True


def _verify_plugin(host: str, execute: Callable[[list[str]], subprocess.CompletedProcess[str] | None]) -> bool:
    """公開一覧の有効状態・版と2スキルの実体を確認する。"""
    result = execute(["plugin", "list", "--json", *(["--marketplace", "googlechrome"] if host == "codex" else [])])
    if result is None or result.returncode:
        raise ValueError(f"導入後の状態を取得できません: {common.format_cli_error(result)}")
    data = json.loads(result.stdout)
    entries = data.get("installed") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("導入後の状態の形式が不正です")
    verified = False
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("pluginId", entry.get("id")) != PLUGIN:
            continue
        version = entry.get("version")
        path = entry.get("installPath", entry.get("path"))
        source = entry.get("source")
        if not isinstance(path, str) and isinstance(source, dict):
            path = source.get("path")
        if entry.get("enabled") is True and isinstance(version, str) and version and isinstance(path, str):
            root = Path(path)
            verified = all(
                (root / "skills" / name / "SKILL.md").is_file() for name in ("modern-web-guidance", "chrome-extensions")
            )
        if verified:
            break
    if not verified:
        raise ValueError("導入後の有効状態・版・2スキルの実体を確認できません")
    return True


def run(host: Literal["claude", "codex", "antigravity"]) -> bool:
    """fullだけを対象にし、失敗や無効状態を他の更新へ伝播させない。"""
    if dotfiles_mode.is_minimal():
        return False
    # Nodeはmise shimが唯一の提供元になり得る。CLI本体は従来のnative探索を使う。
    executable = shutil.which("npx") if host == "antigravity" else common.resolve_executable(host)
    if executable is None:
        return False
    executable = Path(executable)

    def execute(args: list[str]) -> subprocess.CompletedProcess[str] | None:
        return common.run_subprocess(
            [str(executable), *args],
            timeout=300,
            tag="web-guidance",
            env_overrides={"DISABLE_TELEMETRY": "1"},
        )

    def command(args: list[str]) -> bool:
        result = execute(args)
        if result is None or result.returncode != 0:
            logger.warning("Modern Web Guidance CLI: %s: %s", args, common.format_cli_error(result))
        return result is not None and result.returncode == 0

    try:
        if host == "antigravity":
            changed = _run_agy(executable)
        else:
            result = execute(["plugin", "list", "--json", *(["--marketplace", "googlechrome"] if host == "codex" else [])])
            if result is None or result.returncode != 0:
                raise ValueError(f"導入状態を取得できません: {common.format_cli_error(result)}")
            data = json.loads(result.stdout)
            entries = data.get("installed") if host == "codex" and isinstance(data, dict) else data
            if not isinstance(entries, list):
                raise ValueError("導入状態の形式が不正です")
            installed = [
                entry for entry in entries if isinstance(entry, dict) and entry.get("pluginId", entry.get("id")) == PLUGIN
            ]
            if any(entry.get("enabled") is False for entry in installed):
                return False
            result = execute(["plugin", "marketplace", "list", "--json"])
            if result is None or result.returncode != 0:
                raise ValueError(f"marketplace一覧を取得できません: {common.format_cli_error(result)}")
            data = json.loads(result.stdout)
            markets = data.get("marketplaces") if isinstance(data, dict) else data
            if not isinstance(markets, list):
                raise ValueError("marketplace一覧の形式が不正です")
            registered = any(isinstance(entry, dict) and entry.get("name") == "googlechrome" for entry in markets)
            operation = ["upgrade" if host == "codex" else "update", "googlechrome"] if registered else ["add", SOURCE]
            args = (
                ["plugin", "add", PLUGIN]
                if host == "codex"
                else ["plugin", "update" if installed else "install", PLUGIN, "--scope=user"]
            )
            changed = _preserve_cache(
                host,
                lambda: command(["plugin", "marketplace", *operation]) and command(args) and _verify_plugin(host, execute),
            )
        if not changed:
            raise ValueError("導入または更新に失敗しました")
        return True
    except (ValueError, OSError) as error:
        logger.warning("Modern Web Guidance: %s。既存配置を削除せずagent-basicsを利用する。update-dotfilesで再試行する", error)
        return False
