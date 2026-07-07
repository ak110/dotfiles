"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_formatters.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import pathlib

_SUMMARY_MAX_LEN = 40


def _parse_target_repo(text: str) -> str:
    """フィードバックファイル本文先頭のfrontmatterからtarget_repoを抽出する。"""
    if not text.startswith("---\n"):
        return "(unknown)"
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return "(unknown)"
    for line in text[4:end].splitlines():
        if line.startswith("target_repo:"):
            return line.split(":", 1)[1].strip()
    return "(unknown)"


def _truncate_summary(line: str) -> str:
    """要約1行を`_SUMMARY_MAX_LEN`で切り詰め、超過時は`...`を付与する。"""
    if len(line) > _SUMMARY_MAX_LEN:
        return f"{line[:_SUMMARY_MAX_LEN]}..."
    return line


def _body_summary(text: str) -> str:
    """フィードバック本文からfrontmatterを除いた先頭要約を1行で返す。

    本文先頭行が`_SUMMARY_MAX_LEN`を超える場合は切り詰めて`...`を付与する。
    """
    body = text
    if text.startswith("---\n"):
        try:
            end = text.index("\n---\n", 4)
            body = text[end + 5 :]
        except ValueError:
            body = text
    stripped = body.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    return _truncate_summary(first_line)


def _tbd_body_summary(text: str) -> str:
    r"""TBD本文（`## 質問\n\n{message}\n\n## 回答\n\n`形式）から質問本文の先頭要約を1行で返す。

    frontmatterと`## 質問`見出し行をスキップし、質問本文の先頭行を`_body_summary`と同じ
    切り詰めルールで返す。
    """
    body = text
    if text.startswith("---\n"):
        try:
            end = text.index("\n---\n", 4)
            body = text[end + 5 :]
        except ValueError:
            body = text
    for line in body.splitlines():
        stripped_line = line.strip()
        if not stripped_line or stripped_line == "## 質問":
            continue
        return _truncate_summary(stripped_line)
    return ""


def _shorten_home(path: pathlib.Path, home: pathlib.Path) -> str:
    """$HOME配下のパスを`~/...`へ短縮する。外なら絶対パスのまま返す。"""
    try:
        rel = path.relative_to(home)
    except ValueError:
        return str(path)
    return f"~/{rel}"
