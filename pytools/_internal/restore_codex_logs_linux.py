"""Codex診断ログDBを共有メモリーから通常ストレージへ安全に復元する。

`chezmoi apply`後処理から呼ばれ、旧配置が所有する3ファイルだけを対象とする。
復元と共有メモリー側の回収を別のpost-apply実行へ分け、競合時は両方を保持する。
"""

import collections
import hashlib
import logging
import os
import pathlib
import shutil
import sys
import tempfile

import psutil

from pytools._internal import log_format

logger = logging.getLogger(__name__)

_DATABASE_NAMES = ("logs_2.sqlite", "logs_2.sqlite-wal", "logs_2.sqlite-shm")
_SHM_ROOT = pathlib.Path("/dev/shm")
_COPY_BUFFER_SIZE = 1024 * 1024
_ACCESS_DENIED = object()
# codex --helpのCommands一覧のうち、常駐して診断ログDBを開き得る起動形を表示対象とする。
_CODEX_LABEL_SUBCOMMANDS = frozenset({"app-server", "exec", "exec-server", "mcp-server", "remote-control"})
# `node <package entry point>`形式でCodexを起動する実行体の名前。
_NODE_EXECUTABLE_NAMES = frozenset({"node", "nodejs"})


def run(
    *,
    home_dir: pathlib.Path | None = None,
    shm_root: pathlib.Path = _SHM_ROOT,
) -> bool:
    """管理対象の診断ログを通常ストレージへ復元し、安全な後続実行で旧targetを回収する。"""
    if sys.platform != "linux":
        return False
    running = _running_codex_processes()
    if running:
        logger.warning(
            log_format.format_status(
                "codex-logs",
                f"Codexが稼働中のため通常ストレージへの復元を延期: {_format_running_processes(running)}",
            )
        )
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
        running = _running_codex_processes()
        if running:
            logger.warning(
                log_format.format_status(
                    "codex-logs",
                    f"コピー中にCodexの起動を検知したため通常ストレージへの置換を延期: {_format_running_processes(running)}",
                )
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


def _running_codex_processes() -> tuple[str, ...]:
    """稼働中と判定したCodexプロセスの表示ラベルを走査順に返し、空タプルで停止中を表す。

    判定対象は自ユーザー所有プロセスに限る。保護対象である共有メモリー側DB
    `/dev/shm/codex-<UID>-logs_2.sqlite`は所有者限定権限であり、復元先の`~/.codex`も
    所有者以外は書き込めないため、他ユーザーのプロセスは保護対象を書き換えられない。
    自ユーザー所有プロセスに限り、判定素材を1つも取得できない場合だけ安全側で稼働中と扱う。

    判定素材は役割で分ける。実行名、実行ファイルパス、command line第1要素は実行体を表す値であり
    `_is_codex_process_value`で判定する。command line第2要素以降はファイルパスやプロンプト文字列など
    任意の値を含むため、実行体の識別規則を適用しない。node系実行体の場合だけ、
    パッケージのentry pointに当たるcommand line第2要素を`_is_codex_argument_value`で判定する。
    """
    uid = os.getuid()
    labels: list[str] = []
    for process in psutil.process_iter(["name", "exe", "cmdline", "uids"], ad_value=_ACCESS_DENIED):
        try:
            info = process.info
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if getattr(info.get("uids"), "real", None) != uid:
            continue
        cmdline, name_value, exe_value = info.get("cmdline"), info.get("name"), info.get("exe")
        cmdline_items = cmdline if isinstance(cmdline, (list, tuple)) else []
        cmdline_values = [value for value in cmdline_items if isinstance(value, str)]
        name = name_value if isinstance(name_value, str) else ""
        exe = exe_value if isinstance(exe_value, str) else ""
        executable_values = [value for value in (name, exe, *cmdline_values[:1]) if value]
        argument_values = [value for value in cmdline_values[1:] if value]
        entry_point = cmdline_values[1] if len(cmdline_values) >= 2 and _is_node_executable(name, exe) else ""
        if not executable_values and not argument_values:
            labels.append(f"pid {process.pid}")
        elif any(_is_codex_process_value(value) for value in executable_values) or _is_codex_argument_value(entry_point):
            labels.append(_process_label(name, exe, cmdline_values, process.pid))
    return tuple(labels)


def _process_label(name: str, exe: str, cmdline_values: list[str], pid: int) -> str:
    """実行名と、許可した第2要素のサブコマンド名だけでラベルを構成する。

    `name`を取得できない場合は、Codexの識別に用いた`exe`又は`cmdline`第1要素から実行名を導く。
    いずれからも実行名を得られない場合だけ`pid <pid>`とする。
    `codex [OPTIONS] [PROMPT]`の通常起動では第2要素が利用者のプロンプトになり得るため、
    完全一致で許可したサブコマンド名以外はラベルへ含めない。
    """
    candidates = (_executable_name(value) for value in (name, exe, cmdline_values[0] if cmdline_values else ""))
    executable = next((candidate for candidate in candidates if candidate), "")
    if not executable:
        return f"pid {pid}"
    if len(cmdline_values) >= 2 and cmdline_values[1] in _CODEX_LABEL_SUBCOMMANDS:
        return f"{executable} {cmdline_values[1]}"
    return executable


def _executable_name(value: str) -> str:
    """パス表記を含み得る値から、拡張子を除いた実行ファイル名を取り出す。"""
    if not value:
        return ""
    return pathlib.PurePosixPath(value.replace("\\", "/")).name.removesuffix(".exe")


def _format_running_processes(labels: tuple[str, ...]) -> str:
    """ラベルごとの件数を数え、ラベル昇順で`<ラベル> (<件数>件)`を連結する。"""
    counts = collections.Counter(labels)
    return ", ".join(f"{label} ({count}件)" for label, count in sorted(counts.items()))


def _is_codex_process_value(value: str) -> bool:
    """実行体を表す値がCodex launcher/packageを示すか判定する。

    適用対象は実行名、実行ファイルパス、command line第1要素に限る。
    `bin/codex-medium`のようなラッパー起動を拾うため`codex-`接頭辞の前方一致を含める。
    """
    normalized = value.replace("\\", "/").lower()
    stem = _executable_name(normalized)
    return stem == "codex" or stem.startswith("codex-") or "@openai/codex" in normalized


def _is_node_executable(name: str, exe: str) -> bool:
    """実行名又は実行ファイルパスがnode系実行体を示すか判定する。"""
    return any(_executable_name(value.replace("\\", "/").lower()) in _NODE_EXECUTABLE_NAMES for value in (name, exe))


def _is_codex_argument_value(value: str) -> bool:
    """node系起動のcommand line第2要素がCodexのパッケージ指定を示すか判定する。

    適用対象は`node <package entry point>`のentry pointに当たる位置に限る。
    引数はファイルパスやプロンプト文字列など任意の値を含み、`@openai/codex`という文字列自体も
    検索語などとして現れ得るため、実行体の識別規則を適用せず、entry point位置での
    `@openai/codex`の包含だけを根拠とする。
    """
    return "@openai/codex" in value.replace("\\", "/").lower()


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
