r"""PreToolUse統合フックのBashコマンドに対する実害保護と手順支援。"""

from __future__ import annotations

import os
import re
import sys
lazy from collections.abc import Sequence

lazy from agent_toolkit._common import wait_schedule
lazy from agent_toolkit._common.bash_invocations import (
    extract_bash_invocations,
    heredoc_command_substitutions,
    is_read_predicate,
)
lazy from agent_toolkit._common.runtime_identity import RuntimeIdentity, co_author_trailer
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import is_atk_command
lazy from agent_toolkit._hooks.notice import _WARN_TAG
lazy from agent_toolkit._hooks.pretooluse.notices import _block_notice, _llm_notice
lazy from agent_toolkit._hooks.pretooluse.shell_checks_container import (
    _warn_container_root_bind_mount,
)
lazy from agent_toolkit._hooks.pretooluse.shell_checks_git import (
    _GIT_GREP_PATTERN_FILE_OPTIONS,
    _GIT_GREP_PATTERN_OPTIONS,
    _GLOBAL_OPTIONS_LOCATING_REPOSITORY,
    _GLOBAL_OPTIONS_POSSIBLY_AFFECTING_MESSAGE,
    _GLOBAL_OPTIONS_WITH_VALUE,
    _git_commit_block,
    _git_commit_messages,
    _git_subcommand_tokens,
    _warn_git_rev_parse_short_multiple,
    git_commit_options,
    git_subcommand_tokens,
)
lazy from agent_toolkit._hooks.pretooluse.shell_checks_process import (
    _check_bash_process_kill_by_pattern,
    _check_bash_self_matching_pgrep,
)
lazy from agent_toolkit._hooks.pretooluse.shell_checks_search import (
    _GIT_GREP_VALUED_OPTIONS,
    _check_bash_option_after_terminator,
)

__all__ = [
    "RuntimeIdentity",
    "_GIT_GREP_PATTERN_FILE_OPTIONS",
    "_GIT_GREP_PATTERN_OPTIONS",
    "_GIT_GREP_VALUED_OPTIONS",
    "_GLOBAL_OPTIONS_LOCATING_REPOSITORY",
    "_GLOBAL_OPTIONS_POSSIBLY_AFFECTING_MESSAGE",
    "_GLOBAL_OPTIONS_WITH_VALUE",
    "_check_bash_option_after_terminator",
    "_check_bash_process_kill_by_pattern",
    "_check_bash_self_matching_pgrep",
    "_git_commit_block",
    "_git_commit_messages",
    "_git_subcommand_tokens",
    "_warn_container_root_bind_mount",
    "_warn_git_rev_parse_short_multiple",
    "co_author_trailer",
    "git_commit_options",
    "git_subcommand_tokens",
]


def _check_read_predicate_stderr_discard(command: str) -> str | None:
    """読取の診断破棄を警告する。再取得で診断を復元できるため実行を続ける。"""
    for invocation in extract_bash_invocations(command):
        if is_read_predicate(invocation.segment.tokens) and invocation.outputs[1] == "/dev/null":
            return _llm_notice(
                "読取・存在確認コマンドの標準エラーを`/dev/null`へ捨てるため、入力・パス・権限の異常の診断を失う。",
                tag=_WARN_TAG,
                subject=command,
                removable_cause=True,
                fix="標準エラーの破棄を外して再実行せよ。出力量は標準出力の対象限定・保存で抑え、"
                "標準エラーは呼び出し元へ残すか別ファイルへ保存し、別の呼び出しで読め。",
            )
    return None


_ATK_PROCESS_LOOP_CONTROL_SUBCOMMANDS = frozenset({"abort", "abort-cancel", "status", "instruct", "instruct-cancel"})


"""`atk wi process-loop`のうち常駐を起動せずに結果を返すサブコマンド。"""


def _is_atk_resident_display(tokens: tuple[str, ...]) -> bool:
    """常駐と追従の表示（`atk serve`、`atk wi process-loop`の常駐、`atk agents logs --follow`）の起動かを返す。

    これらは`atk`の長い出力の保存の対象外で、背景で起動するときは出力をログへ移す必要があるため、
    リダイレクトの遮断から除く。
    """
    arguments = tokens[1:]
    if arguments[:1] == ("serve",):
        return True
    if arguments[:2] == ("wi", "process-loop"):
        return not any(token in _ATK_PROCESS_LOOP_CONTROL_SUBCOMMANDS for token in arguments[2:])
    return arguments[:2] == ("agents", "logs") and any(
        token == "--follow" or token.startswith("--follow=") for token in arguments[2:]
    )


