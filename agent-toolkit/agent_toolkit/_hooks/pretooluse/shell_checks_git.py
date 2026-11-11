"""Gitの引数とcommitメッセージの検査。"""

from __future__ import annotations

import pathlib
import re
lazy from collections.abc import Iterable, Sequence

lazy from agent_toolkit._common import shell_cwd
lazy from agent_toolkit._common.bash_invocations import extract_bash_invocations, is_unknown_word
lazy from agent_toolkit._common.heredocs import mask_heredoc_bodies
lazy from agent_toolkit._common.runtime_identity import RuntimeIdentity, co_author_trailer
lazy from agent_toolkit._common.shell_segments import ExecutionSegment
lazy from agent_toolkit._common.shell_tokens import is_atk_command, strip_redirections
lazy from agent_toolkit._hooks.notice import _WARN_TAG
lazy from agent_toolkit._hooks.pretooluse.notices import _block_notice, _llm_notice

_GLOBAL_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-C",
        "-c",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--config-env",
        "--list-cmds",
    }
)


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


_GLOBAL_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--no-pager",
        "-p",
        "--paginate",
        "--bare",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--glob-pathspecs",
        "--noglob-pathspecs",
        "--icase-pathspecs",
        "--no-optional-locks",
        "--exec-path",
        "--html-path",
        "--man-path",
        "--info-path",
        "--help",
        "--version",
    }
)


def git_subcommand_tokens(segment: ExecutionSegment) -> tuple[str, tuple[str, ...]] | None:
    """`git`と専用commit起動のサブコマンド名・引数を返す。"""
    if not segment.resolved or not segment.tokens:
        return None
    tokens = segment.tokens
    # 専用起動でも通常commitと同じ検査へ渡す。任意の登録scriptは展開しない。
    if is_atk_command(tokens) and tokens[1:4] == ("run-script", "commit-with-timeout", "--"):
        arguments = tokens[4:]
        if len(arguments) >= 3 and arguments[0] == "--timeout" and arguments[2] == "--":
            return "commit", arguments[3:]
        if len(arguments) >= 2 and arguments[0].startswith("--timeout=") and arguments[1] == "--":
            return "commit", arguments[2:]
        return None
    if pathlib.PurePath(tokens[0]).name != "git":
        return None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            return token, tuple(tokens[index + 1 :])
        if token in _GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token in _GLOBAL_OPTIONS_WITHOUT_VALUE or "=" in token:
            index += 1
            continue
        return None
    return None


# 終了証跡など既存の内部importを保持し、新しい消費側は公開名を使う。
_git_subcommand_tokens = git_subcommand_tokens


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
    """commitの値とオプションを分け、結合短形式を分離形と同じ順序で返す。

    値として消費した`--`や`--help`を再解釈しない。実際の`--`以後のpathspecは返さない。
    """
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
                    # 省略可能な値は同じ語の残りだけを消費する。
                    yield option, token[position + 1 :] or None
                    break
                yield option, None
        else:
            yield token, None


def _git_commit_messages(command: str, *, cwd: str = "") -> Iterable[str]:
    """指定内容を確定できる通常commitのメッセージを返す。

    メッセージは`-m`・`--message`に加え、`-F -`・`--file=-`へ同じコマンドのheredocで渡した本文と、
    `-F <path>`・`--file=<path>`で指定したhookの時点で存在する通常ファイルからも読む。
    エージェントのcommitの多くは`-F -`のheredocで作成されるため、この形を読めないと帰属行を確かめないまま通す。
    相対パスは呼出しごとの実行位置と`git -C`で解決し、実行位置が不明なら本文を読まない。
    heredocを一意に対応付けられない場合、存在しないファイル、`-C`・`-c`・`--reuse-message`・`--reedit-message`、
    `--amend --no-edit`、fixup、squashの場合は判定しない。
    """
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        parsed = git_subcommand_tokens(invocation.segment)
        if parsed is None or parsed[0] != "commit":
            continue
        arguments = parsed[1]
        # `--`より後ろはpathspecであり、未知でもメッセージの指定に作用しない。それより前の未知の語は
        # メッセージやその入力ファイルを変える可能性があるため、検証済みと推定しない。
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
                    # 専用起動にGitのglobal optionはなく、現在のcwdからcommitする。
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


def _unknown_global_words(prefix: Sequence[str]) -> bool | None:
    """`commit`より前の語の未知語を分類する。

    実行位置だけを変える大域オプションの値にだけ未知語があれば真、未知語が無ければ偽を返す。
    メッセージに作用しうる語に未知語があれば、検査の対象から外すため`None`を返す。
    展開を含む語は語の全体が未知語になるため、`--git-dir=$R`の形はオプション名を確定できず`None`になる。
    """
    location_unknown = False
    words = iter(prefix)
    for word in words:
        if word in _GLOBAL_OPTIONS_LOCATING_REPOSITORY:
            location_unknown = is_unknown_word(next(words, "")) or location_unknown
        elif is_unknown_word(word):
            return None
    return location_unknown


