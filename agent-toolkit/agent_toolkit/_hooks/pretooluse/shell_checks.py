# ruff: noqa: F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
r"""Claude Code plugin agent-toolkit: PreToolUse統合フック。

任意ツールの実行前に以下のチェックを順に実行する。
block系checkは1プロセスで直列実行し、最初の違反でexit 2する。
warn種別のcheckはstdoutの`hookSpecificOutput.additionalContext`へ警告を載せつつ処理を継続する
（exit 0で終了したフックのstderrはコーディングエージェントへ届かないため）。
auto-fix種別のcheckは`updatedInput`でツール入力を自動書き換えする。
関連チェック項目は初回で一括開示する（反復サイクル防止のため）。

統合しているチェック:

任意ツール:

- メインエージェント応答の日本語文字比率が閾値未満の場合の警告 (warn)
- ユーザーが直接読む質問本文・計画本文の文字化け、他言語文字、口語表現の検査 (block)
- plan-modeスキル未起動のままのplan file編集（Write/Edit/MultiEdit）の警告 (warn)
- plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続の警告 (warn)

固定見出し（新形式と旧形式の互換別名）と固定表の構造、素材表・要求表・素材参照、
計画メタ情報の4項目と記法、計画単位のエージェント提案詳細表（5項目）を含む
フェンス整合、参照実在は
`agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`が担うため
本フックでは扱わない。

mcp__plugin_agent-toolkit_agents_server__start / start_explore / start_shell / send_message / kill:

- 委譲先へ渡す絶対`cwd`と`send_message`・`kill`のprompt/sessionの検査 (block)
- 全チェック通過時の強制承認 (auto-approve)

wait:

- 既存sessionの観測として通過 (pass-through)

Bash:

- 多段シェルへのコード文字列と`.env`内容出力の遮断 (block)
- `python`の`-c`へ渡す複数文のコードと構文として成立しないコードの遮断 (block)
- 単純な明示パスの不存在と`atk`未対応オプションの遮断 (block)
- 単純な`git grep`後方オプションの受理位置への移動 (auto-fix)
- 長い固定`sleep`の後に別コマンドを連結する前景待機の検出 (warn/block)
- 高容量のユーザー領域を無限定に再帰検索する実行位置の検出 (warn)
- 高容量のユーザー領域を対象限定なしに走査する`find`・`ls -R`の検出 (warn)
- 走査範囲を限定しないファイルシステムの根からの`find`の遮断 (block)
- 検証コマンド又は保存本文を返すコマンドの出力を`tail`・`head`で切り詰める指定の検出 (warn/block)
- 切り詰め直後の`$?`が検証コマンドの終了状態を隠す指定の検出 (warn)
- パターン一致によるプロセス終了（`pkill`・`killall`等）の遮断 (block)
- git amend / rebase直前に`git log`未確認のブロック (block)
- git push実行時のamend後dirty状態のブロック (block)
- 非Pythonプロジェクトでの`uv run python <path>`形式起動の補正又は警告 (auto-fix/warn)
- `git commit`未検証警告 (warn)
- `agent-toolkit/`配下のコミット時のversion bump漏れ警告 (warn)
- `git log --decorate`の自動付与 (auto-fix)
- `codex exec`の未決事項念押し (warn)
- 一括ステージ実行時の自セッション編集対象外ファイル警告 (warn)

Skill:

- `agent-toolkit:plan-mode`起動時の計画単位の状態リセット (side-effect)

TaskStop:

- 自セッションの所有記録又は停滞検知完了記録に一致しない対象の遮断 (block)

Read / Write / Edit / MultiEdit / apply_patch:

- 文字化け（U+FFFD）検出 (warn。ユーザーが直接読む本文はblock)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (warn)
- lockfile / 生成物ディレクトリの直接編集 (warn)
- `.env`系のReadとシークレット・鍵ファイルの直接編集 (block)
- manifestファイルの手編集 (warn)
- ホームディレクトリの絶対パス混入 (warn)
- 口語的な日本語表現の混入 (warn)
- 「Xを根拠にYしない」「Xを理由にYしない」形式のメタ規範文言の増加 (warn)
- .md規範文書のWrite/Edit/MultiEditでfrontmatter同期注記の本体該当語句の実在検証warn (warn)
- 日本語を含む書き込み文字列へのハングル・キリル文字の混入 (warn。ユーザーが直接読む本文はblock)
- .md規範文書の本文中にある他ファイルの節参照の実在検証 (warn)

各チェックの詳細仕様（対象パターン・エラー文言・例外条件）は対応する実装関数のdocstringを参照する。
block系checkの検査対象は「新規に書き込まれる側」（変更後断片）を基本とする。
変更前断片は既存内容の修正・削除を妨げないため単独では検査対象としない。

ホスト差の扱い:

- 編集入力は`_hook_tool_input`が共通の操作記録へ正規化し、検査本体はホストを区別しない
- 非空文字列の`turn_id`をCodex判定の正本とし、payload読込直後に一度だけ判定する
- Bashの終了コードを取得できないCodexでは、成功状態を前提とするamend・rebase、push、commitの各検査を実行しない
- 外部ファイル解決を伴うfrontmatter同期注記・本文節参照の検査と、PowerShellの改行検査はClaude入力へ限定する
- 警告は1つの`hookSpecificOutput.additionalContext`へ結合し、遮断は最初の違反をexit 2とstderrで返す
"""

from __future__ import annotations

import ast
import dataclasses
import datetime
import importlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING

from pyfltr.colloquial import check as _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position

from agent_toolkit._atk import managed_temp  # noqa: E402  # pylint: disable=wrong-import-position,import-error

from agent_toolkit._common.file_lock import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    locked_rotate_and_append as _locked_rotate_and_append,
)
from agent_toolkit._git import status as _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable=wrong-import-position
from agent_toolkit._hooks import (
    bash_command_parser as _bash_command_parser,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    response_language_check as _response_language_check,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    scratchpad_path as _scratchpad_path,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    tool_input as _hook_tool_input,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import transcript as _transcript  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._hooks.bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _GLOBAL_OPTIONS_WITH_VALUE,
    _GLOBAL_OPTIONS_WITHOUT_VALUE,
    CwdResolution,
    GitEvent,
    QuotingScanner,
    extract_git_events,
    resolve_cwd_change,
    resolve_execution_segment,
    shell_redirection_targets,
    split_bash_segments,
    without_shell_redirections,
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import _WARN_TAG  # noqa: E402

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter  # noqa: E402
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    observed_atk_help_paths,
    read_state,
    record_atk_help_paths,
    update_state,
)
from agent_toolkit._plan import structure as _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._plan.locations import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_plan_adjunct_file,
    is_plan_component_file,
)
import contextlib

if TYPE_CHECKING:
    from agent_toolkit._hooks.pretooluse.dispatch import (
        _ExecutionSegment,
        _extract_execution_pipelines,
        _extract_execution_segments,
        _has_uv_terminal_option,
        _is_python_token,
    )
    from agent_toolkit._hooks.pretooluse.notices import _block_notice, _llm_notice


# --- Bash: uv run python <path>形式の起動ブロック ---

# 副作用の理由:
# cwd又はその祖先で最初に見つかるpyproject.tomlが[tool.uv]のみで
# [project]セクションを持たない場合、`uv run python <path>`は当該ディレクトリを
# プロジェクト解決対象として扱い`.venv`と`uv.lock`を生成する（uvの仕様）。
# エージェントがPEP 723スクリプトを誤って`uv run python <path>`形式で起動する
# 事故を予防的にblockする。
#
# 判定の優先順位:
#
# 1. `uv run`と`python`の間（uv run自身のオプション位置）に`--script`または
#    `--no-project`が現れる場合は許容する（cwdの依存解決を行わないため副作用なし）。
# 2. cwd変更経路（Bashの`cd` / `pushd`先行・`uv --directory` / `uv --project`）
#    の実効cwdが解決済みで、cwd又はその祖先で最初に見つかるpyproject.tomlが
#    [project]セクションを持つPythonプロジェクトの場合は許容する
#    （`uv run python -c '...'`等の正規利用を妨げない）。
# 3. それ以外はblockする。
#
# cwd変更経路の引数にシェル展開が含まれる場合は、実効cwdとPythonプロジェクトの
# 種別を静的に確定できないため、プロジェクト判定を行わずblock側に倒す。
# 環境変数経由のcwd / project切り替え（UV_WORKING_DIR / UV_PROJECT）は
# 利用頻度が低く実装コストに見合わないため対応スコープ外とする。

_UV_RUN_PYTHON_BLOCK_MSG = (
    "blocked: `python`トークンの前に`--script`も`--no-project`も指定しない`uv run python`呼び出しである"
    "（`python`の後にパスが続く場合も`-c`が続く場合も同じ）。"
    "Pythonプロジェクトでない場所では、`uv`がカレントディレクトリをプロジェクトとして扱い、"
    "副作用として`.venv`と`uv.lock`を生成する。プロジェクトに依存しない形を明示しない限り安全に続行できない。"
)

_UV_RUN_PYTHON_FIX = (
    "agent-toolkit配下の入口は`uv run --project <plugin root> --locked --no-default-groups <パス>`を使う。"
    "`agent-toolkit/scripts/`に残すリモート補助処理とその他のPEP 723スクリプトは"
    "`uv run --script <パス>`を使うか、実行可能なshebangを直接呼び出す。"
    "カレントディレクトリのプロジェクト解決を省く場合は`uv run --no-project python ...`を使う。"
    "いずれでもない場合は、カレントディレクトリまたはその祖先で最初に見つかる`pyproject.toml`が"
    "`[project]`節を持つディレクトリで実行する。静的に解決できる`cd`の遷移先は実効作業ディレクトリとして評価する。"
    "作業ディレクトリの変更に未解決のシェル展開があると、プロジェクト種別を確認できないため遮断する。"
)

_SIMPLE_SCRIPT_SUFFIXES = frozenset({".py", ".pyw"})


def _rewrite_simple_uv_script(command: str, cwd: str) -> str | None:
    """安全に一意変換できる単純な`uv run python <script>`を`--script`形へ直す。"""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if any(token in {"|", "|&", ";", "&&", "||", "&"} or _SHELL_REDIRECTION_PATTERN.match(token) for token in tokens):
        return None
    info = _parse_uv_run_python(tokens)
    if info is None or info[0] or info[1]:
        return None
    python_index = next((index for index, token in enumerate(tokens) if _is_python_token(token)), None)
    if python_index is None or python_index + 1 >= len(tokens):
        return None
    script = tokens[python_index + 1]
    if script.startswith("-") or pathlib.PurePath(script).suffix.lower() not in _SIMPLE_SCRIPT_SUFFIXES:
        return None
    if cwd and _cwd_in_python_project(cwd):
        return None
    return shlex.join([*tokens[:python_index], "--script", script, *tokens[python_index + 2 :]])


_GIT_GREP_FLAGS = frozenset(
    {
        "-F",
        "-H",
        "-I",
        "-P",
        "-E",
        "-i",
        "-l",
        "-n",
        "-q",
        "-w",
        "--fixed-strings",
        "--perl-regexp",
        "--extended-regexp",
        "--ignore-case",
        "--files-with-matches",
        "--line-number",
        "--quiet",
        "--word-regexp",
    }
)
_GIT_GREP_VALUED_OPTIONS = frozenset(
    {"-A", "-B", "-C", "-m", "--after-context", "--before-context", "--context", "--max-count"}
)


def _rewrite_simple_git_grep(command: str) -> str | None:
    """単純な`git grep`でパターン後方にある既知オプションだけを前方へ移す。"""
    masked = _bash_command_parser.mask_heredoc_bodies(command)
    if any(operator in masked for operator in ("|", ";", "&&", "||", "\n")):
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(tokens) < 4 or tokens[:2] != ["git", "grep"]:
        return None
    separator_index = tokens.index("--") if "--" in tokens else len(tokens)
    options_and_pattern = tokens[:separator_index]
    pathspec = tokens[separator_index:]
    leading: list[str] = []
    index = 2
    while index < len(options_and_pattern):
        token = options_and_pattern[index]
        option_name = token.split("=", 1)[0]
        if token in _GIT_GREP_FLAGS:
            leading.append(token)
        elif option_name in _GIT_GREP_VALUED_OPTIONS:
            leading.append(token)
            if "=" not in token:
                if index + 1 >= len(options_and_pattern):
                    return None
                index += 1
                leading.append(options_and_pattern[index])
        else:
            break
        index += 1
    if index >= len(options_and_pattern):
        return None
    pattern = options_and_pattern[index]
    if pattern.startswith("-") or any(character in pattern for character in "$`"):
        return None
    moved: list[str] = []
    rest: list[str] = []
    index += 1
    while index < len(options_and_pattern):
        token = options_and_pattern[index]
        option_name = token.split("=", 1)[0]
        if token in _GIT_GREP_FLAGS:
            moved.append(token)
        elif option_name in _GIT_GREP_VALUED_OPTIONS:
            if "=" in token:
                moved.append(token)
            elif index + 1 < len(options_and_pattern) and not options_and_pattern[index + 1].startswith("-"):
                moved.extend((token, options_and_pattern[index + 1]))
                index += 1
            else:
                return None
        elif token.startswith("-"):
            return None
        else:
            rest.append(token)
        index += 1
    if not moved:
        return None
    return shlex.join(["git", "grep", *leading, *moved, pattern, *rest, *pathspec])


def _single_unquoted_pipe_index(masked: str) -> int | None:
    """引用の外側に単独のパイプ演算子がちょうど1つある場合に、その位置を返す。

    `|&`・`||`・`;`・`&&`・改行のいずれかが引用の外側にある入力と、引用の外側のパイプが
    1つでない入力はNoneを返す。引用が閉じない入力もNoneを返す。
    入力はheredoc本文をマスクした文字列とし、当該マスクは文字位置を保つため、
    返す位置は元のコマンド文字列へそのまま適用できる。
    """
    positions: list[int] = []
    nested = _bash_command_parser.nested_shell_positions(masked)
    scanner = QuotingScanner(masked)
    while scanner.index < len(masked):
        if scanner.consume_quoted():
            continue
        index = scanner.index
        if index in nested:
            scanner.index += 1
            continue
        char = masked[index]
        if char in {"'", '"'}:
            scanner.enter_quote(char)
            continue
        if char == "\n" or char == ";" or masked.startswith("&&", index):
            return None
        if masked.startswith("||", index) or masked.startswith("|&", index):
            return None
        if char == "|":
            positions.append(index)
        scanner.index += 1
    if scanner.quote is not None or len(positions) != 1:
        return None
    return positions[0]


# --- 外部コマンドと`atk`が共有する受理形式の走査 ---


def _attached_short_value_option(token: str, valued: Iterable[str]) -> str | None:
    """値を密着させた短縮オプションの形であれば、当該オプション名を返す。

    `-A14`のように値を空白なしで連結した形は対象コマンドが受理する1つのトークンである。
    短縮オプションの連結として1文字ずつ照合すると、値の各文字が受理集合に無いという判定になる。
    """
    if not token.startswith("-") or token.startswith("--"):
        return None
    return next(
        (
            option
            for option in valued
            if option.startswith("-") and not option.startswith("--") and token.startswith(option) and len(token) > len(option)
        ),
        None,
    )


_NEGATIVE_NUMBER_PATTERN = re.compile(r"-\d+(?:\.\d+)?")


@dataclasses.dataclass(frozen=True)
class _OptionScan:
    """受理形式の走査結果。"""

    unknown_option: str | None
    """受理集合のいずれにも当たらない最初のオプション。当たるものが無い場合はNone。"""

    positionals: tuple[str, ...]
    """位置引数として扱ったトークン。"""

    ambiguous_option: str | None = None
    """値付き短縮オプションとフラグ連結の両方に解釈できる最初のトークン。"""


def _is_accepted_option_form(
    token: str,
    flags: frozenset[str],
    valued: frozenset[str],
    *,
    accepts_long_negation: bool,
) -> bool:
    """受理集合と完全一致しないトークンが、受理される既知の記法かを返す。

    対象は、値を密着させた短縮オプション、単独フラグの連結、および`--no-`接頭辞の否定形とする。
    """
    if _attached_short_value_option(token, valued) is not None:
        return True
    if token.startswith("-") and not token.startswith("--") and len(token) > 2:
        return all(f"-{character}" in flags for character in token[1:])
    option_name = token.split("=", 1)[0]
    if not accepts_long_negation or not option_name.startswith("--no-"):
        return False
    return f"--{option_name.removeprefix('--no-')}" in flags | valued


def _scan_accepted_options(
    arguments: Sequence[str],
    flags: Iterable[str],
    valued: Iterable[str],
    *,
    accepts_long_negation: bool = False,
) -> _OptionScan:
    """引数列をオプションと位置引数へ分類し、受理しないオプションの有無を返す。

    対象コマンドの引数構文を判定の入力とするため、オプション終端`--`以降を位置引数として扱い、
    値を取るオプションの直後のトークンを当該オプションの値として扱う。
    この2つを判定の入力から除くと、オプション終端の後ろに置いた検索patternと値引数の位置のデータが
    受理しないオプションとして報告される。
    `-`は標準入力を指す操作対象であり、位置引数として扱う。
    負の数は値として渡されるため、受理しないオプションとして扱わない。
    `accepts_long_negation`は、肯定形を受理するコマンドが`--no-`接頭辞の否定形も受理する場合に指定する。
    """
    flag_set = frozenset(flags)
    valued_set = frozenset(valued)
    positionals: list[str] = []
    unknown: str | None = None
    ambiguous: str | None = None
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            positionals.extend(arguments[index + 1 :])
            break
        option_name = token.split("=", 1)[0]
        attached_value = _attached_short_value_option(token, valued_set)
        if attached_value is not None and all(f"-{character}" in flag_set for character in token[len(attached_value) :]):
            ambiguous = token
            break
        if token in flag_set or option_name in valued_set:
            if option_name in valued_set and "=" not in token:
                index += 1
        elif _is_accepted_option_form(token, flag_set, valued_set, accepts_long_negation=accepts_long_negation):
            pass
        elif token.startswith("-") and token != "-" and not _NEGATIVE_NUMBER_PATTERN.fullmatch(token):
            unknown = token
            break
        else:
            positionals.append(token)
        index += 1
    return _OptionScan(unknown, tuple(positionals), ambiguous)


def _shares_option_prefix(token: str, option: str) -> bool:
    """オプションの導入記号より長い共通接頭辞を持つかを返す。"""
    introducer = 2 if token.startswith("--") and option.startswith("--") else 1
    common = 0
    for left, right in zip(token, option, strict=False):
        if left != right:
            break
        common += 1
    return common > introducer


def _format_accepted_option_candidates(token: str, flags: Iterable[str], valued: Iterable[str]) -> str:
    """受理しないオプションの通知へ載せる、対象に近い受理オプションと対処を組み立てる。

    受理集合の全体は判定を変えないまま実行主体のコンテキストを占めるため、
    対象トークンとオプションの導入記号より長い共通接頭辞を持つものだけを列挙する。
    共通接頭辞を持つものが無い呼び出しでは、受理形式を確定する手段だけを示す。
    """
    candidates = sorted(option for option in set(flags) | set(valued) if _shares_option_prefix(token, option))
    if not candidates:
        return "対処: `--help`を単独で実行して受理形式を確定する。"
    return (
        "接頭辞が一致する受理オプション: "
        + ", ".join(candidates)
        + "\n対処: 上記のいずれかへ修正するか、`--help`を単独で実行して受理形式を確定する。"
    )


