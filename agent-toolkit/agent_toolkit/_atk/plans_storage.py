"""計画ファイルの内容・日時を保つ保存と作業側の回収。"""

from __future__ import annotations

import os
import pathlib
import re
import tempfile
from collections.abc import Iterable

from agent_toolkit._atk import plans_bundle as _plans_bundle
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._plan import locations as _plan_file


def copy_working_bundle(
    private_notes: pathlib.Path,
    relative_main: pathlib.Path,
    working_bundle: tuple[pathlib.Path, ...],
) -> tuple[tuple[pathlib.Path, ...], dict[pathlib.Path, tuple[tuple[int, int], bytes]]]:
    """作業バンドルを`private-notes/plans/`へ排他的に複製し、回収用snapshotを返す。"""
    destination_directory = _plan_file.new_plans_root(private_notes) / relative_main.parent
    destination_directory.mkdir(parents=True, exist_ok=True)
    snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]] = {}
    destinations: list[pathlib.Path] = []
    for source in working_bundle:
        source_stat = source.stat(follow_symlinks=False)
        content = source.read_bytes()
        snapshots[source] = ((source_stat.st_dev, source_stat.st_ino), content)
        destination = destination_directory / source.name
        if destination.exists():
            if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != content:
                raise _wi_web_input.WebInputError(
                    _plans_bundle.SAVED_BUNDLE_CONFLICT_MESSAGE.format(destination=destination),
                    next_action=_plans_bundle.SAVED_BUNDLE_CONFLICT_NEXT_ACTION,
                )
            destinations.append(destination)
            continue
        descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{source.name}.", suffix=".tmp", dir=destination_directory)
        temporary = pathlib.Path(raw_temporary)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != content:
                    raise _wi_web_input.WebInputError(
                        _plans_bundle.SAVED_BUNDLE_CONFLICT_MESSAGE.format(destination=destination),
                        next_action=_plans_bundle.SAVED_BUNDLE_CONFLICT_NEXT_ACTION,
                    ) from None
            if destination.read_bytes() != content:
                raise _wi_web_input.WebInputError(
                    f"保存した計画ファイルの読戻し内容が一致しません: {destination}",
                    next_action=_plans_bundle.READBACK_NEXT_ACTION,
                )
        finally:
            temporary.unlink(missing_ok=True)
        destinations.append(destination)
    return tuple(sorted(destinations)), snapshots


def remove_finalized_working_bundle(
    working_bundle: tuple[pathlib.Path, ...],
    snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]],
    destinations: tuple[pathlib.Path, ...],
    relative_main: pathlib.Path,
    home: pathlib.Path | str | None,
) -> None:
    """保存成功後に変更されていない作業ファイルを日時ごと移す。"""
    for source in working_bundle:
        identity, content = snapshots[source]
        current = source.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity or source.read_bytes() != content:
            raise _wi_web_input.WebInputError(
                f"保存処理中に作業ファイルが変更されたため回収しません: {source}",
                next_action=_plans_bundle.CHANGED_DURING_SAVE_NEXT_ACTION,
            )
    destination_by_name = {path.name: path for path in destinations}
    ordered_sources = sorted(working_bundle, key=lambda path: (path.name == relative_main.name, path.name))
    for source in ordered_sources:
        finalize_plan_file(source, destination_by_name[source.name], snapshots[source][1])
    _plans_bundle.remove_working_residue(working_bundle[0].parent / relative_main.name)
    root = _plan_file.working_plans_root(home).resolve(strict=False)
    for directory in (working_bundle[0].parent, working_bundle[0].parent.parent):
        if directory != root and directory.is_relative_to(root):
            try:
                directory.rmdir()
            except OSError:
                break


def working_snapshots(
    working_bundle: tuple[pathlib.Path, ...],
) -> dict[pathlib.Path, tuple[tuple[int, int], bytes]]:
    """回収前に作業バンドルが一致するかを確かめるためのsnapshotを返す。"""
    snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]] = {}
    for source in working_bundle:
        metadata = source.stat(follow_symlinks=False)
        snapshots[source] = ((metadata.st_dev, metadata.st_ino), source.read_bytes())
    return snapshots


