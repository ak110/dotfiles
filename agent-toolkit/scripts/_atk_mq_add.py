"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

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

import _atk_mq_frontmatter as _frontmatter
import _atk_mq_schedule as _schedule
import _atk_mq_tbd as _tbd
import markdown_it
from _atk_mq_common import (
    MQ_STATE_INBOX,
    MQ_STATE_PROCESSING,
    MQ_STATES,
    MQ_TYPE_FEEDBACK,
    WebInputError,
    _collect_message_via_editor,
    _commit_and_push,
    _count_feedback,
    _max_existing_seq,
    _pull,
    _reject_bare_repo_path_override,
    _repo_lock,
    _resolve_repo_path_override,
    _subdir,
)
from _atk_mq_formatters import _shorten_home
from _atk_mq_repo import _resolve_repo_id, resolve_add_target, resolve_head_commit


def _read_saved_entry_details(path: pathlib.Path) -> dict[str, object | None]:
    """保存済みエントリを再読込し、利用者が照合するメタデータを返す。"""
    parsed = _frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    if parsed is None:
        raise WebInputError(f"保存済みエントリのfrontmatterを読み込めません: {path.name}")
    data, _body = parsed
    schedule_mapping = data.get(_schedule.SCHEDULE_KEY)
    dependency = schedule_mapping.get("dependency") if isinstance(schedule_mapping, dict) else None
    return {
        "target_repo": data.get("target_repo"),
        "target_commit": data.get("target_commit"),
        "plan_file": data.get("plan_file"),
        "dependency": dependency,
    }


def _print_entry_details(details: dict[str, object | None]) -> None:
    """エントリの照合対象を固定順で表示する。"""
    for key in ("target_repo", "target_commit", "plan_file"):
        value = details[key]
        print(f"    {key}: {value if value is not None else 'なし'}")
    dependency = details["dependency"]
    rendered_dependency = "なし" if dependency is None else json.dumps(dependency, ensure_ascii=False, sort_keys=True)
    print(f"    dependency: {rendered_dependency}")


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


_EMPTY_FEEDBACK_ERROR = "feedback本文が実質空です"
_PLAN_BASE_COMMIT_LINE_RE = re.compile(
    r"^\s*-\s*(?:ベースコミット|基準コミット):\s*`([0-9a-fA-F]+)`[^\n]*$",
    re.MULTILINE,
)


def parse_entry_message(message: str, *, entry_type: str) -> tuple[dict[str, object], str]:
    """先頭frontmatterと論理本文を返し、種別共通の本文契約を検証する。"""
    frontmatter, body = _parse_leading_frontmatter(message)
    if entry_type == MQ_TYPE_FEEDBACK and _body_is_effectively_empty(body):
        raise WebInputError(_EMPTY_FEEDBACK_ERROR)
    if entry_type != MQ_TYPE_FEEDBACK:
        _tbd.reject_reserved_tbd_markup(body)
    return frontmatter, body


