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
import difflib
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
        _FOREIGN_SCRIPT_RE,
        _JAPANESE_SCRIPT_RE,
        _REPLACEMENT_CHAR,
        _is_claude_job_file,
        _is_plan_file_or_adjunct,
        _materialize_cached,
    )
    from agent_toolkit._hooks.pretooluse.notices import _block_notice, _llm_notice


def _check_edit_operation_blocks(
    tool_name: str,
    operation: _hook_tool_input.EditOperation,
) -> bool:
    """1操作分の遮断検査を実行する。"""
    fields = [(fragment.label, fragment.after) for fragment in operation.fragments]
    display_path = operation.display_path
    if (
        _check_mojibake(tool_name, fields)
        or _check_foreign_script_mixin(tool_name, fields)
        # PowerShellの改行要件はpatch断片のLF表現から判定できないため、Claudeの`Write`だけへ適用する。
        or (tool_name == "Write" and _is_ps1(display_path) and _check_ps1_eol(tool_name, fields, display_path))
    ):
        return True
    return any(_check_lockfiles(tool_name, path) or _check_secrets(tool_name, path) for path in operation.display_paths)


def _check_edit_boundary_resolution(
    tool_name: str,
    operations: list[_hook_tool_input.EditOperation],
) -> bool:
    """複数断片の全境界が現在内容へ一意に解決できるかを遮断前に確認する。"""
    del tool_name  # noqa: PLW0613
    if sum(len(operation.fragments) for operation in operations) < 2:
        return False
    unresolved = [
        f"{operation.display_path}: {label}"
        for operation in operations
        if (labels := _hook_tool_input.unresolved_fragment_labels(operation))
        for label in labels
    ]
    if not unresolved:
        return False
    print(
        _block_notice(
            "blocked: 複数の境界を持つ編集入力に、現在のファイル内容へ一意に適用できない境界がある。"
            "対象ファイルは変更していない。\n" + "\n".join(unresolved),
            fix="対象ファイルの現行内容を取得し、一致しない境界を現行の文面へそろえてから編集を再実行する。",
        ),
        file=sys.stderr,
    )
    return True


def _collect_edit_operation_warnings(
    tool_name: str,
    operation: _hook_tool_input.EditOperation,
    index: int,
    images: dict[int, _hook_tool_input.MaterializedEdit | None],
    *,
    is_codex: bool,
) -> list[str]:
    """1操作分の警告本文を順に集める。"""
    fields = [(fragment.label, fragment.after) for fragment in operation.fragments]
    display_path = operation.display_path
    image = _materialize_cached(operation, index, images)
    if image is None:
        colloquial_warning = next(
            (
                warning
                for _, value in fields
                if (warning := _check_colloquial(tool_name, None, value, operation.path)) is not None
            ),
            None,
        )
    else:
        colloquial_warning = _check_colloquial(
            tool_name,
            image.before_image,
            image.after_image,
            operation.path,
        )

    # 断片入力を使う検査。
    warnings = [
        warning
        for warning in (
            _check_manifest(tool_name, display_path),
            _check_home_path(tool_name, fields, display_path),
            colloquial_warning,
            _check_style_negation(tool_name, operation, display_path),
        )
        if warning is not None
    ]
    if is_codex:
        # 同一patch内で追加・移動する参照先を実ファイルだけで解決できないため、
        # 外部ファイル解決を伴う2検査はClaude入力へ限定する。
        return warnings
    # 全文像を使う検査。
    content = image.after_image if image is not None else None
    if content is None:
        return warnings
    warnings.extend(
        warning for warning in (_check_body_section_reference_exists(tool_name, content, display_path),) if warning is not None
    )
    return warnings


def _check_foreign_script_mixin(tool_name: str, fields: list[tuple[str, str]]) -> bool:
    """日本語を含む文字列へのハングル・キリル文字の混入を検出したらTrueを返す。

    日本語（ひらがな・カタカナ・漢字）を含まない文字列は対象外とする。
    多言語の文字列を意図的に扱う場面での誤検出を避けるためである。
    文脈の提示には`ascii()`を用いる。`repr()`は非ASCII文字をそのまま出力するため、
    メッセージ自体が検出対象文字を含むことになる。
    """
    for field, value in fields:
        if _JAPANESE_SCRIPT_RE.search(value) is None:
            continue
        match = _FOREIGN_SCRIPT_RE.search(value)
        if match is None:
            continue
        start = max(0, match.start() - 10)
        end = min(len(value), match.end() + 10)
        print(
            _block_notice(
                f"blocked: 日本語本文の`{tool_name}.{field}`に日本語以外の文字（ハングル／キリル文字）が混入している。"
                f"文脈: {ascii(value[start:end])}。",
                fix="意図した日本語の文字へ置き換える。",
            ),
            file=sys.stderr,
        )
        return True
    return False


def _check_mojibake(tool_name: str, fields: list[tuple[str, str]]) -> bool:
    """U+FFFD（mojibake）を検出したらTrueを返す。"""
    for field, value in fields:
        position = value.find(_REPLACEMENT_CHAR)
        if position == -1:
            continue
        start = max(0, position - 10)
        end = min(len(value), position + 11)
        sample = value[start:end]
        print(
            _block_notice(
                f"blocked: `{tool_name}.{field}`にU+FFFD（文字化け）を検出した。文脈: {sample!r}",
                fix="U+FFFDを意図した文字へ置き換えて再実行する。",
            ),
            file=sys.stderr,
        )
        return True
    return False


