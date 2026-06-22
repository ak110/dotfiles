#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""口語的な日本語表現の混入を検査する独立スクリプト。

agent-toolkitプラグイン同梱の辞書ファイル（`agent-toolkit/scripts/_colloquial_words.txt`と
`_colloquial_words_allow.txt`）を共通ロジック経由で読み込み、
対象ファイルから検出された口語表現を列挙する。
検出辞書をエージェントのコンテキストへ持ち込まない設計のため、
検出語そのものはstderr出力に含まれない。
危険語彙を含む隔離ファイル（`writing-standards/references/tone-examples.md`および`agent-standards/references/self-rationalization-loop-indicators.md`）も同じ理由で検査対象から除外する。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# agent-toolkit/scriptsをsys.pathに追加し、共通モジュールを読み込む。
# 本スクリプトはagent-toolkit/skills/writing-standards/scripts/配下に置かれる前提。
_AGENT_TOOLKIT_SCRIPTS = pathlib.Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_AGENT_TOOLKIT_SCRIPTS))
import _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# 抜粋の最大文字数。違反行を見やすく示す切り詰め幅。
_EXCERPT_LIMIT = 100

# ディレクトリ展開時に走査する拡張子。日本語が含まれうるテキストファイルを対象とする。
# `.md.tmpl`はchezmoiテンプレート由来の二重拡張子。`pathlib.Path.suffix`は最後の要素のみを返すため、
# 末尾一致判定で複合拡張子も対象に含める。`.tmpl`単独はテンプレート構文を含み誤検出が多いため対象外とする。
_DEFAULT_EXTENSIONS = frozenset({".md", ".py", ".txt", ".yaml", ".yml", ".toml", ".md.tmpl"})

# ディレクトリ展開時にスキップするディレクトリ名。VCS管理外・自動生成・依存物を除外する。
_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "site",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
    }
)

# 検査対象から自動的に外すファイル群。
# 辞書ファイル本体は自身を検査するとほぼ全行マッチするため除外する。
# 危険語彙を含む隔離ファイル（`tone-examples.md`・`self-rationalization-loop-indicators.md`）は
# 悪い例を意図的に記載しており、辞書ファイルと同等の理由で除外する。
# 自テストファイルは辞書の置換候補定義を発火させる入力テキスト（違反語そのもの）を含むため除外する。
_TONE_EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "references" / "tone-examples.md"
_SELF_RATIONALIZATION = (
    pathlib.Path(__file__).resolve().parents[2] / "agent-standards" / "references" / "self-rationalization-loop-indicators.md"
)
_SELF_TEST = pathlib.Path(__file__).resolve().parent / "check_colloquial_test.py"
_EXCLUDED_FILES = frozenset(
    {
        _colloquial_check.DENY_PATH.resolve(),
        _colloquial_check.ALLOW_PATH.resolve(),
        _TONE_EXAMPLES.resolve(),
        _SELF_RATIONALIZATION.resolve(),
        _SELF_TEST.resolve(),
    }
)


def main() -> int:
    """口語的な日本語表現の混入を検査するエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="口語的な日本語表現の混入を検査する。",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象のファイルまたはディレクトリ（複数指定可）",
    )
    args = parser.parse_args()

    deny_patterns = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
    allow_patterns = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)
    if not deny_patterns:
        # 辞書未配置・空でも安全側に通過させる。
        return 0

    targets = _expand_paths(args.paths)
    total = 0
    for path in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for hit in _colloquial_check.scan_text(text, deny_patterns, allow_patterns):
            print(_format_hit_line(path, hit), file=sys.stderr)
            total += 1
    if total:
        print(
            f"colloquial-check: {total} colloquial expression(s) detected. Rewrite using formal written-style Japanese.",
            file=sys.stderr,
        )
        return 1
    return 0


def _format_hit_line(
    path: pathlib.Path,
    hit: tuple[int, int, str, str, str | None],
) -> str:
    """検出ヒットを表示用の1行へ整形する。

    置換候補が与えられた場合は`[match] -> [候補] excerpt`形式で挿入する。
    抜粋は`_EXCERPT_LIMIT`を超える長さで末尾を省略する。
    """
    line_no, col, match_str, snippet, replacement = hit
    excerpt = snippet if len(snippet) <= _EXCERPT_LIMIT else snippet[:_EXCERPT_LIMIT] + "…"
    suggestion = f" -> [{replacement}]" if replacement else ""
    return f"{path}:{line_no}:{col} [{match_str}]{suggestion} {excerpt}"


def _expand_paths(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    """ファイル/ディレクトリ混在の入力を、検査対象ファイルの一覧へ展開する。

    ディレクトリは再帰的に対象拡張子のファイルを収集する。
    `_EXCLUDED_DIRS`配下と`_EXCLUDED_FILES`に含まれるファイルは除外する。
    順序の安定性のため、ディレクトリ展開分はpath順に並べる。
    """
    expanded: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for p in paths:
        if p.is_file():
            _add(expanded, seen, p)
        elif p.is_dir():
            for sub in sorted(p.rglob("*")):
                if not sub.is_file():
                    continue
                # 除外判定は引数ディレクトリ`p`からの相対パス成分のみで行う。
                # 絶対パス全体（`sub.parts`）で判定すると、引数ディレクトリ自身が`site`・`dist`等の
                # 汎用名を含む場合に配下全体が誤って除外される。
                if any(part in _EXCLUDED_DIRS for part in sub.relative_to(p).parts):
                    continue
                name_lower = sub.name.lower()
                if not any(name_lower.endswith(ext) for ext in _DEFAULT_EXTENSIONS):
                    continue
                _add(expanded, seen, sub)
    return expanded


def _add(out: list[pathlib.Path], seen: set[pathlib.Path], path: pathlib.Path) -> None:
    """重複・除外ファイルを除き出力リストへ追加する。"""
    resolved = path.resolve()
    if resolved in _EXCLUDED_FILES or resolved in seen:
        return
    seen.add(resolved)
    out.append(path)


if __name__ == "__main__":
    sys.exit(main())