def _plan_metadata_text(plan_text: str) -> str | None:
    """計画Markdownの背景直下にある一意な`### 計画メタ情報`の非コード本文を返す。"""
    tokens = markdown_it.MarkdownIt("commonmark").parse(plan_text)
    lines = plan_text.splitlines()
    backgrounds: list[tuple[int, int]] = []
    for index, token in enumerate(tokens):
        if (
            token.type != "heading_open"
            or token.tag != "h2"
            or token.level != 0
            or token.map is None
            or tokens[index + 1].content != "背景"
        ):
            continue
        end_line = len(lines)
        for following in tokens[index + 2 :]:
            if following.type == "heading_open" and following.level == 0 and following.map is not None:
                if int(following.tag[1:]) > 2:
                    continue
                end_line = following.map[0]
                break
        backgrounds.append((token.map[1], end_line))
    if len(backgrounds) > 1:
        raise WebInputError(f"計画ファイルのフェンス外の`## 背景`が1件ではありません: 実際={len(backgrounds)}件")
    if not backgrounds:
        return None
    background_start, background_end = backgrounds[0]
    candidates = [
        (index, token)
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
        and token.tag == "h3"
        and token.level == 0
        and token.map is not None
        and background_start <= token.map[0] < background_end
        and tokens[index + 1].content == "計画メタ情報"
    ]
    if len(candidates) > 1:
        raise WebInputError(
            f"計画ファイルの`## 背景`直下にあるフェンス外の`### 計画メタ情報`が1件ではありません: 実際={len(candidates)}件"
        )
    if not candidates:
        return None
    index, metadata = candidates[0]
    assert metadata.map is not None
    end_line = background_end
    for following in tokens[index + 2 :]:
        if following.type == "heading_open" and following.level == 0 and following.map is not None:
            if int(following.tag[1:]) > 3:
                continue
            end_line = min(end_line, following.map[0])
            break
    code_lines = {
        line_no
        for token in tokens
        if token.type in {"fence", "code_block"}
        and token.map is not None
        and metadata.map[1] <= token.map[0]
        and token.map[1] <= end_line
        for line_no in range(token.map[0], token.map[1])
    }
    return "\n".join(lines[line_no] for line_no in range(metadata.map[1], end_line) if line_no not in code_lines)


def _verify_plan_base_commit(plan_path: pathlib.Path, target_commit: str | None) -> None:
    """計画ファイルのベースコミットと投入先作業ツリーのHEADを照合する。

    双方が完全OIDとして得られた場合だけ比較し、不一致なら`WebInputError`を送出する。
    計画側が欠落または短縮表記の場合は警告を出力して投入を継続する。
    """
    try:
        plan_text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise WebInputError(f"plan_fileを読み込めません: {plan_path}") from error
    metadata = _plan_metadata_text(plan_text)
    candidates = _PLAN_BASE_COMMIT_LINE_RE.findall(metadata or "")
    if len(candidates) > 1:
        raise WebInputError(f"計画ファイルの`### 計画メタ情報`にベースコミット候補が複数あります: 実際={len(candidates)}件")
    if not candidates or len(candidates[0]) not in {40, 64}:
        print(
            "警告: 計画ファイルの`### 計画メタ情報`からベースコミットの完全OIDを抽出できないため、"
            "投入先作業ツリーのHEADとの照合を省略します。",
            file=sys.stderr,
        )
        return
    plan_commit = candidates[0]
    if target_commit is None or plan_commit.casefold() == target_commit.casefold():
        return
    raise WebInputError(
        "計画ファイルのベースコミットと投入先作業ツリーのHEADが一致しません。"
        f"計画ファイル={plan_commit}、投入先HEAD={target_commit}。"
        "計画と投入先の両方を確認し、正しいベースコミットと一致する作業ツリーから再実行してください。"
    )


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
            raise WebInputError("plan_file指定時のメッセージfrontmatterのtarget_repoは文字列で指定してください")
        try:
            item_target_repo = _resolve_repo_id(raw_target_repo)
        except SystemExit as error:
            raise WebInputError(f"target_repoを解決できません: {raw_target_repo}") from error
        if item_target_repo != target_repo:
            raise WebInputError(
                "plan_file指定時はメッセージfrontmatterで対象リポジトリを別の値へ上書きできません。"
                f"投入先={target_repo}、frontmatter={item_target_repo}"
            )


def reject_message_file_path(message: str) -> None:
    """本文文字列が実在通常ファイルのパスだけの場合に`WebInputError`を送出する。"""
    value = message.strip()
    if not value:
        return
    try:
        if pathlib.Path(value).is_file():
            raise WebInputError(
                f"MESSAGEがファイルパス '{value}' として解釈できます。"
                "MESSAGEは本文文字列を受け取ります。"
                f'ファイル内容を渡す場合は "$(cat {value})" のようにシェルで展開してください。'
            )
    except OSError:
        # パス長制限などで検査できない文字列は本文として扱う。
        return


