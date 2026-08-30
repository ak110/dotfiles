"""計画ファイルの保存root・可搬表記・種別判定を扱う共通モジュール。

新規計画はprivate-notes内の`plans/yyyy/MM/`へ保存し、永続参照には
`$(atk config get private_notes)/`を固定接頭辞として用いる。旧`~/.claude/plans/`
直下の既存ファイルと、過去に保存された絶対パスは読み取り互換として受理する。
本モジュールはシェルを起動せず、portable値を通常の相対パスとして検証する。
"""

from __future__ import annotations

import datetime
import os
import pathlib
import re

import platformdirs

PORTABLE_PLAN_PREFIX = "$(atk config get private_notes)/"
"""永続する計画参照に使う固定可搬接頭辞。"""

NEW_PLANS_DIRECTORY = "plans"
"""private-notes直下の新しい計画root名。"""

_FORBIDDEN_NAME_CHARACTERS = set('/\\:*?"<>|')
_CANONICAL_MAIN_RE = re.compile(r"(?P<day>[0-9]{2})-(?P<label>.+)-(?P<token>[0-9a-f]{4})\.md\Z")
_MIGRATED_MAIN_RE = re.compile(r"(?P<day>[0-9]{2})-(?P<legacy_name>.+\.md)\Z")


def private_notes_root(
    private_notes: pathlib.Path | str | None = None,
    *,
    home: pathlib.Path | str | None = None,
) -> pathlib.Path:
    """private-notesのrootを解決する。

    明示値、`AGENT_TOOLKIT_PRIVATE_NOTES`、既存の`~/private-notes`、
    `platformdirs`の順で採用する。root自体を自動作成しない。
    """
    if private_notes is not None:
        return pathlib.Path(private_notes).expanduser()
    override = os.environ.get("AGENT_TOOLKIT_PRIVATE_NOTES")
    if override:
        return pathlib.Path(override).expanduser()
    home_path = pathlib.Path(home).expanduser() if home is not None else pathlib.Path.home()
    default = home_path / "private-notes"
    if default.exists():
        return default
    return pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False)) / "private-notes"


def new_plans_root(private_notes: pathlib.Path | str | None = None) -> pathlib.Path:
    """新しい計画rootの絶対パスを返す。"""
    return private_notes_root(private_notes) / NEW_PLANS_DIRECTORY


def legacy_plans_root(home: pathlib.Path | str | None = None) -> pathlib.Path:
    """旧計画rootの絶対パスを返す。"""
    home_path = pathlib.Path(home).expanduser() if home is not None else pathlib.Path.home()
    return home_path / ".claude" / "plans"


def _resolve(path: pathlib.Path) -> pathlib.Path:
    """存在しないパスも含めて実体基準の絶対パスへ変換する。"""
    return path.expanduser().resolve(strict=False)


def _lexically_under(path: pathlib.Path, root: pathlib.Path) -> bool:
    """symlinkを解決せずにpathがroot配下にあるかを判定する。"""
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    return not any(part in ("", ".", "..") for part in relative.parts)


def _relative_to(path: pathlib.Path, root: pathlib.Path) -> pathlib.PurePath | None:
    """pathがroot配下ならroot相対値を返す。"""
    try:
        return _resolve(path).relative_to(_resolve(root))
    except (OSError, ValueError):
        return None


def _validate_portable_remainder(remainder: str) -> pathlib.PurePosixPath:
    """portable接頭辞後の相対値を検証する。"""
    if not remainder or "$(" in remainder or "\x00" in remainder:
        raise ValueError("計画ファイルの可搬パスが空、または別のシェル式を含んでいます")
    if "\\" in remainder:
        raise ValueError("計画ファイルの可搬パスにWindows区切り文字を指定できません")
    relative = pathlib.PurePosixPath(remainder)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("計画ファイルの可搬パスはprivate-notes配下の相対パスで指定してください")
    return relative


