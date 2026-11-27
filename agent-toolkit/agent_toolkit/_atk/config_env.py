from __future__ import annotations

import json
import pathlib
from collections.abc import Callable

from agent_toolkit._atk import config_settings
from agent_toolkit._common import env_values, private_notes, runtime_environment
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError
lazy from agent_toolkit._atk import config_shared_migration, git_sync


def path() -> pathlib.Path:
    return private_notes.default_private_notes(runtime_environment.home()).resolve() / config_settings.SHARED_SETTINGS_FILENAME


def update(transform: Callable[[dict[str, str]], dict[str, str]], keys: set[str]) -> bool:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if git_sync.has_remote(target.parent):
        return config_shared_migration.update_shared_settings(target, transform, touched=keys)
    with config_settings.local_config_lock(target):
        current = config_settings.load_shared_settings(target)
        changed = transform(dict(current))
        if changed == current:
            return False
        atomic_write(target, json.dumps(changed, ensure_ascii=False, indent=2) + "\n", fsync=True)
    return True


def save(updates: dict[str, str]) -> bool:
    def transform(current: dict[str, str]) -> dict[str, str]:
        return current | {key: env_values.restore(key, value, current.get(key, "")) for key, value in updates.items()}

    return update(transform, set(updates))


def reference(key: str) -> str:
    return env_values.reference(path(), key)


def source_path(raw: str) -> pathlib.Path:
    caller = runtime_environment.current()
    cwd = caller.cwd if caller is not None else pathlib.Path.cwd()
    source = pathlib.Path(raw)
    if caller is not None and source.parts and source.parts[0] == "~":
        source = runtime_environment.home().joinpath(*source.parts[1:])
    return cwd / source.expanduser()


def imported(target: pathlib.Path) -> bool:
    key = next(key for key, filename in env_values.SETTING_FILES.items() if filename == target.name)
    return target.with_name(target.name + ".imported").is_file() and key in config_settings.load_shared_settings(path())


def migration_status(key: str, local: dict[str, str]) -> str:
    filename = env_values.SETTING_FILES[key]
    target = config_settings.config_dir() / filename
    if target.exists() or local.get(key + "_file"):
        return "旧envの取り込み待ち。診断を確認し、atk serveを再起動するかatk config getで取り込む。"
    if target.with_name(filename + ".imported").is_file():
        return f"取り込み済み。元ファイルは{filename}.importedに退避済み。"
    return "このホストに移行元の固定envファイルはない。共有設定を使用する。"


def preview_values(local: dict[str, str]) -> dict[str, str]:
    values = {}
    for key, filename in env_values.SETTING_FILES.items():
        source = config_settings.config_dir() / filename
        raw = runtime_environment.environment().get(config_settings.config_env_name(key + "_file")) or local.get(key + "_file")
        if not source.exists() and raw:
            source = source_path(raw)
        if source.is_file():
            values[key] = env_values.encode(env_values.read(str(source)))
    return values


def migrate(*, read_only: bool = False) -> None:
    root = config_settings.config_dir()
    files = {key: root / name for key, name in env_values.SETTING_FILES.items() if (root / name).exists()}
    if not files:
        return
    try:
        values: dict[str, str] = {}
        for key, source in files.items():
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"{source.name}を通常ファイルにする")
            values[key] = env_values.encode(env_values.read(str(source)))
            backup = source.with_name(source.name + ".imported")
            if backup.exists() and (backup.is_symlink() or backup.read_bytes() != source.read_bytes()):
                raise ValueError(f"{backup.name}が別内容のため退避できない")

        def transform(current: dict[str, str]) -> dict[str, str]:
            for key, value in values.items():
                if key in current and env_values.parse(current[key]) != env_values.parse(value):
                    raise ValueError(f"{key}と{env_values.SETTING_FILES[key]}が別内容のため上書きしない")
            return current | values

        transform(config_settings.load_shared_settings(path()))
        if read_only:
            return
        update(transform, set(values))
        for source in files.values():
            source.replace(source.with_name(source.name + ".imported"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionableError(
            f"接続envを共有設定へ取り込めない（{type(error).__name__}）",
            next_action=(
                f"{root}のenvファイルをKEY=VALUE形式・読取可能な通常ファイルにする。"
                f"{path()}の対応キーや.importedが既にある場合は内容を確認し、残す値を選んで元ファイルを退避してから再実行する"
            ),
        ) from error
