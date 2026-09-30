"""agent-toolkitプラグイン配下の`atk wi`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_add.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import headings as _headings
from agent_toolkit._atk.wi import style_diagnostics as _style_diagnostics
from agent_toolkit._atk.wi import user_comment as _user_comment
from agent_toolkit._atk.wi import uwi as _uwi
from agent_toolkit._atk.wi.common import (
    MISSING_DEPENDENCY_NEXT_ACTION,
    WI_STATE_INBOX,
    WI_STATES,
    WI_TYPE_AWI,
    WI_TYPE_UWI,
    WebInputError,
    _collect_message_via_editor,
    _commit_and_push,
    _max_existing_seq,
    _pull,
    _reject_bare_repo_path_override,
    _repo_lock,
    _subdir,
    _validate_filename,
    existing_entry_filenames,
    is_agent_environment,
    is_case_sensitive,
    missing_dependency_warnings,
)
from agent_toolkit._atk.wi.formatters import _shorten_home
from agent_toolkit._atk.wi.repo import resolve_add_target, resolve_head_commit, resolve_repo_id_or_raise
from agent_toolkit._common import body_match as _body_match
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._plan import locations as _plan_file


def _saved_mismatch_next_action(filename: str) -> str:
    """保存後の読み戻しが送信した本文と一致しないときの次の操作を返す。"""
    return f"保存は済んでいる。`atk wi show {filename}`で確認し、`atk wi edit {filename} --body-file <本文ファイル>`で直す"


def _target_repo_error(value: object, error: WebInputError) -> WebInputError:
    """target_repoの解決失敗を、投入の失敗行1本へ包む。"""
    return WebInputError(f"target_repoを解決できません: {value}（{error.reason}）", next_action=error.next_action)


def _read_saved_entry_details(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
    """保存済みエントリを再読込し、本文の一致を検証したうえで照合用のメタデータを返す。

    `expected_body`には書き込み処理が組み立てた確定本文を渡す。保存経路で本文が欠落または改変されて
    いないことを、呼び出し元が終了状態で確定できるようにする。
    """
    saved_body = _frontmatter.decode_entry_text(path.read_bytes())
    parsed = _frontmatter.parse_frontmatter(saved_body)
    if parsed is None:
        raise WebInputError(
            f"保存済みエントリのfrontmatterを読み込めません: {path.name}", next_action=_saved_mismatch_next_action(path.name)
        )
    data, _body = parsed
    raw_dependencies = data.get("depends_on")
    depends_on = [value for value in raw_dependencies if isinstance(value, str)] if isinstance(raw_dependencies, list) else []
    if _body_match.verdict(expected_body, saved_body) != "一致":
        position = _body_match.first_difference(expected_body, saved_body)
        raise WebInputError(
            f"保存本文が送信元本文と一致しない: {path.name}\n"
            f"最初の差異: {position}文字目\n"
            f"送信元本文:\n{expected_body}\n"
            f"保存本文:\n{saved_body}",
            next_action=_saved_mismatch_next_action(path.name),
        )
    return {
        "target_repo": data.get("target_repo"),
        "target_commit": data.get("target_commit"),
        "plan_file": data.get("plan_file"),
        "depends_on": depends_on,
        "source": data.get("source"),
        "extra_frontmatter": {key: value for key, value in data.items() if key not in _RESERVED_FRONTMATTER_KEYS},
    }


def _print_entry_details(details: dict[str, object | None]) -> None:
    """エントリの照合対象を固定順で表示する。"""
    for key in ("target_repo", "target_commit", "plan_file"):
        value = details[key]
        print(f"    {key}: {value if value is not None else 'なし'}")
    depends_on = details["depends_on"]
    rendered_dependencies = (
        "、".join(str(value) for value in depends_on) if isinstance(depends_on, (list, tuple)) and depends_on else "なし"
    )
    print(f"    depends_on: {rendered_dependencies}")
    source = details["source"]
    print(f"    source: {source if source is not None else 'なし'}")
    extra_frontmatter = details["extra_frontmatter"]
    rendered_extra_frontmatter = (
        json.dumps(extra_frontmatter, ensure_ascii=False, default=str)
        if isinstance(extra_frontmatter, dict) and extra_frontmatter
        else "なし"
    )
    print(f"    extra_frontmatter: {rendered_extra_frontmatter}")


def _normalize_dependencies(values: list[str] | None, inbox_dir: pathlib.Path) -> tuple[str, ...]:
    """CLIの依存ファイル名を検証し、`.md`付きの初出順へ正規化する。"""
    return tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in (values or ())))


def _missing_dependency_warnings(
    private_notes: pathlib.Path,
    inbox_dir: pathlib.Path,
    generated: list[str],
    dependencies: tuple[str, ...],
) -> list[str]:
    """投入したエントリの依存先のうち、取り込み先に実在しないものを警告文へ列挙する。

    依存先が実在しないことを理由に投入を拒否せず、警告を返して登録を続ける。
    `--depends-on`はその呼び出しの全エントリへ共通に付くため、エントリと依存先の組ごとに1件返す。
    判定と文面は`--batch`経路と共有し、両経路で同じ条件の参照へ同じ警告が出る状態を保つ。
    """
    if not dependencies:
        return []
    return missing_dependency_warnings(
        [(filename, dependency) for filename in generated for dependency in dependencies],
        resolvable=existing_entry_filenames(private_notes),
        case_sensitive=is_case_sensitive(inbox_dir),
    )


def _parse_leading_frontmatter(message: str) -> tuple[dict[str, object], str]:
    """共有frontmatterパーサーへの薄いラッパー。"""
    parsed = _frontmatter.parse_frontmatter(message)
    if parsed is None:
        return {}, message
    return parsed


def _body_is_effectively_empty(body: str) -> bool:
    """本文が実質空か判定する。

    実質空とは、空文字・空白のみ、または全ての非空行が箇条書きマーカー
    （`-`・`*`・`+`のいずれか単独文字）のみで構成される状態を指す。
    session-review自動投入・ユーザー直接投入いずれの経路でも、
    実効的な指示・観察事象を含まない投入をCLI側で一律検出するための基準とする。
    """
    non_empty_lines = [line.strip() for line in body.split("\n") if line.strip()]
    if not non_empty_lines:
        return True
    return all(line in ("-", "*", "+") for line in non_empty_lines)


_EMPTY_AWI_ERROR = "AWI本文が実質空です"
_EMPTY_AWI_NEXT_ACTION = "本文を記入して再投入する"
_REQUIRED_AWI_HEADINGS: tuple[str, ...] = (
    "反映内容と反映先",
    "適用範囲",
    "実現性",
    "完成条件",
)
"""`source`を持つ通常AWIが必須とするH2見出し。

