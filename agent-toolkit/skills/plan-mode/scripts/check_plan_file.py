#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""計画ファイルの軽量機械チェック。

チェック対象は次の5点に限定する。
- `## 変更内容`「対象ファイル一覧」の`- [ ]`項目と`### \\`<パス>\\``見出しの1対1対応
- 各H3配下にコードブロック（フェンスで囲われた本文）が存在するか
- H3見出しのパスのうち、`（新設）`・`（廃止・削除）`マーカーが無いものの実在確認
- Markdownフェンスの入れ子整合（開始フェンスより長いフェンスが情報文字列付きで
  内側に現れていないか、閉じフェンスの検出位置が意図した位置と一致しない疑いが無いか）
- `## 実行方法`節に振り返り・セッション終了などのセッション運用工程が記載されていないか
  （計画ファイルのスコープは当該計画の実装・検証・コミット・レビューに限定する）

いずれも警告のみで終了コードは常に0とする（次工程移行のブロックは`ExitPlanMode`と
`plan-impl-executor`起動の2地点でメインエージェントが完成条件充足を判断して行う）。
"""

from __future__ import annotations

import pathlib
import re
import sys

_CHECKBOX_RE = re.compile(r"^- \[ \] `([^`]+)`")
_H3_PATH_RE = re.compile(r"^### `([^`]+)`")
_NEW_OR_DELETED_RE = re.compile(r"（(新設|廃止・削除)")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})\s*(.*)$")
_H2_RE = re.compile(r"^## (.+)$")
_SESSION_OPS_RE = re.compile(r"session-review|exit-session|振り返り|セッション終了|session-review-dotfiles")


def _extract_checkbox_paths(text: str) -> list[str]:
    return [m.group(1) for line in text.splitlines() if (m := _CHECKBOX_RE.match(line))]


def _extract_h3_paths(text: str) -> list[str]:
    return [m.group(1) for line in text.splitlines() if (m := _H3_PATH_RE.match(line))]


def _has_code_block_after(text: str, path: str) -> bool:
    marker = f"### `{path}`"
    idx = text.find(marker)
    if idx < 0:
        return False
    next_h3 = text.find("\n### `", idx + len(marker))
    body = text[idx : next_h3 if next_h3 > 0 else len(text)]
    return "```" in body


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

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
