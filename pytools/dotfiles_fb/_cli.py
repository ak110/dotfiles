"""~/private-notesのフィードバック項目を操作するCLIエントリポイント。

サブコマンド構成。
- add: inboxへフィードバックを投入する
- list: inboxの全件をtarget_repoごとにグループ化して出力する
- adopt: 採用としてinboxから削除しコミット・push
- reject: 不採用として単純削除しコミット・push
- rm: 単純削除しコミット・push
- edit: $EDITORで対象ファイルを編集しコミット・push
- commit: 外部編集後のinbox配下未コミット変更をコミット・push
- enable: feedback-inboxフラグファイルを作成する
- disable: feedback-inboxフラグファイルを削除する
- status: feedback-inboxの有効状態を判定する（正常0・無効1）
- process-loop: 対象リポジトリのinboxが0件になるまで`claude /process-feedbacks`を繰り返し起動する
"""

import argparse
import datetime
import os
import pathlib
import subprocess
import sys
import tempfile
import typing
from collections.abc import Iterable

from pytools._internal.cli import enable_completion


def _build_parser() -> argparse.ArgumentParser:
    """サブコマンド付きargparseパーサーを構築する。"""
    parser = argparse.ArgumentParser(
        description="~/private-notesのフィードバック項目を操作する。",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    add = sub.add_parser("add", help="フィードバックをinboxへ投入する")
    add.add_argument("repo_path", metavar="REPO_PATH", help="フィードバック対象リポジトリのパス（~展開可能）。")
    add.add_argument(
        "messages",
        metavar="MESSAGE",
        nargs="*",
        help="投入するフィードバックメッセージ（省略時は$EDITORで編集する）。",
    )
    add.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help="投入元の識別子（任意。frontmatterに source: <NAME> として記録する。既知値: session-review）。",
    )

    list_ = sub.add_parser("list", help="inboxの全件をtarget_repoごとに出力する")
    list_.add_argument(
        "--target-repo",
        metavar="PATH",
        default=None,
        help="対象リポジトリのパスでフィルタする（~展開可能）。",
    )

    adopt = sub.add_parser("adopt", help="採用としてinboxから削除しコミット・push")
    adopt.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="採用するinboxファイル名（1個以上）。"
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    reject = sub.add_parser("reject", help="不採用として単純削除しコミット・push")
    reject.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="不採用とするinboxファイル名（1個以上）。"
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    rm = sub.add_parser("rm", help="inboxから単純削除しコミット・push")
    rm.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="削除するinboxファイル名（1個以上）。"
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    edit = sub.add_parser("edit", help="$EDITORで対象ファイルを編集しコミット・push")
    edit.add_argument(
        "filename", metavar="FILENAME", help="編集対象のinboxファイル名。"
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    sub.add_parser(
        "commit",
        help="外部編集後にinbox配下の未コミット変更をコミット・push（差分なしなら無動作）",
    )

    sub.add_parser(
        "enable",
        help="feedback-inboxフラグファイルを作成する（chezmoi apply再評価で上書きされ得る）",
    )
    sub.add_parser(
        "disable",
        help="feedback-inboxフラグファイルを削除する（chezmoi apply再評価で上書きされ得る）",
    )
    sub.add_parser(
        "status",
        help="feedback-inboxの有効状態を判定する（正常時exit 0、無効時exit 1で原因を標準エラー出力へ書く）",
    )

    loop = sub.add_parser(
        "process-loop",
        help="対象リポジトリのinbox件数が0件になるまで`claude /process-feedbacks`を繰り返し起動する",
    )
    loop.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        metavar="N",
        help="反復上限回数（既定: 無制限）。",
    )
    loop.add_argument(
        "--target-repo",
        metavar="PATH",
        default=None,
        help="対象リポジトリのパス（~展開可能）。既定は`git rev-parse --show-toplevel`の取得値。",
    )

    enable_completion(parser)
    return parser


