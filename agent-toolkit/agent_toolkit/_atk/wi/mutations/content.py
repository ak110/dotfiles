from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.wi import add as _add
from agent_toolkit._atk.wi import cli_input as _wi_cli_input
from agent_toolkit._atk.wi import constants as _constants
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import headings as _headings
from agent_toolkit._atk.wi import processing_owner as _processing_owner
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import user_comment as _user_comment
from agent_toolkit._atk.wi import uwi as _uwi
from agent_toolkit._atk.wi.constants import (
    DETAILED_KEY,
    DETAILED_SOURCES,
    WI_EDITABLE_STATES,
    WI_STATE_HOLD,
    WI_STATE_INBOX,
    WI_STATE_PROCESSING,
    WI_TYPE_AWI,
    WI_TYPE_UWI,
    is_detailed,
)
from agent_toolkit._atk.wi.mutations.targets import invalidate_repo_bound_metadata, resolve_editable_targets
from agent_toolkit._atk.wi.repo import append_entry as _append_entry
from agent_toolkit._atk.wi.repo import edit_entry as _edit_entry
from agent_toolkit._atk.wi.repo import (
    resolve_repo_id,
    verify_target_repo_content,
)
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common.next_action import ActionableError, recover_from_failure

_USER_COMMENT_EDIT_NEXT_ACTION = (
    "本文からユーザーコメント節（`## ユーザーコメント`見出し以降）を除いて再実行する。"
    "保存済みのユーザーコメント節は変更されずに残る"
)
_USER_COMMENT_BROKEN_NEXT_ACTION = recover_from_failure(
    component="WIのユーザーコメント",
    evidence="保存原本",
    first_step="`atk wi show {name}`でユーザーコメント節の構造を確認する",
    retry="本人のコメントを改変せず、構造の不良を解消してから再試行する",
)
_BROKEN_STORED_NEXT_ACTION = recover_from_failure(
    component="WIの本文",
    evidence="保存原本",
    first_step="`atk wi show {name}`で本文の構造を確認する",
    retry="元の内容を保持し、構造を修復してから再試行する",
)
_EDITABLE_STATE_NEXT_ACTION = (
    "inbox・processing・holdの項目を指定する。終了した項目は"
    "`atk wi hold <ファイル名> --state <adopted|rejected>`（現在の状態を指定）でholdへ戻してから編集する"
)
_RESERVED_EDIT_KEY_NEXT_ACTIONS = {
    "state": "状態はatk wiの状態遷移コマンドで変更する",
    "depends_on": "frontmatterからdepends_onを除き、依存は`atk wi set-dependencies`で更新する",
    "target_commit": "frontmatterからtarget_commitを除いて再実行する（target_commitはatkが記録する）",
    "cooldown_until": "frontmatterからcooldown_untilを除き、期限は`atk wi edit <FILE> --cooldown-until`で設定する",
    "effect_check": "frontmatterからeffect_checkを除き、効果は`atk wi effect-check`で記録する",
    "repair_target": "frontmatterからrepair_targetを除いて再実行する（repair_targetはatkが記録する）",
    "repair_kind": "frontmatterからrepair_kindを除いて再実行する（repair_kindはatkが記録する）",
    "plan_file": (
        "frontmatterからplan_fileを除いて再実行する（plan_fileは廃止した型で保存された項目のメタデータであり、新しく書けない）"
    ),
    "priority": "frontmatterからpriorityを除き、優先区分は`atk wi set-priority`で設定する",
    "github_source": "frontmatterからgithub_sourceを除き、保存されたGitHubの出所は変更しない",
    "processing_owner": "frontmatterからprocessing_ownerを除き、担当はatk wi take-overで変更する",
    "detailed": "frontmatterからdetailedを除き、詳細化した本文の保存では--detailを指定する",
    "proposal_fingerprint": "frontmatterからproposal_fingerprintを除いて再実行する（原稿の指紋はatkが記録する）",
    "submitter_session": "frontmatterからsubmitter_sessionを除いて再実行する（submitter_sessionはatkが記録する）",
    "run_skill_run_id": "frontmatterからrun_skill_run_idを除いて再実行する（実行識別子はatkが記録する）",
}
"""`atk wi edit`が変更を受け付けない予約キーと、代わりに行う操作の対応。"""