def _is_truncation_exempt_producer(producer: str) -> bool:
    """規範が切り詰め禁止の対象外と定める取得かを返す。

    ヘルプ表示は出力量が入力に依存せず上限を持ち、`atk`のサブコマンドは自身の標準出力の量を
    公開契約として制御する。`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」は
    この2つを切り詰め禁止の対象外と定めるため、補正も反復の計数も行わない。
    """
    try:
        tokens = shlex.split(producer, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    if pathlib.PurePosixPath(tokens[0]).name == "atk":
        return True
    return any(token in {"--help", "-h"} for token in tokens[1:])


def _split_simple_truncation(command: str) -> tuple[str, tuple[str, ...]] | None:
    """単純な1段パイプのうち後段が切り詰めコマンドである場合だけ分割する。

    パイプ演算子の判定と分割位置は引用を考慮した走査で求める。
    検索patternなどの引数の内側にあるパイプ文字を演算子として数えると、
    当該呼び出しが切り詰めの補正の対象から外れる。
    後段のトークン列をそのまま返すため、補正側は当該トークン列へ保存先を操作対象として渡し、
    補正前のコマンドが要求した範囲を同じ呼び出しの結果へ返せる。
    """
    masked = _bash_command_parser.mask_heredoc_bodies(command)
    pipe_index = _single_unquoted_pipe_index(masked)
    if pipe_index is None:
        return None
    producer = command[:pipe_index].strip()
    consumer = command[pipe_index + 1 :].strip()
    if _is_truncation_exempt_producer(producer):
        return None
    try:
        consumer_tokens = shlex.split(consumer, posix=True)
    except ValueError:
        return None
    if not producer or not consumer_tokens:
        return None
    name = pathlib.PurePosixPath(consumer_tokens[0]).name
    if name in {"head", "tail"}:
        return producer, tuple(consumer_tokens)
    if name in _GREP_COMMANDS and any(
        token == "-m" or token.startswith("-m") or token == "--max-count" or token.startswith("--max-count=")
        for token in consumer_tokens[1:]
    ):
        return producer, tuple(consumer_tokens)
    return None


_TRUNCATION_CONSUMER_VALUE_OPTIONS: frozenset[str] = frozenset(
    {"-n", "-c", "-m", "-e", "-f", "--lines", "--bytes", "--max-count", "--regexp", "--file"}
)
"""切り詰めconsumerのうち、直後のトークンを値として取るオプション。

値を密着させた形（`-n5`・`-m1`）と`=`で連結した形は同じトークンの内側に値を持つため、
走査は当該トークン1つだけを消費し、直後のトークンを値として扱わない。
"""
_TRUNCATION_CONSUMER_PATTERN_OPTIONS: frozenset[str] = frozenset({"-e", "-f", "--regexp", "--file"})
"""grep系のpatternを位置引数以外の場所で受け取るオプション。"""


def _truncation_consumer_operands(tokens: Sequence[str]) -> tuple[str, ...]:
    """切り詰めconsumerが既に持つ操作対象の位置引数を返す。

    grep系では先頭の非オプショントークンがpatternであり操作対象に当たらない。
    `-`と`/dev/stdin`は標準入力を指す操作対象であり、保存先を渡す形へ書き換えられないため対象に含める。
    """
    name = pathlib.PurePosixPath(tokens[0]).name
    pattern_pending = name in _GREP_COMMANDS
    operands: list[str] = []
    option_terminator = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if not option_terminator and token == "--":
            option_terminator = True
            index += 1
            continue
        if not option_terminator and token.startswith("-") and token != "-":
            option_name = token.split("=", 1)[0]
            if option_name in _TRUNCATION_CONSUMER_PATTERN_OPTIONS or _attached_short_value_option(
                token, _TRUNCATION_CONSUMER_PATTERN_OPTIONS
            ):
                pattern_pending = False
            if option_name in _TRUNCATION_CONSUMER_VALUE_OPTIONS and token == option_name:
                index += 2
                continue
            index += 1
            continue
        if pattern_pending:
            pattern_pending = False
            index += 1
            continue
        operands.append(token)
        index += 1
    return tuple(operands)


@dataclasses.dataclass(frozen=True)
class _TruncationFix:
    """切り詰めの補正が1つの直列区間へ適用した内容。

    通知本文が是正の対象を一意に示すため、検出した直列区間と当該区間で切り詰めと判定した
    コマンドの表記を保持する。
    """

    position: int
    """当該呼び出しの何番目の直列区間か。1から数える。"""

    segment: str
    """検出の対象とした直列区間のコマンド文字列。"""

    truncation_command: str
    """当該区間で切り詰めと判定したコマンドの表記。"""

    log_path: str
    """標準出力の保存先の絶対パス。"""

    stderr_merged: bool
    """保存先へ標準エラーも入るか。producerが`2>&1`を末尾に持つ場合に真とする。"""

    read_back: bool
    """補正後のコマンドが保存先からconsumerの要求範囲を読み戻すか。"""


_STDERR_DUPLICATION_SUFFIX = "2>&1"


def _autofix_bash_segment(
    command: str,
    cwd: str,
    session_id: str,
    *,
    inside_conditional: bool = False,
) -> tuple[str, list[str], _TruncationFix | None] | None:
    """1つの直列区間にある競合しない補正を適用する。

    戻り値の3つ目は、切り詰めを補正した場合の適用内容とする。`position`は呼び出し元が確定する。
    切り詰めの通知は呼び出し全体の構成に依存するため、本関数では組み立てず`_autofix_bash_command`が生成する。

    切り詰めの補正は、保存先を操作対象としてconsumerへ渡す読み戻しを加え、補正前のコマンドが
    要求した範囲を同じ呼び出しの結果へ返す。読み戻しを加える区間の保存先は上書きとする。
    consumerが操作対象を既に持ち読み戻しへ書き換えられない区間だけ、保存先を追記とする。
    追記は当該区間がループ本体で反復される場合に各反復の出力を残すが、読み戻しと併用すると
    consumerが累積した内容を読み、反復ごとの範囲を返さなくなる。
    保存先は補正1回ごとに一意であるため、追記でも別の呼び出しの内容は混ざらない。
    """
    truncation = _split_simple_truncation(command)
    producer = truncation[0] if truncation is not None else command
    rewritten = _rewrite_simple_uv_script(producer, cwd) or producer
    notices: list[str] = []
    if rewritten != producer:
        notices.append("安全に一意変換できるコマンド入力を推奨形へ補正した。")
    git_grep_rewritten = _rewrite_simple_git_grep(rewritten)
    if git_grep_rewritten is not None:
        rewritten = git_grep_rewritten
        notices.append("`git grep`のパターン後方にある既知オプションを受理位置へ移した。")
    fix: _TruncationFix | None = None
    if truncation is not None:
        if not session_id:
            return None
        try:
            session_temp = managed_temp.create_managed_temp("session", session_id=session_id)
        except (managed_temp.ManagedTempError, OSError):
            return None
        consumer_tokens = truncation[1]
        read_back = not _truncation_consumer_operands(consumer_tokens)
        log_path = str(session_temp / f"bash-output-{time.time_ns()}.log")
        redirection = ">" if read_back else ">>"
        # `2>&1`はその時点の標準出力の宛先を標準エラーへ複製する。保存先への
        # リダイレクトを当該冗長化の後方へ置くと、標準エラーは元の宛先のまま残る。
        stderr_merged = rewritten.rstrip().endswith(_STDERR_DUPLICATION_SUFFIX)
        if stderr_merged:
            body = rewritten.rstrip()[: -len(_STDERR_DUPLICATION_SUFFIX)].rstrip()
            rewritten = f"{body} {redirection} {shlex.quote(log_path)} {_STDERR_DUPLICATION_SUFFIX}"
        else:
            rewritten = f"{rewritten} {redirection} {shlex.quote(log_path)}"
        if read_back:
            rewritten = f"{rewritten}; {shlex.join([*consumer_tokens, log_path])}"
            if inside_conditional:
                # `||`と`&&`の被演算子の内側では、読み戻しを同じ被演算子の内側へ留める。
                # 外側の`;`区間へ移すと、読み戻しが条件によらず実行されて成否が変わる。
                rewritten = f"{{ {rewritten}; }}"
        fix = _TruncationFix(
            position=0,
            segment=command,
            truncation_command=pathlib.PurePosixPath(consumer_tokens[0]).name,
            log_path=log_path,
            stderr_merged=stderr_merged,
            read_back=read_back,
        )
    if rewritten == command:
        return None
    return rewritten, notices, fix


def _format_truncation_autofix_notice(saved: list[_TruncationFix], *, total_segments: int) -> str:
    """切り詰め補正の通知本文を、補正対象の直列区間と保存先の対応として組み立てる。

    実行主体が是正の対象を特定できるよう、検出した直列区間と当該区間で切り詰めと判定した
    コマンドの表記を区間ごとに示す。
    実行主体が受け取る結果の変化も本文へ示す。
    読み戻しを加えた区間と加えていない区間で、当該呼び出しの結果に何が返るかの案内を分ける。
    """
    lines = [
        f"- 第{fix.position}直列区間 `{fix.segment}`: 切り詰めと判定したコマンドは`{fix.truncation_command}`。"
        f"{'標準出力と標準エラー' if fix.stderr_merged else '標準出力'}を`{fix.log_path}`へ保存し、"
        f"{'当該保存先を操作対象として同じコマンドへ渡した' if fix.read_back else '当該保存先へ追記した'}"
        for fix in saved
    ]
    read_back = [fix for fix in saved if fix.read_back]
    appended = [fix for fix in saved if not fix.read_back]
    messages = ["切り詰め処理を除去し、標準出力の全量を保存先へ補正した。", *lines]
    if read_back:
        messages.append(
            "読み戻しを加えた区間は、補正前のコマンドが要求した範囲を当該呼び出しの結果へ返す。全量は保存先に残る。"
        )
    if appended:
        if len(appended) >= total_segments:
            messages.append("当該呼び出しは標準出力を返さない。")
        else:
            messages.append("切り詰めを含まない直列区間の標準出力は当該呼び出しの結果へ残る。")
        messages.append("読み戻しを加えていない区間がループ本体で反復される場合、反復ごとの出力は同じ保存先へ追記される。")
        messages.append("保存先から必要な範囲だけを行数指定又は構造化条件で読む操作が残っている。")
    if any(not fix.stderr_merged for fix in saved):
        messages.append("標準エラーを保存先へ向けていない区間の標準エラーは、当該呼び出しの結果へ残る。")
    alternatives = _truncated_command_alternatives(saved)
    if alternatives:
        messages.append("補正対象のコマンドに対応する指定: " + "、".join(alternatives))
    messages.append(f"切り詰めを含まない書き方: {_OUTPUT_TRUNCATION_AVOIDANCE}")
    messages.append("同じセッションで次に同種の切り詰め指定を検出した場合は、補正せず実行前に遮断する。")
    return "\n".join(messages)


_COMMAND_SPECIFIC_LIMITATIONS: dict[str, str] = {
    "git": "`git grep`は一致件数を`-c`、一致ファイル名を`-l`で返す",
    "ls": "`ls`は対象のディレクトリとglobで走査範囲を限定する",
    "rg": "`rg`は一致件数を`-c`、一致ファイル名を`-l`で返す",
    "grep": "`grep`は一致件数を`-c`、一致ファイル名を`-l`で返す",
    "find": "`find`は`-maxdepth`と述語で走査範囲を限定する",
}
"""補正対象の直列区間の先頭コマンドごとの、出力量を制御する指定。

一般的な方針だけを示す通知は、同じ組み立ての反復を止めない。
"""


def _truncation_count(tokens: Sequence[str]) -> int | None:
    """`head`又は`tail`の件数指定を静的に解決できる場合だけ返す。"""
    for index, token in enumerate(tokens[1:], start=1):
        if token == "-n" and index + 1 < len(tokens):
            value = tokens[index + 1]
        elif re.fullmatch(r"-[0-9]+", token):
            value = token[1:]
        else:
            continue
        return int(value)
    return None


def _is_managed_truncation_log(path: str) -> bool:
    """hookが全量保存先として生成した管理対象ログのパスなら真を返す。"""
    candidate = pathlib.Path(path)
    if not candidate.is_absolute() or not candidate.name.startswith("bash-output-") or candidate.suffix != ".log":
        return False
    try:
        managed_temp.validate_managed_temp(candidate.parent)
    except (managed_temp.ManagedTempError, OSError):
        return False
    return candidate.is_file()


def _specific_truncation_alternative(segment: str) -> str | None:
    """検出した用途から一意に組み立てられる、切り詰めを含まない代替を返す。"""
    split = _split_simple_truncation(segment)
    if split is None:
        return None
    producer, consumer = split
    try:
        producer_tokens = shlex.split(producer)
    except ValueError:
        return None
    if not producer_tokens or not consumer:
        return None
    producer_name = pathlib.PurePath(producer_tokens[0]).name
    consumer_name = pathlib.PurePath(consumer[0]).name
    operands = [token for token in producer_tokens[1:] if not token.startswith("-")]
    count = _truncation_count(consumer)
    if producer_name == "cat" and consumer_name == "tail" and len(operands) == 1 and _is_managed_truncation_log(operands[0]):
        direct = shlex.join([*consumer, operands[0]])
        return f"保存済みログの終端確認は`{direct}`を直接実行する"
    if (
        producer_name == "ls"
        and consumer_name == "head"
        and count == 1
        and "-d" in producer_tokens
        and len(operands) == 1
        and not any(character in operands[0] for character in "*?[]{}")
    ):
        return f"対象の存在確認は`test -e {shlex.quote(operands[0])}`を実行する"
    if producer_name == "find" and consumer_name == "head" and count == 1:
        direct = shlex.join([*producer_tokens, "-print", "-quit"])
        return f"単一対象の選択はproducer自身の終了条件を使い、`{direct}`を実行する"
    return None


def _truncated_command_alternatives_for_segments(segments: Sequence[str]) -> list[str]:
    """補正対象の直列区間へ対応する代替を、具体形から一般形の順で返す。"""
    alternatives: list[str] = []
    for segment in segments:
        specific = _specific_truncation_alternative(segment)
        if specific is not None and specific not in alternatives:
            alternatives.append(specific)
        for token in segment.split():
            hint = _COMMAND_SPECIFIC_LIMITATIONS.get(pathlib.PurePath(token).name)
            if hint is not None and hint not in alternatives:
                alternatives.append(hint)
    return alternatives


def _truncated_command_alternatives(saved: Sequence[_TruncationFix]) -> list[str]:
    """補正対象の直列区間へ対応する代替を、重複なく返す。"""
    return _truncated_command_alternatives_for_segments([fix.segment for fix in saved])


_OUTPUT_TRUNCATION_AVOIDANCE = (
    "当該コマンド自身が提供する対象の限定、件数指定、要約指定又は構造化条件で出力量を制御する。"
    "制御できない場合は標準出力をファイルへリダイレクトして全量を保存し、"
    "保存済みファイルから必要な範囲だけを行数指定又は構造化条件で読む。"
    "分離実行を利用できる場合は、読み取り専用の探索をagents_serverのstart_explore、"
    "コマンド実行をstart_shellへ分離してもよい。"
    "判定条件の正本は`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」とする。"
)
"""切り詰めを含む呼び出しを組み直す手段。

補正の通知と、`_check_bash_truncation_autofix_repeat`の遮断の通知が本定数を参照する。
同一セッションの初回は補正して実行を通し、2回目以降は補正せず遮断するため、
いずれの通知も本定数が示す形への組み替えを求める。
保存と再読の形と、コマンド自身の限定指定はこの検査の判定条件に一致しないため、
分離実行を利用できない実行主体も当該本文だけで切り詰めを含まない形へ到達できる。
"""


_TRUNCATION_AUTOFIX_REPEAT_KEY = "truncation_autofix_detected"
"""切り詰め補正の検出をセッション単位で数えるキー。

検索語と対象パスの違いで初回へ戻さないため、判定の種別だけをキーとする。
"""


def _check_bash_truncation_autofix_repeat(command: str, session_id: str) -> str | None:
    """規範が禁じる切り詰め指定を、同じセッションの2回目以降は補正せず遮断する。

    初回は補正して実行を通し、次回から遮断する旨を補正の通知本文が示す。
    補正は不成立な入力を成功する入力へ変換するため、反復も許すと実行主体が入力を改めないまま
    同じ保存と読み戻しを繰り返す。
    `_is_truncation_exempt_producer`が対象外と判定した取得は、`_split_simple_truncation`が
    切り詰めとして返さないため、検出回数へ算入されず遮断もされない。
    """
    segments = _split_serial_shell_commands(command, separators=_STATUS_SHELL_SEPARATORS)
    if not any(_split_simple_truncation(segment) is not None for segment in segments):
        return None
    if not _record_repeat_detection(session_id, _TRUNCATION_AUTOFIX_REPEAT_KEY):
        return None
    alternatives = _truncated_command_alternatives_for_segments(segments)
    fix = _OUTPUT_TRUNCATION_AVOIDANCE
    if alternatives:
        fix = "補正対象の用途に対応する指定: " + "、".join(alternatives) + "。" + fix
    print(
        _block_notice(
            "blocked: 規範が禁じる初回取得の件数限定を、同じセッションで再び検出した。",
            fix=fix,
        ),
        file=sys.stderr,
    )
    return "block"


def _autofix_bash_command(command: str, cwd: str, session_id: str) -> tuple[str, str] | None:
    """安全に一意変換できるBash入力を補正し、補正後入力と通知を返す。

    実在しないパスの除去を先に適用し、その結果へ直列区間ごとの補正を適用する。
    区間ごとの補正は保存先のリダイレクトを挿入するため、先に適用すると当該保存先が
    実在しないパスの候補として現れる。
    """
    notices: list[str] = []
    summary_parts: list[str] = []
    command_after_path_fix = command
    missing_fix = _autofix_missing_paths(command, cwd)
    if missing_fix is not None:
        command_after_path_fix, removed = missing_fix
        missing_notice = "実在しない検索・読取パスを当該呼び出しの対象から除いた。除いた対象: " + "、".join(removed)
        notices.append(missing_notice)
        # この文面は除去の対象だけを示すため、2件目以降の要旨も同じ文面で成立する。
        summary_parts.append(missing_notice)
    segments = _split_serial_shell_commands(command_after_path_fix, separators=_STATUS_SHELL_SEPARATORS)
    replacements: list[tuple[int, int, str]] = []
    saved: list[_TruncationFix] = []
    cursor = 0
    for index, segment in enumerate(segments, start=1):
        start = command_after_path_fix.find(segment, cursor)
        if start < 0:
            return None
        cursor = start + len(segment)
        preceding = command_after_path_fix[:start].rstrip()
        following = command_after_path_fix[cursor:].lstrip()
        inside_conditional = preceding.endswith(("||", "&&")) or following.startswith(("||", "&&"))
        fixed = _autofix_bash_segment(segment, cwd, session_id, inside_conditional=inside_conditional)
        if fixed is None:
            continue
        rewritten, segment_notices, fix = fixed
        replacements.append((start, cursor, rewritten))
        notices.extend(segment_notices)
        if fix is not None:
            saved.append(dataclasses.replace(fix, position=index))
    if not replacements and missing_fix is None:
        return None
    rewritten_command = command_after_path_fix
    for start, end, replacement in reversed(replacements):
        rewritten_command = rewritten_command[:start] + replacement + rewritten_command[end:]
    try:
        syntax = subprocess.run(  # noqa: S603
            ["bash", "-n"],
            input=rewritten_command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if syntax.returncode != 0:
        return None
    unique_notices = list(dict.fromkeys(notices))
    body = " ".join(unique_notices)
    if saved:
        truncation_notice = _format_truncation_autofix_notice(saved, total_segments=len(segments))
        body = f"{body}\n{truncation_notice}" if body else truncation_notice
        summary_parts.append(_format_truncation_autofix_summary(saved))
    summary = "\n".join(summary_parts) if summary_parts else None
    if missing_fix is not None:
        # 実在しないパスの除去は呼び出しの対象集合そのものを狭めるため、是正を要する通知として返す。
        return rewritten_command, _llm_notice(body, tag=_WARN_TAG, removable_cause=True, summary=summary)
    # 残る補正は、補正前の呼び出しが要求した結果をそのまま当該呼び出しへ返す。
    # 実行主体の是正を要さないため、振り返りの問題候補へ残らない情報提示のタグで返す。
    return rewritten_command, _llm_notice(body, tag="notice", summary=summary)


def _format_truncation_autofix_summary(saved: Sequence[_TruncationFix]) -> str:
    """2件目以降の通知へ用いる要旨を、補正の対象と保存先だけで組み立てる。

    理由の説明、書き方の案内及び判定条件の所在は1件目の本文が既に届けているため、要旨から外す。
    """
    targets = "、".join(f"第{fix.position}直列区間の`{fix.truncation_command}`→`{fix.log_path}`" for fix in saved)
    return f"切り詰め処理を除去し、標準出力の全量を保存先へ補正した。対象: {targets}"


_TEMP_FILE_SAVE_PHRASE = "管理対象一時領域のファイルへ保存する"
_FILE_LAUNCH_FORM_PHRASE = (
    "保存したファイルは、ファイルを実行対象として渡す起動形（`bash <ファイル>`、`python3 <ファイル>`、"
    "`powershell -File <ファイル>`など）で起動する。"
)
"""解消手段としてファイルの書込を案内する場合に用いる保存先の名指し。"""

_SPECIALIZED_COMMAND_FIRST_PHRASE = (
    "そのコードが構造化データからの項目の取り出しだけを行う場合は`jq`、"
    "行の抽出と置換だけを行う場合は`rg`で成立するため、先にその成否を判定する。成立しない場合は、"
)
"""保存と実行の前に判定する専用コマンドの案内。

保存と実行だけを示す案内は、1回の取得で成立する用途でも2工程を選ばせる。
"""

_NESTED_SHELLS = frozenset({"bash", "dash", "sh", "zsh"})
_ENV_READ_COMMANDS = frozenset({"cat", "head", "less", "more", "tail", "xxd"})
_ENV_BASENAME_PATTERN = re.compile(r"^\.env(?:\..+)?$")
_BLOCKED_CHAIN_ARTIFACT_GUIDANCE = (
    "この遮断では呼び出し全体を実行しないため、同じ呼び出しの先行工程による成果物も未作成である。"
    "後続工程が読む成果物は、別の呼び出しで先に作成する。"
)


def _check_bash_nested_code_string(command: str) -> bool:
    """別のシェルへコード文字列を渡す多段の引用解釈を遮断する。

    `references/claude-hooks.md`「遮断・警告フックの成立条件」の第1段で復元できないと判定して遮断を維持する。
    段ごとの展開規則が重なると、実行主体が渡した入力とは異なるコマンドが成立し、
    当該コマンドが削除、上書き、外部送信などの復元できない操作を含み得るためである。
    """
    masked = _bash_command_parser.mask_heredoc_bodies(command)
    token_groups: list[tuple[str, ...]] = []
    with contextlib.suppress(ValueError):
        token_groups.append(tuple(shlex.split(masked, posix=True)))
    token_groups.extend(segment.tokens for segment in _extract_execution_segments(masked) if segment.resolved)
    for tokens in token_groups:
        if any(
            token in _NESTED_SHELLS and index + 1 < len(tokens) and tokens[index + 1] == "-c"
            for index, token in enumerate(tokens)
        ):
            print(
                _block_notice(
                    "blocked: 別のシェルへ`-c`でコード文字列を渡す入力は、引用を2段以上で解釈する。",
                    fix=f"{_BLOCKED_CHAIN_ARTIFACT_GUIDANCE}実行するコードを{_TEMP_FILE_SAVE_PHRASE}。{_FILE_LAUNCH_FORM_PHRASE}",
                ),
                file=sys.stderr,
            )
            return True
        if tokens and pathlib.PurePath(tokens[0]).name == "su" and "-c" in tokens[1:]:
            print(
                _block_notice(
                    "blocked: `su -c`へコード文字列を渡す入力は、引用を2段以上で解釈する。",
                    fix=f"{_BLOCKED_CHAIN_ARTIFACT_GUIDANCE}実行するコードを{_TEMP_FILE_SAVE_PHRASE}。{_FILE_LAUNCH_FORM_PHRASE}",
                ),
                file=sys.stderr,
            )
            return True
    if re.search(r"(?:^|[;&|]\s*)ssh\s+[^\n;&|]*[\"'][^\n]*[\"']", masked):
        print(
            _block_notice(
                "blocked: `ssh`へ引用したコード文字列を渡す入力は、ローカルと接続先で引用を解釈する。",
                fix=(
                    f"{_BLOCKED_CHAIN_ARTIFACT_GUIDANCE}実行するコードを{_TEMP_FILE_SAVE_PHRASE}。"
                    f"保存したファイルを接続先へ転送する。{_FILE_LAUNCH_FORM_PHRASE}"
                ),
            ),
            file=sys.stderr,
        )
        return True
    return False


_PYTHON_EVAL_OPTION = "-c"


def _python_eval_code(tokens: Sequence[str]) -> str | None:
    """`python`の実行位置に続く`-c`の直後のコード文字列を返す。

    当該形でない場合と、`-c`より前に値を取り得るオプションが現れて位置を確定できない場合はNoneを返す。
    """
    for index, token in enumerate(tokens):
        if not _is_python_token(token):
            continue
        for offset in range(index + 1, len(tokens)):
            argument = tokens[offset]
            if argument == _PYTHON_EVAL_OPTION:
                return tokens[offset + 1] if offset + 1 < len(tokens) else None
            if not argument.startswith("-"):
                return None
        return None
    return None


def _check_bash_python_code_string(command: str) -> bool:
    """`python`の`-c`へ複数の文又は構文として成立しないコードを渡す入力を遮断する。

    `agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」は、複数行のコードを
    評価用引数へ埋め込むことを既定で従う規定として禁じる。
    `references/claude-hooks.md`「遮断・警告フックの成立条件」の第1段で復元できないと判定して遮断する。
    コマンド文字列とコードの引用境界が重なるとコードの改行が失われ、後続の文が前の文へ連結された
    別のコードが成立する。当該コードが削除、上書きなどの復元できない操作を含み得るためである。
    単一の文だけを渡す呼び出しは、引用境界が重なっても実行されるコードが変わらないため対象にしない。
    """
    masked = _bash_command_parser.mask_heredoc_bodies(command)
    token_groups: list[tuple[str, ...]] = []
    with contextlib.suppress(ValueError):
        token_groups.append(tuple(shlex.split(masked, posix=True)))
    token_groups.extend(segment.tokens for segment in _extract_execution_segments(masked) if segment.resolved)
    for tokens in token_groups:
        code = _python_eval_code(tokens)
        if code is None:
            continue
        try:
            parsed = ast.parse(code)
        except SyntaxError:
            reason = "構文として成立しない"
        else:
            if len(parsed.body) < 2:
                continue
            reason = "複数の文を含む"
        print(
            _block_notice(
                f"blocked: `python`の`-c`へ渡すコードが{reason}。"
                "コマンド文字列とコードの引用境界が重なると、コードの改行が失われる。",
                fix=(
                    f"{_BLOCKED_CHAIN_ARTIFACT_GUIDANCE}{_SPECIALIZED_COMMAND_FIRST_PHRASE}"
                    f"実行するコードを{_TEMP_FILE_SAVE_PHRASE}。{_FILE_LAUNCH_FORM_PHRASE}"
                ),
            ),
            file=sys.stderr,
        )
        return True
    return False


def _check_bash_env_full_read(command: str) -> bool:
    """内容出力コマンドによる`.env`系ファイルの全文又は範囲読取を遮断する。"""
    for segment in _extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        name = pathlib.PurePath(segment.tokens[0]).name
        if name not in _ENV_READ_COMMANDS:
            continue
        for token in _argument_tokens(segment):
            if token.startswith("-") or token in {"-", "/dev/stdin"}:
                continue
            normalized = token.rstrip("/")
            basename = pathlib.PurePath(normalized).name
            if basename.endswith((".example", ".sample")):
                continue
            if _ENV_BASENAME_PATTERN.fullmatch(basename):
                print(
                    _block_notice(
                        f"blocked: `{name}`による`.env`系ファイルの内容出力は禁止されている。対象: {token}",
                        fix="必要なキーの値だけを`grep`、`sed`又は`awk`で抽出する。",
                    ),
                    file=sys.stderr,
                )
                return True
    return False


# 明示パスの実在を検査する対象コマンドと、pattern・scriptを先頭の非オプション引数として取るコマンド。
_PATH_OPERAND_COMMANDS: frozenset[str] = frozenset(
    {"rg", "ugrep", "cat", "sed", "ls", "cp", "find", "wc", "grep", "egrep", "fgrep"}
)
_PATTERN_FIRST_COMMANDS: frozenset[str] = frozenset({"rg", "ugrep", "sed", "grep", "egrep", "fgrep"})
# pattern・scriptを別の位置で受け取るオプション。指定がある場合は先頭の非オプション引数もパス候補とする。
_PATTERN_OPTIONS: frozenset[str] = frozenset({"-e", "-f", "--regexp", "--file", "--expression"})
# 直後のトークンを値として取る既知のオプション。パス候補の判定から当該値を除く。
_VALUE_OPTIONS: frozenset[str] = frozenset(
    {
        "-e",
        "-f",
        "-m",
        "-A",
        "-B",
        "-C",
        "-g",
        "-t",
        "-T",
        "-d",
        "--regexp",
        "--file",
        "--expression",
        "--max-count",
        "--include",
        "--exclude",
        "--exclude-dir",
        "--glob",
        "--type",
        "--type-not",
        "--max-filesize",
        "--max-columns",
        "--max-depth",
        "--encoding",
        "--color",
        "--colour",
        "--context",
        "--after-context",
        "--before-context",
    }
)
_FIND_NON_PATH_VALUE_PREDICATES: frozenset[str] = frozenset(
    {
        "-name",
        "-iname",
        "-regex",
        "-iregex",
        "-lname",
        "-ilname",
        "-maxdepth",
        "-mindepth",
        "-type",
        "-size",
        "-perm",
        "-user",
        "-group",
        "-mtime",
        "-mmin",
        "-path",
        "-ipath",
        "-wholename",
        "-iwholename",
    }
)
"""`find`の述語のうち、直後のトークンを値として取り、当該値が実在のパスを指さないもの。

`-name`系と`-regex`系の値はファイル名のパターン、`-path`系の値は探索先からの相対パターンであり、
`-maxdepth`・`-type`などの値は数値と種別である。いずれも実在を検査する対象にならない。
値が実在するパスを指す`-newer`・`-newermt`・`-anewer`・`-cnewer`は含めず、現行どおりパス候補として扱う。
当該述語の値をパス候補として扱うと、`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」が
推測したパスの解決手段として指定する`find`の呼び出しそのものへ不在の警告が発火する。
"""
_COMMAND_VALUE_OPTIONS: dict[str, frozenset[str]] = {"find": _FIND_NON_PATH_VALUE_PREDICATES}
"""実行位置のコマンド名ごとの、直後のトークンを値として取るオプション。

`_VALUE_OPTIONS`はgrep系のオプションを対象とするため、別のコマンドの述語を同じ集合へ加えない。
同じ集合へ加えると、当該オプション名を持つ別のコマンドの判定も変わる。
"""
_PATH_LIKE_PATTERN = re.compile(r"[/]|^[.~]|\.[A-Za-z0-9_]+$")


def _looks_like_path(token: str) -> bool:
    """パス候補として実在を検査する形かを返す。

    パス区切り、先頭のドット・チルダ、拡張子のいずれかを持つトークンだけを対象とする。
    拡張子を持たない語をパスとして扱うと、検索patternと`find`の述語を誤って対象にする。
    """
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", token):
        return False
    return _PATH_LIKE_PATTERN.search(token) is not None


def _argument_tokens(segment: _ExecutionSegment, start: int = 1) -> tuple[str, ...]:
    """区間の実行位置以降から、シェルのリダイレクトを除いた引数トークン列を返す。

    受理形式（オプション、位置引数、値の数）とoperandを引数の個数と並びから導く検査は、
    本関数が返す列だけを入力とする。
    検査ごとに`segment.tokens`を直接切り出すと、リダイレクトのトークンと宛先を引数として数える誤りが再現する。
    """
    return without_shell_redirections(segment.tokens[start:])


def _path_operands(segment: _ExecutionSegment) -> list[str]:
    """区間の実行位置から、実在を検査するパス候補を取り出す。"""
    name = pathlib.PurePath(segment.tokens[0]).name
    if name not in _PATH_OPERAND_COMMANDS:
        return []
    tokens = list(_argument_tokens(segment))
    value_options = _VALUE_OPTIONS | _COMMAND_VALUE_OPTIONS.get(name, frozenset())
    operands: list[str] = []
    option_terminator = False
    pattern_consumed = name not in _PATTERN_FIRST_COMMANDS
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not option_terminator and token == "--":
            option_terminator = True
            index += 1
            continue
        if not option_terminator and token.startswith("-") and token != "-":
            name_part = token.split("=", 1)[0]
            if name_part in _PATTERN_OPTIONS:
                pattern_consumed = True
            if name_part in value_options and "=" not in token:
                index += 2
                continue
            index += 1
            continue
        if not pattern_consumed:
            pattern_consumed = True
            index += 1
            continue
        operands.append(token)
        index += 1
    if name in _COPY_COMMANDS and operands:
        # `cp`と`mv`の最終operandは宛先であり、実在しないことが正常な入力である。
        operands = operands[:-1]
    return operands


_COPY_COMMANDS: frozenset[str] = frozenset({"cp", "mv"})
_WRITE_TARGET_VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "atk": frozenset({"--output-file"}),
    "curl": frozenset({"-o", "--output"}),
    "wget": frozenset({"-O", "--output-document"}),
    "sort": frozenset({"-o", "--output"}),
}
"""コマンド名ごとの、直後のトークンを書込先として取るオプション。"""


def _command_write_targets(segment: _ExecutionSegment) -> list[str]:
    """区間が作成する書込先のうち、コマンド文字列から静的に確定できるものを返す。

    シェルのリダイレクト先と同じく、以降の区間の不在判定から除くために用いる。
    値の位置に別のオプションが現れる呼び出しは書込先を確定できないため返さない。
    その呼び出しでは保存先が作成されないため、後続区間の読取に対する警告が真陽性になる。
    """
    name = pathlib.PurePath(segment.tokens[0]).name
    tokens = list(_argument_tokens(segment))
    targets: list[str] = []
    options = _WRITE_TARGET_VALUE_OPTIONS.get(name, frozenset())
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in options:
            value = tokens[index + 1] if index + 1 < len(tokens) else None
            if value is not None and not value.startswith("-"):
                targets.append(value)
            index += 2
            continue
        attached = next((option for option in options if option.startswith("--") and token.startswith(f"{option}=")), None)
        if attached is not None:
            targets.append(token.split("=", 1)[1])
        index += 1
    if name == "tee":
        targets.extend(token for token in tokens if not token.startswith("-"))
    if name in _COPY_COMMANDS:
        operands = [token for token in tokens if not token.startswith("-")]
        if len(operands) >= 2:
            targets.append(operands[-1])
    return [target for target in targets if target and not any(character in target for character in "*$?[]{}~`")]


_SCRIPT_INTERPRETERS: frozenset[str] = frozenset({"bash", "sh", "zsh", "node", "perl", "ruby", "pwsh", "powershell"})
"""スクリプトファイルを引数として受け取る実行ファイル名。"""
_SCRIPT_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".pyw", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1"}
)
"""スクリプトファイルとして実行される拡張子。"""


