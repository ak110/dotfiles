"""プロジェクトのClaude Code設定を初期化・同期するコマンド。

設計思想:
- .claude/rules/agent.md: 汎用的なエージェント向けの指示。
  ~/dotfiles で最新版を管理し、claudize コマンドで各プロジェクトへ配布する。
  .claude/rules/ 内のファイルは Claude Code が自動読み込みするため、
  CLAUDE.md からの明示的な参照は不要。
- .claude/rules/*.md (言語別): 言語固有のルール。
  該当言語のファイルが存在し、かつルールが未配置の場合のみ配布する。
- CLAUDE.md: プロジェクト固有の指示。プロジェクトごとにカスタマイズする。

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

_AGENT_RULE = Path(".claude") / "rules" / "agent.md"
_SECTION_MARKER = "## 関連ドキュメント"
# 旧形式の移行時に除外する参照パターン
_LEGACY_EXCLUDE_PATTERNS = {"@CLAUDE.project.md"}

# 条件付き言語別ルール: (ルールファイル名, 検出用glob)
_CONDITIONAL_RULES: list[tuple[str, list[str]]] = [
    ("python.md", ["*.py"]),
    ("python-test.md", ["*.py"]),
    ("typescript.md", ["*.ts", "*.tsx"]),
    ("typescript-test.md", ["*.ts", "*.tsx"]),
]
# 無条件で配布するルール
_UNCONDITIONAL_RULES: list[str] = ["markdown.md", "rules.md", "skills.md"]


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="DEBUG")
    template_dir = Path.home() / "dotfiles" / ".claude" / "rules"
    target_dir = Path.cwd()
    _claudize(target_dir, template_dir)


def _claudize(target_dir: Path, template_dir: Path) -> None:
    """本体ロジック。テスト時にパスを差し替え可能にするため分離。"""
    # テンプレート読み込み
    template_path = template_dir / "agent.md"
    if not template_path.exists():
        logger.error("テンプレートが見つかりません: %s", template_path)
        sys.exit(1)
    template_content = template_path.read_text(encoding="utf-8")

    claude_md = target_dir / "CLAUDE.md"
    project_md = target_dir / "CLAUDE.project.md"
    base_md = target_dir / "CLAUDE.base.md"
    agent_md = target_dir / _AGENT_RULE

    # パターン判定
    pattern = _detect_pattern(claude_md, project_md, base_md, agent_md)

    if pattern == "A":
        _handle_initial(claude_md)
    elif pattern == "B":
        _handle_legacy_migration(claude_md, project_md, agent_md)
    elif pattern == "E":
        _handle_intermediate_migration(claude_md, base_md, agent_md)
    elif pattern == "C":
        _handle_new_format(claude_md)
    elif pattern == "D":
        _handle_claude_md_only(claude_md)

    # agent.md をテンプレートで上書き
    agent_md.parent.mkdir(parents=True, exist_ok=True)
    agent_md.write_text(template_content, encoding="utf-8")
    logger.info("上書き: %s", agent_md)

    # 言語別ルールの配布
    _sync_lang_rules(target_dir, template_dir)


def _detect_pattern(claude_md: Path, project_md: Path, base_md: Path, agent_md: Path) -> str:
    """ファイル存在状態から処理パターンを判定する。"""
    has_project = project_md.exists()
    has_md = claude_md.exists()
    has_base = base_md.exists()
    has_agent = agent_md.exists()

    # エラーケース: base と agent が同居
    if has_base and has_agent:
        _die("CLAUDE.base.md と .claude/rules/agent.md が同居しています。手動で確認してください。")

    if has_project:
        if has_agent:
            _die("CLAUDE.project.md と .claude/rules/agent.md が同居しています。")
        if not has_md and not has_base:
            _die("CLAUDE.project.md のみ存在します (欠損状態)。手動で確認してください。")
        if not has_md and has_base:
            _die("CLAUDE.project.md と CLAUDE.base.md が存在しますが CLAUDE.md がありません。")
        if has_md and has_base:
            _die("3ファイル (CLAUDE.md, CLAUDE.project.md, CLAUDE.base.md) が同居しています。")
        # has_md and not has_base and not has_agent
        return "B"

    if has_base:
        # 中間形式: CLAUDE.base.md → .claude/rules/agent.md への移行
        return "E"

    if has_agent:
        # 最新形式
        return "C"

    if has_md:
        # CLAUDE.md のみ
        return "D"

    if not has_md and has_agent:
        return "C"
    if not has_md and not has_agent:
        return "A"

    return "A"


def _handle_initial(claude_md: Path) -> None:
    """パターンA: 初回。CLAUDE.md を新規作成。"""
    content = "# カスタム指示\n"
    claude_md.write_text(content, encoding="utf-8")
    logger.info("作成: %s", claude_md)


def _handle_legacy_migration(claude_md: Path, project_md: Path, agent_md: Path) -> None:
    """パターンB: 旧形式からの移行。"""
    old_md_content = claude_md.read_text(encoding="utf-8")
    project_md_content = project_md.read_text(encoding="utf-8")

    # 旧 CLAUDE.md の関連ドキュメントセクションから追加コンテンツを抽出
    extra_lines = _extract_extra_content(old_md_content)

    # CLAUDE.project.md の内容をベースに新 CLAUDE.md を構築
    new_content = project_md_content
    new_content = _remove_base_reference(new_content)
    new_content = _remove_legacy_references(new_content)
    if extra_lines:
        new_content = _merge_into_section(new_content, extra_lines)

    # git mv でリネームし、履歴を追跡可能にする
    is_git = _is_git_tracked(claude_md)
    if is_git:
        # 旧 CLAUDE.md → .claude/rules/agent.md (テンプレートなので後で上書きされる)
        agent_md.parent.mkdir(parents=True, exist_ok=True)
        _git_mv(claude_md, agent_md)
        # CLAUDE.project.md → CLAUDE.md
        _git_mv(project_md, claude_md)
    else:
        project_md.unlink()

    claude_md.write_text(new_content, encoding="utf-8")
    logger.info("移行: %s", claude_md)
    logger.info("移行完了")


def _handle_intermediate_migration(claude_md: Path, base_md: Path, agent_md: Path) -> None:
    """パターンE: 中間形式からの移行。CLAUDE.base.md → .claude/rules/agent.md"""
    # .claude/rules/ ディレクトリ作成
    agent_md.parent.mkdir(parents=True, exist_ok=True)

    # CLAUDE.base.md を移動
    is_git = _is_git_tracked(base_md)
    if is_git:
        _git_mv(base_md, agent_md)
    else:
        base_md.rename(agent_md)

    # CLAUDE.md から @CLAUDE.base.md 参照を除去
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        updated = _remove_base_reference(content)
        if updated != content:
            claude_md.write_text(updated, encoding="utf-8")
            logger.info("参照除去: %s", claude_md)

    logger.info("移行完了: CLAUDE.base.md → .claude/rules/agent.md")


def _handle_new_format(claude_md: Path) -> None:
    """パターンC: 最新形式 (既に移行済み)。"""
    if not claude_md.exists():
        return
    content = claude_md.read_text(encoding="utf-8")

    # チェック: 旧形式の残骸がないか
    _check_not_legacy(content)

    # 残存する @CLAUDE.base.md 参照があれば除去
    updated = _remove_base_reference(content)
    if updated != content:
        claude_md.write_text(updated, encoding="utf-8")
        logger.info("参照除去: %s", claude_md)


def _handle_claude_md_only(claude_md: Path) -> None:
    """パターンD: CLAUDE.md のみ。"""
    content = claude_md.read_text(encoding="utf-8")

    # チェック: 旧形式の残骸がないか
    _check_not_legacy(content)

    # 残存する @CLAUDE.base.md 参照があれば除去
    updated = _remove_base_reference(content)
    if updated != content:
        claude_md.write_text(updated, encoding="utf-8")
        logger.info("参照除去: %s", claude_md)


def _sync_lang_rules(target_dir: Path, template_dir: Path) -> None:
    """言語別ルールを配布する。"""
    rules_dir = target_dir / ".claude" / "rules"

    # 無条件ルール (既存でなければ配布)
    for rule_name in _UNCONDITIONAL_RULES:
        _copy_rule_if_absent(rules_dir / rule_name, template_dir / rule_name)

    # 条件付きルール (該当ファイルが存在し、かつルールが未配置の場合のみ)
    for rule_name, globs in _CONDITIONAL_RULES:
        dst = rules_dir / rule_name
        if dst.exists():
            # 既存でも差分チェックのためテンプレートと比較
            _copy_rule_if_absent(dst, template_dir / rule_name)
            continue
        if not _has_files(target_dir, globs):
            continue
        _copy_rule_if_absent(dst, template_dir / rule_name)


def _copy_rule_if_absent(dst: Path, src: Path) -> None:
    """テンプレートからルールをコピーする (既存ならスキップ)。"""
    if not src.exists():
        return
    if dst.exists():
        # 差分がある場合は通知
        if dst.read_text(encoding="utf-8") != src.read_text(encoding="utf-8"):
            logger.warning("差分あり: %s (code --diff %s %s で確認)", dst, src, dst)
        return
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    logger.info("配布: %s", dst)


def _has_files(target_dir: Path, globs: list[str]) -> bool:
    """指定globに該当するファイルがtarget_dir内に存在するか判定する。

    `.` で始まるディレクトリ内のファイルは無視する。
    """
    for pattern in globs:
        for path in target_dir.rglob(pattern):
            parts = path.relative_to(target_dir).parts[:-1]
            if not any(p.startswith(".") for p in parts):
                return True
    return False


def _check_not_legacy(content: str) -> None:
    """CLAUDE.md が旧形式の残骸でないことを検証する。"""
    for line in content.splitlines():
        if any(pat in line for pat in _LEGACY_EXCLUDE_PATTERNS):
            _die("CLAUDE.md に @CLAUDE.project.md 参照があります。手動で確認してください。")


def _remove_base_reference(content: str) -> str:
    """CLAUDE.md から @CLAUDE.base.md 参照とその前後の空行を除去する。"""
    lines = content.splitlines(keepends=True)
    filtered: list[str] = []
    for line in lines:
        if line.strip() == "@CLAUDE.base.md":
            # 参照行の直前の空行も除去
            while filtered and filtered[-1].strip() == "":
                filtered.pop()
            continue
        filtered.append(line)
    return "".join(_collapse_blank_lines(filtered))


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
    """Git mv でファイルをリネームする。異なるディレクトリ間の移動にも対応。"""
    # リポジトリルートを取得
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=src.parent,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    root = Path(repo_root)
    subprocess.run(
        ["git", "mv", str(src.relative_to(root)), str(dst.relative_to(root))],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    logger.info("git mv: %s → %s", src.relative_to(root), dst.relative_to(root))


def _die(message: str) -> None:
    """エラーメッセージを表示して終了する。"""
    logger.error(message)
    sys.exit(1)


if __name__ == "__main__":
    _main()