def remove_checked_out_working_bundle(
    working_bundle: tuple[pathlib.Path, ...],
    snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]],
    relative_main: pathlib.Path,
) -> None:
    """保存成功後に変更されていないcheckout作業バンドルを回収する。"""
    for source in working_bundle:
        identity, content = snapshots[source]
        current = source.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity or source.read_bytes() != content:
            raise _wi_web_input.WebInputError(
                f"保存処理中に作業ファイルが変更されたため回収しません: {source}",
                next_action=_plans_bundle.CHANGED_DURING_SAVE_NEXT_ACTION,
            )
    for source in sorted(working_bundle, key=lambda path: (path.name == relative_main.name, path.name)):
        source.unlink()
    _plans_bundle.remove_working_residue(working_bundle[0].parent / relative_main.name)


def bundle_contents(bundle: Iterable[pathlib.Path]) -> dict[str, bytes]:
    """計画バンドルをファイル名からbytesへの写像として返す。"""
    return {path.name: path.read_bytes() for path in bundle}


def update_saved_bundle(
    destination_directory: pathlib.Path,
    saved_bundle: tuple[pathlib.Path, ...],
    working_contents: dict[str, bytes],
) -> tuple[pathlib.Path, ...]:
    """保存側のinodeを維持して作業側の内容へ更新する。

    claude-plans-viewerは作成日時を計画の並び順へ使うため、同名の既存ファイルは
    置換せず書き込み、新規ファイルだけを排他的に作成する。
    """
    saved_by_name = {path.name: path for path in saved_bundle}
    for name, content in working_contents.items():
        destination = destination_directory / name
        if name in saved_by_name:
            destination.write_bytes(content)
        else:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
        if destination.read_bytes() != content:
            raise _wi_web_input.WebInputError(
                f"保存した計画ファイルの読戻し内容が一致しません: {destination}",
                next_action=_plans_bundle.READBACK_NEXT_ACTION,
            )
    for name, path in saved_by_name.items():
        if name not in working_contents:
            path.unlink()
    return tuple(sorted(destination_directory / name for name in working_contents))


def finalize_plan_file(source: pathlib.Path, destination: pathlib.Path, content: bytes) -> None:
    """commit済みの移し先を、移し元の内容と日時を保って確定する。

    移し元の内容が確認時の`content`と一致する場合だけ`os.replace`でinodeごと移す。
    確認から読取までの間に作業ファイルが書き換わった場合は、作業ファイルを移さずに失敗させる。

    `atk serve`の計画ファイル画面は作成日時を計画の並び順へ使う。Linuxには作成日時を設定するAPIが
    無いため、移し元のinodeを`os.replace`で移して作成日時を保つ。
    """
    if source.read_bytes() != content:
        raise _wi_web_input.WebInputError(
            f"保存処理中に作業ファイルが変更されたため回収しません: {source}",
            next_action=_plans_bundle.CHANGED_DURING_SAVE_NEXT_ACTION,
        )
    os.replace(source, destination)


def plan_display_name(relative_main: pathlib.Path) -> str:
    """commitメッセージへ含める計画名を返す。"""
    filename = relative_main.name
    match = re.fullmatch(r"[0-9]{2}-(?P<label>.+)-[0-9a-f]{4}\.md", filename)
    if match is not None:
        return match.group("label")
    migrated = re.fullmatch(r"[0-9]{2}-(?P<label>.+)\.md", filename)
    if migrated is not None:
        return migrated.group("label")
    return relative_main.stem


def checkout_conflict_differences(
    recorded: dict[str, bytes],
    working: dict[str, bytes],
    saved: dict[str, bytes],
    remote: dict[str, bytes],
) -> str:
    """取得時点・作業側の双方と異なる保存元を、相違の種類とともに返す。"""
    differences: list[str] = []
    for name in sorted(recorded.keys() | working.keys() | saved.keys() | remote.keys()):
        if saved.get(name) not in (recorded.get(name), working.get(name)):
            differences.append(f"{name}（保存元が取得時点とも作業側とも異なる）")
        if remote.get(name) not in (recorded.get(name), working.get(name)):
            differences.append(f"{name}（保存元のremoteが取得時点とも作業側とも異なる）")
    return "、".join(differences)


def birth_date(path: pathlib.Path) -> str:
    """保存先の年月を決める日時を実行ホストのローカル日付へ変換する。"""
    try:
        return _plan_file.file_birth_date(path).strftime("%Y/%m/%d")
    except OSError as error:
        raise _wi_web_input.WebInputError(
            str(error), next_action=f"{path}の存在と読み取り権限を確認してから再実行する"
        ) from error
