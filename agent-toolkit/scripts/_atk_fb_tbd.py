"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_tbd.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import os
import pathlib
import subprocess
import sys

from _atk_fb_common import (
    _collect_message_via_editor,
    _commit_and_push,
    _copy_to_tempfile,
    _edit_and_commit_via_editor,
    _is_tbd_answered,
    _iter_inbox_entries,
    _max_existing_seq,
    _private_notes_path,
    _pull,
    _reject_bare_repo_path_override,
    _repo_lock,
    _resolve_repo_path_override,
    _stamp_result,
    _validate_filename,
    _validate_filenames_only,
)
from _atk_fb_formatters import _parse_target_repo, _shorten_home
from _atk_fb_list import _render_tbd_entries
from _atk_fb_repo import _resolve_repo_id, _verify_frontmatter_target_repo


def _tbd_subdir(private_notes: pathlib.Path) -> pathlib.Path:
    """tbd/inbox配下のディレクトリパスを返す。必要時に作成する。"""
    path = private_notes / "tbd" / "inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tbd_filename_completer(prefix: str, **_: object) -> list[str]:
    """argcomplete用のTBDファイル名補完候補生成。

    `AGENT_TOOLKIT_PRIVATE_NOTES`環境変数（未設定時は`~/private-notes/`）配下の
    `tbd/inbox/*.md`ファイル名をprefix一致で返す。
    """
    tbd_dir = _private_notes_path(pathlib.Path.home()) / "tbd" / "inbox"
    if not tbd_dir.exists():
        return []
    return sorted(p.name for p in tbd_dir.iterdir() if p.suffix == ".md" and p.name.startswith(prefix))


def _looks_like_question(message: str) -> bool:
    """メッセージ本文に疑問文らしき表現が含まれるかを簡易判定する。

    `？`・`?`を部分文字列として含む場合、または末尾（句点除去後）が`か`で終わる場合を問いとみなす。
    高度な自然言語処理は導入せず、誤検知は許容してユーザーが目視で気づける警告にとどめる。
    """
    if "？" in message or "?" in message:
        return True
    return message.rstrip().rstrip("。").endswith("か")


def _cmd_tbd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """`tb add`サブコマンド: TBDをtbd/inboxへ投入してcommit・push。

    対象リポジトリは常にカレントディレクトリから解決する。ただし`tb add`直後のトークンが実在
    ディレクトリの場合は旧REPO_PATH位置引数形式の呼び出しとみなし、`atk.py`側の事前抽出で
    当該引数をREPO_PATHとして扱う（互換維持、抽出結果は`args.repo_path_override`で受け取る）。
    `--target-repo`指定時は、レガシーREPO_PATH位置引数が無い場合のfallback値として使う。
    `choice`類型以外は`_looks_like_question`で疑問文の有無を判定し、
    含まれない場合は投入対象ファイル名を添えて標準エラーへ警告する（投入自体は成功させる）。
    """
    messages, repo_path_override = _resolve_repo_path_override(args.messages, args.repo_path_override)
    _reject_bare_repo_path_override(repo_path_override, messages, args.subparser)
    if repo_path_override is not None:
        target_repo = _resolve_repo_id(repo_path_override)
    elif args.target_repo:
        target_repo = _resolve_repo_id(args.target_repo)
    else:
        target_repo = _resolve_repo_id(None)
    if args.question_type == "choice" and not args.choices:
        args.subparser.error("--question-type=choice のときは --choices を指定してください。")
    if not messages:
        message = _collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    with _repo_lock(private_notes):
        _pull(private_notes)
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        tbd_dir = _tbd_subdir(private_notes)
        counter = _max_existing_seq(tbd_dir, timestamp) + 1
        fm_extra = ""
        if args.scope:
            fm_extra += f"scope: {args.scope}\n"
        if args.source:
            fm_extra += f"source: {args.source}\n"
        fm_extra += f"question_type: {args.question_type}\n"
        if args.question_type == "choice":
            fm_extra += f"choices: {args.choices}\n"
        generated: list[str] = []
        for message in messages:
            filename = f"{timestamp}-{counter:03d}.md"
            if args.question_type != "choice" and not _looks_like_question(message):
                print(
                    f"警告: {filename}の質問本文に問い（疑問文）が含まれていません。"
                    "回答者が何に答えるべきか分かる文面か確認してください。",
                    file=sys.stderr,
                )
            content = (
                f"---\ntarget_repo: {target_repo}\n{fm_extra}---\n\n"
                f"## 質問\n\n{message}\n\n## 回答\n\n"
                "<!-- ユーザーはこの行以降に回答を追記する -->\n"
            )
            (tbd_dir / filename).write_text(content, encoding="utf-8")
            generated.append(filename)
            counter += 1
        count = len(generated)
        _commit_and_push(
            private_notes,
            f"chore: add {count} tbd {'item' if count == 1 else 'items'}",
            ["tbd"],
        )
    print(f"{count}件投入:")
    for filename in generated:
        print(f"  {_shorten_home(tbd_dir / filename, home)}")