def _is_ps1(file_path: str) -> bool:
    """`.ps1` / `.ps1.tmpl`の場合に真を返す。"""
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


def _check_ps1_eol(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """BOMなしのLF-only書き込みと改行規約の不一致を検出したらTrueを返す。"""
    for field, value in fields:
        if "\n" not in value:
            continue
        if "\r\n" in value:
            continue
        print(
            _block_notice(
                f"blocked: `{tool_name}.{field}`にLFだけの内容を検出した。"
                "この書き込みではUTF-8 BOMが失われて日本語が文字化けし、"
                f"`.gitattributes`の`*.ps1 text eol=crlf`規約とも一致しない。対象: {file_path}",
                fix=(
                    "既存ファイルにはEditツールを使う（CRLFを透過的に維持する）。"
                    "新規ファイルはBashでUTF-8 BOMとCRLF改行を指定して書き込む。"
                ),
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- lockfile / 生成物ディレクトリcheck ---

# （label, regex, hint）のタプル。regexはfile_path全体に対するマッチ。
_LOCKFILE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("uv.lock", re.compile(r"(^|/)uv\.lock$"), "依存の追加には`uv add`、削除には`uv remove`を使う。"),
    (
        "pnpm-lock.yaml",
        re.compile(r"(^|/)pnpm-lock\.yaml$"),
        "依存の追加には`pnpm add`、削除には`pnpm remove`を使う。",
    ),
    ("package-lock.json", re.compile(r"(^|/)package-lock\.json$"), "依存の追加には`npm install <pkg>`を使う。"),
    ("yarn.lock", re.compile(r"(^|/)yarn\.lock$"), "依存の追加には`yarn add`を使う。"),
    ("Cargo.lock", re.compile(r"(^|/)Cargo\.lock$"), "依存の追加には`cargo add`を使う。"),
    ("mise.lock", re.compile(r"(^|/)mise\.lock$"), "ツール管理には`mise use`・`mise install`を使う。"),
    (
        ".venv/",
        re.compile(r"(^|/)\.venv/"),
        "仮想環境のファイルを直接編集せず、`uv`などで再構築する。",
    ),
    (
        "node_modules/",
        re.compile(r"(^|/)node_modules/"),
        "`node_modules`は生成ディレクトリであるため、直接編集しない。",
    ),
)


def _check_lockfiles(tool_name: str, file_path: str) -> bool:
    """lockfileや生成物ディレクトリへの直接編集を検出した場合に真を返す。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _LOCKFILE_RULES:
        if pattern.search(normalized):
            fix = "このパスを直接編集せず、パッケージ管理ツールで再生成する。" if label in {".venv/", "node_modules/"} else hint
            print(
                _block_notice(
                    f"blocked: {tool_name}による{label}の直接編集は禁止されている。対象: {file_path}",
                    fix=fix,
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- シークレット / 鍵ファイルcheck ---

_SECRETS_PATTERN = re.compile(
    r"(^|/)("
    r"\.env(\..+)?"
    r"|\.encrypt_key"
    r"|\.secret_key"
    r"|github_action(\.pub)?"
    r"|[^/]+\.(pem|key)"
    r")$"
)

_SECRETS_EXEMPT_SUFFIXES: tuple[str, ...] = (".example", ".sample", "-example", "-sample")

# `.env`・`.env.<接尾辞>`だけを対象とする案内付加用のパターン。
# 遮断対象と戻り値は`_SECRETS_PATTERN`が決めるため、本パターンは案内の有無だけを分ける。
_ENV_FILE_PATTERN = re.compile(r"(^|/)\.env(\..+)?$")

# `.env`系の遮断時だけ添える代替経路の案内。
# 対象を`.env`系へ限定するのは、鍵・証明書へBash経由の改変経路を案内しないためである。
_ENV_FILE_GUIDANCE = (
    "Git worktreeを実行可能にする場合は、Bashの`cp`で原本を複製する。"
    "簡易確認のために値を追加・変更・削除する場合は、編集ツールでファイル全体を書き換えず、"
    "Bashの`echo ... >>`または`sed -i`で行を操作する。"
)


def _check_secrets(tool_name: str, file_path: str) -> bool:
    """シークレット / 鍵ファイルへの直接編集を検出した場合に真を返す。

    遮断対象が`.env`・`.env.<接尾辞>`の場合だけ、遮断理由の直後へ
    `cp`による複製とBash経由の値操作という代替経路の案内を添える。
    鍵・証明書などの他の対象では遮断理由だけを表示する。
    """
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if normalized.endswith(_SECRETS_EXEMPT_SUFFIXES):
        return False
    if _SECRETS_PATTERN.search(normalized):
        guidance = (
            _ENV_FILE_GUIDANCE.strip()
            if _ENV_FILE_PATTERN.search(normalized)
            else "鍵または証明書ファイルを編集せず、この編集を中止する。"
        )
        print(
            _block_notice(
                f"blocked: {tool_name}によるシークレット・鍵ファイルの直接編集は禁止されている。"
                f"誤編集はサービス停止や情報漏洩を招く。対象: {file_path}",
                fix=guidance,
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- manifest手編集check (warn) ---

_MANIFEST_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "pyproject.toml",
        re.compile(r"(^|/)pyproject\.toml$"),
        (
            "`[project.dependencies]`・`[project.optional-dependencies]`の編集は、"
            "`uv.lock`を同期させるため`uv add`・`uv remove`を使う。"
            "`[tool.*]`と版数の編集はそのまま進めてよい。"
        ),
    ),
    (
        "package.json",
        re.compile(r"(^|/)package\.json$"),
        (
            "依存の編集は、`pnpm-lock.yaml`を同期させるため`pnpm add`・`pnpm remove`を使う。"
            "`scripts`とメタデータの編集はそのまま進めてよい。"
        ),
    ),
)


def _check_manifest(tool_name: str, file_path: str) -> str | None:
    """manifest手編集を検出したら警告本文を返す（warnのみ、exit codeは変えない）。"""
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _MANIFEST_RULES:
        if pattern.search(normalized):
            return _llm_notice(
                f"`{tool_name}`で`{label}`を編集しようとしている。{hint}",
                tag=_WARN_TAG,
                removable_cause=True,
            )
    return None


# --- ホームディレクトリパス混入check (warn) ---

# 混入を許容するファイル末尾パターン（ローカル設定やログなど）
_HOME_PATH_SKIP_SUFFIXES: tuple[str, ...] = (
    ".local.md",
    ".local.json",
    ".local.yaml",
    ".local.yml",
    ".local.toml",
    ".jsonl",
    ".log",
)


def _unmanaged_path(resolved_path: pathlib.Path) -> bool | None:
    """対象パスの最近接既存親からルートまでのGit管理マーカーを調べる。"""
    try:
        existing_parent = resolved_path if resolved_path.is_dir() else resolved_path.parent
        while not existing_parent.exists():
            existing_parent = existing_parent.parent
        for parent in (existing_parent, *existing_parent.parents):
            try:
                (parent / ".git").lstat()
            except FileNotFoundError:
                continue
            except OSError:
                return None
            return False
        return True
    except (OSError, ValueError):
        return None


def _check_home_path(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    """ホームディレクトリの絶対パス混入を検出したら警告本文を返す。

    リポジトリ管理ファイルに`/home/user/...`のような環境依存パスが書き込まれると
    他環境での再現性が失われるため警告する。警告のみでeditは継続（warn）。
    Git管理外の作業文書であり、正確な絶対パスを記録する計画ファイルは対象外とする。
    対象パスの最近接既存親からルートまでにGit管理マーカーがないと確定できた文書は対象外とする。
    マーカーの確認不能時は既存検査を継続する。
    """
    if _is_plan_file_or_adjunct(file_path) or _is_claude_job_file(file_path):
        return None

    try:
        resolved_path = pathlib.Path(file_path).resolve()
        if _unmanaged_path(resolved_path) is True:
            return None
    except (OSError, ValueError):
        pass

    home_str = str(pathlib.Path.home())
    # ルートなど極端に短いパスは誤検出を避けてスキップ。
    if len(home_str) < 3:
        return None

    normalized_path = file_path.replace("\\", "/")
    if normalized_path.endswith(_HOME_PATH_SKIP_SUFFIXES):
        return None
    if normalized_path.endswith("/CLAUDE.local.md") or normalized_path == "CLAUDE.local.md":
        return None
    if normalized_path.endswith("/.claude/settings.local.json"):
        return None

    # POSIX正規化された両表記で検査（WindowsからPOSIX風パスが混入するケースに対応）
    candidates = {home_str, home_str.replace("\\", "/")}

    for field, value in fields:
        for home in candidates:
            position = value.find(home)
            if position == -1:
                continue
            start = max(0, position - 20)
            end = min(len(value), position + len(home) + 20)
            sample = value[start:end]
            return _llm_notice(
                f"`{tool_name}.{field}`にホームディレクトリの絶対パス（{home}）を検出した。"
                "版管理対象のファイルでは、環境依存のパスを避けるため`~`、`$HOME`、"
                f"または`pathlib.Path.home()`を使う。文脈: {sample!r}",
                tag=_WARN_TAG,
                removable_cause=True,
            )
    return None


# --- 口語表現混入check (warn) ---

# モジュールロード時に1回だけコンパイルする。
# 本ファイルからパターンの実体を文字列で参照せず、辞書を正本として読み込む。
_COLLOQUIAL_DENY_PATTERNS = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
_COLLOQUIAL_ALLOW_PATTERNS = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)

_COLLOQUIAL_MAX_LISTED_MATCHES = 5
"""口語表現検査及び誤字検査の通知へ列挙する一致位置の上限。超過分は総件数だけを示す。"""
_MANAGED_TEMP_MARKER = ".agent-toolkit-managed-temp.json"

COLLOQUIAL_DETECTED_TERMS_LABEL = "検出語"


def colloquial_detected_terms_text(detected_terms: Iterable[str]) -> str:
    """口語表現検査の通知本文が検出語を示す部分を返す。

    同じ仕様を複数の検体が別方向に固定して一致しなくなることを防ぐため、実装と検体はこの1箇所だけを参照する。
    """
    joined = "、".join(dict.fromkeys(detected_terms))
    return f"{COLLOQUIAL_DETECTED_TERMS_LABEL}: {joined}。"


# --- ユーザーが直接読む本文への誤字検査 (warn) ---

_TYPO_DICT_PATH = pathlib.Path(__file__).parent / "typo_words.txt"
# モジュールロード時に1回だけコンパイルする。実際に観測した誤字だけを登録した辞書のため、
# 口語表現検査と異なりallowlistは持たない。
_TYPO_PATTERNS = _colloquial_check.load_patterns(_TYPO_DICT_PATH)

TYPO_DETECTED_TERMS_LABEL = "誤字候補"


def typo_detected_terms_text(detected_pairs: Iterable[tuple[str, str | None]]) -> str:
    """誤字検査の通知本文が誤字候補を示す部分を返す。

    同じ仕様を複数の検体が別方向に固定して一致しなくなることを防ぐため、実装と検体はこの1箇所だけを参照する。
    """
    joined = "、".join(
        f"{detected}→{replacement}" if replacement is not None else detected
        for detected, replacement in dict.fromkeys(detected_pairs)
    )
    return f"{TYPO_DETECTED_TERMS_LABEL}: {joined}。"


def check_user_facing_typo(tool_name: str, fields: list[tuple[str, str]]) -> str | None:
    """ユーザーが直接読む本文への日本語の変換誤りを検出して警告本文を返す（warn）。

    総件数、欄名及び先頭`_COLLOQUIAL_MAX_LISTED_MATCHES`件までの位置（行・列）を示す。
    上限を超える一致の位置は総件数だけで示す。誤字候補と置換候補は`typo_detected_terms_text`経由で示す。
    """
    matches: list[tuple[str, int, int, str, str | None]] = []
    for field, value in fields:
        for line_no, column, detected, _snippet, replacement in _colloquial_check.scan_text(value, _TYPO_PATTERNS, []):
            matches.append((field, line_no, column, detected, replacement))
    if not matches:
        return None
    listed = "; ".join(
        f"{field}の行{line_no}、列{column}" for field, line_no, column, *_ in matches[:_COLLOQUIAL_MAX_LISTED_MATCHES]
    )
    terms = typo_detected_terms_text((detected, replacement) for _, _, _, detected, replacement in matches)
    return _llm_notice(
        f"`{tool_name}`が渡すユーザー向け本文に誤字候補を検出した。一致: {len(matches)}件（{listed}）。{terms}"
        "変換誤りかどうかを本文の文脈で判定し、誤りである場合は当該箇所を修正してから同じ呼び出しを再発行する。"
        f" 対象: {tool_name}",
        tag=_WARN_TAG,
        removable_cause=True,
    )


def _is_in_managed_temp(file_path: str) -> bool:
    """Git作業ツリー境界より内側に管理対象一時領域のマーカーがある場合に真を返す。"""
    try:
        current = pathlib.Path(file_path).expanduser().resolve(strict=False)
        current = current if current.is_dir() else current.parent
        for directory in (current, *current.parents):
            if (directory / _MANAGED_TEMP_MARKER).is_file():
                return True
            if (directory / ".git").exists():
                return False
        return False
    except (OSError, ValueError):
        return False


def _check_colloquial(
    tool_name: str,
    before_image: str | None,
    after_image: str,
    file_path: str,
) -> str | None:
    """口語的な日本語表現の混入を検出して警告本文を返す（warn）。

    総件数、検出語の一覧及び先頭`_COLLOQUIAL_MAX_LISTED_MATCHES`件までの位置（行・列）を示す。
    上限を超える一致の位置は総件数だけで示す。
    allowlistに一致する部分を先に除去してからdenylistを適用し、
    複合動詞・複合名詞などの標準用語が誤検出されることを抑える。
    """
    # 計画ファイルは起草中の素材に口語表現が含まれることがあり、専用の計画検査と
    # writing-standardsの除外規定が適用されるため、この警告だけを対象外とする。
    if file_path and (_is_plan_file_or_adjunct(file_path) or _is_in_managed_temp(file_path)):
        return None
    if not after_image:
        return None
    hits = _colloquial_check.scan_text(after_image, _COLLOQUIAL_DENY_PATTERNS, _COLLOQUIAL_ALLOW_PATTERNS)
    if before_image is not None:
        changed_lines = {
            line_no
            for opcode, _, _, after_start, after_end in difflib.SequenceMatcher(
                a=before_image.splitlines(),
                b=after_image.splitlines(),
            ).get_opcodes()
            if opcode in {"replace", "insert"}
            for line_no in range(after_start + 1, after_end + 1)
        }
        hits = [hit for hit in hits if hit[0] in changed_lines]
    if not hits:
        return None
    listed = "; ".join(f"行{line_no}、列{column}" for line_no, column, *_ in hits[:_COLLOQUIAL_MAX_LISTED_MATCHES])
    target = file_path or tool_name
    return _llm_notice(
        f"`{tool_name}`が書き込む変更行に口語的な日本語表現を検出した。"
        f"一致: {len(hits)}件（{listed}）。{colloquial_detected_terms_text(hit[2] for hit in hits)}"
        "ユーザーへ向けた発話は`agent-toolkit/share/rules-main.md`「ユーザー向け発話ルール」、"
        "それ以外の成果物は`agent-toolkit:writing-standards`の`references/writing.md`「日本語の書き方」に従う。"
        "検出箇所を含む文全体を書き換える。単語だけを同義語へ置き換えず、文全体を組み直す。"
        f" 対象: {target}",
        tag=_WARN_TAG,
        removable_cause=True,
    )


# --- 「Xを根拠にYしない」形式の増加検出 (warn, FB10) ---

# 誤読リスクのある禁止規定形式。
# 「Xでなければ`Y`してよい」と誤読される可能性があるため、全称否定形への書き換えを推奨する。
_STYLE_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"([^、\s]{1,20})を根拠に([^、\s]{1,20})しない"),
    re.compile(r"([^、\s]{1,20})を理由に([^、\s]{1,20})しない"),
)


def _is_style_negation_target_doc(file_path: str) -> bool:
    """対象ドキュメント（コーディングエージェント向け文書判定対象と同一の判定基準）への編集かを判定する。"""
    return _plan_format.is_agent_doc_target_file(file_path)


def _count_style_negation_matches(text: str) -> int:
    """`_STYLE_NEGATION_PATTERNS`の総マッチ件数を返す。"""
    return sum(len(pattern.findall(text)) for pattern in _STYLE_NEGATION_PATTERNS)


def _check_style_negation(tool_name: str, operation: _hook_tool_input.EditOperation, file_path: str) -> str | None:
    """『Xを根拠にYしない』『Xを理由にYしない』形式の増加を検出したら警告本文を返す（warn）。

    全文を書き込む操作（Claudeの`Write`、Codex patchの`*** Add File:`）は変更後全文の
    マッチ件数が1件以上であれば警告する。断片単位の操作（ClaudeのEdit・MultiEdit、
    Codex patchの`*** Update File:`）は断片ごとに変更前後の件数を比較し、増加時のみ警告する
    （既存文字列の保持時は件数同数で誤検出しない）。
    """
    if not _is_style_negation_target_doc(file_path):
        return None
    if operation.is_whole_write:
        increased = _count_style_negation_matches(operation.whole_after_text or "") > 0
    else:
        increased = any(
            _count_style_negation_matches(fragment.after) > _count_style_negation_matches(fragment.before)
            for fragment in operation.fragments
        )
    if not increased:
        return None
    return _llm_notice(
        f"{tool_name}による編集で「`X`を根拠に`Y`しない」「`X`を理由に`Y`しない」形のメタ規範表現が増加した。"
        f"対象: {file_path}。この形は「`X`でなければ`Y`してよい」と読み違えられる。"
        "全称否定形（「いかなる理由（例: `X`）があっても`Y`しない」）への書き換えを検討する。",
        tag=_WARN_TAG,
        removable_cause=True,
    )


# frontmatter区間（`^---$`〜`^---$`）の抽出用。
_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)


def _resolve_referenced_path(file_path: str, referenced: str) -> pathlib.Path | None:
    """`file_path`の祖先ディレクトリを起点に`referenced`（相対パス）の実ファイルを探索する。

    frontmatterの同期注記は同一ディレクトリまたは近隣ディレクトリの兄弟ファイルを
    裸ファイル名（例: `02-agent-operations.md`）で参照する形式が実運用で使われるため、
    `.git`を持つ祖先（リポジトリルート）を発見しても即確定とせず、以下の順に実在確認する。

    1. `file_path`の各祖先ディレクトリ（近い順。同一ディレクトリの兄弟ファイル参照に対応）
    2. リポジトリルート配下の`agent-toolkit/rules/`・`agent-toolkit/skills/`
       （近隣ディレクトリの参照に対応。`.git`祖先が見つかった場合のみ）

    いずれの経路でも実在しない場合は`None`を返す。
    """
    start = pathlib.Path(file_path).resolve().parent
    ancestors = (start, *start.parents)
    search_roots: list[pathlib.Path] = list(ancestors)

    repo_root: pathlib.Path | None = None
    for candidate in ancestors:
        if (candidate / ".git").exists():
            repo_root = candidate
            break
    if repo_root is not None:
        search_roots.extend(repo_root / neighbor for neighbor in ("agent-toolkit/rules", "agent-toolkit/skills"))

    for candidate in search_roots:
        resolved = candidate / referenced
        if resolved.exists():
            return resolved
    return None


# --- .md規範文書の本文中にある節参照の実在検証check (warn) ---

_BODY_SECTION_REFERENCE_RE = re.compile(r"`([^`\n]+\.md)`「([^」\n]+)」[節項]")


def _check_body_section_reference_exists(tool_name: str, content: str, file_path: str) -> str | None:
    """規範文書の本文中にある他ファイルの節参照の実在を検査して警告本文を返す（warn）。

    本checkは本文（frontmatter区間を除く）を走査する。
    参照先ファイル名が複数のパスへ一致する場合は照合せず、一意に解決できない旨を警告する。
    """
    # 対象ファイル判定: `agent-toolkit/rules/`・`agent-toolkit/skills/`・`agent-toolkit/agents/`配下の`.md`
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    is_target = any(
        pattern.search(normalized)
        for pattern in (
            re.compile(r"(^|/)agent-toolkit/rules/[^/]+\.md$"),
            re.compile(r"(^|/)agent-toolkit/skills/(?:(?!.*/references/).)+/[^/]+\.md$"),
            re.compile(r"(^|/)agent-toolkit/agents/[^/]+\.md$"),
        )
    )
    if not is_target:
        return None

    # frontmatter区間を除いた本文のみを走査対象とする。
    frontmatter_match = _FRONTMATTER_BLOCK_RE.match(content)
    self_body = content[frontmatter_match.end() :] if frontmatter_match is not None else content

    # 本文から節参照を抽出
    references = _BODY_SECTION_REFERENCE_RE.findall(self_body)
    if not references:
        return None

    reasons: list[str] = []
    for file_name, section_name in references:
        # ファイル参照の解決: 相対解決を第一とし、リポジトリ内で一意に定まる場合のみ照合
        resolved = _resolve_referenced_path(file_path, file_name)
        if resolved is None:
            reasons.append(f"referenced file path does not exist: {file_name}")
            continue

        # 参照先ファイルを読み込み、節名を照合
        try:
            referenced_body = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            reasons.append(f"failed to read referenced file: {file_name}")
            continue

        # 見出し一致（`^#+\s*<節名>$`）または部分文字列一致で照合
        search_corpus = referenced_body
        heading_pattern = re.compile(rf"^#+\s*{re.escape(section_name)}\s*$", re.MULTILINE)
        if heading_pattern.search(search_corpus) is None and section_name not in search_corpus:
            reasons.append(f"section name does not exist: `{file_name}` '{section_name}'")

    if not reasons:
        return None
    return _llm_notice(
        "規範文書の本文が持つ節参照が実在しない可能性がある"
        f"（{tool_name}、対象: {file_path}）: {'; '.join(reasons)}。"
        "参照先のファイルと節名が一致することを確認する。",
        tag=_WARN_TAG,
        removable_cause=True,
    )


# --- plan mode中のplan file編集をplan-modeスキル未起動の場合にブロック ---

_PLAN_FILE_EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


def _check_plan_mode_skill_first(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> str | None:
    """plan-modeスキル未起動のままplan fileを編集しようとした場合に警告する。

    判定条件:

    - `session_id`が空でない（空ならセッション状態を取得できず判定不能のためスキップ）
    - セッション状態の`plan_mode_skill_invoked`が偽
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - 対象の`file_path`が計画作業root（`~/.claude/plans/`）または保存済み計画root
      （`$(atk config get private_notes)/plans/`）の計画ファイル（メイン）・計画ファイル（詳細）・計画ファイル（バグ）

    `permission_mode`の値に依らず適用する（plan mode外でも計画ファイル編集時には同様に違反が起こり得るため）。
    サブエージェント経由の呼び出しでも同一の判定が働く
    （本checkは`isSidechain`を参照せず、`permission_mode`とセッション状態のみで判定するため）。
    計画ファイル編集に至るまでは警告を表示しない
    （`agent-toolkit:process-wi`等の他スキル呼び出し・通常のRead・Bash操作は素通りする）。
    既存計画へのEdit・MultiEditで、一意かつ最後の`## 進捗ログ`見出し行までの接頭部が
    編集後も不変である場合は、受領側の正規操作として警告しない。
    ファイル又は入力を解釈できない場合は警告を維持する。
    警告のみでツール呼び出しは継続する（block降格）。
    呼び出し元はplan-modeの直接委譲手順で計画確定前に警告を解消・検収する。
    違反を検出した場合は通知本文を返し、呼び出し元が`additionalContext`へ結合する。
    違反が無い場合はNoneを返す。
    """
    if not session_id:
        return None
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return None
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not _is_plan_file_or_adjunct(file_path_raw):
        return None
    state = read_state(session_id)
    if state.get("plan_mode_skill_invoked", False):
        return None
    if tool_name in {"Edit", "MultiEdit"} and _is_progress_log_only_edit(tool_name, tool_input, file_path_raw):
        return None
    return _llm_notice(
        "warning: `agent-toolkit:plan-mode`スキルを起動せずに計画ファイルを編集している。"
        "自身で計画を起草する場合は、同スキルを起動し、計画ファイルの編集を続ける前に`Phase 1`（初期理解）からやり直す。"
        "委譲した計画をレビューし、成果物と根拠から一意に定まる値だけを訂正する場合は、"
        "訂正内容と根拠を`## 変更履歴`へ記録したうえで、`plan-mode`をやり直さずに続行する。"
        "計画を確定する前に、`plan-mode`の直接委譲の手順でこの警告を解消して検証する。",
        tag=_WARN_TAG,
        removable_cause=True,
    )


def _is_progress_log_only_edit(tool_name: str, tool_input: dict, file_path: str) -> bool:
    """既存計画の進捗ログ節だけを変更するEdit又はMultiEditであるかを返す。"""
    try:
        existing = pathlib.Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    edited = _apply_edits_to_content(tool_name, tool_input, existing)
    if edited is None:
        return False
    existing_prefix = _progress_log_heading_prefix(existing)
    edited_prefix = _progress_log_heading_prefix(edited)
    return existing_prefix is not None and existing_prefix == edited_prefix


def _progress_log_heading_prefix(content: str) -> str | None:
    """一意かつ最後の進捗ログH2見出し行までの接頭部を返す。"""
    headings = _plan_format.extract_headings(content)
    progress_index = _plan_format.find_heading_index(headings, 2, _plan_format.PLAN_H2_PROGRESS)
    if progress_index is None:
        return None
    h2_headings = [heading for heading in headings if heading.level == 2]
    if sum(heading.text in _plan_format.h2_aliases(_plan_format.PLAN_H2_PROGRESS) for heading in h2_headings) != 1:
        return None
    progress_heading = headings[progress_index]
    if not h2_headings or h2_headings[-1] != progress_heading:
        return None
    return "".join(content.splitlines(keepends=True)[: progress_heading.lineno])


# --- plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続をブロック ---


# `_check_direct_agent_toolkit_edits_after_plan_mode`専用の配布先追加パターン。
# 原本パス（`agent-toolkit/rules/`・`agent-toolkit/skills/.../SKILL.md`・
# `agent-toolkit/skills/.../references/`・`agent-toolkit/agents/`）は
# `_plan_format.is_agent_doc_target_file`のSSOTを再利用して判定するため本定数へ列挙しない。
# 本定数は原本パスから配布された実在経路のみを追加対象として保持する。
#
# 実在する配布経路は次の2系統である。
#
# - `agent-toolkit/rules/` → `~/.claude/rules/agent-toolkit/`
#   （`pytools/_internal/sync_agent_toolkit_rules.py`によるcopy sync）
# - `~/.claude/plugins/cache/<owner>-<repo>/agent-toolkit/`
#   （Claude Codeのプラグインマーケットプレイス経由の配布展開先）
#
# `.claude/skills/agent-toolkit*/`および`.chezmoi-source/dot_claude/`配下への
# agent-toolkit経由の配布経路は存在しないため本定数の対象に含めない。
# `AGENTS.md`・`CLAUDE.md`のbasename一致（`_plan_format.AGENT_DOC_TARGET_BASENAMES`）は
# プロジェクトごとの文書へ波及するため本checkの対象からは除外する
# （本checkはagent-toolkit本体への連続直接編集の抑止を目的とし、
# プロジェクトごとの`AGENTS.md`・`CLAUDE.md`編集は本目的の対象外）。
_DIRECT_AGENT_TOOLKIT_DISTRIBUTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `agent-toolkit/rules/` → `~/.claude/rules/agent-toolkit/`
    # （`pytools/_internal/sync_agent_toolkit_rules.py`によるcopy sync）
    re.compile(r"(^|/)\.claude/rules/agent-toolkit/.+\.md$"),
    # `~/.claude/plugins/cache/<owner>-<repo>/agent-toolkit/`
    # （Claude Codeのプラグインマーケットプレイス経由の配布展開先）
    re.compile(r"(^|/)\.claude/plugins/cache/[^/]+/agent-toolkit/.+\.md$"),
)

# `_is_direct_agent_toolkit_edit_target`専用の除外パターン。
# `_plan_format.AGENT_DOC_TARGET_PATTERNS`はプロジェクト直下の`.claude/rules/`・`.claude/skills/`配下も
# コーディングエージェント向け文書として判定するが、本checkはagent-toolkit本体への連続直接編集の抑止を
# 目的とするため、プロジェクトごとの規範文書は対象から外す。
# `.claude/rules/agent-toolkit/`はagent-toolkitの配布先であるため、配布経路側で引き続き対象とする。
_PROJECT_LOCAL_AGENT_DOC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)\.claude/rules/(?!agent-toolkit/).+\.md$"),
    re.compile(r"(^|/)\.claude/skills/.+\.md$"),
)


def _is_direct_agent_toolkit_edit_target(file_path: str) -> bool:
    """`_check_direct_agent_toolkit_edits_after_plan_mode`の対象パス判定。

    原本パスは`_plan_format.is_agent_doc_target_file`のSSOTを再利用して判定する
    （`agent-toolkit/rules/`・`agent-toolkit/skills/.../SKILL.md`・
    `agent-toolkit/skills/.../references/`・`agent-toolkit/agents/`・
    `.chezmoi-source/dot_claude/rules/`を含む）。
    加えて、実在する配布経路（`~/.claude/rules/agent-toolkit/`・
    `~/.claude/plugins/cache/*/agent-toolkit/`）を
    `_DIRECT_AGENT_TOOLKIT_DISTRIBUTION_PATTERNS`で追加照合する。
    `AGENTS.md`・`CLAUDE.md`のbasename一致とプロジェクト直下の`.claude/rules/`・
    `.claude/skills/`配下は、プロジェクトごとの文書へ波及するため本checkの対象外とする。
    """
    if not isinstance(file_path, str) or not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    # basename一致（AGENTS.md/CLAUDE.md）はプロジェクト文書波及のため除外する。
    if pathlib.Path(normalized).name in _plan_format.AGENT_DOC_TARGET_BASENAMES:
        return False
    # プロジェクト直下の`.claude/rules/`・`.claude/skills/`配下も同じ理由で除外する。
    if any(pat.search(normalized) for pat in _PROJECT_LOCAL_AGENT_DOC_PATTERNS):
        return False
    if _plan_format.is_agent_doc_target_file(file_path):
        return True
    return any(pat.search(normalized) for pat in _DIRECT_AGENT_TOOLKIT_DISTRIBUTION_PATTERNS)


def _check_direct_agent_toolkit_edits_after_plan_mode(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> tuple[bool, str | None]:
    """plan-modeスキル起動後、計画ファイル未作成のまま`agent-toolkit`配下の直接編集連続を検知する。

    判定条件:

    - `session_id`が空でない
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - セッション状態の`plan_mode_skill_invoked`が真
    - セッション状態の`plan_file_written`が偽

    連続判定は`last_agent_toolkit_edit_path`と対象パスを比較し、
    直前と異なるパスのときのみ`direct_agent_toolkit_edit_count`をincrementする。
    計画作業root（`~/.claude/plans/`）または保存済み計画root（`$(atk config get private_notes)/plans/`）の
    計画ファイル（メイン）・計画ファイル（詳細）・計画ファイル（バグ）へのWrite/Edit時は
    `plan_file_written`を真にしてカウンタをリセットする。
    対象外パスへの編集時もカウンタをリセットする。
    カウンタ2件目でwarn（`additionalContext`へ載せる通知本文を返して進行を継続）、
    3件目以上でblock（stderr出力＋第1要素にTrueを返してツール呼び出しを中断）する。
    block時は`direct_agent_toolkit_edit_count`と`last_agent_toolkit_edit_path`を更新しない。
    block後にコーディングエージェントが同一パスを再試行した場合、
    直前パス一致条件によるカウンタ加算スキップで素通りする回避を防ぐため、
    カウンタは加算直前の値のまま保持し、再試行時に再度加算されblockが継続する。

    Returns:
        （block判定, 通知本文またはNone）のタプル。
    """
    if not session_id:
        return False, None
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False, None
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not file_path_raw:
        return False, None
    state = read_state(session_id)
    if not state.get("plan_mode_skill_invoked", False):
        return False, None

    # 各計画ファイルの編集時は`plan_file_written`を真にしカウンタをリセットする。
    if _is_plan_file_or_adjunct(file_path_raw):

        def _mark_plan_written(current: dict) -> dict | None:
            changed = False
            if not current.get("plan_file_written", False):
                current["plan_file_written"] = True
                changed = True
            if current.get("direct_agent_toolkit_edit_count", 0) != 0:
                current["direct_agent_toolkit_edit_count"] = 0
                changed = True
            if current.get("last_agent_toolkit_edit_path") is not None:
                current["last_agent_toolkit_edit_path"] = None
                changed = True
            return current if changed else None

        update_state(session_id, _mark_plan_written)
        return False, None

    # 計画ファイルが既に作成済みの場合は本checkの対象外。
    if state.get("plan_file_written", False):
        return False, None

    # 対象外パスへの編集ならカウンタをリセットして通過。
    if not _is_direct_agent_toolkit_edit_target(file_path_raw):

        def _reset_counter(current: dict) -> dict | None:
            if current.get("direct_agent_toolkit_edit_count", 0) == 0 and current.get("last_agent_toolkit_edit_path") is None:
                return None
            current["direct_agent_toolkit_edit_count"] = 0
            current["last_agent_toolkit_edit_path"] = None
            return current

        update_state(session_id, _reset_counter)
        return False, None

    # 直前と同一パスの場合はincrementしない（連続判定は異なるファイルに対する編集を対象とする）。
    last_path = state.get("last_agent_toolkit_edit_path")
    if isinstance(last_path, str) and last_path == file_path_raw:
        return False, None

    # 並列edit時のlost update回避のため、都度ロック内で加算する。
    # `_mark_plan_written`・`_reset_counter`と同様、`update_state`のmutator内で
    # 現在値を再取得してから+1する。呼び出し元へは結果値を`captured`辞書経由で返す。
    captured: dict[str, int] = {"count": 0}

    def _increment(current: dict) -> dict | None:
        count = int(current.get("direct_agent_toolkit_edit_count", 0) or 0) + 1
        captured["count"] = count
        if count >= 3:
            # block時はstate更新をスキップする。
            # 直前パスとカウンタを更新してしまうと、コーディングエージェントが
            # 同一パスを再試行した際に「直前と同一パス」条件で
            # `_increment`到達前にreturn Falseとなりblockが素通りする。
            # 更新をスキップすることで再試行時も再度3件目としてblockが継続する。
            return None
        current["direct_agent_toolkit_edit_count"] = count
        current["last_agent_toolkit_edit_path"] = file_path_raw
        return current

    update_state(session_id, _increment)
    new_count = captured["count"]

    if new_count >= 3:
        print(
            _block_notice(
                f"blocked: `plan-mode`スキルの起動後、計画ファイルを作成しないままagent-toolkit配下を対象とする"
                f"`Write`・`Edit`・`MultiEdit`を{new_count}回連続で実行した。",
                fix="agent-toolkit配下のファイルを編集する前に`~/.claude/plans/`配下へ計画ファイルを作成する。",
            ),
            file=sys.stderr,
        )
        return True, None
    if new_count == 2:
        return False, _llm_notice(
            f"warn: `plan-mode`スキルの起動後、計画ファイルを作成しないままagent-toolkit配下を対象とする"
            f"`Write`・`Edit`・`MultiEdit`を{new_count}回連続で実行した。次の同種の編集は遮断する。"
            "先に`~/.claude/plans/`配下へ計画ファイルを作成する。",
            tag=_WARN_TAG,
            removable_cause=True,
        )
    return False, None


def _apply_edits_to_content(tool_name: str, tool_input: dict, existing: str) -> str | None:
    """Edit又はMultiEditを既存内容へ適用した文字列を返す。"""
    if tool_name == "Edit":
        old_string = tool_input.get("old_string")
        new_string = tool_input.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return None
        if not existing:
            return new_string
        if bool(tool_input.get("replace_all")):
            return existing.replace(old_string, new_string)
        return existing.replace(old_string, new_string, 1)

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return None
        result = existing
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            old_string = edit.get("old_string")
            new_string = edit.get("new_string")
            if not isinstance(old_string, str) or not isinstance(new_string, str):
                continue
            if bool(edit.get("replace_all")):
                result = result.replace(old_string, new_string)
            else:
                result = result.replace(old_string, new_string, 1)
        return result

    return None
