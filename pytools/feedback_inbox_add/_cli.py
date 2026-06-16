"""フィードバック項目をprivate-notesのinboxへ投入するCLIエントリポイント。"""

import argparse
import datetime
import pathlib
import subprocess
import sys

from pytools._internal.cli import enable_completion

# 章見出しとsource識別子のマッピング
_SECTION_SOURCES: dict[str, str] = {
    "## プロジェクトドキュメント改善提案": "project-doc",
    "## pyfltr改善提案": "pyfltr",
    "## agent-toolkit改善提案": "agent-toolkit",
}

# 「提案無し」を示す本文パターン
_NO_PROPOSAL_TEXT = "提案無し"


def _source_target_repos(home: pathlib.Path) -> dict[str, str]:
    """sourceごとのtarget_repoマップを返す（project-docを除く）。"""
    return {
        "pyfltr": str(home / "pyfltr"),
        "agent-toolkit": str(home / "dotfiles"),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(
        description="stdinからmarkdownを読み込み、フィードバック項目をprivate-notesのinboxへ投入する。",
    )
    parser.add_argument(
        "--project-doc-repo",
        metavar="PATH",
        help="プロジェクトドキュメント改善提案章のtarget_repoとなるリポジトリパス。",
    )
    enable_completion(parser)
    return parser.parse_args(argv)


def _parse_sections(markdown: str) -> dict[str, list[str]]:
    """markdownを章単位にパースし、各章の箇条書き項目リストを返す。

    キーはsource識別子。「提案無し」のみの章は結果に含めない。
    `## `で始まる既知の見出し以外は無視する。`### `等のサブセクションも無視する。
    """
    result: dict[str, list[str]] = {}
    current_source: str | None = None
    current_items: list[str] = []

    def _flush() -> None:
        if current_source is None:
            return
        # 「提案無し」のみの章はスキップする
        if len(current_items) == 1 and _NO_PROPOSAL_TEXT in current_items[0]:
            return
        if current_items:
            result[current_source] = list(current_items)

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped in _SECTION_SOURCES:
            _flush()
            current_source = _SECTION_SOURCES[stripped]
            current_items = []
        elif stripped.startswith("## "):
            # 未知の章見出し: 現在章を区切るが新たな章は開始しない
            _flush()
            current_source = None
            current_items = []
        elif current_source is not None and stripped.startswith("- "):
            current_items.append(stripped[2:])  # 先頭の "- " を除く

    _flush()
    return result


def _split_item(item_text: str) -> tuple[str, str]:
    """箇条書き項目テキストから対象ファイルと提案内容を分離する。

    区切りは全角ダッシュ（—, U+2014）または ` - `（スペース込みのハイフン）を優先順で試みる。
    区切りが見つからない場合は対象ファイルを空文字列として返す。
    """
    if "—" in item_text:
        parts = item_text.split("—", maxsplit=1)
        return parts[0].strip(), parts[1].strip()
    if " - " in item_text:
        parts = item_text.split(" - ", maxsplit=1)
        return parts[0].strip(), parts[1].strip()
    return "", item_text.strip()


def _count_existing_inbox_files(inbox_dir: pathlib.Path, timestamp_prefix: str) -> int:
    """同一タイムスタンププレフィックスを持つinboxファイルの件数を返す。"""
    if not inbox_dir.exists():
        return 0
    return sum(1 for p in inbox_dir.iterdir() if p.name.startswith(timestamp_prefix))


def _write_feedback_file(
    inbox_dir: pathlib.Path,
    filename: str,
    source: str,
    target_repo: str,
    target: str,
    item_text: str,
    created: str,
) -> None:
    """frontmatter付きフィードバックファイルを書き込む。"""
    inbox_dir.mkdir(parents=True, exist_ok=True)
    content = f"---\ncreated: {created}\nsource: {source}\ntarget_repo: {target_repo}\ntarget: {target}\n---\n\n- {item_text}\n"
    (inbox_dir / filename).write_text(content, encoding="utf-8")


def _run_git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[bytes]:
    """gitコマンドをcwdで実行し、失敗時は例外を送出する。"""
    return subprocess.run(["git", *args], cwd=cwd, check=True)


def main(argv: list[str] | None = None) -> None:
    """エントリポイント。

    `pyproject.toml`の`[project.scripts]`から
    `feedback-add = "pytools.feedback_inbox_add:main"`の形で参照される。
    """
    args = parse_args(argv)

    flag_file = pathlib.Path.home() / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
    if not flag_file.exists():
        print("feedback-inbox機能が無効です（フラグファイルが存在しません）。", file=sys.stderr)
        sys.exit(1)

    private_notes = pathlib.Path.home() / "private-notes"
    if not private_notes.exists():
        print(
            "~/private-notesが見つかりません。GitHubからcloneしてから再実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    markdown = sys.stdin.read()
    sections = _parse_sections(markdown)

    if not sections:
        print("処理対象なし")
        sys.exit(0)

    # project-doc章がある場合は--project-doc-repoが必須
    if "project-doc" in sections and not args.project_doc_repo:
        print(
            "プロジェクトドキュメント改善提案章があります。--project-doc-repo <path> でリポジトリパスを指定してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    # pull前にtarget_repoマップを確定する
    target_repo_map: dict[str, str] = _source_target_repos(pathlib.Path.home())
    if args.project_doc_repo:
        target_repo_map["project-doc"] = str(pathlib.Path(args.project_doc_repo).expanduser().resolve())

    # pull: リモート最新状態へ合わせてから連番を決める
    inbox_dir = private_notes / "feedback" / "inbox"
    _run_git(["pull", "--ff-only"], cwd=private_notes)

    # タイムスタンプと連番を決めてファイルを生成する
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    created_iso = now.isoformat()

    # pull後のinbox状態を基準に連番の開始値を決める
    existing_count = _count_existing_inbox_files(inbox_dir, timestamp)
    counter = existing_count + 1

    generated_files: list[str] = []
    for source, items in sections.items():
        target_repo = target_repo_map[source]
        for item_text in items:
            target, _ = _split_item(item_text)
            filename = f"{timestamp}-{counter:03d}.md"
            _write_feedback_file(
                inbox_dir=inbox_dir,
                filename=filename,
                source=source,
                target_repo=target_repo,
                target=target,
                item_text=item_text,
                created=created_iso,
            )
            generated_files.append(filename)
            counter += 1

    # git操作: add → commit → push
    _run_git(["add", str(inbox_dir)], cwd=private_notes)
    n = len(generated_files)
    _run_git(["commit", "-m", f"chore: add {n} feedback items"], cwd=private_notes)
    _run_git(["push"], cwd=private_notes)

    files_summary = ", ".join(generated_files)
    print(f"{n}件投入: {files_summary}")
    sys.exit(0)
