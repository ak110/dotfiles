"""~/private-notesのフィードバック項目を操作するCLIエントリポイント。

サブコマンド構成。
- add: inboxへフィードバックを投入する
- list: inboxの全件を正規化リモートURLごとにグループ化して出力する
- adopt: 採用としてinboxからadopted/へ移動しコミット・push
- reject: 不採用としてinboxからrejected/へ移動しコミット・push
- rm: inboxから単純削除しコミット・push
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
import re
import shutil
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
    add.add_argument(
        "repo_path",
        metavar="REPO_PATH",
        help="フィードバック対象リポジトリのローカルパス（リモートURLを自動取得して格納）。",
    )
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
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルタする。",
    )

    adopt = sub.add_parser("adopt", help="採用としてinboxからadopted/へ移動しコミット・push")
    adopt.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="採用するinboxファイル名（1個以上）。"
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    reject = sub.add_parser("reject", help="不採用としてinboxからrejected/へ移動しコミット・push")
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
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）。既定は現在の作業リポジトリ。",
    )

    enable_completion(parser)
    return parser


def _feedback_filename_completer(prefix: str, **_: object) -> list[str]:
    """argcomplete用のフィードバックファイル名補完候補生成。

    `~/private-notes/feedback/inbox/`配下の`*.md`ファイル名をprefix一致で返す。
    ディレクトリ不在時は空リストを返す。
    """
    feedback_dir = pathlib.Path.home() / "private-notes" / "feedback" / "inbox"
    if not feedback_dir.exists():
        return []
    return sorted(p.name for p in feedback_dir.iterdir() if p.suffix == ".md" and p.name.startswith(prefix))


def _subdir(private_notes: pathlib.Path, name: str) -> pathlib.Path:
    """feedback/配下の指定サブディレクトリパスを返す。必要時に作成する。"""
    path = private_notes / "feedback" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    """指定ディレクトリ配下の`*.md`ファイル件数を返す。"""
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

    $EDITOR未設定・エディター非ゼロ終了・保存内容が空のいずれもNoneを返し、
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
            print(f"エディターが終了コード{result.returncode}で終了しました。", file=sys.stderr)
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
    target_repo = _resolve_repo_id(args.repo_path)
    messages = list(args.messages)
    if not messages:
        message = _collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    _pull(private_notes)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    created_iso = now.isoformat()
    inbox_dir = _subdir(private_notes, "inbox")
    counter = _max_existing_seq(inbox_dir, timestamp) + 1
    source_line = f"source: {args.source}\n" if args.source else ""
    generated: list[str] = []
    for message in messages:
        filename = f"{timestamp}-{counter:03d}.md"
        content = f"---\ncreated: {created_iso}\ntarget_repo: {target_repo}\n{source_line}---\n\n{message}\n"
        (inbox_dir / filename).write_text(content, encoding="utf-8")
        generated.append(filename)
        counter += 1
    count = len(generated)
    _commit_and_push(
        private_notes,
        f"chore: add {count} feedback {'item' if count == 1 else 'items'}",
        ["feedback"],
    )
    print(f"{count}件投入:")
    for filename in generated:
        print(f"  {_shorten_home(inbox_dir / filename, home)}")
    print(f"inbox: 計{_count_feedback(inbox_dir)}件")


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

    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    """
    inbox_dir = private_notes / "feedback" / "inbox"
    _pull(private_notes)
    if not inbox_dir.exists():
        return
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)
    entries: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(inbox_dir.iterdir()):
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
    """adoptサブコマンド: 採用としてinboxからadopted/へ移動しcommit・push。"""
    inbox_dir = private_notes / "feedback" / "inbox"
    _validate_filenames_only(args.filenames, inbox_dir)
    _pull(private_notes)
    paths = _resolve_feedback_targets(args.filenames, inbox_dir)
    adopted_dir = _subdir(private_notes, "adopted")
    for p in paths:
        shutil.move(p, adopted_dir / p.name)
    count = len(paths)
    _commit_and_push(
        private_notes,
        f"chore: process {count} feedback {'item' if count == 1 else 'items'} (adopted)",
        ["feedback"],
    )
    print(f"{count}件採用処理: {', '.join(p.name for p in paths)}")