def validate_plan_relative_path(relative_path: pathlib.Path | str) -> pathlib.Path:
    """新plans rootからの相対メイン計画パスを検証して返す。

    形式は`yyyy/MM/dd-{名称}-{小文字16進数4桁}.md`で、年・月・日の実在日付、
    パス文字、名称、16進数を検証する。
    """
    raw = os.fspath(relative_path)
    if not raw or "$(" in raw or "\x00" in raw or "\\" in raw:
        raise ValueError("計画ファイルの相対パスが不正です")
    relative = pathlib.PurePosixPath(raw)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("計画ファイルの相対パスはplans root配下で指定してください")
    if len(relative.parts) != 3:
        raise ValueError("計画ファイルはyyyy/MM/ファイル名の形式で指定してください")
    year_text, month_text, filename = relative.parts
    if not (re.fullmatch(r"[0-9]{4}", year_text) and re.fullmatch(r"[0-9]{2}", month_text)):
        raise ValueError("計画ファイルの年月ディレクトリが不正です")
    match = _CANONICAL_MAIN_RE.fullmatch(filename)
    if match is None:
        raise ValueError("計画ファイル名はdd-{日本語の名称}-{小文字16進数4桁}.mdの形式で指定してください")
    label = match.group("label")
    if not label or any(character in _FORBIDDEN_NAME_CHARACTERS or ord(character) < 0x20 for character in label):
        raise ValueError("計画ファイル名に使用できない文字が含まれています")
    try:
        datetime.date(int(year_text), int(month_text), int(match.group("day")))
    except ValueError as error:
        raise ValueError("計画ファイル名の日付が不正です") from error
    return pathlib.Path(*relative.parts)


def validate_migrated_plan_relative_path(relative_path: pathlib.Path | str) -> pathlib.Path:
    """移行で生成した旧ファイル名の新root相対パスを検証して返す。

    移行先は旧ファイル名を維持するため、正規の新規作成形式にないパスも
    `dd-{旧ファイル名}.md`として存在する。この形式は新規作成には用いず、
    移行済み計画の判定とcommitだけで受理する。
    """
    raw = os.fspath(relative_path)
    if not raw or "$(" in raw or "\x00" in raw or "\\" in raw:
        raise ValueError("移行済み計画ファイルの相対パスが不正です")
    relative = pathlib.PurePosixPath(raw)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("移行済み計画ファイルはplans root配下で指定してください")
    if len(relative.parts) != 3:
        raise ValueError("移行済み計画ファイルはyyyy/MM/dd-{旧ファイル名}の形式で指定してください")
    year_text, month_text, filename = relative.parts
    match = _MIGRATED_MAIN_RE.fullmatch(filename)
    if not (re.fullmatch(r"[0-9]{4}", year_text) and re.fullmatch(r"[0-9]{2}", month_text) and match):
        raise ValueError("移行済み計画ファイルの年月またはファイル名が不正です")
    legacy_name = match.group("legacy_name")
    if legacy_name.endswith((".detail.md", ".bugs.md", ".review.md", "-workaround-check.md")) or any(
        character in _FORBIDDEN_NAME_CHARACTERS or ord(character) < 0x20 for character in legacy_name
    ):
        raise ValueError("移行済み計画ファイル名に使用できない文字が含まれています")
    try:
        datetime.date(int(year_text), int(month_text), int(match.group("day")))
    except ValueError as error:
        raise ValueError("移行済み計画ファイルの日付が不正です") from error
    return pathlib.Path(*relative.parts)


def _main_candidate_for_new_path(path: pathlib.Path) -> pathlib.Path | None:
    """新root内のdetail/bugsから対応するメイン候補を返す。"""
    name = path.name
    if name.endswith((".review.md", "-workaround-check.md")):
        return None
    if name.endswith(".detail.md"):
        return path.with_name(name[: -len(".detail.md")] + ".md")
    if name.endswith(".bugs.md"):
        return path.with_name(name[: -len(".bugs.md")] + ".md")
    if name.endswith(".md"):
        return path
    return None


def _new_plan_kind(file_path: str | os.PathLike[str]) -> str | None:
    """新root内のファイル種別を返す。"""
    try:
        path = _resolve(pathlib.Path(file_path))
        root = _resolve(new_plans_root())
        relative = path.relative_to(root)
        if len(relative.parts) != 3:
            return None
        candidate = _main_candidate_for_new_path(path)
        if candidate is None:
            return None
        candidate_rel = candidate.relative_to(root)
        try:
            validate_plan_relative_path(candidate_rel)
        except ValueError:
            validate_migrated_plan_relative_path(candidate_rel)
        if path.name.endswith(".bugs.md"):
            return "adjunct"
        if path.name.endswith(".detail.md"):
            return "detail"
        if path.name.endswith(".md"):
            return "main"
    except (OSError, ValueError):
        return None
    return None


