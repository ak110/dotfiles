"""プロジェクト規範と共有スキルをClaude CodeとCodexで共用できる形へ整える。"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from agent_toolkit._atk import outcome
from agent_toolkit._git import command as _git_command

_AGENTS_MD = "AGENTS.md"
_CLAUDE_MD = "CLAUDE.md"
_CLAUDE_LOCAL_MD = "CLAUDE.local.md"
_CLAUDE_ADAPTER = "# CLAUDE.md\n\n@AGENTS.md\n"
_SKILLS_LINK_TARGET = "../.claude/skills"


def _classify_instruction_path(path: Path, *, expected_target: str) -> str:
    if path.is_symlink():
        return "expected_symlink" if os.readlink(path) == expected_target else "other_symlink"
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "adapter" if _is_claude_adapter(path) else "regular_file"
    return "unknown"


def _is_claude_adapter(path: Path) -> bool:
    try:
        non_empty = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return False
    return non_empty in [["@AGENTS.md"], ["# CLAUDE.md", "@AGENTS.md"]]


def _git_value(target: Path, *arguments: str) -> str | None:
    result = _git_command.run(["-C", str(target), *arguments], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _exclude_from_git(target: Path, path: Path) -> None:
    toplevel = _git_value(target, "rev-parse", "--show-toplevel")
    common_dir = _git_value(target, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if toplevel is None or common_dir is None:
        return
    root = Path(toplevel).resolve()

    pattern = "/" + (path.parent.resolve() / path.name).relative_to(root).as_posix()
    exclude = Path(common_dir) / "info" / "exclude"
    previous = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if pattern in (line.strip() for line in previous.splitlines()):
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    separator = "" if not previous or previous.endswith("\n") else "\n"
    with exclude.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{separator}{pattern}\n")


def migrate_project_instructions(target: Path) -> None:
    agents_md = target / _AGENTS_MD
    claude_md = target / _CLAUDE_MD
    agents_kind = _classify_instruction_path(agents_md, expected_target=_CLAUDE_MD)
    claude_kind = _classify_instruction_path(claude_md, expected_target=_AGENTS_MD)
    has_local = (target / _CLAUDE_LOCAL_MD).exists()

    if agents_kind == "missing" and claude_kind == "missing":
        return
    if agents_kind == "missing" and claude_kind == "regular_file":
        claude_md.rename(agents_md)
        claude_kind = "missing"
    elif agents_kind == "expected_symlink" and claude_kind == "regular_file":
        agents_md.unlink()
        claude_md.rename(agents_md)
        claude_kind = "missing"
    elif agents_kind == "regular_file" and (claude_kind == "expected_symlink" or (claude_kind == "adapter" and not has_local)):
        claude_md.unlink()
        claude_kind = "missing"
    elif not (agents_kind == "regular_file" and claude_kind in {"missing", "adapter"}):
        raise ValueError(f"自動移行対象外の指示ファイル状態: {agents_md}={agents_kind}, {claude_md}={claude_kind}")

    if has_local:
        if claude_kind == "missing":
            claude_md.write_text(_CLAUDE_ADAPTER, encoding="utf-8", newline="\n")
        _exclude_from_git(target, claude_md)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _is_managed_skills_link(link: Path, source: Path) -> bool:
    r"""`.agents/skills`のリンクが`.claude/skills`を共有するものかを返す。

    Windowsのジャンクションは絶対パスを格納し、Gitが展開したリンクは区切りが`\`になるため、参照先の実体でも比べる。
    """
    if os.readlink(link) == _SKILLS_LINK_TARGET:
        return True
    return sys.platform == "win32" and link.exists() and link.resolve() == source.resolve()


def _ensure_skills_link(target: Path) -> None:
    source = target / ".claude" / "skills"
    link = target / ".agents" / "skills"
    if not source.exists():
        return
    link.parent.mkdir(exist_ok=True)
    if _is_link(link):
        if not _is_managed_skills_link(link, source):
            raise ValueError(f"期待しないリンク先: {link} -> {os.readlink(link)} （期待: {_SKILLS_LINK_TARGET}）")
        if link.exists():
            return
        # 以前の版がWindowsで作ったリンクは、相対パスの区切り`/`を解決できず壊れているため作り直す。
        link.unlink()
    elif link.exists():
        raise ValueError(f"シンボリックリンクではない項目を検出: {link}")
    if sys.platform == "win32":
        # シンボリックリンクは開発者モードか管理者権限が要る（WinError 1314）。ジャンクションは要らない。
        # pylint: disable=import-outside-toplevel,import-error
        import _winapi  # noqa: PLC0415 -- Windows専用

        _winapi.CreateJunction(str(source), str(link))
        # Git for Windowsはジャンクションを辿り、共有した中身を未追跡の複製として並べる。
        _exclude_from_git(target, link)
    else:
        link.symlink_to(_SKILLS_LINK_TARGET)


def _remove_skills_link(target: Path) -> None:
    link = target / ".agents" / "skills"
    if _is_link(link):
        if not _is_managed_skills_link(link, target / ".claude" / "skills"):
            raise ValueError(f"期待しないリンク先のため削除しません: {link} -> {os.readlink(link)}")
        link.unlink()
    elif link.exists():
        raise ValueError(f"シンボリックリンクではない項目を削除しません: {link}")
    agents_dir = target / ".agents"
    if agents_dir.is_dir() and not any(agents_dir.iterdir()):
        agents_dir.rmdir()


def _remove_rules_dir(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"規範ディレクトリではない項目を削除しません: {path}")
    shutil.rmtree(path)
    for parent in (path.parent, path.parent.parent):
        if parent.name not in {"rules", ".claude"} or not parent.exists() or any(parent.iterdir()):
            break
        parent.rmdir()


def _rules_paths(target: Path) -> tuple[Path, Path]:
    return target / ".claude" / "rules" / "agent-toolkit", target / ".claude" / "rules" / "agent-basics"


def build_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--clean", action="store_true", help="配置済みの共有リンクと、以前に複製した規範を削除する。")


def dispatch(args: argparse.Namespace) -> int:
    target = Path.cwd()
    try:
        if args.clean:
            _remove_skills_link(target)
            rules, legacy = _rules_paths(target)
            _remove_rules_dir(rules)
            _remove_rules_dir(legacy)
        else:
            migrate_project_instructions(target)
            _ensure_skills_link(target)
    except (OSError, ValueError) as error:
        outcome.report_failure(
            f"プロジェクトの設定を完了できません: {error}",
            next_action="表示されたパスを確認し、手動で退避または削除してから`atk setup-project`を再実行する",
        )
        return 1
    outcome.report_success(f"プロジェクト設定を{'削除した' if args.clean else '整えた'}: {target}")
    return 0
