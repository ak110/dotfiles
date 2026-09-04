"""agent-toolkitプラグイン配下の`atk wi`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_tbd.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import os
import pathlib
import re
import subprocess
import sys

from _atk_wi_common import (
    WI_PROCESSABLE_STATES,
    WI_STATE_HOLD,
    WI_STATE_INBOX,
    WI_STATE_PROCESSING,
    WI_TYPE_UWI,
    WebInputError,
    _commit_and_push,
    _copy_to_tempfile,
    _is_uwi_answered,
    _iter_entries,
    _pull,
    _repo_lock,
    _require_type,
    _validate_filename,
    is_agent_environment,
)
from _atk_wi_repo import _resolve_repo_id

ANSWER_MARKER = "<!-- ユーザーはこの行以降に回答を追記する -->"
"""TBDエントリの回答欄開始位置を示すHTMLコメント。

`_atk_wi_add.add_entries`が投入時に付与し、`answer_tbd`・`_cmd_answer`が回答本文の切り出しに使う。
本文字列を直接記述せず、常に本定数を参照する。
"""

QUESTION_HEADING = "## 質問"
"""TBDエントリの質問見出し。`_atk_wi_add.add_entries`が投入時に付与する。"""

ANSWER_HEADING = "## 回答"
"""TBDエントリの回答見出し。`_atk_wi_add.add_entries`が投入時に付与する。"""

_RESERVED_MARKUP_HEADINGS = (QUESTION_HEADING, ANSWER_HEADING)


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


def reject_reserved_tbd_markup(body: str) -> None:
    """TBD本文がツール側で自動付与する要素を含む場合に`WebInputError`を送出する。

    検査対象は回答欄マーカーと、行頭に現れる質問見出し・回答見出しとする。
    投入側が本文へ同じ要素を書くと`add_entries`が無検査で連結し二重生成となるため、
    警告ではなく拒否とする（既存の非ブロッキング警告`warn_question_quality`は無視された実績がある）。
    CLIとWeb UIの双方が`add_entries`を経由するため、本検査1箇所で両経路を覆う。
    """
    if ANSWER_MARKER in body:
        raise WebInputError("TBD本文に回答欄マーカーが含まれています。本文には質問内容のみを書いてください")
    for line in body.splitlines():
        if line.strip() in _RESERVED_MARKUP_HEADINGS:
            raise WebInputError(
                f"TBD本文にツールが自動付与する見出し（{line.strip()}）が含まれています。本文には質問内容のみを書いてください"
            )


def warn_question_quality(filename: str, message: str, question_type: str | None) -> None:
    """TBD投入時の質問本文の品質警告を標準エラーへ出力する。

    書式上の予約要素（回答欄マーカー・自動付与の見出し）の混入は
    `reject_reserved_tbd_markup`が拒否で扱い、本関数は内容面の品質のみを警告で扱う。
    """
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
            "agent-toolkit:add-awiが定める自己完結要件を満たす形に見直してください。",
            file=sys.stderr,
        )


def _resolve_active_entry(
    private_notes: pathlib.Path,
    filename: str,
    state: str | None = None,
) -> pathlib.Path:
    """状態指定時は完全一致、省略時は既存の優先順で対象を解決する。

    同名が複数の対象状態に存在する場合はprocessing、inbox、holdの順に優先する
    （`start-processing`後の中断復帰時にprocessing側が最新状態のため）。
    """
    if state is not None:
        if state not in (*WI_PROCESSABLE_STATES, WI_STATE_HOLD):
            raise WebInputError("stateはinbox、processing又はholdで指定してください")
        candidate = _validate_filename(filename, private_notes / state)
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(filename)
    for candidate_state in (WI_STATE_PROCESSING, WI_STATE_INBOX, WI_STATE_HOLD):
        candidate = _validate_filename(filename, private_notes / candidate_state)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(filename)


def require_tbd_entry(path: pathlib.Path, text: str) -> None:
    """対象エントリの種別がTBDでない場合に`WebInputError`を送出する。

    CLIとWeb APIの双方が呼び出す共通の検証とする。
    frontmatterの`type`のみを根拠とし、所在ディレクトリーは根拠にしない。
    """
    entry_type = _require_type(path, text)
    if entry_type != WI_TYPE_UWI:
        raise WebInputError(f"回答はTBDのエントリにのみ適用できます（type={entry_type}）: {path.name}")


def _answer_noninteractive(private_notes: pathlib.Path, *, filename: str, answer: str) -> None:
    """引数で受け取った回答本文をTBDの回答欄へ非対話で反映する。

    回答本文は回答欄マーカー以降へ置く本文だけを受け取る。
    マーカー自体を含む本文は、反映後にマーカーが二重化するため拒否する。
    対象解決・種別検証・commit・pushは`answer_tbd`が担う。
    対象不在時に`_resolve_active_entry`が送出する`FileNotFoundError`は、
    Tracebackを露出させず他サブコマンドと同じ文面の案内へ変換する。
    """
    if ANSWER_MARKER in answer:
        print(
            f"回答本文に回答欄マーカーを含められません: {filename}",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        changed = answer_tbd(private_notes, filename=filename, answer=answer)
    except FileNotFoundError:
        print(f"inbox・processingのいずれにも存在しません: {filename}", file=sys.stderr)
        sys.exit(1)
    except WebInputError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    if changed:
        print(f"1件回答反映: {filename}")
    else:
        print("差分なし。")


def answer_tbd(
    private_notes: pathlib.Path,
    *,
    filename: str,
    answer: str,
    state: str | None = None,
    lock_timeout: float = -1,
    expected_content: str | None = None,
) -> bool:
    """平引数でTBD回答欄を更新する。対象はinbox・processing・holdのTBDに限る。

    呼び出し元に依存せず、共有コア入口で空回答を拒否する。
    """
    if not answer.strip():
        raise WebInputError("回答本文が空です")
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        try:
            path = _resolve_active_entry(private_notes, filename, state)
        except FileNotFoundError as error:
            if expected_content is None:
                raise
            raise RuntimeError("編集中に他プロセスが対象を変更しました") from error
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            if expected_content is None:
                raise
            raise RuntimeError("編集中に他プロセスが対象を変更しました") from error
        if expected_content is not None and text != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        require_tbd_entry(path, text)
        if ANSWER_MARKER not in text:
            raise WebInputError("回答欄マーカーがありません")
        # 既存データにマーカーが重複するエントリが存在するため最後のマーカーを基準に分割する。
        # 最初のマーカーで分割すると回答見出しが消失し質問本文が途中で切断される。
        content = text.rsplit(ANSWER_MARKER, maxsplit=1)[0] + ANSWER_MARKER + "\n" + answer.strip() + "\n"
        if text == content:
            return False
        path.write_text(content, encoding="utf-8")
        _commit_and_push(private_notes, "chore: answer uwi item", [str(path.relative_to(private_notes))])
    return True


def _cmd_answer(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """answerサブコマンド: TBDへ回答する。

    `filename`と`answer_body`の双方を指定した場合は非対話で当該TBDの回答欄を更新する。
    いずれかを省略した場合は、active状態（inbox・processing）のうちfrontmatterの`type`が`tbd`かつ
    未回答のエントリを1件ずつ画面表示し`$EDITOR`で回答する。
    エディターが非ゼロ終了コードで終了した場合、以降の対象を中断してexit 1を返す
    （エディター起動失敗・ユーザーによる強制終了などを成功として扱わないため）。
    """
    if is_agent_environment():
        print(
            "TBDの回答はユーザーだけが書き込みます。エージェント環境から起動したatkでは回答できません。",
            file=sys.stderr,
        )
        sys.exit(1)
    filename = getattr(args, "filename", None)
    answer_body = getattr(args, "answer_body", None)
    if filename is not None and answer_body is not None:
        _answer_noninteractive(private_notes, filename=filename, answer=answer_body)
        return
    if filename is not None or answer_body is not None:
        print("非対話で回答する場合はファイル名と回答本文の両方を指定してください。", file=sys.stderr)
        sys.exit(1)
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
        for path, _repo, text, _state, _kind in _iter_entries(private_notes, WI_PROCESSABLE_STATES, filter_repo, WI_TYPE_UWI):
            if _is_uwi_answered(text):
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
        edited_text = answered.decode("utf-8")
        if ANSWER_MARKER not in edited_text:
            print(f"回答欄マーカーがありません: {path.name}", file=sys.stderr)
            tmp_path.unlink(missing_ok=True)
            continue
        try:
            answer_tbd(
                private_notes,
                filename=path.name,
                answer=edited_text.rsplit(ANSWER_MARKER, maxsplit=1)[1],
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