def resolve_plan_file(
    value: pathlib.Path | str,
    *,
    private_notes: pathlib.Path | str | None = None,
    allow_legacy_absolute: bool = True,
) -> pathlib.Path:
    """保存済み計画参照を実ファイルパスへ解決する。

    portable値はprivate-notes内へ限定する。旧rootのパスと過去の絶対パスは、
    既存データを読むための互換経路として受理する。新規保存値の正規化には
    `normalize_plan_file`を用いる。
    """
    raw = os.fspath(value)
    if not raw:
        raise ValueError("plan_fileが空です")
    notes = _resolve(private_notes_root(private_notes))
    if raw.startswith(PORTABLE_PLAN_PREFIX):
        relative = _validate_portable_remainder(raw[len(PORTABLE_PLAN_PREFIX) :])
        candidate = _resolve(notes / relative)
        if not candidate.is_relative_to(notes):
            raise ValueError("計画ファイルの可搬パスがprivate-notes外を指しています")
        return candidate
    if "$(" in raw:
        raise ValueError("plan_fileには固定された可搬接頭辞以外のシェル式を指定できません")
    path = pathlib.Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("plan_fileは可搬表記または絶対パスで指定してください")
    resolved = _resolve(path)
    for root in (notes / NEW_PLANS_DIRECTORY, legacy_plans_root()):
        if _lexically_under(path, root) and not resolved.is_relative_to(_resolve(root)):
            raise ValueError("計画ファイルのシンボリックリンクが許可root外を指しています")
    if resolved.is_relative_to(notes) or resolved.is_relative_to(_resolve(legacy_plans_root())):
        return resolved
    if allow_legacy_absolute:
        return resolved
    raise ValueError("plan_fileが許可された保存root外を指しています")


def to_portable_plan_file(
    path: pathlib.Path | str,
    *,
    private_notes: pathlib.Path | str | None = None,
) -> str:
    """新plans root内の絶対パスをportable値へ変換する。

    旧rootまたは過去のroot外絶対パスは読み取り互換のため絶対表記を維持する。
    """
    notes = _resolve(private_notes_root(private_notes))
    resolved = resolve_plan_file(path, private_notes=notes)
    relative = _relative_to(resolved, notes)
    if relative is not None and relative.parts and relative.parts[0] == NEW_PLANS_DIRECTORY:
        return PORTABLE_PLAN_PREFIX + pathlib.PurePosixPath(*relative.parts).as_posix()
    return str(resolved)


def normalize_plan_file(
    value: pathlib.Path | str,
    *,
    private_notes: pathlib.Path | str | None = None,
) -> str:
    """保存用のplan_file値へ正規化する。"""
    return to_portable_plan_file(value, private_notes=private_notes)


def stored_plan_file_path(
    value: pathlib.Path | str,
    *,
    private_notes: pathlib.Path | str | None = None,
) -> pathlib.Path:
    """保存済みplan_fileを読み取り用実体へ解決する別名。"""
    return resolve_plan_file(value, private_notes=private_notes)


def _plan_file_name(file_path: str) -> str | None:
    """旧`~/.claude/plans/`直下のファイル名を返す。"""
    if not file_path:
        return None
    try:
        path = _resolve(pathlib.Path(file_path))
        plans_dir = _resolve(legacy_plans_root())
        relative = path.relative_to(plans_dir)
    except (OSError, ValueError):
        return None
    if len(relative.parts) != 1:
        return None
    return relative.parts[0]


def _is_component_name(name: str) -> bool:
    """旧rootの計画構成要素名を判定する。"""
    if (
        name.endswith(".review.md")
        or name.endswith(".codex.log")
        or name.endswith("-workaround-check.md")
        or name.endswith(".bugs.md")
    ):
        return False
    return name.endswith(".md")


def is_plan_component_file(file_path: str) -> bool:
    """計画ファイル（メイン）または計画ファイル（詳細）か判定する。"""
    name = _plan_file_name(file_path)
    if name is not None:
        return _is_component_name(name)
    try:
        path = resolve_plan_file(file_path)
    except (OSError, ValueError):
        return False
    return _new_plan_kind(path) in {"main", "detail"}


def is_plan_main_file(file_path: str) -> bool:
    """計画ファイル（メイン）か判定する。"""
    name = _plan_file_name(file_path)
    if name is not None:
        return _is_component_name(name) and not name.endswith(".detail.md")
    try:
        path = resolve_plan_file(file_path)
    except (OSError, ValueError):
        return False
    return _new_plan_kind(path) == "main"


def is_plan_adjunct_file(file_path: str) -> bool:
    """計画付属のbugsファイルか判定する。"""
    name = _plan_file_name(file_path)
    if name is not None:
        return name.endswith(".bugs.md")
    try:
        path = resolve_plan_file(file_path)
    except (OSError, ValueError):
        return False
    return _new_plan_kind(path) == "adjunct"


# 実装側で読みやすい公開別名。既存hookの関数名は維持する。
plan_root = new_plans_root
portable_path = to_portable_plan_file
resolve_stored_plan_file = resolve_plan_file
is_valid_plan_relative_path = validate_plan_relative_path
