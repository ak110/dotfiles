#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""計画ファイルの軽量機械チェック。

チェック対象は次の8点に限定する。
- `## 変更内容`「対象ファイル一覧」の`- [ ]`項目と`### \\`<パス>\\``見出しの1対1対応
- 各H3配下にコードブロック（フェンスで囲われた本文）が存在するか
- H3見出しのパスのうち、`（新設）`・`（廃止・削除）`マーカーが無いものの実在確認
- Markdownフェンスの入れ子整合（開始フェンスより長いフェンスが情報文字列付きで
  内側に現れていないか、閉じフェンスの検出位置が意図した位置と一致しない疑いが無いか）
- `## 実行方法`節に振り返り・セッション終了などのセッション運用工程が記載されていないか
  （計画ファイルのスコープは当該計画の実装・検証・コミット・レビューに限定する）
- `## 実行方法`節がスキル呼び出し構文で参照するスキル名が実在するか
- `### 対象ファイル一覧`で`（廃止・削除）`と注記された項目の対応するH3節`text`コードブロック内に、
  削除を指示する語が現れているか
- `#### 廃止・改名対象一覧`H4節が列挙する識別子（ファイルパス・関数名・クラス名・定数名）が
  リポジトリ内に定義として残存していないか

いずれも警告のみで終了コードは常に0とする（次工程移行のブロックは`ExitPlanMode`と
`plan-impl-executor`起動の2地点でメインエージェントが完成条件充足を判断して行う）。
"""

from __future__ import annotations

import collections.abc
import pathlib
import re
import sys

_CHECKBOX_RE = re.compile(r"^- \[ \] `([^`]+)`")
_H3_PATH_RE = re.compile(r"^### `([^`]+)`")
_NEW_OR_DELETED_RE = re.compile(r"（(新設|廃止・削除)")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})\s*(.*)$")
_H2_RE = re.compile(r"^## (.+)$")
_HEADING_RE = re.compile(r"^#{2,4}\s")
_SESSION_OPS_RE = re.compile(r"session-review|exit-session|振り返り|セッション終了|session-review-dotfiles")

# スキル呼び出しを示す構文に限定してスキル名を抽出する3パターン。
# コマンド名・パス・関数名等の無関係なバッククォート識別子を誤検出しないため、
# 接頭辞なしの任意識別子をスキル名と推定する方式は採らない。
_SKILL_TOOL_CALL_RE = re.compile(r"Skillツールで\s*`([^`]+)`")
_AGENT_TOOLKIT_SKILL_REF_RE = re.compile(r"`(agent-toolkit:[^`]+)`")
# 先頭が`/`かつ2文字目以降に`/`を含まない（絶対パスと区別するため）バッククォート識別子。
_SLASH_COMMAND_RE = re.compile(r"`(/[^`/]+)`")

_DELETED_TARGET_MARKER = "廃止・削除"
_DELETION_INSTRUCTION_WORDS = ("削除する", "廃止する")

_DEPRECATED_LIST_HEADING_RE = re.compile(r"^####\s+廃止・改名対象一覧\s*$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _looks_like_python_definition_identifier(name: str) -> bool:
    """識別子が`_`始まり・snake_case・UPPER_CASEいずれかのPython定義名らしい形式か判定する。

    コマンド名・サブコマンド名等（アンダースコアを含まない単純な単語）を
    検査対象から除外するため、アンダースコアを含む語のみ対象とする。
    """
    if not _IDENTIFIER_RE.fullmatch(name):
        return False
    if name.startswith("_"):
        return True
    if "_" not in name:
        return False
    return name == name.lower() or name == name.upper()


def _identifier_definition_pattern(name: str) -> re.Pattern[str]:
    """関数定義・クラス定義・定数定義のいずれかに識別子`name`が現れる行を検出する正規表現を返す。"""
    escaped = re.escape(name)
    return re.compile(
        rf"^\s*(async\s+)?def\s+{escaped}\s*\(|^\s*class\s+{escaped}\s*[(:]|^\s*{escaped}\s*(:[^=\n]+)?=",
        re.MULTILINE,
    )


def _is_excluded_repo_path(rel_parts: tuple[str, ...]) -> bool:
    """遡及走査から除外するパス（`.git`・`.claude/archive`配下）かを判定する。"""
    if ".git" in rel_parts:
        return True
    return ".claude" in rel_parts and "archive" in rel_parts


def _extract_checkbox_paths(text: str) -> list[str]:
    return [m.group(1) for line in text.splitlines() if (m := _CHECKBOX_RE.match(line))]


def _extract_h3_paths(text: str) -> list[str]:
    return [m.group(1) for line in text.splitlines() if (m := _H3_PATH_RE.match(line))]


def _h3_section_body(text: str, path: str) -> str:
    r"""`### \\`<path>\\``見出し配下の本文（次のH3見出し直前まで）を返す。見出しが無ければ空文字を返す。"""
    marker = f"### `{path}`"
    idx = text.find(marker)
    if idx < 0:
        return ""
    next_h3 = text.find("\n### `", idx + len(marker))
    return text[idx + len(marker) : next_h3 if next_h3 > 0 else len(text)]


def _has_code_block_after(text: str, path: str) -> bool:
    return "```" in _h3_section_body(text, path)


def _extract_fenced_code_blocks(body: str, *, info_string: str) -> list[str]:
    """本文中の情報文字列が`info_string`と一致するフェンスコードブロックの内容を全出現分抽出する。"""
    blocks: list[str] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        m = _FENCE_RE.match(lines[i])
        if m and m.group(2).strip() == info_string:
            fence_char, fence_len = m.group(1)[0], len(m.group(1))
            close_re = re.compile(rf"^{re.escape(fence_char)}{{{fence_len},}}\s*$")
            j = i + 1
            content_lines: list[str] = []
            while j < len(lines) and not close_re.match(lines[j]):
                content_lines.append(lines[j])
                j += 1
            blocks.append("\n".join(content_lines))
            i = j + 1
            continue
        i += 1
    return blocks


def _iter_h2_sections(text: str, heading: str) -> list[str]:
    """`## <heading>`見出し配下の本文（次のH2直前まで）を全出現分列挙する。

    計画ファイルが完全なサンプル計画（`plan-mode/references/sample.md`等）を
    地の文へ埋め込む場合、同名H2見出しが複数出現し得る。どれが計画本体の
    見出しかを判定する高精度な手段は持たないため、全出現を対象に含めて
    検出漏れを避ける（非ブロックの軽量チェックのため、埋め込み例示への
    誤検出よりも本体の見落としを避けることを優先する）。
    """
    lines = text.splitlines()
    sections: list[str] = []
    start = None
    for i, line in enumerate(lines):
        m = _H2_RE.match(line)
        if m and m.group(1).strip() == heading:
            start = i + 1
            continue
        if start is not None and m:
            sections.append("\n".join(lines[start:i]))
            start = None
    if start is not None:
        sections.append("\n".join(lines[start:]))
    return sections


def _check_fence_nesting(text: str) -> list[str]:
    """開いたフェンスより長い情報文字列付きフェンスが内側に現れる箇所を検出する。"""
    warnings: list[str] = []
    stack: list[tuple[int, str, int]] = []  # (line_no, char, length)
    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        m = _FENCE_RE.match(line)
        if not m:
            continue
        fence, info = m.group(1), m.group(2).strip()
        char, length = fence[0], len(fence)
        if stack:
            top_line, top_char, top_len = stack[-1]
            if char == top_char and length >= top_len and info == "":
                stack.pop()
                continue
            if char == top_char and length >= top_len and info != "":
                warnings.append(
                    f"{line_no}行目: {top_line}行目で開いたフェンス（長さ{top_len}）以上の"
                    f"長さ{length}のフェンスが情報文字列`{info}`付きで内側に現れた。"
                    "埋め込み内容のフェンスより外側フェンスを長くする"
                )
            continue
        stack.append((line_no, char, length))
    for line_no, _char, length in stack:
        warnings.append(f"{line_no}行目: 長さ{length}のフェンスがファイル終端まで閉じていない疑いがある")
    return warnings


def _check_execution_method_scope(text: str) -> list[str]:
    """`## 実行方法`節に振り返り・セッション終了などのセッション運用工程が無いか検出する。"""
    warnings: list[str] = []
    for section_no, body in enumerate(_iter_h2_sections(text, "実行方法"), start=1):
        for line_no, line in enumerate(body.splitlines(), start=1):
            if _SESSION_OPS_RE.search(line):
                warnings.append(
                    f"実行方法節（{section_no}件目の出現）内({line_no}行目相当): "
                    "振り返り・セッション終了などのセッション運用工程が記載されている疑いがある。"
                    "計画ファイルのスコープは当該計画の実装・検証・コミット・レビューに限定し、"
                    "セッション運用工程は呼び出し元セッションが別途担う"
                )
    return warnings


def _extract_skill_references(body: str) -> list[str]:
    """本文からスキル呼び出しを示す構文に限定してスキル名を抽出する。"""
    names: list[str] = []
    names.extend(_SKILL_TOOL_CALL_RE.findall(body))
    names.extend(_AGENT_TOOLKIT_SKILL_REF_RE.findall(body))
    names.extend(raw[1:] for raw in _SLASH_COMMAND_RE.findall(body))
    return names


def _available_skill_names() -> set[str]:
    """`agent-toolkit/skills/*/SKILL.md`と`.claude/skills/*/SKILL.md`から利用可能なスキル名一覧を取得する。"""
    names: set[str] = set()
    agent_toolkit_root = pathlib.Path(__file__).resolve().parents[3]
    for skill_md in agent_toolkit_root.glob("skills/*/SKILL.md"):
        names.add(f"agent-toolkit:{skill_md.parent.name}")
    local_skills_root = pathlib.Path.cwd() / ".claude" / "skills"
    if local_skills_root.is_dir():
        for skill_md in local_skills_root.glob("*/SKILL.md"):
            names.add(skill_md.parent.name)
    return names


def _check_skill_names_exist(text: str) -> list[str]:
    """`## 実行方法`節が参照するスキル名が利用可能なスキル一覧に実在するか検出する。"""
    warnings: list[str] = []
    available = _available_skill_names()
    for body in _iter_h2_sections(text, "実行方法"):
        for name in dict.fromkeys(_extract_skill_references(body)):
            if name in available:
                continue
            candidate = name.removeprefix("agent-toolkit:") if name.startswith("agent-toolkit:") else f"agent-toolkit:{name}"
            hint = f"（接頭辞違いの候補: `{candidate}`）" if candidate in available else "（接頭辞違いの候補も無し）"
            warnings.append(f"実在しないスキル名の疑い: `{name}`{hint}")
    return warnings


def _check_deletion_instruction_present(text: str) -> list[str]:
    """`（廃止・削除）`と注記された項目のH3節`text`コードブロック内に削除指示語が現れるか検出する。"""
    warnings: list[str] = []
    deleted_paths = [
        m.group(1) for line in text.splitlines() if _DELETED_TARGET_MARKER in line and (m := _CHECKBOX_RE.match(line))
    ]
    for path in deleted_paths:
        body = _h3_section_body(text, path)
        text_blocks = _extract_fenced_code_blocks(body, info_string="text")
        # `text`以外の情報文字列（`python`等）のコードブロックのみが存在する場合、
        # 「コードブロックが無いH3」検査（`_has_code_block_after`、任意の情報文字列を許容）は
        # 通過するが本検査は不成立のままとなる。両検査の対象コードブロック種別を揃えるため、
        # `text`ブロックが1件も無い場合も食い違いとして警告し、他検査への委譲で見逃さない。
        combined = "\n".join(text_blocks)
        if not any(word in combined for word in _DELETION_INSTRUCTION_WORDS):
            warnings.append(
                f"指定内容の食い違いの疑い: `{path}`は対象ファイル一覧で（廃止・削除）と注記されているが、"
                "対応するH3節のtextコードブロック内に削除を指示する語（「削除する」「廃止する」）が見当たらない"
            )
    return warnings


def _extract_deprecated_identifiers(text: str) -> list[str]:
    """`#### 廃止・改名対象一覧`H4節が列挙するバッククォート囲み識別子を全出現分抽出する。"""
    identifiers: list[str] = []
    lines = text.splitlines()
    in_section = False
    for line in lines:
        if _DEPRECATED_LIST_HEADING_RE.match(line):
            in_section = True
            continue
        if in_section and _HEADING_RE.match(line):
            in_section = False
            continue
        if in_section:
            identifiers.extend(re.findall(r"`([^`]+)`", line))
    return identifiers


def _iter_repo_files(repo_root: pathlib.Path, plan_path: pathlib.Path) -> collections.abc.Iterator[pathlib.Path]:
    """遡及走査対象のファイルを、`.git`・`.claude/archive`・計画ファイル自身を除外して列挙する。"""
    plan_resolved = plan_path.resolve()
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded_repo_path(path.relative_to(repo_root).parts):
            continue
        if path.resolve() == plan_resolved:
            continue
        yield path


def _check_deprecated_identifiers_removed(text: str, plan_path: pathlib.Path) -> list[str]:
    """`#### 廃止・改名対象一覧`が列挙する識別子の定義箇所が残存していないか検出する。"""
    warnings: list[str] = []
    identifiers = _extract_deprecated_identifiers(text)
    if not identifiers:
        return warnings
    repo_root = pathlib.Path.cwd()
    for name in identifiers:
        if "/" in name:
            if (repo_root / name).exists():
                warnings.append(f"廃止・改名対象一覧の識別子が残存している疑い: `{name}`（ファイルが実在する）")
            continue
        if not _looks_like_python_definition_identifier(name):
            # コマンド名・サブコマンド名等は検査対象外とする
            continue
        pattern = _identifier_definition_pattern(name)
        for path in _iter_repo_files(repo_root, plan_path):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if pattern.search(content):
                warnings.append(f"廃止・改名対象一覧の識別子が残存している疑い: `{name}`（{path}に定義が残存）")
                break
    return warnings


def main() -> int:
    """計画ファイル1件を対象に軽量機械チェックを実行し、警告をstderrへ出力する。"""
    if len(sys.argv) != 2:
        print("usage: check_plan_file.py <plan-file-path>", file=sys.stderr)
        return 0
    plan_path = pathlib.Path(sys.argv[1])
    text = plan_path.read_text(encoding="utf-8")
    warnings: list[str] = []

    checkbox_paths = _extract_checkbox_paths(text)
    h3_paths = _extract_h3_paths(text)
    missing_h3 = [p for p in checkbox_paths if p not in h3_paths]
    missing_checkbox = [p for p in h3_paths if p not in checkbox_paths]
    if missing_h3:
        warnings.append(f"H3見出しが無い対象ファイル: {missing_h3}")
    if missing_checkbox:
        warnings.append(f"対象ファイル一覧に無いH3見出し: {missing_checkbox}")

    for path in checkbox_paths:
        if not _has_code_block_after(text, path):
            warnings.append(f"コードブロックが無いH3: {path}")

    for path in h3_paths:
        if _NEW_OR_DELETED_RE.search(text.split(f"### `{path}`", 1)[0][-40:]):
            continue
        checkbox_line = next(
            (line for line in text.splitlines() if f"`{path}`" in line and line.startswith("- [ ]")),
            "",
        )
        if _NEW_OR_DELETED_RE.search(checkbox_line):
            continue
        if not (pathlib.Path(path).is_absolute() or (plan_path.parents[-1] / path).exists()):
            resolved = pathlib.Path.cwd() / path
            if not resolved.exists():
                warnings.append(f"実在確認できないパス: {path}")

    warnings.extend(_check_fence_nesting(text))
    warnings.extend(_check_execution_method_scope(text))
    warnings.extend(_check_skill_names_exist(text))
    warnings.extend(_check_deletion_instruction_present(text))
    warnings.extend(_check_deprecated_identifiers_removed(text, plan_path))

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