def _segment_runs_script_file(segment: _ExecutionSegment) -> bool:
    """区間がスクリプトファイルを実行する呼び出しかを返す。

    判定は当該区間の先頭トークンと引数だけで確定し、外部への照会を要さない。
    当該プログラムが出力するファイルはコマンド文字列に現れないため、
    以降の区間の読取対象を静的な実在判定の対象にできない。
    """
    if segment.is_agent_toolkit_script:
        return True
    name = pathlib.PurePath(segment.tokens[0]).name
    if _is_python_token(name) or name in _SCRIPT_INTERPRETERS:
        return any(not token.startswith("-") for token in _argument_tokens(segment))
    return pathlib.PurePath(name).suffix in _SCRIPT_SUFFIXES


@dataclasses.dataclass(frozen=True)
class _ExplicitPathScan:
    """明示パスの実在判定の結果。"""

    missing: tuple[str, ...]
    """実在しないパス候補を出現順に重複なく並べたもの。"""

    present: tuple[str, ...]
    """実在するか、先行区間が作成するパス候補。"""


def _scan_explicit_paths(command: str, cwd: str) -> _ExplicitPathScan:
    """検索・読取・複製コマンドの明示パスを、実在するものと実在しないものへ分けて返す。

    実行位置ごとに判定するため、パイプと制御演算子を含む呼び出しも対象とする。
    実行区間を先頭から順に走査し、先行する区間が出力リダイレクトの宛先として作成するパスと、
    コマンド自身の出力オプションが指す書込先は、以降の区間の不在判定から除く。全量を保存先へ
    保存してから同じ呼び出しで読む形が規範の求める形であり、当該形を不在として扱うと
    規定どおりの操作へ毎回警告が発火するためである。
    除外は当該コマンド文字列から書き込み先として確定できる宛先に限り、変数とglobを含むトークンは
    判定の対象外のまま扱う。
    先行する区間がスクリプトファイルを実行する場合は、以降の区間を不在判定の対象から外す。
    当該プログラムが出力するファイルはコマンド文字列へ現れないためである。
    相対パスの解決基準は、同じコマンド文字列の内側にある`cd`の遷移先を反映した実効の作業ディレクトリとする。
    `cd <ディレクトリ> &&`に続く相対パスを起動時の作業ディレクトリから解決すると、
    実際には成功する呼び出しへ不在の警告が発火する。
    `cd`の遷移先を静的に解決できない場合は、起動時の作業ディレクトリを基準として判定する。
    """
    missing: list[str] = []
    present: list[str] = []
    created: set[pathlib.Path] = set()
    current = CwdResolution(cwd, True)
    for segment in _extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        cwd_change = resolve_cwd_change(list(segment.tokens), current)
        if cwd_change is not None:
            current = cwd_change
            continue
        if _segment_runs_script_file(segment):
            break
        base = current.path if current.resolved and current.path else cwd
        for candidate in _path_operands(segment):
            if candidate in {"-", "/dev/stdin"} or any(character in candidate for character in "*$?[]{}~`"):
                continue
            if not _looks_like_path(candidate):
                continue
            path = pathlib.Path(candidate)
            resolved = path if path.is_absolute() else pathlib.Path(base) / path
            if resolved in created or resolved.exists():
                if candidate not in present:
                    present.append(candidate)
                continue
            if candidate not in missing:
                missing.append(candidate)
        write_targets = list(shell_redirection_targets(segment.tokens)) + _command_write_targets(segment)
        for target in write_targets:
            if any(character in target for character in "*$?[]{}~`"):
                continue
            target_path = pathlib.Path(target)
            created.add(target_path if target_path.is_absolute() else pathlib.Path(base) / target_path)
    return _ExplicitPathScan(tuple(missing), tuple(present))


def _check_bash_explicit_path_exists(command: str, cwd: str) -> str | None:
    """検索・読取・複製コマンドの展開を含まない明示パスが存在するか検査する。

    実在しないパスを除いても対象が残る呼び出しは`_autofix_bash_command`が当該パスを除いた形へ補正し、
    補正後の入力が本判定へ渡るため、本判定は補正が成立しない呼び出しだけを警告する。
    不在のパスは実行位置ごとに全件を列挙し、複数パスを渡した呼び出しの是正が1回で済む形にする。
    通した場合の結果は当該コマンドが不在のパスで失敗することに限り、作業ツリーへ副作用を残さない。
    `references/claude-hooks.md`「遮断・警告フックの成立条件」の第1段が復元できる結果へ警告を求めるため、警告で返す。
    """
    if not cwd:
        return None
    scan = _scan_explicit_paths(command, cwd)
    if not scan.missing:
        return None
    return _llm_notice(
        "明示された検索・読取パスが存在しない。対象: " + "、".join(scan.missing) + "\n"
        "対処: Git管理対象は`rg --files`、属性・ディレクトリ構造は`find`で実体を解決し、実在するパスを指定する。"
        "不在を確認する意図では、対象ごとに別の呼び出しで`test -e <絶対パス>`を実行し、終了コードで判定する。",
        tag=_WARN_TAG,
        removable_cause=True,
    )


_COMMAND_WORD_SEPARATORS = " \t"


def _remove_command_word(command: str, word: str) -> str | None:
    """コマンド文字列から、空白で区切られた1語として1回だけ現れる語を除いた文字列を返す。

    当該語が空白区切りの1語として現れない場合と、複数回現れる場合はNoneを返す。
    引用の内側や別の語と連結した位置を機械的に除くと、当該語以外の内容を失う。
    """
    matches = list(re.finditer(rf"(?<![^\s]){re.escape(word)}(?![^\s])", command))
    if len(matches) != 1:
        return None
    start, end = matches[0].span()
    while start > 0 and command[start - 1] in _COMMAND_WORD_SEPARATORS:
        start -= 1
    if start == 0:
        while end < len(command) and command[end] in _COMMAND_WORD_SEPARATORS:
            end += 1
    return command[:start] + command[end:]


def _remove_missing_paths(command: str, missing: Sequence[str]) -> str | None:
    """実在しないパスを語として除いた文字列を返す。除去を確定できない場合はNoneを返す。"""
    rewritten = command
    for candidate in missing:
        replaced = _remove_command_word(rewritten, candidate)
        if replaced is None:
            return None
        rewritten = replaced
    return rewritten


def _operand_loss_commands(before: str, after: str) -> list[str]:
    """パスの除去により操作対象の引数を全て失う区間のコマンド名を返す。"""
    before_segments = list(_extract_execution_segments(before))
    after_segments = list(_extract_execution_segments(after))
    if len(before_segments) != len(after_segments):
        return []
    losses: list[str] = []
    for original, updated in zip(before_segments, after_segments, strict=False):
        if not original.resolved or not updated.resolved or not updated.tokens:
            continue
        if _path_operands(original) and not _path_operands(updated):
            losses.append(updated.tokens[0])
    return losses


def _check_bash_missing_path_operand_loss(command: str, cwd: str) -> str | None:
    """実在しないパスの除去で操作対象の引数を全て失う呼び出しを遮断する。

    引数を失ったコマンドは標準入力を読んで別の意味で成立するため、補正して実行させない。
    """
    if not cwd:
        return None
    scan = _scan_explicit_paths(command, cwd)
    if not scan.missing or not scan.present:
        return None
    rewritten = _remove_missing_paths(command, scan.missing)
    if rewritten is None:
        return None
    losses = _operand_loss_commands(command, rewritten)
    if not losses:
        return None
    print(
        _block_notice(
            "block: 実在しないパスを除くと操作対象の引数が無くなるコマンドがある。"
            f"対象のコマンド: {'、'.join(f'`{name}`' for name in losses)}。"
            f"実在しないパス: {'、'.join(scan.missing)}",
            fix=(
                "当該コマンドへ実在するパスを指定するか、当該コマンドを呼び出しから外す。"
                "引数を失ったコマンドは標準入力を読み、補正前とは異なる成否を返す。"
                "不在を確認する意図では、対象ごとに別の呼び出しで`test -e <絶対パス>`を実行し、終了コードで判定する。"
            ),
        ),
        file=sys.stderr,
    )
    return "block"