def _check_bash_atk_output_loss(command: str) -> str | None:
    """atkの出力接続とwaitの背景化へ、結果を受領する手段を警告する。

    `atk`はエージェント環境で短い出力を直接表示し、長い出力を自ら保存して保存先を示す。
    パイプかリダイレクトを介すと終了コードと警告を同じ呼び出しで観測できず、保存先の行が後段かファイルへ渡り、
    結果を得るまでに再読の呼び出しが増える。出力の接続先はファイル（`/dev/null`を含む）、別のファイル記述子、
    未確定のパスを区別せず、標準出力と標準エラーの最終的な接続先が呼び出し元から引き継いだ1と2から変わったかだけで判定する。
    常駐と追従の表示は保存の対象外のため、リダイレクトの判定から除く。
    引数のリテラルやheredoc本文、未知の実行位置は判定せず、ホストが管理する背景実行は通す。
    保存結果や状態の再照会で受領を回復できる手順支援のため、警告とする。
    区切り語を引用しないheredocの本文にある置換は、`_check_bash_unquoted_heredoc_substitution`が実行前に遮断する。
    """
    for invocation in extract_bash_invocations(command):
        tokens = invocation.segment.tokens
        if not invocation.segment.resolved or not is_atk_command(tokens):
            continue
        is_wait = tokens[1:3] == ("agents", "wait")
        causes: list[str] = []
        if is_wait and invocation.background:
            causes.append("シェルの`&`による背景化")
        if invocation.output_pipe:
            causes.append("atkから後段へ出力を渡すパイプ")
        elif invocation.output_redirected and not _is_atk_resident_display(tokens):
            causes.append("出力のリダイレクト")
        if not causes:
            continue
        label = "atk agents wait" if is_wait else "atk"
        waiting = (
            "`Claude Code`で背景で待つ場合は`Bash`の`run_in_background`を使い、返されたタスクの識別子で結果を受領せよ。"
            if "CLAUDECODE" in os.environ and not wait_schedule.background_tasks_disabled()
            else "待機は単独の前景実行とし、CLI自身の期限で返る結果と終了コードを同じターン内に回収せよ。"
            "`Claude Code`の`Bash`では`timeout=600000`を指定し、未終了なら同じコマンドを再発行せよ。"
        )
        return _llm_notice(
            f"`{label}`の結果と終了状態を直接受領できない入力（{'、'.join(causes)}）を検出した。",
            tag=_WARN_TAG,
            subject=command,
            removable_cause=True,
            fix=(
                "`atk`はパイプとリダイレクトを外して単独で発行し、長い出力は`atk`が示す保存先"
                "（標準出力の`保存先:`と標準エラーの`標準エラー保存先:`）から読め。"
                "複数対象は対応するコマンドへまとめて渡し、形は公開ヘルプで確認せよ"
                "（例: `atk config get execute_model chores_model`）。出力量は対象限定や要約の公開入力で抑えよ。"
                "`atk agents wait`は`&`も外して単独で発行せよ。"
                f"{waiting}"
                "保存した本文の選別は別の呼び出しで行え。"
            ),
        )
    return None


_HEREDOC_SUBSTITUTION_DISPLAY_LIMIT = 120


"""通知へ載せる置換1件あたりの最大文字数。置換の範囲を特定できる長さに収め、文脈の占有を抑える。"""


def _check_bash_unquoted_heredoc_substitution(command: str) -> str | None:
    """区切り語を引用しないheredocの本文にある、エスケープされていないコマンド置換を遮断する。

    bashは区切り語を引用しないheredoc（`<<EOF`・`<<-EOF`）の本文を展開し、本文のバッククォートと`$(...)`の中の
    コマンドを実行する。Markdownのコード表記とシェルの置換は同じ記号であり、書き手が置換に気付かないまま
    外部への保存、プロセスの終了、ファイルの上書きなど復元できない結果が残るため、実行前に遮断する。
    判定はコマンド文字列だけから確定し、遮断された主体は区切り語の引用、事前の変数代入またはエスケープへ
    書き直して同じターンで再実行できる。遮断とする根拠は
    `agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`「遮断と警告の選択」にある。
    本文の範囲と展開の有無は`agent_toolkit._common.heredocs.heredoc_bodies`の1つの定義から得る。
    """
    found = heredoc_command_substitutions(command)
    if not found:
        return None
    shown = "、".join(
        f"「{text if len(text) <= _HEREDOC_SUBSTITUTION_DISPLAY_LIMIT else text[:_HEREDOC_SUBSTITUTION_DISPLAY_LIMIT] + '…'}」"
        for text in found
    )
    return _block_notice(
        f"blocked: 区切り語を引用しないヒアドキュメントの本文にコマンド置換（{shown}）がある。"
        "`bash`はこの本文を展開し、置換の中のコマンドを実行する。",
        fix=(
            "本文をそのまま書く場合は区切り語を引用せよ（`<<'EOF'`）。"
            "置換の結果を本文へ入れる場合は、事前に変数へ代入して本文では`$VAR`で参照せよ。"
            "リテラルのバッククォートと`$(`は直前に`\\`を置いてエスケープせよ（「\\`」・「\\$(」）。"
        ),
    )