`agent-toolkit:wi-standards`の`## 通常AWIの本文`が定める必須節をそのまま写す。
規範が必須と定める集合と本検査が判定する集合を1箇所へ集約し、
規範の一部だけを判定する状態が生じないようにする。規範側の必須節を変える改訂では本定数も同じ変更単位で更新する。
"""

_CAUSE_ANALYSIS_HEADING = "原因分析"
"""観測した欠陥を起点とする通常AWIだけが持つH2見出し。

起点が観測した欠陥かは本文から機械判定できないため、必須集合へ加えず、置いた場合の順序と非空だけを判定する。
"""

_AWI_HEADING_ORDER: tuple[str, ...] = (
    "反映内容と反映先",
    _CAUSE_ANALYSIS_HEADING,
    "適用範囲",
    "実現性",
    "完成条件",
    "ユーザー指摘の逐語引用",
)
"""同じ規範が定めるH2の並び順。

`原因分析`と`ユーザー指摘の逐語引用`は条件付きで必須なため順序の判定にだけ用いる。
"""


def parse_entry_message(message: str, *, entry_type: str) -> tuple[dict[str, object], str]:
    """先頭frontmatterと論理本文を返し、種別共通の本文契約を検証する。"""
    frontmatter, body = _parse_leading_frontmatter(message)
    if entry_type == WI_TYPE_AWI and _body_is_effectively_empty(body):
        raise WebInputError(_EMPTY_AWI_ERROR, next_action=_EMPTY_AWI_NEXT_ACTION)
    if entry_type != WI_TYPE_AWI:
        _uwi.reject_reserved_uwi_markup(body)
    return frontmatter, body


def _require_agent_awi_sections(
    body: str,
    frontmatter: dict[str, object],
    *,
    entry_type: str,
    source: str | None,
    plan_file: str | None,
) -> None:
    """`source`を持つ通常AWIが必須H2を全件持ち、規定順序に従うことを検証する。

    不足と順序の不一致は1件の例外へまとめて返す。
    1件ずつ返すと、起草側が本文を書き直して再投入する往復が不足節の件数だけ生じるためである。
    """
    raw_source = frontmatter.get("source", source)
    item_source = raw_source if isinstance(raw_source, str) else source
    if entry_type != WI_TYPE_AWI or plan_file is not None or not item_source:
        return
    sections = _headings.h2_sections(body)
    filled = {name for name, has_body in sections if has_body}
    appeared = [name for name, _has_body in sections if name in _AWI_HEADING_ORDER]
    problems: list[str] = []
    if missing := [name for name in _REQUIRED_AWI_HEADINGS if name not in filled]:
        problems.append(f"非空の必須節がありません: {'、'.join(missing)}")
    if _CAUSE_ANALYSIS_HEADING in appeared and _CAUSE_ANALYSIS_HEADING not in filled:
        problems.append(f"本文が空の節があります: {_CAUSE_ANALYSIS_HEADING}")
    order_indexes = [_AWI_HEADING_ORDER.index(name) for name in appeared]
    if order_indexes != sorted(order_indexes):
        problems.append(f"H2の順序が規定と異なります: {'、'.join(appeared)}")
    if not problems:
        return
    raise WebInputError(
        "。".join(problems)
        + f"。agent-toolkit:wi-standardsの`## 通常AWIの本文`が、H2を{'、'.join(_AWI_HEADING_ORDER)}の順に置くこと、"
        + f"{'、'.join(_REQUIRED_AWI_HEADINGS)}を必須とすること、"
        + f"{_CAUSE_ANALYSIS_HEADING}を置く場合は本文を書くことを定めます。",
        next_action=(
            f"本文のH2を{'、'.join(_AWI_HEADING_ORDER)}の順に並べ、{'、'.join(_REQUIRED_AWI_HEADINGS)}へ本文を書いてから"
            f"再投入する。{_CAUSE_ANALYSIS_HEADING}を置く場合はその節にも本文を書く"
        ),
    )


def _require_agent_source(frontmatter: dict[str, object], source: str | None) -> None:
    """CLIのエージェント投入でsourceが確定していることを検証する。

    環境変数では呼出元を区別できないため、ユーザーの手動投入を受領する
    Web UI等の共有保存関数には本検査を適用しない。
    """
    raw_source = frontmatter.get("source", source)
    item_source = raw_source if isinstance(raw_source, str) else source
    if is_agent_environment() and not item_source:
        raise WebInputError(
            "エージェント環境ではsourceの明示が必須です。",
            next_action="--sourceオプション、または本文先頭のfrontmatterで指定してください。",
        )


def _verify_frontmatter_target_repos(parsed_messages: list[tuple[dict[str, object], str]]) -> None:
    """`target_repo`省略時に、全メッセージのfrontmatterが解決可能な対象リポジトリを持つことを検証する。

    キー欠落・非文字列値・空文字列・解決不能をいずれも原因別の`WebInputError`で拒否し、
    `_repo_lock`取得前に全件を確定する。
    """
    for frontmatter, _body in parsed_messages:
        raw_target_repo = frontmatter.get("target_repo")
        if raw_target_repo is None:
            raise WebInputError(
                "target_repoが指定されていない",
                next_action="`--target-repo`を指定するか、各メッセージのfrontmatterへtarget_repoを記載して再投入する",
            )
        if not isinstance(raw_target_repo, str) or not raw_target_repo.strip():
            raise WebInputError(
                "メッセージfrontmatterのtarget_repoは空でない文字列で指定してください",
                next_action="frontmatterのtarget_repoへローカルworktreeのパスかremote URLを書いて再投入する",
            )
        try:
            resolve_repo_id_or_raise(raw_target_repo)
        except WebInputError as error:
            raise _target_repo_error(raw_target_repo, error) from error


def _verify_plan_target_repos(
    parsed_messages: list[tuple[dict[str, object], str]],
    target_repo: str,
) -> None:
    """計画実装型の全メッセージが投入先リポジトリを実効的に上書きしないことを検証する。"""
    for frontmatter, _body in parsed_messages:
        raw_target_repo = frontmatter.get("target_repo")
        if raw_target_repo is None:
            continue
        if not isinstance(raw_target_repo, str):
            raise WebInputError(
                "plan_file指定時のメッセージfrontmatterのtarget_repoは文字列で指定してください",
                next_action="frontmatterのtarget_repoを削除するか、投入先と同じ値の文字列にして再投入する",
            )
        try:
            item_target_repo = resolve_repo_id_or_raise(raw_target_repo)
        except WebInputError as error:
            raise _target_repo_error(raw_target_repo, error) from error
        if item_target_repo != target_repo:
            raise WebInputError(
                "plan_file指定時はメッセージfrontmatterで対象リポジトリを別の値へ上書きできません。"
                f"投入先={target_repo}、frontmatter={item_target_repo}",
                next_action="frontmatterのtarget_repoを削除するか、投入先と同じ値にして再投入する",
            )


_RESERVED_FRONTMATTER_KEYS = (
    "target_repo",
    "target_commit",
    "type",
    "source",
    "origin_session",
    "origin_locator",
    "scope",
    "question_type",
    "choices",
    "plan_file",
    "queue_schedule",
    "depends_on",
    "cooldown_until",
    "repair_target",
    "repair_kind",
    "reservation",
    "reservation_companion",
    "target_commit_history",
    "submitter_session",
)
"""frontmatter生成で単一箇所（`add_entries`）が専有するキー。

