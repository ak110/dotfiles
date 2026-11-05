"""子Claudeのプラグイン入力を監視し、所有タスクからのreloadに反映する。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from agent_toolkit._agents_server import plugin_root
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import host_homes, process_tree

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")


_LOADABLE_SCOPES = frozenset({"user", "project", "local"})
"""子Claudeが導入先から読み込むscope。これ以外の項目は導入先を持たない場合がある。"""


class PluginReloadError(RuntimeError):
    """プラグインの解決、入力更新、またはCLIへの反映に失敗した。"""


class _InstalledPlugin(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(min_length=1)
    enabled: bool
    scope: str
    install_path: str = Field(alias="installPath", min_length=1)
    project_path: str | None = Field(default=None, alias="projectPath")

    @field_validator("install_path", "project_path")
    @classmethod
    def _absolute_path(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).is_absolute():
            raise ValueError("Claudeの導入情報には絶対パスが必要")
        return value


class _Changes(FileSystemEventHandler):
    def __init__(self, owner: ClaudePluginReloader) -> None:
        self.owner = owner

    def on_any_event(self, event: FileSystemEvent) -> None:
        """変更通知をイベントループへ配送する。"""
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        if event.is_directory and event.event_type == "modified":
            return
        paths = [Path(os.fsdecode(event.src_path))]
        if destination := getattr(event, "dest_path", ""):
            paths.append(Path(os.fsdecode(destination)))
        if any(self.owner.matches(path) for path in paths):
            self.owner.loop.call_soon_threadsafe(self.owner.mark_changed)


class _LoadedPlugin(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    source: str = Field(min_length=1)
    version: str | None = None


class _ReloadResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    plugins: list[_LoadedPlugin]
    error_count: int = Field(ge=0)


class ClaudePluginReloader:
    """SDK接続ごとの入力と監視threadを所有する。SDK操作は呼出元が直列化する。"""

    def __init__(self, options: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
        self.options = options
        self.loop = loop
        self._changed = asyncio.Event()
        self._generation = 0
        self._applied_generation = 0
        self._closed = False
        self._observer: Any = None
        self._roots: set[Path] = set()
        self._files: set[Path] = set()
        self._area: Path | None = None
        self._mirror: Path | None = None
        self._toolkit_index: int | None = None
        self._source: Path | None = None
        self._versioned = False
        self._reload_pending = False
        self.updated = False

    @property
    def dirty(self) -> bool:
        """未反映の変更通知を保持しているかを返す。"""
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
        """所有している監視と入力を終了させる。"""
        await self.close()

    async def prepare(self) -> None:
        """初期入力と監視を用意し、必要なtoolkit pathだけを置換する。"""
        await run_file_operation(self._prepare)

    def _prepare(self) -> None:
        for index, item in enumerate(self.options.get("plugins", [])):
            root = Path(item["path"])
            manifest = json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
            if manifest.get("name") == "agent-toolkit":
                if self._toolkit_index is not None:
                    raise PluginReloadError("明示toolkitが複数ある。SDK options.pluginsを確認する")
                self._toolkit_index = index
                self._source = plugin_root.plugin_source_root(root).resolve()
                # 元の版が既に消えていても、保持複製のmanifestで版付き配置を判定する。
                version = manifest.get("version")
                self._versioned = isinstance(version, str) and self._source.name == version
        installed = self._inventory()
        source = self._select_source(installed)
        if self._versioned:
            self._area = managed_temp.create_output_temp("claude-plugin-input")
            self._mirror = self._area / "agent-toolkit"
            self._switch(self._stage(source))
            assert self._toolkit_index is not None
            plugins = [dict(item) for item in self.options["plugins"]]
            plugins[self._toolkit_index]["path"] = str(self._mirror)
            self.options["plugins"] = plugins
        self._set_watch_paths(installed, source)
        self._observer = Observer()
        self._schedule()
        self._observer.start()

    async def wait_for_change(self) -> None:
        """連続した書換えを短い時間窓で集約し、所有タスクを起こす。"""
        await self._changed.wait()
        while True:
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=0.2)
            except TimeoutError:
                return

    def mark_changed(self) -> None:
        """Observer threadから配送された変更をloop上で記録する。"""
        if not self._closed:
            self._generation += 1
            self._changed.set()

    def request_reload(self) -> None:
        """debounceを終えた変更を、次のidleまで保持する。"""
        self._reload_pending = True

    async def reload_if_idle(self, client: Any, session: SessionState | None) -> None:
        """接続の所有タスクから呼び、進行中turnの終了後にだけ再読み込みする。"""
        if (
            not self._reload_pending
            or session is None
            or not (session.cli_turn_state == "idle" or session.cli_turn_state is None and session.terminal)
        ):
            return
        self._reload_pending = False
        try:
            await self.reload(client)
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
        """入力に関係するイベントだけを通知対象にする。"""
        if any(part in plugin_root.STABLE_PLUGIN_ROOT_EXCLUDED for part in path.parts):
            return False
        if self._area is not None and path.is_relative_to(self._area):
            return False
        return any(path.is_relative_to(root) or root.is_relative_to(path) for root in self._roots) or any(
            path == file or file.is_relative_to(path) for file in self._files
        )

    def _inventory(self) -> list[_InstalledPlugin]:
        command = [str(self.options.get("cli_path") or "claude")]
        if (sources := self.options.get("setting_sources")) is not None:
            command.extend(["--setting-sources", ",".join(sources)])
        if (settings := self.options.get("settings")) is not None:
            command.extend(["--settings", str(settings)])
        command.extend(["plugin", "list", "--json"])
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
        if result.returncode:
            raise PluginReloadError(
                f"Claudeの導入一覧を取得できない（終了コード{result.returncode}）。"
                "同じcwdと設定でclaude plugin list --jsonを確認する"
            )
        # synced scopeなどCLIが読み込まない項目は空のinstallPathを返すため、読込対象のscopeだけを検証する。
        entries = TypeAdapter(list[dict[str, Any]]).validate_json(result.stdout)
        items = [_InstalledPlugin.model_validate(entry) for entry in entries if entry.get("scope") in _LOADABLE_SCOPES]
        cwd = Path(self.options["cwd"]).resolve()
        sources = self.options.get("setting_sources")
        return [
            item
            for item in items
            if item.enabled
            and (sources is None or item.scope in sources)
            and (
                item.scope == "user"
                or item.scope in {"project", "local"}
                and item.project_path is not None
                and Path(item.project_path).resolve() == cwd
            )
        ]

    def _select_source(self, installed: list[_InstalledPlugin]) -> Path | None:
        if not self._versioned:
            return self._source
        candidates = {Path(item.install_path).resolve() for item in installed if item.id.startswith("agent-toolkit@")}
        if len(candidates) != 1:
            raise PluginReloadError("有効toolkitの導入先が一意でない。同じcwdと設定でclaude plugin list --jsonを確認する")
        source = candidates.pop()
        if not source.is_dir():
            raise PluginReloadError(f"toolkitの導入先が存在しない: {source}。Claudeの導入状態を確認する")
        return source

    def _stage(self, source: Path | None) -> Path:
        assert self._area is not None and source is not None
        generation = Path(tempfile.mkdtemp(prefix="generation-", dir=self._area))
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
                configuration = json.loads(mcp_path.read_text(encoding="utf-8"))
                runtime = plugin_root.SERVER_PLUGIN_ROOT
                server = json.loads((runtime / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["agents_server"]
                # CLIのskill/modsだけを更新し、接続中のMCPが使う実行基盤は起動版へ固定する。
                server["args"] = [argument.replace("${CLAUDE_PLUGIN_ROOT}", str(runtime)) for argument in server["args"]]
                configuration["mcpServers"]["agents_server"] = server
                mcp_path.write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")
        except BaseException:
            shutil.rmtree(generation)
            raise
        return generation

    def _switch(self, generation: Path) -> None:
        assert self._area is not None and self._mirror is not None
        previous = self._area / "previous"
        if self._mirror.exists():
            os.replace(self._mirror, previous)
        try:
            # 呼出元がidleとSDK要求を直列化する。完成版だけを固定pathへ移し、失敗時に戻す。
            os.replace(generation, self._mirror)
        except BaseException:
            if previous.exists():
                os.replace(previous, self._mirror)
            raise

    def _restore(self) -> None:
        assert self._area is not None and self._mirror is not None
        previous = self._area / "previous"
        if previous.exists():
            if self._mirror.exists():
                shutil.rmtree(self._mirror)
            os.replace(previous, self._mirror)

    def _set_watch_paths(self, installed: list[_InstalledPlugin], source: Path | None) -> None:
        self._roots = {Path(item.install_path).resolve() for item in installed}
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

    def _schedule(self) -> None:
        handler = _Changes(self)
        watches: dict[Path, bool] = {}
        for root in self._roots:
            if root.is_dir():
                watches[root] = True
        for target in self._roots | self._files:
            parent = target.parent
            while not parent.is_dir():
                parent = parent.parent
            watches.setdefault(parent, False)
        for path, recursive in watches.items():
            self._observer.schedule(handler, str(path), recursive=recursive)

    async def reload(self, client: Any) -> dict[str, Any]:
        """完成した入力へ切替え、SDKのreload応答を検証して変更を確定する。"""
        generation = self._generation
        installed = await run_file_operation(self._inventory)
        source = self._select_source(installed)
        staged: Path | None = None
        try:
            if self._mirror is not None:
                staged = await run_file_operation(self._stage, source)
                await run_file_operation(self._switch, staged)
            expected, required = await run_file_operation(self._expected_plugins, installed)
            # SDK 0.2.177は専用public methodを持たない。private APIへの依存をここへ限定する。
            response = await client._query._send_control_request({"subtype": "reload_plugins"})  # pylint: disable=protected-access
            self._validate_response(response, expected, required)
        except BaseException:
            if self._mirror is not None:
                await run_file_operation(self._restore)
            if staged is not None and staged.exists():
                await run_file_operation(shutil.rmtree, staged)
            raise
        if self._area is not None:
            await run_file_operation(shutil.rmtree, self._area / "previous")
        self._set_watch_paths(installed, source)
        await run_file_operation(self._reschedule)
        self._applied_generation = generation
        self.updated = True
        if self.dirty:
            self._changed.set()
        else:
            self._changed.clear()
        return response

    def _expected_plugins(self, installed: list[_InstalledPlugin]) -> tuple[dict[Path, tuple[str, str | None]], set[Path]]:
        roots = {Path(item.install_path).resolve() for item in installed}
        # 明示toolkitは固定入力で検証する。導入版との同名重複を残すかはCLIが判断する。
        required = {
            Path(item.install_path).resolve()
            for item in installed
            if self._toolkit_index is None or not item.id.startswith("agent-toolkit@")
        }
        for item in self.options.get("plugins", []):
            root = Path(item["path"]).resolve()
            roots.add(root)
            required.add(root)
        expected: dict[Path, tuple[str, str | None]] = {}
        for root in roots:
            manifest = json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
            name, version = manifest.get("name"), manifest.get("version")
            if not isinstance(name, str) or not name or version is not None and not isinstance(version, str):
                raise PluginReloadError(f"プラグインのmanifestが不正: {root}。nameとversionを確認する")
            expected[root] = name, version
        return expected, required

    def _validate_response(self, response: Any, expected: dict[Path, tuple[str, str | None]], required: set[Path]) -> None:
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
        for item in result.plugins:
            if item.path == "builtin":
                continue
            path = Path(item.path)
            if not path.is_absolute():
                raise PluginReloadError("reload_pluginsが相対pathを返した。CLIの診断ログを確認する")
            path = path.resolve()
            if expected.get(path) != (item.name, item.version):
                raise PluginReloadError(
                    f"reload_pluginsのプラグイン名・版・pathが入力と一致しない: {item.name}。導入状態を確認する"
                )
            loaded.add(path)
        if missing := required - loaded:
            raise PluginReloadError(
                f"reload_pluginsの応答に必要なプラグインがない: {sorted(map(str, missing))}。導入状態を確認する"
            )

    def _reschedule(self) -> None:
        self._observer.unschedule_all()
        self._schedule()

    async def close(self, *, retain_input: bool = False) -> None:
        """監視threadの終了を待ち、所有している入力複製を回収する。"""
        self._closed = True
        try:
            if self._observer is not None:
                await run_file_operation(self._stop_observer)
        finally:
            if self._area is not None and not retain_input:
                await run_file_operation(shutil.rmtree, self._area)
                self._area = None

    def _stop_observer(self) -> None:
        self._observer.stop()
        if self._observer.ident is not None:
            self._observer.join()
