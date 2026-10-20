"""WIの固定保存パスとfrontmatterの状態を解決する。"""

import pathlib
from collections.abc import Iterator, Mapping

from agent_toolkit._atk.wi.constants import WI_STATES
from agent_toolkit._atk.wi.entry_text import parse_frontmatter
from agent_toolkit._common import wi_layout as _wi_layout
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.wi_layout import entry_path

__all__ = ["entry_path", "entry_state_from_metadata", "entry_state_of", "iter_entry_paths"]


def iter_entry_paths(private_notes: pathlib.Path) -> Iterator[pathlib.Path]:
    """年月配置と移行前の状態配置の項目を列挙する。"""
    yield from _wi_layout.iter_entry_paths(private_notes, legacy_states=WI_STATES)


def entry_state_of(path: pathlib.Path, text: str | None = None) -> str:
    """保存状態を検証する。旧配置は移行時の状態を保持する。"""
    parsed = parse_frontmatter(text if text is not None else path.read_text(encoding="utf-8"))
    return entry_state_from_metadata(path, parsed[0] if parsed is not None else {})


def entry_state_from_metadata(path: pathlib.Path, metadata: Mapping[str, object]) -> str:
    """取得済みの管理情報から保存状態を検証する。"""
    state = metadata.get("state")
    if state is None and path.parent.name in WI_STATES:
        return path.parent.name
    if state not in WI_STATES:
        raise ActionableError(
            f"frontmatterのstateが不正または欠落している: {path} ({state!r})",
            next_action=f"{path}のstateを{'・'.join(WI_STATES)}のいずれかへ修正する",
        )
    return str(state)