def _autofix_missing_paths(command: str, cwd: str) -> tuple[str, tuple[str, ...]] | None:
    """実在しないパスを除いても対象が残る呼び出しを、当該パスを除いた形へ補正する。

    実在しないパスを含む呼び出しは当該コマンド自身が失敗し、同じ内容の再発行を要する。
    実在するパスが1件以上残る場合だけ補正し、対象が残らない呼び出しは補正せず警告へ委ねる。
    補正で除いた対象は通知本文へ列挙し、母集団が減ったことを実行主体が観測できる状態にする。
    """
    if not cwd:
        return None
    scan = _scan_explicit_paths(command, cwd)
    if not scan.missing or not scan.present:
        return None
    rewritten = _remove_missing_paths(command, scan.missing)
    if rewritten is None or _operand_loss_commands(command, rewritten):
        return None
    return rewritten, scan.missing


_ENV_ASSIGN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_PYPROJECT_PROJECT_SECTION_PATTERN = re.compile(r"(?m)^\[project(?:\.[\w\-]+)?\]\s*$")


def _check_bash_uv_run_python(command: str, cwd: str) -> str | None:
    """`uv run python <path>`形式の起動を非Pythonプロジェクトで検出して警告する。

    判定詳細は本関数の冒頭コメントを参照する。
    通した場合の結果はプロジェクト解決の失敗による終了に限り、作業ツリーへ副作用を残さないため警告で返す。
    """
    segments = split_bash_segments(command)
    current_cwd = CwdResolution(cwd, bool(cwd))
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return None
        cwd_change = resolve_cwd_change(tokens, current_cwd)
        if cwd_change is not None:
            current_cwd = cwd_change
            continue
        info = _parse_uv_run_python(tokens)
        if info is not None:
            has_script_or_no_project, directory_or_project_overridden = info
            if not has_script_or_no_project and (
                directory_or_project_overridden or not current_cwd.resolved or not _cwd_in_python_project(current_cwd.path)
            ):
                return _llm_notice(
                    f"{_UV_RUN_PYTHON_BLOCK_MSG}\n対処: {_UV_RUN_PYTHON_FIX}",
                    tag=_WARN_TAG,
                    removable_cause=True,
                )
    return None


def _skip_env_assignments(tokens: list[str], start: int) -> int:
    """先頭の`KEY=VALUE`形式の環境変数代入をスキップした次の位置を返す。"""
    i = start
    while i < len(tokens) and _ENV_ASSIGN_PATTERN.match(tokens[i]):
        i += 1
    return i


def _parse_uv_run_python(tokens: list[str]) -> tuple[bool, bool] | None:
    """`uv [...] run [...] python`構造をtokensから検出する。

    構造を検出した場合は`(has_script_or_no_project, directory_or_project_overridden)`を返す。
    対象構造でなければNoneを返す。
    `--script` / `--no-project`は`uv`トークンと`python`トークンの間に
    出現する場合のみ「uv runのオプション」として扱う（`python`以降に書かれた
    場合は`python`の引数として解釈されるため対象外）。
    """
    i = _skip_env_assignments(tokens, 0)
    if i >= len(tokens) or tokens[i] != "uv":
        return None
    uv_idx = i
    python_idx: int | None = None
    for j in range(uv_idx + 1, len(tokens)):
        if _is_python_token(tokens[j]):
            python_idx = j
            break
    if python_idx is None:
        return None
    has_run_between = any(tokens[j] == "run" for j in range(uv_idx + 1, python_idx))
    if not has_run_between:
        return None
    has_script_or_no_project = False
    directory_or_project_overridden = False
    for tok in tokens[uv_idx + 1 : python_idx]:
        if tok in ("--script", "--no-project"):
            has_script_or_no_project = True
        elif tok in ("--directory", "--project") or tok.startswith("--directory=") or tok.startswith("--project="):
            directory_or_project_overridden = True
    return has_script_or_no_project, directory_or_project_overridden


def _cwd_in_python_project(cwd: str) -> bool:
    """cwdから祖先方向へ最初に見つかる`pyproject.toml`が`[project]`を持つ場合に真を返す。

    uvのプロジェクト解決と同じ探索順序に合わせる。直近の`pyproject.toml`が`[project]`を
    欠く場合、uvは当該ディレクトリへ`.venv`と`uv.lock`を生成するため偽を返す。
    祖先まで見つからない場合と読み込みに失敗した場合も偽を返す。
    """
    if not cwd:
        return False
    cwd_path = pathlib.Path(cwd)
    for directory in (cwd_path, *cwd_path.parents):
        try:
            text = (directory / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return False
        return _PYPROJECT_PROJECT_SECTION_PATTERN.search(text) is not None
    return False


# --- Bash: 固定sleep後に処理が続く前景待機の検出 ---

_SLEEP_COMMAND = "sleep"
_LONG_SLEEP_SECONDS = 30
"""前景の固定待機として扱う`sleep`の秒数。

観測した固定待機は420秒から570秒であり、この範囲を後続コマンドの種類によらず検出する。
一方で短い待機は処理の一部として用いられるため、既存の通過検体`sleep 5`を含む範囲は
読み取り専用の状態確認コマンドが続く場合だけを検出対象とする。
"""
_LOOP_KEYWORDS = frozenset({"until", "while", "for"})
_LOOP_END_KEYWORD = "done"
_POLL_COMMAND_PREFIXES = (
    ("ls",),
    ("cat",),
    ("git", "status"),
    ("gh", "run", "view"),
    ("gh", "run", "watch"),
    ("ps",),
    ("pgrep",),
    ("atk", "wi", "list"),
    ("atk", "wi", "show"),
    ("atk", "mq", "list"),
    ("atk", "mq", "show"),
    ("systemctl", "status"),
    ("systemctl", "is-active"),
)
_CURL_COMMAND = ("curl",)
_CURL_DATA_SHORT_OPTIONS = ("-d", "-F", "-T")
_CURL_DATA_LONG_OPTIONS = (
    "--data",
    "--data-raw",
    "--data-binary",
    "--data-ascii",
    "--data-urlencode",
    "--form",
    "--form-string",
    "--upload-file",
    "--json",
)
_CURL_METHOD_SHORT_OPTION = "-X"
_CURL_METHOD_LONG_OPTION = "--request"
_CURL_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
_CURL_NEXT_OPTIONS = ("--next", "-:")


def _split_curl_operations(args: Sequence[str]) -> list[list[str]]:
    """`--next`・独立トークンの`-:`で区切られた操作単位へトークン列を分割する。

    簡略化: `-:`が他の短縮オプションと結合した形は区切りとして検出しない,
    既知の限界: curlの短縮オプションクラスタを完全には解析しない,
    見直し契機: 結合形を使う書込みcurlの見逃しを実測した場合
    """
    operations: list[list[str]] = [[]]
    for arg in args:
        if arg in _CURL_NEXT_OPTIONS:
            operations.append([])
            continue
        operations[-1].append(arg)
    return operations


def _curl_args_have_write_indicator(args: Sequence[str]) -> bool:
    """curlの引数列に書込みを示す操作が1件以上含まれるかを判定する。"""
    return any(_curl_operation_has_write_indicator(operation) for operation in _split_curl_operations(args))


def _curl_operation_has_write_indicator(args: Sequence[str]) -> bool:
    """`--next`で区切った1操作にデータ送信または書込みHTTPメソッドがあるかを判定する。"""
    for arg in args:
        if any(arg == option or arg.startswith(option) for option in _CURL_DATA_SHORT_OPTIONS):
            return True
        if any(arg == option or arg.startswith(f"{option}=") for option in _CURL_DATA_LONG_OPTIONS):
            return True

    last_method: str | None = None
    for index, arg in enumerate(args):
        method = _extract_curl_method_value(args, index, arg)
        if method is not None:
            last_method = method
    return last_method is not None and last_method.upper() not in _CURL_READ_ONLY_METHODS


def _extract_curl_method_value(args: Sequence[str], index: int, arg: str) -> str | None:
    """`-X`・`--request`のHTTPメソッド値を連結形・分離形・`=`結合形から抽出する。"""
    if arg in (_CURL_METHOD_SHORT_OPTION, _CURL_METHOD_LONG_OPTION):
        return args[index + 1] if index + 1 < len(args) else ""
    if arg.startswith(_CURL_METHOD_SHORT_OPTION) and len(arg) > len(_CURL_METHOD_SHORT_OPTION):
        return arg[len(_CURL_METHOD_SHORT_OPTION) :]
    if arg.startswith(f"{_CURL_METHOD_LONG_OPTION}="):
        return arg[len(_CURL_METHOD_LONG_OPTION) + 1 :]
    return None


_WORD_BOUNDARY_CONTROL_CHARS = ("&", "|")
"""単体で単語境界となるBash制御演算子。このうち`;`・`&&`は別途区切りとして処理する。

`&`・`|`単体は本関数の分割対象ではないが、直後に空白無しで`#`が続く場合はコメント開始として
認識する必要がある（例: `sleep 0&#comment`は`&`直後がコメント開始）。
`(`・`)`は含めない。コマンド置換`$(...)`・算術展開`$((...))`・プロセス置換
`<(...)`・`>(...)`等の閉じ括弧は制御演算子ではなく式構文の一部であり、
直後の文字は同一単語の続きとなる（単語境界にならない）。サブシェルを開閉する
制御演算子としての単体`(`・`)`と展開構文中の`(`・`)`を区別する構文解析は本関数の
対象外とし、閉じ括弧を無条件に単語境界とすることによる誤検出を避けるため対象から除く。
"""


_SERIAL_SHELL_SEPARATORS: frozenset[str] = frozenset({";", "&&"})
_STATUS_SHELL_SEPARATORS: frozenset[str] = frozenset({";", "&&", "||", "&"})


def _split_serial_shell_commands(
    command: str,
    *,
    separators: frozenset[str] = _SERIAL_SHELL_SEPARATORS,
) -> list[str]:
    """指定したクォート外のシェル演算子でBash入力を直列コマンドへ分割する。

    クォート外の`#`（Bashコメント開始）から行末までをスキップし、
    コメント内の演算子を区切りとして誤検出しない。
    """
    command = _bash_command_parser.mask_heredoc_bodies(command)
    nested = _bash_command_parser.nested_shell_positions(command)
    segments: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    escaped = False
    # 現在位置が新しい単語を開始し得る位置か（行頭・エスケープなし空白直後・制御演算子直後）。
    # エスケープされた空白は単語を区切らないため、直前文字の生の空白判定だけでは
    # `foo\ #literal`のような字面をコメント開始と誤認する。
    word_boundary = True
    index = 0
    while index < len(command):
        char = command[index]
        if index in nested:
            buffer.append(char)
            word_boundary = False
            index += 1
            continue
        if escaped:
            buffer.append(char)
            escaped = False
            word_boundary = False
            index += 1
            continue
        if char == "\\" and quote != "'" and command.startswith("\\\n", index):
            # 行継続（バックスラッシュ改行）はBash仕様上、入力から完全に除去され前後を
            # 単純連結する。バッファへは何も追加せず、単語境界の状態も変化させない
            # （行継続前の空白直後であれば、継続後も引き続き単語境界のままとなる）
            index += 2
            continue
        if char == "\\" and quote != "'":
            buffer.append(char)
            escaped = True
            word_boundary = False
            index += 1
            continue
        if quote is not None:
            buffer.append(char)
            if char == quote:
                quote = None
            word_boundary = False
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            buffer.append(char)
            word_boundary = False
            index += 1
            continue
        # クォート外の`#`を検出した場合、単語先頭位置に限り行末までをスキップする
        # （Bash仕様では`#`は単語の先頭にある場合だけコメント開始）
        if char == "#" and word_boundary:
            newline_index = command.find("\n", index)
            if newline_index < 0:
                # 改行が無い場合はそのまま終了
                break
            # 改行のみを次の処理へ渡す
            index = newline_index
            word_boundary = True
            continue
        if char == "\n":
            buffer.append(char)
            word_boundary = True
            index += 1
            continue
        if char in (" ", "\t"):
            buffer.append(char)
            word_boundary = True
            index += 1
            continue
        separator_length = 0
        if command.startswith("&&", index) and "&&" in separators or command.startswith("||", index) and "||" in separators:
            separator_length = 2
        elif (
            char == ";"
            and ";" in separators
            or (
                char == "&"
                and "&" in separators
                and not command.startswith("&>", index)
                and not (index > 0 and command[index - 1] in "<>|")
            )
        ):
            separator_length = 1
        if separator_length:
            segments.append("".join(buffer).strip())
            buffer = []
            word_boundary = True
            index += separator_length
            continue
        if char in _WORD_BOUNDARY_CONTROL_CHARS:
            buffer.append(char)
            word_boundary = True
            index += 1
            continue
        buffer.append(char)
        word_boundary = False
        index += 1
    segments.append("".join(buffer).strip())
    return [segment for segment in segments if segment]


def _fixed_sleep_seconds(command: str) -> float | None:
    """コマンドが数値リテラルを与える`sleep`単体である場合にその秒数を返す。"""
    args = _command_tokens(command)
    if args is None or len(args) != 2 or args[0] != _SLEEP_COMMAND:
        return None
    try:
        return float(args[1])
    except ValueError:
        return None


def _is_fixed_sleep(command: str) -> bool:
    """コマンドが数値リテラルを与える`sleep`単体であるかを判定する。"""
    return _fixed_sleep_seconds(command) is not None


def _is_long_fixed_sleep(command: str) -> bool:
    """コマンドが閾値以上の数値リテラルを与える`sleep`単体であるかを判定する。"""
    seconds = _fixed_sleep_seconds(command)
    return seconds is not None and seconds >= _LONG_SLEEP_SECONDS


def _command_tokens(command: str) -> list[str] | None:
    """制御構文の接頭予約語を除いたコマンドトークンを返す。"""
    try:
        args = shlex.split(command, posix=True)
    except ValueError:
        return None
    while args and args[0] in {"do", "then", "else"}:
        args = args[1:]
    return args


def _command_tokens_with_quotes(command: str) -> list[str] | None:
    """クォートを保持したコマンドトークンを返す。"""
    try:
        args = shlex.split(command, posix=False)
    except ValueError:
        return None
    while args and args[0] in {"do", "then", "else"}:
        args = args[1:]
    return args


def _first_token(command: str) -> str | None:
    """コマンドの先頭トークンを返す（分割できない場合と空の場合はNone）。"""
    try:
        args = shlex.split(command, posix=True)
    except ValueError:
        return None
    return args[0] if args else None


def _starts_loop_keyword(command: str) -> bool:
    """コマンドの先頭トークンが条件ループの予約語であるかを判定する。"""
    tokens = _command_tokens(command) or []
    return bool(tokens) and tokens[0] in _LOOP_KEYWORDS


def _loop_scope_flags(segments: list[str]) -> list[bool]:
    """各セグメントがループ予約語から対応する`done`までの範囲に属するかを返す。

    ループを開くセグメントと対応する`done`のセグメント自身も範囲に含める。
    `done`が現れないまま入力が終わる場合は、末尾までを当該ループの範囲として扱う。
    """
    flags: list[bool] = []
    depth = 0
    for segment in segments:
        if _starts_loop_keyword(segment):
            depth += 1
            flags.append(True)
            continue
        flags.append(depth > 0)
        tokens = _command_tokens(segment) or []
        if depth > 0 and tokens and tokens[0] == _LOOP_END_KEYWORD:
            depth -= 1
    return flags


def _is_read_only_status_command(args: Sequence[str]) -> bool:
    """コマンドのトークン列が読み取り専用の状態確認であるかを判定する。"""
    if not args:
        return False
    if any(tuple(args[: len(prefix)]) == prefix for prefix in _POLL_COMMAND_PREFIXES):
        return True
    return tuple(args[:1]) == _CURL_COMMAND and not _curl_args_have_write_indicator(args[1:])


def _polling_loop_body_flags(segments: list[str]) -> tuple[list[bool], list[bool]]:
    """入れ子でなく早期離脱を持たない単純ポーリングループの本体範囲を返す。

    1つ目は本体範囲であり、2つ目はそのうち条件式が読み取り専用の状態確認コマンドである
    `while`ループの本体範囲とする。後者は反復のたびに条件式が状態を確認するため、
    本体に`sleep`があるだけで固定待機と状態確認の反復が成立する。
    """
    flags = [False] * len(segments)
    status_condition_flags = [False] * len(segments)
    start: int | None = None
    eligible = False
    status_condition = False
    nested = False
    has_early_exit = False
    depth = 0
    for index, segment in enumerate(segments):
        tokens = _command_tokens(segment) or []
        first = tokens[0] if tokens else None
        if first in _LOOP_KEYWORDS:
            if depth == 0:
                start = index
                status_condition = first == "while" and _is_read_only_status_command(tokens[1:])
                eligible = first == "for" or (first == "while" and tokens[1:] in (["true"], [":"])) or status_condition
                nested = False
                has_early_exit = False
            else:
                nested = True
            depth += 1
            continue
        if depth == 0:
            continue
        if first == _LOOP_END_KEYWORD:
            depth -= 1
            if depth == 0:
                if start is not None and eligible and not nested and not has_early_exit:
                    flags[start + 1 : index] = [True] * (index - start - 1)
                    if status_condition:
                        status_condition_flags[start + 1 : index] = [True] * (index - start - 1)
                start = None
            continue
        if first in {"break", "exit", "return"}:
            has_early_exit = True
    if depth == 1 and start is not None and eligible and not nested and not has_early_exit:
        flags[start + 1 :] = [True] * (len(segments) - start - 1)
        if status_condition:
            status_condition_flags[start + 1 :] = [True] * (len(segments) - start - 1)
    return flags, status_condition_flags


def _is_sleep_poll_pair(left: str, right: str, *, previous: str | None = None) -> bool:
    """隣接する2コマンドがsleepと読み取り専用状態確認の組であるかを判定する。"""
    left_args = _command_tokens(left)
    right_args = _command_tokens(right)
    if left_args is None or right_args is None:
        return False
    if not left_args or left_args[0] != _SLEEP_COMMAND or not right_args:
        return False
    if previous is not None:
        previous_args = _command_tokens(previous) or []
        if previous_args and previous_args[0] == "kill" and right_args[0] == "ps" and "-p" in right_args[1:]:
            return False
    return _is_read_only_status_command(right_args)


def _has_foreground_sleep_wait(segments: list[str]) -> bool:
    """ループ本体の外にある`sleep`が検出条件を満たすかを判定する。

    除外の要否は`sleep`候補自身が属する範囲だけで決める。
    直後のセグメントは検出条件の判定にだけ用い、その所属は除外条件へ混ぜない。
    """
    in_loop_body = _loop_scope_flags(segments)
    polling_loop_body, status_condition_loop_body = _polling_loop_body_flags(segments)
    return any(
        (polling_loop_body[index] or not in_loop_body[index])
        and (
            _is_long_fixed_sleep(segments[index])
            or (status_condition_loop_body[index] and _is_fixed_sleep(segments[index]))
            or _is_sleep_poll_pair(
                segments[index],
                segments[index + 1],
                previous=segments[index - 1] if index > 0 else None,
            )
        )
        for index in range(len(segments) - 1)
    )


def _record_repeat_detection(session_id: str, key: str) -> bool:
    """検出をセッション状態へ一度だけ記録し、記録済みだったかを返す。

    排他ロック下で現在値を読み、未記録なら記録して偽を、記録済みなら真を返す。
    `session_id`が空の場合は記録できないため常に偽を返し、初回と同じ扱いになる。
    """
    already_detected = False

    def _record(state: dict) -> dict | None:
        nonlocal already_detected
        already_detected = bool(state.get(key))
        if already_detected:
            return None
        state[key] = True
        return state

    update_state(session_id, _record)
    return already_detected


def _check_bash_sleep_poll_pattern(
    command: str,
    session_id: str,
    run_in_background: bool,
) -> str | None:
    """固定sleep後に処理が続く前景待機を初回warn、同一セッション内再検出でblockする。

    検出条件は、閾値以上の`sleep`の直後に任意のコマンドが続く形と、
    閾値未満の`sleep`の直後に読み取り専用の状態確認コマンドが続く形の2つとする。
    前者は待機後に続くコマンドの種類に依存しないため、状態確認コマンド名の追随保守を要しない。
    条件式が読み取り専用の状態確認コマンドである`while`ループの本体は、
    反復のたびに条件式が状態を確認するため、`sleep`単体だけでも検出する。
    条件成立で抜けるループ（`until`・条件式が状態確認コマンドでない`while`）の本体は検出対象から除く。
    入れ子でない`for`・`while true`・`while :`・条件式が状態確認コマンドの`while`の本体は、
    早期離脱が無い場合だけ検出対象とする。
    入れ子ループは検出対象から除く。
    当該範囲の外にある`sleep`は、同一のBash呼び出しにループが含まれる場合も通常どおり判定する。

    簡略化: クォート外の`;`・`&&`直列連結だけを検出する,
    既知の限界: サブシェルで包んだ状態確認は検出しない,
    見直し契機: サブシェル包みの反復ポーリングを実測した場合
    """
    if run_in_background:
        return None
    if not _has_foreground_sleep_wait(_split_serial_shell_commands(command)):
        return None

    already_detected = _record_repeat_detection(session_id, "sleep_poll_detected")
    guidance = (
        "待機対象の終了状態を返す公開機能で待つ。委譲先には`atk agents wait`、CIには`atk wait-ci`を使う。\n"
        "当該公開機能が無い場合は、完了通知を利用できるなら受領する。利用できなければ`sleep`を単独で実行し、"
        "終了後の別の呼び出しで状態を確認する。\n"
        "公開機能で待機中は同じ対象の状態照会を重ねない。根拠: "
        "`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」。"
    )
    if already_detected:
        print(
            _block_notice(
                "block: 前景の`sleep`に別のコマンドが続く呼び出しを、当該セッションで再び検出した。",
                fix=guidance,
            ),
            file=sys.stderr,
        )
        return "block"
    return _llm_notice(
        f"warn: 前景の`sleep`の後に別のコマンドが続いており、反復ポーリングになる可能性がある。\n{guidance}",
        tag=_WARN_TAG,
        removable_cause=True,
    )


# --- Bash: パターン一致によるプロセス終了の検出 ---

_PROCESS_KILL_BY_PATTERN_RE = re.compile(r"(?<![\w-])(pkill|killall)(?![\w-])")
_PROCESS_KILL_UNSAFE_MARKERS = frozenset("$`(){}")
_PROCESS_KILL_LITERAL_SEARCH_COMMANDS = frozenset({"egrep", "fgrep", "grep", "rg"})


def _git_grep_literal_pattern_indices(arguments: Sequence[str]) -> set[int]:
    """`git grep`がリテラル検索パターンとして読む引数位置を返す。"""
    indices: set[int] = set()
    pattern_seen = False
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            break
        option_name = token.split("=", 1)[0]
        if option_name in _GIT_GREP_PATTERN_OPTIONS:
            pattern_seen = True
            if "=" in token:
                indices.add(index)
            elif index + 1 < len(arguments):
                index += 1
                indices.add(index)
        elif _attached_short_value_option(token, _GIT_GREP_PATTERN_OPTIONS) is not None:
            pattern_seen = True
            indices.add(index)
        elif option_name in _GIT_GREP_PATTERN_FILE_OPTIONS:
            pattern_seen = True
            if "=" not in token:
                index += 1
        elif _attached_short_value_option(token, _GIT_GREP_PATTERN_FILE_OPTIONS) is not None:
            pattern_seen = True
        elif option_name in _GIT_GREP_VALUED_OPTIONS:
            if "=" not in token:
                index += 1
        elif not token.startswith("-") and not pattern_seen:
            pattern_seen = True
            indices.add(index)
        index += 1
    return indices


def _git_log_literal_search_indices(arguments: Sequence[str]) -> set[int]:
    """`git log`が検索語として読む引数位置を返す。"""
    indices: set[int] = set()
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            break
        if token in {"-S", "-G", "--grep"} and index + 1 < len(arguments):
            index += 1
            indices.add(index)
        elif (token.startswith(("-S", "-G")) and len(token) > 2) or token.startswith("--grep="):
            indices.add(index)
        index += 1
    return indices


def _has_active_process_kill_syntax(segment: str) -> bool:
    """区間に引用で無効化されていないシェル構文があれば真を返す。"""
    quote: str | None = None
    escaped = False
    for character in segment:
        if escaped:
            escaped = False
            continue
        if quote != "'" and character == "\\":
            escaped = True
            continue
        if quote == "'":
            if character == "'":
                quote = None
            continue
        if quote == '"':
            if character == '"':
                quote = None
            elif character in "$`":
                return True
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in _PROCESS_KILL_UNSAFE_MARKERS or character in "\n\r":
            return True
    return False


def _has_unsafe_process_kill_match(segment: str) -> bool:
    """禁止語を含む区間が、既知の検索コマンドのリテラル引数でなければ真を返す。"""
    try:
        raw_tokens = shlex.split(segment, posix=True)
    except ValueError:
        return True
    if not raw_tokens:
        return False
    command_name = pathlib.PurePosixPath(raw_tokens[0]).name
    attached_pager_match = command_name == "git" and any(
        token.startswith("-O") and _PROCESS_KILL_BY_PATTERN_RE.search(token[2:]) for token in raw_tokens
    )
    if not attached_pager_match and not any(_PROCESS_KILL_BY_PATTERN_RE.search(token) for token in raw_tokens):
        return False
    if _has_active_process_kill_syntax(segment):
        return True
    if command_name == "git":
        parsed_segments = _extract_execution_segments(segment)
        if len(parsed_segments) != 1 or tuple(raw_tokens) != parsed_segments[0].tokens:
            return True
        subcommand = _git_subcommand_tokens(parsed_segments[0])
        if subcommand is None or subcommand[0] not in {"grep", "log"}:
            return True
        name, arguments = subcommand
        prefix = raw_tokens[: len(raw_tokens) - len(arguments)]
        if any(_PROCESS_KILL_BY_PATTERN_RE.search(token) for token in prefix):
            return True
        safe_indices = (
            _git_grep_literal_pattern_indices(arguments) if name == "grep" else _git_log_literal_search_indices(arguments)
        )
        return any(
            (
                _PROCESS_KILL_BY_PATTERN_RE.search(token)
                or name == "grep"
                and token.startswith("-O")
                and _PROCESS_KILL_BY_PATTERN_RE.search(token[2:])
            )
            and index not in safe_indices
            for index, token in enumerate(arguments)
        )
    if command_name not in _PROCESS_KILL_LITERAL_SEARCH_COMMANDS:
        return True
    return not any(_PROCESS_KILL_BY_PATTERN_RE.search(token) for token in raw_tokens[1:])


def _check_bash_process_kill_by_pattern(command: str) -> bool:
    """`pkill`・`killall`等パターン指定によるプロセス終了をブロックする。

    対象の所有権を確認できないパターン一致の一括終了は事故の危険があるため禁止する。
    自身が起動して識別子（PID）を確認したプロセスに対する`kill <PID>`形式は対象外とする。
    ヒアドキュメント本文をマスクした文字列を解析し、禁止語が実行位置ではなく、安全な引数位置の
    リテラルだと確定できる区間だけを許可する。
    """
    matching_segments = [segment for segment in split_bash_segments(command) if "pkill" in segment or "killall" in segment]
    if not matching_segments or not any(_has_unsafe_process_kill_match(segment) for segment in matching_segments):
        return False
    print(
        _block_notice(
            "blocked: パターン一致によるプロセス終了（`pkill`／`killall`）は、対象プロセスの所有を確認できないため禁止する。",
            fix="自身が起動しPIDで特定したプロセスに対して`kill <PID>`を使う。",
        ),
        file=sys.stderr,
    )
    return True


# --- Bash: 全量観測が必要なコマンド出力の切り詰め検出 ---

_VERIFICATION_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("pyfltr", "ci"),
    ("pyfltr", "run"),
    ("pyfltr", "fast"),
    ("pyfltr", "run-for-agent"),
    ("pytest",),
    ("cargo", "test"),
    ("dotnet", "test"),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("pnpm", "test"),
    ("vitest",),
)
_STATE_CHANGING_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("atk", "wi", "add"),
    ("atk", "wi", "edit"),
    ("atk", "wi", "start-processing"),
    ("atk", "wi", "hold"),
    ("atk", "wi", "unhold"),
    ("atk", "wi", "return-to-inbox"),
    ("atk", "wi", "adopt"),
    ("atk", "wi", "reject"),
    ("atk", "wi", "rm"),
    ("atk", "wi", "set-dependencies"),
    ("atk", "wi", "answer"),
    ("atk", "wi", "commit"),
    ("atk", "wi", "migrate"),
    ("atk", "plans", "commit"),
    ("atk", "plans", "rewrite-references"),
    ("atk", "review-table", "init"),
    ("atk", "review-table", "add"),
    ("atk", "review-table", "respond"),
    ("git", "commit"),
    ("git", "push"),
    ("gh", "pr", "create"),
    ("gh", "pr", "merge"),
)
"""出力に生成したファイル名と構造検収の対象となるメタデータだけが現れる状態変更コマンドの前置き。

本文の照合はCLI内部で完結し、出力を根拠としない。
"""
_COMPLETE_OUTPUT_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("atk", "wi", "show"),
    ("atk", "review-table", "show"),
)
"""状態を変更せず、出力の全量が後続の照合の根拠となるコマンドの前置語。

`atk wi show`は一括取得の契約が全項目の出力を本文採用の条件とし、`atk review-table show`は
未解消の指摘と対応状況を確認する手段であり、不一致を検出した場合に差異を特定する手段でもある。
いずれも一部だけを読むと判断の根拠が失われる。
状態変更コマンドは`_STATE_CHANGING_COMMAND_PREFIXES`で別に判定する。
"""
_COMPLETE_OUTPUT_EXCLUDED_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("gh", "pr", "create"),
    ("gh", "release", "create"),
)
"""作成結果の識別子1件だけを返し、全量比較を要しないコマンドの前置語。"""
_OUTPUT_TRUNCATION_COMMANDS: frozenset[str] = frozenset({"head", "tail"})
_STATE_OUTPUT_TRUNCATION_COMMANDS: frozenset[str] = _OUTPUT_TRUNCATION_COMMANDS | {"grep", "egrep", "fgrep", "rg"}
_VERIFICATION_TARGET_KEYWORDS: tuple[str, ...] = ("test", "check", "lint", "format", "fmt")
"""全量観測を要するタスク名の部分一致語。整形時の警告も保持するため`format`と`fmt`を含む。"""
_GREP_COMMANDS: frozenset[str] = frozenset({"grep", "egrep", "fgrep"})
_GREP_LONG_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "--regexp",
        "--file",
        "--include",
        "--exclude",
        "--exclude-dir",
        "--binary-files",
        "--directories",
        "--devices",
        "--max-count",
        "--label",
    }
)
_GREP_LONG_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--recursive",
        "--ignore-case",
        "--invert-match",
        "--word-regexp",
        "--line-regexp",
        "--line-number",
        "--with-filename",
        "--no-filename",
        "--quiet",
        "--silent",
        "--count",
        "--files-with-matches",
        "--files-without-match",
        "--only-matching",
        "--text",
        "--binary",
        "--null",
        "--null-data",
        "--extended-regexp",
        "--fixed-strings",
        "--basic-regexp",
        "--perl-regexp",
    }
)
_GREP_SHORT_OPTIONS_WITHOUT_VALUE = frozenset("rRivwxsclLohnbIaEFGPqzZU")
_OUTPUT_FULL_SAVE_COMMAND = "tee"
_SHELL_REDIRECTION_PATTERN = re.compile(r"^(?:\d+)?(?:&>>|&>|<<<|<<|>>|<>|>&|<&|>\||>|<)")
_TEE_NON_FILE_OPERAND_PATTERN = re.compile(r"^(?:/dev/null|/dev/(?:stdin|stdout|stderr|tty)|/dev/fd/\d+|/proc/self/fd/\d+)/?$")
_MAKE_ASSIGNMENT_PATTERN = re.compile(r"^[^=\s]+?\s*(?:::=|:=|\?=|\+=|!=|=)")
_MAKE_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-C",
        "-E",
        "-f",
        "-I",
        "-o",
        "-W",
        "--directory",
        "--file",
        "--makefile",
        "--include-dir",
        "--old-file",
        "--assume-old",
        "--what-if",
        "--new-file",
        "--assume-new",
        "--eval",
    }
)
"""`make --help`で値を必須とする短長オプション（別名を含む）。

長形の`--name=value`形式は、走査側がオプション名と値を分離して判定する。
"""
_MAKE_LONG_OPTIONS: frozenset[str] = frozenset(
    {
        "--always-make",
        "--assume-new",
        "--assume-old",
        "--check-symlink-times",
        "--debug",
        "--directory",
        "--dry-run",
        "--environment-overrides",
        "--eval",
        "--file",
        "--help",
        "--ignore-errors",
        "--include-dir",
        "--jobs",
        "--just-print",
        "--keep-going",
        "--load-average",
        "--makefile",
        "--max-load",
        "--new-file",
        "--no-builtin-rules",
        "--no-builtin-variables",
        "--no-keep-going",
        "--no-print-directory",
        "--no-silent",
        "--old-file",
        "--output-sync",
        "--print-data-base",
        "--print-directory",
        "--question",
        "--quiet",
        "--recon",
        "--silent",
        "--stop",
        "--touch",
        "--trace",
        "--version",
        "--warn-undefined-variables",
        "--what-if",
    }
)
"""`make --help`に現れる長形オプション。GNU Makeの一意な省略解決に使う。"""

