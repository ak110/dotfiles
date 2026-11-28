"""Gitの引数とcommitメッセージの検査。"""

from __future__ import annotations

import pathlib
import re
lazy import subprocess
lazy from collections.abc import Iterable, Sequence

lazy from agent_toolkit._common import git_invocation, shell_cwd
lazy from agent_toolkit._common.bash_invocations import extract_bash_invocations, is_unknown_word
lazy from agent_toolkit._common.heredocs import mask_heredoc_bodies
lazy from agent_toolkit._common.runtime_identity import RuntimeIdentity, co_author_trailer
lazy from agent_toolkit._common.shell_tokens import is_atk_command, strip_redirections
lazy from agent_toolkit._git import command as git_command
lazy from agent_toolkit._hooks.notice import _WARN_TAG
lazy from agent_toolkit._hooks.pretooluse.notices import _block_notice, _llm_notice

_GLOBAL_OPTIONS_WITH_VALUE = git_invocation.GLOBAL_OPTIONS_WITH_VALUE
_GLOBAL_OPTIONS_LOCATING_REPOSITORY: frozenset[str] = frozenset({"-C", "--git-dir", "--work-tree"})


"""値が対象リポジトリと実行位置だけを変え、commitメッセージの本文に作用しない大域オプション。

値に展開や置換を含んでもcommitの書式検査を行い、実行位置を不明として扱う。
"""


_GLOBAL_OPTIONS_POSSIBLY_AFFECTING_MESSAGE: frozenset[str] = frozenset(
    {"-c", "--namespace", "--super-prefix", "--config-env", "--list-cmds"}
)


"""値がcommitメッセージやその入力に作用しうるか、作用しないと確かめていない大域オプション。

値に展開や置換を含むcommitは、検証済みと推定せず書式検査の対象から外す。
`_GLOBAL_OPTIONS_WITH_VALUE`へ加えるオプションは、どちらかへ必ず分類する（テストが強制する）。
"""


git_subcommand_tokens = _git_subcommand_tokens = git_invocation.git_subcommand_tokens


_GIT_COMMIT_VALUE_OPTIONS = frozenset(
    {
        "-m",
        "--message",
        "-F",
        "--file",
        "-C",
        "--reuse-message",
        "-c",
        "--reedit-message",
        "--author",
        "--date",
        "--cleanup",
        "-t",
        "--template",
        "--trailer",
        "--fixup",
        "--squash",
        "--pathspec-from-file",
    }
)


def git_commit_options(arguments: Sequence[str]) -> Iterable[tuple[str, str | None]]:
    tokens = iter(arguments)
    for token in tokens:
        if token == "--":
            break
        if token.startswith("--"):
            option, separator, value = token.partition("=")
            yield option, value if separator else next(tokens, None) if option in _GIT_COMMIT_VALUE_OPTIONS else None
        elif token.startswith("-") and len(token) > 1:
            for position, char in enumerate(token[1:], 1):
                option = "-" + char
                if option in _GIT_COMMIT_VALUE_OPTIONS:
                    yield option, token[position + 1 :] or next(tokens, None)
                    break
                if char in "Su":
                    yield option, token[position + 1 :] or None
                    break
                yield option, None
        else:
            yield token, None


def is_new_commit_message(arguments: Sequence[str]) -> bool:
    names = {name for name, _ in git_commit_options(arguments)}
    return not (names & {"--fixup", "--squash", "-C", "--reuse-message"} or {"--amend", "--no-edit"} <= names)


_UNCHANGING_COMMIT_OPTIONS = frozenset(
    {
        "-m",
        "--message",
        "-F",
        "--file",
        "-q",
        "--quiet",
        "-a",
        "--all",
        "--allow-empty",
        "-n",
        "--no-verify",
    }
)


def commit_input_unchanged_by_options(command: str) -> bool:
    commits = [
        parsed[1]
        for invocation in extract_bash_invocations(command)
        if (parsed := git_subcommand_tokens(invocation.segment)) is not None and parsed[0] == "commit"
    ]
    return bool(commits) and all(
        option in _UNCHANGING_COMMIT_OPTIONS for arguments in commits for option, _ in git_commit_options(arguments)
    )


