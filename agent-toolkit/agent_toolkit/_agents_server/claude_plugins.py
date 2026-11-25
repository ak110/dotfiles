"""子Claudeのプラグイン入力を監視し、所有タスクからのreloadに反映する。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import watchdog.observers
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.api import ObservedWatch

from agent_toolkit._agents_server import plugin_root
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import host_homes, private_directory, process_tree

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")

_CLAUDE_HOOK_LAUNCHER = '"${CLAUDE_PLUGIN_ROOT}/scripts/launch.py"'
_HOOK_GENERATION_ROOT = 'const generationRoot: string = "";'


def _expand_generation_root(value: Any, root: Path) -> Any:
    if isinstance(value, str):
        return value.replace("${CLAUDE_PLUGIN_ROOT}", str(root))
    if isinstance(value, dict):
        return {key: _expand_generation_root(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_generation_root(item, root) for item in value]
    return value


def _bind_hook_generation(generation: Path) -> None:
    configuration_path = generation / "hooks" / "hooks.json"
    if configuration_path.is_file():
        configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
        launcher = shlex.quote((generation / "scripts" / "launch.py").as_posix())
        for matchers in configuration.get("hooks", {}).values():
            for matcher in matchers:
                for hook in matcher.get("hooks", ()):
                    command = hook.get("command")
                    if isinstance(command, str):
                        hook["command"] = command.replace(_CLAUDE_HOOK_LAUNCHER, launcher)
        configuration_path.write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")
    helper_path = generation / "hooks" / "plugin_launch.ts"
    if helper_path.is_file():
        helper = helper_path.read_text(encoding="utf-8")
        root = json.dumps(generation.as_posix())
        if _HOOK_GENERATION_ROOT in helper:
            helper = helper.replace(_HOOK_GENERATION_ROOT, f"const generationRoot: string = {root};", 1)
        else:
            helper = helper.replace("`${root}/", f"`${{{root}}}/")
            resume_path = generation / "hooks" / "usage_limit_resume.ts"
            if resume_path.is_file():
                resume = resume_path.read_text(encoding="utf-8")
                resume = resume.replace("usageLimitResumePrompt($.plugin.root)", f"usageLimitResumePrompt({root})")
                resume_path.write_text(resume, encoding="utf-8")
        helper_path.write_text(helper, encoding="utf-8")


_LOADABLE_SCOPES = frozenset({"user", "project", "local"})
"""子Claudeが導入先から読み込むscope。これ以外の項目は導入先を持たない場合がある。"""


_IN_USE_DIRECTORY = ".in_use"
"""Claude Code CLIが導入先へ利用中の印として書く一時ファイルの置き場所。