_MISE_RUN_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "--affected-base",
        "--affected-head",
        "--allow-env",
        "--allow-net",
        "--allow-read",
        "--allow-write",
        "--cd",
        "--env",
        "--jobs",
        "--output",
        "--shell",
        "--task-cache",
        "--timeout",
        "--tool",
        "-C",
        "-E",
        "-j",
        "-o",
        "-s",
        "-t",
    }
)
_MISE_RUN_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--affected",
        "--affected-explain",
        "--affected-json",
        "--all",
        "--continue-on-error",
        "--deny-all",
        "--deny-env",
        "--deny-net",
        "--deny-read",
        "--deny-write",
        "--dry-run",
        "--force",
        "--fresh-env",
        "--locked",
        "--no-cache",
        "--no-deps",
        "--no-timings",
        "--quiet",
        "--raw",
        "--silent",
        "--skip-deps",
        "--skip-tools",
        "--task-cache-explain",
        "--task-cache-explain-json",
        "--task-cache-stats",
        "--verbose",
        "--yes",
        "-c",
        "-f",
        "-n",
        "-q",
        "-r",
        "-S",
        "-v",
        "-y",
    }
)
_PNPM_RUN_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "--changed-files-ignore-pattern",
        "--dir",
        "--filter",
        "--filter-prod",
        "--loglevel",
        "--resume-from",
        "--test-pattern",
        "-C",
        "-F",
    }
)
_PNPM_RUN_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--aggregate-output",
        "--color",
        "--dry-run",
        "--fail-if-no-match",
        "--help",
        "--if-present",
        "--no-bail",
        "--no-color",
        "--parallel",
        "--recursive",
        "--report-summary",
        "--reporter-hide-prefix",
        "--sequential",
        "--stream",
        "--use-stderr",
        "--workspace-root",
        "--yes",
        "-h",
        "-r",
        "-s",
        "-w",
        "-y",
    }
)
_NPM_RUN_OPTIONS_WITH_VALUE: frozenset[str] = frozenset({"--script-shell", "--workspace", "-w"})
_NPM_RUN_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {"--foreground-scripts", "--if-present", "--ignore-scripts", "--include-workspace-root", "--workspaces"}
)


def _segment_starts_with(segment: _ExecutionSegment, prefix: tuple[str, ...]) -> bool:
    """区間の実行位置以降のトークン列が指定の接頭トークン列で始まるかを返す。"""
    return segment.resolved and segment.tokens[: len(prefix)] == prefix


def _segment_is_help_only(segment: _ExecutionSegment) -> bool:
    """区間が`--help`以外のオプションを持たないヘルプ専用呼び出しかを返す。"""
    arguments = without_shell_redirections(segment.tokens[1:])
    return (
        segment.resolved
        and "--" not in arguments
        and "--help" in arguments
        and all(token == "--help" for token in arguments if token.startswith("-"))
    )


