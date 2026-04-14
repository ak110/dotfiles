"""claudeを使ってコミットメッセージを生成してgit commitを実行する。"""

import argparse
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# 詳細差分の取得から除外するファイルパターン（コンテキスト長節約）
_EXCLUDE_PATTERNS = [
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
]

_DEFAULT_FORMAT = """\
Conventional Commits形式、日本語で記述すること。
形式: <type>[(<scope>)]: <description>
typeはfeat/fix/docs/style/refactor/test/chore/perf/ciのいずれか。
descriptionは日本語で書く。
"""


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="INFO")

    parser = argparse.ArgumentParser(description="claudeでコミットメッセージを生成してgit commitを実行する。")
    parser.add_argument("--amend", action="store_true", help="HEADのコミットをamendする。")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="コミットメッセージを表示するのみでコミットしない。",
    )
    parser.add_argument(
        "--model",
        "-m",
        default="sonnet",
        help="claudeのモデル（デフォルト: sonnet）。",
    )
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high", "max"],
        help="思考レベル。",
    )
    parser.add_argument(
        "additional_prompt",
        nargs="?",
        default=None,
        help="フォーマット指示や差分説明などの追加プロンプト。省略可能。",
    )
    args = parser.parse_args()

    git_root = _get_git_root()

    # ステージング確認（除外なし）
    staged_names = _get_staged_names()
    staged_stat = ""
    head_message = ""
    head_diff = ""

    if args.amend:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            capture_output=True,
            text=True,
            check=True,
        )
        head_message = result.stdout.strip()
        head_diff = _get_diff(staged_only=False)
        if not staged_names and not head_diff:
            logger.error("差分がありません。")
            sys.exit(1)
        if staged_names:
            staged_stat = _get_staged_stat()
    else:
        if not staged_names:
            logger.error("ステージング済みの変更がありません。")
            sys.exit(1)
        staged_stat = _get_staged_stat()

    # 詳細差分（lock系ファイルを除外）
    staged_diff = _get_diff(staged_only=True) if staged_names else ""

    format_instructions = _get_format_instructions(git_root)
    prompt = _build_prompt(
        git_root=git_root,
        format_instructions=format_instructions,
        staged_stat=staged_stat,
        staged_diff=staged_diff,
        amend=args.amend,
        head_message=head_message,
        head_diff=head_diff,
        dry_run=args.dry_run,
        additional_prompt=args.additional_prompt or "",
    )

    _run_claude(prompt, git_root=git_root, model=args.model, effort=args.effort)


def _get_git_root() -> Path:
    """Gitリポジトリのルートディレクトリを返す。"""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def _get_format_instructions(git_root: Path | None = None) -> str:
    """コミットメッセージのフォーマット指示を取得する。

    優先順: .gitmessage（リポジトリ固有） → git config commit.template → デフォルト
    """
    if git_root is None:
        git_root = _get_git_root()

    # リポジトリ固有の .gitmessage を最優先で確認
    gitmessage = git_root / ".gitmessage"
    if gitmessage.exists():
        return gitmessage.read_text(encoding="utf-8")

    # git config commit.template を確認
    result = subprocess.run(
        ["git", "config", "--get", "commit.template"],
        capture_output=True,
        text=True,
        check=False,  # returncode=1 は設定未定義を意味するため正常
    )
    if result.returncode == 0 and result.stdout.strip():
        template_path = Path(result.stdout.strip()).expanduser()
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")

    return _DEFAULT_FORMAT


def _get_staged_names() -> list[str]:
    """ステージング済みファイルの一覧を返す（除外なし）。"""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [f for f in result.stdout.splitlines() if f]


def _get_staged_stat() -> str:
    """ステージング済み変更の概要を返す（除外なし）。"""
    result = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _get_diff(*, staged_only: bool) -> str:
    """詳細差分を返す（lock系ファイルは除外）。"""
    exclude_args = [f":!{p}" for p in _EXCLUDE_PATTERNS]
    cmd = ["git", "diff", "--cached", "--", *exclude_args] if staged_only else ["git", "show", "HEAD", "--", *exclude_args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def _build_prompt(
    *,
    git_root: Path,
    format_instructions: str,
    staged_stat: str,
    staged_diff: str,
    amend: bool,
    head_message: str,
    head_diff: str,
    dry_run: bool,
    additional_prompt: str = "",
) -> str:
    """claudeへのプロンプトを構築する。"""
    lines: list[str] = []

    # 一時ディレクトリから起動するため、gitコマンドは git -C で操作する
    lines.append(f"gitコマンドは全て `git -C {git_root}` の形式で実行すること（一時ディレクトリから起動するため）。")
    lines.append("")

    if amend:
        lines.append("以下の情報を元に、HEADのコミットをgit commit --amendで改訂してください。")
        lines.append("")
        lines.append(f"# フォーマット\n{format_instructions}")
        lines.append(f"# 既存のコミットメッセージ\n{head_message}")
        lines.append(f"# 既存のコミット差分（HEAD）\n{head_diff}")
        if staged_stat:
            lines.append(f"# 追加のステージング済み変更の概要\n{staged_stat}")
        if staged_diff:
            lines.append(f"# 追加のステージング済み変更の詳細\n{staged_diff}")
        if dry_run:
            lines.append("実際にコミットはしないでください。実行するコミットメッセージを表示するだけにしてください。")
        else:
            lines.append("git commit --amend -m '...' を使ってコミットしてください。")
    else:
        lines.append("以下のgit差分を分析して、適切なgit commitを実行してください。")
        lines.append("")
        lines.append("変更が明確に複数の論理単位にまたがる場合は複数のコミットに分割してください。")
        lines.append("分割する場合は git restore --staged / git add を使って適切にステージングを組み替えてください。")
        lines.append("")
        lines.append(f"# フォーマット\n{format_instructions}")
        lines.append(f"# ステージング済みの変更の概要\n{staged_stat}")
        if staged_diff:
            lines.append(f"# ステージング済みの変更の詳細（lock系ファイルは除外済み）\n{staged_diff}")
        if dry_run:
            lines.append("実際にコミットはしないでください。実行するコミットメッセージを表示するだけにしてください。")

    if additional_prompt:
        lines.append("")
        lines.append(f"# ユーザーによる追加の指示\n{additional_prompt}")

    return "\n".join(lines)


def _run_claude(prompt: str, *, git_root: Path, model: str, effort: str | None) -> None:
    """claudeを呼び出してgit操作を実行させる。"""
    cmd = [
        "claude",
        "--print",
        "--tools",
        "Bash",
        "--permission-mode",
        "bypassPermissions",
        "--add-dir",
        str(git_root),
        "--exclude-dynamic-system-prompt-sections",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--model",
        model,
        "--no-session-persistence",
    ]
    if effort is not None:
        cmd.extend(["--effort", effort])
    # --tools が可変長引数のため、prompt の前に -- が必須
    cmd.extend(["--", prompt])
    # 一時ディレクトリをカレントにすることで CLAUDE.md の自動読み込みを防ぐ
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(cmd, check=False, cwd=tmpdir)
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    _main()
