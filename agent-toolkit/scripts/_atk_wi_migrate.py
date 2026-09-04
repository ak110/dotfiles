"""既存のprivate-notesをワークアイテム体系へ一方向に変換する。

キュー項目のfrontmatterの`type`値と、キュー項目と計画ファイルの本文に残る旧称を
現行の呼称へ置き換える。廃止した保存状態のディレクトリにある項目は`hold`へ移す。
変換はロック下で全件を検証してから1回のcommitで確定し、commit前の失敗では変更を残さない。
"""

import argparse
import collections.abc
import pathlib
import re
import subprocess

import _atk_git_sync
import _atk_wi_common as _common
import _git_command
from _atk_wi_constants import WI_STATE_HOLD, WI_STATES

_WITHDRAWN_STATES = ("planning", "editing")
"""現行の保存状態が廃止し、`hold`へ移す旧状態のディレクトリ名。"""

_PLANS_DIRECTORY = "plans"
"""変換対象へ含める計画ファイルの保存rootのディレクトリ名。"""

_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("plan-and-add-feedback", "plan-and-add-awi"),
    ("process_feedbacks_skill_invoked", "process_wi_skill_invoked"),
    ("Feedback-Originated", "AWI-Originated"),
    ("feedback-standards", "wi-standards"),
    ("plan-feedback-body", "plan-awi-body"),
    ("process-feedbacks", "process-wi"),
    ("pick_feedbacks_model", "pick_wi_model"),
    ("pick-feedbacks", "pick-wi"),
    ("tbd-format", "uwi-format"),
    ("z-feedback.md", "z-awi.md"),
    ("add-feedback", "add-awi"),
    ("--feedback", "--awi"),
    ("通常型フィードバック", "通常型AWI"),
    ("計画型フィードバック", "計画型AWI"),
    ("通常フィードバック", "通常AWI"),
    ("_atk_mq_", "_atk_wi_"),
    ("atk mq", "atk wi"),
    ("mq-show", "wi-show"),
    ("MQ_", "WI_"),
    ("フィードバック", "AWI"),
)
"""現行の呼称を持つ複合識別子と日本語の呼称の対応。長い語から順に適用する。

`feedbacks-planner`のように現行の実体を持たない廃止済みの複合識別子はここへ載せない。
`_IDENTIFIER_RE`の判定により概念語としての置換の対象にもならず、当時の名称のまま残る。
"""

_IDENTIFIER_RE = re.compile(r"[0-9A-Za-z_.:/-]+")
"""識別子として一続きに扱う範囲。

ハイフンを含む範囲は`feedbacks-planner`や`~/private-notes/feedback/inbox/`のように
当時の実体を指す複合識別子とみなし、概念語の置換と修復のいずれも適用しない。
"""

_WORD_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("feedbacks", "awis"),
    ("feedback", "awi"),
    ("FEEDBACKS", "AWIS"),
    ("FEEDBACK", "AWI"),
    ("Feedback", "Awi"),
    ("TBD", "UWI"),
    ("Tbd", "Uwi"),
    ("tbd", "uwi"),
)
"""概念語として現れる旧称と現行の呼称の対応。複数形を単数形より先に適用する。"""

_REPAIRS: tuple[tuple[str, str], ...] = (
    ("process_awis_contract_test", "process_feedbacks_contract_test"),
    ("process-wi-finish", "process-feedbacks-finish"),
    ("process-wi-lane", "process-feedbacks-lane"),
    ("process-wi-loop", "process-feedbacks-loop"),
    ("_AWIS_PLANNER", "_FEEDBACKS_PLANNER"),
    ("awi/filename", "feedback/filename"),
)
"""複合識別子を概念語として置き換えた時期の変換が生んだ誤った識別子と、当時の名称の対応。

ハイフンを含まないため`_REPAIR_WORDS`の対象にならないものと、廃止済みの実体を指すために
現行の呼称へ変換してはならないものを個別に列挙する。長い語から順に適用する。
"""

