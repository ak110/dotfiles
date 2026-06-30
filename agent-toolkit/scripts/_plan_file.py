"""Plan file（`~/.claude/plans/*.md`）判定の共通ユーティリティ。

pretooluse / posttooluse の双方から参照する。`agent-toolkit/scripts/`配下の
配布物独立性を保つため`pytools/_internal/`は参照せず近接配置する。
"""

import hashlib
import pathlib
import re

_FENCE_PATTERN = re.compile(r"^(`{3,}|~{3,})")


def strip_background_text_blocks(content: str) -> str:
    """`## 背景`配下のフェンス付きコードブロック内容を除去して返す。

    scratchpad事前lint検査の出力対象と一致させるための共通ヘルパー。
    背景セクション内の地の文（コードブロック外）はそのまま保持する。
    コードフェンス内の`## `見出し文字列を次セクション開始と誤検出しないよう、
    行単位の走査でフェンス範囲を考慮してから`## 背景`配下の範囲を確定する。
    フェンス判定は同字種かつ開始長以上で閉じる方式に揃え、長いフェンス記法と波線記法に対応する。
    """
    section_range = _locate_background_section(content)
    if section_range is None:
        return content
    start, end = section_range
    section_text = content[start:end]
    stripped_section = _strip_fence_block_contents_in_section(section_text)
    return content[:start] + stripped_section + content[end:]


def _locate_background_section(content: str) -> tuple[int, int] | None:
    """`## 背景`見出し行の開始位置と次のH2見出し（または末尾）の開始位置を返す。

    コードフェンス内の`## `行はH2見出しと扱わない。
    フェンス終端は同字種かつ開始フェンス以上の長さで閉じるものに限る。
    `## 背景`が無い場合はNone。
    """
    lines = content.splitlines(keepends=True)
    fence_marker: str | None = None
    start: int | None = None
    offset = 0
    for line in lines:
        line_start = offset
        offset += len(line)
        stripped = line.strip()
        fence_match = _FENCE_PATTERN.match(stripped)
        if fence_match:
            candidate = fence_match.group(1)
            if fence_marker is None:
                fence_marker = candidate
                continue
            if stripped[0] == fence_marker[0] and len(stripped) >= len(fence_marker) and set(stripped) == {fence_marker[0]}:
                fence_marker = None
            continue
        if fence_marker is not None:
            continue
        if start is None:
            if line.startswith("## 背景"):
                start = line_start
            continue
        if line.startswith("## "):
            return (start, line_start)
    if start is None:
        return None
    return (start, len(content))


def _strip_fence_block_contents_in_section(section_text: str) -> str:
    """セクション内のフェンス付きコードブロックの内容を、フェンス行のみ残して除去する。

    `text`コードブロックに限らずフェンス全般を対象とする（背景セクションの引用素材は
    フェンス言語指定によらず除去対象に揃える）。
    フェンス判定は`_locate_background_section`と同じ仕様で、
    同字種かつ開始長以上で閉じるものに限る。
    """
    lines = section_text.splitlines(keepends=True)
    result: list[str] = []
    fence_marker: str | None = None
    for line in lines:
        stripped = line.strip()
        fence_match = _FENCE_PATTERN.match(stripped)
        if fence_marker is None:
            if fence_match:
                fence_marker = fence_match.group(1)
            result.append(line)
            continue
        # フェンス内
        if (
            fence_match
            and stripped[0] == fence_marker[0]
            and len(stripped) >= len(fence_marker)
            and set(stripped) == {fence_marker[0]}
        ):
            # 閉じフェンス
            result.append(line)
            fence_marker = None
        # フェンス内の本文行: 出力しない
    return "".join(result)


def compute_prelint_hashes(content: str) -> tuple[str, str]:
    """Plan file contentの全文SHA256と背景フェンスブロック除去後SHA256のペアを返す。

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