def _recognized_atk_command_path(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    """実行トークン列から公開済みの最下層`atk`サブコマンド経路を返す。"""
    if len(tokens) < 2 or pathlib.PurePath(tokens[0]).name not in {"atk", "atk.py"}:
        return None
    from agent_toolkit._atk.help_text import HELP  # pylint: disable=import-outside-toplevel

    arguments = tuple("wi" if index == 0 and value == "mq" else value for index, value in enumerate(tokens[1:]))
    paths = (tuple(key.split()[1:]) for key in HELP if key.startswith("atk "))
    return next(
        (path for path in sorted(paths, key=len, reverse=True) if arguments[: len(path)] == path),
        None,
    )


def _check_bash_atk_help_observation(command: str, session_id: str) -> str | None:
    """未観測の最下層`atk`サブコマンドへ、CLI定義から生成した受理形式を添える。

    ヘルプを生成できない場合も実行は止めず、生成できなかった旨と確認手段を警告で返す。
    ヘルプ生成の失敗だけでは呼び出し自体の不成立を確定できないためである。
    """
    paths: list[tuple[str, ...]] = []
    for segment in _extract_execution_segments(command):
        if not segment.resolved or not segment.tokens or _segment_is_help_only(segment):
            continue
        path = _recognized_atk_command_path(segment.tokens)
        if path is not None and path not in paths:
            paths.append(path)
    if not paths:
        return None
    observed = observed_atk_help_paths(session_id)
    missing = [path for path in paths if " ".join(path) not in observed]
    if not missing:
        return None
    try:
        from agent_toolkit.atk import format_command_contract  # pylint: disable=import-outside-toplevel

        help_sections = [format_command_contract(path) for path in missing]
    except Exception as error:  # noqa: BLE001 - Hookはヘルプ生成不能を安全側へ倒す
        return _llm_notice(
            f"atkサブコマンドのヘルプを生成できない: {error}\n"
            "対処: `atk <サブコマンド> --help`を単独で実行して受理形式を確認する。",
            tag=_WARN_TAG,
            removable_cause=False,
        )
    if any(section is None for section in help_sections):
        return _llm_notice(
            "atkサブコマンドのヘルプ定義を解決できない。\n"
            "対処: `atk <サブコマンド> --help`を単独で実行して受理形式を確認する。",
            tag=_WARN_TAG,
            removable_cause=False,
        )
    record_atk_help_paths(session_id, [" ".join(path) for path in missing])
    bodies = [f"atk {' '.join(path)}: {section}" for path, section in zip(missing, help_sections, strict=True)]
    return _llm_notice(
        "info: 未観測のatkサブコマンドについて、実行前に受理形式を案内する。\n" + "\n".join(bodies),
        tag="notice",
    )


_ATK_HELP_ONLY_FLAGS: frozenset[str] = frozenset({"-h", "--help"})
"""全ての`atk`サブコマンドが共通で受理するヘルプのフラグ。

当該フラグだけを受理し、値付きオプションも位置引数も持たないサブコマンドは引数を受理しない。
"""


def _check_bash_atk_options(command: str) -> str | None:
    """公開済み最下層`atk`サブコマンドの受理形式に一致しない引数を実行前に検出する。

    公開契約から不成立が確定する呼び出しは、同じ失敗を実行させず遮断する。
    通知本文は当該判定が保持する受理形式から組み立て、受理形式に応じて対処を切り替える。
    受理形式から導かない固定の対処文は、引数を受理しないサブコマンドで実行できない案内になる。
    認識できた経路の直下に当該階層が受理しないサブコマンドがある呼び出しは、
    `_check_bash_unknown_atk_subcommand`が階層の誤りとして扱うため本判定の対象から外す。
    同じ呼び出しへ実態の異なる2つの本文を返さないためである。
    """
    from agent_toolkit.atk import command_option_contract  # pylint: disable=import-outside-toplevel

    for segment in _extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        path = _recognized_atk_command_path(segment.tokens)
        if path is None:
            continue
        if _atk_unknown_subcommand(segment) is not None:
            continue
        contract = command_option_contract(path)
        if contract is None:
            continue
        flags, valued, positionals = contract
        arguments = list(_argument_tokens(segment, 1 + len(path)))
        scan = _scan_accepted_options(arguments, flags, valued)
        if scan.unknown_option is not None:
            print(
                _block_notice(
                    f"`atk {' '.join(path)}`が受理しないオプションである。対象: {scan.unknown_option}",
                    fix=_format_accepted_option_candidates(scan.unknown_option, flags, valued),
                ),
                file=sys.stderr,
            )
            return "block"
        if not positionals and scan.positionals:
            accepts_no_arguments = not valued and set(flags) <= _ATK_HELP_ONLY_FLAGS
            remedy = (
                "対処: 当該サブコマンドは引数を受理しない。引数を付けずに再発行する。"
                if accepts_no_arguments
                else "対処: 当該の値をオプションで渡すか、位置引数を受理するサブコマンドへ変更する。"
                "受理するオプションは`--help`を単独で実行して確認する。"
            )
            print(
                _block_notice(
                    f"`atk {' '.join(path)}`は位置引数を受理しない。対象: {'、'.join(scan.positionals)}",
                    fix=remedy.removeprefix("対処: "),
                ),
                file=sys.stderr,
            )
            return "block"
    return None


def _segment_is_state_changing(segment: _ExecutionSegment) -> bool:
    """区間が列挙済みの状態変更コマンドであるかを返す。

    Gitはサブコマンド前のグローバルオプションを許容するため、既存のGitイベント解析でサブコマンドを解決する。
    """
    if not segment.resolved or not segment.tokens or _segment_is_help_only(segment):
        return False
    if segment.tokens[0] != "git":
        return any(_segment_starts_with(segment, prefix) for prefix in _STATE_CHANGING_COMMAND_PREFIXES)
    events = _bash_command_parser.extract_git_events(shlex.join(segment.tokens), ".")
    return len(events) == 1 and events[0].subcommand in {"commit", "push"}


def _grep_file_operands(segment: _ExecutionSegment) -> tuple[tuple[str, ...], tuple[str, ...], frozenset[str]] | None:
    """再帰`grep`区間のpattern本文の列、ファイルoperand及び認識済みオプションを返す。

    patternは通知本文が置換後のコマンドを組み立てるために返す。複数の`-e`を渡した呼び出しでは
    指定順に全件を返し、置換後のコマンドが同じ対象集合を検索する形になるようにする。
    ファイルから読む指定では本文を一意に取り出せないため空の列を返す。
    """
    operands: list[str] = []
    options: set[str] = set()
    patterns: list[str] = []
    tokens = _argument_tokens(segment)
    index = 0
    option_terminator = False
    while index < len(tokens):
        token = tokens[index]
        if option_terminator or token == "-" or not token.startswith("-"):
            operands.append(token)
            index += 1
            continue
        if token == "--":
            option_terminator = True
            index += 1
            continue
        if token.startswith("--"):
            name, separator, value = token.partition("=")
            if name in _GREP_LONG_OPTIONS_WITH_VALUE:
                options.add(name)
                if name == "--regexp":
                    found = value if separator else (tokens[index + 1] if index + 1 < len(tokens) else None)
                    if found is not None:
                        patterns.append(found)
                index += 1 if separator else 2
                continue
            if name in _GREP_LONG_OPTIONS_WITHOUT_VALUE and not separator:
                options.add(name)
                index += 1
                continue
            return None
        short = token[1:]
        if short[:1] in {"e", "f"}:
            options.add(f"-{short[0]}")
            if short[0] == "e":
                found = short[1:] if len(short) > 1 else (tokens[index + 1] if index + 1 < len(tokens) else None)
                if found is not None:
                    patterns.append(found)
            index += 1 if len(short) > 1 else 2
            continue
        if not short or any(character not in _GREP_SHORT_OPTIONS_WITHOUT_VALUE for character in short):
            return None
        options.update(f"-{character}" for character in short)
        index += 1
    pattern_is_option = bool(options & {"-e", "-f", "--regexp", "--file"})
    if not pattern_is_option:
        patterns = operands[:1]
    elif options & {"-f", "--file"}:
        patterns = []
    return (tuple(patterns), tuple(operands if pattern_is_option else operands[1:]), frozenset(options))


_RECURSIVE_GREP_WITHOUT_EXCLUSION_FIX = (
    "Git管理対象の内容は`git grep`、Git管理外・正規表現・除外設定に従う内容は`rg`を使う。"
    "隠し対象を母集団に含める`rg`には`--hidden`を付け、属性・ディレクトリ構造の探索は`find`を使う。"
    "`grep`を使う場合は`--include`・`--exclude`・`--exclude-dir`で対象を限定する。"
)


_GREP_LINE_NUMBER_OPTIONS: frozenset[str] = frozenset({"-n", "--line-number"})
_GREP_PATTERN_TYPE_BY_OPTION: dict[str, str] = {
    "-F": "-F",
    "--fixed-strings": "-F",
    "-E": "-E",
    "--extended-regexp": "-E",
    "-P": "-P",
    "--perl-regexp": "-P",
    "-G": "-G",
    "--basic-regexp": "-G",
}
"""元の`grep`の種別指定と、置換後の`git grep`が受理する短縮形の対応。"""
_RG_PATTERN_TYPE_BY_GIT_GREP: dict[str, str] = {"-F": "-F", "-P": "-P"}
"""`git grep`の種別指定のうち`rg`が同じ意味で受理するもの。

`rg`は`-E`を文字コードの指定として解釈し、基本正規表現を受理しない。
このため`-E`と`-G`は置換後の`rg`へ引き継がず、`rg`の既定の正規表現へ委ねる。
"""


def _preserved_grep_options(options: frozenset[str], *, for_ripgrep: bool) -> list[str]:
    """元の呼び出しが指定した出力形式と種別のうち、置換後も保つものを返す。"""
    preserved: list[str] = []
    if options & _GREP_LINE_NUMBER_OPTIONS:
        preserved.append("-n")
    pattern_type = next(
        (_GREP_PATTERN_TYPE_BY_OPTION[option] for option in sorted(options) if option in _GREP_PATTERN_TYPE_BY_OPTION),
        None,
    )
    if pattern_type is not None:
        mapped = _RG_PATTERN_TYPE_BY_GIT_GREP.get(pattern_type) if for_ripgrep else pattern_type
        if mapped is not None:
            preserved.append(mapped)
    return preserved


def _describe_grep_replacement(
    patterns: Sequence[str],
    targets: Sequence[str],
    base: pathlib.Path,
    options: frozenset[str] = frozenset(),
) -> str:
    """遮断対象ごとのGit作業ツリー判定と、置換後のコマンド文字列を通知本文へまとめる。

    判定は遮断が確定した経路でだけ実行する。全ての対象が同じGit作業ツリーへ属する場合は`git grep`、
    いずれも属さない場合は`rg`の形を、当該呼び出しのpatternとパスを埋めた状態で示す。
    元の呼び出しが指定した行番号出力、種別及び複数のpatternは置換後も保つ。
    提示が元の指定を欠くと、受領した実行主体が指定を戻して組み直す往復が生じる。
    対象が双方を含む場合とpattern本文を一意に取り出せない場合は、確定できなかった理由を示す。
    """
    described: list[str] = []
    roots: list[str | None] = []
    for target in targets:
        candidate = pathlib.Path(target).expanduser()
        resolved = candidate if candidate.is_absolute() else base / candidate
        root = _git_status.get_worktree_root(str(resolved))
        roots.append(root)
        described.append(f"`{target}`はGit作業ツリー`{root}`に属する" if root is not None else f"`{target}`はGit管理外")
    judgement = "対象の判定: " + "、".join(described) + "。"
    if not patterns:
        return judgement + "置換後の形を確定できない理由: 当該呼び出しのpattern本文を一意に取り出せない。"
    operands = " ".join(shlex.quote(target) for target in targets)
    pattern_arguments = " ".join(f"-e {shlex.quote(pattern)}" for pattern in patterns)
    unique_roots = set(roots)
    if unique_roots == {None}:
        preserved = " ".join(_preserved_grep_options(options, for_ripgrep=True))
        replacement = f"rg {preserved} {pattern_arguments} -- {operands}".replace("  ", " ").rstrip()
    elif len(unique_roots) == 1:
        preserved = " ".join(_preserved_grep_options(options, for_ripgrep=False))
        prefix = f"git -C {shlex.quote(str(roots[0]))} grep"
        replacement = f"{prefix} {preserved} {pattern_arguments} -- {operands}".replace("  ", " ").rstrip()
    else:
        return judgement + "置換後の形を確定できない理由: 対象がGit管理対象と管理外の双方を含む。"
    return judgement + f"置換後のコマンド: `{replacement}`"


def _check_bash_recursive_grep_without_exclusion(command: str, cwd: str) -> str | None:
    """除外指定の無い再帰`grep`を、補正せず初回から遮断する。

    検索対象の内容、ファイル種別及びリンク構造はBash入力に現れないため、`rg`と出力及び終了状態が
    同値になる入力集合を構文だけから確定できない。
    遮断が確定した経路では、対象ごとにGit作業ツリーへ属するかを判定して通知本文へ載せる。
    実行主体が`git grep`と`rg`のどちらを選ぶかを別の呼び出しで取得せずに決められるようにするためである。
    """
    base = pathlib.Path(cwd) if cwd else pathlib.Path.cwd()
    targets: list[str] | None = None
    patterns: tuple[str, ...] = ()
    preserved_options: frozenset[str] = frozenset()
    for pipeline in _extract_execution_pipelines(command):
        for segment in pipeline:
            if not segment.resolved or segment.tokens[0] not in _GREP_COMMANDS:
                continue
            parsed = _grep_file_operands(segment)
            if parsed is None:
                raw_options = _argument_tokens(segment)
                has_recursive_option = any(
                    token in {"-r", "-R", "--recursive"}
                    or (
                        token.startswith("-")
                        and not token.startswith("--")
                        and token[1:2] not in {"e", "f"}
                        and any(letter in "rR" for letter in token[1:])
                    )
                    for token in raw_options
                )
                has_exclusion = any(
                    token in {"--include", "--exclude", "--exclude-dir"}
                    or token.startswith(("--include=", "--exclude=", "--exclude-dir="))
                    for token in raw_options
                )
                if has_recursive_option and not has_exclusion:
                    targets = []
                    break
                continue
            segment_patterns, files, options = parsed
            if not options & {"-r", "-R", "--recursive"}:
                continue
            if options & {"--include", "--exclude", "--exclude-dir"}:
                continue
            directories = [token for token in files if token != "-" and (token.endswith("/") or (base / token).is_dir())]
            if not files or directories:
                targets = directories
                patterns = segment_patterns
                preserved_options = options
                break
        if targets is not None:
            break
    if targets is None:
        return None
    # operandを解決できない区間と、operandを省略した呼び出しは実効の走査起点であるcwdを対象とする。
    described = _describe_grep_replacement(patterns, targets or [str(base)], base, preserved_options)
    print(
        _block_notice(
            f"block: 除外設定を反映しない再帰`grep`を、安全に`rg`へ補正できない形でディレクトリへ実行している。{described}",
            fix=_RECURSIVE_GREP_WITHOUT_EXCLUSION_FIX,
        ),
        file=sys.stderr,
    )
    return "block"


def _check_bash_help_with_execution(command: str) -> str | None:
    """同じ実行ファイルのヘルプ取得と、同じ実行ファイルのヘルプ取得以外の区間との並置を検出する。

    `-h`は実行ファイルごとに意味が異なるためヘルプ指定として扱わない。
    ヘルプ取得だけを並べた呼び出しは、警告が求める実行の分離を適用する区間を持たないため対象にしない。
    通した場合の結果は受理形式の確定が同じ呼び出しの内側へ入ることに限り、復元できるため警告で返す。
    """
    segments = [segment for segment in _extract_execution_segments(command) if segment.resolved and segment.tokens]
    names = [pathlib.PurePosixPath(segment.tokens[0]).name for segment in segments]
    help_names = {name for name, segment in zip(names, segments, strict=True) if _segment_is_help_only(segment)}
    non_help_names = {name for name, segment in zip(names, segments, strict=True) if not _segment_is_help_only(segment)}
    if help_names & non_help_names:
        return _llm_notice(
            "同じシェル呼び出しの中でヘルプの取得と同じ実行ファイルの実行が並んでいる。\n"
            "対処: 先にヘルプだけを実行して受理形式を確定し、実行は別の呼び出しへ分ける。",
            tag=_WARN_TAG,
            removable_cause=True,
        )
    return None


def _tee_operand_is_non_regular_file(token: str) -> bool:
    """`tee`のoperandが既知の特殊出力先または既存の非通常ファイルかを返す。"""
    normalized = token.rstrip("/")
    if _TEE_NON_FILE_OPERAND_PATTERN.fullmatch(normalized):
        return True
    path = pathlib.Path(normalized)
    if not path.is_absolute():
        return False
    try:
        return path.exists() and not path.is_file()
    except OSError:
        return False


def _tee_saves_to_file(segment: _ExecutionSegment) -> bool:
    """`tee`区間に標準出力を保存するファイル引数があるかを返す。"""
    if not _segment_starts_with(segment, (_OUTPUT_FULL_SAVE_COMMAND,)):
        return False
    option_terminator = False
    tokens = _argument_tokens(segment)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _tee_operand_is_non_regular_file(token):
            index += 1
            continue
        if option_terminator:
            return True
        if token == "--":
            option_terminator = True
        elif token.startswith("-") and token != "-":
            pass
        else:
            return True
        index += 1
    return False


def _make_option_requires_value(token: str) -> bool:
    """`make`の短長オプションが別トークンの必須値を取るかを返す。"""
    if token in _MAKE_OPTIONS_WITH_VALUE:
        return True
    if not token.startswith("--") or "=" in token:
        return False
    matches = tuple(option for option in _MAKE_LONG_OPTIONS if option.startswith(token))
    return len(matches) == 1 and matches[0] in _MAKE_OPTIONS_WITH_VALUE


def _make_targets(segment: _ExecutionSegment) -> tuple[str, ...]:
    """`make`区間からオプションと変数代入を除いたターゲット名を返す。"""
    if not _segment_starts_with(segment, ("make",)):
        return ()
    targets: list[str] = []
    option_terminator = False
    tokens = iter(_argument_tokens(segment))
    for token in tokens:
        if _MAKE_ASSIGNMENT_PATTERN.match(token):
            continue
        if option_terminator:
            targets.append(token)
            continue
        if token == "--":
            option_terminator = True
            continue
        if token.startswith("-"):
            if _make_option_requires_value(token):
                next(tokens, None)
            continue
        targets.append(token)
    return tuple(targets)


def _option_operand_index(
    tokens: tuple[str, ...],
    start: int,
    options_with_value: frozenset[str],
    options_without_value: frozenset[str],
) -> int | None:
    """先頭のオプション列を走査し、最初のoperandの位置を返す。"""
    index = start
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1 if index + 1 < len(tokens) else None
        name, separator, _ = token.partition("=")
        if name in options_with_value:
            index += 1 if separator else 2
            continue
        if token in options_without_value:
            index += 1
            continue
        short_value_option = next(
            (option for option in options_with_value if len(option) == 2 and token.startswith(option) and token != option),
            None,
        )
        if short_value_option is not None:
            index += 1
            continue
        if token.startswith("-"):
            return None
        return index
    return None


def _mise_task_targets(tokens: tuple[str, ...], start: int) -> tuple[str, ...]:
    """`mise run`のオプションと複数タスク区切りを除いたタスク名を返す。"""
    first = _option_operand_index(tokens, start, _MISE_RUN_OPTIONS_WITH_VALUE, _MISE_RUN_OPTIONS_WITHOUT_VALUE)
    if first is None:
        return ()
    targets = [tokens[first]]
    targets.extend(tokens[index + 1] for index, token in enumerate(tokens[first:-1], start=first) if token == ":::")
    return tuple(targets)


def _task_runner_targets(segment: _ExecutionSegment) -> tuple[str, ...]:
    """既知のタスクランナー起動形からタスク名を返す。

    外部のタスク定義へ問い合わせず、静的に確定できる起動形だけを扱う。`mise x`は
    `mise exec`の別名であり、`pnpm run-script`は`pnpm run`の別名である。
    """
    runners = (
        (("mise", "run"), _MISE_RUN_OPTIONS_WITH_VALUE, _MISE_RUN_OPTIONS_WITHOUT_VALUE),
        (("mise", "r"), _MISE_RUN_OPTIONS_WITH_VALUE, _MISE_RUN_OPTIONS_WITHOUT_VALUE),
        (("mise", "tasks", "run"), _MISE_RUN_OPTIONS_WITH_VALUE, _MISE_RUN_OPTIONS_WITHOUT_VALUE),
        (("pnpm", "run"), _PNPM_RUN_OPTIONS_WITH_VALUE, _PNPM_RUN_OPTIONS_WITHOUT_VALUE),
        (("pnpm", "run-script"), _PNPM_RUN_OPTIONS_WITH_VALUE, _PNPM_RUN_OPTIONS_WITHOUT_VALUE),
        (("npm", "run"), _NPM_RUN_OPTIONS_WITH_VALUE, _NPM_RUN_OPTIONS_WITHOUT_VALUE),
    )
    tokens = _argument_tokens(segment, 0)
    for prefix, options_with_value, options_without_value in runners:
        if not _segment_starts_with(segment, prefix):
            continue
        if prefix[0] == "mise":
            return _mise_task_targets(tokens, len(prefix))
        target_index = _option_operand_index(tokens, len(prefix), options_with_value, options_without_value)
        return () if target_index is None else (tokens[target_index],)
    return ()


def _segment_requires_complete_output(segment: _ExecutionSegment) -> bool:
    """区間が全量観測を必要とするコマンドの実行位置から始まるかを返す。

    `mise tasks ls --json`でタスク本文を解決すると、フック実行時の外部プロセスの成否、
    miseの導入及び設定の信頼へ判定が依存し、失敗時は結局キーワード規則へ縮退する。
    そのため、外部プロセスへ問い合わせず静的な起動形とタスク名のキーワードで判定する。
    """
    if _has_uv_terminal_option(segment.tokens):
        return False
    if any(_segment_starts_with(segment, prefix) for prefix in _COMPLETE_OUTPUT_EXCLUDED_PREFIXES):
        return False
    return (
        any(_segment_starts_with(segment, prefix) for prefix in _VERIFICATION_COMMAND_PREFIXES)
        or _segment_is_state_changing(segment)
        or any(_segment_starts_with(segment, prefix) for prefix in _COMPLETE_OUTPUT_COMMAND_PREFIXES)
        or segment.is_agent_toolkit_script
        or any(
            keyword in target.lower()
            for target in (*_make_targets(segment), *_task_runner_targets(segment))
            for keyword in _VERIFICATION_TARGET_KEYWORDS
        )
    )


def _pipeline_truncation_detections(pipeline: Sequence[_ExecutionSegment]) -> list[tuple[str, str]]:
    """1つのパイプライン内で検出した、全量観測が必要なコマンドと切り詰めコマンドの対を返す。

    全量観測が必要なコマンドより後方で最初に現れる切り詰めコマンドを発生点とし、
    その手前に`tee`が無い場合に当該対を加える。`tee`で全量を先に保存してから抽出する形は対象外とし、
    切り詰めた後に`tee`で保存する形は保存内容が既に切り詰め後であるため対象とする。
    同一パイプラインに対象コマンドが複数ある場合は、該当する全件を返す。
    状態変更コマンドでは`head`・`tail`に加えて`grep`系も切り詰めとして扱う。
    通知本文が是正の対象を一意に示すため、真偽ではなく検出した対を返す。
    """
    detections: list[tuple[str, str]] = []
    for index, segment in enumerate(pipeline):
        if not _segment_requires_complete_output(segment):
            continue
        following = pipeline[index + 1 :]
        truncation_commands = (
            _STATE_OUTPUT_TRUNCATION_COMMANDS if _segment_is_state_changing(segment) else _OUTPUT_TRUNCATION_COMMANDS
        )
        truncation_index = next(
            (position for position, item in enumerate(following) if item.resolved and item.tokens[0] in truncation_commands),
            None,
        )
        if truncation_index is None:
            continue
        if any(_tee_saves_to_file(item) for item in following[:truncation_index]):
            continue
        detections.append((segment.tokens[0], following[truncation_index].tokens[0]))
    return detections


def _pipeline_truncates_required_output(pipeline: Sequence[_ExecutionSegment]) -> bool:
    """1つのパイプライン内で、必要な出力が全量保存されないまま切り詰められるかを判定する。"""
    return bool(_pipeline_truncation_detections(pipeline))


def _check_bash_output_truncation(command: str, session_id: str) -> str | None:
    """全量観測が必要な出力を`tail`・`head`で切り詰める指定を初回から遮断する。

    全量をファイルへ保存してから必要部分を抽出する形、構造化出力をレコード種別で抽出する形、
    または分離したコンテキストで実行する形を解消手段として示す。
    全パイプラインの検証コマンドと保存本文を返すコマンドを対象とし、1件でも切り詰めに該当すれば1回だけ通知する。
    通知本文へは、検出の対象とした直列区間と当該区間で切り詰めと判定したコマンドの表記を区間ごとに並べる。
    是正の対象を示さない通知は、実行主体が入力全体を推測で書き直し、同じ形の再提出を反復させる。
    `;`・`&&`・`||`・`&`で連結した後続コマンドは対象コマンドの出力を受け取らないため対象外とする。
    判定は`_extract_execution_pipelines`が返す実行位置で行うため、対象コマンド名を検索語・引数として
    含むだけの場合は検出しない。実行位置を確定できない区間と、実行位置以外で起動される対象コマンドも
    検出しない（助言であり非検出側の誤差の実害が小さいため）。
    """
    detected: list[str] = [
        f"- 第{index}直列区間: 全量観測が必要なコマンドは`{required_command}`、"
        f"切り詰めと判定したコマンドは`{truncation_command}`"
        for index, pipeline in enumerate(_extract_execution_pipelines(command), start=1)
        for required_command, truncation_command in _pipeline_truncation_detections(pipeline)
    ]
    if not detected:
        return None
    del session_id
    print(
        _block_notice(
            "block: 全量観測が必要なコマンドの実行出力を`tail`・`head`・`grep`などで限定している。\n" + "\n".join(detected),
            fix=(
                "実行出力を`tail`・`head`で切り詰めている場合を含め、最初に全出力を保存し、"
                "保存済みファイルから抽出するか、構造化出力から必要なレコード種別を選ぶか、"
                "agents_serverの`start_shell`ツールを使って分離したコンテキストでコマンドを実行する。"
                "実行中の出力を切り詰めない。"
            ),
        ),
        file=sys.stderr,
    )
    return "block"


def _contains_unquoted_status_expansion(token: str) -> bool:
    """トークンに単一引用符で保護されていない`$?`があるかを返す。"""
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(token):
        char = token[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\":
                escaped = True
            elif char == '"':
                quote = None
            elif token.startswith("$?", index):
                return True
            index += 1
            continue
        if char == "\\":
            escaped = True
        elif char in {"'", '"'}:
            quote = char
        elif token.startswith("$?", index):
            return True
        index += 1
    return False


def _status_report_follows_truncation(command: str) -> bool:
    """直後のserial commandが切り詰め対象コマンドの終了状態を利用する形かを返す。"""
    tokens = _command_tokens_with_quotes(command)
    if not tokens:
        return False
    if any("PIPESTATUS" in token for token in tokens):
        return False
    return any(_contains_unquoted_status_expansion(token) for token in tokens)


def _check_bash_output_status_after_truncation(command: str) -> str | None:
    """切り詰め直後の`$?`報告が検証コマンドの状態を隠す場合に診断を返す。"""
    serial_commands = _split_serial_shell_commands(command, separators=_STATUS_SHELL_SEPARATORS)
    for index, serial_command in enumerate(serial_commands[:-1]):
        if not any(_pipeline_truncates_required_output(pipeline) for pipeline in _extract_execution_pipelines(serial_command)):
            continue
        if _status_report_follows_truncation(serial_commands[index + 1]):
            return _llm_notice(
                "warn: 出力を切り詰めるパイプラインの後にある`$?`は、対象コマンドではなく"
                "`head`・`tail`の終了状態を示す。出力を切り詰める前に対象コマンドの終了状態を保持する。",
                tag=_WARN_TAG,
                removable_cause=True,
            )
    return None


def _is_high_capacity_home_target(token: str) -> bool:
    """高容量のユーザー領域を表す検索対象かを返す。"""
    normalized = token.rstrip("/")
    home = pathlib.Path.home()
    targets = {
        str(home),
        str(home / ".local"),
        str(home / ".npm"),
        str(home / ".codex"),
        str(home / ".claude"),
        "~",
        "~/.local",
        "~/.npm",
        "~/.codex",
        "~/.claude",
        "$HOME",
        "$HOME/.local",
        "$HOME/.npm",
        "$HOME/.codex",
        "$HOME/.claude",
        "${HOME}",
        "${HOME}/.local",
        "${HOME}/.npm",
        "${HOME}/.codex",
        "${HOME}/.claude",
    }
    return normalized in targets


_RECURSIVE_SEARCH_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--column",
        "--fixed-strings",
        "--heading",
        "--ignore-case",
        "--line-number",
        "--no-heading",
        "--smart-case",
        "--with-filename",
        "--word-regexp",
        "-F",
        "-H",
        "-S",
        "-i",
        "-n",
        "-w",
    }
)
"""検索の対象集合と出力量を減らさず、値を取らない付随オプション。"""
_RECURSIVE_SEARCH_OPTIONS_WITH_VALUE: frozenset[str] = frozenset({"--color"})
"""検索の対象集合と出力量を減らさず、値を1つ取る付随オプション。"""


