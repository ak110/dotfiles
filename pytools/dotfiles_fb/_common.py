"""feedback/tbd共通の環境判定・git操作・ファイル名検証ヘルパー。"""

import datetime
import os
import pathlib
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator

from pytools.dotfiles_fb._formatters import _parse_target_repo


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


def _stamp_result(
    path: pathlib.Path,
    *,
    outcome: str,
    now: datetime.datetime,
    commit: str | None = None,
    note: str | None = None,
) -> None:
    """対象ファイル末尾へ`## 処理結果`節を追記する。

    outcomeは`adopted`・`rejected`・`tbd-adopted`のいずれかを受け取る。
    commit・noteは省略可能で、指定時のみ対応する箇条書き項目を追加する。
    """
    body = path.read_text(encoding="utf-8")
    if not body.endswith("\n"):
        body += "\n"
    lines = [
        "",
        "## 処理結果",
        "",
        f"- 採否: {outcome}",
        f"- 処理日時: {now.isoformat(timespec='seconds')}",
    ]
    if commit:
        lines.append(f"- 対応commit: {commit}")
    if note:
        lines.append(f"- メモ: {note}")
    body += "\n".join(lines) + "\n"
    path.write_text(body, encoding="utf-8")


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


def _validate_filenames_only(filenames: list[str], base_dir: pathlib.Path) -> None:
    """ファイル名群のみ検証する（pull前の早期拒否用）。"""
    for f in filenames:
        _validate_filename(f, base_dir)


def _iter_inbox_entries(inbox_dir: pathlib.Path, target_repo: str | None = None) -> Iterator[tuple[pathlib.Path, str, str]]:
    """inbox配下の`.md`ファイルを名前順に走査し、`(path, target_repo, text)`を返す。

    `target_repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみ返す。ディレクトリ不在時は何も返さない。
    """
    if not inbox_dir.exists():
        return
    for path in sorted(inbox_dir.iterdir()):
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        entry_repo = _parse_target_repo(text)
        if target_repo is not None and entry_repo != target_repo:
            continue
        yield path, entry_repo, text


def _is_tbd_answered(text: str) -> bool:
    """TBD本文の`## 回答`節にHTMLコメント以外の非空内容があれば真。"""
    marker = "\n## 回答\n"
    idx = text.find(marker)
    if idx < 0:
        return False
    body = text[idx + len(marker) :]
    next_h2 = body.find("\n## ")
    if next_h2 >= 0:
        body = body[:next_h2]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        return True
    return False


def _count_pending_entries(
    private_notes: pathlib.Path,
    target_repo: str | None = None,
) -> int:
    """`process-loop`常駐ループ専用: feedback件数とTBD回答済み件数の合計を返す。

    `--type`・`--status`フィルタは持たず、常駐ループの反復判定に必要な合計のみを返す
    （`_list.py`の`_cmd_list`が持つフィルタ分岐との共通化は行わない）。
    """
    feedback_dir = private_notes / "feedback" / "inbox"
    feedback_count = sum(1 for _ in _iter_inbox_entries(feedback_dir, target_repo))
    tbd_dir = private_notes / "tbd" / "inbox"
    tbd_count = sum(1 for _, _, text in _iter_inbox_entries(tbd_dir, target_repo) if _is_tbd_answered(text))
    return feedback_count + tbd_count


def _count_feedback(feedback_dir: pathlib.Path) -> int:
    """指定ディレクトリ配下の`*.md`ファイル件数を返す。"""
    if not feedback_dir.exists():
        return 0
    return sum(1 for p in feedback_dir.iterdir() if p.suffix == ".md")


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
