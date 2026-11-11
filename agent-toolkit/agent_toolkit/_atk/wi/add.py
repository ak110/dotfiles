"""WI投入のCLI入口と保存処理。"""

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.wi import cli_input as _wi_cli_input
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import user_comment as _user_comment
from agent_toolkit._atk.wi import uwi as _uwi
from agent_toolkit._atk.wi.add_reports import compare_draft_openings, has_synced_run_report, print_draft_openings
from agent_toolkit._atk.wi.add_validation import (
    _EMPTY_AWI_ERROR,
    _EMPTY_AWI_NEXT_ACTION,
    _RESERVED_FRONTMATTER_KEYS,
    AWI_CAUSE_TABLE_ROWS,
    _parse_leading_frontmatter,
    _require_agent_source,
    _target_repo_error,
    _validate_add_entries,
    body_is_effectively_empty,
    normalize_cooldown_until,
    parse_entry_message,
    require_agent_awi_sections,
    require_cause_analysis_format,
    require_confirmed_cause,
)
from agent_toolkit._atk.wi.constants import WI_STATE_INBOX, WI_TYPE_AWI, WI_TYPE_UWI
from agent_toolkit._atk.wi.filenames import MISSING_DEPENDENCY_NEXT_ACTION
from agent_toolkit._atk.wi.formatters import shorten_home
from agent_toolkit._atk.wi.repo import resolve_add_target, resolve_head_commit, resolve_repo_id_or_raise
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import body_match as _body_match
from agent_toolkit._plan import owner_records as _owner_records


def run_report_metadata(data: dict[str, object]) -> dict[str, object]:
    """報告区分を明示したUWIへ、起動元が発行した実行対応を設定する。"""
    if data.get("run_skill_report") == "true" and (run_id := os.environ.get("AGENT_TOOLKIT_RUN_SKILL_ID")):
        return {**data, "run_skill_run_id": run_id}
    return data


def _saved_mismatch_next_action(filename: str) -> str:
    """保存後の読み戻しが送信した本文と一致しないときの次の操作を返す。"""
    return f"保存は済んでいる。`atk wi show {filename}`で確認し、`atk wi edit {filename} --body-file <本文ファイル>`で直す"


