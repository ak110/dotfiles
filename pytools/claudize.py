"""プロジェクトのClaude Code設定を初期化・同期するコマンド。

設計思想:
- CLAUDE.base.md: 汎用的なエージェント向けの指示。
  ~/dotfiles で最新版を管理し、claudize コマンドで各プロジェクトへ配布する。
- CLAUDE.md: プロジェクト固有の指示。プロジェクトごとにカスタマイズする。
  先頭付近の @CLAUDE.base.md でベース指示を参照する。

運用方針:
- 適用先は基本的にバージョン管理下にあるため、多少の破壊的変更は許容する。
- 想定外のファイル状態はエラー終了し、手動介入を求める。
"""

import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_SECTION_MARKER = "## 関連ドキュメント"
_BASE_REF = "@CLAUDE.base.md"
# 旧形式の移行時に除外する参照パターン
_LEGACY_EXCLUDE_PATTERNS = {"@CLAUDE.project.md"}


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="DEBUG")
    template_path = Path.home() / "dotfiles" / "CLAUDE.base.md"
    target_dir = Path.cwd()
    _claudize(target_dir, template_path)


def _claudize(target_dir: Path, template_path: Path) -> None:
    """本体ロジック。テスト時にパスを差し替え可能にするため分離。"""
    # テンプレート読み込み
    if not template_path.exists():
        logger.error("テンプレートが見つかりません: %s", template_path)
        sys.exit(1)
    template_content = template_path.read_text(encoding="utf-8")

    claude_md = target_dir / "CLAUDE.md"
    project_md = target_dir / "CLAUDE.project.md"
    base_md = target_dir / "CLAUDE.base.md"

    # パターン判定 (全8状態を明示的に列挙)
    pattern = _detect_pattern(claude_md, project_md, base_md)

    if pattern == "A":
        _handle_initial(claude_md)
    elif pattern == "B":
        _handle_legacy_migration(claude_md, project_md)
    elif pattern == "C":
        _handle_new_format(claude_md)
    elif pattern == "D":
        _handle_claude_md_only(claude_md)

    # 最後に CLAUDE.base.md をテンプレートで上書き
    base_md.write_text(template_content, encoding="utf-8")
    logger.info("上書き: %s", base_md)


def _detect_pattern(claude_md: Path, project_md: Path, base_md: Path) -> str:
    """ファイル存在状態から処理パターンを判定する。"""
    has_project = project_md.exists()
    has_md = claude_md.exists()
    has_base = base_md.exists()

    if has_project:
        if not has_md and not has_base:
            _die("CLAUDE.project.md のみ存在します (欠損状態)。手動で確認してください。")
        if not has_md and has_base:
            _die("CLAUDE.project.md と CLAUDE.base.md が存在しますが CLAUDE.md がありません。")
        if has_md and has_base:
            _die("3ファイル (CLAUDE.md, CLAUDE.project.md, CLAUDE.base.md) が同居しています。")
        # has_md and not has_base
        return "B"

    if not has_md and not has_base:
        return "A"
    if not has_md and has_base:
        _die("CLAUDE.base.md のみ存在します (CLAUDE.md がありません)。")
    if has_md and has_base:
        return "C"
    # has_md and not has_base
    return "D"


def _handle_initial(claude_md: Path) -> None:
    """パターンA: 初回。CLAUDE.md を新規作成。"""
    content = "# カスタム指示\n\n@CLAUDE.base.md\n"
    claude_md.write_text(content, encoding="utf-8")
    logger.info("作成: %s", claude_md)


