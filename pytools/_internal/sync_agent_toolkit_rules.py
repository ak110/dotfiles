"""agent-toolkitプラグインの配布ルールを`~/.claude/rules/agent-toolkit/`へ同期する。

`agent-toolkit/rules/`配下のMarkdownを配布先の`~/.claude/rules/agent-toolkit/`へ
`pytilpack.pathlib.sync(..., delete=True)`で反映する。
chezmoiは`agent-toolkit/`配下を配布対象にしないため、`chezmoi apply`後処理で別経路の同期を行う。
"""

import logging

import pytilpack.pathlib

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)


def run() -> bool:
    """`agent-toolkit/rules/`を`~/.claude/rules/agent-toolkit/`へ同期する。"""
    dotfiles_root = claude_common.find_dotfiles_root()
    if dotfiles_root is None:
        logger.info(log_format.format_status("agent-toolkit rules", "dotfiles ルートが見つからずスキップ"))
        return False

    src = dotfiles_root / "agent-toolkit" / "rules"
    dst = claude_common.CLAUDE_HOME / "rules" / "agent-toolkit"
    pytilpack.pathlib.sync(src, dst, delete=True)
    return True
