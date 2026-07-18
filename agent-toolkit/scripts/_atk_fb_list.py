"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_list.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import pathlib
import shutil

from _atk_fb_common import (
    FEEDBACK_ACTIVE_STATES,
    FEEDBACK_STATE_ADOPTED,
    FEEDBACK_STATE_INBOX,
    FEEDBACK_STATE_PROCESSING,
    FEEDBACK_STATE_REJECTED,
    _is_tbd_answered,
    _iter_feedback_entries_with_state,
    _iter_inbox_entries,
    _pull,
)
from _atk_fb_formatters import _body_summary, _display_width, _tbd_body_summary
from _atk_fb_repo import _resolve_repo_id


def _category_line_matches(line: str, category: str) -> bool:
    """カテゴリ記録行が指定カテゴリと一致するか判定する。"""
    stripped = line.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].strip()
    return bool(stripped.startswith("カテゴリ:") and stripped.removeprefix("カテゴリ:").strip() == category)


def _has_category(text: str, category: str) -> bool:
    """`## 処理結果`節に指定カテゴリが記録されているか判定する。"""
    lines = text.splitlines()
    in_result_section = False
    for line in lines:
        if line.startswith("## "):
            in_result_section = line.strip() == "## 処理結果"
            continue
        if in_result_section and _category_line_matches(line, category):
            return True
    return False


def _render_tbd_entries(entries: list[tuple[pathlib.Path, str, str]]) -> None:
    """TBDエントリ一覧を`# tbd`種別ヘッダ付きの1件1行形式で標準出力へ出力する。

    入力は`_iter_inbox_entries`の返り値と同形式の`(path, target_repo, text)`リストとする。
    `atk fb list`と`atk tb list`の双方から共通出力ヘルパーとして呼び出す。
    入力が空リストの場合は何も出力しない。
    """
    if not entries:
        return
    print("# tbd")
    for path, target_repo, text in entries:
        label = "answered" if _is_tbd_answered(text) else "unanswered"
        prefix = f"{path.name}: {target_repo} [{label}] "
        available_width = shutil.get_terminal_size().columns - _display_width(prefix)
        print(f"{prefix}{_tbd_body_summary(text, available_width)}")


def _cmd_list(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """listサブコマンド: feedback/tbdを1件1行（filename・target_repo・状態・要約）で出力する。

    `--type`指定で出力対象種別（feedback・tbd・all）を限定する（既定: all）。
    `--status`指定で表示範囲を限定する（既定: active）。
    `active`はfeedback側`inbox`・`processing`とtbd側`answered`を出力する。
    feedback側は`inbox`・`processing`・`adopted`・`rejected`・`all`を解釈する。
    tbd側は`answered`・`unanswered`で回答状況を限定する（`inbox`・`processing`・`adopted`・`rejected`・`all`は
    tbd側に作用せず、tbd inboxの全件を返す）。
    `--category`指定時はfeedback側のみを指定ラベルへ限定する。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--type=all`（既定）指定時、該当部エントリが1件以上ある場合のみ種別ヘッダを出力する。
    `--count`指定時は、フィルター適用後のfeedback件数とTBD件数の合計を整数のみで出力し、
    種別ヘッダ・エントリ行は出力しない。
    """
    if not args.skip_pull:
        _pull(private_notes)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)

    # --status=activeはfeedback側`inbox`+`processing`、tbd側`answered`を指す。
    # --status=allは4状態フォルダ全連結。個別状態指定時は当該状態のみ。
    feedback_states: tuple[str, ...]
    if args.status == "active":
        feedback_states = FEEDBACK_ACTIVE_STATES
    elif args.status == "all":
        feedback_states = (
            FEEDBACK_STATE_INBOX,
            FEEDBACK_STATE_PROCESSING,
            FEEDBACK_STATE_ADOPTED,
            FEEDBACK_STATE_REJECTED,
        )
    elif args.status in (
        FEEDBACK_STATE_INBOX,
        FEEDBACK_STATE_PROCESSING,
        FEEDBACK_STATE_ADOPTED,
        FEEDBACK_STATE_REJECTED,
    ):
        feedback_states = (args.status,)
    else:
        # tbd専用フィルタ（answered/unanswered）指定時はfeedback側はactive扱い
        feedback_states = FEEDBACK_ACTIVE_STATES

    feedback_entries_with_state: list[tuple[pathlib.Path, str, str, str]] = []
    if args.type in ("all", "feedback"):
        feedback_entries_with_state = list(_iter_feedback_entries_with_state(private_notes, feedback_states, filter_repo))
        if args.category is not None:
            feedback_entries_with_state = [
                entry for entry in feedback_entries_with_state if _has_category(entry[2], args.category)
            ]

    tbd_entries: list[tuple[pathlib.Path, str, str]] = []
    if args.type in ("all", "tbd"):
        tbd_dir = private_notes / "tbd" / FEEDBACK_STATE_INBOX
        for path, target_repo, text in _iter_inbox_entries(tbd_dir, filter_repo):
            answered = _is_tbd_answered(text)
            if args.status == "answered" and not answered:
                continue
            if args.status == "unanswered" and answered:
                continue
            if args.status == "active" and not answered:
                continue
            tbd_entries.append((path, target_repo, text))

    if args.count:
        print(len(feedback_entries_with_state) + len(tbd_entries))
        return

    if feedback_entries_with_state:
        print("# feedback")
        for path, target_repo, text, state in feedback_entries_with_state:
            prefix = f"{path.name}: {target_repo} [{state}] "
            available_width = shutil.get_terminal_size().columns - _display_width(prefix)
            print(f"{prefix}{_body_summary(text, available_width)}")

    _render_tbd_entries(tbd_entries)
