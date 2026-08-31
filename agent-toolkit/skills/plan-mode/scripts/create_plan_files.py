#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py[linkify]>=4.0.0", "platformdirs>=4.0"]
# ///
"""plan-modeが使う新規計画ファイルの内部作成処理。

このスクリプトは公開CLIではなく、plan-modeが管理対象一時領域へ準備した
メイン・detail・bug本文をprivate-notesへ確定するための内部APIを提供する。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import os
import pathlib
import re
import secrets
import sys
import tempfile
from collections.abc import Iterator

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))

import _file_lock  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error

PLAN_STEM_PLACEHOLDER = "__PLAN_STEM__"
"""本文中で最終計画stemが未確定であることを示す固定プレースホルダー。"""
PORTABLE_PLAN_PREFIX = _plan_file.PORTABLE_PLAN_PREFIX
"""計画本文で受理する可搬参照の固定接頭辞。"""

_TOKEN_RE = re.compile(r"[0-9a-f]{4}\Z")
_PORTABLE_REFERENCE_RE = re.compile(re.escape(PORTABLE_PLAN_PREFIX) + r"[^\s`<>\"']+")
_FORBIDDEN_NAME_CHARACTERS = frozenset('/\\:*?"<>|')
_DEFAULT_MAX_ATTEMPTS = 100


class PlanCreationError(RuntimeError):
    """計画ファイルを確定できなかった場合のエラー。"""


class _CandidateCollision(Exception):
    """候補stemが既存ファイルまたは競合で利用できないことを示す。"""


def _validate_plan_name(plan_name: str) -> str:
    """計画ファイル名へ埋め込む名称を検証して返す。"""
    if not plan_name or plan_name in {".", ".."}:
        raise ValueError("計画名が空です")
    if any(character in _FORBIDDEN_NAME_CHARACTERS or ord(character) < 0x20 for character in plan_name):
        raise ValueError("計画名にパス区切り文字または制御文字を指定できません")
    if plan_name.endswith(".md"):
        raise ValueError("計画名に拡張子を指定できません")
    return plan_name


def _validate_date(value: datetime.date | None) -> datetime.date:
    """計画名へ埋め込む日付を検証して返す。"""
    if value is None:
        return datetime.date.today()
    if not isinstance(value, datetime.date):
        raise TypeError("dateはdatetime.dateで指定してください")
    return value


def _resolved_plans_root(private_notes: pathlib.Path | str | None) -> pathlib.Path:
    """新しい計画rootを解決し、private-notes外へのsymlinkを拒否する。"""
    notes = _plan_file.private_notes_root(private_notes).expanduser().resolve(strict=False)
    plans_root = (notes / _plan_file.NEW_PLANS_DIRECTORY).resolve(strict=False)
    if not plans_root.is_relative_to(notes):
        raise PlanCreationError("計画rootがprivate-notesの外を指しています")
    return plans_root


def _require_plans_root_path(path: pathlib.Path, plans_root: pathlib.Path) -> None:
    """`path`の実体が解決済み計画root配下にあることを検証する。"""
    try:
        resolved = path.resolve(strict=False)
    except OSError as error:
        raise PlanCreationError(f"計画作成先を検証できません: {path}") from error
    if not resolved.is_relative_to(plans_root):
        raise PlanCreationError(f"計画作成先がprivate-notesの外を指しています: {path}")


def _read_source(path: pathlib.Path | str) -> bytes:
    """一時領域の入力本文をUTF-8として読み取る。"""
    source = pathlib.Path(path).expanduser()
    content = source.read_bytes()
    content.decode("utf-8")
    return content


def _replace_placeholder(content: bytes, stem: str) -> bytes:
    """本文中の固定stemプレースホルダーを最終stemへ置換する。"""
    return content.replace(PLAN_STEM_PLACEHOLDER.encode("utf-8"), stem.encode("utf-8"))


def _candidate_is_taken(directory: pathlib.Path, stem: str) -> bool:
    """同一ディレクトリに候補stemと衝突する項目があるか判定する。"""
    prefix = f"{stem}."
    return any(entry.name.startswith(prefix) for entry in directory.iterdir())


def _write_temporary(directory: pathlib.Path, stem: str, suffix: str, content: bytes) -> pathlib.Path:
    """本文を同一ディレクトリの排他的な一時ファイルへ書き込む。"""
    file_descriptor, raw_path = tempfile.mkstemp(prefix=f".{stem}.", suffix=suffix, dir=directory)
    path = pathlib.Path(raw_path)
    try:
        with os.fdopen(file_descriptor, "wb") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    if path.read_bytes() != content:
        with contextlib.suppress(OSError):
            path.unlink()
        raise PlanCreationError(f"一時ファイルの読み戻し内容が一致しません: {path}")
    return path


def _file_identity(path: pathlib.Path) -> tuple[int, int]:
    """ファイルのdevice/inode識別子を返す。"""
    stat = path.stat()
    return stat.st_dev, stat.st_ino


def _remove_owned(path: pathlib.Path, identity: tuple[int, int], content: bytes) -> bool:
    """inodeと内容が一致する自作ファイルだけを回収する。"""
    try:
        if _file_identity(path) != identity or path.read_bytes() != content:
            return False
        path.unlink()
    except OSError:
        return False
    return True


def _portable_reference_values(text: str) -> Iterator[str]:
    """本文から固定portable接頭辞を持つ参照値を抽出する。"""
    for match in _PORTABLE_REFERENCE_RE.finditer(text):
        value = match.group(0).rstrip(".,;:!?、。，；：！？)]}>")
        if value:
            yield value


def _check_portable_references(contents: tuple[bytes, ...], private_notes: pathlib.Path | str | None) -> None:
    """固定portable参照を安全な共通resolverで検査する。"""
    for content in contents:
        text = content.decode("utf-8")
        if PLAN_STEM_PLACEHOLDER in text:
            raise PlanCreationError("計画本文に未解決のstemプレースホルダーがあります")
        for reference in _portable_reference_values(text):
            try:
                _plan_file.resolve_plan_file(reference, private_notes=private_notes)
            except (OSError, ValueError) as error:
                raise PlanCreationError(f"計画本文の可搬参照が不正です: {reference}: {error}") from error


def _check_structure(
    main_path: pathlib.Path,
    work_dir: pathlib.Path,
    private_notes: pathlib.Path | str | None,
) -> None:
    """確定した二ファイル計画を既存の構造検査へ渡す。"""
    scripts_directory = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts_directory))
    import check_plan_file  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    errors, _warnings = check_plan_file.check(main_path, work_dir, private_notes=private_notes)
    if errors:
        raise PlanCreationError("計画構造検査に失敗しました: " + " / ".join(errors))


def _finalize_candidate(
    directory: pathlib.Path,
    plans_root: pathlib.Path,
    stem: str,
    main_content: bytes,
    detail_content: bytes,
    bug_content: bytes | None,
    work_dir: pathlib.Path,
    private_notes: pathlib.Path | str | None,
) -> tuple[pathlib.Path, ...]:
    """同じstemの全ファイルを排他的に確定し、途中失敗時に部分成果を残さず返す。"""
    main_path = directory / f"{stem}.md"
    detail_path = directory / f"{stem}.detail.md"
    targets = [(main_path, main_content, ".md.tmp"), (detail_path, detail_content, ".detail.md.tmp")]
    if bug_content is not None:
        targets.append((directory / f"{stem}.bugs.md", bug_content, ".bugs.md.tmp"))
    _require_plans_root_path(directory, plans_root)
    for path, _content, _suffix in targets:
        _require_plans_root_path(path, plans_root)
    temporary_paths: list[pathlib.Path] = []
    owned: list[tuple[pathlib.Path, tuple[int, int], bytes]] = []
    try:
        for _path, content, suffix in targets:
            temporary_paths.append(_write_temporary(directory, stem, suffix, content))

        for (path, content, _suffix), temporary_path in zip(targets, temporary_paths, strict=True):
            try:
                os.link(temporary_path, path)
            except FileExistsError as error:
                raise _CandidateCollision from error
            owned.append((path, _file_identity(path), content))

        for path, content, _suffix in targets:
            _require_plans_root_path(path, plans_root)
            if path.read_bytes() != content:
                raise PlanCreationError(f"確定後の計画本文を読み戻せません: {path}")
        _check_portable_references(tuple(content for _path, content, _suffix in targets), private_notes)
        _check_structure(main_path, work_dir, private_notes)
        return tuple(path for path, _content, _suffix in targets)
    except BaseException:
        for path, identity, content in reversed(owned):
            _remove_owned(path, identity, content)
        raise
    finally:
        for temporary_path in temporary_paths:
            with contextlib.suppress(OSError):
                temporary_path.unlink()


def create_plan_files(
    main_source: pathlib.Path | str,
    detail_source: pathlib.Path | str,
    plan_name: str,
    *,
    bug_source: pathlib.Path | str | None = None,
    private_notes: pathlib.Path | str | None = None,
    date: datetime.date | None = None,
    work_dir: pathlib.Path | str | None = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> tuple[pathlib.Path, ...]:
    """入力本文を新しい計画rootへ作成し、確定済みパスを返す。

    ``main_source``、``detail_source``及び任意の``bug_source``は管理対象一時領域にあるUTF-8本文を指す。
    新規作成では旧`~/.claude/plans/`へ書き込まない。
    """
    if max_attempts <= 0:
        raise ValueError("max_attemptsは1以上にしてください")
    name = _validate_plan_name(plan_name)
    plan_date = _validate_date(date)
    main_content = _read_source(main_source)
    detail_content = _read_source(detail_source)
    bug_content = _read_source(bug_source) if bug_source is not None else None
    sources = [pathlib.Path(main_source), pathlib.Path(detail_source)]
    if bug_source is not None:
        sources.append(pathlib.Path(bug_source))
    resolved_sources = [source.expanduser().resolve() for source in sources]
    if len(set(resolved_sources)) != len(resolved_sources):
        raise ValueError("メイン、detail及びbugに同じ入力ファイルを指定できません")

    metadata, metadata_errors = _plan_format.parse_plan_metadata(main_content.decode("utf-8"))
    if metadata_errors:
        raise PlanCreationError("計画メタ情報を解析できません: " + " / ".join(metadata_errors))
    work_type = metadata.values.get("作業種別") if metadata is not None else None
    if work_type == "バグ対応" and bug_content is None:
        raise PlanCreationError("作業種別がバグ対応の場合は計画ファイル（バグ）の入力が必要です")
    if work_type == "通常変更" and bug_content is not None:
        raise PlanCreationError("作業種別が通常変更の場合は計画ファイル（バグ）の入力を指定できません")

    plans_root = _resolved_plans_root(private_notes)
    date_directory = plans_root / f"{plan_date.year:04d}" / f"{plan_date.month:02d}"
    _require_plans_root_path(date_directory, plans_root)
    date_directory.mkdir(parents=True, exist_ok=True)
    _require_plans_root_path(date_directory, plans_root)
    checked_work_dir = pathlib.Path(work_dir or pathlib.Path.cwd()).expanduser().resolve()
    notes_for_resolver = pathlib.Path(private_notes).expanduser() if private_notes is not None else None

    lock_path = plans_root / ".agent-toolkit-plan-create.lock"
    _file_lock.ensure_plan_lock_ignored(lock_path)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        _file_lock.acquire_lock(lock_file)
        try:
            _require_plans_root_path(date_directory, plans_root)
            for _attempt in range(max_attempts):
                token = secrets.token_hex(2)
                if _TOKEN_RE.fullmatch(token) is None:
                    raise PlanCreationError(f"乱数suffixが4桁16進数ではありません: {token}")
                stem = f"{plan_date.day:02d}-{name}-{token}"
                if _candidate_is_taken(date_directory, stem):
                    continue
                try:
                    return _finalize_candidate(
                        date_directory,
                        plans_root,
                        stem,
                        _replace_placeholder(main_content, stem),
                        _replace_placeholder(detail_content, stem),
                        _replace_placeholder(bug_content, stem) if bug_content is not None else None,
                        checked_work_dir,
                        notes_for_resolver,
                    )
                except _CandidateCollision:
                    continue
        finally:
            _file_lock.release_lock(lock_file)
    raise PlanCreationError(f"計画ファイル名の衝突を解消できませんでした（試行回数={max_attempts}）")


def _parse_date(value: str) -> datetime.date:
    """CLIの日付引数を検証する。"""
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("日付はYYYY-MM-DDで指定してください") from error


def main(argv: list[str] | None = None) -> int:
    """内部作成処理のCLI入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-source", required=True, type=pathlib.Path)
    parser.add_argument("--detail-source", required=True, type=pathlib.Path)
    parser.add_argument("--bug-source", type=pathlib.Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--private-notes", type=pathlib.Path)
    parser.add_argument("--date", type=_parse_date)
    parser.add_argument("--work-dir", type=pathlib.Path, default=pathlib.Path.cwd())
    args = parser.parse_args(argv)
    try:
        paths = create_plan_files(
            args.main_source,
            args.detail_source,
            args.name,
            bug_source=args.bug_source,
            private_notes=args.private_notes,
            date=args.date,
            work_dir=args.work_dir,
        )
    except (OSError, UnicodeError, PlanCreationError, TypeError, ValueError) as error:
        print(f"計画ファイルを作成できません: {error}", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
