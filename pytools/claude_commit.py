# PYTHON_ARGCOMPLETE_OK
"""claudeを使ってコミットメッセージを生成しgit commitを実行する。"""

import argparse
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_toolkit._common import automated_prompt, message_format

from pytools._internal import claude_common
from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)

# 生成した指示の出所と種別。agent-toolkitは本パッケージの依存であり通常のimportで解決する。
_PROMPT_SOURCE = "dotfiles/claude-commit"
_PROMPT_KIND = "commit-request"

_DEFAULT_FORMAT = """\
Conventional Commits形式、日本語で記述すること。
形式: <type>[(<scope>)]: <description>
typeはfeat/fix/docs/style/refactor/test/chore/perf/ciのいずれか。
descriptionは日本語で書く。
"""


def main() -> None:
    """claudeでコミットメッセージを生成しgit commitを実行するエントリポイント。"""
    setup_logging()

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
        choices=["low", "medium", "high", "xhigh", "max"],
        help="思考レベル。",
    )
    parser.add_argument(
        "additional_prompt",
        nargs="?",
        default=None,
        help="フォーマット指示や差分説明などの追加プロンプト。省略可能。",
    )
    enable_completion(parser)
    args = parser.parse_args()

    git_root = _get_git_root()

    staged_names = _get_names(scope="staged")
    unstaged_names = _get_names(scope="unstaged")
    untracked_names = _get_names(scope="untracked")
    has_changes = bool(staged_names or unstaged_names or untracked_names)

    head_message = ""
    if args.amend:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        head_message = result.stdout.strip()
    elif not has_changes:
        logger.error("変更がありません。")
        sys.exit(1)

    staged_stat = _get_stat(scope="staged") if staged_names else ""
    unstaged_stat = _get_stat(scope="unstaged") if unstaged_names else ""

    format_instructions = _get_format_instructions(git_root)
    prompt = _build_prompt(
        git_root=git_root,
        format_instructions=format_instructions,
        staged_stat=staged_stat,
        unstaged_stat=unstaged_stat,
        untracked_names=untracked_names,
        amend=args.amend,
        head_message=head_message,
        dry_run=args.dry_run,
        additional_prompt=args.additional_prompt or "",
    )

    _run_claude(prompt, git_root=git_root, model=args.model, effort=args.effort)
    sys.exit(0)


def _get_git_root() -> Path:
    """Gitリポジトリのルートディレクトリを返す。"""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return Path(result.stdout.strip())


def _get_format_instructions(git_root: Path | None = None) -> str:
    """コミットメッセージのフォーマット指示を取得する。

    優先順: `.gitmessage`（リポジトリ固有）→ `git config commit.template` → 組み込み既定値
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
        encoding="utf-8",
        errors="replace",
        check=False,  # returncode=1 は設定未定義を意味するため正常
    )
    if result.returncode == 0 and result.stdout.strip():
        template_path = Path(result.stdout.strip()).expanduser()
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")

    return _DEFAULT_FORMAT


def _get_names(*, scope: str) -> list[str]:
    """変更ファイル名一覧を返す。

    Args:
        scope: `staged`（ステージ済み）・`unstaged`（未ステージ、追跡中）・`untracked`（未追跡）
    """
    if scope == "staged":
        cmd = ["git", "diff", "--cached", "--name-only"]
    elif scope == "unstaged":
        cmd = ["git", "diff", "--name-only"]
    elif scope == "untracked":
        cmd = ["git", "ls-files", "--others", "--exclude-standard"]
    else:
        raise ValueError(f"unknown scope: {scope}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return [f for f in result.stdout.splitlines() if f]


def _get_stat(*, scope: str) -> str:
    """変更概要を返す。scope: 'staged' | 'unstaged'"""
    if scope == "staged":
        cmd = ["git", "diff", "--cached", "--stat"]
    elif scope == "unstaged":
        cmd = ["git", "diff", "--stat"]
    else:
        raise ValueError(f"unknown scope: {scope}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def _build_prompt(
    *,
    git_root: Path,
    format_instructions: str,
    staged_stat: str,
    unstaged_stat: str = "",
    untracked_names: list[str] | None = None,
    amend: bool,
    head_message: str,
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

    if staged_stat:
        lines.append(f"# ステージング済みの変更の概要\n{staged_stat}")
    if unstaged_stat:
        lines.append(f"# 未ステージの変更の概要\n{unstaged_stat}")
    if untracked_names:
        lines.append("# 未追跡ファイルの一覧\n" + "\n".join(untracked_names))

    lines.append("")
    lines.append("差分の詳細は `git diff --cached` / `git diff` / `git show HEAD` などで自分で確認すること。")

    if dry_run:
        lines.append("")
        lines.append("実際にコミットはしないでください。実行するコミットメッセージを表示するだけにしてください。")

    # 生成した指示とユーザーが渡した追加指示を、それぞれの出所を示す境界で分けて囲む。
    # 包装はagent-toolkitの共通実装を経由し、本リポジトリの他の自動注入経路と同じ形式へそろえる。
    generated = automated_prompt.wrap(
        "\n".join(lines),
        source=_PROMPT_SOURCE,
        kind=_PROMPT_KIND,
    )
    if not additional_prompt:
        return generated
    forwarded = message_format.xml_message(
        message_format.FORWARDED_USER_INPUT_ELEMENT,
        additional_prompt,
        {
            "from": _PROMPT_SOURCE,
            "origin": "user",
            "source": "--prompt",
            "scope": "element body",
        },
    )
    return f"{generated}\n\n{forwarded}"


def _run_claude(prompt: str, *, git_root: Path, model: str, effort: str | None) -> None:
    """claudeを呼び出してgit操作を実行させる。"""
    claude = claude_common.resolve_executable("claude", preferred_directories=(Path.home() / ".local" / "bin",))
    if claude is None:
        print("claudeコマンドが見つかりません。", file=sys.stderr)
        sys.exit(127)
    cmd = [
        str(claude),
        "--print",
        "--tools=Bash",
        "--permission-mode=bypassPermissions",
        f"--add-dir={git_root}",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--system-prompt=.",
        f"--model={model}",
        "--no-session-persistence",
    ]
    if effort is not None:
        cmd.append(f"--effort={effort}")
    # --tools が可変長引数のため、prompt の前に -- が必須
    cmd.extend(["--", prompt])
    # 一時ディレクトリをカレントにすることで CLAUDE.md の自動読み込みを防ぐ
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(cmd, check=False, cwd=tmpdir)
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