出力frontmatterはCLIが生成するキーを単一の値へ確定させ、入力メッセージのfrontmatterへ
同名キーが含まれていても`frontmatter_data.update()`による辞書更新で
入力値を除外する。このうち`target_repo`・`source`は明示された入力側の値を
CLIオプションより優先して採用するが、`target_repo`は`resolve_repo_id_or_raise`で正規化してから
保存する。
`type`・`scope`・`question_type`・`choices`はCLIオプション
（`--type`・`--scope`・`--question-type`・`--choices`）の値で確定させ入力側の値を採用しない。
`origin_session`・`origin_locator`は旧形式のキーとして予約し、新規投入時に引き継がない。
`submitter_session`は`atk wi add --type=uwi`を実行したセッションの識別子であり、回答済みUWIの通知を
投入元のセッションへ限る`_hooks.uwi_completion`だけが読む。要求の由来の判定には使わず、
本文のfrontmatterからの指定を採用しない。
`target_commit`・`plan_file`・`queue_schedule`・`depends_on`・`cooldown_until`・`repair_target`・`repair_kind`・
`reservation`・`reservation_companion`・`target_commit_history`はユーザーによる直接指定を禁止し、
CLIが管理する識別情報、依存、修復UWI、旧形式の内部metadataとして予約する。
"""


_PROCESS_ROOT_SESSION_PREFIX = "mcp-"
"""`_agents_server.status_file.create_process_root_identity`が生成するプロセス専用の識別子の接頭辞。

