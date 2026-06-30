"""Plan file（`~/.claude/plans/*.md`）判定の共通ユーティリティ。

pretooluse / posttooluse の双方から参照する。`agent-toolkit/scripts/`配下の
配布物独立性を保つため`pytools/_internal/`は参照せず近接配置する。
"""

import hashlib
import pathlib
import re

_BACKGROUND_SECTION_PATTERN = re.compile(
    r"^## 背景\s*\n.*?(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
_TEXT_CODE_BLOCK_PATTERN = re.compile(
    r"^```text\s*\n.*?^```\s*$",
    re.MULTILINE | re.DOTALL,
)


def strip_background_text_blocks(content: str) -> str:
    """`## 背景`配下の`text`コードブロック内容を除去して返す。

    scratchpad事前lint検査の出力対象と一致させるための共通ヘルパー。
    背景セクション内の地の文（コードブロック外）はそのまま保持する。
    """

    def _strip_in_section(match: re.Match[str]) -> str:
        return _TEXT_CODE_BLOCK_PATTERN.sub("```text\n```", match.group(0))

    return _BACKGROUND_SECTION_PATTERN.sub(_strip_in_section, content)


def compute_prelint_hashes(content: str) -> tuple[str, str]:
    """Plan file contentの全文SHA256と背景text除去後SHA256のペアを返す。

    PreToolUseとPostToolUseが同一ロジックで2種類のSHA256を算出するため、本関数に共通化する。
    PreToolUseはWriteのcontentから、PostToolUseはscratchpadファイル内容から呼び出す。
    """
    full_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    stripped_sha = hashlib.sha256(
        strip_background_text_blocks(content).encode("utf-8"),
    ).hexdigest()
    return full_sha, stripped_sha


def is_plan_file(file_path: str) -> bool:
    """`~/.claude/plans/`直下のplan file（`*.md`）の場合に真を返す。

    `.review.md` / `.codex.log`は副次ファイルのため除外する。
    サブディレクトリ配下のファイルは対象外（直下のみ）。
    """
    if not file_path:
        return False
    try:
        path = pathlib.Path(file_path).resolve()
        plans_dir = (pathlib.Path.home() / ".claude" / "plans").resolve()
        rel = path.relative_to(plans_dir)
    except (OSError, ValueError):
        return False
    if len(rel.parts) != 1:
        return False
    name = rel.parts[0]
    if name.endswith(".review.md") or name.endswith(".codex.log"):
        return False
    return name.endswith(".md")