def _handle_legacy_migration(claude_md: Path, project_md: Path) -> None:
    """パターンB: 旧形式からの移行。"""
    old_md_content = claude_md.read_text(encoding="utf-8")
    project_md_content = project_md.read_text(encoding="utf-8")

    # 旧 CLAUDE.md の関連ドキュメントセクションから追加コンテンツを抽出
    extra_lines = _extract_extra_content(old_md_content)

    # CLAUDE.project.md の内容をベースに新 CLAUDE.md を構築
    new_content = project_md_content
    new_content = _ensure_base_reference(new_content)
    new_content = _remove_legacy_references(new_content)
    if extra_lines:
        new_content = _merge_into_section(new_content, extra_lines)

    # git mv でリネームし、履歴を追跡可能にする
    base_md = claude_md.parent / "CLAUDE.base.md"
    is_git = _is_git_tracked(claude_md)
    if is_git:
        # 旧 CLAUDE.md → CLAUDE.base.md (テンプレートなので後で上書きされる)
        _git_mv(claude_md, base_md)
        # CLAUDE.project.md → CLAUDE.md
        _git_mv(project_md, claude_md)
    else:
        project_md.unlink()

    claude_md.write_text(new_content, encoding="utf-8")
    logger.info("移行: %s", claude_md)
    logger.info("移行完了")


def _handle_new_format(claude_md: Path) -> None:
    """パターンC: 新形式 (既に移行済み)。"""
    content = claude_md.read_text(encoding="utf-8")

    # チェック: 旧形式の残骸がないか
    _check_not_legacy(content)

    # @CLAUDE.base.md 参照を確認・修復
    updated = _ensure_base_reference(content)
    if updated != content:
        claude_md.write_text(updated, encoding="utf-8")
        logger.info("参照修復: %s", claude_md)


def _handle_claude_md_only(claude_md: Path) -> None:
    """パターンD: CLAUDE.md のみ。"""
    content = claude_md.read_text(encoding="utf-8")

    # チェック: 旧形式の残骸がないか
    _check_not_legacy(content)

    # @CLAUDE.base.md 参照を確認・追加
    updated = _ensure_base_reference(content)
    if updated != content:
        claude_md.write_text(updated, encoding="utf-8")
        logger.info("参照追加: %s", claude_md)


def _check_not_legacy(content: str) -> None:
    """CLAUDE.md が旧形式の残骸でないことを検証する。"""
    for line in content.splitlines():
        if any(pat in line for pat in _LEGACY_EXCLUDE_PATTERNS):
            _die("CLAUDE.md に @CLAUDE.project.md 参照があります。手動で確認してください。")


def _ensure_base_reference(content: str) -> str:
    """先頭の # 見出し行の直後に @CLAUDE.base.md 参照を確保する。

    期待する形式:
        # 見出し
        (空行)
        @CLAUDE.base.md
        (空行)
        ...残りの内容...
    """
    lines = content.splitlines(keepends=True)

    # 先頭の # 見出し行を探す
    heading_idx = None
    for i, line in enumerate(lines):
        if line.startswith("# "):
            heading_idx = i
            break

    # 既に正しい位置にあるか確認
    if heading_idx is not None:
        # 見出し + 空行 + @CLAUDE.base.md のパターンを確認
        ref_idx = heading_idx + 2  # 見出し行 + 空行の次
        if (
            heading_idx + 1 < len(lines)
            and lines[heading_idx + 1].strip() == ""
            and ref_idx < len(lines)
            and lines[ref_idx].strip() == _BASE_REF
        ):
            return content  # 既に正しい位置にある

    ref_line = _BASE_REF + "\n"

    # 既存の @CLAUDE.base.md 行とその前後の空行を除去
    filtered: list[str] = []
    for line in lines:
        if line.strip() == _BASE_REF:
            # 参照行の直前の空行も除去
            while filtered and filtered[-1].strip() == "":
                filtered.pop()
            continue
        filtered.append(line)
    # 除去後に連続空行ができたら1つに圧縮
    lines = _collapse_blank_lines(filtered)

    # 見出し行を再探索 (行が除去された可能性があるため)
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            insert_idx = i + 1
            break

    # 見出し直後に 空行 + 参照 + 空行 を挿入 (既存の空行があればそれを活用)
    has_blank_after = insert_idx < len(lines) and lines[insert_idx].strip() == ""
    if has_blank_after:
        # 既存の空行の後に参照 + 空行を挿入
        lines[insert_idx + 1 : insert_idx + 1] = [ref_line, "\n"]
    else:
        lines[insert_idx:insert_idx] = ["\n", ref_line, "\n"]

    return "".join(lines)


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    """連続する空行を1つに圧縮する。"""
    result: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        result.append(line)
        prev_blank = is_blank
    return result


