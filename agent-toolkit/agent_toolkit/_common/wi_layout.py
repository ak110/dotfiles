"""WIの年月固定配置を読む共通のパス境界。"""

import datetime
import pathlib
import re
from collections.abc import Iterator

from agent_toolkit._common.next_action import ActionableError


def entry_path(private_notes: pathlib.Path, filename: str, *, fallback_date: datetime.date | None = None) -> pathlib.Path:
    """名前の日付、既存の保存先、指定された投入日の順で固定保存先を解決する。"""
    if re.fullmatch(r"\d{8}-\d{6}-\d{3,}\.md", filename) is None:
        if pathlib.Path(filename).name == filename and "/" not in filename and "\\" not in filename:
            existing = [path for path in iter_entry_paths(private_notes) if path.name == filename]
            if len(existing) == 1:
                return existing[0]
            if not existing and fallback_date is not None and filename.endswith(".md") and filename not in {".", ".."}:
                return private_notes / f"{fallback_date.year:04d}" / f"{fallback_date.month:02d}" / filename
        raise ActionableError(
            f"WIのファイル名を年月へ解決できない: {filename}",
            next_action="YYYYMMDD-HHMMSS-NNN.md形式のファイル名を指定する",
        )
    try:
        datetime.date.fromisoformat(filename[:8])
    except ValueError as error:
        raise ActionableError(
            f"WIのファイル名の日付が不正: {filename}", next_action="ファイル名の日付を修正して再実行する"
        ) from error
    return private_notes / filename[:4] / filename[4:6] / filename


def iter_entry_paths(private_notes: pathlib.Path, *, legacy_states: tuple[str, ...] = ()) -> Iterator[pathlib.Path]:
    """年月配置と、指定された移行前の状態配置の項目を列挙する。"""
    names: set[str] = set()
    for path in sorted(private_notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")):
        dated = re.fullmatch(r"\d{8}-\d{6}-\d{3,}\.md", path.name)
        if path.name in names or (dated is not None and path != private_notes / path.name[:4] / path.name[4:6] / path.name):
            raise ActionableError(
                f"WIの固定配置が重複しているか、ファイル名の年月と一致しない: {path}",
                next_action="同名の項目を比較し、各ファイルを一意な名前とその年月の保存先へ修正する",
            )
        names.add(path.name)
        yield path
    for state in legacy_states:
        yield from sorted((private_notes / state).glob("*.md"))
