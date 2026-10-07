"""計画rootの配下にあるパスが、計画バンドルのどの種別のファイルかを判定する。

判定はhookが計画ファイルの書込みを識別するために使う。種別と接尾辞の定義は`_plan.bundle_kinds`が持ち、
本モジュールは計画rootの内外と保存形式の名前の検証を加えてパスを判定する。
"""

from __future__ import annotations

import os
import pathlib

from agent_toolkit._plan import bundle_kinds as _bundle_kinds
from agent_toolkit._plan import locations as _locations


def _resolve(path: pathlib.Path) -> pathlib.Path:
    """存在しないパスも含めて実体基準の絶対パスへ変換する。"""
    return path.expanduser().resolve(strict=False)


def _legacy_direct_name(file_path: str) -> str | None:
    """旧`~/.claude/plans/`直下のファイル名を返す。"""
    if not file_path:
        return None
    try:
        path = _resolve(pathlib.Path(file_path))
        plans_dir = _resolve(_locations.legacy_plans_root())
        relative = path.relative_to(plans_dir)
    except (OSError, ValueError):
        return None
    if len(relative.parts) != 1:
        return None
    return relative.parts[0]


def _new_plan_kind(file_path: str | os.PathLike[str]) -> _bundle_kinds.BundleKind | None:
    """`~/.claude/plans`または`private-notes/plans/`内の計画ファイルの種別を返す。"""
    try:
        path = _resolve(pathlib.Path(file_path))
        working_root = _resolve(_locations.working_plans_root())
        saved_root = _resolve(_locations.new_plans_root())
        root = next(candidate for candidate in (working_root, saved_root) if path.is_relative_to(candidate))
        candidate = _locations.main_candidate_path(path)
        if candidate is None:
            return None
        candidate_rel = candidate.relative_to(root)
        if root == working_root and len(candidate_rel.parts) == 1:
            _locations.validate_working_plan_relative_path(candidate_rel)
        else:
            try:
                _locations.validate_plan_relative_path(candidate_rel)
            except ValueError:
                _locations.validate_migrated_plan_relative_path(candidate_rel)
    except (OSError, StopIteration, ValueError):
        return None
    return _bundle_kinds.kind_of_name(path.name)


def _kind_of_file(file_path: str) -> _bundle_kinds.BundleKind | None:
    """計画rootの配下にあるパスの種別を返す。計画ファイルとして扱えないパスは`None`を返す。"""
    name = _legacy_direct_name(file_path)
    if name is not None:
        return _bundle_kinds.kind_of_name(name)
    try:
        path = _locations.resolve_plan_file(file_path)
    except (OSError, ValueError):
        return None
    return _new_plan_kind(path)


def is_plan_main_file(file_path: str) -> bool:
    """計画ファイル（メイン）か判定する。"""
    return _kind_of_file(file_path) is _bundle_kinds.MAIN


def is_plan_adjunct_file(file_path: str) -> bool:
    """計画付属のbugsファイルか判定する。"""
    return _kind_of_file(file_path) is _bundle_kinds.BUGS


def is_plan_handoff_file(file_path: str) -> bool:
    """計画root配下の引き継ぎ記録か判定する。

    引き継ぎ記録は計画本体ではないため、`agent-toolkit:plan-mode`の未起動を警告する判定の対象から外す。
    起草中の素材を含むため、口語表現があるか確かめる対象からは外さない。
    """
    return _kind_of_file(file_path) is _bundle_kinds.HANDOFF
