import argparse
import os
import pathlib
import re
import stat
import uuid
import zlib
from collections.abc import Iterable

from agent_toolkit._atk import config_settings, outcome
from agent_toolkit._atk.wi import entries, sync
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.diagnostic_redaction import redact_secrets

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_RESERVED = re.compile(r"CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]", re.IGNORECASE)
_COUNTER = "cumulative-bytes"


def _valid_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name) and not name.endswith(".") and not _RESERVED.fullmatch(name.split(".")[0]))


def validate_name(name: str) -> None:
    if not _valid_name(name):
        raise ValueError(
            f"添付名に使用できない名前: {name!r}。半角英数字で始め、半角英数字・ピリオド・下線・ハイフンで指定する"
        )


def _safe_stat(path: pathlib.Path, *, directory: bool = False) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or not (
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    ):
        raise ValueError(f"添付の保存先に通常のファイルまたはディレクトリ以外がある: {path}")
    return info


def _directory(private_notes: pathlib.Path, filename: str) -> pathlib.Path:
    if not filename.endswith(".md"):
        raise ValueError("WIのファイル名を指定する")
    stem = filename[:-3]
    validate_name(stem)
    if stem.casefold() == _COUNTER:
        raise ValueError("添付の累計記録と同名のWIには添付できない")
    root = private_notes / "attachments"
    _safe_stat(root, directory=True)
    folder = root / stem
    _safe_stat(folder, directory=True)
    return folder


def paths(private_notes: pathlib.Path, filename: str) -> list[pathlib.Path]:
    if _safe_stat(private_notes / "attachments", directory=True) is None:
        return []
    if not filename.endswith(".md") or not _valid_name(filename[:-3]) or filename[:-3].casefold() == _COUNTER:
        return []
    folder = _directory(private_notes, filename)
    if not folder.exists():
        return []
    result = []
    for item in sorted(folder.iterdir()):
        validate_name(item.name)
        if _safe_stat(item) is not None:
            result.append(item)
    return result


def paths_for_removal(private_notes: pathlib.Path, wi_paths: Iterable[pathlib.Path]) -> list[pathlib.Path]:
    return [item for wi_path in wi_paths for item in paths(private_notes, wi_path.name)]


def _read(path: pathlib.Path, limit: int) -> bytes:
    if _safe_stat(path) is None:
        raise FileNotFoundError(path)
    with path.open("rb") as file:
        return file.read(limit + 1)


def _limit(key: str) -> int:
    value = config_settings.raw_mutable_setting(key)
    config_settings.validate_mutable_setting(key, value)
    return int(value) * 1024 * 1024


def _limit_error(key: str, current: int, limit: int) -> ValueError:
    return ValueError(
        f"添付の上限超過（追加後: {current}バイト、上限: {limit}バイト）。変更する場合は atk config set {key} <MiB> を使う"
    )


def _is_png(data: bytes) -> bool:
    if not data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"):
        return False
    chunks = memoryview(data)
    offset = 8
    has_pixels = False
    while offset + 12 <= len(chunks):
        length = int.from_bytes(chunks[offset : offset + 4], "big")
        end = offset + 8 + length
        if end + 4 > len(chunks):
            return False
        kind = data[offset + 4 : offset + 8]
        if zlib.crc32(chunks[offset + 4 : end]) != int.from_bytes(chunks[end : end + 4], "big"):
            return False
        if kind == b"IDAT":
            has_pixels |= length > 0
        if kind == b"IEND":
            return has_pixels and length == 0 and end + 4 == len(chunks)
        offset = end + 4
    return False


def raster_type(data: bytes) -> str | None:
    if _is_png(data):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 14 and data.endswith(b";"):
        return "image/gif"
    if (
        len(data) >= 20
        and data[:4] == b"RIFF"
        and data[8:16] in (b"WEBPVP8 ", b"WEBPVP8L", b"WEBPVP8X")
        and int.from_bytes(data[4:8], "little") == len(data) - 8
    ):
        return "image/webp"
    return None