_WORK_ITEM_FILENAME_PATTERN = re.compile(r"(?<![0-9A-Za-z_-])\d{8}-\d{6}-\d{3}\.md(?![0-9A-Za-z_.-])")


_COMMIT_BLOCK_RERUN = (
    "このBashコマンドは全体が実行されていない（`git add`など、連結した他のコマンドも実行されていない）。"
    "同じコマンド全体へ修正を加えて再実行せよ。"
)


"""commitの遮断の是正へ添える、未実行の範囲と再実行の範囲。commitだけを直して前段を外す再実行を防ぐ。"""


def _git_commit_format_violations(messages: Iterable[str]) -> list[tuple[str, str]]:
    """通常commitのWI名記載と件名・本文間の空行欠落を、違反と是正の組で返す。"""
    violations: list[tuple[str, str]] = []
    for message in messages:
        if match := _WORK_ITEM_FILENAME_PATTERN.search(message):
            violations.append(
                (
                    f"公開するコミットメッセージに作業項目のファイル名（`{match.group()}`）が含まれている。",
                    "公開するコミットへ作業項目のファイル名を書くな。実装コミットとAWIの対応は"
                    "`atk run-script plan-progress`が記録する対応記録ファイル（`<stem>.wi-commits.jsonl`）へ残し、"
                    "メッセージから作業項目のファイル名を含む行を除け。",
                )
            )
        lines = message.splitlines()
        if len(lines) > 1 and lines[1].strip():
            violations.append(("`commit`の件名と本文の間の空行が欠けている。", "件名の次に空行を1行入れよ。"))
    return violations


def _git_commit_attribution_violations(
    messages: Iterable[str],
    identity: RuntimeIdentity | None,
    *,
    attribution_disabled: bool = False,
) -> list[tuple[str, str]]:
    """通常commitが観測identityのtrailerをちょうど1件持つか確かめ、違反と是正の組を返す。"""
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
                    "観測したモデルに対応する上記の`Co-Authored-By:`を1行だけ入れよ。",
                )
            ]
    return []


def _git_commit_block(
    messages: Iterable[str],
    identity: RuntimeIdentity | None,
    *,
    attribution_disabled: bool = False,
) -> str | None:
    """通常commitのメッセージに対する全ての検査の違反を、1回の遮断の通知へ並べて返す。違反が無ければ`None`を返す。

    最初の違反だけを返すと、受け取ったエージェントは残りの違反で再び遮断される。
    """
    messages = tuple(messages)
    violations = list(dict.fromkeys(_git_commit_format_violations(messages)))
    violations += _git_commit_attribution_violations(messages, identity, attribution_disabled=attribution_disabled)
    if not violations:
        return None
    if len(violations) == 1:
        return _block_notice(violations[0][0], fix=violations[0][1] + _COMMIT_BLOCK_RERUN)
    return _block_notice(
        f"`commit`のメッセージに違反が{len(violations)}件ある。"
        + "".join(f"（{number}）{reason}" for number, (reason, _fix) in enumerate(violations, 1)),
        fix="".join(f"（{number}）{fix}" for number, (_reason, fix) in enumerate(violations, 1)) + _COMMIT_BLOCK_RERUN,
    )


def _commit_heredoc_body(command: str) -> str | None:
    """`git commit`を宣言した行のheredoc本文を返す。対応するheredocが1件に定まらない場合は`None`を返す。

    `mask_heredoc_bodies`が本文を空白へ、終端行の先頭を`;`へ置き換えた結果と元の行を比べて本文の範囲を求める。
    """
    original_lines = command.split("\n")
    masked_lines = mask_heredoc_bodies(command).split("\n")
    bodies: list[str] = []
    for terminator, (original, masked) in enumerate(zip(original_lines, masked_lines, strict=True)):
        if original == masked or not masked.startswith(";") or masked[1:].strip():
            continue
        # 本文は宣言行の直後から終了行の前までに連続し、置換後は空白だけの行になる。宣言行は空白だけにならない。
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
    """`git commit -F <path>`が読むファイルの本文を返す。解決できないか通常ファイルでない場合は`None`を返す。"""
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
    """通常modeの単一revision検証へ渡す通常revisionを返す。

    `--`以降はパスとして扱い、revisionに数えない。リダイレクトの演算子と対象も数えない。
    検証指定より前のrevisionとrangeは数えず、先頭の専用modeは対象外とする。
    """
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
                "`git rev-parse <元のオプション> <revision>`を個別に実行して入力と出力の対応を保て。",
            )
    return None
