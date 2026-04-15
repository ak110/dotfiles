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

    staged_names = _get_names(scope="staged")
    unstaged_names = _get_names(scope="unstaged")
    untracked_names = _get_names(scope="untracked")
    has_changes = bool(staged_names or unstaged_names or untracked_names)

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
        head_diff = _get_head_diff()
    elif not has_changes:
        logger.error("変更がありません。")
        sys.exit(1)

    staged_stat = _get_stat(scope="staged") if staged_names else ""
    staged_diff = _get_diff(scope="staged") if staged_names else ""
    unstaged_stat = _get_stat(scope="unstaged") if unstaged_names else ""
    unstaged_diff = _get_diff(scope="unstaged") if unstaged_names else ""

    format_instructions = _get_format_instructions(git_root)
    prompt = _build_prompt(
        git_root=git_root,
        format_instructions=format_instructions,
        staged_stat=staged_stat,
        staged_diff=staged_diff,
        unstaged_stat=unstaged_stat,
        unstaged_diff=unstaged_diff,
        untracked_names=untracked_names,
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


def _get_names(*, scope: str) -> list[str]:
    """変更ファイル名一覧を返す（除外なし）。

    Args:
        scope: 'staged'（ステージ済み）／'unstaged'（未ステージ、追跡中）／'untracked'（未追跡）
    """
    if scope == "staged":
        cmd = ["git", "diff", "--cached", "--name-only"]
    elif scope == "unstaged":
        cmd = ["git", "diff", "--name-only"]
    elif scope == "untracked":
        cmd = ["git", "ls-files", "--others", "--exclude-standard"]
    else:
        raise ValueError(f"unknown scope: {scope}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [f for f in result.stdout.splitlines() if f]


def _get_stat(*, scope: str) -> str:
    """変更概要を返す（除外なし）。scope: 'staged' | 'unstaged'"""
    if scope == "staged":
        cmd = ["git", "diff", "--cached", "--stat"]
    elif scope == "unstaged":
        cmd = ["git", "diff", "--stat"]
    else:
        raise ValueError(f"unknown scope: {scope}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def _get_diff(*, scope: str) -> str:
    """詳細差分を返す（lock系ファイルは除外）。scope: 'staged' | 'unstaged'"""
    exclude_args = [f":!{p}" for p in _EXCLUDE_PATTERNS]
    if scope == "staged":
        cmd = ["git", "diff", "--cached", "--", *exclude_args]
    elif scope == "unstaged":
        cmd = ["git", "diff", "--", *exclude_args]
    else:
        raise ValueError(f"unknown scope: {scope}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def _get_head_diff() -> str:
    """HEADのコミット差分を返す（lock系ファイルは除外）。"""
    exclude_args = [f":!{p}" for p in _EXCLUDE_PATTERNS]
    result = subprocess.run(
        ["git", "show", "HEAD", "--", *exclude_args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _build_prompt(
    *,
    git_root: Path,
    format_instructions: str,
    staged_stat: str,
    staged_diff: str,
    unstaged_stat: str = "",
    unstaged_diff: str = "",
    untracked_names: list[str] | None = None,
    amend: bool,
    head_message: str,
    head_diff: str,
    dry_run: bool,
    additional_prompt: str = "",
) -> str:
    """claudeへのプロンプトを構築する。"""
    untracked_names = untracked_names or []
    lines: list[str] = []

    # 一時ディレクトリから起動するため、gitコマンドは git -C で操作する
    lines.append(f"gitコマンドは全て `git -C {git_root}` の形式で実行すること（一時ディレクトリから起動するため）。")
    lines.append("")

    if amend:
        lines.append("HEADのコミットを `git commit --amend` で改訂してください。")
        lines.append("")
        lines.append("ステージング済み・未ステージ・未追跡の変更が混在している可能性があります。")
        lines.append("HEADのコミット内容と関連する変更のみを `git add` で追加してからamendしてください。")
        lines.append("無関係な変更はそのまま残してください。")
        lines.append("取り込むべき変更が全く無い場合は、メッセージのみを書き直してください。")
    else:
        lines.append("以下のgit差分を分析して、適切な `git commit` を実行してください。")
        lines.append("")
        lines.append("ステージング済み・未ステージ・未追跡の変更が混在している可能性があります。")
        lines.append("コミット対象とすべき変更を `git add` で適切にステージングしてからコミットしてください。")
        lines.append("変更が明確に複数の論理単位にまたがる場合は複数のコミットに分割してください。")
        lines.append("ステージングの組み替えには `git add` / `git restore --staged` を使ってください。")

    lines.append("")
    lines.append(f"# フォーマット\n{format_instructions}")

    if amend:
        lines.append(f"# 既存のコミットメッセージ\n{head_message}")
        lines.append(f"# 既存のコミット差分（HEAD）\n{head_diff}")

    if staged_stat:
        lines.append(f"# ステージング済みの変更の概要\n{staged_stat}")
    if staged_diff:
        lines.append(f"# ステージング済みの変更の詳細（lock系ファイルは除外済み）\n{staged_diff}")
    if unstaged_stat:
        lines.append(f"# 未ステージの変更の概要\n{unstaged_stat}")
    if unstaged_diff:
        lines.append(f"# 未ステージの変更の詳細（lock系ファイルは除外済み）\n{unstaged_diff}")
    if untracked_names:
        lines.append("# 未追跡ファイルの一覧\n" + "\n".join(untracked_names))

    if dry_run:
        lines.append("")
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