_SHELL_ASSIGNMENT_PATTERN = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.DOTALL)


_DRIVE_LETTER_PATH_PATTERN = re.compile(r"[A-Za-z]:[/\\]")


_PATH_ELEMENT_VARIABLE_PATTERN = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")


def _segment_assignments(raw_tokens: Sequence[str]) -> list[tuple[str, str]]:
    """区間の元トークン列から、先頭の代入の並びと`export`の引数にある代入を順に返す。

    単独の代入文とコマンド前置の代入はいずれも先頭の`KEY=VALUE`の並びに現れる。
    `export`の後の`KEY=VALUE`もシェル変数への代入として扱う。
    """
    assignments: list[tuple[str, str]] = []
    index = 0
    while index < len(raw_tokens) and (match := _SHELL_ASSIGNMENT_PATTERN.match(raw_tokens[index])):
        assignments.append((match.group(1), match.group(2)))
        index += 1
    if index < len(raw_tokens) and raw_tokens[index] == "export":
        for token in raw_tokens[index + 1 :]:
            if match := _SHELL_ASSIGNMENT_PATTERN.match(token):
                assignments.append((match.group(1), match.group(2)))
    return assignments


def _drive_letter_path_elements(value: str, assigned: dict[str, str]) -> list[str]:
    r"""PATHの値のうち、ドライブ文字形式で始まる要素を説明用の文字列で返す。

    要素の先頭（値の先頭かコロンの直後）がリテラルの`C:/`・`C:\\`形式である要素と、
    同じコマンドの中でそれより前にドライブ文字形式の値を代入した変数の展開で始まる要素を対象とする。
    """
    found: list[str] = []
    for position in range(len(value)):
        if position > 0 and value[position - 1] != ":":
            continue
        rest = value[position:]
        if _DRIVE_LETTER_PATH_PATTERN.match(rest):
            end = rest.find(":", 2)
            found.append(rest if end < 0 else rest[:end])
            continue
        variable = _PATH_ELEMENT_VARIABLE_PATTERN.match(rest)
        if variable is None:
            continue
        name = variable.group(1) or variable.group(2)
        assigned_value = assigned.get(name)
        if assigned_value is not None and _DRIVE_LETTER_PATH_PATTERN.match(assigned_value):
            found.append(f"${name}（代入値: {assigned_value}）")
    return found


def _warn_windows_drive_letter_path(command: str, *, is_codex: bool) -> str | None:
    """WindowsのGit BashでPATHへドライブ文字形式の要素を加えるコマンドへ警告本文を返す。

    Git Bash（MSYS2）のPATHはコロン区切りでドライブ文字形式を変換しないため、`C:/x`は`C`と`/x`の2要素に分かれ、
    意図したディレクトリが検索されない。その結果を根拠に結論を下す前に気付けるよう、実行の直前に判定する。

    判定の結論は警告とする。根拠は`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`
    「遮断と警告の選択」にある。誤ったPATHはコマンドを失敗させずに誤った結果を返し、
    その結果が誤った結論の根拠になる。判定はコマンド文字列から機械的に確定でき、実行を止めないため誤検出の費用も小さい。
    影響はそのコマンドのプロセス環境に閉じ、正しい形式で再実行すれば是正できるため遮断はしない。
    反復しても母集団の欠落や工程の停止を招かないため、反復時の昇格もしない。

    対象はWindows上のClaude CodeのBashツールに限る。CodexはPowerShell（PATHはセミコロン区切り）で
    シェルを実行し、Windows以外ではドライブ文字形式のパスが通常の操作で現れない。
    継承した環境変数の展開、コマンド置換および別のBash呼び出しで代入した変数は、値をhookの入力から
    確定できないため判定しない。
    """
    if sys.platform != "win32" or is_codex:
        return None
    assigned: dict[str, str] = {}
    found: list[str] = []
    for segment in extract_execution_segments(command):
        for name, value in _segment_assignments(segment.raw_tokens):
            if name == "PATH":
                found.extend(_drive_letter_path_elements(value, assigned))
            assigned[name] = value
    if not found:
        return None
    return _llm_notice(
        f"`PATH`へドライブ文字形式の要素（{'、'.join(found)}）を加えている。"
        "`Git Bash`の`PATH`はコロン区切りのため、ドライブ文字形式の要素は`C`と`/...`の2要素に分かれ、"
        "この`PATH`では意図したディレクトリが検索対象にならない。"
        "`PATH`に依存する結果は、修正した指定で実行して確かめよ。",
        tag=_WARN_TAG,
        fix="`/c/Users/...`の形で書くか、`\"$(cygpath -u '<Windows形式のパス>')\"`で変換した値を`PATH`へ加えて再実行せよ。",
        removable_cause=True,
        subject=command,
    )