def read_attachment(private_notes: pathlib.Path, filename: str, name: str) -> tuple[bytes, str | None]:
    validate_name(name)
    limit = _limit("wi_attachment_file_mib")
    data = _read(_directory(private_notes, filename) / name, limit)
    if len(data) > limit:
        raise _limit_error("wi_attachment_file_mib", len(data), limit)
    return data, raster_type(data)


def previewable(private_notes: pathlib.Path, filename: str, name: str) -> bool:
    try:
        return read_attachment(private_notes, filename, name)[1] is not None
    except FileNotFoundError, ValueError:
        return False


def _check_secrets(data: bytes, name: str) -> None:
    for encoding in ("latin-1", "utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        if redact_secrets(text) != text:
            raise ValueError(f"添付に秘匿値が含まれる: {name}")


def _add(private_notes: pathlib.Path, filename: str, sources: list[pathlib.Path]) -> list[pathlib.Path]:
    folder = _directory(private_notes, filename)
    counter = folder.parent / _COUNTER
    previous = _read(counter, 128).decode("ascii") if _safe_stat(counter) is not None else None
    if previous is not None and not re.fullmatch(r"[0-9]+ [0-9a-f]{32}\r?\n", previous):
        raise ValueError("添付の累計記録が不正です")
    accumulated = int(previous.split()[0]) if previous else 0
    existing = paths(private_notes, filename)
    current = sum(item.stat().st_size for item in existing)
    names = {item.name.casefold() for item in existing}
    pending = {}
    file_key = "wi_attachment_file_mib"
    file_limit = _limit(file_key)
    added = 0
    item_key, total_key = "wi_attachment_item_mib", "wi_attachment_total_mib"
    item_limit, total_limit = _limit(item_key), _limit(total_key)
    for source in sources:
        name = source.name
        validate_name(name)
        if name.casefold() in names:
            raise ValueError(f"同名の添付がある: {name}")
        names.add(name.casefold())
        data = _read(source, file_limit)
        if len(data) > file_limit:
            raise ValueError(f"現在の累計: {accumulated}バイト。{_limit_error(file_key, len(data), file_limit)}")
        added += len(data)
        for key, size, limit in ((item_key, current + added, item_limit), (total_key, accumulated + added, total_limit)):
            if size > limit:
                raise ValueError(f"現在の累計: {accumulated}バイト。{_limit_error(key, size, limit)}")
        _check_secrets(data, name)
        pending[folder / name] = data
    folder.mkdir(parents=True, exist_ok=True)
    written: list[pathlib.Path] = []
    try:
        for path, data in pending.items():
            with path.open("xb") as file:
                written.append(path)
                file.write(data)
        # 同じ増分の別ホストの更新も、同一変更としてrebaseで省かず、競合として扱う。 pyfltr: allow-comment
        atomic_write(counter, f"{accumulated + added} {uuid.uuid4().hex}\n")
    except OSError:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return [*pending, counter]


def cmd_attachments(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    names = entries.validate_named_filenames(private_notes, [args.filename])
    with sync.repo_lock(private_notes):
        sync.push_pending_commits(private_notes)
        sync.pull(private_notes)
        selected, _ = entries.read_named_entries(private_notes, names, target_repo=None)
        if not selected:
            raise ValueError(f"WIが見つからない: {args.filename}")
        filename = selected[0][0].name
        if args.wi_subcommand == "attachments":
            for path in paths(private_notes, filename):
                print(path.relative_to(private_notes).as_posix())
            return
        if args.wi_subcommand == "attach":
            changed = _add(private_notes, filename, args.paths)
        else:
            validate_name(args.name)
            path = _directory(private_notes, filename) / args.name
            if _safe_stat(path) is None:
                raise ValueError(f"添付が見つからない: {args.name}")
            path.unlink()
            changed = [path]
        sync.commit_and_push(
            private_notes,
            f"chore: {args.wi_subcommand} WI files",
            [path.relative_to(private_notes).as_posix() for path in changed],
        )
        outcome.report_success(f"{filename}の添付を更新した")