def _feedback_filename_completer(prefix: str, **_: object) -> list[str]:
    """argcomplete用のフィードバックファイル名補完候補生成。

    `~/private-notes/feedback/`配下の`*.md`ファイル名をprefix一致で返す。
    ディレクトリ不在時は空リストを返す。
    """
    feedback_dir = pathlib.Path.home() / "private-notes" / "feedback"
    if not feedback_dir.exists():
        return []
    return sorted(p.name for p in feedback_dir.iterdir() if p.suffix == ".md" and p.name.startswith(prefix))


def _flag_path(home: pathlib.Path) -> pathlib.Path:
    """feedback-inboxの有効化フラグファイルの絶対パスを返す。"""
    return home / ".config" / "agent-toolkit" / "feedback-inbox.enabled"


def _check_environment(home: pathlib.Path) -> tuple[int, str]:
    """feedback-inboxの有効状態を判定し、(exit_code, message)を返す。

    正常時は(0, 有効案内)、フラグファイル不在・private-notes不在時は(1, 原因案内)。
    """
    if not _flag_path(home).exists():
        return 1, "feedback-inbox機能が無効です（フラグファイルが存在しません）。"
    if not (home / "private-notes").exists():
        return 1, "~/private-notesが見つかりません。GitHubからクローンしてから再実行してください。"
    return 0, "feedback-inboxは有効です。"


def _ensure_environment(home: pathlib.Path) -> pathlib.Path:
    """フラグファイルとprivate-notesディレクトリの存在を確認し、private-notesパスを返す。"""
    code, message = _check_environment(home)
    if code != 0:
        print(message, file=sys.stderr)
        sys.exit(code)
    return home / "private-notes"


def _run_git(args: list[str], cwd: pathlib.Path) -> None:
    """gitコマンドをcwdで実行し、失敗時は例外を送出する。"""
    subprocess.run(["git", *args], cwd=cwd, check=True)


def _pull(private_notes: pathlib.Path) -> None:
    """private-notesリポジトリで`git pull --ff-only`を実行する。"""
    _run_git(["pull", "--ff-only"], cwd=private_notes)


def _commit_and_push(private_notes: pathlib.Path, message: str, rel_paths: Iterable[str]) -> None:
    """指定パスをaddしcommit・pushする。"""
    rel_list = list(rel_paths)
    _run_git(["add", *rel_list], cwd=private_notes)
    _run_git(["commit", "-m", message], cwd=private_notes)
    _run_git(["push"], cwd=private_notes)


def _validate_filename(filename: str, base_dir: pathlib.Path) -> pathlib.Path:
    r"""ファイル名が基準ディレクトリ直下の単純名であることを検証して絶対パスを返す。

    `/`・`\`・`..`・絶対パス・空文字列・カレント参照は早期に拒否する。
    """
    parts = pathlib.Path(filename).parts
    if (
        filename in ("", ".", "..")
        or "/" in filename
        or "\\" in filename
        or ".." in parts
        or pathlib.PurePath(filename).is_absolute()
    ):
        print(f"不正なファイル名: {filename}", file=sys.stderr)
        sys.exit(2)
    path = base_dir / filename
    base_resolved = base_dir.resolve()
    try:
        path.resolve().relative_to(base_resolved)
    except ValueError:
        print(f"ファイル名が基準ディレクトリ外を指しています: {filename}", file=sys.stderr)
        sys.exit(2)
    return path


def _count_feedback(feedback_dir: pathlib.Path) -> int:
    """inbox配下の`*.md`ファイル件数を返す。"""
    if not feedback_dir.exists():
        return 0
    return sum(1 for p in feedback_dir.iterdir() if p.suffix == ".md")


def _shorten_home(path: pathlib.Path, home: pathlib.Path) -> str:
    """$HOME配下のパスを`~/...`へ短縮する。外なら絶対パスのまま返す。"""
    try:
        rel = path.relative_to(home)
    except ValueError:
        return str(path)
    return f"~/{rel}"


