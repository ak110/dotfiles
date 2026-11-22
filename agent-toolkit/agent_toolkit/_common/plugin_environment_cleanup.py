"""更新後に必要な共有環境と使用中の環境を残し、旧世代を回収する。"""

import json
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

import psutil

from agent_toolkit._common import environment_usage, file_lock, plugin_environment


def _process_references() -> tuple[list[str], bool]:
    """旧launcherを含め同じOSアカウントの参照を1回取得する。"""
    references: list[str] = []
    uncertain = False
    username = psutil.Process().username()
    for process in psutil.process_iter():
        try:
            try:
                owner = process.username()
            except psutil.AccessDenied:
                # 所有者を読めないのは他アカウントやシステムのプロセスであり、同じアカウントの参照の根拠にならない。
                # 不確実扱いにすると、Windowsでは常に存在するこれらのプロセスのために回収が一度も成立しない。
                continue
            if owner != username or process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                continue
            command = process.cmdline()
            references.extend(command)
            # 名前で起動したPythonではargvと実体のどちらにも有効化した仮想環境が出ない。
            if (
                command
                and (name := Path(command[0]).name) == command[0]
                and name.lower().startswith(("python", "pypy"))
                and (active := process.environ().get("VIRTUAL_ENV"))
            ):
                references.append(active)
            # 公開launcherは環境内Pythonの絶対パスをargv[0]へ渡す。
            try:
                references.append(process.exe())
            except psutil.AccessDenied:
                if not command:
                    uncertain = True
        except psutil.NoSuchProcess:
            continue
        except psutil.Error, OSError:
            uncertain = True
    return [os.path.normcase(value) for value in references], uncertain


def _release_reason(target: Path, sources: object, environment: Mapping[str, str], *, python_executable: Path) -> str | None:
    roots = plugin_environment.registered_roots(target)
    if roots:
        for root, digest in roots.items():
            try:
                root.stat()
            except FileNotFoundError:
                continue
            try:
                current = plugin_environment.dependency_digest(root)
            except FileNotFoundError:
                return "登録した配布元の現在の依存環境を確認できない"
            if current == digest:
                return "登録した配布元がまだ同じ依存環境を使う"
        return None
    if isinstance(sources, dict):
        for value in sources:
            root = Path(value)
            if not root.is_absolute():
                continue
            try:
                if (
                    plugin_environment.legacy_project_environment(root, environment, python_executable=python_executable)
                    == target
                ):
                    return None
            except FileNotFoundError:
                continue
    return "更新器の登録と旧キーのどちらも確認できない"


def cleanup(required: Sequence[Path], environment: Mapping[str, str], *, python_executable: Path) -> list[dict[str, object]]:
    """環境ごとの保持・回収理由を返す。個別失敗は他の対象の回収を妨げない。"""
    directory = plugin_environment.runtime_data(environment) / "environments"
    required_set = {path.absolute() for path in required}
    references, uncertain = _process_references()
    results: list[dict[str, object]] = []
    if not directory.is_dir():
        return results
    for target in sorted(directory.iterdir()):
        if target.is_symlink() or not target.is_dir():
            continue
        row: dict[str, object] = {"path": str(target), "status": "retained", "sources": {}}
        results.append(row)
        try:
            metadata = target / ".agent-toolkit-sources.json"
            if metadata.is_file():
                row["sources"] = json.loads(metadata.read_text(encoding="utf-8"))
            if target.absolute() in required_set:
                row["reason"] = "更新後の有効版が必要とする環境"
                continue
            prefix = os.path.normcase(str(target.absolute()))
            if uncertain or any(prefix in value for value in references):
                row.update(
                    status="deferred" if uncertain else "retained", reason="使用中またはプロセスの使用状態を確認できない"
                )
                continue
            # prepareと同じinodeの即時排他を取り、構築中の環境も保護する。
            with target.with_name(target.name + ".prepare-lock").open("a+b") as preparation:
                file_lock.acquire_lock(preparation, blocking=False)
                try:
                    with environment_usage.lock(target, exclusive=True):
                        reason = _release_reason(target, row["sources"], environment, python_executable=python_executable)
                        if reason is not None:
                            row["reason"] = reason
                            continue
                        final_references, final_uncertain = _process_references()
                        if final_uncertain or any(prefix in value for value in final_references):
                            row.update(status="deferred", reason="排他取得後に使用の解放を確認できない")
                            continue
                        shutil.rmtree(target)
                    row.update(status="deleted", reason="登録済みで解放された環境、または未使用と確認した旧キーの環境")
                finally:
                    file_lock.release_lock(preparation)
        except (OSError, ValueError, psutil.Error) as error:
            row.update(status="deferred", reason=f"回収を保留: {error}")
    return results
