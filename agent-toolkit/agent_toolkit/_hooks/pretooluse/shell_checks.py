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

- メインエージェント応答の日本語文字比率が閾値未満の場合の警告/ブロック (warn/block)
- ユーザーが直接読む質問本文・計画本文の文字化け、他言語文字、口語表現の検査 (warn/block)
- plan-modeスキル未起動のままのplan file編集（Write/Edit/MultiEdit）の警告 (warn)
- plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続のブロック (warn/block)

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

- 長い固定`sleep`の後に別コマンドを連結する前景待機の検出 (warn/block)
- 高容量のユーザー領域を無限定に再帰検索する実行位置の検出 (warn)
- 高容量のユーザー領域を対象限定なしに走査する`find`・`ls -R`の検出 (warn)
- 検証コマンド又は保存本文を返すコマンドの出力を`tail`・`head`で切り詰める指定の検出 (warn/block)
- 切り詰め直後の`$?`が検証コマンドの終了状態を隠す指定の検出 (warn)
- パターン一致によるプロセス終了（`pkill`・`killall`等）の遮断 (block)
- git amend / rebase直前に`git log`未確認のブロック (block)
- git push実行時のamend後dirty状態のブロック (block)
- 非Pythonプロジェクトでの`uv run python <path>`形式起動のブロック (block)
- `git commit`未検証警告 (warn)
- `agent-toolkit/`配下のコミット時のversion bump漏れ警告 (warn)
- `git log --decorate`の自動付与 (auto-fix)
- `codex exec`の未決事項念押し (warn)
- 一括ステージ実行時の自セッション編集対象外ファイル警告 (warn)

Skill:

- `agent-toolkit:plan-mode`起動時の計画単位の状態リセット (side-effect)

TaskStop:

- 初回呼び出しのブロックと、直近ブロックから一定時間内の再実行の通過 (block)

Write / Edit / MultiEdit / apply_patch:

- 文字化け（U+FFFD）検出 (block)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (block)
- lockfile / 生成物ディレクトリの直接編集 (block)
- シークレット / 鍵ファイルの直接編集 (block)
- manifestファイルの手編集 (warn)
- ホームディレクトリの絶対パス混入 (warn)
- 口語的な日本語表現の混入 (warn)
- 「Xを根拠にYしない」「Xを理由にYしない」形式のメタ規範文言の増加 (warn)
- .md規範文書のWrite/Edit/MultiEditでfrontmatter同期注記の本体該当語句の実在検証warn (warn)
- 日本語を含む書き込み文字列へのハングル・キリル文字の混入 (block)
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
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from pyfltr.colloquial import check as _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position

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
    extract_git_events,
    resolve_cwd_change,
    resolve_execution_segment,
    split_bash_segments,
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import _WARN_TAG  # noqa: E402

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter  # noqa: E402
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    read_state,
    update_state,
)
from agent_toolkit._plan import structure as _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._plan.locations import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_plan_adjunct_file,
    is_plan_component_file,
)

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

_ENV_ASSIGN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_PYPROJECT_PROJECT_SECTION_PATTERN = re.compile(r"(?m)^\[project(?:\.[\w\-]+)?\]\s*$")


def _check_bash_uv_run_python(command: str, cwd: str) -> bool:
    """`uv run python <path>`形式の起動を非Pythonプロジェクトでブロックする。

    判定詳細は本関数の冒頭コメントを参照する。真を返すとblock（exit 2）。
    """
    segments = split_bash_segments(command)
    current_cwd = CwdResolution(cwd, bool(cwd))
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return False
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
                print(_block_notice(_UV_RUN_PYTHON_BLOCK_MSG, fix=_UV_RUN_PYTHON_FIX), file=sys.stderr)
                return True
    return False


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
        "完了通知を受領するか、背景ジョブの機械可読な完了標識を使うか、`atk watch`で委譲作業を観測し、\n"
        "待機状態を示してターンを終了する。"
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
    if not raw_tokens or _has_active_process_kill_syntax(segment):
        return True
    command_name = pathlib.PurePosixPath(raw_tokens[0]).name
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
    matching_segments = [segment for segment in split_bash_segments(command) if _PROCESS_KILL_BY_PATTERN_RE.search(segment)]
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
    ("atk", "wi", "convert-to-plan"),
    ("atk", "wi", "set-dependencies"),
    ("atk", "wi", "answer"),
    ("atk", "wi", "commit"),
    ("atk", "wi", "migrate"),
    ("atk", "plans", "commit"),
    ("atk", "plans", "checkout"),
    ("atk", "plans", "migrate"),
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
    arguments = segment.tokens[1:]
    return (
        segment.resolved
        and "--" not in arguments
        and "--help" in arguments
        and all(token == "--help" for token in arguments if token.startswith("-"))
    )


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


