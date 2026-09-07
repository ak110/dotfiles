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
    from _hooks.pretooluse.content_checks import _MANAGED_TEMP_MARKER
    from _hooks.pretooluse.notices import _block_notice, _llm_notice
    from _hooks.pretooluse.shell_checks import _PROCESS_KILL_UNSAFE_MARKERS


def _is_in_verified_managed_temp(file_path: str) -> bool:
    """真正性を検証できた管理対象一時領域の配下であれば真を返す。"""
    try:
        managed_temp = importlib.import_module("_atk.managed_temp")
    except ImportError:
        return False
    try:
        current = pathlib.Path(file_path).expanduser().resolve(strict=False)
        current = current if current.is_dir() else current.parent
        for directory in (current, *current.parents):
            if not (directory / _MANAGED_TEMP_MARKER).is_file():
                continue
            return managed_temp.validate_managed_temp(directory) == directory
        return False
    except (OSError, ValueError, managed_temp.ManagedTempError):
        return False


def _contains_heredoc(command: str) -> bool:
    """コマンド本文がヒアドキュメント（`<<`）を含むかを返す。

    ヒアドキュメント本文は実行されないリテラルだが、区間分割と実行位置解析は本文を
    実行コマンド列として扱う。本文中のリテラル一致による誤検出を避けるため、
    誤検出側の実害が誤検出しない側を上回る検査は当該コマンドを対象から外す。
    """
    return "<<" in command


# --- Bash: git amend / rebaseをlog未確認でブロック ---


