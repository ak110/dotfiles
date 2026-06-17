"""フィードバック項目をprivate-notesのinboxへ投入するCLIエントリポイント。"""

import argparse
import datetime
import pathlib
import subprocess
import sys

from pytools._internal.cli import enable_completion


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(
        description="位置引数で受け取ったメッセージ群をprivate-notesのinboxへ投入する。",
    )
    parser.add_argument(
        "repo_path",
        metavar="REPO_PATH",
        help="フィードバック対象リポジトリのパス（~展開可能）。",
    )
    parser.add_argument(
        "messages",
        metavar="MESSAGE",
        nargs="+",
        help="投入するフィードバックメッセージ（1個以上）。",
    )
    enable_completion(parser)
    return parser.parse_args(argv)


def _count_existing_inbox_files(inbox_dir: pathlib.Path, timestamp_prefix: str) -> int:
    """同一タイムスタンププレフィックスを持つinboxファイルの件数を返す。"""
    if not inbox_dir.exists():
        return 0
    return sum(1 for p in inbox_dir.iterdir() if p.name.startswith(timestamp_prefix))


def _write_feedback_file(
    inbox_dir: pathlib.Path,
    filename: str,
    target_repo: str,
    message: str,
    created: str,
) -> None:
    """frontmatter付きフィードバックファイルを書き込む。"""
    inbox_dir.mkdir(parents=True, exist_ok=True)
    content = f"---\ncreated: {created}\ntarget_repo: {target_repo}\n---\n\n{message}\n"
    (inbox_dir / filename).write_text(content, encoding="utf-8")


def _run_git(args: list[str], cwd: pathlib.Path) -> None:
    """gitコマンドをcwdで実行し、失敗時は例外を送出する。"""
    subprocess.run(["git", *args], cwd=cwd, check=True)


def main(argv: list[str] | None = None, *, home: pathlib.Path | None = None, now: datetime.datetime | None = None) -> None:
    """エントリポイント。

    `pyproject.toml`の`[project.scripts]`から
    `feedback-add = "pytools.feedback_inbox_add:main"`の形で参照される。
    """
    args = parse_args(argv)
    if home is None:
        home = pathlib.Path.home()
    if now is None:
        now = datetime.datetime.now()

    flag_file = home / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
    if not flag_file.exists():
        print("feedback-inbox機能が無効です（フラグファイルが存在しません）。", file=sys.stderr)
        sys.exit(1)

    private_notes = home / "private-notes"
    if not private_notes.exists():
        print(
            "~/private-notesが見つかりません。GitHubからcloneしてから再実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    target_repo = str(pathlib.Path(args.repo_path).expanduser().resolve())

    # pull: リモート最新状態へ合わせてから連番を決める
    inbox_dir = private_notes / "feedback" / "inbox"
    _run_git(["pull", "--ff-only"], cwd=private_notes)

    timestamp = now.strftime("%Y%m%d-%H%M%S")
    created_iso = now.isoformat()

    # pull後のinbox状態を基準に連番の開始値を決める
    existing_count = _count_existing_inbox_files(inbox_dir, timestamp)
    counter = existing_count + 1

    generated_files: list[str] = []
    for message in args.messages:
        filename = f"{timestamp}-{counter:03d}.md"
        _write_feedback_file(
            inbox_dir=inbox_dir,
            filename=filename,
            target_repo=target_repo,
            message=message,
            created=created_iso,
        )
        generated_files.append(filename)
        counter += 1

    _run_git(["add", str(inbox_dir)], cwd=private_notes)
    count = len(generated_files)
    _run_git(["commit", "-m", f"chore: add {count} feedback {'item' if count == 1 else 'items'}"], cwd=private_notes)
    _run_git(["push"], cwd=private_notes)

    files_summary = ", ".join(generated_files)
    inbox_total = sum(1 for p in inbox_dir.iterdir() if p.suffix == ".md")
    print(f"{count}件投入: {files_summary}")
    print(f"inbox: 計{inbox_total}件")
    sys.exit(0)
