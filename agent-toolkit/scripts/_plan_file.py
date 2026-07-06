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
    `strip_diff_markers_in_changes_blocks`（`## 変更内容`配下が対象）とは対象節が異なり、
    `compute_prelint_hashes`が両関数を組み合わせてscratchpad出力と同一の加工後テキストを算出する。
    """
    section_range = _locate_h2_section(content, "背景")
    if section_range is None:
        return content
    start, end = section_range
    section_text = content[start:end]
    stripped_section = _strip_fence_block_contents_in_section(section_text)
    return content[:start] + stripped_section + content[end:]


def _locate_h2_section(content: str, heading: str) -> tuple[int, int] | None:
    """指定したH2見出し（`## <heading>`）の開始位置と次のH2見出し（または末尾）の開始位置を返す。

    コードフェンス内の`## `行はH2見出しと扱わない。
    フェンス終端は同字種かつ開始フェンス以上の長さで閉じるものに限る。
    対象見出しが無い場合はNone。
    """
    lines = content.splitlines(keepends=True)
    fence_marker: str | None = None
    start: int | None = None
    offset = 0
    heading_prefix = f"## {heading}"
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
            if line.startswith(heading_prefix):
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
    フェンス判定は`_locate_h2_section`と同じ仕様で、
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


# unified diff相当ブロックの識別に使う先頭行パターン。
# フェンス開始直後の先頭2行以内にこれらのいずれかを含むブロックのみ加工対象とする。
_DIFF_HEAD_PATTERN = re.compile(r"^(@@|---|\+\+\+)")
_DIFF_MARKER_LOOKAHEAD_LINES = 2


def strip_diff_markers_in_changes_blocks(content: str) -> str:
    """`## 変更内容`配下のunified diff相当コードブロックのフェンスマーカー行と行頭プレフィックスを除去して返す。

    diff相当の識別条件はフェンス開始直後の先頭2行以内に`@@`または`---`・`+++`を含むこと。
    識別できないコードブロック（言語指定`text`かつ`+`・`-`をリスト記法として使うケース、
    `python`・`sh`等の言語指定を持つブロック等）は対象外とし内容を変更しない。
    scratchpad出力の加工パイプラインで、plan-file-guidelines.md
    「計画ファイル全体の遵守事項」節の差分表記対応として使う。
    """
    section_range = _locate_h2_section(content, "変更内容")
    if section_range is None:
        return content
    start, end = section_range
    section_text = content[start:end]
    processed_section = _strip_diff_marker_blocks_in_section(section_text)
    return content[:start] + processed_section + content[end:]


def _strip_diff_marker_blocks_in_section(section_text: str) -> str:
    """セクション内のunified diff相当コードブロックのフェンスマーカー行と行頭プレフィックスを除去する。

    diff相当でないブロック・閉じフェンス欠落ブロックは変更しない。
    """
    lines = section_text.splitlines(keepends=True)
    result: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        fence_match = _FENCE_PATTERN.match(stripped)
        if not fence_match:
            result.append(line)
            i += 1
            continue
        fence_marker = fence_match.group(1)
        block_lines: list[str] = []
        j = i + 1
        closing_index: int | None = None
        while j < n:
            candidate_stripped = lines[j].strip()
            candidate_match = _FENCE_PATTERN.match(candidate_stripped)
            if (
                candidate_match
                and candidate_stripped
                and candidate_stripped[0] == fence_marker[0]
                and len(candidate_stripped) >= len(fence_marker)
                and set(candidate_stripped) == {fence_marker[0]}
            ):
                closing_index = j
                break
            block_lines.append(lines[j])
            j += 1
        if closing_index is None:
            # 閉じフェンスが見つからない場合は元のまま出力する
            result.append(line)
            result.extend(block_lines)
            i = j
            continue
        is_diff = any(_DIFF_HEAD_PATTERN.match(bl.strip()) for bl in block_lines[:_DIFF_MARKER_LOOKAHEAD_LINES])
        if is_diff:
            # フェンスマーカー行（開始・終了）は出力せず、行頭`+`・`-`のみ除去して本文を出力する
            for bl in block_lines:
                result.append(re.sub(r"^[+-]", "", bl, count=1))
        else:
            result.append(line)
            result.extend(block_lines)
            result.append(lines[closing_index])
        i = closing_index + 1
    return "".join(result)


def compute_prelint_hashes(content: str) -> tuple[str, str]:
    """Plan file contentの全文SHA256と、加工後テキストのSHA256のペアを返す。

    加工後テキストは`## 背景`配下フェンスブロック除去
    （`strip_background_text_blocks`）と`## 変更内容`配下diffマーカー除去
    （`strip_diff_markers_in_changes_blocks`）を順に適用した結果で、
    scratchpad出力の加工パイプラインと同一の変換内容にする。
    PreToolUseとPostToolUseが同一ロジックで2種類のSHA256を算出するため、本関数に共通化する。
    PreToolUseはWriteのcontentから、PostToolUseはscratchpadファイル内容から呼び出す。
    """
    full_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    stripped = strip_diff_markers_in_changes_blocks(strip_background_text_blocks(content))
    stripped_sha = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
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
