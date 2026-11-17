"""更新後に必要な共有環境と使用中の環境を残し、旧世代を回収する。"""

import json
import logging
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

import psutil

from agent_toolkit._common import environment_usage, file_lock, plugin_environment

logger = logging.getLogger(__name__)


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
            # 公開launcherは環境内Pythonの絶対パスをargv[0]へ渡す。
            # 継承されたenvironは、Python依存を使用しないアプリにも残るため参照根拠にしない。
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


def cleanup(required: Sequence[Path], environment: Mapping[str, str]) -> list[dict[str, object]]:
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
                row["reason"] = "使用中またはプロセスの使用状態を確認できない"
                continue
            # prepareと同じinodeの即時排他を取り、構築中の環境も保護する。
            with target.with_name(target.name + ".prepare-lock").open("a+b") as preparation:
                file_lock.acquire_lock(preparation, blocking=False)
                try:
                    with environment_usage.lock(target, exclusive=True):
                        shutil.rmtree(target)
                    row.update(status="deleted", reason="必要版・使用プロセス・構築がない旧環境")
                finally:
                    file_lock.release_lock(preparation)
        except (OSError, ValueError, psutil.Error) as error:
            row.update(reason=f"回収を保留: {error}")
    for row in results:
        logger.info("共有依存環境: %s", json.dumps(row, ensure_ascii=False))
    return results