_RESERVED_FRONTMATTER_KEYS = (
    "target_repo",
    "target_commit",
    "type",
    "source",
    "scope",
    "question_type",
    "choices",
    "plan_file",
    "queue_schedule",
    "repair_target",
    "repair_kind",
)
"""frontmatter生成で単一箇所（`add_entries`）が専有するキー。

出力frontmatterはここに列挙したキーを必ず単一の値へ確定させ、入力メッセージのfrontmatterへ
同名キーが含まれていても`frontmatter_data.update()`による辞書更新で
入力値を除外する。このうち`target_repo`・`source`は明示された入力側の値を
CLIオプションより優先して採用するが、`target_repo`は`_resolve_repo_id`で正規化してから
保存する。`type`・`scope`・`question_type`・`choices`はCLIオプション
（`--type`・`--scope`・`--question-type`・`--choices`）の値で確定させ入力側の値を採用しない。
`target_commit`・`plan_file`・`queue_schedule`・`repair_target`・`repair_kind`は利用者による直接指定を禁止し、
システムが自動生成する計画実装型の識別情報・分類メタデータ・修復TBDの対象と理由区分として予約する。
"""


def _add_entries_locked(
    private_notes: pathlib.Path,
    *,
    parsed_messages: list[tuple[dict[str, object], str]],
    target_repo: str,
    source: str | None,
    now: datetime.datetime,
    entry_type: str,
    scope: str | None,
    question_type: str | None,
    choices: str | None,
    target_commit: str | None = None,
    plan_file: str | None = None,
    repair_targets: list[str | None] | None = None,
    repair_kinds: list[_schedule.RepairKind | None] | None = None,
) -> list[str]:
    """取得済みrepoロック内でエントリを書き込み、生成ファイル名を返す。"""
    effective_repair_targets: list[str | None] = [None for _ in parsed_messages] if repair_targets is None else repair_targets
    effective_repair_kinds: list[_schedule.RepairKind | None] = (
        [None for _ in parsed_messages] if repair_kinds is None else repair_kinds
    )
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
    inbox_dir = _subdir(private_notes, MQ_STATE_INBOX)
    counter = _max_existing_seq(private_notes, timestamp) + 1
    generated: list[str] = []
    for (frontmatter, body), repair_target, repair_kind in zip(
        parsed_messages,
        effective_repair_targets,
        effective_repair_kinds,
        strict=True,
    ):
        raw_target_repo = frontmatter.get("target_repo", target_repo)
        raw_source = frontmatter.get("source", source)
        try:
            item_target_repo = _resolve_repo_id(raw_target_repo) if isinstance(raw_target_repo, str) else target_repo
        except SystemExit as error:
            raise WebInputError(f"target_repoを解決できません: {raw_target_repo}") from error
        item_source = raw_source if isinstance(raw_source, str) else source
        filename = f"{timestamp}-{counter:03d}.md"
        while any((private_notes / state / filename).exists() for state in MQ_STATES):
            counter += 1
            filename = f"{timestamp}-{counter:03d}.md"
        frontmatter_data: dict[str, object] = {"target_repo": item_target_repo, "type": entry_type}
        if target_commit is not None and item_target_repo == target_repo:
            frontmatter_data["target_commit"] = target_commit
        if item_source:
            frontmatter_data["source"] = item_source
        frontmatter_data.update((key, value) for key, value in frontmatter.items() if key not in _RESERVED_FRONTMATTER_KEYS)
        if entry_type != MQ_TYPE_FEEDBACK:
            if scope:
                frontmatter_data["scope"] = scope
            frontmatter_data["question_type"] = question_type
            if choices:
                frontmatter_data["choices"] = choices
            if repair_target is not None:
                frontmatter_data["repair_target"] = repair_target
                frontmatter_data["repair_kind"] = repair_kind
            logical_body = f"\n{_tbd.QUESTION_HEADING}\n\n{body}\n\n{_tbd.ANSWER_HEADING}\n\n{_tbd.ANSWER_MARKER}\n"
        else:
            logical_body = body if body.startswith("\n") else f"\n{body.rstrip()}\n"
            if plan_file is not None:
                frontmatter_data["plan_file"] = plan_file
                metadata = _schedule.ScheduleMetadata(
                    body_sha256=_schedule.body_sha256(logical_body),
                    normalized_target_repo=item_target_repo,
                    feedback_type="plan-impl",
                    dependency=_schedule.Dependency(kind="none"),
                    plan_file=plan_file,
                    target_files=(),
                    carry_count=0,
                    carry_reasons=(),
                )
                frontmatter_data["queue_schedule"] = _schedule.metadata_to_mapping(metadata)
        content = _frontmatter.serialize_frontmatter(frontmatter_data, logical_body)
        (inbox_dir / filename).write_text(content, encoding="utf-8")
        if entry_type != MQ_TYPE_FEEDBACK:
            _tbd.warn_question_quality(filename, body, question_type)
        generated.append(filename)
        counter += 1
    return generated