_REPAIR_WORDS: tuple[tuple[str, str], ...] = (
    ("awis", "feedbacks"),
    ("awi", "feedback"),
    ("Awi", "Feedback"),
    ("uwi", "tbd"),
    ("Uwi", "Tbd"),
)
"""複合識別子の内側で当時の名称へ戻す語の対応。複数形を単数形より先に適用する。

戻した名称は続く変換で改めて判定されるため、現行の実体を持つものは現行の呼称になる。
"""

_PROTECTED_TOKENS = (
    "agent-toolkit:plan-and-add-awi",
    "process_feedbacks_contract_test",
    "process-feedbacks-finish",
    "process-feedbacks-lane",
    "process-feedbacks-loop",
    "agent-toolkit:add-awi",
    "エージェント由来のフィードバック",
    "人間由来のフィードバック",
    "_FEEDBACKS_PLANNER",
    "feedback/filename",
    "plan-and-add-awi",
    "関連フィードバック",
    "plan-awi-body",
    "z-awi.md",
    "add-awi",
    "--awi",
)
"""変換も修復もしない文字列。長い語から順に退避する。

保存済みの計画ファイルで読み取り互換の対象となる語、現行の実体を持たない廃止済みの識別子、
および旧称を含まないため修復の対象にならない現行の識別子を含む。
"""

_PROTECTED_HEADINGS = ("ユーザーコメント", "回答")
"""ユーザーだけが書き込む節の見出し。配下の本文を置換しない。"""

_HEADING_RE = re.compile(r"^\s*(?P<marks>#{1,6})\s+(?P<title>.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>`{3,}|~{3,})(?P<info>[^\r\n]*)")


def _protected_lines(text: str) -> set[int]:
    """置換しない行の索引を返す。

    ユーザーだけが書き込む節の配下と、逐語で保持するコードブロックの内側を対象とする。
    """
    lines = text.splitlines(keepends=True)
    protected: set[int] = set()
    user_section_marks: int | None = None
    fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        if fence is not None:
            protected.add(index)
            marker, length = fence
            stripped = line.lstrip()
            if stripped.startswith(marker * length) and stripped[length:].strip() == "":
                fence = None
            continue
        opening = _FENCE_RE.match(line)
        if opening is not None and opening.group("marker"):
            fence = (opening.group("marker")[0], len(opening.group("marker")))
            protected.add(index)
            continue
        heading = _HEADING_RE.match(line)
        if heading is not None:
            marks = len(heading.group("marks"))
            if heading.group("title") in _PROTECTED_HEADINGS:
                user_section_marks = marks
                protected.add(index)
                continue
            if user_section_marks is not None and marks <= user_section_marks:
                user_section_marks = None
        if user_section_marks is not None:
            protected.add(index)
    return protected


def _protected(line: str, replace: collections.abc.Callable[[str], str]) -> str:
    """保護対象の文字列を退避したうえで`replace`を適用する。"""
    placeholders = {token: f"\x00{index}\x00" for index, token in enumerate(_PROTECTED_TOKENS)}
    for token, placeholder in placeholders.items():
        line = line.replace(token, placeholder)
    line = replace(line)
    for token, placeholder in placeholders.items():
        line = line.replace(placeholder, token)
    return line


def _repair_identifier(match: re.Match[str]) -> str:
    """複合識別子の内側にある誤った語を当時の名称へ戻す。"""
    identifier = match.group()
    if "-" not in identifier:
        return identifier
    for old, new in _REPAIR_WORDS:
        identifier = identifier.replace(old, new)
    return identifier


def _repair_line(line: str) -> str:
    """複合識別子を概念語として置き換えた時期の変換が生んだ誤った識別子を戻す。"""
    for old, new in _REPAIRS:
        line = line.replace(old, new)
    return _IDENTIFIER_RE.sub(_repair_identifier, line)


def _convert_identifier(match: re.Match[str]) -> str:
    """複合識別子でない範囲の概念語を現行の呼称へ置き換える。"""
    identifier = match.group()
    if "-" in identifier:
        return identifier
    for old, new in _WORD_REPLACEMENTS:
        identifier = identifier.replace(old, new)
    return identifier