def _cmd_reject(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rejectサブコマンド: 不採用としてinboxからrejected/へ移動しcommit・push。"""
    inbox_dir = private_notes / "feedback" / "inbox"
    _validate_filenames_only(args.filenames, inbox_dir)
    _pull(private_notes)
    paths = _resolve_feedback_targets(args.filenames, inbox_dir)
    rejected_dir = _subdir(private_notes, "rejected")
    for p in paths:
        shutil.move(p, rejected_dir / p.name)
    count = len(paths)
    _commit_and_push(
        private_notes,
        f"chore: process {count} feedback {'item' if count == 1 else 'items'} (rejected)",
        ["feedback"],
    )
    print(f"{count}件不採用処理: {', '.join(p.name for p in paths)}")


def _cmd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rmサブコマンド: inboxから単純削除しcommit・push。"""
    inbox_dir = private_notes / "feedback" / "inbox"
    _validate_filenames_only(args.filenames, inbox_dir)
    _pull(private_notes)
    paths = _resolve_feedback_targets(args.filenames, inbox_dir)
    for p in paths:
        p.unlink()
    count = len(paths)
    _commit_and_push(
        private_notes,
        f"chore: remove {count} feedback {'item' if count == 1 else 'items'}",
        ["feedback"],
    )
    print(f"{count}件削除: {', '.join(p.name for p in paths)}")


def _cmd_edit(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """editサブコマンド: $EDITORで対象ファイルを編集しcommit・push（差分なしなら無動作）。"""
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のため編集できません。", file=sys.stderr)
        sys.exit(1)
    inbox_dir = private_notes / "feedback" / "inbox"
    path = _validate_filename(args.filename, inbox_dir)
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
    inbox_rel = "feedback/inbox"
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", inbox_rel],
        cwd=private_notes,
        check=True,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        print("差分なし。")
        return
    _commit_and_push(private_notes, "chore: edit feedback items externally", [inbox_rel])
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


def _normalize_remote_url(url: str) -> str:
    """リモートURLを`host/owner/repo`形式へ正規化して返す。

    HTTPS形式・SSH短縮形式・SSH URI形式・既に正規化済みの`host/owner/repo`形式の4種を受理する。
    受理外はValueErrorを送出する。出力は全体小文字化し`.git`サフィックスを除去する。
    """
    # HTTPS: https://github.com/owner/repo[.git]
    m = re.match(r"https?://([^/:]+)/(.+)", url)
    if m:
        host = m.group(1)
        path = m.group(2)
        path = re.sub(r"\.git$", "", path)
        return f"{host}/{path}".lower()

    # SSH URI: ssh://git@github.com[:port]/owner/repo[.git]
    m = re.match(r"ssh://[^@]+@([^/:]+)(?::\d+)?/(.+)", url)
    if m:
        host = m.group(1)
        path = m.group(2)
        path = re.sub(r"\.git$", "", path)
        return f"{host}/{path}".lower()

    # SSH shorthand: git@github.com:owner/repo[.git]
    m = re.match(r"[^@]+@([^:]+):(.+)", url)
    if m:
        host = m.group(1)
        path = m.group(2)
        path = re.sub(r"\.git$", "", path)
        return f"{host}/{path}".lower()

    # Already normalized: host/owner/repo (2+ slashes, no scheme, no @)
    if re.match(r"[^/]+/[^/]+/[^/]+$", url) and "://" not in url and "@" not in url:
        return re.sub(r"\.git$", "", url).lower()

    raise ValueError(f"リモートURLとして解析できません: {url!r}")


def _resolve_local_worktree(value: str | None) -> pathlib.Path:
    """ローカル作業ツリーのパスを解決して返す。

    - `value`が実在するローカルパスなら`expanduser().resolve()`した結果を返す
    - `value`が実在しないパスやURL文字列なら「ローカルパスが必要」旨をstderrへ出力してexit 2
    - `value`省略時は`git rev-parse --show-toplevel`の出力を返す。失敗時もexit 2
    """
    if value is not None:
        local_path = pathlib.Path(value).expanduser()
        if not local_path.exists():
            print(
                f"ローカルパスとして存在しません（URLではなくローカルパスを指定してください）: {value}",
                file=sys.stderr,
            )
            sys.exit(2)
        return local_path.resolve()

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("git rev-parse --show-toplevel が失敗しました。gitリポジトリ内で実行してください。", file=sys.stderr)
        sys.exit(2)
    return pathlib.Path(result.stdout.strip())


def _resolve_repo_id(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
    """リポジトリ識別子（正規化リモートURL）を解決して返す。

    - `value`がURLらしい文字列（スキームを持つ・`@`を含む・スラッシュ2個以上の3要素）なら直接正規化する
    - ローカルパスとして判定した場合は`git -C <path> remote get-url origin`の出力を正規化する
    - `value`省略時は`cwd`（省略時は`_resolve_local_worktree`で取得した作業ツリー）を使う
    - パス不在・git未管理・remote未設定はexit 2で原因を標準エラー出力へ書く
    """
    if value is not None:
        # ローカルパスとして実在すればremote URLを取得して正規化、それ以外はURL文字列として正規化を試みる
        local_path = pathlib.Path(value).expanduser()
        if local_path.exists():
            local_path = local_path.resolve()
            result = subprocess.run(
                ["git", "-C", str(local_path), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                print(
                    f"リモートURLを取得できませんでした（git remote get-url origin）: {local_path}",
                    file=sys.stderr,
                )
                sys.exit(2)
            try:
                return _normalize_remote_url(result.stdout.strip())
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                sys.exit(2)
        try:
            return _normalize_remote_url(value)
        except ValueError:
            print(
                f"パスが存在せずリモートURLとしても解析できません: {value}",
                file=sys.stderr,
            )
            sys.exit(2)

    # value省略時: ローカル作業ツリーを特定してからremoteを取得
    if cwd is None:
        cwd = _resolve_local_worktree(None)
    result = subprocess.run(
        ["git", "-C", str(cwd), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"リモートURLを取得できませんでした（git remote get-url origin）: {cwd}",
            file=sys.stderr,
        )
        sys.exit(2)
    remote_url = result.stdout.strip()
    try:
        return _normalize_remote_url(remote_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)


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

    件数判定には`_resolve_repo_id`で取得した正規化リモートURLを使う。
    claudeへの起動引数には`--target-repo`指定値（未指定時は`git rev-parse --show-toplevel`の値）の
    ローカルパス文字列を渡す。
    """
    inbox_dir = private_notes / "feedback" / "inbox"

    # ローカルパスと正規化リモートURLをそれぞれ取得する
    local_path_str = str(_resolve_local_worktree(args.target_repo))
    repo_id = _resolve_repo_id(args.target_repo)

    iteration = 0
    while True:
        remaining = _count_feedback_for_repo(inbox_dir, repo_id)
        if remaining == 0:
            if iteration == 0:
                print(f"対象リポジトリのinboxは空です（target_repo={repo_id}）。処理対象なし。")
            else:
                print(f"対象リポジトリのinboxが空になりました（{iteration}回実行、target_repo={repo_id}）。")
            return
        if args.max_iterations is not None and iteration >= args.max_iterations:
            print(f"反復上限{args.max_iterations}回に達しました（対象リポジトリのinbox残{remaining}件）。")
            return
        iteration += 1
        print(f"[反復 {iteration}] 対象リポジトリのinbox残{remaining}件、claudeを起動します")
        result = subprocess.run(
            ["claude", "--permission-mode=auto", "/process-feedbacks", local_path_str],
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