def read_saved_entry_details(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
    """保存済みエントリを再読込し、本文の一致を検証したうえで一致確認の対象を示すメタデータを返す。

    `expected_body`には書き込み処理が組み立てた確定本文を渡す。保存の処理中に本文が欠落または改変されて
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
        "depends_on": depends_on,
        "cooldown_until": data.get("cooldown_until"),
        "source": data.get("source"),
        "extra_frontmatter": {key: value for key, value in data.items() if key not in _RESERVED_FRONTMATTER_KEYS},
    }


def print_entry_details(details: dict[str, object | None]) -> None:
    """一致を確認したエントリの項目を決まった順で表示する。"""
    for key in ("target_repo", "target_commit"):
        value = details[key]
        print(f"    {key}: {value if value is not None else 'なし'}")
    depends_on = details["depends_on"]
    rendered_dependencies = (
        "、".join(str(value) for value in depends_on) if isinstance(depends_on, (list, tuple)) and depends_on else "なし"
    )
    print(f"    depends_on: {rendered_dependencies}")
    cooldown_until = details.get("cooldown_until")
    print(f"    cooldown_until: {cooldown_until if cooldown_until is not None else 'なし'}")
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
    return tuple(dict.fromkeys(_wi_filenames.validate_filename(value, inbox_dir).name for value in (values or ())))


def _missing_dependency_warnings(
    private_notes: pathlib.Path,
    inbox_dir: pathlib.Path,
    generated: list[str],
    dependencies: tuple[str, ...],
) -> list[str]:
    """投入したエントリの依存先のうち、取り込み先に実在しないものを警告文へ列挙する。

    依存先が実在しないことを理由に投入を拒否せず、警告を返して登録を続ける。
    `--depends-on`はその呼び出しの全エントリへ共通に付くため、エントリと依存先の組ごとに1件返す。
    判定と文面は`--batch`指定時と共有し、どちらの場合も同じ条件の参照へ同じ警告が出る状態を保つ。
    """
    if not dependencies:
        return []
    return _wi_filenames.missing_dependency_warnings(
        [(filename, dependency) for filename in generated for dependency in dependencies],
        resolvable=_wi_filenames.existing_entry_filenames(private_notes),
        case_sensitive=_wi_filenames.is_case_sensitive(inbox_dir),
    )


def _resolve_submitter_session() -> str | None:
    """UWIを投入したセッションの識別子を環境から解決する。

    委譲先は委譲元のセッションを返すため、委譲先が投入したUWIも委譲元のセッションへ通知される。
    解決できない場合と、会話へ対応しないプロセス専用の識別子だった場合は`None`を返す。
    計画ファイルを所有する会話の判定と同じ処理を使う。
    """
    return _owner_records.resolve_conversation_session_id()


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
    repair_targets: list[str | None] | None = None,
    repair_kinds: list[str | None] | None = None,
    depends_on: tuple[str, ...] = (),
    cooldown_until: str | None = None,
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
    counter = _wi_filenames.max_existing_seq(private_notes, timestamp) + 1
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
            item_target_repo = (
                resolve_repo_id_or_raise(raw_target_repo, warn_non_origin=True)
                if isinstance(raw_target_repo, str)
                else target_repo
            )
        except WebInputError as error:
            raise _target_repo_error(raw_target_repo, error) from error
        item_source = raw_source if isinstance(raw_source, str) else source
        filename = f"{timestamp}-{counter:03d}.md"
        while filename in _wi_filenames.existing_entry_filenames(private_notes):
            counter += 1
            filename = f"{timestamp}-{counter:03d}.md"
        frontmatter_data: dict[str, object] = {"target_repo": item_target_repo, "type": entry_type, "state": WI_STATE_INBOX}
        if target_commit is not None and item_target_repo == target_repo:
            frontmatter_data["target_commit"] = target_commit
        if item_source:
            frontmatter_data["source"] = item_source
        frontmatter_data.update((key, value) for key, value in frontmatter.items() if key not in _RESERVED_FRONTMATTER_KEYS)
        if entry_type == WI_TYPE_UWI:
            frontmatter_data = run_report_metadata(frontmatter_data)
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
            if depends_on:
                frontmatter_data["depends_on"] = list(depends_on)
            if cooldown_until is not None:
                frontmatter_data["cooldown_until"] = cooldown_until
        content = _frontmatter.normalize_newlines(_frontmatter.serialize_frontmatter(frontmatter_data, logical_body))
        path = _wi_entries.entry_path(private_notes, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        _frontmatter.write_entry_text(path, content)
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
    depends_on: tuple[str, ...] = (),
    cooldown_until: str | None = None,
    storage: _wi_sync.Storage = _wi_sync.DEFAULT_STORAGE,
    saved_details: dict[str, dict[str, object | None]] | None = None,
    submitter_session: str | None = None,
) -> list[str]:
    """個別の引数を受け取ってキューへエントリを追加し、生成ファイル名を返す。

    frontmatterの予約キー（`_RESERVED_FRONTMATTER_KEYS`）以外のキーは入力順で出力frontmatterへ引き継ぐ。
    UWI種別では、`parse_entry_message`が保存形式の未回答外枠を取り除いた本文を保存する。
    残った予約見出し・回答欄マーカーは`WebInputError`で拒否する（CLIとWeb UIの共通処理）。
    `target_repo`を省略（`None`）した場合は、各メッセージのfrontmatterの`target_repo`を必須とし、
    `_wi_sync.repo_lock`取得前に全件の型・非空・解決可否を検証する。
    `submitter_session`はUWI種別のfrontmatterへだけ保存する。
    """
    parsed_messages, normalized_target_repo, normalized_cooldown = _validate_add_entries(
        messages=messages,
        target_repo=target_repo,
        entry_type=entry_type,
        question_type=question_type,
        choices=choices,
        target_commit=target_commit,
        source=source,
        cooldown_until=cooldown_until,
    )
    with storage.mutation(private_notes) as mutation:
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
            depends_on=depends_on,
            cooldown_until=normalized_cooldown,
            submitter_session=submitter_session,
        )
        generated = [filename for filename, _content in written]
        count = len(generated)
        mutation.commit(
            f"chore: add {count} {entry_type} {'item' if count == 1 else 'items'}",
            [str(_wi_entries.entry_path(private_notes, filename).relative_to(private_notes)) for filename in generated],
        )
        if saved_details is not None:
            saved_details.update(
                (
                    filename,
                    read_saved_entry_details(_wi_entries.entry_path(private_notes, filename), expected_body=content),
                )
                for filename, content in written
            )
    return generated


def read_body_files(paths: list[str]) -> list[str]:
    """`--body-file`で指定された各パスの内容を本文として読む。

    引用符・改行を含む長文を、シェルの引用規則による解釈を介さずに渡すために使う。
    読み込みに失敗したパスは`WebInputError`を送出し、部分的に読み込んだ内容を投入へ進めない。
    """
    bodies: list[str] = []
    for raw in paths:
        path = pathlib.Path(raw).expanduser()
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                bodies.append(stream.read())
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


def cmd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """addサブコマンド: メッセージをinboxへ投入してcommit・push。

    対象リポジトリは常にカレントディレクトリから解決する。ただし`atk wi add`直後のトークンが実在
    ディレクトリの場合は旧REPO_PATH位置引数形式の呼び出しとみなし、`atk.py`側の事前抽出で
    その引数をREPO_PATHとして扱う（互換維持、抽出結果は`args.repo_path_override`で受け取る）。
    各メッセージ先頭がYAML frontmatter形式の場合は`target_repo`・`source`をCLIオプションより優先する。
    `--target-repo`指定時は、レガシーREPO_PATH位置引数が無くfrontmatterにも`target_repo`が
    無い場合のfallback値として使う。
    エディター経由の本文確定後に対象worktreeのHEADを取得してから`_wi_sync.pull`を実行する順序とし、
    エディターの起動前に、他端末の投入分を反映するremote同期の完了を投入者が待たずに済むようにする。
    remote同期失敗時はエディターで確定済みの本文をstderrへ再表示してから終了し、入力内容の消失を防ぐ。
    各メッセージの本文が実質空（`body_is_effectively_empty`）の場合は`_wi_sync.repo_lock`取得前に拒否する。
    `--body-file`を指定した場合はそのファイルの内容を本文として扱う。
    引用符・改行を含む長文を、シェルの引用規則による解釈を介さずに渡す。複数回指定すれば複数件を投入できる。
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
    _wi_cli_input.reject_bare_repo_path_override(repo_path_override, messages, args.subparser)
    target_value = repo_path_override if repo_path_override is not None else args.target_repo
    target_repo, local_worktree = resolve_add_target(target_value)
    collected_via_editor = not messages
    if not messages:
        message = _wi_cli_input.collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    validation_errors: list[WebInputError] = []
    for message in messages:
        try:
            if is_agent_environment() and _user_comment.has_reserved_heading(message):
                raise WebInputError(
                    _user_comment.AGENT_USER_COMMENT_ADD_ERROR,
                    next_action=_user_comment.AGENT_USER_COMMENT_ADD_NEXT_ACTION,
                )
            frontmatter, body = parse_entry_message(message, entry_type=args.type)
            _require_agent_source(frontmatter, args.source)
            try:
                require_agent_awi_sections(
                    body,
                    frontmatter,
                    entry_type=args.type,
                    source=args.source,
                )
            except WebInputError as error:
                validation_errors.append(error)
            try:
                require_confirmed_cause(body, entry_type=args.type)
                require_cause_analysis_format(body, entry_type=args.type)
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
    choices = ",".join(args.choices) if args.choices is not None else None
    saved_details: dict[str, dict[str, object | None]] = {}
    sources = [str(pathlib.Path(path).expanduser().resolve()) for path in body_files] if body_files else ["エディター入力"]
    try:
        if args.dry_run:
            _validate_add_entries(
                messages=messages,
                target_repo=target_repo,
                entry_type=args.type,
                question_type=args.question_type,
                choices=choices,
                target_commit=target_commit,
                source=args.source,
                cooldown_until=getattr(args, "cooldown_until", None),
            )
            print_draft_openings(messages, sources, compare_paths=args.compare_opening_file)
            _outcome.report_success("投入前の検証が成立した（--dry-runのため保存していない）")
            return
        if args.compare_opening_file is not None:
            # 保存より前に全原稿の冒頭を比較し、不一致なら1件も保存しない。
            compare_draft_openings(messages, sources, compare_paths=args.compare_opening_file)
        generated = add_entries(
            private_notes,
            messages=messages,
            target_repo=target_repo,
            source=args.source,
            now=now,
            entry_type=args.type,
            scope=args.scope,
            question_type=args.question_type,
            choices=choices,
            target_commit=target_commit,
            depends_on=canonical_dependencies,
            cooldown_until=getattr(args, "cooldown_until", None),
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
    inbox_dir = private_notes
    for warning in _missing_dependency_warnings(private_notes, inbox_dir, generated, canonical_dependencies):
        _outcome.report_warning(warning, next_action=MISSING_DEPENDENCY_NEXT_ACTION)
    _outcome.report_success(f"{count}件をinboxへ投入した")
    for filename in generated:
        print(f"  {shorten_home(_wi_entries.entry_path(private_notes, filename), home)}")
        print_entry_details(saved_details[filename])


__all__ = [
    "AWI_CAUSE_TABLE_ROWS",
    "_parse_leading_frontmatter",
    "body_is_effectively_empty",
    "has_synced_run_report",
    "normalize_cooldown_until",
]