def _max_existing_seq(feedback_dir: pathlib.Path, timestamp_prefix: str) -> int:
    """同一タイムスタンププレフィックスを持つinboxファイルの最大連番を返す。

    例えば`{prefix}-001.md`と`{prefix}-003.md`が存在する場合は3を返す。
    非連続連番でも新規生成側で既存ファイルへ衝突しないよう最大値を基準にする。
    """
    if not feedback_dir.exists():
        return 0
    max_seq = 0
    for p in feedback_dir.iterdir():
        if not p.name.startswith(f"{timestamp_prefix}-"):
            continue
        try:
            seq = int(p.stem.rsplit("-", 1)[-1])
        except ValueError:
            continue
        max_seq = max(max_seq, seq)
    return max_seq


def _collect_message_via_editor() -> str | None:
    """$EDITORで一時ファイルを開き、保存内容をstripして返す。

    EDITOR未設定・エディター非ゼロ終了・保存内容が空のいずれもNoneを返し、
    原因をstderrへ出力する。一時ファイルは終了時に必ず削除する。
    """
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のためエディター経路を利用できません。", file=sys.stderr)
        return None
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as f:
        tmp_path = pathlib.Path(f.name)
    try:
        result = subprocess.run([editor, str(tmp_path)], check=False)
        if result.returncode != 0:
            print(f"エディターがexit code {result.returncode}で終了しました。", file=sys.stderr)
            return None
        message = tmp_path.read_text(encoding="utf-8").strip()
        if not message:
            print("本文が空のため投入を中止しました。", file=sys.stderr)
            return None
        return message
    finally:
        tmp_path.unlink(missing_ok=True)


def _cmd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """addサブコマンド: メッセージをinboxへ投入してcommit・push。"""
    target_repo = str(pathlib.Path(args.repo_path).expanduser().resolve())
    feedback_dir = private_notes / "feedback"
    messages = list(args.messages)
    if not messages:
        message = _collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    _pull(private_notes)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    created_iso = now.isoformat()
    counter = _max_existing_seq(feedback_dir, timestamp) + 1
    feedback_dir.mkdir(parents=True, exist_ok=True)
    source_line = f"source: {args.source}\n" if args.source else ""
    generated: list[str] = []
    for message in messages:
        filename = f"{timestamp}-{counter:03d}.md"
        content = f"---\ncreated: {created_iso}\ntarget_repo: {target_repo}\n{source_line}---\n\n{message}\n"
        (feedback_dir / filename).write_text(content, encoding="utf-8")
        generated.append(filename)
        counter += 1
    count = len(generated)
    _commit_and_push(
        private_notes,
        f"chore: add {count} feedback {'item' if count == 1 else 'items'}",
        [str(feedback_dir.relative_to(private_notes))],
    )
    print(f"{count}件投入:")
    for filename in generated:
        print(f"  {_shorten_home(feedback_dir / filename, home)}")
    print(f"inbox: 計{_count_feedback(feedback_dir)}件")


def _parse_target_repo(text: str) -> str:
    """フィードバックファイル本文先頭のfrontmatterからtarget_repoを抽出する。"""
    if not text.startswith("---\n"):
        return "(unknown)"
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return "(unknown)"
    for line in text[4:end].splitlines():
        if line.startswith("target_repo:"):
            return line.split(":", 1)[1].strip()
    return "(unknown)"


def _cmd_list(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """listサブコマンド: inbox全件をtarget_repoごとにグループ化して出力。

    `--target-repo`指定時は、~展開と絶対パス正規化を施した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    """
    feedback_dir = private_notes / "feedback"
    _pull(private_notes)
    if not feedback_dir.exists():
        return
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = str(pathlib.Path(args.target_repo).expanduser().resolve())
    entries: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(feedback_dir.iterdir()):
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        target_repo = _parse_target_repo(text)
        if filter_repo is not None and target_repo != filter_repo:
            continue
        entries.setdefault(target_repo, []).append((path.name, text))
    for repo, items in entries.items():
        print(f"## target_repo: {repo}")
        for name, text in items:
            print(f"### {name}")
            print(text)
            print()


def _validate_filenames_only(filenames: list[str], base_dir: pathlib.Path) -> None:
    """ファイル名群の検証のみ行う（pull前の早期拒否用）。"""
    for f in filenames:
        _validate_filename(f, base_dir)


def _resolve_feedback_targets(filenames: list[str], feedback_dir: pathlib.Path) -> list[pathlib.Path]:
    """inbox配下のファイル名群を検証・解決し、未存在があればexit 2する。"""
    paths = [_validate_filename(f, feedback_dir) for f in filenames]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"inboxに存在しません: {p.name}", file=sys.stderr)
        sys.exit(2)
    return paths


