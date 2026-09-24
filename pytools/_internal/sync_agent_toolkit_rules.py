"""agent-toolkitプラグインの配布ルールをClaude CodeとCodexの配布先へ同期する。

`agent-toolkit/rules/`配下のMarkdownを、配布先の`~/.claude/rules/agent-toolkit/`と
`~/.codex/agent-toolkit/rules/`へ反映する。chezmoiは`agent-toolkit/`配下を配布対象にしないため、
`chezmoi apply`後処理で別経路の同期を行う。

配布先の本文は境界標識で囲む。実行主体は常時読み込まれる規範として当該本文を受け取るため、
生成主体、種別及び埋め込み元のパスを本文から判別できる状態にする。
同期は内容の一致で冪等性を判定する。境界は要素名と属性で判別する。
"""

import logging
import shutil
from pathlib import Path

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

CODEX_HOME = Path.home() / ".codex"
RULES_RELATIVE = Path("agent-toolkit") / "rules"
NORMATIVE_ELEMENT = "agent-toolkit-auto-inserted"
NORMATIVE_SOURCE = "agent-toolkit"
NORMATIVE_KIND = "rules"


def run() -> bool:
    """`agent-toolkit/rules/`をClaude CodeとCodexの配布先へ同期する。"""
    dotfiles_root = claude_common.find_dotfiles_root()
    if dotfiles_root is None:
        logger.info(log_format.format_status("agent-toolkit rules", "dotfiles ルートが見つからずスキップ"))
        return False

    src = dotfiles_root / RULES_RELATIVE
    if not src.is_dir():
        logger.warning(log_format.format_status("agent-toolkit rules", f"コピー元が存在しません: {src}"))
        return True

    destinations = (
        claude_common.CLAUDE_HOME / "rules" / "agent-toolkit",
        CODEX_HOME / RULES_RELATIVE,
    )
    changed = False
    for destination in destinations:
        if _sync_destination(src, destination):
            changed = True
    return changed


def wrapped_body(rule: Path) -> str:
    """配布先へ書く本文を、境界標識で囲んだ形で返す。"""
    marker = (RULES_RELATIVE / rule.name).as_posix()
    body = rule.read_text(encoding="utf-8").rstrip("\n")
    opening = f'<{NORMATIVE_ELEMENT} source="{NORMATIVE_SOURCE}" kind="{NORMATIVE_KIND}" path="{marker}">'
    return f"{opening}\n{body}\n</{NORMATIVE_ELEMENT}>\n"


def _sync_destination(src: Path, destination: Path) -> bool:
    """配布先の内容を配布元へそろえ、変更があれば`True`を返す。"""
    changed = False
    if destination.is_symlink():
        # 旧版はリンクで配布していた。本文を書き換えて配る形へ移行するため、リンクを取り除く。
        destination.unlink()
        changed = True
    destination.mkdir(parents=True, exist_ok=True)
    expected: set[str] = set()
    for rule in sorted(src.glob("*.md")):
        expected.add(rule.name)
        target = destination / rule.name
        content = wrapped_body(rule)
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            continue
        if claude_common.atomic_write_text(target, content, tag="agent-toolkit rules"):
            changed = True
    for existing in sorted(destination.iterdir()):
        if existing.name in expected:
            continue
        if existing.is_dir() and not existing.is_symlink():
            shutil.rmtree(existing)
        else:
            existing.unlink()
        changed = True
    return changed