`claude plugin list --json`は実行のたびに各導入先のこの配下へ一時ファイルを作成・改名・削除する（CLI 2.1.319で観測）。
入力の内容ではないため変更通知から外す。外さないと、再読み込みの一覧取得そのものが次の再読み込みを起こし、
一覧取得の所要の周期で再読み込みを繰り返す。
"""


_WatchKey = tuple[Path, bool]
"""監視先のパスと再帰指定の組。"""


class PluginReloadError(RuntimeError):
    """プラグインの解決、入力更新、またはCLIへの反映に失敗した。"""


class _InventoryUnavailable(PluginReloadError):
    pass


class _PluginCleanupError(PluginReloadError):
    pass


class _InstalledPlugin(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(min_length=1)
    install_path: str = Field(alias="installPath", min_length=1)

    @field_validator("install_path")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("Claudeの導入情報には絶対パスが必要")
        return value


def _watch_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not Path(value).is_absolute():
        return None
    try:
        return Path(value).resolve()
    except OSError, RuntimeError, ValueError:
        return None


@dataclass(frozen=True)
class _WatchPlugin:
    install_path: Path


class _Changes(FileSystemEventHandler):
    def __init__(self) -> None:

        self.owners: frozenset[ClaudePluginReloader] = frozenset()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        if event.is_directory and event.event_type == "modified":
            return
        paths = [Path(os.fsdecode(event.src_path))]
        if destination := getattr(event, "dest_path", ""):
            paths.append(Path(os.fsdecode(destination)))
        for owner in self.owners:
            if any(owner.matches(path) for path in paths):
                owner.loop.call_soon_threadsafe(owner.mark_changed)


class _SharedWatch:
    """agents_serverのプロセス内の接続が共有する、プラグイン入力の変更監視。

    watchdogが標準で選ぶObserver（Linuxでは`InotifyObserver`）は監視先ごとにinotifyのインスタンスを確保し、
    その上限はOSアカウント全体で共有される。接続ごとにObserverを持つと、同じ監視先も接続の数だけ確保し、
    数件の並行委譲で上限に届く。同じ監視先（パスと再帰指定の組）は参照数付きで1件だけ登録し、
    最後の参照の解除で登録を外す。全参照が無くなったら監視threadを停止・joinし、資源を残さない。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._observer: Any = None
        self._watches: dict[_WatchKey, tuple[ObservedWatch, _Changes]] = {}

    @property
    def observer(self) -> Any:
        return self._observer

    def acquire(self, owner: ClaudePluginReloader, keys: set[_WatchKey]) -> None:
        """`keys`を`owner`の参照として加え、途中で登録に失敗したらこの呼び出しで加えた参照を戻して送出する。"""
        added: list[_WatchKey] = []
        try:
            with self._lock:
                for key in sorted(keys, key=str):
                    self._add(owner, key)
                    added.append(key)
        except BaseException:
            self.release(owner, set(added))
            raise

    def release(self, owner: ClaudePluginReloader, keys: set[_WatchKey]) -> None:
        """`keys`から`owner`の参照を外し、参照の無くなった監視先とObserverを停止する。

        停止の失敗は監視の縮退と区別し、所有タスクの回収へ渡す`_PluginCleanupError`として送出する。
        """
        try:
            with self._lock:
                for key in keys:
                    entry = self._watches.get(key)
                    if entry is None:
                        continue
                    watch, handler = entry
                    handler.owners = handler.owners - {owner}
                    if not handler.owners:
                        del self._watches[key]
                        self._observer.unschedule(watch)
                if not self._watches and self._observer is not None:
                    # 停止を確認できるまで保持し、後続の解除（空集合の解除を含む）で停止を再試行する。
                    self._observer.stop()
                    if self._observer.ident is not None:
                        self._observer.join()
                    self._observer = None
        except Exception as cleanup_error:
            raise _PluginCleanupError(
                "プラグイン変更監視の停止を確認できない。接続所有タスクの回収結果と例外ログを確認する"
            ) from cleanup_error

    def _add(self, owner: ClaudePluginReloader, key: _WatchKey) -> None:
        if self._observer is None:
            # 監視先を持たない状態で開始し、監視先ごとの資源確保の失敗をその登録の失敗として受ける。
            observer = watchdog.observers.Observer()
            observer.start()
            self._observer = observer
        entry = self._watches.get(key)
        if entry is None:
            path, recursive = key
            handler = _Changes()
            try:
                watch = self._observer.schedule(handler, str(path), recursive=recursive)
            except BaseException:
                # 開始済みObserverへの登録は、emitterの開始に失敗してもhandlerの対応だけを残す。
                with contextlib.suppress(KeyError):
                    self._observer.remove_handler_for_watch(handler, ObservedWatch(str(path), recursive=recursive))
                raise
            entry = self._watches[key] = (watch, handler)
        entry[1].owners = entry[1].owners | {owner}


_shared_watch = _SharedWatch()


