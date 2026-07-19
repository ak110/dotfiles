#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# ///
"""規範文書間の`<filename>「<title>」節`形式参照に対する見出し実在検査を行うpre-commitローカルhook。

`agent-toolkit/rules/`・`agent-toolkit/skills/`配下の`.md`ファイルを対象に、
`<path>「<title>」節`形式（`path`は`.md`パスまたは`agent-toolkit:<skill-name>`形式）の参照を抽出し、
参照先ファイルの実在と参照先ファイル内H2/H3見出し（`title`）の実在を照合する。
計画ファイル本文専用ロジックを持つ`agent-toolkit/skills/plan-mode/scripts/_check_line_ref_section_ref.py`
とは独立の実装とし、当該ファイルの単一契約（呼出元は`check_line_ref.py`のみ）は変更しない。
正規表現パターンは循環import回避のため意図的に複製する
（`_check_line_ref_section_ref.py`冒頭docstringが説明する複製方針と同じ扱い）。

パス解決は次の優先順で行う。
- スラッシュを含むパス: リポジトリルート相対パスとして解決する
- `agent-toolkit:<skill-name>`形式: `agent-toolkit/skills/<skill-name>/SKILL.md`へ解決する
- スラッシュを含まない裸ファイル名: 参照元ファイルと同一ディレクトリを最優先候補とし、
  見つからない場合は`agent-toolkit/rules/`・`agent-toolkit/skills/**/references/`・
  `agent-toolkit/skills/**/SKILL.md`配下をファイル名で走査する。候補が一意に定まらない場合は
  「解決不能（曖昧）」として違反へ計上する
"""

from __future__ import annotations

import pathlib
import re
import sys

_SECTION_REF_PATTERN = re.compile(
    r"(?P<path>`?[A-Za-z0-9_.{}/:-]+\.md`?|`?agent-toolkit:[A-Za-z0-9_-]+`?)「(?P<section>[^」]+)」節"
)
_HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.MULTILINE)
_SKILL_REF_RE = re.compile(r"^agent-toolkit:([A-Za-z0-9_-]+)$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _strip_backticks(token: str) -> str:
    return token.strip("`")


def _strip_fenced_blocks(content: str) -> str:
    """フェンスコードブロック（```〜```/````〜````等）の内容を除去する。

    サンプル・テンプレート例示文書内の参照風文字列（実在照合対象外の架空パス）を
    誤検出しないための前処理。フェンス行自体は空行に置換し行番号はずれない。
    """
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in lines:
        m = _FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_fence = False
                fence_marker = ""
            out.append("\n")
            continue
        out.append("\n" if in_fence else line)
    return "".join(out)


def _search_bases(repo_root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    return (
        repo_root / "agent-toolkit" / "rules",
        repo_root / "agent-toolkit" / "skills",
        repo_root / "agent-toolkit" / "agents",
        repo_root / "agent-toolkit" / "references",
    )


def _resolve_by_pattern(repo_root: pathlib.Path, pattern: str) -> pathlib.Path | str | None:
    """相対パスパターン一致で`_search_bases`配下を走査する（一意解決できない場合は"ambiguous"）。

    `pattern`はスラッシュを含んでもよい（`pathlib.Path.rglob`は多階層パターンに対応する）。
    """
    try:
        matches = [p for base in _search_bases(repo_root) for p in base.rglob(pattern) if p.is_file()]
    except (NotImplementedError, ValueError):
        return None
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return "ambiguous"
    return None


def _resolve_target(repo_root: pathlib.Path, source_path: pathlib.Path, raw_path: str) -> pathlib.Path | None | str:
    """`raw_path`を解決する。一意解決できない場合は"ambiguous"を返す。

    スラッシュを含むパスは優先順にリポジトリルート相対・参照元と同一ディレクトリ相対で解決を試みる。
    いずれも解決できない場合、他スキルの`references/`配下等を指す相対パス表記
    （例: `references/foo.md`）を許容するため、末尾のファイル名一致でのフォールバック検索を行う
    （`_check_line_ref_section_ref.py`が採用するファイル名一致フォールバック方針に揃える）。
    """
    token = _strip_backticks(raw_path)
    if m := _SKILL_REF_RE.match(token):
        candidate = repo_root / "agent-toolkit" / "skills" / m.group(1) / "SKILL.md"
        return candidate if candidate.is_file() else None
    if "/" in token:
        root_relative = repo_root / token
        if root_relative.is_file():
            return root_relative
        source_relative = source_path.parent / token
        if source_relative.is_file():
            return source_relative
        by_pattern = _resolve_by_pattern(repo_root, token)
        if by_pattern is not None:
            return by_pattern
        return _resolve_by_pattern(repo_root, token.rsplit("/", 1)[-1])
    same_dir = source_path.parent / token
    if same_dir.is_file():
        return same_dir
    return _resolve_by_pattern(repo_root, token)


def _check_file(repo_root: pathlib.Path, path: pathlib.Path) -> list[str]:
    violations: list[str] = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path}: 読み込み失敗: {exc}"]
    scan_target = _strip_fenced_blocks(content)
    for m in _SECTION_REF_PATTERN.finditer(scan_target):
        raw_path = m.group("path")
        section = m.group("section")
        if "{" in raw_path or "}" in raw_path:
            # テンプレート変数を含む動的パス（例: `docs/v{next}/OVERVIEW.md`）は
            # 静的解決対象外として検査をスキップする。
            continue
        resolved = _resolve_target(repo_root, path, raw_path)
        if isinstance(resolved, str):
            violations.append(f"{path}: 参照先ファイル解決不能（曖昧）: {raw_path}「{section}」節")
            continue
        if resolved is None:
            violations.append(f"{path}: 参照先ファイル不在: {raw_path}「{section}」節")
            continue
        target_text = resolved.read_text(encoding="utf-8")
        headings = {h.strip() for h in _HEADING_RE.findall(target_text)}
        if section.strip() not in headings:
            violations.append(f"{path}: 節名不在: {raw_path}「{section}」節")
    return violations


def main(argv: list[str]) -> int:
    """対象ファイル群の規範文書間参照を検査し、違反があれば1を返す。"""
    repo_root = pathlib.Path(__file__).resolve().parent.parent.parent
    all_violations: list[str] = []
    for path_str in argv:
        all_violations.extend(_check_file(repo_root, pathlib.Path(path_str)))
    if all_violations:
        print("規範文書間参照の実在検査違反:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