def _pipeline_has_recursive_home_search(tokens: Sequence[str]) -> bool:
    """付随オプションだけを許して、高容量領域だけを対象とする無限定再帰検索を判定する。

    範囲を制限し得る未知オプションは誤警告を避けるため非検出とする。
    """
    if len(tokens) < 3:
        return False
    if tokens[0] == "rg":
        arguments = tokens[1:]
    elif tokens[0] == "grep" and tokens[1] in {"-r", "-R"}:
        arguments = tokens[2:]
    else:
        return False
    operands: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in _RECURSIVE_SEARCH_OPTIONS_WITHOUT_VALUE:
            index += 1
            continue
        if token in _RECURSIVE_SEARCH_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in _RECURSIVE_SEARCH_OPTIONS_WITH_VALUE):
            index += 1
            continue
        if token.startswith("-"):
            return False
        operands.append(token)
        index += 1
    if len(operands) < 2:
        return False
    paths = operands[1:]
    return all(_is_high_capacity_home_target(path) for path in paths)


def _check_bash_recursive_home_search(command: str) -> str | None:
    """高容量のユーザー領域を無限定に再帰検索する実行位置へ警告を返す。"""
    if not any(
        segment.resolved and _pipeline_has_recursive_home_search(_argument_tokens(segment, 0))
        for pipeline in _extract_execution_pipelines(command)
        for segment in pipeline
    ):
        return None
    return _llm_notice(
        "warn: 再帰検索が大容量のユーザーディレクトリを対象としている。"
        "対象ディレクトリを狭め、不要領域を除外し、検索対象と出力に上限を設けるか、"
        "`rg`・再帰`grep`を使う前に分離した実行コンテキストで検索する。",
        tag=_WARN_TAG,
        removable_cause=True,
    )


_FIND_GLOBAL_OPTIONS = frozenset({"-H", "-L", "-P"})
_FIND_BOUNDS = frozenset({"-prune", "-maxdepth", "-xdev", "-mount"})
_FIND_KNOWN_EXPRESSION_OPTIONS = frozenset(
    {
        "-amin",
        "-anewer",
        "-atime",
        "-cmin",
        "-cnewer",
        "-ctime",
        "-daystart",
        "-delete",
        "-depth",
        "-empty",
        "-exec",
        "-execdir",
        "-executable",
        "-false",
        "-files0-from",
        "-fls",
        "-follow",
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-fstype",
        "-gid",
        "-group",
        "-ignore_readdir_race",
        "-ilname",
        "-iname",
        "-inum",
        "-ipath",
        "-iregex",
        "-iwholename",
        "-links",
        "-lname",
        "-ls",
        "-mmin",
        "-mtime",
        "-name",
        "-newer",
        "-nogroup",
        "-noignore_readdir_race",
        "-noleaf",
        "-nouser",
        "-nowarn",
        "-ok",
        "-okdir",
        "-path",
        "-perm",
        "-print",
        "-print0",
        "-printf",
        "-quit",
        "-readable",
        "-regex",
        "-regextype",
        "-samefile",
        "-size",
        "-true",
        "-type",
        "-uid",
        "-used",
        "-user",
        "-warn",
        "-wholename",
        "-writable",
        "-xtype",
    }
)
_LS_SHORT_OPTIONS_WITHOUT_VALUE = frozenset("ABCDFGHKLNQRSUXZabcdfghiklmnopqrstvux1")
_LS_SHORT_OPTIONS_WITH_VALUE = frozenset({"I", "T", "w"})
_LS_LONG_OPTIONS_WITHOUT_VALUE = frozenset(
    {
        "--all",
        "--almost-all",
        "--author",
        "--context",
        "--directory",
        "--dereference",
        "--dereference-command-line",
        "--dereference-command-line-symlink-to-dir",
        "--dired",
        "--escape",
        "--file-type",
        "--full-time",
        "--group-directories-first",
        "--help",
        "--hide-control-chars",
        "--human-readable",
        "--ignore-backups",
        "--inode",
        "--kibibytes",
        "--literal",
        "--no-group",
        "--numeric-uid-gid",
        "--quote-name",
        "--recursive",
        "--reverse",
        "--show-control-chars",
        "--si",
        "--size",
        "--version",
        "--zero",
    }
)
_LS_LONG_OPTIONS_WITH_VALUE = frozenset(
    {
        "--block-size",
        "--classify",
        "--color",
        "--format",
        "--hide",
        "--hyperlink",
        "--ignore",
        "--indicator-style",
        "--quoting-style",
        "--sort",
        "--tabsize",
        "--time",
        "--time-style",
        "--width",
    }
)
_LS_LONG_OPTIONS_WITH_OPTIONAL_VALUE = frozenset({"--classify", "--color", "--hyperlink"})


def _find_unbounded_start_paths(tokens: Sequence[str]) -> list[str] | None:
    """走査範囲を限定しない`find`区間の起点パスを返す。

    `-prune`・`-maxdepth`・`-xdev`・`-mount`のいずれかを対象限定とみなす。
    限定がある場合と、範囲を制限し得る未知のオプションを含む場合は、限定の有無を構文だけから
    確定できないためNoneを返す。起点を省略した呼び出しもNoneを返す。
    """
    index = 1
    while index < len(tokens) and tokens[index] in _FIND_GLOBAL_OPTIONS:
        index += 1
    paths: list[str] = []
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-") or token in {"(", "!", ","}:
            break
        paths.append(token)
        index += 1
    if not paths:
        return None
    expression = tokens[index:]
    if any(token in _FIND_BOUNDS for token in expression):
        return None
    known_expression = all(
        not token.startswith("-")
        or token in _FIND_KNOWN_EXPRESSION_OPTIONS
        or token.startswith("-newer")
        and len(token) == len("-newer") + 2
        for token in expression
    )
    return paths if known_expression else None


def _find_has_unbounded_home_traversal(tokens: Sequence[str]) -> bool:
    """`find`区間が高容量領域だけを対象とし、走査範囲を限定しない場合に真を返す。"""
    paths = _find_unbounded_start_paths(tokens)
    return paths is not None and all(_is_high_capacity_home_target(path) for path in paths)


def _find_traverses_filesystem_root(tokens: Sequence[str]) -> bool:
    """`find`区間がファイルシステムの根を起点とし、走査範囲を限定しない場合に真を返す。"""
    if not tokens or tokens[0] != "find":
        return False
    paths = _find_unbounded_start_paths(tokens)
    return paths is not None and any(not path.rstrip("/") for path in paths)


def _ls_has_unbounded_home_traversal(tokens: Sequence[str]) -> bool:
    """`ls`区間が再帰指定と高容量領域だけのoperandを持つ場合に真を返す。"""
    operands: list[str] = []
    recursive = False
    option_terminator = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if option_terminator or token == "-" or not token.startswith("-"):
            operands.append(token)
            index += 1
            continue
        if token == "--":
            option_terminator = True
            index += 1
            continue
        name, separator, _ = token.partition("=")
        if name in _LS_LONG_OPTIONS_WITH_VALUE:
            if name in _LS_LONG_OPTIONS_WITH_OPTIONAL_VALUE:
                index += 1
            else:
                index += 1 if separator else 2
            continue
        if token in _LS_LONG_OPTIONS_WITHOUT_VALUE and not separator:
            recursive = recursive or token == "--recursive"
            index += 1
            continue
        if token.startswith("--"):
            return False
        short_options = token[1:]
        value_option_index = next(
            (position for position, character in enumerate(short_options) if character in _LS_SHORT_OPTIONS_WITH_VALUE),
            None,
        )
        if value_option_index is not None:
            leading = short_options[:value_option_index]
            if any(character not in _LS_SHORT_OPTIONS_WITHOUT_VALUE for character in leading):
                return False
            recursive = recursive or "R" in leading
            index += 1 if value_option_index < len(short_options) - 1 else 2
            continue
        if not short_options or any(character not in _LS_SHORT_OPTIONS_WITHOUT_VALUE for character in short_options):
            return False
        recursive = recursive or "R" in short_options
        index += 1
    return recursive and bool(operands) and all(_is_high_capacity_home_target(path) for path in operands)


def _pipeline_has_unbounded_home_traversal(tokens: Sequence[str]) -> bool:
    """対象限定を伴わずに高容量のユーザー領域だけを走査する`find`と`ls -R`を判定する。

    `find`では`-prune`・`-maxdepth`・`-xdev`・`-mount`のいずれかを対象限定とみなす。
    `ls`は除外の手段を持たないため、再帰指定と高容量領域の指定だけで判定する。
    `fd`はoperandのパターンとパスを構文だけでは判別できず、既定で除外設定を反映するため対象にしない。
    """
    if not tokens:
        return False
    if tokens[0] == "find":
        return _find_has_unbounded_home_traversal(tokens)
    if tokens[0] == "ls":
        return _ls_has_unbounded_home_traversal(tokens)
    return False


def _check_bash_unbounded_home_traversal(command: str) -> str | None:
    """対象限定の無い`find`・`ls -R`による高容量領域の走査へ警告を返す。"""
    if not any(
        segment.resolved and _pipeline_has_unbounded_home_traversal(_argument_tokens(segment, 0))
        for pipeline in _extract_execution_pipelines(command)
        for segment in pipeline
    ):
        return None
    return _llm_notice(
        "warn: 除外設定を持たない走査コマンドが大容量のユーザーディレクトリを無限定に走査している。"
        "`find`では`-prune`と`-maxdepth`で対象集合を先に限定し、"
        "ファイル一覧の取得には除外設定を反映する`rg --files`を使う。",
        tag="warn",
        removable_cause=True,
    )


_UNBOUNDED_ROOT_TRAVERSAL_FIX = (
    "探索する対象を含むディレクトリを走査の起点へ指定する。"
    "根からの探索が必要な場合は`-maxdepth`で深さを限定するか、`-prune`で走査対象を限定する。"
)


def _check_bash_unbounded_root_traversal(command: str) -> str | None:
    """走査範囲を限定しないファイルシステムの根からの`find`を初回から遮断する。

    根からの走査は`/proc`・`/sys`とマウント先を含み、実用的な時間で終わらない。
    観測事象では発行した委譲先の応答が返らなくなり、背景ジョブの停止を要した。
    この停止は最初の発行で生じるため、1件目を警告として通過させる形にせず初回から遮断する。
    高容量のユーザー領域を起点とする走査は`_check_bash_unbounded_home_traversal`の警告で扱う。
    """
    if not any(
        segment.resolved and _find_traverses_filesystem_root(_argument_tokens(segment, 0))
        for pipeline in _extract_execution_pipelines(command)
        for segment in pipeline
    ):
        return None
    print(
        _block_notice(
            "block: 走査範囲を限定しない`find`をファイルシステムの根から実行している。",
            fix=_UNBOUNDED_ROOT_TRAVERSAL_FIX,
        ),
        file=sys.stderr,
    )
    return "block"


# --- Bash: codex exec未決事項の念押し ---

_CODEX_EXEC_PREFIX: tuple[str, ...] = ("codex", "exec")
_CODEX_EXEC_RESUME_PREFIX: tuple[str, ...] = ("codex", "exec", "resume")


def _check_bash_codex_exec(command: str) -> str | None:
    """Codex exec（resume以外）を検出した場合に未決事項確認の警告文を返す。

    判定は`_extract_execution_segments`が返す実行位置で行い、実行位置のトークン列が`codex exec`で
    始まる区間を対象とする。当該区間が`codex exec resume`である場合は除外する。
    `codex exec`という文字列を引数として含むだけの読み取り操作は検出しない。
    実行位置を確定できない区間と、実行位置以外で起動される`codex exec`も検出しない
    （助言であり非検出側の誤差の実害が小さいため）。
    """
    for segment in _extract_execution_segments(command):
        if not _segment_starts_with(segment, _CODEX_EXEC_PREFIX):
            continue
        if _segment_starts_with(segment, _CODEX_EXEC_RESUME_PREFIX):
            continue
        break
    else:
        return None
    return _llm_notice(
        "`codex exec`を実行しようとしている。計画ファイルをレビューへ提出する実行であれば、"
        "ユーザー確認ではなく推測で確定した判断が無いかを確認し、未解決の質問をユーザーと解消してから続行する。"
    )


# --- 規範が明文で禁じる引数の形の実行前検出 ---
#
# 本節の各判定は、`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」が
# 既に明文で禁じている形のうち、静的に確定できるものを実行前に検出する。
# 通した場合の結果はいずれも当該コマンドの失敗に限り復元できるため、応答水準は警告とする。

_GIT_GREP_PATTERN_TYPE_OPTIONS: frozenset[str] = frozenset(
    {"-F", "-E", "-P", "-G", "--fixed-strings", "--basic-regexp", "--extended-regexp", "--perl-regexp"}
)


def _git_subcommand_tokens(segment: _ExecutionSegment) -> tuple[str, tuple[str, ...]] | None:
    """`git`区間のサブコマンド名と、当該サブコマンド以降の引数を返す。"""
    if not segment.resolved or not segment.tokens:
        return None
    if pathlib.PurePath(segment.tokens[0]).name != "git":
        return None
    index = 1
    tokens = segment.tokens
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


_GIT_GREP_BASIC_REGEXP_METACHARACTERS: frozenset[str] = frozenset(".*[]^$\\")
"""`git grep`が種別の指定なしに基本正規表現として解釈するメタ文字。

いずれも含まないpatternでは`-F`を指定した場合と一致結果が変わらないため、当該場合は警告しない。
"""
_GIT_GREP_PATTERN_OPTIONS: frozenset[str] = frozenset({"-e", "--regexp"})
_GIT_GREP_PATTERN_FILE_OPTIONS: frozenset[str] = frozenset({"-f", "--file"})


def _git_grep_pattern(arguments: Sequence[str]) -> str | None:
    """`git grep`の引数列からpattern本文を一意に取り出す。

    取り出せない場合はNoneを返す。ファイルからpatternを読む指定、オプション終端の位置によって
    pattern本文が定まらない指定が該当する。
    """
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            return None
        name = token.split("=", 1)[0]
        if name in _GIT_GREP_PATTERN_FILE_OPTIONS:
            return None
        if name in _GIT_GREP_PATTERN_OPTIONS:
            if "=" in token:
                return token.split("=", 1)[1]
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if _attached_short_value_option(token, _GIT_GREP_PATTERN_FILE_OPTIONS) is not None:
            return None
        attached_pattern = _attached_short_value_option(token, _GIT_GREP_PATTERN_OPTIONS)
        if attached_pattern is not None:
            return token[len(attached_pattern) :]
        if name in _GIT_GREP_VALUED_OPTIONS:
            index += 1 if "=" in token else 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return None


def _git_grep_specifies_pattern_type(token: str) -> bool:
    """`git grep`のトークンがpattern種別の指定に当たるかを返す。

    値を密着させた短縮オプション（`-ePAT`・`-m2`など）では、値の側の文字が種別を表す文字と
    一致しても種別の指定に当たらない。当該形を種別の指定として扱うと、種別を指定しない呼び出しを
    指定済みと判定して検出から外す。
    """
    if token in _GIT_GREP_PATTERN_TYPE_OPTIONS:
        return True
    if not token.startswith("-") or token.startswith("--"):
        return False
    attached_value_options = _GIT_GREP_VALUED_OPTIONS | _GIT_GREP_PATTERN_OPTIONS | _GIT_GREP_PATTERN_FILE_OPTIONS
    if _attached_short_value_option(token, attached_value_options) is not None:
        return False
    return any(letter in "FEPG" for letter in token[1:])


_GIT_GREP_BASIC_ALTERNATION = r"\|"
_GIT_GREP_BASIC_ALTERNATION_FIX = (
    "選択として検索する場合は`-E`を明示し、patternを`(a|b)`の形へ書き換える。"
    rf"`{_GIT_GREP_BASIC_ALTERNATION}`をリテラルとして検索する場合は`-F`を明示する。"
)


def _check_bash_git_grep_pattern_type(command: str) -> str | None:
    """`git grep`でpattern種別を明示せず、かつ指定の有無で一致結果が変わる呼び出しを検出する。

    種別を指定しない`git grep`は基本正規表現として解釈するため、メタ文字を含まないpatternでは
    `-F`を指定した場合と一致結果が変わらない。当該場合は是正すべき差異を示さないため警告しない。
    pattern本文を一意に取り出せない指定は、差異の有無を確定できないため警告する。
    """
    for segment in _extract_execution_segments(command):
        resolved = _git_subcommand_tokens(segment)
        if resolved is None or resolved[0] != "grep":
            continue
        arguments = without_shell_redirections(resolved[1])
        if any(token == "--help" for token in arguments):
            continue
        if any(_git_grep_specifies_pattern_type(token) for token in arguments):
            continue
        pattern = _git_grep_pattern(arguments)
        if pattern is not None and not any(character in _GIT_GREP_BASIC_REGEXP_METACHARACTERS for character in pattern):
            continue
        if pattern is not None and _GIT_GREP_BASIC_ALTERNATION in pattern:
            print(
                _block_notice(
                    "block: `git grep`が種別を指定せず、patternへ"
                    f"`{_GIT_GREP_BASIC_ALTERNATION}`を含んでいる。"
                    "基本正規表現は当該表記を選択として解釈しないため、この呼び出しは意図した一致を返さない。",
                    fix=_GIT_GREP_BASIC_ALTERNATION_FIX,
                ),
                file=sys.stderr,
            )
            return "block"
        return _llm_notice(
            "`git grep`が固定文字列・拡張正規表現・Perl互換正規表現のいずれの種別も指定していない。\n"
            "対処: 検索意図に応じて`-F`・`-E`・`-P`のいずれかを明示し、"
            "オプション、pattern、`--`、pathspecの順で引数を置く。"
            "patternが正規表現のメタ文字を含む場合は`-E`、リテラルとして検索する場合は`-F`を選ぶ。",
            tag=_WARN_TAG,
            removable_cause=True,
            summary=_format_git_grep_pattern_type_summary(pattern),
        )
    return None


def _format_git_grep_pattern_type_summary(pattern: str | None) -> str:
    """2件目以降の通知へ用いる要旨を、警告の対象だけで組み立てる。

    対処の案内と種別の選び方は1件目の本文が既に届けているため、要旨から外す。
    """
    target = f"`{pattern}`" if pattern is not None else "pattern本文を一意に取り出せない指定"
    return f"`git grep`が種別を指定していない。対象のpattern: {target}"


_SHELL_METACHARACTERS_IN_WORD = frozenset({"(", ")", "`"})
_SHELL_SUBSTITUTION_PREFIXES = "$<>"


def _substitution_open_index(text: str, search_from: int) -> tuple[int, int] | None:
    """コマンド置換、プロセス置換又はサブシェルを開く括弧の位置と接頭の長さを返す。

    対象は、`$(`・`<(`・`>(`の形と、語の先頭に現れる`(`とする。
    語の内側に接頭なしで現れる`(`（`report(1).txt`など）は引用の崩れであり、対象から外す。
    """
    index = text.find("(", search_from)
    while index >= 0:
        if index > 0 and text[index - 1] in _SHELL_SUBSTITUTION_PREFIXES:
            return index, 1
        if index == 0 or text[index - 1].isspace():
            return index, 0
        index = text.find("(", index + 1)
    return None


def _strip_balanced_substitutions(text: str) -> str:
    """対応の取れたコマンド置換、プロセス置換及びサブシェルを取り除いた残りを返す。

    これらの括弧は構文上の必然として現れ、引用できない。語の内側に接頭付きで現れる形も
    取り除く。対応が取れない括弧と、接頭を持たず語の内側に現れる括弧はそのまま残し、
    引用の崩れの検出対象にする。
    """
    result = text
    search_from = 0
    while True:
        found = _substitution_open_index(result, search_from)
        if found is None:
            return result
        start, prefix_length = found
        depth = 0
        end = -1
        for index in range(start, len(result)):
            if result[index] == "(":
                depth += 1
            elif result[index] == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end < 0:
            search_from = start + 1
            continue
        result = result[: start - prefix_length] + result[end + 1 :]
        search_from = 0


def _check_bash_unquoted_shell_metacharacter(command: str) -> str | None:
    """語の内側にある引用されていないシェルメタ文字を検出する。

    検出対象は、単語の途中に現れる丸括弧とバッククォートに限る。
    サブシェル、プロセス置換及びコマンド置換は引用できないため、対応の取れた範囲を
    取り除いた残りに現れるものだけを対象とする。
    二重引用符とドル記号は正当な用法が多く、静的には引用の崩れと区別できないため対象にしない。
    """
    masked = _bash_command_parser.mask_heredoc_bodies(command)
    stripped = re.sub(r"'[^']*'", lambda match: "_" * len(match.group()), masked)
    stripped = re.sub(r'"[^"]*"', lambda match: "_" * len(match.group()), stripped)
    stripped = _strip_balanced_substitutions(stripped)
    for word in stripped.split():
        detected = next((character for character in word if character in _SHELL_METACHARACTERS_IN_WORD), None)
        if detected is None:
            continue
        return _llm_notice(
            f"語の内側に引用されていないシェルメタ文字がある。対象の文字: {detected}\n"
            "対処: 当該引数を`$'...'`のANSI-Cクォートで囲むか、変数へ代入してから`\"$VAR\"`で展開する。",
            tag=_WARN_TAG,
            removable_cause=True,
        )
    return None


_GIT_OBJECT_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")
_GIT_RANGE_PATTERN = re.compile(r"^([0-9a-f]{7,64})\.{2,3}([0-9a-f]{7,64})$")


def _git_object_candidates(arguments: Sequence[str]) -> list[str]:
    """Git objectのOIDとして渡された候補を取り出す。"""
    candidates: list[str] = []
    for token in arguments:
        if token.startswith("-"):
            continue
        range_match = _GIT_RANGE_PATTERN.match(token)
        if range_match is not None:
            candidates.extend(range_match.groups())
            continue
        if _GIT_OBJECT_PATTERN.match(token):
            candidates.append(token)
    return candidates


def _git_object_exists(oid: str, cwd: str) -> bool | None:
    """対象リポジトリでOIDを解決できるかを返す。判定できない場合はNoneを返す。"""
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", cwd, "cat-file", "-e", f"{oid}^{{commit}}"],  # noqa: S607
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode == 0:
        return True
    if "not a git repository" in completed.stderr.lower():
        return None
    return False


def _check_bash_unresolved_git_object(command: str, cwd: str) -> str | None:
    """対象リポジトリで解決できないGit objectのOIDを渡す呼び出しを検出する。

    branch名とtag名を誤って対象にしないため、7文字以上の16進文字列だけを候補とする。
    判定のためのGitコマンドが失敗した場合は、判定不能を検出の根拠にせず通過させる。
    """
    if not cwd:
        return None
    for segment in _extract_execution_segments(command):
        resolved = _git_subcommand_tokens(segment)
        if resolved is None:
            continue
        for oid in _git_object_candidates(without_shell_redirections(resolved[1])):
            if _git_object_exists(oid, cwd) is False:
                return _llm_notice(
                    f"対象リポジトリで解決できないGit objectのOIDを渡している。対象: {oid}\n"
                    "対処: 当該操作の直前に対象リポジトリで`git rev-parse`によりrevisionを解決し、"
                    "得た値をそのまま渡す。",
                    tag=_WARN_TAG,
                    removable_cause=True,
                )
    return None


def _check_bash_rg_multiline_pattern(command: str) -> str | None:
    """改行を含むpatternへ複数行モードを指定していない`rg`の呼び出しを検出する。"""
    for segment in _extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        if pathlib.PurePath(segment.tokens[0]).name != "rg":
            continue
        arguments = without_shell_redirections(segment.tokens[1:])
        if any(token in {"-U", "--multiline"} for token in arguments):
            continue
        if any("\\n" in token and not token.startswith("-") for token in arguments):
            return _llm_notice(
                "`rg`のpatternへ`\\n`を含めているが、複数行モードを指定していない。\n対処: `-U`又は`--multiline`を指定する。",
                tag=_WARN_TAG,
                removable_cause=True,
            )
    return None


def _atk_subcommand_catalog(prefix: tuple[str, ...]) -> list[tuple[str, str]]:
    """指定した`atk`サブコマンド経路の直下にある受理サブコマンドと要約を返す。"""
    from agent_toolkit._atk.help_text import HELP  # pylint: disable=import-outside-toplevel

    depth = len(prefix) + 1
    catalog: list[tuple[str, str]] = []
    for key, entry in HELP.items():
        parts = key.split()
        if parts[0] != "atk" or len(parts) != depth + 1:
            continue
        if tuple(parts[1:depth]) != prefix:
            continue
        summary = entry.get("summary", "") if isinstance(entry, dict) else ""
        catalog.append((parts[-1], summary))
    return sorted(catalog)


def _atk_unknown_subcommand(segment: _ExecutionSegment) -> tuple[tuple[str, ...], str] | None:
    """`atk`区間が当該階層の受理しないサブコマンドを指定する場合に、経路と対象を返す。

    照合は最上位の段と、認識できた経路の直下の段の双方へ適用する。
    下位の段の誤りを受理形式の判定へ委ねると、当該判定は下位サブコマンドを位置引数として表現するため、
    実態と異なる本文が返る。
    当該階層が下位サブコマンドを持たない場合と、受理一覧を取得できない場合はNoneを返す。
    """
    if not segment.resolved or len(segment.tokens) < 2:
        return None
    if pathlib.PurePath(segment.tokens[0]).name not in {"atk", "atk.py"}:
        return None
    path = _recognized_atk_command_path(segment.tokens)
    if path is None:
        prefix: tuple[str, ...] = ()
        candidate = segment.tokens[1]
    else:
        prefix = path
        arguments = _argument_tokens(segment, 1 + len(path))
        candidate = arguments[0] if arguments else ""
    if not candidate or candidate.startswith("-"):
        return None
    catalog = _atk_subcommand_catalog(prefix)
    if not catalog or any(name == candidate for name, _ in catalog):
        return None
    return prefix, candidate


def _check_bash_unknown_atk_subcommand(command: str) -> str | None:
    """`atk`のコマンド木に実在しないサブコマンドを指定した呼び出しを検出する。

    当該階層が受理するサブコマンドと要約を本文へ示し、受領した実行主体が同じターンの内側で
    正しい形へ書き換える材料を得られるようにする。
    """
    for segment in _extract_execution_segments(command):
        unknown = _atk_unknown_subcommand(segment)
        if unknown is None:
            continue
        prefix, candidate = unknown
        catalog = _atk_subcommand_catalog(prefix)
        listed = "\n".join(f"- {name}: {summary}" for name, summary in catalog)
        label = f"`atk {' '.join(prefix)}`" if prefix else "`atk`"
        print(
            _block_notice(
                f"{label}のコマンド木に実在しないサブコマンドを指定している。対象: {candidate}\n"
                f"{label}が受理するサブコマンド:\n{listed}",
                fix="上記のいずれかへ修正する。",
            ),
            file=sys.stderr,
        )
        return "block"
    return None


# revisionを位置引数として受け取る`git`サブコマンド。
# オプション終端の不在がrevisionとオプションの曖昧性を生むのは当該サブコマンドに限るため、
# `commit`・`push`・`config`のようにrevisionを受け取らないサブコマンドは検出対象から外す。
_GIT_REVISION_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "bisect",
        "blame",
        "branch",
        "cat-file",
        "checkout",
        "cherry-pick",
        "describe",
        "diff",
        "log",
        "merge",
        "merge-base",
        "range-diff",
        "rebase",
        "reset",
        "restore",
        "rev-list",
        "rev-parse",
        "revert",
        "shortlog",
        "show",
        "switch",
        "tag",
    }
)
_HYPHEN_PREFIXED_DATA_PATTERN = re.compile(r"^-{1,2}[^\s=]*\.[A-Za-z0-9_]+$")