class _LoadedPlugin(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    version: str | None = None


class _ReloadResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    plugins: list[Any]
    error_count: int = Field(ge=0)


class ClaudePluginReloader:
    def __init__(self, options: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
        self.options = options
        self.loop = loop
        self._changed = asyncio.Event()
        self._generation = 0
        self._applied_generation = 0
        self._closed = False

        self._watch_keys: set[_WatchKey] | None = None
        self._roots: set[Path] = set()
        self._files: set[Path] = set()
        self._area: Path | None = None
        self._mirror: Path | None = None
        self.resource_root: Path | None = None
        self._toolkit_index: int | None = None
        self._source: Path | None = None
        self._versioned = False
        self._reload_pending = False
        self.updated = False
        # 導入一覧の取得で回収を確認できなかった停止結果。接続の所有タスクが後始末で引き取り、`retry`で再試行する。
        # 子孫・生成停止の未確認理由・直接の子の回収待ちを個別に写さず、停止結果ごと渡して所有状態の欠落を防ぐ。
        self.unreclaimed: list[process_tree.ReclaimError] = []

    @property
    def dirty(self) -> bool:
        return self._generation != self._applied_generation

    async def __aenter__(self) -> ClaudePluginReloader:
        """入力と監視を準備し、失敗時も所有資源を回収する。"""
        try:
            await self.prepare()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def prepare(self) -> None:
        await run_file_operation(self._prepare)
        if self._watch_keys is None:
            self._changed.clear()

    def _prepare(self) -> None:
        for index, item in enumerate(self.options.get("plugins", [])):
            root = Path(item["path"])
            manifest = json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
            if manifest.get("name") == "agent-toolkit":
                if self._toolkit_index is not None:
                    raise PluginReloadError("明示toolkitが複数ある。SDK options.pluginsを確認する")
                self._toolkit_index = index
                self._source = plugin_root.plugin_source_root(root).resolve()

                version = manifest.get("version")
                self._versioned = isinstance(version, str) and self._source.name == version

        try:
            installed = self._inventory()
        except _InventoryUnavailable as error:
            # 一覧が無いと導入先の版を選べない。明示入力を確定済みの入力としてそのまま使い、監視だけを無効にする。
            # 保持複製の元の版（`self._source`）は既に消えている場合があり、複製元にできない。
            self._versioned = False
            self._disable_watch(error)
            return
        source = self._select_source(installed)
        if self._versioned:
            self._area = managed_temp.create_output_temp("claude-plugin-input")
            self._mirror = self._area / "agent-toolkit"
            staged = self._stage(source)
            self._switch(staged)
            self.resource_root = staged
            assert self._toolkit_index is not None
            plugins = [dict(item) for item in self.options["plugins"]]
            plugins[self._toolkit_index]["path"] = str(self._mirror)
            self.options["plugins"] = plugins
        self._set_watch_paths(installed, source)
        try:
            keys = self._watch_targets()
            _shared_watch.acquire(self, keys)
        except OSError as error:
            self._disable_watch(error)
        else:
            self._watch_keys = keys

    async def wait_for_change(self) -> None:
        await self._changed.wait()
        while True:
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=0.2)
            except TimeoutError:
                return

    def mark_changed(self) -> None:
        if not self._closed and self._watch_keys is not None:
            self._generation += 1
            self._changed.set()

    def request_reload(self) -> None:
        self._reload_pending = True

    async def reload_if_idle(self, client: Any, session: SessionState | None) -> None:
        if (
            not self._reload_pending
            or session is None
            or not (session.cli_turn_state == "idle" or session.cli_turn_state is None and session.terminal)
        ):
            return
        self._reload_pending = False
        try:
            await self.reload(client)
        except _PluginCleanupError:
            # 所有資源の停止未確認は任意更新の失敗と分け、次queryを止める。
            raise
        # 更新元・ファイル操作・SDKの失敗は、この任意の更新境界で会話の失敗と分離する。
        # CancelledErrorは所有タスクの終了要求なので伝播させる。
        except Exception:  # pylint: disable=broad-exception-caught
            _LOG.exception(
                "claude_plugin_reload_failed session_id=%s。"
                "この例外ログと同じcwd・設定のclaude plugin list --jsonで更新入力を確認し、"
                "修正後にプラグインを再保存してください",
                session.session_id,
            )
        else:
            _LOG.info("claude_plugins_reloaded session_id=%s", session.session_id)

    def matches(self, path: Path) -> bool:
        if any(part in plugin_root.STABLE_PLUGIN_ROOT_EXCLUDED or part == _IN_USE_DIRECTORY for part in path.parts):
            return False
        if self._area is not None and path.is_relative_to(self._area):
            return False
        return any(path.is_relative_to(root) or root.is_relative_to(path) for root in self._roots) or any(
            path == file or file.is_relative_to(path) for file in self._files
        )

    def _inventory(self) -> list[_InstalledPlugin | _WatchPlugin]:
        command = [str(self.options.get("cli_path") or "claude")]
        if (sources := self.options.get("setting_sources")) is not None:
            command.extend(["--setting-sources", ",".join(sources)])
        if (settings := self.options.get("settings")) is not None:
            command.extend(["--settings", str(settings)])
        command.extend(["plugin", "list", "--json"])
        try:
            result = process_tree.run_with_timeout(
                command,
                cwd=self.options["cwd"],
                env={**os.environ, **self.options.get("env", {})},
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=30,
            )
        except process_tree.ReclaimError as error:
            # 所有資源の停止未確認は任意更新の失敗と分け、所有タスクの回収へ渡す。
            self.unreclaimed.append(error)
            raise _PluginCleanupError(
                "Claudeの導入一覧の子プロセスの停止を確認できない。"
                f"{error}。残存プロセスを終了してから同じcwd・設定のclaude plugin list --jsonを確認する"
            ) from error
        except subprocess.TimeoutExpired as error:
            # 子孫を回収済みの時間上限は、補助監視の任意更新の失敗として縮退する。
            raise _InventoryUnavailable(
                "Claudeの導入一覧が時間上限に達した（子プロセスは回収済み）。同じcwdと設定でclaude plugin list --jsonを確認する"
            ) from error
        except OSError as error:
            raise _InventoryUnavailable(
                f"Claudeの導入一覧を取得できない（{type(error).__name__}）。同じcwdと設定でclaude plugin list --jsonを確認する"
            ) from error
        if result.returncode:
            raise _InventoryUnavailable(
                f"Claudeの導入一覧を取得できない（終了コード{result.returncode}）。"
                "同じcwdと設定でclaude plugin list --jsonを確認する"
            )
        try:
            entries = TypeAdapter(list[Any]).validate_json(result.stdout)
        except ValidationError as error:
            raise _InventoryUnavailable(
                "Claudeの導入一覧の構造が不正。同じcwdと設定でclaude plugin list --jsonを確認する"
            ) from error
        cwd = Path(self.options["cwd"]).resolve()
        sources = self.options.get("setting_sources")
        items: list[_InstalledPlugin | _WatchPlugin] = []
        for entry in entries:
            plugin_id = entry.get("id") if isinstance(entry, dict) else None
            if isinstance(plugin_id, str) and plugin_id.startswith("agent-toolkit@"):
                plugin = self._toolkit_entry(entry, cwd, sources)
            else:
                plugin = self._watch_entry(entry, cwd, sources)
            if plugin is not None:
                items.append(plugin)
        return items

    @staticmethod
    def _toolkit_entry(entry: dict[str, Any], cwd: Path, sources: list[str] | None) -> _InstalledPlugin | None:
        scope = TypeAdapter(str).validate_python(entry.get("scope"), strict=True)
        if scope not in _LOADABLE_SCOPES or sources is not None and scope not in sources:
            return None
        if not TypeAdapter(bool).validate_python(entry.get("enabled"), strict=True):
            return None
        if scope != "user":
            project = TypeAdapter(str | None).validate_python(entry.get("projectPath"), strict=True)
            if project is None or (cwd / project).resolve() != cwd:
                return None
            if not Path(project).is_absolute():
                raise PluginReloadError("利用対象のprojectPathには絶対パスが必要。Claudeの導入状態を確認する")
        return _InstalledPlugin.model_validate(entry)

    @staticmethod
    def _watch_entry(entry: Any, cwd: Path, sources: list[str] | None) -> _WatchPlugin | None:
        if not isinstance(entry, dict):
            _LOG.debug("プラグイン監視の対象外: 一覧項目がオブジェクトでない")
            return None
        scope, enabled = entry.get("scope"), entry.get("enabled")
        if not isinstance(scope, str) or scope not in _LOADABLE_SCOPES or sources is not None and scope not in sources:
            _LOG.debug("プラグイン監視の対象外: scopeが利用対象でない")
            return None
        if enabled is not True:
            _LOG.debug("プラグイン監視の対象外: enabledがtrueでない")
            return None
        if scope != "user" and _watch_path(entry.get("projectPath")) != cwd:
            _LOG.debug("プラグイン監視の対象外: projectPathが対象と一致しない")
            return None
        path = _watch_path(entry.get("installPath"))
        if path is None:
            _LOG.debug("プラグイン監視の対象外: 有効なinstallPathがない")
            return None
        return _WatchPlugin(path)

    def _select_source(self, installed: list[_InstalledPlugin | _WatchPlugin]) -> Path | None:
        if not self._versioned:
            return self._source
        candidates = {Path(item.install_path).resolve() for item in installed if isinstance(item, _InstalledPlugin)}
        if len(candidates) != 1:
            raise PluginReloadError("有効toolkitの導入先が一意でない。同じcwdと設定でclaude plugin list --jsonを確認する")
        source = candidates.pop()
        if not source.is_dir():
            raise PluginReloadError(f"toolkitの導入先が存在しない: {source}。Claudeの導入状態を確認する")
        return source

    def _stage(self, source: Path | None) -> Path:
        assert self._area is not None and source is not None
        generation = private_directory.make_temporary(self._area, "generation-")
        try:
            shutil.copytree(
                source,
                generation,
                dirs_exist_ok=True,
                symlinks=True,
                ignore=shutil.ignore_patterns(*plugin_root.STABLE_PLUGIN_ROOT_EXCLUDED),
            )
            mcp_path = generation / ".mcp.json"
            if mcp_path.is_file():
                configuration = _expand_generation_root(json.loads(mcp_path.read_text(encoding="utf-8")), generation)
                runtime = plugin_root.SERVER_PLUGIN_ROOT
                server = json.loads((runtime / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["agents_server"]
                # CLIのskill/modsだけを更新し、接続中のMCPが使う実行基盤は起動版へ固定する。
                configuration["mcpServers"]["agents_server"] = _expand_generation_root(server, runtime)
                mcp_path.write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")
            _bind_hook_generation(generation)
        except BaseException:
            shutil.rmtree(generation)
            raise
        return generation

    def _switch(self, generation: Path) -> None:
        assert self._area is not None and self._mirror is not None
        candidate = None
        previous = self._area / "previous"
        try:
            candidate = private_directory.make_temporary(self._area, "mirror-")
            shutil.copytree(generation, candidate, dirs_exist_ok=True, symlinks=True)
            if self._mirror.exists():
                os.replace(self._mirror, previous)
            try:
                os.replace(candidate, self._mirror)
            except BaseException:
                if previous.exists():
                    os.replace(previous, self._mirror)
                raise
        except BaseException:
            shutil.rmtree(generation)
            raise
        finally:
            if candidate is not None and candidate.exists():
                shutil.rmtree(candidate)

    def _restore(self) -> None:
        assert self._area is not None and self._mirror is not None
        previous = self._area / "previous"
        if previous.exists():
            rejected = self._area / "rejected"
            if self._mirror.exists():
                os.replace(self._mirror, rejected)
            os.replace(previous, self._mirror)
            if rejected.exists():
                shutil.rmtree(rejected)

    def _set_watch_paths(self, installed: list[_InstalledPlugin | _WatchPlugin], source: Path | None) -> None:
        self._roots = {
            item.install_path if isinstance(item, _WatchPlugin) else Path(item.install_path).resolve() for item in installed
        }
        if source is not None:
            self._roots.add(source)
        env = {**os.environ, **self.options.get("env", {})}
        config = host_homes.claude_config_dir(env)
        self._files = {config / "plugins" / name for name in ("installed_plugins.json", "known_marketplaces.json")}
        sources = self.options.get("setting_sources")
        if sources is None or "user" in sources:
            self._files.add(config / "settings.json")
        cwd = Path(self.options["cwd"]).resolve()
        for directory in (cwd, *cwd.parents):
            for scope, name in (("project", "settings.json"), ("local", "settings.local.json")):
                if sources is None or scope in sources:
                    self._files.add(directory / ".claude" / name)
        settings = self.options.get("settings")
        if settings is not None and not str(settings).lstrip().startswith("{"):
            self._files.add((cwd / str(settings)).resolve())

    def _watch_targets(self) -> set[_WatchKey]:
        watches: dict[Path, bool] = {}
        for root in self._roots:
            if root.is_dir():
                watches[root] = True
        for target in self._roots | self._files:
            if (parent := next((candidate for candidate in target.parents if candidate.is_dir()), None)) is not None:
                watches.setdefault(parent, False)
        return set(watches.items())

    async def reload(self, client: Any) -> dict[str, Any]:
        generation = self._generation
        installed = await run_file_operation(self._inventory)
        source = self._select_source(installed)
        staged: Path | None = None
        try:
            if self._mirror is not None:
                staged = await run_file_operation(self._stage, source)
                await run_file_operation(self._switch, staged)
            expected = await run_file_operation(self._expected_plugins)
            # SDK 0.2.177は専用public methodを持たない。private APIへの依存をここへ限定する。
            response = await client._query._send_control_request({"subtype": "reload_plugins"})  # pylint: disable=protected-access
            self._validate_response(response, expected)
        except BaseException:
            if self._mirror is not None:
                await run_file_operation(self._restore)
            raise
        if staged is not None:
            self.resource_root = staged
        if self._area is not None:
            await run_file_operation(shutil.rmtree, self._area / "previous")
        self._set_watch_paths(installed, source)
        # 監視を無効にした接続では再登録しない。開始時の縮退と同じく、確定済み入力で委譲を続ける契約である。
        if self._watch_keys is not None:
            try:
                await run_file_operation(self._reschedule)
            except OSError as error:
                # SDKへの反映は確定済みなので、監視の再開失敗だけを分離する。
                await run_file_operation(self._disable_watch, error)
        self._applied_generation = generation
        self.updated = True
        if self.dirty and self._watch_keys is not None:
            self._changed.set()
        else:
            self._changed.clear()
        return response

    def _expected_plugins(self) -> dict[Path, tuple[str, str | None]]:
        expected: dict[Path, tuple[str, str | None]] = {}
        for item in self.options.get("plugins", []):
            root = Path(item["path"]).resolve()
            manifest_path = root / ".claude-plugin/plugin.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except FileNotFoundError as error:
                raise PluginReloadError(f"明示プラグインのmanifestがない: {root}。SDK options.pluginsを確認する") from error
            name, version = manifest.get("name"), manifest.get("version")
            if not isinstance(name, str) or not name or version is not None and not isinstance(version, str):
                raise PluginReloadError(f"プラグインのmanifestが不正: {root}。nameとversionを確認する")
            expected[root] = name, version
        return expected

    def _validate_response(self, response: Any, expected: dict[Path, tuple[str, str | None]]) -> None:
        # CLI 2.1.318 / SDK 0.2.177の実応答ではerrors配列の代わりにerror_countを返す。
        try:
            result = _ReloadResponse.model_validate(response)
        except ValidationError as error:
            raise PluginReloadError("reload_pluginsの応答形式が不正。CLIとSDKの対応版を確認する") from error
        if result.error_count:
            raise PluginReloadError(
                f"reload_pluginsが{result.error_count}件のエラーを返した。プラグインとCLIの診断ログを確認する"
            )
        loaded: set[Path] = set()
        for entry in result.plugins:
            path = _watch_path(entry.get("path")) if isinstance(entry, dict) else None
            if path is None or path not in expected:
                _LOG.debug("reload_pluginsの明示入力以外の結果を検証しない")
                continue
            try:
                item = _LoadedPlugin.model_validate(entry)
            except ValidationError as error:
                raise PluginReloadError("reload_pluginsの利用対象が不正。CLIの診断ログを確認する") from error
            if expected[path] != (item.name, item.version):
                raise PluginReloadError(
                    f"reload_pluginsのプラグイン名・版・pathが入力と一致しない: {item.name}。導入状態を確認する"
                )
            loaded.add(path)
        if missing := expected.keys() - loaded:
            raise PluginReloadError(
                f"reload_pluginsの応答に必要なプラグインがない: {sorted(map(str, missing))}。導入状態を確認する"
            )

    def _reschedule(self) -> None:

        assert self._watch_keys is not None
        keys = self._watch_targets()
        _shared_watch.acquire(self, keys - self._watch_keys)
        _shared_watch.release(self, self._watch_keys - keys)
        self._watch_keys = keys

    def _disable_watch(self, error: Exception) -> None:
        if self._watch_keys is not None:
            keys, self._watch_keys = self._watch_keys, None
            _shared_watch.release(self, keys)
        _LOG.warning(
            "プラグイン変更監視を無効にし、確定済み入力で委譲を継続する: %s。"
            "同じcwd・設定のclaude plugin list --jsonとこの診断を確認し、"
            "原因の解消後に新しい委譲を開始すると監視を再開する",
            error,
        )

    async def close(self, *, retain_input: bool = False) -> None:
        self._closed = True
        try:
            # 縮退時の停止失敗で残ったObserverも、ほかの参照が無ければここで停止を再試行する。
            keys, self._watch_keys = self._watch_keys or set(), None
            await run_file_operation(_shared_watch.release, self, keys)
        finally:
            if self._area is not None and not retain_input:
                await run_file_operation(shutil.rmtree, self._area)
                self._area = None