def commit_requires_preflight(command: str) -> bool:
    for invocation in extract_bash_invocations(command):
        segment = invocation.segment
        parsed = git_subcommand_tokens(segment)
        if parsed is None or parsed[0] != "commit" or not is_new_commit_message(parsed[1]):
            continue
        if any(option in {"-n", "--no-verify"} for option, _ in git_commit_options(parsed[1])):
            return True
        prefix = segment.tokens[: len(segment.tokens) - len(parsed[1])]
        if any(token.removeprefix("-c").lower().startswith("core.hookspath=") for token in prefix):
            return True
    return False


def _git_commit_messages(command: str, *, cwd: str = "") -> Iterable[str]:
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        parsed = git_subcommand_tokens(invocation.segment)
        if parsed is None or parsed[0] != "commit":
            continue
        arguments = parsed[1]

        options = list(git_commit_options(arguments))
        location_unknown = _unknown_global_words(invocation.segment.tokens[: len(invocation.segment.tokens) - len(arguments)])
        if location_unknown is None or any(
            is_unknown_word(option) or value is not None and is_unknown_word(value) for option, value in options
        ):
            continue
        names = {option for option, _value in options}
        if names & {"--fixup", "--squash"}:
            continue
        if {"--amend", "--no-edit"} <= names:
            continue
        messages: list[str] = []
        message_files: list[str] = []
        unknown = False
        for token, value in options:
            if token in {"-m", "--message", "-F", "--file"}:
                if value is None:
                    unknown = True
                    break
                (messages if token in {"-m", "--message"} else message_files).append(value)
            elif token in {"-C", "--reuse-message", "-c", "--reedit-message"}:
                unknown = True
                break
        if unknown:
            continue
        for message_file in message_files:
            if message_file == "-":
                body = _commit_heredoc_body(command)
            else:
                body = _read_commit_message_file(
                    message_file,
                    ("git", "commit") if is_atk_command(invocation.segment.tokens) else invocation.segment.tokens,
                    cwd=current.path if current.resolved and not location_unknown else "",
                )
            if body is None:
                unknown = True
                break
            messages.append(body)
        if unknown or not messages:
            continue
        yield "\n\n".join(messages)


def duplicate_autosquash_subject(command: str, cwd: str) -> str | None:
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        tokens = invocation.segment.tokens
        parsed = git_subcommand_tokens(invocation.segment)
        if parsed is None or parsed[0] != "commit" or not current.resolved:
            continue
        prefix = () if is_atk_command(tokens) else tokens[1 : len(tokens) - len(parsed[1]) - 1]
        if len(prefix) % 2 or any(key != "-C" for key in prefix[::2]) or any(is_unknown_word(x) for x in prefix):
            continue
        for flag, argument in git_commit_options(parsed[1]):
            if flag not in {"--fixup", "--squash"} or not argument or is_unknown_word(argument) or "\x00" in argument:
                continue
            modifier, sep, rev = argument.partition(":")
            revise = flag == "--fixup" and sep and modifier in {"amend", "reword"}
            subject = _duplicate_subject(current.path, prefix, rev if revise else argument)
            if subject is None:
                continue
            oid = subject
            action = (
                "amend/rewordのfixupを作らずautosquashを中止してください。"
                if revise
                else (
                    f"`{flag}`を外し、`git commit -m '{'fixup' if flag == '--fixup' else 'squash'}! {oid}'`"
                    " で作り直してください。"
                )
            )
            return _block_notice(
                "未公開の履歴に同じ件名のコミットがあり、`autosquash`の統合先を一意に決められない。",
                fix=action + " history-rewrite.md「fixupの実行上の制約」を参照してください。" + _COMMIT_BLOCK_RERUN,
            )
    return None


