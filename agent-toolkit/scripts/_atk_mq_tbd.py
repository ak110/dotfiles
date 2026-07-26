"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_tbd.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import os
import pathlib
import re
import subprocess
import sys

from _atk_mq_common import (
    MQ_ACTIVE_STATES,
    MQ_STATE_INBOX,
    MQ_STATE_PROCESSING,
    MQ_TYPE_TBD,
    WebInputError,
    _commit_and_push,
    _copy_to_tempfile,
    _is_tbd_answered,
    _iter_entries,
    _parse_type,
    _private_notes_path,
    _pull,
    _repo_lock,
    _require_type,
    _validate_filename,
)
from _atk_mq_repo import _resolve_repo_id


def _tbd_filename_completer(prefix: str, **_: object) -> list[str]:
    """argcomplete用のTBDファイル名補完候補生成。

    `AGENT_TOOLKIT_PRIVATE_NOTES`環境変数（未設定時は`~/private-notes/`）配下の
    active状態（inbox・processing）から、frontmatterの`type`が`tbd`のファイル名をprefix一致で返す。
    """
    private_notes = _private_notes_path(pathlib.Path.home())
    candidates: list[str] = []
    for state in MQ_ACTIVE_STATES:
        state_dir = private_notes / state
        if not state_dir.exists():
            continue
        for path in state_dir.iterdir():
            if path.suffix != ".md" or not path.name.startswith(prefix):
                continue
            if _parse_type(path.read_text(encoding="utf-8")) == MQ_TYPE_TBD:
                candidates.append(path.name)
    return sorted(candidates)


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


def warn_question_quality(filename: str, message: str, question_type: str | None) -> None:
    """TBD投入時の質問本文の品質警告を標準エラーへ出力する。"""
    if question_type != "choice" and not _looks_like_question(message):
        print(
            f"警告: {filename}の質問本文に問い（疑問文）が含まれていません。"
            "回答者が何に答えるべきか分かる文面か確認してください。",
            file=sys.stderr,
        )
    reason = _detect_self_containment_deficiency(message)
    if reason is not None:
        print(
            f"警告: {filename}の質問本文が単独で判断可能な情報を欠く可能性があります（{reason}）。"
            "agent-toolkit:process-feedbacksが定める自己完結要件を満たす形に見直してください。",
            file=sys.stderr,
        )


def _resolve_active_entry(private_notes: pathlib.Path, filename: str) -> pathlib.Path:
    """inbox・processingの順で対象ファイルを解決する。

    同名がinboxとprocessingの双方に存在する場合はprocessingを優先する
    （`start-processing`後の中断復帰時にprocessing側が最新状態のため）。
    """
    for state in (MQ_STATE_PROCESSING, MQ_STATE_INBOX):
        candidate = _validate_filename(filename, private_notes / state)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(filename)


def require_tbd_entry(path: pathlib.Path, text: str) -> None:
    """対象エントリの種別がTBDでない場合に`WebInputError`を送出する。

    CLIとWeb APIの双方が呼び出す共通の検証とする。
    frontmatterの`type`のみを根拠とし、所在ディレクトリーは根拠にしない。
    """
    entry_type = _require_type(path, text)
    if entry_type != MQ_TYPE_TBD:
        raise WebInputError(f"回答はTBDのエントリにのみ適用できます（type={entry_type}）: {path.name}")


def answer_tbd(
    private_notes: pathlib.Path,
    *,
    filename: str,
    answer: str,
    lock_timeout: float = -1,
    expected_content: str | None = None,
) -> bool:
    """平引数でTBD回答欄を更新する。対象はinbox・processingのTBDに限る。"""
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        path = _resolve_active_entry(private_notes, filename)
        marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
        text = path.read_text(encoding="utf-8")
        require_tbd_entry(path, text)
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


def _cmd_answer(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """answerサブコマンド: 未回答TBDを1件ずつ画面表示し$EDITORで回答する。

    走査対象はactive状態（inbox・processing）のうちfrontmatterの`type`が`tbd`かつ未回答のエントリとする。
    エディターが非ゼロ終了コードで終了した場合、以降の対象を中断してexit 1を返す
    （エディター起動失敗・ユーザーによる強制終了などを成功として扱わないため）。
    """
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のため回答経路を利用できません。", file=sys.stderr)
        sys.exit(1)
    targets: list[pathlib.Path] = []
    with _repo_lock(private_notes):
        _pull(private_notes)
        filter_repo: str | None = None
        if args.target_repo is not None:
            filter_repo = _resolve_repo_id(args.target_repo)
        for path, _repo, text, _state, _kind in _iter_entries(private_notes, MQ_ACTIVE_STATES, filter_repo, MQ_TYPE_TBD):
            if _is_tbd_answered(text):
                continue
            targets.append(path)
    if not targets:
        print("未回答のTBDはありません。")
        return
    edited: list[str] = []
    had_conflict = False
    editor_failed = False
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
            editor_failed = True
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
    elif not had_conflict and not editor_failed:
        print("差分なし。")
    if had_conflict or editor_failed:
        sys.exit(1)