def _check_bash_amend_rebase_without_log(command: str, session_id: str, cwd: str) -> bool:
    """Git commit --amend / git rebaseをgit log未確認で実行しようとした場合にブロックする。

    amend / rebaseは既存コミットを書き換えるため、直前にgit log --decorateで
    コミット状態（特にプッシュ済みかどうか）を確認する必要がある。
    リセット条件は対象コミットの親子関係が変化する操作（commit・rebase・reset）に限定する
    （`posttooluse.py` `_GIT_LOG_RESET_SUBCOMMANDS`が単一のリセット判定箇所）。
    ファイル編集・push・Stopの介在ではリセットしない
    （push・Stopはコミット木を書き換えないため再確認を強制する必要がない）。

    `git_log_checked`はcwd別に管理する辞書`{cwd: True}`形式を採用する。
    旧形式のbool値（`True` / `False`）はcwd空文字列環境向けの後方互換として
    そのまま参照する。
    判定は`extract_git_events`の結果を消費し、各git呼び出しの実効cwd
    （`cd`・`pushd`・`git -C`の影響を反映）ごとに行う。
    実効cwdがscratchpad配下（`_scratchpad_path.is_scratchpad_path`）で、かつ当該cwdの
    `git remote`（`_git_status.run_git_lines`）が空リストの場合だけ、当該イベントを
    検査対象から外す。取得失敗（`None`）または非空リストの場合は検査を適用する
    （取得失敗を除外の根拠にしない）。
    """
    targets: list[tuple[GitEvent, str]] = []
    for event in extract_git_events(command, cwd):
        if event.subcommand == "commit" and "--amend" in event.subcommand_args:
            targets.append((event, "git commit --amend"))
        elif event.subcommand == "rebase":
            targets.append((event, "git rebase"))
    if not targets:
        return False
    unresolved = next(((event, op) for event, op in targets if not event.cwd_resolved), None)
    if unresolved is not None:
        event, op = unresolved
        if event.unresolved_expression is not None:
            reason = f"blocked: {op}。作業ディレクトリを表す式{event.unresolved_expression!r}を静的に解決できない。"
            fix = "先に`git -C <絶対パス> log --oneline --decorate`を実行し、履歴の書き換えを`git -C <絶対パス>`で再実行する。"
        else:
            reason = f"blocked: {op}。コマンドが未解決のシェル展開によって作業ディレクトリを変更している。"
            fix = "先に対象リポジトリで`git log --oneline --decorate`を実行し、静的に解決できる作業ディレクトリで再実行する。"
        print(
            _block_notice(
                reason,
                fix=fix,
            ),
            file=sys.stderr,
        )
        return True
    state = read_state(session_id)
    log_state = state.get("git_log_checked", False)
    for event, op in targets:
        event_cwd = event.cwd
        if event_cwd and (
            _scratchpad_path.is_scratchpad_path(pathlib.Path(event_cwd)) or _is_in_verified_managed_temp(event_cwd)
        ):
            remotes = _git_status.run_git_lines(["git", "remote"], event_cwd)
            if remotes == []:
                continue
        if isinstance(log_state, dict):
            if event_cwd and log_state.get(event_cwd, False):
                continue
        elif log_state:
            continue
        print(
            _block_notice(
                f"blocked: {op}。`amend`・`rebase`の前に`commit`の状態を確認する必要がある。",
                fix=(
                    "amend・rebaseの前に`git log --oneline --decorate`を実行してcommitの状態を確認する"
                    "（特にpush済みのcommitをamend・rebaseしない）。同じBashコマンド内の`git log`はこの検査を満たさない。"
                    "同じ実効作業ディレクトリに対して、先行する別のBash呼び出しで実行する。"
                ),
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- Bash: git push 前 amend後dirty状態のブロック ---


def _check_bash_git_push_after_amend_with_dirty_status(command: str, session_id: str, cwd: str) -> bool:
    """Git push 実行前に amend直後の未コミット差分残置を機械的にブロックする。

    posttooluse側で git commit --amend / --fixup 成功時に設定した
    cwd別の`amend_pending_status_check`フラグがTrueで、かつ現在の作業ツリーに追跡ファイル
    未コミット差分がある場合、pushをブロックして目視・機械両面での確認を促す。
    フラグが立っていないか差分がない場合はブロックしない。
    差分なし時は該当cwdのフラグも解除して通過させるが、解除対象は実送出pushに限定する
    （`git push --dry-run`など送出しないpush系サブコマンドではdirty時blockを実施しclean時は解除せず状態を保つ）。
    差分検出は共有ヘルパー`_git_status.has_tracked_dirty`（`git -C <cwd> status --porcelain`実行）を使い、
    未追跡ファイル（`??`行）を除いた出力行が1件以上あればdirtyと判定する。
    cwd解析は既存の`extract_git_events(command, cwd)`ヘルパーで`git -C <path>`および
    `cd <path> && git push`両形式に対応する（cwd別辞書の実効cwd参照を統一）。
    """
    push_events = [event for event in extract_git_events(command, cwd) if event.subcommand == "push"]
    if not push_events:
        return False
    state = read_state(session_id)
    flags = state.get(_git_status.AMEND_PENDING_FLAG_KEY)
    if not isinstance(flags, dict):
        return False
    for event in push_events:
        if not event.cwd_resolved:
            if any(value is True for value in flags.values()):
                if event.unresolved_expression is not None:
                    reason = (
                        "blocked: `amend`・`fixup`の後の`git push`で、作業ディレクトリを表す式"
                        f"{event.unresolved_expression!r}を解決できない。"
                    )
                    fix = "対象リポジトリを確認したうえで、`git -C <絶対パス> push ...`の形で再実行する。"
                else:
                    reason = "blocked: `amend`・`fixup`の後の`git push`で、作業ディレクトリを解決できない。"
                    fix = "amendの状態を確認し、静的に解決できる作業ディレクトリで再実行する。"
                print(
                    _block_notice(
                        reason,
                        fix=fix,
                    ),
                    file=sys.stderr,
                )
                return True
            continue
        if not flags.get(event.cwd, False):
            continue
        dirty = _git_status.has_tracked_dirty(event.cwd)
        if dirty is None:
            continue
        if dirty:
            print(
                _block_notice(
                    f"blocked: {event.cwd}に追跡対象の未コミット変更が残ったまま、"
                    "`git commit --amend`・`--fixup`の後に`git push`しようとしている。",
                    fix=(
                        "`git status`で内容を確認し、`git add`と`git commit --amend`（または`--fixup=<sha>`）で"
                        "残りの差分をamend済みcommitへ取り込むか、pushの前に後続のcommitを作成する。"
                    ),
                ),
                file=sys.stderr,
            )
            return True
        if _git_status.git_push_is_real_send(event.subcommand_args):
            event_cwd = event.cwd

            def _reset(current: dict, target_cwd: str = event_cwd) -> dict | None:
                current_flags = current.get(_git_status.AMEND_PENDING_FLAG_KEY)
                if not isinstance(current_flags, dict) or not current_flags.get(target_cwd, False):
                    return None
                current_flags[target_cwd] = False
                current[_git_status.AMEND_PENDING_FLAG_KEY] = current_flags
                return current

            update_state(session_id, _reset)
    return False


# --- Bash: 一括ステージ実行時の未編集ファイル警告 ---


def _has_a_flag(args: list[str]) -> bool:
    """`git commit`の`-a`フラグ検出。`--all`、または短フラグクラスタ内の`a`を検出する。

    `-am`・`-amx`等の連結ショートフラグにも一致する。
    簡略化: 値付きフラグ（`-S<key-id>`等でクラスタ内に`a`が現れる形）は誤検出しうる。
    `git commit`の`-S<value>`は`-S <value>`形式でも受け付けるため、
    実運用ではまず短フラグクラスタに値が続かないため許容する。
    見直し契機: 誤警告報告が発生した場合。
    """
    for tok in args:
        if tok == "--all":
            return True
        if tok.startswith("-") and not tok.startswith("--") and "a" in tok[1:]:
            return True
    return False


def _detect_bulk_stage_mode(event: GitEvent) -> str | None:
    """一括ステージ操作の検出。該当時はモード名を返す。

    - `git add -A` / `git add --all` / `git add .`: `include_untracked`
    - `git add -u` / `git add --update`: `tracked_only`
    - `git commit -a` / `git commit --all` / `git commit -am`等: `tracked_only`
    """
    args = event.subcommand_args
    if event.subcommand == "add":
        for tok in args:
            if tok in ("-A", "--all", "."):
                return "include_untracked"
        for tok in args:
            if tok in ("-u", "--update"):
                return "tracked_only"
        return None
    if event.subcommand == "commit":
        if _has_a_flag(args):
            return "tracked_only"
        return None
    return None


def _parse_git_status_short(stdout: str, mode: str) -> set[str]:
    """`git status --short`出力から変更ファイルの相対パス集合を返す。

    `mode == "tracked_only"`のときは`??`（未追跡）行を除外する。
    リネーム行`R  old -> new`は新パスを採用する。
    簡略化: クォート付きパス（`core.quotepath`有効時のUnicodeエスケープ等）は
    先頭・末尾のダブルクォート除去のみで内部のエスケープは非対応。
    見直し契機: エスケープを含むパスで誤検出報告が発生した場合。
    """
    files: set[str] = set()
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        prefix = line[:2]
        if mode == "tracked_only" and prefix == "??":
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path.startswith('"') and path.endswith('"') and len(path) >= 2:
            path = path[1:-1]
        if path:
            files.add(path)
    return files


def _normalize_to_relative(path: str, cwd: str) -> str:
    """絶対パスを`cwd`起点の相対パスへ正規化する。相対パスは`pathlib.Path`のみ適用する。"""
    if not path:
        return path
    p = pathlib.Path(path)
    if p.is_absolute() and cwd:
        try:
            return str(p.relative_to(pathlib.Path(cwd), walk_up=True))
        except ValueError:
            return str(p)
    return str(p)


def _check_bash_bulk_stage_with_unedited_files(
    command: str,
    session_id: str,
    payload_cwd: str,
) -> str | None:
    """一括ステージ実行時に自セッション未編集の変更が含まれる場合の警告文を返す。

    `git add -A/--all/.` は未追跡を含む集合、`git add -u/--update` と
    `git commit -a/--all/-am`等は追跡済みのみを対象として作業ツリー変更を判定する。
    セッション状態の`session_edited_files`集合との差集合が空でない場合、
    個別ファイル指定への切替を促すwarnをhookSpecificOutputで返す。
    """
    for event in extract_git_events(command, payload_cwd):
        mode = _detect_bulk_stage_mode(event)
        if mode is None:
            continue
        if not event.cwd_resolved:
            continue
        effective_cwd = event.cwd
        if not effective_cwd:
            continue
        try:
            proc = subprocess.run(
                ["git", "status", "--short"],
                cwd=effective_cwd,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, FileNotFoundError):
            continue
        if proc.returncode != 0:
            continue
        changed = _parse_git_status_short(proc.stdout, mode)
        if not changed:
            continue
        state = read_state(session_id)
        edited_raw = state.get("session_edited_files", []) or []
        edited: set[str] = set()
        for entry in edited_raw:
            if isinstance(entry, str) and entry:
                edited.add(_normalize_to_relative(entry, effective_cwd))
        changed_norm = {_normalize_to_relative(p, effective_cwd) for p in changed}
        unedited = changed_norm - edited
        if not unedited:
            continue
        sample = sorted(unedited)[:5]
        return _llm_notice(
            "warn: 一括`stage`に、当該セッションのファイル編集ツールによる編集記録が無いファイルが含まれている。"
            "シェルコマンドや生成器が変更したファイルは記録されないため、"
            f"`stage`の前に所有を確認する。候補: {sample}。"
            "ファイル単位の`stage`（`git add <file>`）への切り替えを検討する。",
            tag=_WARN_TAG,
        )
    return None


# --- Bash: git commit未検証警告 ---


_GIT_COMMIT_INCLUDE_WORKTREE_FLAGS: frozenset[str] = frozenset({"--all"})


def _commit_event_includes_worktree(event: GitEvent) -> bool:
    """`GitEvent`の`subcommand_args`から`-a` / `--all`相当の指定を検出する。

    短縮オプション結合（`-am`等）にも対応するため、`-`始まりで`a`を含むトークンを対象とする。
    `--`以降（pathspec区切り）は対象外とする。
    """
    for token in event.subcommand_args:
        if token == "--":
            break
        if token in _GIT_COMMIT_INCLUDE_WORKTREE_FLAGS:
            return True
        if token.startswith("-") and not token.startswith("--") and "a" in token[1:]:
            return True
    return False


def _is_docs_only_commit(event: GitEvent, cwd: str) -> bool:
    """コミット対象のファイルが全てMarkdownの場合に真を返す。

    docs-only変更では手動テストを省略しpre-commit側のtextlint / markdownlintに
    委ねる運用を想定しており、その場合に未検証警告を抑制する。

    `git commit -a` / `--all`等のコマンドでは作業ツリー側の変更も対象となるため、
    stagedとworking treeを切り分けて判定する。
    `cwd`不在やgit呼び出し失敗時は偽を返して警告を継続する。
    """
    if not cwd:
        return False
    include_working_tree = _commit_event_includes_worktree(event)
    args = ["git", "diff", "--name-only", "HEAD"] if include_working_tree else ["git", "diff", "--cached", "--name-only"]
    files = _git_status.run_git_lines(args, cwd)
    if not files:
        return False
    return all(path.lower().endswith(".md") for path in files)


def _check_bash_git_commit(command: str, session_id: str, cwd: str) -> str | None:
    """テスト未実行のままgit commitする場合に警告文を返す。

    テスト実行済み（stateの`test_executed`が真）の場合はスキップする。
    状態ファイル不在時は`test_executed` = falseとして扱い警告を表示する。
    コミット対象が全てMarkdownファイルの場合はpre-commit側に検証を委ねる運用を想定してスキップする。
    `git`コマンドの検出はシェルトークン解析（`extract_git_events`）に基づき、各セグメントの先頭
    トークンが`git`である場合のみサブコマンドを認識する。単純な部分文字列一致と異なり、
    `grep`の検索パターン文字列等クォート内に現れる`git commit`は先頭トークンに現れないため
    誤反応しない（`_check_bash_amend_rebase_without_log`等と同一の検出方式）。
    ヒアドキュメント本文中の記述は`extract_git_events`の既知の限界として本checkでも扱わない
    （`_bash_command_parser.split_bash_segments`のdocstring参照）。
    実効cwdがscratchpad配下（`_scratchpad_path.is_scratchpad_path`）で、かつ当該cwdの
    `git remote`（`_git_status.run_git_lines`）が空リストのイベントは検査対象から外す。
    取得失敗（`None`）または非空リストの場合は検査を適用する（取得失敗を除外の根拠にしない）。
    """
    commit_events = [e for e in extract_git_events(command, cwd) if e.subcommand == "commit"]
    if not commit_events:
        return None
    commit_events = [
        event
        for event in commit_events
        if not (
            event.cwd_resolved
            and (_scratchpad_path.is_scratchpad_path(pathlib.Path(event.cwd)) or _is_in_verified_managed_temp(event.cwd))
            and _git_status.run_git_lines(["git", "remote"], event.cwd) == []
        )
    ]
    if not commit_events:
        return None
    state = read_state(session_id)
    if state.get("test_executed", False):
        return None
    if any(not event.cwd_resolved for event in commit_events):
        return _llm_notice(
            "テストを実行せずにcommitしようとしている。`01-agent.md`の検証後commit手順に従い、先にテストを実行する。",
            tag=_WARN_TAG,
        )
    commit_event = commit_events[0]
    if _is_docs_only_commit(commit_event, commit_event.cwd):
        return None
    return _llm_notice(
        "テストを実行せずにcommitしようとしている。`01-agent.md`の検証後commit手順に従い、先にテストを実行する。",
        tag=_WARN_TAG,
    )


# --- Bash: agent-toolkit/配下のversion bump漏れ警告 ---

_AGENT_TOOLKIT_PREFIX = "agent-toolkit/"
_AGENT_TOOLKIT_PLUGIN_MANIFEST = _plan_format.PLUGIN_MANIFEST_PATH
_AGENT_TOOLKIT_TEST_SUFFIX = "_test.py"
_AGENT_TOOLKIT_SCRIPTS_PREFIX = "agent-toolkit/scripts/"


def _check_bash_agent_toolkit_version_bump(command: str, cwd: str) -> str | None:
    """agent-toolkit/配下の変更をコミットする際にversion bump漏れを警告する。

    判定:

    1. `extract_git_events`が`commit`サブコマンドを1件以上返した場合のみ動作する
       （`_check_bash_git_commit`と同じ検出方式であり、`git commit`という文字列を引数として
       含むだけの読み取り操作では動作しない）
    2. ステージ済みファイルに`agent-toolkit/`配下を含まない、または
       `agent-toolkit/scripts/*_test.py`のみの場合は警告しない
    3. ステージ済み差分に`agent-toolkit/.claude-plugin/plugin.json`を
       含む場合は警告しない
    4. 未プッシュ範囲（`@{u}..HEAD`）に`agent-toolkit/.claude-plugin/plugin.json`
       を変更したコミットがある場合は警告しない。`@{u}`が解決できない場合
       （上流未設定・追跡先削除済みの`gone`状態）は、構成済みリモートの既定ブランチ
       （`refs/remotes/<remote>/HEAD`）との比較へフォールバックする。フォールバックも
       解決できない場合は警告側へ倒す
    5. 上記いずれにも該当しない場合、warn JSONを返す
    """
    commit_events = [event for event in extract_git_events(command, cwd) if event.subcommand == "commit"]
    if not commit_events or any(not event.cwd_resolved for event in commit_events):
        return None
    effective_cwd = commit_events[0].cwd
    if not effective_cwd:
        return None

    staged = _git_status.run_git_lines(["git", "diff", "--cached", "--name-only"], effective_cwd)
    if staged is None or not staged:
        return None
    agent_toolkit_files = [p for p in staged if p.startswith(_AGENT_TOOLKIT_PREFIX)]
    if not agent_toolkit_files:
        return None
    non_test_files = [
        p
        for p in agent_toolkit_files
        if not (p.startswith(_AGENT_TOOLKIT_SCRIPTS_PREFIX) and p.endswith(_AGENT_TOOLKIT_TEST_SUFFIX))
    ]
    if not non_test_files:
        return None
    if _AGENT_TOOLKIT_PLUGIN_MANIFEST in staged:
        return None

    unpushed = _git_status.run_git_lines(
        ["git", "rev-list", "@{u}..HEAD", "--", _AGENT_TOOLKIT_PLUGIN_MANIFEST],
        effective_cwd,
    )
    if unpushed is None:
        default_branch = _git_status.resolve_default_branch(effective_cwd)
        if default_branch is not None:
            unpushed = _git_status.run_git_lines(
                ["git", "rev-list", f"{default_branch}..HEAD", "--", _AGENT_TOOLKIT_PLUGIN_MANIFEST],
                effective_cwd,
            )
    if unpushed:
        return None

    return _llm_notice(
        "agent-toolkit配下のファイルがstageされているが、当該commitと未push範囲で"
        "`agent-toolkit/.claude-plugin/plugin.json`の`version`が変更されていない。"
        "フックスクリプト、スキル、エージェント定義、ルールファイルなどの利用者向け挙動を変更する場合は、"
        "commit前に`plugin.json`の`version`を更新し、`.claude-plugin/marketplace.json`も同期する。",
        tag=_WARN_TAG,
    )


# --- Bash: git log --decorate自動付与 ---


def _mask_quoted_text(segment: str) -> str:
    """引用符とその内側を同じ長さのNUL文字へ置換する。"""
    masked = list(segment)
    quote: str | None = None
    escaped = False
    for index, character in enumerate(segment):
        if quote is not None:
            masked[index] = "\x00"
            if escaped and quote == '"':
                escaped = False
            elif character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            masked[index] = "\x00"
    return "".join(masked)


def _git_subcommand_index(tokens: tuple[str, ...]) -> int | None:
    """実行位置以降のgitトークン列からサブコマンド位置を返す。"""
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--") and "=" in token:
            key = token.partition("=")[0]
            if key in _GLOBAL_OPTIONS_WITH_VALUE:
                index += 1
                continue
            return None
        if token in _GLOBAL_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                return None
            index += 2
            continue
        if token in _GLOBAL_OPTIONS_WITHOUT_VALUE:
            index += 1
            continue
        if token.startswith("-"):
            return None
        return index
    return None


def _check_bash_git_log_decorate(command: str, tool_input: dict) -> dict | None:
    r"""Git logに--decorateがない場合、自動で挿入したupdatedInputを返す。

    ヒアドキュメント本文を除く各区間について、実行位置のgitサブコマンドと元コマンド上の
    語の位置を同時に解決する。引用符内の字面や位置対応を確定できない区間は変更しない。
    """
    analysis = command.split("<<", 1)[0]
    previous_end = 0
    for segment in split_bash_segments(analysis):
        segment_start = analysis.find(segment, previous_end)
        if segment_start < 0:
            continue
        previous_end = segment_start + len(segment)
        try:
            raw_tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        execution = resolve_execution_segment(raw_tokens)
        if not execution.resolved or not execution.tokens:
            continue
        command_token = execution.tokens[0]
        if any(marker in command_token for marker in _PROCESS_KILL_UNSAFE_MARKERS):
            continue
        if pathlib.PurePosixPath(command_token).name != "git":
            continue
        words = list(re.finditer(r"\S+", _mask_quoted_text(segment)))
        if len(words) != len(raw_tokens):
            continue
        subcommand_index = _git_subcommand_index(execution.tokens)
        if subcommand_index is None or execution.tokens[subcommand_index] != "log":
            continue
        raw_subcommand_index = len(raw_tokens) - len(execution.tokens) + subcommand_index
        subcommand_word = words[raw_subcommand_index]
        if "\x00" in subcommand_word.group():
            continue
        subcommand_args = execution.tokens[subcommand_index + 1 :]
        if any(token in {"--decorate", "--no-decorate"} or token.startswith("--decorate=") for token in subcommand_args):
            continue
        insertion = segment_start + subcommand_word.end()
        updated_input = dict(tool_input)
        updated_input["command"] = command[:insertion] + " --decorate" + command[insertion:]
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": updated_input,
            },
        }
    return None