def _grep_file_operands(segment: _ExecutionSegment) -> tuple[tuple[str, ...], frozenset[str]] | None:
    """再帰`grep`区間のファイルoperandと認識済みオプションを返す。"""
    operands: list[str] = []
    options: set[str] = set()
    tokens = segment.tokens[1:]
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
            name, separator, _ = token.partition("=")
            if name in _GREP_LONG_OPTIONS_WITH_VALUE:
                options.add(name)
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
            index += 1 if len(short) > 1 else 2
            continue
        if not short or any(character not in _GREP_SHORT_OPTIONS_WITHOUT_VALUE for character in short):
            return None
        options.update(f"-{character}" for character in short)
        index += 1
    pattern_is_option = bool(options & {"-e", "-f", "--regexp", "--file"})
    return (tuple(operands if pattern_is_option else operands[1:]), frozenset(options))


def _check_bash_recursive_grep_without_exclusion(command: str, cwd: str) -> str | None:
    """除外指定の無い再帰`grep`がディレクトリを読む場合に警告する。"""
    base = pathlib.Path(cwd) if cwd else pathlib.Path.cwd()
    for pipeline in _extract_execution_pipelines(command):
        for segment in pipeline:
            if not segment.resolved or segment.tokens[0] not in _GREP_COMMANDS:
                continue
            parsed = _grep_file_operands(segment)
            if parsed is None:
                continue
            files, options = parsed
            if not options & {"-r", "-R", "--recursive"}:
                continue
            if options & {"--include", "--exclude", "--exclude-dir"}:
                continue
            if not files or any(token != "-" and (token.endswith("/") or (base / token).is_dir()) for token in files):
                return _llm_notice(
                    "warn: 除外設定を反映しない再帰`grep`をディレクトリへ実行している。"
                    "`.gitignore`とツール固有の除外を反映する`rg`か、Git管理対象へ限定する`git grep`を使う。"
                    "`grep`を使う場合は`--include`・`--exclude`・`--exclude-dir`で対象を限定する。",
                    tag=_WARN_TAG,
                    removable_cause=True,
                )
    return None


def _check_bash_state_change_command_chaining(command: str) -> str | None:
    """状態変更コマンドが最後の直列区間でない場合に遮断する。

    最後の区間では当該コマンドの終了コードがシェルの終了コードとなるため対象外とする。
    代替手段が当該コマンドの単独実行に一意に定まり、同じターンで実行できるため遮断する。
    """
    serial_commands = _split_serial_shell_commands(command, separators=_STATUS_SHELL_SEPARATORS)
    for serial_command in serial_commands[:-1]:
        if any(_segment_is_state_changing(segment) for segment in _extract_execution_segments(serial_command)):
            print(
                _block_notice(
                    "block: 状態を変更するコマンドを他のコマンドと同じシェル呼び出しへ連結している。",
                    fix="当該コマンドを単独で実行し、終了コードと出力を直接観測する。",
                ),
                file=sys.stderr,
            )
            return "block"
    return None