def _convert_line(line: str) -> str:
    """1行の旧称を現行の呼称へ置き換える。"""
    for old, new in _REPLACEMENTS:
        line = line.replace(old, new)
    return _IDENTIFIER_RE.sub(_convert_identifier, line)


def _replace_line(line: str) -> str:
    """誤った識別子を戻してから、1行の旧称を現行の呼称へ置き換える。

    修復で当時の名称へ戻した識別子が変換の対象になるため、保護対象の退避は各段で行う。
    """
    return _protected(_protected(line, _repair_line), _convert_line)


def _converted_text(text: str) -> str:
    """保護範囲を除いた本文の旧称を現行の呼称へ置き換える。"""
    protected = _protected_lines(text)
    return "".join(
        line if index in protected else _replace_line(line) for index, line in enumerate(text.splitlines(keepends=True))
    )


def _remaining_old_names(text: str) -> tuple[str, ...]:
    """保護範囲外に残る旧称と、修復されなかった誤った識別子を返す。"""
    protected = _protected_lines(text)
    placeholders = {token: f"\x00{index}\x00" for index, token in enumerate(_PROTECTED_TOKENS)}
    remaining: list[str] = []
    for index, line in enumerate(text.splitlines(keepends=True)):
        if index in protected:
            continue
        for token, placeholder in placeholders.items():
            line = line.replace(token, placeholder)
        remaining.extend(old for old, _new in (*_REPLACEMENTS, *_REPAIRS) if old in line)
        remaining.extend(
            old
            for identifier in _IDENTIFIER_RE.findall(line)
            if "-" not in identifier
            for old, _new in _WORD_REPLACEMENTS
            if old in identifier
        )
    return tuple(dict.fromkeys(remaining))


def _protected_text(text: str) -> str:
    """保護範囲の行だけを連結して返す。"""
    protected = _protected_lines(text)
    return "".join(line for index, line in enumerate(text.splitlines(keepends=True)) if index in protected)


def _target_files(private_notes: pathlib.Path) -> list[pathlib.Path]:
    """変換対象のキュー項目と計画ファイルを返す。"""
    targets: list[pathlib.Path] = []
    for state in (*WI_STATES, *_WITHDRAWN_STATES):
        directory = private_notes / state
        if not directory.is_dir():
            continue
        targets.extend(path for path in sorted(directory.glob("*.md")) if path.is_file() and not path.is_symlink())
    plans_root = private_notes / _PLANS_DIRECTORY
    if plans_root.is_dir():
        targets.extend(path for path in sorted(plans_root.rglob("*")) if path.is_file() and not path.is_symlink())
    return targets


def _destination(path: pathlib.Path, private_notes: pathlib.Path) -> pathlib.Path:
    """変換後の配置先を返す。廃止した状態の項目は`hold`へ移す。"""
    if path.parent.name in _WITHDRAWN_STATES and path.parent.parent == private_notes:
        return private_notes / WI_STATE_HOLD / path.name
    return path


def _planned_changes(private_notes: pathlib.Path) -> tuple[dict[pathlib.Path, tuple[pathlib.Path, bytes]], list[str]]:
    """変換対象ごとの`(配置先, 変換後の内容)`と、検出した問題の一覧を返す。"""
    planned: dict[pathlib.Path, tuple[pathlib.Path, bytes]] = {}
    errors: list[str] = []
    destinations: set[pathlib.Path] = set()
    for path in _target_files(private_notes):
        original = path.read_bytes()
        destination = _destination(path, private_notes)
        if destination != path and (destination.exists() or destination in destinations):
            errors.append(f"移動先に同名の項目が存在します: {destination}")
            continue
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError:
            converted_bytes = original
        else:
            converted = _converted_text(text)
            converted_bytes = converted.encode("utf-8")
            remaining = _remaining_old_names(converted)
            if remaining:
                errors.append(f"変換後も旧称が残ります: {path}（{', '.join(remaining)}）")
                continue
            if _protected_text(converted) != _protected_text(text):
                errors.append(f"保護範囲が変換で変化しました: {path}")
                continue
        if destination == path and converted_bytes == original:
            continue
        destinations.add(destination)
        planned[path] = (destination, converted_bytes)
    return planned, errors


