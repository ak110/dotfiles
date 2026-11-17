"""登録されたroot集合から、パスの最も内側の所属を解決する。"""

import pathlib
from collections.abc import Iterable


def owning_root(path: pathlib.Path, roots: Iterable[pathlib.Path]) -> pathlib.Path | None:
    """パスを含む最も内側の解決済みrootを返し、登録外ならNoneを返す。"""
    return owning_resolved_root(path, [root.resolve() for root in roots])


def owning_resolved_root(path: pathlib.Path, resolved_roots: Iterable[pathlib.Path]) -> pathlib.Path | None:
    """解決済みのrootを解決し直さずに、パスを含む最も内側のrootを返す。同じroot集合へ多数のパスを当てる走査で使う。"""
    resolved = path.resolve()
    owner: pathlib.Path | None = None
    for candidate in resolved_roots:
        if resolved.is_relative_to(candidate) and (owner is None or len(candidate.parts) > len(owner.parts)):
            owner = candidate
    return owner
