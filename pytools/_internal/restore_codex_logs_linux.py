"""Codex診断ログDBを共有メモリーから通常ストレージへ安全に復元する。

`chezmoi apply`後処理から呼ばれ、旧配置が所有する3ファイルだけを対象とする。
復元と共有メモリー側の回収を別のpost-apply実行へ分け、競合時は両方を保持する。
"""

import hashlib
import logging
import os
import pathlib
import shutil
import sys
import tempfile
from collections.abc import Iterable

import psutil

from pytools._internal import log_format

logger = logging.getLogger(__name__)

_DATABASE_NAMES = ("logs_2.sqlite", "logs_2.sqlite-wal", "logs_2.sqlite-shm")
_SHM_ROOT = pathlib.Path("/dev/shm")
_COPY_BUFFER_SIZE = 1024 * 1024
_ACCESS_DENIED = object()


def run(
    *,
    home_dir: pathlib.Path | None = None,
    shm_root: pathlib.Path = _SHM_ROOT,
) -> bool:
    """管理対象の診断ログを通常ストレージへ復元し、安全な後続実行で旧targetを回収する。"""
    if sys.platform != "linux":
        return False
    if _codex_is_running():
        logger.warning(log_format.format_status("codex-logs", "Codexが稼働中のため通常ストレージへの復元を延期"))
        return False

    codex_dir = (home_dir or pathlib.Path.home()) / ".codex"
    pairs = _database_pairs(codex_dir, shm_root)
    states = {home_path: _home_state(home_path, target_path) for home_path, target_path in pairs}
    unrelated = [path for path, state in states.items() if state == "unrelated"]
    if unrelated:
        logger.warning(
            log_format.format_status(
                "codex-logs",
                "管理対象外のpathがあるため全体を変更せず復元を延期: " + ", ".join(map(str, unrelated)),
            )
        )
        return False

    conflicting = [
        (home_path, target_path)
        for home_path, target_path in pairs
        if states[home_path] == "regular" and target_path.is_file() and not _files_equal(home_path, target_path)
    ]
    if conflicting:
        snapshot = _save_conflict_snapshot(codex_dir, pairs)
        _warn_conflict(codex_dir, pairs, snapshot)
        return False

    if all(state == "regular" for state in states.values()):
        targets = [target_path for _, target_path in pairs if target_path.is_file()]
        if targets:
            for target_path in targets:
                target_path.unlink()
            logger.info(log_format.format_status("codex-logs", f"復元済みの共有メモリー側DBを回収: {len(targets)}件"))
            return True
        return False

    changed = False
    for home_path, target_path in pairs:
        if states[home_path] == "managed" and not target_path.exists():
            home_path.unlink()
            changed = True

    restore_pairs = [
        (home_path, target_path)
        for home_path, target_path in pairs
        if states[home_path] in {"managed", "missing"} and target_path.is_file()
    ]
    if not restore_pairs:
        return changed

    codex_dir.mkdir(parents=True, exist_ok=True)
    required_bytes = sum(target_path.stat().st_size for _, target_path in restore_pairs)
    available_bytes = shutil.disk_usage(codex_dir).free
    if available_bytes < required_bytes:
        logger.warning(
            log_format.format_status(
                "codex-logs",
                f"通常ストレージの空き容量不足により復元を延期: {codex_dir} "
                f"（空き{available_bytes}バイト・必要{required_bytes}バイト）",
            )
        )
        return changed

    temporary_paths = _copy_to_temporary_files(restore_pairs)
    try:
        if _codex_is_running():
            logger.warning(
                log_format.format_status("codex-logs", "コピー中にCodexの起動を検知したため通常ストレージへの置換を延期")
            )
            return changed
        for home_path, _ in restore_pairs:
            os.replace(temporary_paths[home_path], home_path)
            temporary_paths.pop(home_path)
            changed = True
        _fsync_directory(codex_dir)
    finally:
        for temporary_path in temporary_paths.values():
            temporary_path.unlink(missing_ok=True)

    logger.info(log_format.format_status("codex-logs", f"通常ストレージへ復元: {len(restore_pairs)}件"))
    return changed


def _database_pairs(codex_dir: pathlib.Path, shm_root: pathlib.Path) -> tuple[tuple[pathlib.Path, pathlib.Path], ...]:
    """ホームディレクトリ側のパスと、旧機構が所有する共有メモリー側の`target`を対応付ける。"""
    uid = os.getuid()
    return tuple((codex_dir / name, shm_root / f"codex-{uid}-{name}") for name in _DATABASE_NAMES)


def _home_state(home_path: pathlib.Path, target_path: pathlib.Path) -> str:
    """ホームディレクトリ側のパスを、管理対象のsymlink（`managed`）、`regular`、`missing`、管理対象外へ分類する。"""
    if home_path.is_symlink():
        return "managed" if home_path.readlink() == target_path else "unrelated"
    if home_path.is_file():
        return "regular"
    if not home_path.exists():
        return "missing"
    return "unrelated"


def _codex_is_running() -> bool:
    """Codex候補プロセス又は情報取得不能なプロセスがあれば安全側で真を返す。"""
    try:
        processes: Iterable[psutil.Process] = psutil.process_iter(
            ["name", "exe", "cmdline"],
            ad_value=_ACCESS_DENIED,
        )
        for process in processes:
            try:
                info = getattr(process, "info", None)
            except (psutil.AccessDenied, psutil.ZombieProcess):
                return True
            except psutil.NoSuchProcess:
                continue
            if not isinstance(info, dict):
                return True
            if any(info.get(key) is _ACCESS_DENIED for key in ("name", "exe", "cmdline")):
                return True
            cmdline = info.get("cmdline")
            cmdline_values = cmdline if isinstance(cmdline, (list, tuple)) else []
            values = [info.get("name"), info.get("exe"), *cmdline_values]
            if any(_is_codex_process_value(value) for value in values if isinstance(value, str)):
                return True
    except (psutil.AccessDenied, psutil.ZombieProcess):
        return True
    return False