def add_entries(
    private_notes: pathlib.Path,
    *,
    messages: list[str],
    target_repo: str,
    source: str | None,
    now: datetime.datetime,
    entry_type: str = MQ_TYPE_FEEDBACK,
    scope: str | None = None,
    question_type: str | None = None,
    choices: str | None = None,
    target_commit: str | None = None,
    plan_file: str | None = None,
    lock_timeout: float = -1,
    saved_details: dict[str, dict[str, object | None]] | None = None,
) -> list[str]:
    """平引数でメッセージキューのエントリを追加し、生成ファイル名を返す。

    frontmatterの予約キー（`_RESERVED_FRONTMATTER_KEYS`）以外のキーは入力順で出力frontmatterへ引き継ぐ。
    TBD種別では、本文がツール側で自動付与する見出し・回答欄マーカーを含む場合に
    `_tbd.reject_reserved_tbd_markup`が`WebInputError`を送出する（CLIとWeb UIの共通経路）。
    """
    if not messages:
        raise WebInputError("messagesには1件以上を指定してください")
    if target_commit is not None and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target_commit) is None:
        raise WebInputError("target_commitは40桁または64桁の完全OIDで指定してください")
    try:
        normalized_target_repo = _resolve_repo_id(target_repo)
    except SystemExit as error:
        raise WebInputError(f"target_repoを解決できません: {target_repo}") from error
    plan_path: pathlib.Path | None = None
    if plan_file is not None:
        if entry_type != MQ_TYPE_FEEDBACK:
            raise WebInputError("plan_fileはfeedback種別でのみ指定できます")
        plan_path = pathlib.Path(plan_file)
        if not plan_path.is_absolute():
            raise WebInputError("plan_fileは絶対パスで指定してください")
        try:
            if not plan_path.is_file():
                raise WebInputError(f"plan_fileが実在する通常ファイルではありません: {plan_file}")
        except OSError as error:
            raise WebInputError(f"plan_fileを検証できません: {plan_file}") from error
    if entry_type != MQ_TYPE_FEEDBACK and question_type not in {"choice", "yes-no", "free-form"}:
        raise WebInputError("question_typeが不正です")
    parsed_messages = [parse_entry_message(message, entry_type=entry_type) for message in messages]
    if plan_path is not None:
        _verify_plan_target_repos(parsed_messages, normalized_target_repo)
        _verify_plan_base_commit(plan_path, target_commit)
    if entry_type != MQ_TYPE_FEEDBACK and question_type == "choice" and not choices:
        raise WebInputError("choice形式にはchoicesが必要です")
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        generated = _add_entries_locked(
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
            plan_file=plan_file,
        )
        count = len(generated)
        _commit_and_push(
            private_notes,
            f"chore: add {count} {entry_type} {'item' if count == 1 else 'items'}",
            [MQ_STATE_INBOX],
        )
        if saved_details is not None:
            saved_details.update(
                (filename, _read_saved_entry_details(private_notes / MQ_STATE_INBOX / filename)) for filename in generated
            )
    return generated