def _cmd_tbd_list(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """`tb list`サブコマンド: TBD inboxを1件1行（filename・target_repo・本文冒頭要約）で出力する。

    出力形式は`list --type=tbd`と同一とし、target_repoグループ化・本文全文表示は行わない。
    本文全文表示が必要な場合は`show --all --type=tbd`または`show <filename>`を使う。
    """
    tbd_dir = private_notes / "tbd" / "inbox"
    if not args.skip_pull:
        with _repo_lock(private_notes):
            _pull(private_notes)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)
    entries: list[tuple[pathlib.Path, str, str]] = []
    for path, target_repo, text in _iter_inbox_entries(tbd_dir, filter_repo):
        answered = _is_tbd_answered(text)
        if args.status == "answered" and not answered:
            continue
        if args.status == "unanswered" and answered:
            continue
        entries.append((path, target_repo, text))
    _render_tbd_entries(entries)


def _cmd_tbd_answer(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """`tb answer`サブコマンド: 未回答TBDを1件ずつ画面表示し$EDITORで回答する。"""
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のため回答経路を利用できません。", file=sys.stderr)
        sys.exit(1)
    tbd_dir = private_notes / "tbd" / "inbox"
    targets: list[pathlib.Path] = []
    with _repo_lock(private_notes):
        _pull(private_notes)
        if not tbd_dir.exists():
            targets = []
        else:
            filter_repo: str | None = None
            if args.target_repo is not None:
                filter_repo = _resolve_repo_id(args.target_repo)
            for path in sorted(tbd_dir.iterdir()):
                if path.suffix != ".md":
                    continue
                text = path.read_text(encoding="utf-8")
                if filter_repo is not None and _parse_target_repo(text) != filter_repo:
                    continue
                if _is_tbd_answered(text):
                    continue
                targets.append(path)
    if not targets:
        print("未回答のTBDはありません。")
        return
    edited: list[str] = []
    had_conflict = False
    for path in targets:
        with _repo_lock(private_notes):
            _pull(private_notes)
            if not path.exists():
                continue
            print(f"--- {path.name} ---")
            print(path.read_text(encoding="utf-8"))
            snapshot = path.read_bytes()
        tmp_path = _copy_to_tempfile(snapshot)
        result = subprocess.run([editor, str(tmp_path)], check=False)
        if result.returncode != 0:
            print(
                f"エディターが終了コード{result.returncode}で終了しました。中断します。",
                file=sys.stderr,
            )
            tmp_path.unlink(missing_ok=True)
            break
        answered = tmp_path.read_bytes()
        if answered == snapshot:
            tmp_path.unlink(missing_ok=True)
            continue
        with _repo_lock(private_notes):
            _pull(private_notes)
            if not path.exists() or path.read_bytes() != snapshot:
                print(
                    f"編集中に他プロセスが対象を変更しました: {path.name}。"
                    f"編集内容は{tmp_path}に残しています。スキップします。",
                    file=sys.stderr,
                )
                had_conflict = True
                continue
            path.write_bytes(answered)
            rel = str(path.relative_to(private_notes))
            _commit_and_push(private_notes, "chore: answer 1 tbd item", [rel])
        tmp_path.unlink(missing_ok=True)
        edited.append(path.name)
    if edited:
        print(f"{len(edited)}件回答反映: {', '.join(edited)}")
    elif not had_conflict:
        print("差分なし。")
    if had_conflict:
        sys.exit(1)


def _cmd_tbd_edit(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """`tb edit`サブコマンド: $EDITORでTBDを編集してcommit・push。"""
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のため編集できません。", file=sys.stderr)
        sys.exit(1)
    tbd_dir = private_notes / "tbd" / "inbox"
    path = _validate_filename(args.filename, tbd_dir)
    with _repo_lock(private_notes):
        _pull(private_notes)
        _verify_frontmatter_target_repo(args.filename, [tbd_dir], args.target_repo)
        if not path.exists():
            print(f"tbd/inboxに存在しません: {path.name}", file=sys.stderr)
            sys.exit(2)
        snapshot = path.read_bytes()
    _edit_and_commit_via_editor(
        private_notes,
        path,
        snapshot,
        editor=editor,
        commit_message="chore: edit tbd item",
        retry_hint="再度atk tb editを実行してください。",
    )


def _resolve_tbd_targets(filenames: list[str], tbd_inbox: pathlib.Path) -> list[pathlib.Path]:
    """tbd/inbox配下のファイル名群を検証・解決し、未存在があればexit 2する。"""
    paths = [_validate_filename(f, tbd_inbox) for f in filenames]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"tbd/inboxに存在しません: {p.name}", file=sys.stderr)
        sys.exit(2)
    return paths


def _cmd_tbd_adopt(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """`tb adopt`サブコマンド: 回答済みTBDをtbd/inboxからtbd/adopted/へ移動しcommit・push。

    全ファイルの存在を移動前に一括検証し、途中失敗による部分移動を防ぐ。
    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    """
    tbd_inbox = private_notes / "tbd" / "inbox"
    tbd_adopted = private_notes / "tbd" / "adopted"
    _validate_filenames_only(args.filenames, tbd_inbox)
    with _repo_lock(private_notes):
        _pull(private_notes)
        for filename in args.filenames:
            _verify_frontmatter_target_repo(filename, [tbd_inbox], args.target_repo)
        paths = _resolve_tbd_targets(args.filenames, tbd_inbox)
        tbd_adopted.mkdir(parents=True, exist_ok=True)
        moved: list[str] = []
        rel_paths: list[str] = []
        for src in paths:
            _stamp_result(src, outcome="tbd-adopted", now=now, commit=args.commit, note=args.note)
            dst = tbd_adopted / src.name
            src.rename(dst)
            moved.append(src.name)
            rel_paths.append(str(src.relative_to(private_notes)))
            rel_paths.append(str(dst.relative_to(private_notes)))
        count = len(moved)
        _commit_and_push(
            private_notes,
            f"chore: adopt {count} tbd {'item' if count == 1 else 'items'}",
            rel_paths,
        )
    print(f"{count}件採用: {', '.join(moved)}")


def _cmd_tbd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """`tb rm`サブコマンド: TBDをtbd/inboxから単純削除しcommit・push。

    全ファイルの存在を削除前に一括検証し、途中失敗による部分削除を防ぐ。
    `--note`が指定された場合はcommit messageへ「(理由: <note>)」形式で追記する。
    """
    tbd_inbox = private_notes / "tbd" / "inbox"
    _validate_filenames_only(args.filenames, tbd_inbox)
    with _repo_lock(private_notes):
        _pull(private_notes)
        for filename in args.filenames:
            _verify_frontmatter_target_repo(filename, [tbd_inbox], args.target_repo)
        paths = _resolve_tbd_targets(args.filenames, tbd_inbox)
        for p in paths:
            p.unlink()
        count = len(paths)
        suffix = f" (理由: {args.note})" if args.note else ""
        _commit_and_push(
            private_notes,
            f"chore: remove {count} tbd {'item' if count == 1 else 'items'}{suffix}",
            ["tbd"],
        )
    print(f"{count}件削除: {', '.join(p.name for p in paths)}")
