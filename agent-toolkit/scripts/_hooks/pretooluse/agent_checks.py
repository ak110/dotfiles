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

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _common.file_lock import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    locked_rotate_and_append as _locked_rotate_and_append,
)
from _git import status as _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan import structure as _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan.locations import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_plan_adjunct_file,
    is_plan_component_file,
)
from pyfltr.colloquial import check as _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position
from _hooks import (
    bash_command_parser as _bash_command_parser,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from _hooks import (
    response_language_check as _response_language_check,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from _hooks import scratchpad_path as _scratchpad_path  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _hooks import tool_input as _hook_tool_input  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _hooks import transcript as _transcript  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _hooks.bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
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
from _hooks.notice import block_formatter as _block_notice_formatter  # noqa: E402

# pylint: disable-next=wrong-import-position,import-error
from _hooks.notice import _WARN_TAG  # noqa: E402
from _hooks.notice import formatter as _notice_formatter  # noqa: E402
from _hooks.session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _hooks.pretooluse.notices import _block_notice, _llm_notice


def _handle_language_check(payload: dict, session_id: str) -> tuple[int | None, str | None]:
    """直前メインエージェント応答の言語検査を実行し、セッション状態でエスカレーションを管理する。

    agents_serverが起動した委譲先セッションでは、作業途中の文章を呼び出し元もユーザーも読まないため検査しない。

    Returns:
        (exit code, 警告本文)のタプル。
        exit code 2: ブロック（stderrに出力済み）。
        exit code None + 本文あり: 警告（呼び出し側でadditionalContextに追記）。
        exit code None + 本文None: 対象外。

    セッション状態キー:
    - english_warning_count: 連続英語ターンのカウンタ（int）
    - english_warning_msg_id: 前回検出時のmessage ID（str）

    エスカレーションロジック:
    - WARN: message IDが前回と異なればカウンタ+1、同一なら据え置き。カウンタ≧2でブロック
    - PASS・SKIP: カウンタを0にリセットする。ブロック本文が「2ターン連続」と宣言するため、
      英語主体でないと判定した回を経た後は、1回の検出だけではブロックしない
    - ブロック後はカウンタを1に設定する（日本語に切り替わるまで毎ターンブロックを継続）
    """
    transcript_path = payload.get("transcript_path", "")
    if not isinstance(transcript_path, str) or not transcript_path:
        return (None, None)
    if payload.get("isSidechain") is True:
        return (None, None)
    if os.environ.get("AGENT_TOOLKIT_DELEGATED_SESSION") == "1":
        return (None, None)

    outcome, body, msg_id = _response_language_check.detailed_check(transcript_path)

    if outcome in (
        _response_language_check.CheckOutcome.PASS,
        _response_language_check.CheckOutcome.SKIP,
    ):
        if session_id:

            def _reset_count(current: dict) -> dict | None:
                if current.get("english_warning_count", 0) == 0:
                    return None
                current["english_warning_count"] = 0
                return current

            update_state(session_id, _reset_count)
        return (None, None)

    # WARN
    if not session_id:
        return (None, body)

    # update_stateがOSErrorで失敗した場合、_incrementは実行されずcountは初期値0のまま残る。
    # この場合はブロックしない方向（安全側）にフォールバックする。
    count = 0
    duplicate = False

    def _increment(current: dict) -> dict | None:
        nonlocal count, duplicate
        prev_id = current.get("english_warning_msg_id", "")
        prev_count = current.get("english_warning_count", 0)
        if msg_id and prev_id == msg_id:
            count = prev_count
            duplicate = True
            return None
        count = prev_count + 1
        current["english_warning_count"] = count
        current["english_warning_msg_id"] = msg_id
        return current

    update_state(session_id, _increment)

    if duplicate:
        return (None, None)

    if count >= 2:

        def _set_threshold(current: dict) -> dict | None:
            current["english_warning_count"] = 1
            return current

        update_state(session_id, _set_threshold)
        print(_llm_notice(_response_language_check.BLOCK_BODY, tag=_WARN_TAG), file=sys.stderr)
        return (2, None)

    return (None, body)


# Claude CodeとCodexが生成するagents_serverの完全修飾MCP tool名。
_AGENTS_SERVER_NAMESPACES = (
    "mcp__plugin_agent-toolkit_agents_server__",
    "mcp__agents_server__",
)
_AGENTS_SERVER_START_TOOLS = frozenset(
    f"{namespace}{tool}" for namespace in _AGENTS_SERVER_NAMESPACES for tool in ("start", "start_explore", "start_shell")
)
_AGENTS_SERVER_WAIT_TOOLS = frozenset(f"{namespace}wait" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_SEND_TOOLS = frozenset(f"{namespace}send_message" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_KILL_TOOLS = frozenset(f"{namespace}kill" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_TOOL_NAMES = (
    _AGENTS_SERVER_START_TOOLS | _AGENTS_SERVER_WAIT_TOOLS | _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS
)
_AGENTS_SERVER_SESSION_CWD_KEY = "agents_server_cwd_by_session"


# --- 計画単位の状態管理 ---

# Skillツールの`skill`引数として許容するplan-modeスキル名。
# posttooluse.pyの`_PLAN_MODE_SKILL_NAMES`と対応させる。
_PLAN_MODE_SKILL_NAMES: frozenset[str] = frozenset({"agent-toolkit:plan-mode", "plan-mode"})
_WEBFETCH_VERBATIM_RE = re.compile(
    r"(?:全文|原文|そのまま|逐語|引用|verbatim|word[ -]for[ -]word)",
    re.IGNORECASE,
)


def _check_webfetch_verbatim_request(tool_input: dict) -> str | None:
    """WebFetchへ逐語再現を要求する入力を検出して警告する。"""
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str) or _WEBFETCH_VERBATIM_RE.search(prompt) is None:
        return None
    return _llm_notice(
        "WebFetchは要約モデルを経由するため、その出力は逐語引用の根拠にならない。"
        "逐語で引用する場合は、同じURLの生データをagent-toolkitの管理対象一時領域へ保存し、"
        "保存した本文から該当箇所だけを引用する。",
        tag=_WARN_TAG,
    )


def _check_sendmessage_agent_type_recipient(tool_input: dict) -> str | None:
    """SendMessageの宛先にエージェント種別名を指定した場合に警告する。

    実行環境が渡す呼び出し元識別子は`uds:/tmp/cc-socks/1939480.sock`のように
    経路の区切りを持つ一方、`plugin-dev:skill-reviewer`のようなエージェント種別名は
    経路の区切りを持たない。警告本文が前者への送信を解消手段として指示するため、
    経路の区切りを含む宛先は警告しない。
    """
    recipient = tool_input.get("to")
    if not isinstance(recipient, str) or ":" not in recipient or "/" in recipient:
        return None
    return _llm_notice(
        "エージェント種別名はSendMessageの到達可能な宛先ではない。"
        "通常の完了報告はツール結果として1回返し、即時通知は実行環境が渡した呼び出し元識別子へだけ送る。",
        tag=_WARN_TAG,
    )


# --- TaskStop: 初回遮断と再実行窓 ---

_TASK_STOP_RETRY_WINDOW_SECONDS = 300


def _task_stop_target_ids(tool_input: dict) -> set[str]:
    """`TaskStop`の入力から停止対象の識別子を取り出す。

    `task_id`と非推奨の`shell_id`の双方を対象とする。
    """
    ids: set[str] = set()
    for key in ("task_id", "shell_id"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            ids.add(value)
    return ids


def _check_task_stop(session_id: str, tool_input: dict) -> bool:
    """`TaskStop`呼び出しを初回遮断し、再実行窓内なら通過させる。

    停止対象が状態キー`background_task_ids`へ記録済みの場合は遮断しない。
    当該キーは、PostToolUse(Bash)が`run_in_background`指定の応答から取得したタスクIDを
    記録したものであり、自セッションが起動して停止用の識別子を保持している対象を表す。
    起動主体の確認を要する遮断の対象は、自セッションの起動記録が無い停止に限る。

    それ以外は状態キー`task_stop_blocked_at`（`float`。セッション単位で1つだけ持つ、
    直近の遮断時刻のPOSIX秒）で判定する。値が存在し現在時刻との差が
    `_TASK_STOP_RETRY_WINDOW_SECONDS`以下なら通過（偽を返す）し、それ以外は値を
    現在時刻へ更新して遮断（真を返す）する。

    ここで保存する時刻は再実行許可窓の判定にのみ用いる値であり、状態ファイル自体の
    回収期限（`_session_state.STALE_STATE_MAX_AGE_SECONDS`によるmtime基準の14日）とは
    別の寿命を持つ。
    """
    now = time.time()
    state = read_state(session_id)
    recorded = state.get("background_task_ids")
    recorded_ids = {value for value in recorded if isinstance(value, str)} if isinstance(recorded, list) else set()
    if _task_stop_target_ids(tool_input) & recorded_ids:
        return False
    blocked_at = state.get("task_stop_blocked_at")
    if isinstance(blocked_at, (int, float)) and now - blocked_at <= _TASK_STOP_RETRY_WINDOW_SECONDS:
        return False

    def _mark_blocked(current: dict) -> dict | None:
        current["task_stop_blocked_at"] = now
        return current

    update_state(session_id, _mark_blocked)
    print(
        _block_notice(
            "blocked: TaskStop。背景タスクの停止は、ユーザーの明示的な即時停止要求があるか、"
            "停滞検知の手順を完了した場合に限る。進行が遅いことや非効率に確認できることだけでは停止の指示にならない。"
            "意図の解釈が複数残る場合は、停止の前にAskUserQuestionで確認する。"
            "ユーザーの介入があった場合は、既定では稼働中の委譲先へ追加指示を送る。"
            "停止するのは、当該介入が委譲範囲または前提を無効にし、継続すると誤った成果物が確定する場合に限る。"
            "詳細は`agent-toolkit:delegation`「継続と新規起動」が定める。",
            fix="停止の根拠を確認済みであれば、5分以内にTaskStopを再実行すると続行できる。",
        ),
        file=sys.stderr,
    )
    return True


def _reset_plan_mode_state(session_id: str) -> None:
    """plan-mode起動時に計画単位の状態をリセットする。"""
    if not session_id:
        return

    def _reset(current: dict) -> dict | None:
        changed = False
        if current.pop("current_plan_file_path", None) is not None:
            changed = True
        # 直接編集連続checkの状態も新計画へ持ち越さない。
        if current.get("plan_file_written", False):
            current["plan_file_written"] = False
            changed = True
        if current.get("direct_agent_toolkit_edit_count", 0) != 0:
            current["direct_agent_toolkit_edit_count"] = 0
            changed = True
        if current.get("last_agent_toolkit_edit_path") is not None:
            current["last_agent_toolkit_edit_path"] = None
            changed = True
        return current if changed else None

    update_state(session_id, _reset)


# --- Codex App Server: isSidechainプローブ ---


def _record_iss_sidechain_probe(
    session_id: str,
    tool_name: str,
    payload: dict,
) -> None:
    """多重ネスト構成でのisSidechain実値採取用のデバッグログ記録。

    暫定機構: fb7 (20260719-074241-001.md) の実サンプル採取が目的。
    十分なサンプルが集まり代替判定機構が実装された時点で本ヘルパーは削除する。
    ログ出力先はtempfile.gettempdir()起点でsession_id単位に分離する
    （_stop_gate.pyの_stop_log_path先例に揃える）。
    ローテーションと追記は`_file_lock.locked_rotate_and_append`へ委譲する。
    """
    try:
        log_dir = pathlib.Path(tempfile.gettempdir())
        safe_session_id = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
        log_path = log_dir / f"claude-agent-toolkit-issidechain-{safe_session_id}.log"
        state = read_state(session_id) if session_id else {}
        entry = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "session_id": session_id,
            "tool_name": tool_name,
            "isSidechain": payload.get("isSidechain"),
            "transcript_path": payload.get("transcript_path"),
            "cwd": payload.get("cwd"),
            "current_plan_file_path": state.get("current_plan_file_path") if isinstance(state, dict) else None,
        }
        _locked_rotate_and_append(log_path, json.dumps(entry, ensure_ascii=False) + "\n", 1_000_000)
    except OSError:
        pass


# --- agents_server: 開始点の絶対cwd検査 ---


def _check_agents_server_cwd(tool_input: dict) -> bool:
    """`start.cwd`が非空の絶対パスでない呼び出しを検出する。"""
    cwd = tool_input.get("cwd")
    if isinstance(cwd, str) and cwd.strip() != "" and pathlib.PurePath(cwd).is_absolute():
        return False
    specified = tool_input.get("cwd")
    actual = f"`{specified}`" if isinstance(specified, str) and specified != "" else "unspecified"
    print(
        _block_notice(
            f"blocked: agents_serverのstartには空でない絶対パスの`cwd`が必要である（実際: {actual}）。"
            "指定が無い場合、Codexは要求されたworktreeではなくApp Serverプロセスから作業ディレクトリを解決する。",
            fix="`cwd`へ対象作業ディレクトリの絶対パスを設定して再実行する。",
        ),
        file=sys.stderr,
    )
    return True


def _check_agents_server_continuation_input(session_id: str, tool_input: dict, tool_name: str) -> bool:
    """`send_message`・`kill`の入力と保存済みcwdを検査する。"""
    display_name = tool_name.rsplit("__", 1)[-1]
    if tool_name in _AGENTS_SERVER_SEND_TOOLS:
        prompt = tool_input.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            print(
                _block_notice(
                    f"blocked: {display_name}には空でない`prompt`が必要である。",
                    fix="空でない`prompt`を指定して再実行する。",
                ),
                file=sys.stderr,
            )
            return True
    remote_session_id = tool_input.get("session_id")
    if not isinstance(remote_session_id, str) or not remote_session_id:
        print(
            _block_notice(
                f"blocked: {display_name}には空でない`session_id`が必要である。",
                fix="codex_startが返した`session_id`を使うか、codex_startで新しいセッションを開始する。",
            ),
            file=sys.stderr,
        )
        return True
    state = read_state(session_id)
    cwd_map = state.get(_AGENTS_SERVER_SESSION_CWD_KEY)
    if not isinstance(cwd_map, dict) or not isinstance(cwd_map.get(remote_session_id), str):
        print(
            _block_notice(
                f"blocked: {display_name}は、`session_id`に対応する絶対`cwd`が保存されていないため続行できない。",
                fix="当該セッションを続行せず、絶対`cwd`を指定したagents_serverのstartで新しいセッションを開始する。",
            ),
            file=sys.stderr,
        )
        return True
    return False