def _cmd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """addサブコマンド: メッセージをinboxへ投入してcommit・push。

    対象リポジトリは常にカレントディレクトリから解決する。ただし`mq add`直後のトークンが実在
    ディレクトリの場合は旧REPO_PATH位置引数形式の呼び出しとみなし、`atk.py`側の事前抽出で
    当該引数をREPO_PATHとして扱う（互換維持、抽出結果は`args.repo_path_override`で受け取る）。
    各メッセージ先頭がYAML frontmatter形式の場合は`target_repo`・`source`をCLIオプションより優先する。
    `--target-repo`指定時は、レガシーREPO_PATH位置引数が無くfrontmatterにも`target_repo`が
    無い場合のfallback値として使う。
    エディター経由の本文確定後に対象worktreeのHEADを取得してから`_pull`を実行する順序とし、
    エディター起動前のブロッキング待ち（他端末の投入分を反映するgit pull）を無くしてUXを改善する。
    `_pull`失敗時はエディターで確定済みの本文をstderrへ再表示してから終了し、入力内容の消失を防ぐ。
    各メッセージの本文が実質空（`_body_is_effectively_empty`）の場合は`_repo_lock`取得前に拒否する。
    計画実装型の分類は`--plan-file`の指定だけで確定する。
    本文文字列（位置引数またはエディター確定内容）が実在する通常ファイルのパスと解釈できる場合は、
    本文文字列でなくファイル内容の渡し忘れによる誤操作とみなし`_repo_lock`取得前に拒否する
    （拡張子は問わない。`mktemp`が生成する拡張子なしの一時ファイルパスの誤投入も検出対象に含めるためである）。
    """
    messages, repo_path_override = _resolve_repo_path_override(args.messages, args.repo_path_override)
    _reject_bare_repo_path_override(repo_path_override, messages, args.subparser)
    target_value = repo_path_override if repo_path_override is not None else args.target_repo
    target_repo, local_worktree = resolve_add_target(target_value)
    collected_via_editor = not messages
    if not messages:
        message = _collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    for message in messages:
        try:
            reject_message_file_path(message)
            parse_entry_message(message, entry_type=args.type)
        except WebInputError as error:
            if str(error) == _EMPTY_FEEDBACK_ERROR:
                preview = message.strip().splitlines()[0] if message.strip() else "(空文字列)"
                print(
                    "投入を拒否しました: 本文が実質空です"
                    "（空文字・空白のみ・箇条書きマーカー単独文字のいずれか）。"
                    f"該当メッセージの先頭: {preview}",
                    file=sys.stderr,
                )
            else:
                print(f"投入を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)
    try:
        target_commit = resolve_head_commit(local_worktree) if local_worktree is not None else None
    except SystemExit:
        if collected_via_editor:
            print("HEADコミットの取得に失敗しました。確定済みの本文を以下に再表示します。", file=sys.stderr)
            for message in messages:
                print("---", file=sys.stderr)
                print(message, file=sys.stderr)
        raise
    saved_details: dict[str, dict[str, object | None]] = {}
    try:
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
            saved_details=saved_details,
        )
    except WebInputError as error:
        print(f"投入を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError:
        print("git pullに失敗しました。確定済みの本文が消失しないよう以下に再表示します。", file=sys.stderr)
        for message in messages:
            print("---", file=sys.stderr)
            print(message, file=sys.stderr)
        sys.exit(1)
    count = len(generated)
    inbox_dir = _subdir(private_notes, "inbox")
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    print(f"{count}件投入:")
    for filename in generated:
        print(f"  {_shorten_home(inbox_dir / filename, home)}")
        _print_entry_details(saved_details[filename])
    print(f"inbox: 計{_count_feedback(inbox_dir)}件（processing: {_count_feedback(processing_dir)}件）")
    print(
        f"  うち{target_repo}: {_count_feedback(inbox_dir, target_repo)}件"
        f"（processing: {_count_feedback(processing_dir, target_repo)}件）"
    )
    print("編集する場合:")
    for filename in generated:
        print(f"  atk mq edit {filename}")