def _duplicate_subject(cwd: str, prefix: Sequence[str], revision: str) -> str | None:
    try:
        target = git_command.run(
            [*prefix, "show", "-s", "--format=%H%x00%s", "--end-of-options", f"{revision}^{{commit}}", "--"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        oid, boundary, subject = target.stdout.rstrip("\n").partition("\x00")
        if target.returncode or not boundary or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
            return None
        log = git_command.run(
            [*prefix, "log", "--format=%H%x00%s", "HEAD", "--not", "--remotes", "--"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if log.returncode == 0 and any(
            other_oid != oid and other_subject == subject
            for other_oid, _, other_subject in (line.partition("\x00") for line in log.stdout.split("\n"))
        ):
            return oid
    except OSError, subprocess.TimeoutExpired:
        pass
    return None


def _unknown_global_words(prefix: Sequence[str]) -> bool | None:
    location_unknown = False
    words = iter(prefix)
    for word in words:
        if word in _GLOBAL_OPTIONS_LOCATING_REPOSITORY:
            location_unknown = is_unknown_word(next(words, "")) or location_unknown
        elif is_unknown_word(word):
            return None
    return location_unknown


_WORK_ITEM_FILENAME_PATTERN = re.compile(r"(?<![0-9A-Za-z_-])\d{8}-\d{6}-\d{3}\.md(?![0-9A-Za-z_.-])")


_COMMIT_BLOCK_RERUN = "同じコマンド全体へ修正を加えて再実行してください（`git add`などの前段も含む）。"

_COMMIT_MSG_RERUN = (
    "Gitがcommitの作成を止めた。連結した前段（`git add`など）は実行済みのため、"
    "前段を繰り返さず、本文を直してcommitだけを再実行してください。"
)


def _git_commit_format_violations(messages: Iterable[str]) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    for message in messages:
        if match := _WORK_ITEM_FILENAME_PATTERN.search(message):
            violations.append(
                (
                    f"公開するコミットメッセージに作業項目のファイル名（`{match.group()}`）が含まれている。",
                    "公開するコミットへ作業項目のファイル名を書かないでください。実装コミットとAWIの対応は"
                    "`atk run-script plan-progress`が記録する対応記録ファイル（`<stem>.wi-commits.jsonl`）へ残し、"
                    "メッセージから作業項目のファイル名を含む行を除いてください。",
                )
            )
        lines = message.splitlines()
        if len(lines) > 1 and lines[1].strip():
            violations.append(("`commit`の件名と本文の間の空行が欠けている。", "件名の次に空行を1行入れてください。"))
    return violations


def _git_commit_attribution_violations(
    messages: Iterable[str],
    identity: RuntimeIdentity | None,
    *,
    attribution_disabled: bool = False,
) -> list[tuple[str, str]]:
    if attribution_disabled or identity is None or identity.source != "observed":
        return []
    expected = co_author_trailer(identity)
    for message in messages:
        trailers = [line for line in message.splitlines() if line.startswith("Co-Authored-By:")]
        if trailers != [expected]:
            return [
                (
                    "通常`commit`の`Co-Authored-By:`が、実行ターンで観測したモデルに対応する1行だけになっていない。"
                    f"必要な行: {expected}",
                    "観測したモデルに対応する上記の`Co-Authored-By:`を1行だけ入れてください。",
                )
            ]
    return []


def _git_commit_block(
    messages: Iterable[str],
    identity: RuntimeIdentity | None,
    *,
    attribution_disabled: bool = False,
    prepared: bool = False,
) -> str | None:
    messages = tuple(messages)
    violations = list(dict.fromkeys(_git_commit_format_violations(messages)))
    violations += _git_commit_attribution_violations(messages, identity, attribution_disabled=attribution_disabled)
    if not violations:
        return None
    retry = _COMMIT_MSG_RERUN if prepared else _COMMIT_BLOCK_RERUN
    if len(violations) == 1:
        return _block_notice(violations[0][0], fix=violations[0][1] + retry)
    return _block_notice(
        f"`commit`のメッセージに違反が{len(violations)}件ある。"
        + "".join(f"（{number}）{reason}" for number, (reason, _fix) in enumerate(violations, 1)),
        fix="".join(f"（{number}）{fix}" for number, (_reason, fix) in enumerate(violations, 1)) + retry,
    )


def _commit_heredoc_body(command: str) -> str | None:
    original_lines = command.split("\n")
    masked_lines = mask_heredoc_bodies(command).split("\n")
    bodies: list[str] = []
    for terminator, (original, masked) in enumerate(zip(original_lines, masked_lines, strict=True)):
        if original == masked or not masked.startswith(";") or masked[1:].strip():
            continue

        start = terminator
        while start > 0 and not masked_lines[start - 1].strip():
            start -= 1
        declaration = masked_lines[start - 1] if start > 0 else ""
        if any(
            (parsed := git_subcommand_tokens(item.segment)) is not None and parsed[0] == "commit"
            for item in extract_bash_invocations(declaration)
        ):
            bodies.append("\n".join(original_lines[start:terminator]))
    return bodies[0] if len(bodies) == 1 else None


def _read_commit_message_file(
    path_text: str,
    git_tokens: Sequence[str],
    *,
    cwd: str,
) -> str | None:
    path = pathlib.Path(path_text)
    if not path.is_absolute():
        if not cwd:
            return None
        base = pathlib.Path(cwd)
        for index, token in enumerate(git_tokens[1:], start=1):
            if token == "commit":
                break
            if token == "-C" and index + 1 < len(git_tokens):
                base = base / git_tokens[index + 1]
        path = base / path
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError, UnicodeError:
        return None


_GIT_GREP_PATTERN_OPTIONS: frozenset[str] = frozenset({"-e", "--regexp"})


_GIT_GREP_PATTERN_FILE_OPTIONS: frozenset[str] = frozenset({"-f", "--file"})


_GIT_REV_PARSE_VALUED_OPTIONS = frozenset({"--default", "--prefix", "--git-path", "--resolve-git-dir", "-n"})


"""`man git-rev-parse`の値を別引数で取るオプション（git 2.43.0）。"""


def _rev_parse_short_revisions(arguments: Sequence[str]) -> list[str] | None:
    has_short = False
    if arguments and arguments[0] in {"--sq-quote", "--parseopt"}:
        return None
    revisions: list[str] = []
    skip_value = False
    for token in strip_redirections(arguments):
        if skip_value:
            skip_value = False
            continue
        if token == "--":
            break
        if token in _GIT_REV_PARSE_VALUED_OPTIONS:
            skip_value = True
            continue
        if token in {"--short", "--verify"} or token.startswith("--short="):
            has_short = True
            continue
        if token.startswith("-"):
            continue
        if has_short and ".." not in token:
            revisions.append(token)
    return revisions if has_short else None


def _warn_git_rev_parse_short_multiple(command: str) -> str | None:
    """単一revision検証へ複数revisionを渡す読取へ警告する。

    同コマンドは1回に1つのrevisionだけを受理し、複数を渡すと`fatal: Needed a single revision`で失敗する。
    条文で定めた後も同じ失敗が反復したため、実行の直前に判定する。
    個別の取得で結果を復元できるため、警告とする。
    """
    for invocation in extract_bash_invocations(command):
        if not invocation.arguments_known:
            continue
        segment = invocation.segment
        subcommand = git_subcommand_tokens(segment)
        if subcommand is None or subcommand[0] != "rev-parse":
            continue
        revisions = _rev_parse_short_revisions(subcommand[1])
        if revisions is not None and len(revisions) >= 2:
            return _llm_notice(
                f"`git rev-parse`の単一リビジョン検証へ{len(revisions)}つのリビジョン（{'、'.join(revisions)}）を渡している。"
                "`--short`または`--verify`は1回に1つのリビジョンだけを受理し、複数指定は失敗する。"
                "`--quiet`によって診断と終了コードが変わるため、終了コード128とは限らない。",
                tag=_WARN_TAG,
                subject=command,
                removable_cause=True,
                fix="元の`--short`・`--short=<長さ>`・`--verify`などのオプションを保ち、リビジョンごとに"
                "`git rev-parse <元のオプション> <revision>`を個別に実行して入力と出力の対応を保ってください。",
            )
    return None