この識別子は会話のセッションではないため、回答通知の宛先を表さない。
"""


def _resolve_submitter_session() -> str | None:
    """UWIを投入したセッションの識別子を環境から解決する。

    委譲先は委譲元のセッションを返すため、委譲先が投入したUWIも委譲元のセッションへ通知される。
    解決できない場合と、会話へ対応しないプロセス専用の識別子だった場合は`None`を返す。
    """
    session_id = _plan_file.resolve_owner_session_id()
    if session_id is None or session_id.startswith(_PROCESS_ROOT_SESSION_PREFIX):
        return None
    return session_id


def _add_entries_locked(
    private_notes: pathlib.Path,
    *,
    parsed_messages: list[tuple[dict[str, object], str]],
    target_repo: str | None,
    source: str | None,
    now: datetime.datetime,
    entry_type: str,
    scope: str | None,
    question_type: str | None,
    choices: str | None,
    target_commit: str | None = None,
    plan_file: str | None = None,
    repair_targets: list[str | None] | None = None,
    repair_kinds: list[str | None] | None = None,
    depends_on: tuple[str, ...] = (),
    submitter_session: str | None = None,
) -> list[tuple[str, str]]:
    """取得済みrepoロック内でエントリを書き込み、生成ファイル名と確定本文の組を返す。

    `target_repo`が`None`の場合は各メッセージのfrontmatterの`target_repo`だけを採用する
    （呼び出し元の`add_entries`が全件の存在と解決可否を検証済みとする）。
    """
    effective_repair_targets: list[str | None] = [None for _ in parsed_messages] if repair_targets is None else repair_targets
    effective_repair_kinds: list[str | None] = [None for _ in parsed_messages] if repair_kinds is None else repair_kinds
    if len(effective_repair_targets) != len(parsed_messages):
        raise ValueError("repair_targetsの件数がメッセージ件数と一致しません")
    if len(effective_repair_kinds) != len(parsed_messages):
        raise ValueError("repair_kindsの件数がメッセージ件数と一致しません")
    if any(
        (repair_target is None) != (repair_kind is None)
        for repair_target, repair_kind in zip(effective_repair_targets, effective_repair_kinds, strict=True)
    ):
        raise ValueError("repair_targetとrepair_kindは同時に指定してください")
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    inbox_dir = _subdir(private_notes, WI_STATE_INBOX)
    counter = _max_existing_seq(private_notes, timestamp) + 1
    generated: list[tuple[str, str]] = []
    for (frontmatter, body), repair_target, repair_kind in zip(
        parsed_messages,
        effective_repair_targets,
        effective_repair_kinds,
        strict=True,
    ):
        raw_target_repo = frontmatter.get("target_repo", target_repo)
        raw_source = frontmatter.get("source", source)
        try:
            item_target_repo = resolve_repo_id_or_raise(raw_target_repo) if isinstance(raw_target_repo, str) else target_repo
        except WebInputError as error:
            raise _target_repo_error(raw_target_repo, error) from error
        item_source = raw_source if isinstance(raw_source, str) else source
        filename = f"{timestamp}-{counter:03d}.md"
        while any((private_notes / state / filename).exists() for state in WI_STATES):
            counter += 1
            filename = f"{timestamp}-{counter:03d}.md"
        frontmatter_data: dict[str, object] = {"target_repo": item_target_repo, "type": entry_type}
        if target_commit is not None and item_target_repo == target_repo:
            frontmatter_data["target_commit"] = target_commit
        if item_source:
            frontmatter_data["source"] = item_source
        frontmatter_data.update((key, value) for key, value in frontmatter.items() if key not in _RESERVED_FRONTMATTER_KEYS)
        if entry_type != WI_TYPE_AWI:
            if scope:
                frontmatter_data["scope"] = scope
            frontmatter_data["question_type"] = question_type
            if choices:
                frontmatter_data["choices"] = choices
            if repair_target is not None:
                frontmatter_data["repair_target"] = repair_target
                frontmatter_data["repair_kind"] = repair_kind
            if submitter_session is not None:
                frontmatter_data["submitter_session"] = submitter_session
            logical_body = f"\n{_uwi.QUESTION_HEADING}\n\n{body}\n\n{_uwi.ANSWER_HEADING}\n\n{_uwi.ANSWER_MARKER}\n"
        else:
            logical_body = body if body.startswith("\n") else f"\n{body.rstrip()}\n"
            if plan_file is not None:
                frontmatter_data["plan_file"] = plan_file
            if depends_on:
                frontmatter_data["depends_on"] = list(depends_on)
        content = _frontmatter.normalize_newlines(_frontmatter.serialize_frontmatter(frontmatter_data, logical_body))
        _frontmatter.write_entry_text(inbox_dir / filename, content)
        if entry_type != WI_TYPE_AWI:
            _uwi.warn_question_quality(filename, body, question_type)
        generated.append((filename, content))
        counter += 1
    return generated


def add_entries(
    private_notes: pathlib.Path,
    *,
    messages: list[str],
    target_repo: str | None,
    source: str | None,
    now: datetime.datetime,
    entry_type: str = WI_TYPE_AWI,
    scope: str | None = None,
    question_type: str | None = None,
    choices: str | None = None,
    target_commit: str | None = None,
    plan_file: str | None = None,
    depends_on: tuple[str, ...] = (),
    lock_timeout: float = -1,
    saved_details: dict[str, dict[str, object | None]] | None = None,
    skip_remote_sync: bool = False,
    submitter_session: str | None = None,
) -> list[str]:
    """平引数でメッセージキューのエントリを追加し、生成ファイル名を返す。

    frontmatterの予約キー（`_RESERVED_FRONTMATTER_KEYS`）以外のキーは入力順で出力frontmatterへ引き継ぐ。
    UWI種別では、本文がツール側で自動付与する見出し・回答欄マーカーを含む場合に
    `_uwi.reject_reserved_uwi_markup`が`WebInputError`を送出する（CLIとWeb UIの共通経路）。
    `target_repo`を省略（`None`）した場合は、各メッセージのfrontmatterの`target_repo`を必須とし、
    `_repo_lock`取得前に全件の型・非空・解決可否を検証する。
    `submitter_session`はUWI種別のfrontmatterへだけ保存する。
    """
    parsed_messages, normalized_target_repo, stored_plan_file = _validate_add_entries(
        private_notes,
        messages=messages,
        target_repo=target_repo,
        entry_type=entry_type,
        question_type=question_type,
        choices=choices,
        target_commit=target_commit,
        plan_file=plan_file,
        source=source,
    )
    with _repo_lock(private_notes, timeout=lock_timeout):
        if not skip_remote_sync:
            _pull(private_notes)
        written = _add_entries_locked(
            private_notes,
            parsed_messages=parsed_messages,
            target_repo=normalized_target_repo,
            source=source,
            now=now,
            entry_type=entry_type,
            scope=scope,
            question_type=question_type,
            choices=choices,
            target_commit=target_commit,
            plan_file=stored_plan_file,
            depends_on=depends_on,
            submitter_session=submitter_session,
        )
        generated = [filename for filename, _content in written]
        count = len(generated)
        _commit_and_push(
            private_notes,
            f"chore: add {count} {entry_type} {'item' if count == 1 else 'items'}",
            [WI_STATE_INBOX],
            skip_push=skip_remote_sync,
        )
        if saved_details is not None:
            saved_details.update(
                (
                    filename,
                    _read_saved_entry_details(private_notes / WI_STATE_INBOX / filename, expected_body=content),
                )
                for filename, content in written
            )
    return generated


def _validate_add_entries(
    private_notes: pathlib.Path,
    *,
    messages: list[str],
    target_repo: str | None,
    entry_type: str,
    question_type: str | None,
    choices: str | None,
    target_commit: str | None,
    plan_file: str | None,
    source: str | None = None,
) -> tuple[list[tuple[dict[str, object], str]], str | None, str | None]:
    """保存前の入力検証を行い、正規化済みの値を返す。"""
    if not messages:
        raise WebInputError("messagesには1件以上を指定してください", next_action="本文を1件以上指定して再投入する")
    if target_commit is not None and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target_commit) is None:
        raise WebInputError(
            "target_commitは解決済みの40桁または64桁OIDで指定してください",
            next_action="対象worktreeで`git rev-parse HEAD`を実行して得た完全なOIDを指定して再投入する",
        )
    normalized_target_repo: str | None = None
    if target_repo is not None:
        try:
            normalized_target_repo = resolve_repo_id_or_raise(target_repo)
        except WebInputError as error:
            raise _target_repo_error(target_repo, error) from error
    plan_path: pathlib.Path | None = None
    if plan_file is not None:
        if entry_type != WI_TYPE_AWI:
            raise WebInputError("plan_fileはawi種別でのみ指定できます", next_action="plan_fileを外すか、awi種別で投入し直す")
        try:
            stored_plan_file = _plan_file.normalize_plan_file(plan_file, private_notes=private_notes)
            plan_path = _plan_file.require_saved_plan_file(stored_plan_file, private_notes=private_notes)
        except ValueError as error:
            # 保存前の作業root直下の計画は保存を、保存済みの計画は可搬表記への指定し直しを案内する。
            raise WebInputError(
                f"plan_fileを解決できません: {plan_file}（{error}）",
                next_action=(
                    error.next_action
                    if isinstance(error, _next_action.ActionableError)
                    else "作業root直下の計画は`atk plans commit <ファイル名>`で保存してから、保存済みの計画は"
                    "`$(atk config get private_notes)/plans/yyyy/MM/<ファイル名>`で指定し直す"
                ),
            ) from error
        except OSError as error:
            raise WebInputError(
                f"plan_fileを検証できません: {plan_file}",
                next_action="plan_fileが指すファイルの存在と読み取り権限を確認してから再投入する",
            ) from error
    else:
        stored_plan_file = None
    if entry_type != WI_TYPE_AWI and question_type not in {"choice", "yes-no", "free-form"}:
        raise WebInputError(
            f"question_typeが不正です: {question_type}",
            next_action="question_typeへchoice・yes-no・free-formのいずれかを指定する（CLIでは`--question-type`）",
        )
    parsed_messages = [parse_entry_message(message, entry_type=entry_type) for message in messages]
    for frontmatter, body in parsed_messages:
        _require_agent_awi_sections(
            body,
            frontmatter,
            entry_type=entry_type,
            source=source,
            plan_file=plan_file,
        )
    if normalized_target_repo is None:
        _verify_frontmatter_target_repos(parsed_messages)
    if plan_path is not None:
        if normalized_target_repo is None:
            raise WebInputError(
                "plan_file指定時はtarget_repoを指定してください",
                next_action="target_repoへ計画の対象リポジトリを指定して再投入する",
            )
        _verify_plan_target_repos(parsed_messages, normalized_target_repo)
    if entry_type != WI_TYPE_AWI and question_type == "choice" and not choices:
        raise WebInputError(
            "choice形式にはchoicesが必要です", next_action="`--choices`で選択肢を指定するか、別のquestion_typeを選ぶ"
        )
    return parsed_messages, normalized_target_repo, stored_plan_file


def read_body_files(paths: list[str]) -> list[str]:
    """`--body-file`で指定された各パスの内容を本文として読む。

    シェルの引用規則を経由せずに引用符・改行を含む長文を渡す経路として用いる。
    読み込みに失敗したパスは`WebInputError`を送出し、部分的に読み込んだ内容を投入へ進めない。
    """
    bodies: list[str] = []
    for raw in paths:
        path = pathlib.Path(raw).expanduser()
        try:
            bodies.append(_frontmatter.normalize_newlines(path.read_text(encoding="utf-8")))
        except OSError as error:
            raise WebInputError(
                f"--body-fileの読み込みに失敗しました: {raw}（{error}）",
                next_action="--body-fileのパスと読み取り権限を確認して再実行する",
            ) from error
        except UnicodeDecodeError as error:
            raise WebInputError(
                f"--body-fileをUTF-8として読めません: {raw}",
                next_action="本文ファイルをUTF-8で保存し直して再実行する",
            ) from error
    return bodies


def _cmd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """addサブコマンド: メッセージをinboxへ投入してcommit・push。

    対象リポジトリは常にカレントディレクトリから解決する。ただし`mq add`直後のトークンが実在
    ディレクトリの場合は旧REPO_PATH位置引数形式の呼び出しとみなし、`atk.py`側の事前抽出で
    その引数をREPO_PATHとして扱う（互換維持、抽出結果は`args.repo_path_override`で受け取る）。
    各メッセージ先頭がYAML frontmatter形式の場合は`target_repo`・`source`をCLIオプションより優先する。
    `--target-repo`指定時は、レガシーREPO_PATH位置引数が無くfrontmatterにも`target_repo`が
    無い場合のfallback値として使う。
    エディター経由の本文確定後に対象worktreeのHEADを取得してから`_pull`を実行する順序とし、
    エディター起動前のブロッキング待ち（他端末の投入分を反映するremote同期）を無くしてUXを改善する。
    remote同期失敗時はエディターで確定済みの本文をstderrへ再表示してから終了し、入力内容の消失を防ぐ。
    各メッセージの本文が実質空（`_body_is_effectively_empty`）の場合は`_repo_lock`取得前に拒否する。
    計画実装型の分類は`--plan-file`の指定だけで確定する。
    `--body-file`を指定した場合はそのファイルの内容を本文として扱う。
    シェルの引用規則を経由せずに引用符・改行を含む長文を渡す経路であり、複数回指定で複数件を投入する。
    `--depends-on`が指す依存先が取り込み先に実在しない場合は、投入を拒否せず警告をstderrへ出力する。
    UWIでは投入したセッションの識別子を`submitter_session`へ保存する（`_resolve_submitter_session`）。
    """
    body_files = getattr(args, "body_file", None)
    if body_files:
        try:
            messages = read_body_files(body_files)
        except WebInputError as error:
            _outcome.report_failure(f"投入を拒否した: {error.reason}", next_action=error.next_action)
            sys.exit(1)
    else:
        messages = []
    repo_path_override = args.repo_path_override
    _reject_bare_repo_path_override(repo_path_override, messages, args.subparser)
    target_value = repo_path_override if repo_path_override is not None else args.target_repo
    target_repo, local_worktree = resolve_add_target(target_value)
    collected_via_editor = not messages
    if not messages:
        message = _collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    validation_errors: list[WebInputError] = []
    style_warnings: list[str] = []
    for message in messages:
        try:
            if is_agent_environment() and _user_comment.has_reserved_heading(message):
                raise WebInputError(
                    _user_comment.AGENT_USER_COMMENT_ADD_ERROR,
                    next_action=_user_comment.AGENT_USER_COMMENT_ADD_NEXT_ACTION,
                )
            frontmatter, body = parse_entry_message(message, entry_type=args.type)
            _require_agent_source(frontmatter, args.source)
            style_warnings.extend(_style_diagnostics.warnings_for_body(body))
            try:
                _require_agent_awi_sections(
                    body,
                    frontmatter,
                    entry_type=args.type,
                    source=args.source,
                    plan_file=args.plan_file,
                )
            except WebInputError as error:
                validation_errors.append(error)
        except WebInputError as error:
            if error.reason == _EMPTY_AWI_ERROR:
                preview = message.strip().splitlines()[0] if message.strip() else "(空文字列)"
                validation_errors.append(
                    WebInputError(
                        "投入を拒否した: 本文が実質空である"
                        "（空文字・空白のみ・箇条書きマーカー単独文字のいずれか）。"
                        f"該当メッセージの先頭: {preview}",
                        next_action=_EMPTY_AWI_NEXT_ACTION,
                    )
                )
            else:
                validation_errors.append(error)
    if style_warnings:
        _outcome.report_warning(
            "\n警告: ".join(style_warnings),
            next_action=(
                "対応不要（投入は続行する）。直す場合は投入前に本文を書き直すか、"
                "投入後に`atk wi edit <ファイル名> --body-file <本文ファイル>`で置き換える"
            ),
        )
    if validation_errors:
        # 全メッセージの違反を1回で返し、次の操作は重複を除いて同じ順に並べる。
        next_actions = dict.fromkeys(error.next_action for error in validation_errors)
        _outcome.report_failure(
            "投入を拒否した: " + "\n".join(error.reason for error in validation_errors),
            next_action="\n".join(next_actions),
        )
        sys.exit(1)
    if args.type == WI_TYPE_UWI and args.depends_on:
        _outcome.report_failure(
            "投入を拒否した: --depends-onは--type=awiでのみ指定できる",
            next_action="--depends-onを外すか、--type=awiで投入し直す",
        )
        sys.exit(1)
    try:
        target_commit = resolve_head_commit(local_worktree) if local_worktree is not None else None
    except SystemExit:
        if collected_via_editor:
            _outcome.report_failure(
                "HEADコミットの取得に失敗した。確定済みの本文を以下に再表示する",
                next_action="再表示した本文を保存してから再投入する",
            )
            for message in messages:
                print("---", file=sys.stderr)
                print(message, file=sys.stderr)
        raise
    dependency_dir = private_notes / WI_STATE_INBOX
    canonical_dependencies = _normalize_dependencies(args.depends_on, dependency_dir)
    saved_details: dict[str, dict[str, object | None]] = {}
    try:
        if args.dry_run:
            _validate_add_entries(
                private_notes,
                messages=messages,
                target_repo=target_repo,
                entry_type=args.type,
                question_type=args.question_type,
                choices=args.choices,
                target_commit=target_commit,
                plan_file=args.plan_file,
                source=args.source,
            )
            _outcome.report_success("投入前の検証が成立した（--dry-runのため保存していない）")
            return
        generated = add_entries(
            private_notes,
            messages=messages,
            target_repo=target_repo,
            source=args.source,
            now=now,
            entry_type=args.type,
            scope=args.scope,
            question_type=args.question_type,
            choices=args.choices,
            target_commit=target_commit,
            plan_file=args.plan_file,
            depends_on=canonical_dependencies,
            saved_details=saved_details,
            submitter_session=_resolve_submitter_session() if args.type == WI_TYPE_UWI else None,
        )
    except WebInputError as error:
        _outcome.report_failure(f"投入を拒否した: {error.reason}", next_action=error.next_action)
        sys.exit(1)
    except subprocess.CalledProcessError:
        _outcome.report_failure(
            "remote同期に失敗した。確定済みの本文を以下に再表示する",
            next_action=(f"再表示した本文を保存し、`git -C {private_notes} status`で同期状態を確認してから再投入する"),
        )
        for message in messages:
            print("---", file=sys.stderr)
            print(message, file=sys.stderr)
        sys.exit(1)
    count = len(generated)
    inbox_dir = _subdir(private_notes, WI_STATE_INBOX)
    for warning in _missing_dependency_warnings(private_notes, inbox_dir, generated, canonical_dependencies):
        _outcome.report_warning(warning, next_action=MISSING_DEPENDENCY_NEXT_ACTION)
    _outcome.report_success(f"{count}件をinboxへ投入した")
    for filename in generated:
        print(f"  {_shorten_home(inbox_dir / filename, home)}")
        _print_entry_details(saved_details[filename])
