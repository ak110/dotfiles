"""変更を読んでGitコミットを作成する子エージェントを起動する。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_toolkit._atk import config, outcome
from agent_toolkit._common import automated_prompt, message_format

_PROMPT_SOURCE = "atk-commit"
_PROMPT_KIND = "commit-request"
_DEFAULT_FORMAT = """\
Conventional Commits形式、日本語で記述すること。
形式: <type>[(<scope>)]: <description>
typeはfeat/fix/docs/style/refactor/test/chore/perf/ciのいずれか。
descriptionは日本語で書く。
"""


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _git_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return Path(result.stdout.strip())


def _names(root: Path, scope: str) -> list[str]:
    commands = {
        "staged": ("diff", "--cached", "--name-only"),
        "unstaged": ("diff", "--name-only"),
        "untracked": ("ls-files", "--others", "--exclude-standard"),
    }
    return [name for name in _git(root, *commands[scope]).stdout.splitlines() if name]


def _stat(root: Path, *, staged: bool) -> str:
    return _git(root, "diff", *(("--cached",) if staged else ()), "--stat").stdout


def _format_instructions(root: Path) -> str:
    gitmessage = root / ".gitmessage"
    if gitmessage.exists():
        return gitmessage.read_text(encoding="utf-8")
    template = _git(root, "config", "--get", "commit.template", check=False)
    if template.returncode == 0 and template.stdout.strip():
        path = Path(template.stdout.strip()).expanduser()
        if path.exists():
            return path.read_text(encoding="utf-8")
    return _DEFAULT_FORMAT


def _prompt(
    *,
    root: Path,
    format_instructions: str,
    staged_stat: str,
    unstaged_stat: str,
    untracked_names: list[str],
    amend: bool,
    head_message: str,
    dry_run: bool,
    additional_prompt: str,
) -> str:
    lines = [f"gitコマンドは全て `git -C {root}` の形式で実行すること（一時ディレクトリから起動するため）。", ""]
    if amend:
        lines.extend(
            [
                "HEADのコミットを `git commit --amend` で改訂してください。",
                "",
                "ステージング済み・未ステージ・未追跡の変更が混在している可能性があります。",
                "HEADのコミット内容と関連する変更のみを `git add` で追加してからamendしてください。",
                "無関係な変更はそのまま残してください。",
                "取り込むべき変更が全く無い場合は、メッセージのみを書き直してください。",
            ]
        )
    else:
        lines.extend(
            [
                "以下のgit差分を分析して、適切な `git commit` を実行してください。",
                "",
                "ステージング済み・未ステージ・未追跡の変更が混在している可能性があります。",
                "コミット対象とすべき変更を `git add` で適切にステージングしてからコミットしてください。",
                "変更が明確に複数の論理単位にまたがる場合は複数のコミットに分割してください。",
                "ステージングの組み替えには `git add` / `git restore --staged` を使ってください。",
            ]
        )
    lines.extend(["", f"# フォーマット\n{format_instructions}"])
    if amend:
        lines.append(f"# 既存のコミットメッセージ\n{head_message}")
    if staged_stat:
        lines.append(f"# ステージング済みの変更の概要\n{staged_stat}")
    if unstaged_stat:
        lines.append(f"# 未ステージの変更の概要\n{unstaged_stat}")
    if untracked_names:
        lines.append("# 未追跡ファイルの一覧\n" + "\n".join(untracked_names))
    lines.extend(["", "差分の詳細は `git diff --cached` / `git diff` / `git show HEAD` などで自分で確認すること。"])
    if dry_run:
        lines.extend(["", "実際にコミットはしないでください。実行するコミットメッセージを表示するだけにしてください。"])
    generated = automated_prompt.wrap("\n".join(lines), source=_PROMPT_SOURCE, kind=_PROMPT_KIND)
    if not additional_prompt:
        return generated
    forwarded = message_format.xml_message(
        message_format.FORWARDED_USER_INPUT_ELEMENT,
        additional_prompt,
        {"from": _PROMPT_SOURCE, "origin": "user", "source": "--prompt", "scope": "element body"},
    )
    return f"{generated}\n\n{forwarded}"


def _executable(engine: str) -> str | None:
    local = Path.home() / ".local" / "bin"
    search_path = os.pathsep.join((str(local), os.environ.get("PATH", "")))
    return shutil.which(engine, path=search_path)


def _git_state(root: Path) -> tuple[tuple[int, str], tuple[int, str]]:
    head = _git(root, "rev-parse", "HEAD", check=False)
    status = _git(root, "status", "--porcelain=v1", check=False)
    return (head.returncode, head.stdout), (status.returncode, status.stdout)


def _command(engine: str, executable: str, model: str, effort: str, root: Path, temporary: str, prompt: str) -> list[str]:
    if engine == "claude":
        return [
            executable,
            "--print",
            "--tools=Bash",
            "--permission-mode=bypassPermissions",
            f"--add-dir={root}",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--system-prompt=.",
            f"--model={model}",
            "--no-session-persistence",
            f"--effort={effort}",
            "--",
            prompt,
        ]
    return [
        executable,
        "exec",
        "--model",
        model,
        "-c",
        f"model_reasoning_effort={effort}",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--ephemeral",
        "-C",
        temporary,
        prompt,
    ]


def run(args: argparse.Namespace) -> int:
    """候補を順に起動し、リポジトリの状態を変えた失敗では再試行しない。"""
    try:
        root = _git_root()
        staged = _names(root, "staged")
        unstaged = _names(root, "unstaged")
        untracked = _names(root, "untracked")
        if not args.amend and not (staged or unstaged or untracked):
            outcome.report_failure("変更がありません。")
            return 1
        head_message = _git(root, "log", "-1", "--format=%B").stdout.strip() if args.amend else ""
        prompt = _prompt(
            root=root,
            format_instructions=_format_instructions(root),
            staged_stat=_stat(root, staged=True) if staged else "",
            unstaged_stat=_stat(root, staged=False) if unstaged else "",
            untracked_names=untracked,
            amend=args.amend,
            head_message=head_message,
            dry_run=args.dry_run,
            additional_prompt=args.additional_prompt or "",
        )
        candidates = config.resolve_model_candidates(args.model_type)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        outcome.report_failure(f"commitを開始できません: {error}")
        return 2
    for engine, model, effort in candidates:
        if engine not in {"claude", "codex"}:
            print(f"候補をスキップします: 未対応のengine: {engine}", file=sys.stderr)
            continue
        executable = _executable(engine)
        if executable is None:
            print(f"候補をスキップします: 実行ファイルが見つかりません: {engine}", file=sys.stderr)
            continue
        before = _git_state(root)
        with tempfile.TemporaryDirectory() as temporary:
            command = _command(engine, executable, model, effort, root, temporary, prompt)
            try:
                result = subprocess.run(command, cwd=temporary, check=False)
            except OSError as error:
                print(f"候補をスキップします: {engine}を起動できません: {error}", file=sys.stderr)
                continue
        if result.returncode == 0:
            outcome.report_success("コミット用エージェントの実行が完了した")
            return 0
        if _git_state(root) != before:
            outcome.report_failure(f"{engine}がGit状態を変更した後に失敗したため、次の候補を起動しません")
            return result.returncode
        print(f"候補をスキップします: {engine}がGit状態を変えず終了コード{result.returncode}で失敗した", file=sys.stderr)
    outcome.report_failure("利用できるモデル候補がありません")
    return 1