def _check_bash_option_terminator_missing(command: str, cwd: str) -> str | None:
    """ハイフンで始まるデータをオプション終端なしで位置引数へ渡す呼び出しを検出する。

    オプションとデータを静的に区別できないため、パス区切り又は拡張子を持つ形だけを対象とする。
    `git`はrevisionを位置引数として受け取るサブコマンドだけを対象とする。
    値を密着させた短縮オプション（`rg -g'*.py'`など）は対象コマンドが受理する1つのトークンであり、
    オプション終端を要するデータに当たらないため対象から外す。
    """
    del cwd  # noqa: PLW0613
    for segment in _extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        name = pathlib.PurePath(segment.tokens[0]).name
        if name == "git":
            resolved = _git_subcommand_tokens(segment)
            if resolved is None or resolved[0] not in _GIT_REVISION_SUBCOMMANDS:
                continue
            arguments = list(without_shell_redirections(resolved[1]))
        elif name in _PATH_OPERAND_COMMANDS:
            arguments = list(without_shell_redirections(segment.tokens[1:]))
        else:
            continue
        if "--" in arguments:
            continue
        for token in arguments:
            if not token.startswith("-") or token == "-":
                continue
            if _attached_short_value_option(token, _VALUE_OPTIONS) is not None:
                continue
            if "/" in token or _HYPHEN_PREFIXED_DATA_PATTERN.match(token):
                return _llm_notice(
                    f"ハイフンで始まるデータをオプション終端なしで渡している。対象: {token}\n"
                    "対処: 当該コマンドが提供するオプション終端`--`を、データの直前へ置く。",
                    tag=_WARN_TAG,
                    removable_cause=True,
                )
    return None


_DIRECTORY_CREATION_COMMAND = "mkdir"


def _check_bash_redirect_parent_exists(command: str, cwd: str) -> str | None:
    """出力リダイレクト先の親ディレクトリが存在しない呼び出しを検出する。

    実行区間を先頭から順に走査し、`_scan_explicit_paths`と同じ基準で判定入力を決める。
    リダイレクト先は引用解決済みのトークン列から取り出すため、検索patternなどの引数の内側にある
    リダイレクト記号を出力先の指定として数えない。
    相対パスの解決基準は、同じコマンド文字列の内側にある`cd`の遷移先を反映した実効の作業ディレクトリとする。
    変数展開とコマンド置換を含む出力先は実行前に一意へ解決できないため対象外とする。
    先行する区間がディレクトリを作成する場合は、当該区間より後ろを対象から外す。
    作成するディレクトリの集合をコマンド文字列から一意に確定できないためである。
    """
    if not cwd:
        return None
    current = CwdResolution(cwd, True)
    for segment in _extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        cwd_change = resolve_cwd_change(list(segment.tokens), current)
        if cwd_change is not None:
            current = cwd_change
            continue
        if pathlib.PurePath(segment.tokens[0]).name == _DIRECTORY_CREATION_COMMAND:
            return None
        base = current.path if current.resolved and current.path else cwd
        for target in shell_redirection_targets(segment.tokens):
            if not target or any(character in target for character in "*$?[]{}~`"):
                continue
            if target.startswith(("/dev/", "/proc/")):
                continue
            path = pathlib.Path(target)
            resolved = path if path.is_absolute() else pathlib.Path(base) / path
            parent = resolved.parent
            if not parent.is_dir():
                return _llm_notice(
                    f"出力リダイレクト先の親ディレクトリが存在しない。解決した出力先: {resolved}\n"
                    f"不在の親ディレクトリ: {parent}\n"
                    "対処: 実在するディレクトリ配下の出力先を指定するか、先行して当該ディレクトリを作成する。",
                    tag=_WARN_TAG,
                    removable_cause=True,
                )
    return None


_COMMAND_OPTION_CONTRACT_KEY = "external_command_option_contracts"


def _external_command_targets(command: str) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """受理オプションの照合対象となるコマンドの経路と実引数を返す。

    対象は`rg`に限る。`git <サブコマンド> -h`は長い形のオプションを網羅せず
    （`git commit -h`は`--amend`を示す一方で`--edit`と`--no-edit`を示さないことを実測した）、
    当該出力を受理集合として照合すると正当な呼び出しを誤検出するためである。
    """
    targets: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for segment in _extract_execution_segments(command):
        if not segment.resolved or not segment.tokens or _segment_is_help_only(segment):
            continue
        if pathlib.PurePath(segment.tokens[0]).name != "rg":
            continue
        candidate = (("rg",), tuple(without_shell_redirections(segment.tokens[1:])))
        if any(token.startswith("-") and token != "-" for token in candidate[1]):
            targets.append(candidate)
    return targets


_HELP_VALUE_PLACEHOLDER_PATTERN = re.compile(r"^[ =]?(?:[<[]|[A-Z][A-Z0-9_]+(?![\w-]))")
"""ヘルプ出力でオプション名の直後に現れる値placeholderの表記。

山括弧と角括弧で囲む表記のほかに、`-A NUM, --after-context=NUM`のように大文字だけの語で
値を示す表記がある。後者を値なしへ分類すると、値を密着させた短縮オプションを受理しないと判定する。
説明文の先頭語を値placeholderと誤認しないため、大文字だけで2文字以上の語に限る。
"""


def _parse_help_options(help_text: str) -> tuple[list[str], list[str]] | None:
    """ヘルプ出力から、値を取らないオプションと値を取るオプションを取り出す。"""
    flags: list[str] = []
    valued: list[str] = []
    for line in help_text.splitlines():
        for option_match in re.finditer(r"(?<![\w-])(--?[A-Za-z][\w-]*)(=?)", line):
            option = option_match.group(1)
            following = line[option_match.end() :]
            takes_value = bool(option_match.group(2)) or _HELP_VALUE_PLACEHOLDER_PATTERN.match(following) is not None
            target = valued if takes_value else flags
            if option not in target:
                target.append(option)
    flags = [option for option in flags if option not in valued]
    if not flags and not valued:
        return None
    return flags, valued


def _external_command_option_contract(path: tuple[str, ...], session_id: str) -> tuple[list[str], list[str]] | None:
    """外部コマンドの受理オプションを、セッションごとに1回だけヘルプから取得して保持する。

    `rg`は受理形式を機械可読な定義として公開しないため、当該コマンドのヘルプを解析する。
    取得できない場合はNoneを返し、照合そのものを行わない。
    """
    key = " ".join(path)
    state = read_state(session_id)
    recorded = state.get(_COMMAND_OPTION_CONTRACT_KEY)
    if isinstance(recorded, dict):
        cached = recorded.get(key)
        if isinstance(cached, dict):
            flags = [value for value in cached.get("flags", []) if isinstance(value, str)]
            valued = [value for value in cached.get("valued", []) if isinstance(value, str)]
            return (flags, valued) if flags or valued else None
    argv = [*path, "--help"] if path[0] == "rg" else [*path, "-h"]
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parsed = _parse_help_options(completed.stdout or completed.stderr)
    if parsed is None:
        return None

    def _record(current_state: dict) -> dict | None:
        current = current_state.get(_COMMAND_OPTION_CONTRACT_KEY)
        contracts = dict(current) if isinstance(current, dict) else {}
        if key in contracts:
            return None
        contracts[key] = {"flags": parsed[0], "valued": parsed[1]}
        current_state[_COMMAND_OPTION_CONTRACT_KEY] = contracts
        return current_state

    update_state(session_id, _record)
    return parsed


_GREP_VALUED_SHORT_OPTIONS = frozenset({"-e", "-f", "-m", "-A", "-B", "-C"})
_GREP_SHORT_FLAGS = frozenset({"-n", "-i", "-r", "-R", "-v", "-w", "-l", "-q", "-c", "-s", "-o", "-h", "-H"})


def _ambiguous_grep_short_option(command: str) -> tuple[str, str] | None:
    """値付き短縮オプションへ既知のフラグを連結した検索コマンドを返す。"""
    for segment in _extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        name = pathlib.PurePath(segment.tokens[0]).name
        if name == "grep":
            arguments = _argument_tokens(segment)
            flags = _GREP_SHORT_FLAGS
        elif name == "git":
            subcommand = _git_subcommand_tokens(segment)
            if subcommand is None or subcommand[0] != "grep":
                continue
            name = "git grep"
            arguments = subcommand[1]
            flags = _GIT_GREP_FLAGS
        else:
            continue
        scan = _scan_accepted_options(arguments, flags, _GREP_VALUED_SHORT_OPTIONS)
        if scan.ambiguous_option is not None:
            return name, scan.ambiguous_option
    return None


def _check_bash_external_command_options(command: str, session_id: str) -> str | None:
    """検索コマンドの曖昧な連結と`rg`の未受理オプションを検出する。

    受理形式は当該コマンドのヘルプから1セッション1回だけ取得して保持する。
    走査は`atk`向けの判定と同じ`_scan_accepted_options`を用い、オプション終端と値引数の位置を反映する。
    記憶と別のコマンドの同名オプションからの類推による誤りを、対象に近い受理オプションとともに差し戻す。
    未観測の対象へ受理形式そのものを毎回配送する形は採らない。
    当該配送は対象が増えるたびに実行主体のコンテキストを消費する一方、
    誤りが無い呼び出しでは判断を変えないためである。
    """
    ambiguous = _ambiguous_grep_short_option(command)
    if ambiguous is not None:
        name, token = ambiguous
        return _llm_notice(
            f"`{name}`の値付き短縮オプションへ別の短縮フラグを連結した曖昧な形である。"
            f"`{token[:2]}`は後続の`{token[2:]}`を値として消費する。"
            f"対象: {token}\n対処: 値付きオプションと各フラグを別の引数へ分ける。",
            tag=_WARN_TAG,
            removable_cause=True,
        )
    if not session_id:
        return None
    for path, arguments in _external_command_targets(command):
        contract = _external_command_option_contract(path, session_id)
        if contract is None:
            continue
        flags, valued = contract
        # `--no-`接頭辞の否定形は、対応する肯定形を受理するコマンドが一般に受理する。
        scan = _scan_accepted_options(arguments, flags, valued, accepts_long_negation=True)
        if scan.ambiguous_option is not None:
            return _llm_notice(
                f"`{' '.join(path)}`の値付き短縮オプションへ別の短縮フラグを連結した曖昧な形である。"
                f"対象: {scan.ambiguous_option}\n対処: 値付きオプションと各フラグを別の引数へ分ける。",
                tag=_WARN_TAG,
                removable_cause=True,
            )
        if scan.unknown_option is None:
            continue
        return _llm_notice(
            f"`{' '.join(path)}`が受理しないオプションである。対象: {scan.unknown_option}\n"
            f"{_format_accepted_option_candidates(scan.unknown_option, flags, valued)}",
            tag=_WARN_TAG,
            removable_cause=True,
        )
    return None