def _check_bash_help_with_execution(command: str) -> str | None:
    """同じ実行ファイルのヘルプ取得と、同じ実行ファイルのヘルプ取得以外の区間との並置を遮断する。

    `-h`は実行ファイルごとに意味が異なるためヘルプ指定として扱わない。
    ヘルプ取得だけを並べた呼び出しは、警告が求める実行の分離を適用する区間を持たないため対象にしない。
    代替手段がヘルプの先行実行と後続実行の分離に一意に定まり、同じターンで実行できるため遮断する。
    """
    segments = [segment for segment in _extract_execution_segments(command) if segment.resolved and segment.tokens]
    names = [pathlib.PurePosixPath(segment.tokens[0]).name for segment in segments]
    help_names = {name for name, segment in zip(names, segments, strict=True) if _segment_is_help_only(segment)}
    non_help_names = {name for name, segment in zip(names, segments, strict=True) if not _segment_is_help_only(segment)}
    if help_names & non_help_names:
        print(
            _block_notice(
                "block: 同じシェル呼び出しの中でヘルプの取得と同じ実行ファイルの実行が並んでいる。",
                fix="先にヘルプだけを実行して受理形式を確定し、実行は別の呼び出しへ分ける。",
            ),
            file=sys.stderr,
        )
        return "block"
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
    tokens = segment.tokens[1:]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        redirect_match = _SHELL_REDIRECTION_PATTERN.match(token)
        if redirect_match is not None:
            index += 1
            if redirect_match.end() == len(token):
                index += 1
            continue
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
    tokens = iter(segment.tokens[1:])
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
    for prefix, options_with_value, options_without_value in runners:
        if not _segment_starts_with(segment, prefix):
            continue
        if prefix[0] == "mise":
            return _mise_task_targets(segment.tokens, len(prefix))
        target_index = _option_operand_index(segment.tokens, len(prefix), options_with_value, options_without_value)
        return () if target_index is None else (segment.tokens[target_index],)
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


def _pipeline_truncates_required_output(pipeline: Sequence[_ExecutionSegment]) -> bool:
    """1つのパイプライン内で、必要な出力が全量保存されないまま切り詰められるかを判定する。

    全量観測が必要なコマンドより後方で最初に現れる切り詰めコマンドを発生点とし、
    その手前に`tee`が無い場合に真を返す。`tee`で全量を先に保存してから抽出する形は対象外とし、
    切り詰めた後に`tee`で保存する形は保存内容が既に切り詰め後であるため対象とする。
    同一パイプラインに対象コマンドが複数ある場合は、いずれか1件でも該当すれば真を返す。
    状態変更コマンドでは`head`・`tail`に加えて`grep`系も切り詰めとして扱う。
    """
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
        return True
    return False


def _check_bash_output_truncation(command: str, session_id: str) -> str | None:
    """全量観測が必要な出力を`tail`・`head`で切り詰める指定を初回から遮断する。

    全量をファイルへ保存してから必要部分を抽出する形、構造化出力をレコード種別で抽出する形、
    または分離したコンテキストで実行する形を解消手段として示す。
    全パイプラインの検証コマンドと保存本文を返すコマンドを対象とし、1件でも切り詰めに該当すれば1回だけ通知する。
    `;`・`&&`・`||`・`&`で連結した後続コマンドは対象コマンドの出力を受け取らないため対象外とする。
    判定は`_extract_execution_pipelines`が返す実行位置で行うため、対象コマンド名を検索語・引数として
    含むだけの場合は検出しない。実行位置を確定できない区間と、実行位置以外で起動される対象コマンドも
    検出しない（助言であり非検出側の誤差の実害が小さいため）。
    """
    if not any(_pipeline_truncates_required_output(pipeline) for pipeline in _extract_execution_pipelines(command)):
        return None
    del session_id
    print(
        _block_notice(
            "block: 全量観測が必要なコマンドの実行出力を`tail`・`head`・`grep`などで限定している。",
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
        redirect_match = _SHELL_REDIRECTION_PATTERN.match(token)
        if redirect_match is not None:
            index += 1
            if redirect_match.end() == len(token):
                index += 1
            continue
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
        segment.resolved and _pipeline_has_recursive_home_search(segment.tokens)
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


def _find_has_unbounded_home_traversal(tokens: Sequence[str]) -> bool:
    """`find`区間が高容量領域だけを対象とし、走査範囲を限定しない場合に真を返す。"""
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
    if not paths or not all(_is_high_capacity_home_target(path) for path in paths):
        return False
    expression = tokens[index:]
    if any(token in _FIND_BOUNDS for token in expression):
        return False
    return all(
        not token.startswith("-")
        or token in _FIND_KNOWN_EXPRESSION_OPTIONS
        or token.startswith("-newer")
        and len(token) == len("-newer") + 2
        for token in expression
    )


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
        segment.resolved and _pipeline_has_unbounded_home_traversal(segment.tokens)
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
