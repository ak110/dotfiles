"""選定項目の書込区分と導出した新設先の検査。"""

from __future__ import annotations

import itertools
import pathlib

from check_selection_common import Violation
from check_selection_paths import _covers, _is_range, _new_file_path, _normalize_candidate

from agent_toolkit._plan import selection as _selection

_NEXT_WRITE_PATH = (
    "`書込対象`・`公開工程の書込対象`の不正な要素を、実在ファイル、末尾/付き実在ディレクトリ、"
    "本文明示の新設先または正当な`導出した新設先`の個別パスへ直す。本文に無い新設先は"
    "`パス`・`反映範囲`・`要求`・`配置根拠`の4欄を`導出した新設先`へ記録する"
)


_NEXT_BROAD = "広すぎる範囲を、診断行が示す配下の本文の反映先の個別パスへ置き換える"


_NEXT_EXCLUDED = "`書き込まない反映先`から本文の反映先に無いパスを除く"


_NEXT_SECTION_OVERLAP = "区分間で重複するパスは、所有する1区分だけへ残す"


_NEXT_DERIVED = (
    "`導出した新設先`の記録を診断行が示す条件に合わせて直す。新設パスは実在する親ディレクトリと拡張子を持つ完全な名前にし、"
    "反映範囲を配置根拠のある実在ディレクトリへ対応させ、同じパスを1件だけ記録して書込区分へ個別に置く"
)


def check_decision(
    awi: str,
    reflected: set[str],
    write_files: list[str],
    public_write_files: list[str],
    excluded_paths: list[str],
    work_dir: pathlib.Path | None = None,
    derived_new_paths: list[dict[str, str]] | None = None,
) -> list[Violation]:
    """選定結果の1件の項目の違反を、AWIのファイル名・区分・パスを含む理由と次の操作の組の一覧で返す。"""
    errors: list[Violation] = []
    derived_paths: set[str] = set()
    if work_dir is not None:
        derived_paths, _, derived_errors = _check_derived_paths(
            awi, [*write_files, *public_write_files], derived_new_paths or [], work_dir
        )
        errors.extend(derived_errors)
        declared = {record["パス"] for record in derived_new_paths or []}
        for name, entries in (
            (_selection.WRITE_FILES_KEY, write_files),
            (_selection.PUBLIC_WRITE_FILES_KEY, public_write_files),
        ):
            for entry in entries:
                failure = _write_path_failure(entry, work_dir, reflected, derived_paths, declared)
                if failure is not None:
                    errors.append(Violation(f"{awi}: `{name}`のパスの不正: {entry}: {failure}", next_action=_NEXT_WRITE_PATH))
    reflected_ranges = [path for path in reflected if _is_range(path)]
    for entry in [*write_files, *public_write_files]:
        if not _is_range(entry):
            continue
        inner = sorted(path for path in reflected if path != entry and path.startswith(entry))
        if inner and not any(entry.startswith(scope) for scope in reflected_ranges):
            errors.append(
                Violation(f"{awi}: 広すぎる範囲: {entry}（配下の本文の反映先: {', '.join(inner)}）", next_action=_NEXT_BROAD)
            )
    errors.extend(
        Violation(f"{awi}: 書き込まない反映先の不正: {path}", next_action=_NEXT_EXCLUDED)
        for path in excluded_paths
        if path not in reflected
    )
    sections = (
        (_selection.WRITE_FILES_KEY, write_files),
        (_selection.PUBLIC_WRITE_FILES_KEY, public_write_files),
        (_selection.EXCLUDED_PATHS_KEY, excluded_paths),
    )
    for (first_name, first_paths), (second_name, second_paths) in itertools.combinations(sections, 2):
        overlaps = {
            second if _covers(first, second) else first
            for first in first_paths
            for second in second_paths
            if (_covers(first, second) or _covers(second, first))
            # 書き込まない範囲が書込区分のパスを真に含む組は、範囲の残りを書き込まない選定として受理する。
            and not (second_name == _selection.EXCLUDED_PATHS_KEY and first != second and _covers(second, first))
        }
        errors.extend(
            Violation(f"{awi}: 区分間の重複: {path}（`{first_name}`と`{second_name}`）", next_action=_NEXT_SECTION_OVERLAP)
            for path in sorted(overlaps)
        )
    return errors


def _check_derived_paths(
    awi: str, writes: list[str], records: list[dict[str, str]], work_dir: pathlib.Path
) -> tuple[set[str], set[str], list[Violation]]:
    """導出記録のパス・範囲・書込への対応を確かめ、正当な新設先を返す。"""
    paths: set[str] = set()
    scopes: set[str] = set()
    seen: set[str] = set()
    errors: list[Violation] = []
    for record in records:
        path, scope = record["パス"], record["反映範囲"]
        failures = _derived_record_failures(path, scope, work_dir)
        if path in seen:
            failures.append("同じパスを重複して宣言している")
        if path not in writes:
            failures.append("書込区分（`書込対象`・`公開工程の書込対象`）に同じパスが無い")
        if failures:
            errors.append(
                Violation(
                    f"{awi}: `{_selection.DERIVED_NEW_PATHS_KEY}`の対応が不正: {path}（{scope}）: {'; '.join(failures)}",
                    next_action=_NEXT_DERIVED,
                )
            )
        else:
            paths.add(path)
            scopes.add(scope)
        seen.add(path)
    return paths, scopes, errors


def _derived_record_failures(path: str, scope: str, work_dir: pathlib.Path) -> list[str]:
    """導出記録1件のパスと反映範囲について、成立しない条件を比べた値とともに返す。"""
    failures: list[str] = []
    if (
        _normalize_candidate(path) != path
        or any(char in path for char in "*?[]{}\\")
        or pathlib.PurePosixPath(path).as_posix() != path
    ):
        failures.append("パスが正規化したリポジトリ相対パスではない")
    elif (work_dir / path).exists() and not (work_dir / path).is_file():
        failures.append("パスの実在先が通常ファイルではない")
    elif _new_file_path(path, work_dir) != path:
        failures.append("パスが実在する親ディレクトリと拡張子を持つ完全な新設名ではない")
    if (
        _normalize_candidate(scope) != scope
        or not scope.endswith("/")
        or pathlib.PurePosixPath(scope).as_posix() + "/" != scope
        or not (work_dir / scope).is_dir()
    ):
        failures.append("反映範囲が末尾/付きの実在ディレクトリではない")
    elif not _covers(scope, path):
        failures.append("反映範囲がパスを含まない")
    return failures


def _write_path_failure(
    entry: str, work_dir: pathlib.Path, reflected: set[str], derived_paths: set[str], declared: set[str]
) -> str | None:
    """書込区分の要素1件が成立しない条件を返す。成立する場合は`None`を返す。"""
    target = work_dir / entry
    if _normalize_candidate(entry) != entry:
        return "正規化したリポジトリ相対パスではない"
    if target.is_dir():
        return None if entry.endswith("/") else "実在ディレクトリだが末尾/が無い"
    if target.is_file():
        return None if not entry.endswith("/") else "実在ファイルだが末尾/が付く"
    if target.exists():
        return "実在先がファイルでもディレクトリでもない"
    if entry in reflected or entry in derived_paths:
        return None
    if entry in declared:
        return (
            "未作成のパスで、同じWIの`導出した新設先`の記録が不正なため新設先として受理しない（その記録の診断を直す対象とする）"
        )
    return "未作成のパスで、本文の反映先にも`導出した新設先`にも無い"
