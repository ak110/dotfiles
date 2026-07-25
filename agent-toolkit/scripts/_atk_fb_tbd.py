"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_tbd.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import os
import pathlib
import re
import subprocess
import sys

from _atk_fb_common import (
    WebInputError,
    _collect_message_via_editor,
    _commit_and_push,
    _copy_to_tempfile,
    _dedup_positional_filenames,
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
from _atk_fb_repo import edit_entry as _edit_entry


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


def _detect_self_containment_deficiency(message: str) -> str | None:
    """メッセージ本文が単独で判断可能な情報を欠くかをヒューリスティックで判定する。

    次の3判定を順に適用し、該当した最初の理由文字列を返す（該当なしなら`None`）。

    判定A（単独使用検出）: 一時識別子（`fb12345`・`Q12`等）を検出し、識別子の前後30文字
    以内に文脈語（「〜のため」「〜という」「〜について」「背景」「経緯」「対象」「実装」
    「反映」「採否」「判定」等）が無い場合に該当する。文脈語が近接する場合は自己完結と
    判断し警告しない。

    判定B（本文の短さ）: 本文の前後空白除去後の文字数が100文字未満の場合に該当する。
    判定Aで既に該当している場合は判定Bを重複起動せず、判定Aの理由のみを返す。

    判定C（判定根拠語彙の欠落）: 判定根拠に相当する語彙（「のため」「根拠」「選択肢」
    「理由」「背景」「トレードオフ」「観測事実」「判定」「経緯」「前提」「範囲」「方針」等）
    が本文中に皆無の場合に該当する。

    理由文字列は「一時識別子の単独使用」・「本文が短すぎる」・「判定根拠語彙の欠落」の
    3種を返す。判定は上記順序でショートサーキットする。
    """
    identifier_pattern = re.compile(r"(?:fb|Q|FB)\s?\d{2,}")
    context_words = (
        "のため",
        "という",
        "について",
        "背景",
        "経緯",
        "対象",
        "実装",
        "反映",
        "採否",
        "判定",
    )
    match = identifier_pattern.search(message)
    if match is not None:
        window_start = max(0, match.start() - 30)
        window_end = min(len(message), match.end() + 30)
        surrounding = message[window_start:window_end]
        if not any(word in surrounding for word in context_words):
            return "一時識別子の単独使用"
    if len(message.strip()) < 100:
        return "本文が短すぎる"
    reasoning_words = (
        "のため",
        "根拠",
        "選択肢",
        "理由",
        "背景",
        "トレードオフ",
        "観測事実",
        "判定",
        "経緯",
        "前提",
        "範囲",
        "方針",
    )
    if not any(word in message for word in reasoning_words):
        return "判定根拠語彙の欠落"
    return None


def add_tbd(
    private_notes: pathlib.Path,
    *,
    messages: list[str],
    target_repo: str,
    scope: str | None,
    source: str | None,
    question_type: str,
    choices: str | None,
    now: datetime.datetime,
    lock_timeout: float = -1,
) -> list[str]:
    """平引数でTBDを追加する。"""
    if question_type not in {"choice", "yes-no", "free-form"}:
        raise WebInputError("question_typeが不正です")
    if question_type == "choice" and not choices:
        raise WebInputError("choice形式にはchoicesが必要です")
    if not messages:
        raise WebInputError("messagesには1件以上を指定してください")
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        tbd_dir = _tbd_subdir(private_notes)
        counter = _max_existing_seq(tbd_dir, timestamp) + 1
        extra = (f"scope: {scope}\n" if scope else "") + (f"source: {source}\n" if source else "")
        extra += f"question_type: {question_type}\n"
        if choices:
            extra += f"choices: {choices}\n"
        generated: list[str] = []
        for message in messages:
            filename = f"{timestamp}-{counter:03d}.md"
            if question_type != "choice" and not _looks_like_question(message):
                print(
                    f"警告: {filename}の質問本文に問い（疑問文）が含まれていません。"
                    "回答者が何に答えるべきか分かる文面か確認してください。",
                    file=sys.stderr,
                )
            self_contained_reason = _detect_self_containment_deficiency(message)
            if self_contained_reason is not None:
                print(
                    f"警告: {filename}の質問本文が単独で判断可能な情報を欠く可能性があります"
                    f"（{self_contained_reason}）。"
                    "02-collaboration.mdが定める自己完結要件を満たす形に見直してください。",
                    file=sys.stderr,
                )
            content = (
                f"---\ntarget_repo: {target_repo}\n{extra}---\n\n"
                f"## 質問\n\n{message}\n\n## 回答\n\n"
                "<!-- ユーザーはこの行以降に回答を追記する -->\n"
            )
            (tbd_dir / filename).write_text(content, encoding="utf-8")
            generated.append(filename)
            counter += 1
        count = len(generated)
        _commit_and_push(private_notes, f"chore: add {count} tbd items", ["tbd"])
    return generated


def edit_tbd(
    private_notes: pathlib.Path,
    *,
    filename: str,
    content: str,
    target_repo: str | None = None,
    lock_timeout: float = -1,
    expected_content: str | None = None,
) -> bool:
    """平引数でTBD本文を更新する。"""
    directory = private_notes / "tbd/inbox"
    return _edit_entry(
        private_notes,
        directory=directory,
        filename=filename,
        content=content,
        target_repo=target_repo,
        lock_timeout=lock_timeout,
        expected_content=expected_content,
        commit_message="chore: edit tbd item",
    )


def answer_tbd(
    private_notes: pathlib.Path,
    *,
    filename: str,
    answer: str,
    lock_timeout: float = -1,
    expected_content: str | None = None,
) -> bool:
    """平引数でTBD回答欄を更新する。"""
    directory = private_notes / "tbd/inbox"
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        path = _validate_filename(filename, directory)
        if not path.is_file():
            raise FileNotFoundError(filename)
        marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
        text = path.read_text(encoding="utf-8")
        if expected_content is not None and text != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        if marker not in text:
            raise WebInputError("回答欄マーカーがありません")
        content = text.split(marker, maxsplit=1)[0] + marker + "\n" + answer.strip() + "\n"
        if text == content:
            return False
        path.write_text(content, encoding="utf-8")
        if not _is_tbd_answered(content):
            raise RuntimeError("TBD回答の保存後判定に失敗しました")
        _commit_and_push(private_notes, "chore: answer tbd item", [str(path.relative_to(private_notes))])
    return True


def transition_tbd(
    private_notes: pathlib.Path,
    *,
    action: str,
    filenames: list[str],
    now: datetime.datetime,
    note: str | None = None,
    commit: str | None = None,
    target_repo: str | None = None,
    lock_timeout: float = -1,
) -> list[str]:
    """平引数でTBDを採用又は削除する。"""
    if action not in {"adopt", "remove"}:
        raise WebInputError("TBD操作が不正です")
    inbox = private_notes / "tbd/inbox"
    _validate_filenames_only(filenames, inbox)
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        for filename in filenames:
            _verify_frontmatter_target_repo(filename, [inbox], target_repo)
        paths = _resolve_tbd_targets(filenames, inbox)
        rel_paths: list[str] = []
        if action == "adopt":
            adopted = private_notes / "tbd/adopted"
            adopted.mkdir(parents=True, exist_ok=True)
            for path in paths:
                _stamp_result(path, outcome="tbd-adopted", now=now, commit=commit, note=note)
                target = adopted / path.name
                path.rename(target)
                rel_paths.extend([str(path.relative_to(private_notes)), str(target.relative_to(private_notes))])
        else:
            for path in paths:
                path.unlink()
                rel_paths.append(str(path.relative_to(private_notes)))
        count = len(paths)
        item_word = "item" if count == 1 else "items"
        suffix = f" (理由: {note})" if action == "remove" and note else ""
        _commit_and_push(private_notes, f"chore: {action} {count} tbd {item_word}{suffix}", rel_paths)
    return [path.name for path in paths]


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
    question_type = {"free": "free-form", "yesno": "yes-no", "choice": "choice"}[args.question_type]
    generated = add_tbd(
        private_notes,
        messages=messages,
        target_repo=target_repo,
        scope=args.scope,
        source=args.source,
        question_type=question_type,
        choices=args.choices,
        now=now,
    )
    count = len(generated)
    tbd_dir = _tbd_subdir(private_notes)
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
        marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
        edited_text = answered.decode("utf-8")
        if marker not in edited_text:
            print(f"回答欄マーカーがありません: {path.name}", file=sys.stderr)
            tmp_path.unlink(missing_ok=True)
            continue
        try:
            answer_tbd(
                private_notes,
                filename=path.name,
                answer=edited_text.split(marker, maxsplit=1)[1],
                expected_content=snapshot.decode("utf-8"),
            )
        except RuntimeError:
            print(
                f"編集中に他プロセスが対象を変更しました: {path.name}。編集内容は{tmp_path}に残しています。スキップします。",
                file=sys.stderr,
            )
            had_conflict = True
            continue
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
    tmp_path = _copy_to_tempfile(snapshot)
    subprocess.run([editor, str(tmp_path)], check=True)
    edited = tmp_path.read_text(encoding="utf-8")
    original = snapshot.decode("utf-8")
    if edited == original:
        tmp_path.unlink(missing_ok=True)
        print("差分なし。")
        return
    try:
        edit_tbd(
            private_notes,
            filename=path.name,
            content=edited,
            target_repo=args.target_repo,
            expected_content=original,
        )
    except RuntimeError:
        print(
            f"編集中に他プロセスが対象を変更しました: {path.name}。"
            f"編集内容は{tmp_path}に残しています。再度atk tb editを実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    tmp_path.unlink(missing_ok=True)
    print(f"編集反映: {path.name}")


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
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "tb adopt")
    moved = transition_tbd(
        private_notes,
        action="adopt",
        filenames=args.filenames,
        now=now,
        note=args.note,
        commit=args.commit,
        target_repo=args.target_repo,
    )
    print(f"{len(moved)}件採用: {', '.join(moved)}")


def _cmd_tbd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """`tb rm`サブコマンド: TBDをtbd/inboxから単純削除しcommit・push。

    全ファイルの存在を削除前に一括検証し、途中失敗による部分削除を防ぐ。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    `--note`が指定された場合はcommit messageへ「(理由: <note>)」形式で追記する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "tb rm")
    removed = transition_tbd(
        private_notes,
        action="remove",
        filenames=args.filenames,
        now=datetime.datetime.now(),
        note=args.note,
        target_repo=args.target_repo,
    )
    print(f"{len(removed)}件削除: {', '.join(removed)}")