def _is_codex_process_value(value: str) -> bool:
    """実行名又はcommand line要素がCodex launcher/packageを示すか判定する。"""
    normalized = value.replace("\\", "/").lower()
    stem = pathlib.PurePosixPath(normalized).name.removesuffix(".exe")
    return stem == "codex" or stem.startswith("codex-") or "@openai/codex" in normalized


def _copy_to_temporary_files(
    restore_pairs: list[tuple[pathlib.Path, pathlib.Path]],
) -> dict[pathlib.Path, pathlib.Path]:
    """全`target`をホームディレクトリと同じディレクトリの所有者限定一時ファイルへコピーする。"""
    temporary_paths: dict[pathlib.Path, pathlib.Path] = {}
    try:
        for home_path, target_path in restore_pairs:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{home_path.name}.restore-",
                dir=home_path.parent,
                delete=False,
            ) as output:
                temporary_paths[home_path] = pathlib.Path(output.name)
                with target_path.open("rb") as source:
                    shutil.copyfileobj(source, output, length=_COPY_BUFFER_SIZE)
                    output.flush()
                    os.fsync(output.fileno())
                    os.fchmod(output.fileno(), 0o600)
    except Exception:
        for temporary_path in temporary_paths.values():
            temporary_path.unlink(missing_ok=True)
        raise
    return temporary_paths


def _files_equal(left: pathlib.Path, right: pathlib.Path) -> bool:
    """2ファイルのサイズと内容が一致するかを逐次比較する。"""
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_chunk = left_stream.read(_COPY_BUFFER_SIZE)
            right_chunk = right_stream.read(_COPY_BUFFER_SIZE)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _snapshot_digest(
    snapshot: pathlib.Path,
    pairs: tuple[tuple[pathlib.Path, pathlib.Path], ...],
) -> str:
    """固定したsnapshotのファイル名、存在状態、内容からSHA-256を算出する。"""
    digest = hashlib.sha256()
    for _, target_path in pairs:
        snapshot_file = snapshot / target_path.name
        name = target_path.name.encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        exists = snapshot_file.is_file()
        digest.update(b"\x01" if exists else b"\x00")
        if exists:
            size = snapshot_file.stat().st_size
            digest.update(size.to_bytes(8, "big"))
            with snapshot_file.open("rb") as stream:
                while chunk := stream.read(_COPY_BUFFER_SIZE):
                    digest.update(chunk)
    return digest.hexdigest()


def _save_conflict_snapshot(
    codex_dir: pathlib.Path,
    pairs: tuple[tuple[pathlib.Path, pathlib.Path], ...],
) -> pathlib.Path:
    """共有メモリー側の全現存ファイルを所有者限定の競合snapshotへ保存する。"""
    codex_dir.mkdir(parents=True, exist_ok=True)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".logs_2-restore-conflict-", dir=codex_dir))
    temporary.chmod(0o700)
    try:
        for _, target_path in pairs:
            if not target_path.is_file():
                continue
            snapshot_file = temporary / target_path.name
            with target_path.open("rb") as source, snapshot_file.open("xb") as output:
                shutil.copyfileobj(source, output, length=_COPY_BUFFER_SIZE)
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), 0o600)
        _fsync_directory(temporary)
        digest = _snapshot_digest(temporary, pairs)
        destination = codex_dir / f"logs_2-restore-conflict-{digest}"
        if destination.exists():
            if _snapshot_matches(destination, temporary):
                shutil.rmtree(temporary)
                return destination
            return temporary
        os.rename(temporary, destination)
        _fsync_directory(codex_dir)
        return destination
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _snapshot_matches(left: pathlib.Path, right: pathlib.Path) -> bool:
    """既存snapshotが新しいsnapshotと同じファイル集合・内容を持つか確認する。"""
    left_names = {path.name for path in left.iterdir() if path.is_file()}
    right_names = {path.name for path in right.iterdir() if path.is_file()}
    return left_names == right_names and all(_files_equal(left / name, right / name) for name in left_names)


def _fsync_directory(path: pathlib.Path) -> None:
    """rename又はreplace後のディレクトリエントリを永続化する。"""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _warn_conflict(
    codex_dir: pathlib.Path,
    pairs: tuple[tuple[pathlib.Path, pathlib.Path], ...],
    snapshot: pathlib.Path,
) -> None:
    """自動復元を止めた競合と、利用者が行う復旧・回収手順を警告する。"""
    home_paths = ", ".join(str(home_path) for home_path, _ in pairs)
    target_paths = ", ".join(str(target_path) for _, target_path in pairs)
    logger.warning(
        log_format.format_status(
            "codex-logs",
            "復元未完了: ホームディレクトリと共有メモリーの内容が一致しないため自動変更を停止。"
            f"home=[{home_paths}] target=[{target_paths}] snapshot={snapshot}。"
            "Codexを停止したままSQLiteのDB・WAL・SHMを一組として照合し、ホームディレクトリへ復旧する。"
            "復旧後に内容を検証し、共有メモリー側targetと競合snapshotを手動で回収する。"
            f"home directory={codex_dir}",
        )
    )