def _head_commit(private_notes: pathlib.Path) -> str:
    """現在のHEADの完全OIDを返す。"""
    result = _git_command.run(["rev-parse", "HEAD"], cwd=private_notes, check=True, capture_output=True, text=True)
    if not isinstance(result.stdout, str):
        raise RuntimeError("変換後のHEADを取得できません")
    return result.stdout.strip()


def _relative(path: pathlib.Path, private_notes: pathlib.Path) -> str:
    """private-notesからのPOSIX相対パスを返す。"""
    return path.relative_to(private_notes).as_posix()


def migrate_queue(
    private_notes: pathlib.Path,
    *,
    skip_push: bool = False,
    lock_timeout: float = -1,
) -> dict[str, object]:
    """private-notesの`type`値と旧称をワークアイテム体系へ変換する。"""
    with _common.repo_lock(private_notes, timeout=lock_timeout):
        try:
            _atk_git_sync.ensure_not_rebasing(private_notes)
            if not _atk_git_sync.has_remote(private_notes):
                raise _common.WebInputError("remoteなしのprivate-notesでは変換を実行できません")
            if _atk_git_sync.is_worktree_dirty(private_notes):
                raise _common.WebInputError("private-notesのindex・worktreeがcleanでないため変換を開始できません")
            _atk_git_sync.require_upstream(private_notes)
            _atk_git_sync.pull(private_notes)
            if _atk_git_sync.is_worktree_dirty(private_notes):
                raise _common.WebInputError("remote同期後のprivate-notesがcleanでないため変換を開始できません")
        except _atk_git_sync.RebaseInProgressError as error:
            raise _common.WebInputError(str(error)) from error
        except _atk_git_sync.GitSyncError as error:
            raise _common.WebInputError(str(error)) from error

        planned, errors = _planned_changes(private_notes)
        if errors:
            raise _common.WebInputError("変換を中止しました。\n" + "\n".join(f"- {error}" for error in errors))
        if not planned:
            return {"converted": 0, "moved": 0, "commit": None}

        snapshot = {path: path.read_bytes() for path in planned}
        moved = [path for path, (destination, _content) in planned.items() if destination != path]
        commit_paths = sorted(
            {_relative(path, private_notes) for path in planned}
            | {_relative(destination, private_notes) for destination, _content in planned.values()}
        )
        base_commit = _head_commit(private_notes)
        try:
            for path, (destination, content) in planned.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                if destination != path:
                    path.unlink()
            message = f"chore: migrate {len(planned)} queue {'entry' if len(planned) == 1 else 'entries'} to work items"
            _atk_git_sync.commit_and_push(private_notes, message, commit_paths, skip_push=skip_push)
            if not skip_push and not _atk_git_sync.remote_contains_head(private_notes):
                raise _common.WebInputError("変換commitがremote branchへ到達したことを確認できません")
        except (OSError, subprocess.SubprocessError, _common.WebInputError, _atk_git_sync.GitSyncError):
            if _head_commit(private_notes) == base_commit:
                for path, content in snapshot.items():
                    destination = planned[path][0]
                    if destination != path:
                        destination.unlink(missing_ok=True)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            raise

        return {"converted": len(planned), "moved": len(moved), "commit": _head_commit(private_notes)}


def cmd_migrate(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """migrateサブコマンド: private-notesをワークアイテム体系へ変換する。"""
    target = pathlib.Path(args.private_notes).expanduser() if args.private_notes else private_notes
    result = migrate_queue(target, skip_push=args.skip_push)
    print(f"{result['converted']}件を変換しました（うち{result['moved']}件を移動）。")
    if result["commit"] is not None:
        print(f"commit: {result['commit']}")