def set_entry_importance(
    private_notes: pathlib.Path,
    *,
    filename: str,
    important: bool,
    expected_content: str | None = None,
    storage: _wi_sync.Storage = _wi_sync.DEFAULT_STORAGE,
) -> bool:
    with storage.mutation(private_notes) as mutation:
        records, missing = _wi_entries.read_named_entries(
            private_notes, _wi_entries.validate_named_filenames(private_notes, [filename]), target_repo=None
        )
        if missing:
            raise FileNotFoundError(filename)
        path, _, text, _, _ = records[0]
        if expected_content is not None and text != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError("frontmatterを解析できません", next_action="保存済み本文のfrontmatterを確認する")
        metadata, body = parsed
        if (important and metadata.get("important") is True) or (not important and "important" not in metadata):
            return False
        if important:
            metadata["important"] = True
        else:
            metadata.pop("important", None)
        updated = _frontmatter.serialize_frontmatter(metadata, body)
        if updated == text:
            return False
        _frontmatter.write_entry_text(path, updated)
        mutation.commit("chore: update wi importance", [str(path.relative_to(private_notes))])
    return True


def _outside_fence_heading_offset(content: str, heading: str) -> int | None:
    offset = 0
    fence: str | None = None
    for line in content.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        marker = stripped.lstrip(" ")
        if marker.startswith(("```", "~~~")):
            token = marker[:3]
            if fence is None:
                fence = token
            elif token == fence:
                fence = None
        elif fence is None and stripped == heading:
            return offset
        offset += len(line.encode("utf-8"))
    return None


def _message_starts_result_heading(message: str) -> bool:
    return next((line for line in message.splitlines() if line.strip()), "") == "## 処理結果"


def _reject_agent_user_comment_change(original: str, updated: str) -> bool:
    if not is_agent_environment():
        return False
    try:
        changed = _user_comment.extract_user_comment(original) != _user_comment.extract_user_comment(updated)
    except _user_comment.UserCommentError:
        changed = True
    if not changed:
        return False
    _outcome.report_failure(_user_comment.AGENT_USER_COMMENT_EDIT_ERROR, next_action=_USER_COMMENT_EDIT_NEXT_ACTION)
    return True


def _reject_agent_user_comment_message(message: str) -> bool:
    if not is_agent_environment() or not _user_comment.has_reserved_heading(message):
        return False
    _outcome.report_failure(_user_comment.AGENT_USER_COMMENT_EDIT_ERROR, next_action=_USER_COMMENT_EDIT_NEXT_ACTION)
    return True


def _preserve_agent_user_comment(original: str, updated: str) -> str:
    if not is_agent_environment():
        return updated
    try:
        _before, saved_user_comment = _user_comment.split_before_user_comment(original)
    except _user_comment.UserCommentError:
        _outcome.report_failure(
            _user_comment.AGENT_USER_COMMENT_EDIT_ERROR,
            next_action=_USER_COMMENT_BROKEN_NEXT_ACTION.format(name="<FILE>"),
        )
        sys.exit(1)
    if not saved_user_comment:
        return updated
    return updated.rstrip("\r\n") + "\n\n" + saved_user_comment