def _extract_extra_content(old_md_content: str) -> list[str]:
    """旧 CLAUDE.md の関連ドキュメントセクションから追加コンテンツを抽出する。"""
    section = _extract_section_from(old_md_content, _SECTION_MARKER)
    if section is None:
        return []

    result = []
    for line in section.splitlines():
        # 見出し行自体は除外
        if line.strip() == _SECTION_MARKER:
            continue
        # 旧形式の除外パターンに該当する行は除外
        if any(pat in line for pat in _LEGACY_EXCLUDE_PATTERNS):
            continue
        # 空行も除外 (後で再構成するため)
        if line.strip() == "":
            continue
        result.append(line)
    return result


def _merge_into_section(content: str, extra_lines: list[str]) -> str:
    """関連ドキュメントセクションに追加コンテンツを統合する。"""
    section = _extract_section_from(content, _SECTION_MARKER)
    if section is not None:
        # 既存セクション内の行を取得
        existing_lines = set()
        for line in section.splitlines():
            stripped = line.strip()
            if stripped and stripped != _SECTION_MARKER:
                existing_lines.add(stripped)

        # 重複排除して追加
        new_lines = [line for line in extra_lines if line.strip() not in existing_lines]
        if not new_lines:
            return content

        # セクション末尾に追加
        section_end = content.find(section) + len(section)
        insert_content = "\n".join(new_lines) + "\n"
        if not content[:section_end].endswith("\n"):
            insert_content = "\n" + insert_content
        return content[:section_end] + insert_content + content[section_end:]
    else:
        # セクションがなければ末尾に追加
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + _SECTION_MARKER + "\n\n"
        content += "\n".join(extra_lines) + "\n"
        return content


def _remove_legacy_references(content: str) -> str:
    """@CLAUDE.project.md を含む行を除去する。"""
    lines = content.splitlines(keepends=True)
    return "".join(line for line in lines if not any(pat in line for pat in _LEGACY_EXCLUDE_PATTERNS))


def _extract_section_from(content: str, marker: str) -> str | None:
    """マーカー行から次の同レベル以上の見出しの手前までを返す。

    コードブロック内の見出しは無視する。
    """
    lines = content.splitlines(keepends=True)
    marker_level = len(marker) - len(marker.lstrip("#"))
    in_code_block = False
    start_idx = None

    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        # コードブロックの開始/終了を追跡
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if start_idx is None:
            # マーカー行を探す (行頭マッチ)
            if stripped == marker:
                start_idx = i
        else:
            # 同レベル以上の見出しで終端
            heading_match = re.match(r"^(#{1,6})\s", stripped)
            if heading_match and len(heading_match.group(1)) <= marker_level:
                return "".join(lines[start_idx:i])

    if start_idx is not None:
        return "".join(lines[start_idx:])
    return None


def _is_git_tracked(path: Path) -> bool:
    """ファイルが git 管理下にあるか判定する。"""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path.name],
            cwd=path.parent,
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _git_mv(src: Path, dst: Path) -> None:
    """Git mv でファイルをリネームする。"""
    subprocess.run(
        ["git", "mv", src.name, dst.name],
        cwd=src.parent,
        check=True,
        capture_output=True,
    )
    logger.info("git mv: %s → %s", src.name, dst.name)


def _die(message: str) -> None:
    """エラーメッセージを表示して終了する。"""
    logger.error(message)
    sys.exit(1)


if __name__ == "__main__":
    _main()