def _cmd_adopt(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """adoptサブコマンド: 採用としてinboxから削除しcommit・push。"""
    feedback_dir = private_notes / "feedback"
    _validate_filenames_only(args.filenames, feedback_dir)
    _pull(private_notes)
    paths = _resolve_feedback_targets(args.filenames, feedback_dir)
    for p in paths:
        p.unlink()
    count = len(paths)
    rel = [str(p.relative_to(private_notes)) for p in paths]
    _commit_and_push(
        private_notes,
        f"chore: process {count} feedback {'item' if count == 1 else 'items'} (adopted)",
        rel,
    )
    print(f"{count}件採用処理: {', '.join(p.name for p in paths)}")


def _cmd_reject(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rejectサブコマンド: 不採用として単純削除しcommit・push。"""
    feedback_dir = private_notes / "feedback"
    _validate_filenames_only(args.filenames, feedback_dir)
    _pull(private_notes)
    paths = _resolve_feedback_targets(args.filenames, feedback_dir)
    for p in paths:
        p.unlink()
    count = len(paths)
    rel = [str(p.relative_to(private_notes)) for p in paths]
    _commit_and_push(
        private_notes,
        f"chore: process {count} feedback {'item' if count == 1 else 'items'} (rejected)",
        rel,
    )
    print(f"{count}件不採用処理: {', '.join(p.name for p in paths)}")


def _cmd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rmサブコマンド: inboxから単純削除しcommit・push。"""
    feedback_dir = private_notes / "feedback"
    _validate_filenames_only(args.filenames, feedback_dir)
    _pull(private_notes)
    paths = _resolve_feedback_targets(args.filenames, feedback_dir)
    for p in paths:
        p.unlink()
    count = len(paths)
    rel = [str(p.relative_to(private_notes)) for p in paths]
    _commit_and_push(
        private_notes,
        f"chore: remove {count} feedback {'item' if count == 1 else 'items'}",
        rel,
    )
    print(f"{count}件削除: {', '.join(p.name for p in paths)}")


def _cmd_edit(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """editサブコマンド: $EDITORで対象ファイルを編集しcommit・push（差分なしなら無動作）。"""
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のため編集できません。", file=sys.stderr)
        sys.exit(1)
    feedback_dir = private_notes / "feedback"
    path = _validate_filename(args.filename, feedback_dir)
    _pull(private_notes)
    if not path.exists():
        print(f"inboxに存在しません: {path.name}", file=sys.stderr)
        sys.exit(2)
    before = path.read_bytes()
    subprocess.run([editor, str(path)], check=True)
    after = path.read_bytes()
    if before == after:
        print("差分なし。")
        return
    rel = str(path.relative_to(private_notes))
    _commit_and_push(private_notes, "chore: edit feedback item", [rel])
    print(f"編集反映: {path.name}")


def _cmd_commit(private_notes: pathlib.Path) -> None:
    """commitサブコマンド: 外部編集後のinbox配下未コミット変更をコミット・push。

    inbox配下に未コミット変更が無い場合は早期return。
    """
    _pull(private_notes)
    feedback_rel = "feedback"
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", feedback_rel],
        cwd=private_notes,
        check=True,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        print("差分なし。")
        return
    _commit_and_push(private_notes, "chore: edit feedback items externally", [feedback_rel])
    print("外部編集分をコミット・pushしました。")


def _cmd_enable(home: pathlib.Path) -> None:
    """enableサブコマンド: feedback-inboxフラグファイルを作成する。"""
    path = _flag_path(home)
    if path.exists():
        print(f"既に有効です: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    print(f"有効化しました: {path}")
    print("次回`chezmoi apply`実行時に`setup_feedback_inbox.py`がホスト判定で上書きする場合があります。")


def _cmd_disable(home: pathlib.Path) -> None:
    """disableサブコマンド: feedback-inboxフラグファイルを削除する。"""
    path = _flag_path(home)
    if not path.exists():
        print(f"既に無効です: {path}")
        return
    path.unlink()
    print(f"無効化しました: {path}")
    print("次回`chezmoi apply`実行時に`setup_feedback_inbox.py`がホスト判定で上書きする場合があります。")


def _cmd_status(home: pathlib.Path) -> typing.NoReturn:
    """statusサブコマンド: feedback-inboxの有効状態を判定し終了コードで通知する。"""
    code, message = _check_environment(home)
    stream = sys.stdout if code == 0 else sys.stderr
    print(message, file=stream)
    sys.exit(code)


def _resolve_target_repo(value: str | None) -> str:
    """`--target-repo`の値（未指定時はgit rev-parse --show-toplevelの取得値）を絶対パスへ正規化して返す。"""
    if value is not None:
        return str(pathlib.Path(value).expanduser().resolve())
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return str(pathlib.Path(result.stdout.strip()).resolve())


def _count_feedback_for_repo(feedback_dir: pathlib.Path, target_repo: str) -> int:
    """frontmatterの`target_repo`が指定値と一致するinboxファイル件数を返す。"""
    if not feedback_dir.exists():
        return 0
    count = 0
    for path in feedback_dir.iterdir():
        if path.suffix != ".md":
            continue
        if _parse_target_repo(path.read_text(encoding="utf-8")) == target_repo:
            count += 1
    return count


def _cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """process-loopサブコマンド: 対象リポジトリのinboxが0件になるまでclaude /process-feedbacksを繰り返し起動する。

    `--target-repo`未指定時は`git rev-parse --show-toplevel`の値を既定とし、
    件数判定と内部`claude /process-feedbacks`起動引数で同フィルタを使う。
    """
    feedback_dir = private_notes / "feedback"
    target_repo = _resolve_target_repo(args.target_repo)
    iteration = 0
    while True:
        remaining = _count_feedback_for_repo(feedback_dir, target_repo)
        if remaining == 0:
            if iteration == 0:
                print(f"対象リポジトリのinboxは空です（target_repo={target_repo}）。処理対象なし。")
            else:
                print(f"対象リポジトリのinboxが空になりました（{iteration}回実行、target_repo={target_repo}）。")
            return
        if args.max_iterations is not None and iteration >= args.max_iterations:
            print(f"反復上限{args.max_iterations}回に達しました（対象リポジトリのinbox残{remaining}件）。")
            return
        iteration += 1
        print(f"[反復 {iteration}] 対象リポジトリのinbox残{remaining}件、claudeを起動します")
        result = subprocess.run(
            ["claude", "--permission-mode=auto", "/process-feedbacks", target_repo],
            check=False,
        )
        if result.returncode != 0:
            print(
                f"claudeがexit code {result.returncode}で終了しました。反復を中断します。",
                file=sys.stderr,
            )
            sys.exit(result.returncode)


def main(
    argv: list[str] | None = None,
    *,
    home: pathlib.Path | None = None,
    now: datetime.datetime | None = None,
) -> None:
    """エントリポイント。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if home is None:
        home = pathlib.Path.home()
    if now is None:
        now = datetime.datetime.now()
    if args.subcommand == "enable":
        _cmd_enable(home)
        sys.exit(0)
    if args.subcommand == "disable":
        _cmd_disable(home)
        sys.exit(0)
    if args.subcommand == "status":
        _cmd_status(home)
    private_notes = _ensure_environment(home)
    dispatch = {
        "add": lambda: _cmd_add(args, private_notes, now, home),
        "list": lambda: _cmd_list(args, private_notes),
        "adopt": lambda: _cmd_adopt(args, private_notes),
        "reject": lambda: _cmd_reject(args, private_notes),
        "rm": lambda: _cmd_rm(args, private_notes),
        "edit": lambda: _cmd_edit(args, private_notes),
        "commit": lambda: _cmd_commit(private_notes),
        "process-loop": lambda: _cmd_process_loop(args, private_notes),
    }
    dispatch[args.subcommand]()
    sys.exit(0)