def _read_expected_user_comment(path: str) -> str:
    try:
        return pathlib.Path(path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _outcome.report_failure(
            f"--move-user-commentのファイルをUTF-8として読めない: {path}: {error}",
            next_action="整理に使ったユーザーコメントの本文をUTF-8のファイルへ保存し、その絶対パスを指定する",
        )
        sys.exit(1)


def _move_user_comment(original: str, edited: str, name: str, expected: str) -> None:
    show_section = f"`atk wi show {name} --section ユーザーコメント`"
    try:
        comment = _user_comment.extract_user_comment(original)
    except _user_comment.UserCommentError:
        _outcome.report_failure(
            f"保存済みのユーザーコメント節の構造が壊れているため移せない: {name}",
            next_action=_USER_COMMENT_BROKEN_NEXT_ACTION.format(name=name),
        )
        sys.exit(1)
    if not comment:
        _outcome.report_failure(
            f"保存済みのユーザーコメントが無いため、編集を反映しない: {name}",
            next_action=f"{show_section}で現在の記入を読み直し、取り下げられたコメントを本文へ反映し直してから再実行する",
        )
        sys.exit(1)
    if not _user_comment.same_user_comment(comment, expected):
        _outcome.report_failure(
            f"保存済みのユーザーコメントが、整理に使ったコメントと一致しないため、編集を反映しない: {name}",
            next_action=f"{show_section}で現在のコメントを読み直し、変わった記入を本文へ反映して再実行する",
        )
        sys.exit(1)
    if _user_comment.has_reserved_heading(edited):
        _outcome.report_failure(
            f"--move-user-commentでは本文へユーザーコメント節を含められない: {name}",
            next_action="本文から`## ユーザーコメント`節を除き、その内容を`## ユーザー指摘の逐語引用`へ移して再実行する",
        )
        sys.exit(1)
    if not _user_comment.quotes_user_comment(edited, comment):
        _outcome.report_failure(
            f"保存済みのユーザーコメントの全行が本文の`## ユーザー指摘の逐語引用`に無いため、編集を反映しない: {name}",
            next_action=f"{show_section}で現在のコメントを読み直し、全行を`## ユーザー指摘の逐語引用`へ移して再実行する",
        )
        sys.exit(1)


def _require_agent_edit_source(original: str, content: str) -> None:
    if not is_agent_environment():
        return
    stored = _frontmatter.parse_frontmatter(original)
    parsed = _frontmatter.parse_frontmatter(content)
    if stored is not None and parsed is not None and "source" not in stored[0] and "source" not in parsed[0]:
        return
    raw_source = parsed[0].get("source") if parsed is not None else None
    if not isinstance(raw_source, str) or not raw_source:
        raise WebInputError(
            "エージェント環境ではsourceの明示が必須です。本文先頭のfrontmatterで`source: <出所>`を指定してください。",
            next_action="本文先頭のfrontmatterへ`source: <出所>`を追記して再実行する",
        )
    if raw_source == "human" and (stored is None or stored[0].get("source") != "human"):
        raise WebInputError(
            "エージェント環境ではsourceを新しくhumanに変更できません。",
            next_action=(
                "本文先頭のfrontmatterのsourceへ、起動中の作業かスキルの名前を指定する"
                "（プラグイン接頭辞を除く。`/atk-mode add-awi-by-user`なら`add-awi-by-user`）。"
                "該当する作業もスキルも無い場合はagentを指定して再実行する"
            ),
        )


def edit_entry_content(
    private_notes: pathlib.Path,
    *,
    state: str,
    filename: str,
    content: str,
    target_repo: str | None = None,
    storage: _wi_sync.Storage = _wi_sync.DEFAULT_STORAGE,
    expected_content: str | None = None,
    finalized_content: dict[str, str] | None = None,
    allow_terminal: bool = False,
) -> bool:
    editable_states = (
        (*WI_EDITABLE_STATES, _constants.WI_STATE_ADOPTED, _constants.WI_STATE_REJECTED)
        if allow_terminal
        else WI_EDITABLE_STATES
    )
    if state not in editable_states:
        raise WebInputError("編集可能状態はinbox、processingまたはholdです", next_action=_EDITABLE_STATE_NEXT_ACTION)

    directory = private_notes / state
    return _edit_entry(
        private_notes,
        directory=directory,
        filename=filename,
        content=content,
        target_repo=target_repo,
        storage=storage,
        expected_content=expected_content,
        commit_message="chore: edit wi item",
        content_transformer=invalidate_repo_bound_metadata,
        finalized_content=finalized_content,
    )


def append_entry_content(
    private_notes: pathlib.Path,
    *,
    state: str,
    filename: str,
    content: bytes,
    target_repo: str | None = None,
    storage: _wi_sync.Storage = _wi_sync.DEFAULT_STORAGE,
    expected_content: bytes | None = None,
    finalized_content: dict[str, str] | None = None,
) -> bool:
    if state not in WI_EDITABLE_STATES:
        raise WebInputError("追記可能状態はinbox、processingまたはholdです", next_action=_EDITABLE_STATE_NEXT_ACTION)

    directory = private_notes / state
    paths, _missing = _wi_entries.read_named_entries(
        private_notes, _wi_entries.validate_named_filenames(private_notes, [filename]), target_repo=None
    )
    path = paths[0][0] if paths else directory / filename

    def validate(previous: str, updated: str) -> None:
        del updated
        if _wi_entries.entry_type_of(path, previous) == WI_TYPE_UWI:
            raise WebInputError("UWIには追記できません", next_action="--appendを外して本文全体を置き換える")

    return _append_entry(
        private_notes,
        directory=directory,
        filename=filename,
        content=content,
        target_repo=target_repo,
        storage=storage,
        expected_content=expected_content,
        commit_message="chore: append wi item",
        content_validator=validate,
        finalized_content=finalized_content,
    )


def _strip_trailing_blank_lines(body: str) -> str:
    lines = body.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _build_noninteractive_edit_content(path: pathlib.Path, original: str, message: str) -> str:
    parsed = _frontmatter.parse_frontmatter(original)
    if parsed is None:
        raise WebInputError(
            f"frontmatterが破損しているため編集できません: {path.name}",
            next_action=_BROKEN_STORED_NEXT_ACTION.format(name=path.name),
        )
    stored_data, stored_body = parsed
    entry_type = _wi_entries.entry_type_of(path, original)
    assert entry_type is not None
    message_frontmatter, message_body = _add.parse_entry_message(message, entry_type=entry_type)
    normalized_message_body = message_body.strip("\n")

    requested_type = message_frontmatter.get("type")
    if requested_type is not None and requested_type != entry_type:
        _outcome.report_failure(
            f"typeは変更できない（現在値: {entry_type}）: {path.name}",
            next_action="frontmatterのtypeを元の値へ戻して再実行する",
        )
        sys.exit(2)
    if entry_type != WI_TYPE_UWI:
        uwi_only_keys = sorted({"scope", "question_type", "choices"} & message_frontmatter.keys())
        if uwi_only_keys:
            raise WebInputError(
                f"AWIでは指定できないメタデータです: {', '.join(uwi_only_keys)}",
                next_action=f"frontmatterから{'・'.join(uwi_only_keys)}を除いて再実行する",
            )

    updates = dict(message_frontmatter)
    if "target_repo" in updates:
        raw_target_repo = updates["target_repo"]
        if not isinstance(raw_target_repo, str):
            raise WebInputError(
                "target_repoは文字列で指定してください",
                next_action="frontmatterのtarget_repoへローカルworktreeのパスかremote URLを文字列で指定する",
            )
        updates["target_repo"] = resolve_repo_id(raw_target_repo, warn_non_origin=True)
    for reserved_key, reserved_next_action in _RESERVED_EDIT_KEY_NEXT_ACTIONS.items():
        if reserved_key in updates and (reserved_key not in stored_data or updates[reserved_key] != stored_data[reserved_key]):
            raise WebInputError(
                f"{reserved_key}は予約キーのため atk wi edit では指定できません",
                next_action=reserved_next_action,
            )
    updated_data = {**stored_data, **updates}
    if entry_type == WI_TYPE_UWI:
        updated_data = _add.run_report_metadata(updated_data)
    target_repo_changed = "target_repo" in updates and stored_data.get("target_repo") != updates["target_repo"]
    if target_repo_changed:
        updated_data.pop("target_commit", None)

    if entry_type != WI_TYPE_UWI:
        return _frontmatter.serialize_frontmatter(
            updated_data, "\n" + _strip_trailing_blank_lines(normalized_message_body) + "\n"
        )

    question_type = updated_data.get("question_type")
    # 保存済みのfree-formは読取互換として本文編集を受理し、他の形式からfree-formへ変える編集だけを拒否する。
    accepted_question_types = (
        _constants.STORED_QUESTION_TYPES
        if stored_data.get("question_type") == _constants.QUESTION_TYPE_FREE_FORM
        else _constants.NEW_QUESTION_TYPES
    )
    if question_type not in accepted_question_types:
        raise WebInputError(
            f"question_typeが不正です: {question_type}",
            next_action="frontmatterのquestion_typeへchoice、yes-no、post-approvalを指定する（free-formは新しく指定できない）",
        )
    if question_type == _constants.QUESTION_TYPE_CHOICE and not updated_data.get("choices"):
        raise WebInputError(
            "choice形式にはchoicesが必要です",
            next_action="frontmatterのchoicesへ選択肢を指定するか、question_typeをyes-noへ変える",
        )

    if question_type == _constants.QUESTION_TYPE_POST_APPROVAL:
        if "choices" in updates:
            raise WebInputError("post-approval形式の選択肢は固定です", next_action="編集本文のfrontmatterからchoicesを外す")
        updated_data.pop("choices", None)

    marker_index = stored_body.rfind(_uwi.ANSWER_MARKER)
    if marker_index < 0:
        raise WebInputError("回答欄マーカーがありません", next_action=_BROKEN_STORED_NEXT_ACTION.format(name=path.name))
    answer_heading_index = stored_body.rfind(_uwi.ANSWER_HEADING, 0, marker_index)
    question_heading_index = stored_body.rfind(_uwi.QUESTION_HEADING, 0, answer_heading_index)
    if answer_heading_index < 0 or question_heading_index < 0:
        raise WebInputError(
            "UWIの質問見出しまたは回答見出しがありません",
            next_action=_BROKEN_STORED_NEXT_ACTION.format(name=path.name),
        )
    question_heading_end = question_heading_index + len(_uwi.QUESTION_HEADING)
    updated_body = (
        stored_body[:question_heading_end]
        + "\n\n"
        + normalized_message_body.rstrip()
        + "\n\n"
        + stored_body[answer_heading_index:]
    )
    return _frontmatter.serialize_frontmatter(updated_data, updated_body)


def _resolve_edit_message(args: argparse.Namespace) -> str | None:
    if args.body_file is None:
        return None
    try:
        return _add.read_body_files([args.body_file])[0]
    except WebInputError as error:
        _outcome.report_failure(f"編集を拒否した: {error}", next_action=error.next_action)
        sys.exit(1)


def _apply_cooldown_edit(content: str, value: str, filename: str) -> str:
    """再処理抑制期限をAWIのfrontmatterへ設定し、空文字列では種別によらず削除する。

    期限を適用するのはAWIだけであり、他の種別へ保存すると着手可否判定が不正期限として扱う。
    空文字列による削除は、保存済みの不正期限を修復できるよう種別と保存値の妥当性を問わず受理する。
    """
    parsed = _frontmatter.parse_frontmatter(content)
    if parsed is None:
        raise WebInputError(
            "frontmatterが破損しているため期限を変更できません",
            next_action=_BROKEN_STORED_NEXT_ACTION.format(name=filename),
        )
    data, body = parsed
    if value:
        entry_type = _constants.normalized_wi_type(data.get("type"))
        if entry_type != WI_TYPE_AWI:
            raise WebInputError(
                f"再処理抑制期限を設定できるのはAWIだけです（この項目の種別: {entry_type or '不明'}）",
                next_action=(
                    "AWIを指定して再実行する。この項目に保存済みの期限を除く場合は"
                    f"`atk wi edit {filename} --cooldown-until ''`で解除する"
                ),
            )
        data["cooldown_until"] = _add.normalize_cooldown_until(value)
    else:
        data.pop("cooldown_until", None)
    return _frontmatter.serialize_frontmatter(data, body)


def _reject_agent_processing_edit(path: pathlib.Path, *, detail: bool = False, message: str | None = None) -> None:
    if not is_agent_environment():
        return
    state = _wi_entries.entry_state_of(path)
    if state == WI_STATE_HOLD:
        reject_frozen_hold_edit(path, message)
        return
    if state != WI_STATE_PROCESSING:
        return
    if detail:
        # 詳細化の保存は、処理中の担当自身による着手前の1回の置換に限って許す。  # pyfltr: allow-comment
        # 他の会話からの置換と再置換は、実装・レビューの基準を途中で変えるため拒否する。  # pyfltr: allow-comment
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is not None and (is_detailed(parsed[0]) or parsed[0].get("source") in DETAILED_SOURCES):
            _outcome.report_failure(
                f"詳細化済みか詳細化の対象外のprocessingの項目は本文を置き換えられない: {path.name}",
                next_action=(
                    "実装中に確定した要件の変更は実行レビュー指摘管理表へ記録し、この項目へはatk wi edit --appendで追記する"
                ),
            )
            sys.exit(2)
        owner = _processing_owner.from_text(text)
        if owner is not None and owner == _processing_owner.current():
            return
        _outcome.report_failure(
            f"processingの項目の詳細化は処理中の担当の会話からだけ保存できる: {path.name}（担当: {owner or '記録なし'}）",
            next_action="処理中の担当の会話から再実行するか、担当へ原稿を渡して保存を依頼する",
        )
        sys.exit(2)
    _outcome.report_failure(
        f"processingの項目はエージェント環境から編集できない: {path.name}。"
        "処理中の要求を書き換えると、処理しているセッションが扱う要求の内容が途中で変わる",
        next_action="書き換えたい内容はatk wi addで新しい項目として投入し、この項目へはatk wi edit --appendで追記する",
    )
    sys.exit(2)


def reject_frozen_hold_edit(path: pathlib.Path, message: str | None) -> None:
    parsed = _frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    if parsed is None or _constants.normalized_wi_type(parsed[0].get("type")) != WI_TYPE_AWI:
        return
    if is_detailed(parsed[0]):
        reason = "保留中の詳細化済みのAWIは計画の基準に使うため"
    elif parsed[0].get("source") == "implement" and not _is_merge_update(message):
        reason = (
            "implementの保留AWIは実行レビューの収束まで計画の基準に使い、"
            "マージ用への更新（merge_branchとmerge_commitの追加）だけを受け付けるため"
        )
    else:
        return
    _outcome.report_failure(
        f"{reason}、エージェント環境から本文を置き換えられない: {path.name}",
        next_action="実装中に確定した要件の変更は実行レビュー指摘管理表へ記録し、この項目へはatk wi edit --appendで追記する",
    )
    sys.exit(2)


def _is_merge_update(message: str | None) -> bool:
    if message is None:
        return False
    parsed = _frontmatter.parse_frontmatter(message)
    return parsed is not None and bool(parsed[0].get("merge_branch")) and bool(parsed[0].get("merge_commit"))


def _mark_detailed(content: str, filename: str) -> str:
    parsed = _frontmatter.parse_frontmatter(content)
    assert parsed is not None
    data, body = parsed
    if _constants.normalized_wi_type(data.get("type")) != WI_TYPE_AWI:
        raise WebInputError(
            f"詳細化済みの印を付けられるのはAWIだけです: {filename}",
            next_action="--detailを外して保存する",
        )
    data[DETAILED_KEY] = True
    return _frontmatter.serialize_frontmatter(data, body)


def _print_cooldown_result(content: str) -> None:
    """確定本文に保存した期限だけを表示する。"""
    parsed = _frontmatter.parse_frontmatter(content)
    assert parsed is not None
    print(f"    cooldown_until: {parsed[0].get('cooldown_until', 'なし')}")


def cmd_edit(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    if args.dry_run:
        if args.append or args.cooldown_until is not None:
            args.subparser.error("--dry-runは--appendおよび--cooldown-untilと併用できません。")
        if args.filename is None or args.body_file is None:
            args.subparser.error("--dry-runではFILENAMEと--body-fileを指定してください。")
    message = _resolve_edit_message(args)
    if args.cooldown_until is not None:
        if args.filename is None:
            args.subparser.error("--cooldown-untilではFILENAMEを指定してください。")
        if args.append:
            args.subparser.error("--cooldown-untilは--appendと併用できません。")
    if getattr(args, "detail", False):
        if args.append or args.cooldown_until is not None:
            args.subparser.error("--detailは--appendおよび--cooldown-untilと併用できません。")
        if args.filename is None or message is None:
            args.subparser.error("--detailではFILENAMEと--body-fileを指定してください。")
    if args.move_user_comment is not None:
        if args.append:
            args.subparser.error("--move-user-commentは--appendと併用できません。")
        if args.filename is None or message is None:
            args.subparser.error("--move-user-commentではFILENAMEと--body-fileを指定してください。")
    if args.append:
        if args.filename is None or message is None:
            args.subparser.error("--appendではFILENAMEと--body-fileを指定してください。")
        assert message is not None
    elif message is not None and args.filename is None:
        args.subparser.error("--body-fileを指定する場合はFILENAMEも指定してください。")
    if args.append:
        assert message is not None
        cmd_append(args, private_notes, message)
        return
    if message is not None and _reject_agent_user_comment_message(message):
        sys.exit(1)
    editor = None
    if message is None and args.cooldown_until is None:
        editor = os.environ.get("EDITOR")
        if not editor:
            _outcome.report_failure("$EDITORが未設定のため編集できない", next_action="$EDITORを設定するか--body-fileを指定する")
            sys.exit(1)
    inbox_dir = private_notes / WI_STATE_INBOX
    with _wi_sync.repo_lock(private_notes):
        if args.filename is None:
            _wi_sync.pull(private_notes)
            candidates = sorted(
                (record[0] for record in _wi_entries.iter_entries(private_notes, (WI_STATE_INBOX,), None)),
                key=lambda p: p.name,
            )
            if not candidates:
                _outcome.report_failure("inboxが空のため編集対象が無い", next_action="FILENAMEを指定するか項目を投入する")
                sys.exit(2)
            path = candidates[-1]
        else:
            _wi_filenames.validate_filenames_only([args.filename], inbox_dir)
            if not args.dry_run:
                _wi_sync.pull(private_notes)
            paths = resolve_editable_targets([args.filename], private_notes)
            path = paths[0]
        if args.cooldown_until is not None and _wi_entries.entry_state_of(path) == WI_STATE_PROCESSING:
            _outcome.report_failure(
                f"processingの項目は再処理抑制期限を変更できない: {path.name}",
                next_action="処理を終えてinboxまたはholdへ移してから指定する",
            )
            sys.exit(2)
        snapshot = path.read_bytes()
        original = _frontmatter.decode_entry_text(snapshot)
        editing_body = message is not None or args.cooldown_until is None
        if editing_body:
            _reject_agent_processing_edit(path, detail=bool(getattr(args, "detail", False)), message=message)
        normalized_target_repo = resolve_repo_id(args.target_repo) if args.target_repo is not None else None
        verify_target_repo_content(path, original, normalized_target_repo)
    original = _frontmatter.decode_entry_text(snapshot)
    tmp_path: pathlib.Path | None = None
    if message is None and args.cooldown_until is None:
        assert editor is not None
        tmp_path = _wi_cli_input.copy_to_tempfile(snapshot)
        # 時間上限なし: 対話型のエディターであり、ユーザーが終了させる。共有ロックは保持していない。
        editor_result = subprocess.run([editor, str(tmp_path)], check=False)
        if editor_result.returncode != 0:
            _outcome.report_failure(
                f"エディターが終了コード{editor_result.returncode}で終了したため編集を反映しない: {path.name}。"
                f"本文は保存していない。編集内容は{tmp_path}に残した",
                next_action=f"{tmp_path}の内容を確かめ、atk wi edit {path.name} --body-file {tmp_path}で再実行する",
            )
            sys.exit(1)
        edited = tmp_path.read_text(encoding="utf-8")
    elif message is not None:
        try:
            edited = _build_noninteractive_edit_content(path, original, message)
        except WebInputError as error:
            _outcome.report_failure(f"編集を拒否した: {error}", next_action=error.next_action)
            sys.exit(1)
        if args.move_user_comment is not None:
            _move_user_comment(original, edited, path.name, _read_expected_user_comment(args.move_user_comment))
        else:
            edited = _preserve_agent_user_comment(original, edited)
    else:
        edited = original
    if getattr(args, "detail", False):
        try:
            edited = _mark_detailed(edited, path.name)
        except WebInputError as error:
            _outcome.report_failure(f"編集を拒否した: {error}", next_action=error.next_action)
            sys.exit(1)
    if args.cooldown_until is not None:
        try:
            edited = _apply_cooldown_edit(edited, args.cooldown_until, path.name)
        except WebInputError as error:
            _outcome.report_failure(f"編集を拒否した: {error}", next_action=error.next_action)
            sys.exit(1)
    is_uwi = _wi_entries.entry_type_of(path, original) == WI_TYPE_UWI
    from agent_toolkit._atk.wi import merge_verification  # pylint: disable=import-outside-toplevel

    if (
        not args.dry_run
        and (merge := merge_verification.merge_input(edited)) is not None
        and merge != merge_verification.merge_input(original)
    ):
        try:
            merge_verification.check(edited)
        except ActionableError as error:
            _outcome.report_failure(f"編集を拒否した: {error}", next_action=error.next_action)
            sys.exit(1)
    if args.compare_opening_file is not None and not args.dry_run:
        # 保存より前に冒頭を比較し、不一致なら対象WIを書き換えない。
        # 本文が保存済みと同じでも比較の失敗を成功扱いしないよう、差分なしの判定より先に比較する。
        assert message is not None
        assert args.body_file is not None
        try:
            _add.compare_draft_openings(
                [message],
                [str(pathlib.Path(args.body_file).expanduser().resolve())],
                compare_paths=args.compare_opening_file,
                is_uwi=is_uwi,
            )
        except WebInputError as error:
            _outcome.report_failure(f"編集を拒否した: {error.reason}", next_action=error.next_action)
            sys.exit(1)
    if edited == original and not args.dry_run:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        _outcome.report_success("差分なし（変更は無い）")
        if args.cooldown_until is not None:
            _print_cooldown_result(edited)
        return
    if message is None and _reject_agent_user_comment_change(original, edited):
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        sys.exit(1)
    try:
        _require_agent_edit_source(original, edited)
        invalidate_repo_bound_metadata(original, edited)
        if message is not None or tmp_path is not None:
            parsed_edited = _frontmatter.parse_frontmatter(edited)
            entry_type = parsed_edited[0].get("type") if parsed_edited is not None else None
            _add.require_confirmed_cause(edited, entry_type=entry_type if isinstance(entry_type, str) else WI_TYPE_AWI)
            _add.require_cause_analysis_format(
                edited, original=original, entry_type=entry_type if isinstance(entry_type, str) else WI_TYPE_AWI
            )
    except WebInputError as error:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        _outcome.report_failure(f"編集を拒否した: {error}", next_action=error.next_action)
        sys.exit(1)
    if message is not None:
        parsed = _frontmatter.parse_frontmatter(edited)
        if parsed is not None:
            frontmatter, body = parsed
            original_parsed = _frontmatter.parse_frontmatter(original)
            if original_parsed is not None and isinstance(original_parsed[0].get("source"), str):
                original_frontmatter, original_body = original_parsed
                try:
                    _add.require_agent_awi_sections(
                        original_body,
                        original_frontmatter,
                        entry_type=WI_TYPE_AWI,
                        source=None,
                    )
                except WebInputError:
                    pass
                else:
                    try:
                        _add.require_agent_awi_sections(
                            body,
                            frontmatter,
                            entry_type=WI_TYPE_AWI,
                            source=None,
                        )
                    except WebInputError as error:
                        _outcome.report_failure(f"編集を拒否した: {error}", next_action=error.next_action)
                        sys.exit(1)
    if args.dry_run:
        assert message is not None
        assert args.body_file is not None
        try:
            _add.print_draft_openings(
                [message],
                [str(pathlib.Path(args.body_file).expanduser().resolve())],
                compare_paths=args.compare_opening_file,
                is_uwi=is_uwi,
                output_paths=args.opening_output,
            )
        except WebInputError as error:
            _outcome.report_failure(f"編集を拒否した: {error.reason}", next_action=error.next_action)
            sys.exit(1)
        _outcome.report_success(f"編集前の検証が成立した（--dry-runのため保存していない）: {path.name}")
        if not is_uwi:
            _add.print_deferral_candidates([message], [str(pathlib.Path(args.body_file).expanduser().resolve())])
        return
    finalized_content: dict[str, str] = {}
    try:
        edit_entry_content(
            private_notes,
            state=_wi_entries.entry_state_of(path),
            filename=path.name,
            content=edited,
            target_repo=args.target_repo,
            expected_content=original,
            finalized_content=finalized_content,
        )
    except RuntimeError:
        if tmp_path is None:
            _outcome.report_failure(
                f"編集中に他プロセスが対象を変更した: {path.name}。指定した本文は反映していない",
                next_action="同じFILENAMEと--body-fileで再実行する",
            )
        else:
            _outcome.report_failure(
                f"編集中に他プロセスが対象を変更した: {path.name}。編集内容は{tmp_path}に残した",
                next_action=f"再度atk wi edit {path.name}を実行し、{tmp_path}の編集内容を反映し直す",
            )
        sys.exit(1)
    if tmp_path is not None:
        tmp_path.unlink(missing_ok=True)
    _outcome.report_success(f"編集を反映した: {path.name}")
    details = _add.read_saved_entry_details(
        path,
        expected_body=finalized_content["content"],
    )
    if editing_body:
        _add.print_entry_details(details)
        if not is_uwi:
            _add.print_deferral_candidates(
                [message if message is not None else finalized_content["content"]],
                [str(pathlib.Path(args.body_file).expanduser().resolve()) if args.body_file is not None else path.name],
            )
    if args.cooldown_until is not None:
        _print_cooldown_result(finalized_content["content"])


def cmd_record_observation(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    details = _add.read_body_files([args.body_file])[0].strip()
    if (
        not details
        or _headings.contains_h2(details)
        or any(line.strip().startswith("- 再開区分:") for line in details.splitlines())
    ):
        raise WebInputError(
            "再開記録の詳細は空にできず、H2見出しと再開区分の行は指定できません",
            next_action="--body-fileには観測の詳細だけを記入する",
        )
    resume = _constants.OBSERVATION_ONLY_RESUME_LINE if args.resume == "needs-verify" else "- 再開区分: 再実装へ戻す"
    message = f"## {_constants.OBSERVATION_RESUME_HEADING}\n\n{resume}\n{details}\n"
    cmd_append(args, private_notes, message, observation=True)


def cmd_append(args: argparse.Namespace, private_notes: pathlib.Path, message: str, *, observation: bool = False) -> None:
    assert args.filename is not None
    if _reject_agent_user_comment_message(message):
        sys.exit(1)

    inbox_dir = private_notes / WI_STATE_INBOX
    with _wi_sync.repo_lock(private_notes):
        _wi_filenames.validate_filenames_only([args.filename], inbox_dir)
        _wi_sync.pull(private_notes)
        path = resolve_editable_targets([args.filename], private_notes)[0]
        snapshot = path.read_bytes()
        normalized_target_repo = resolve_repo_id(args.target_repo) if args.target_repo is not None else None
        verify_target_repo_content(path, _frontmatter.decode_entry_text(snapshot), normalized_target_repo)

    original = snapshot.decode("utf-8")
    append_to_results = observation or is_agent_environment()
    if append_to_results:
        try:
            before_user_comment, saved_user_comment = _user_comment.split_before_user_comment(original)
        except _user_comment.UserCommentError:
            _outcome.report_failure(
                _user_comment.AGENT_USER_COMMENT_EDIT_ERROR,
                next_action=_USER_COMMENT_BROKEN_NEXT_ACTION.format(name=path.name),
            )
            sys.exit(1)
        result_boundary = (
            b"" if _outside_fence_heading_offset(before_user_comment, "## 処理結果") is not None else "\n\n## 処理結果".encode()
        )
        if _message_starts_result_heading(message):
            result_boundary = b""
        content = (
            before_user_comment.encode("utf-8")
            + result_boundary
            + b"\n\n"
            + message.encode("utf-8")
            + (b"\n\n" + saved_user_comment.encode("utf-8") if saved_user_comment else b"")
        )
    else:
        result_offset = _outside_fence_heading_offset(original, "## 処理結果")
        if result_offset is None:
            content = snapshot + b"\n\n" + message.encode("utf-8")
        else:
            content = snapshot[:result_offset] + message.encode("utf-8") + b"\n\n" + snapshot[result_offset:]
    finalized_content: dict[str, str] = {}
    try:
        append_entry_content(
            private_notes,
            state=_wi_entries.entry_state_of(path),
            filename=path.name,
            content=content,
            target_repo=args.target_repo,
            expected_content=snapshot,
            finalized_content=finalized_content,
        )
    except RuntimeError:
        _outcome.report_failure(
            f"追記中に他プロセスが対象を変更した: {path.name}。指定した本文は反映していない",
            next_action="本文を確認し、同じコマンドで再実行する",
        )
        sys.exit(1)
    _outcome.report_success(f"追記を反映した: {path.name}")
    _add.read_saved_entry_details(
        path,
        expected_body=finalized_content["content"],
    )
    _add.print_deferral_candidates([finalized_content["content"]], [path.name], previous=original)
