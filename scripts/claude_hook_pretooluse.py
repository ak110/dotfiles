#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code PreToolUse フック: dotfiles 個人環境専用チェック集。

mojibake (U+FFFD) / PowerShell LF-only 書き込みのチェックは Claude Code plugin
`agent-toolkit` へ移管した (`agent-toolkit/scripts/pretooluse.py`)。
本スクリプトは dotfiles 個人環境でのみ必要な、汎用性の低いチェックをまとめる
(他人に配布する `agent-toolkit` には含めにくい個人環境前提のチェック群)。

統合しているチェック:

1. `~/.claude/` 配下への直接編集警告 (warn, 非ブロック)
   - chezmoi の配布先の場合、次回 `chezmoi apply` で上書きされるため
     配布元 (`.chezmoi-source/dot_claude/`) を編集すべき。
   - ただし `settings.json` のように Claude Code 自身が書き換える非 chezmoi 管理
     ファイルもあり、機械的にブロックすると誤判定で作業を阻害する。
     最終判断は LLM に委ねるため、ブロックせず警告のみ表示する。
   - 警告を表示しない対象:
     - サブツリー: `plans/` / `projects/` / `todos/` /
       `shell-snapshots/` / `ide/` / `statsig/` (Claude Code のランタイム領域)
     - ファイル名: `settings.json` (Claude Code 自身が書き換える非 chezmoi 管理設定)
     - 名前に `.local.` を含むファイル (個人ローカル設定)
2. PowerShell スクリプトの必須ディレクティブ欠落ブロック (block, Write のみ)
   - `.ps1` / `.ps1.tmpl` の冒頭付近 (先頭 50 行以内) に
     `Set-StrictMode -Version Latest` と `$ErrorActionPreference = 'Stop'` の両方が必須。
   - CLAUDE.md の Windows PowerShell スクリプト規約を強制する。
   - Edit / MultiEdit の `new_string` はファイル先頭を含まないことが多いため対象外。
   - LF/CRLF の改行チェックは `agent-toolkit` プラグインが担当する。
3. 個人用 / ローカル専用ファイル言及検出 (warn / 非ブロック)
   - 対象パターン:
     - `CLAUDE.local.md` (リポジトリ管理外のローカルメモ)
     - ファイル名に `___` (アンダースコア3つ) を含むトークン (個人メモの慣習)
   - 他のリポジトリ管理ファイルから参照すると、無視指定などを通じてファイル名自体が
     コミットに漏れる原因になる。一方で配布対象ドキュメントで利用者に同名ファイル作成を
     推奨する文脈など、正当な言及もある。文脈依存のため最終判断は LLM に委ね、
     hook は緩い警告のみを表示してブロックはしない
   - `file_path` 自体が対象パターンに一致するファイルの場合は、
     ファイル自身の作成・編集として警告もスキップする
4. agent-toolkit 配布物への dotfiles 固有名混入検出 (block + warn)
   - 対象範囲: `agent-toolkit/` および `.chezmoi-source/dot_claude/rules/agent-toolkit/` 配下
   - block 対象: 個人スキル名 (`~/dotfiles/.chezmoi-source/dot_claude/skills/` 配下) ・
     dotfiles スキル名 (`~/dotfiles/.claude/skills/` 配下) ・
     `pyproject.toml` の `[project.scripts]` キー名・
     `pytools/` 直下のモジュール名・`scripts/` 直下のモジュール名・
     固定の個人プロジェクト名 (`glatasks` / `gv` / `lc` / `smpr`)
   - warn 対象: OSS として正規参照されうる個人プロジェクト名 (`pyfltr` / `pytilpack`)
   - block 対象は配布先の利用者にとって意味不明な参照となるため exit 2 で停止する。
     warn 対象は誤参照が多いため通知するが許容する
   - 動的取得側は対象ディレクトリ未存在時に空集合扱いで安全側に倒す

検査対象は「新規に書き込まれる側」 (`content` / `new_string`) のみ。
`old_string` は既存内容の修正・削除を妨げないため検査しない。

出力契約:

- block: exit 2 + stderr にブロック理由を出力
- warn: exit 0 + stdout に JSON (`hookSpecificOutput`) を出力
  - `permissionDecision: "allow"` と `additionalContext` (警告メッセージ) を付与
  - 組み込みの ask ルール (`.claude/` 配下の確認ダイアログ等) は本フックの allow では
    上書きできないため、確認ダイアログの抑制が必要な経路は PermissionRequest フック
    (`scripts/claude_hook_permissionrequest.py`) で別途処理する
- 通過 (違反なし / スキップ対象ツール / 想定外入力): exit 0、出力なし

メッセージは英語で記述する (ユーザーの日本語思考コンテキストへのノイズ混入を避けるため)。
予期せぬ例外は 0 にフォールバックする (フックが破損して編集できなくなる事故を避けるため)。
"""

import json
import pathlib
import re
import sys
import tomllib
import traceback

# agent-toolkit のメッセージ整形ヘルパーを sys.path 経由で再利用する。
# plugin が無効化されていても dotfiles リポジトリ上にファイルが存在し続けるため import は成立する。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# このスクリプトの hook 識別子。プレフィックス `[auto-generated: dotfiles/claude_hook_pretooluse]` に展開される。
_HOOK_ID = "dotfiles/claude_hook_pretooluse"

_CLAUDE_LOCAL_MD = "CLAUDE.local.md"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM 宛てメッセージを標準プレフィックス / サフィックス付きで整形する。

    `tag` に `warn` 等を渡すとプレフィックスに並置する (`[auto-generated: ...][warn]`)。
    """
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def _main() -> int:
    """エントリポイント。exit code を返す (0 または 2)。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        # 想定外入力ではフックを無効化 (実処理の破損を避ける安全側の判定)
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    # Write/Edit/MultiEdit 以外は全スキップ
    fields = _collect_new_fields(tool_name, tool_input)
    if fields is None:
        return 0

    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""

    # --- block 系 check (最初の違反で exit 2) ---
    if _check_ps1_directives(tool_name, fields, file_path):
        return 2
    dotfiles_block, dotfiles_warn = _check_dotfiles_specific_names(tool_name, fields, file_path)
    if dotfiles_block is not None:
        print(_llm_notice(dotfiles_block), file=sys.stderr)
        return 2

    # --- warn 系 check ---
    warnings: list[str] = []
    home_claude_warning = _home_claude_edit_warning(tool_name, file_path)
    if home_claude_warning is not None:
        warnings.append(home_claude_warning)
    personal_warning = _personal_file_mentions_warning(tool_name, fields, file_path)
    if personal_warning is not None:
        warnings.append(personal_warning)
    if dotfiles_warn is not None:
        warnings.append(dotfiles_warn)

    if warnings:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "additionalContext": _llm_notice(" | ".join(warnings), tag="warn"),
                    }
                },
                ensure_ascii=False,
            )
        )

    return 0


def _collect_new_fields(tool_name: str, tool_input: dict) -> list[tuple[str, str]] | None:
    """対象ツールの「新規書き込みフィールド」を (field 名, 値) のリストで返す。

    対象外ツールの場合は None を返す。値が文字列でないものはスキップする。
    """
    if tool_name == "Write":
        value = tool_input.get("content")
        return [("content", value)] if isinstance(value, str) else []
    if tool_name == "Edit":
        value = tool_input.get("new_string")
        return [("new_string", value)] if isinstance(value, str) else []
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return []
        result: list[tuple[str, str]] = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                continue
            new_string = edit.get("new_string")
            if isinstance(new_string, str):
                result.append((f"edits[{index}].new_string", new_string))
        return result
    return None


# --- PowerShell 必須ディレクティブ check (block) ---

# 冒頭付近に必須のディレクティブ。両方が揃わなければブロックする。
# 行頭厳格マッチ (インデント不可) にすることで「コメント内に文字列が含まれるだけ」や
# 「関数/条件ブロック内に書かれている (= スクリプト全体には適用されない)」ケースをブロックする。
_PS1_REQUIRED_DIRECTIVES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^Set-StrictMode\s+-Version\s+Latest\b", re.MULTILINE),
        "Set-StrictMode -Version Latest",
    ),
    (
        re.compile(r"^\$ErrorActionPreference\s*=\s*'Stop'", re.MULTILINE),
        "$ErrorActionPreference = 'Stop'",
    ),
)

# 検査する先頭行数 (コメントブロックを許容するため広めに取る)。
_PS1_DIRECTIVES_HEAD_LINES = 50


def _is_ps1(file_path: str) -> bool:
    """対象拡張子か判定する (`.ps1` / `.ps1.tmpl`)。"""
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


def _check_ps1_directives(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """PowerShell スクリプトの冒頭ディレクティブ欠落を検出したら True を返す。

    Edit / MultiEdit の `new_string` はファイル先頭を含まないことが多いため Write のみを対象とする。
    LF/CRLF 改行のチェックは `agent-toolkit` プラグイン側で実施しているため重複させない。
    """
    if tool_name != "Write" or not _is_ps1(file_path):
        return False
    for field, value in fields:
        # BOM (U+FEFF) は chezmoi テンプレートで使われることがあるため除去してから判定する
        normalized = value.lstrip("﻿")
        head = "\n".join(normalized.splitlines()[:_PS1_DIRECTIVES_HEAD_LINES])
        missing = [label for pattern, label in _PS1_REQUIRED_DIRECTIVES if pattern.search(head) is None]
        if missing:
            print(
                _llm_notice(
                    f"{tool_name}.{field}: missing required PowerShell directives: "
                    f"{', '.join(missing)}. For Windows PowerShell 5.1 compatibility, add "
                    f"`Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'` "
                    f"near the top (within first {_PS1_DIRECTIVES_HEAD_LINES} lines, at line start)."
                    f" Target: {file_path}"
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- ~/.claude/ 配下の直接編集 check (warn) ---

# 警告対象外のサブツリー (Claude Code のランタイム領域 / プラン作業領域)。
# 配布対象 (rules/ や agents/) は含めない。
_HOME_CLAUDE_ALLOWED_DIRS: frozenset[str] = frozenset(
    {
        "plans",  # plan mode が書き込む計画ファイル
        "projects",  # Claude Code のセッション履歴
        "todos",  # TodoWrite ストレージ
        "shell-snapshots",  # シェル スナップショット
        "ide",  # IDE 連携キャッシュ
        "statsig",  # Statsig SDK のキャッシュ
    }
)

# 警告対象外のファイル名 (Claude Code 自身が書き換える非 chezmoi 管理ファイル)。
_HOME_CLAUDE_ALLOWED_NAMES: frozenset[str] = frozenset(
    {
        "settings.json",  # Claude Code ランタイム設定 (autoMode 等を自身が書き換える)
    }
)

# 警告対象外のファイル名部分一致 (`*.local.*` 系 ローカル設定)。
_HOME_CLAUDE_ALLOWED_NAME_SUBSTRING = ".local."


def _home_claude_edit_warning(tool_name: str, file_path: str) -> str | None:
    """`~/.claude/` 配下への直接編集の警告メッセージを返す (該当しなければ None)。

    chezmoi の配布先のため、配布対象ファイルを編集すると次回 `chezmoi apply` で
    上書きされる。配布元 (`.chezmoi-source/dot_claude/`) を編集すべき。
    ただし `settings.json` など非 chezmoi 管理ファイルを機械的に判別するのは
    難しく、誤判定でブロックすると作業が止まるため、警告のみ表示し判断は LLM に委ねる。
    """
    if not file_path:
        return None
    try:
        target = pathlib.Path(file_path).expanduser()
        # 相対パスでは ~/.claude 配下か判定できないためスキップする
        # (resolve すると CWD 基準で解決され誤検出になり得るので resolve 前に判定)
        if not target.is_absolute():
            return None
        # `.` / `..`・シンボリックリンクを解消して字句比較の迂回を防ぐ。
        # strict=False で存在しないパスでも例外を送出しない。
        target = target.resolve(strict=False)
        home_claude = (pathlib.Path.home() / ".claude").resolve(strict=False)
    except (ValueError, OSError):
        return None
    try:
        rel = target.relative_to(home_claude)
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        # `~/.claude` そのもの (実際にはディレクトリ) は対象外
        return None
    if parts[0] in _HOME_CLAUDE_ALLOWED_DIRS:
        return None
    if rel.name in _HOME_CLAUDE_ALLOWED_NAMES:
        return None
    if _HOME_CLAUDE_ALLOWED_NAME_SUBSTRING in rel.name:
        return None
    return (
        f"{tool_name} targets ~/.claude/ ({file_path})."
        " If this file is distributed via chezmoi, edit `.chezmoi-source/dot_claude/` instead"
        " (direct edits will be overwritten on next `chezmoi apply`)."
        " Proceed only if the target is a non-chezmoi runtime/config file."
    )


# --- 個人用 / ローカル専用ファイル言及 check (warn) ---

# ファイル名に連続アンダースコア (3〜7 文字) を含むトークンを検出する。
# 個人用メモの慣習として使われるファイル名パターン。
# 8 文字以上の連続アンダースコアは区切り線等の装飾用途とみなし対象外にする。
# `[^\W_]` (= [a-zA-Z0-9]) でアンダースコア列の前後を非アンダースコアの word 文字に
# 限定し、`(?!_)` で列が 7 文字を超えないことを保証する。
# `\b` でword境界に固定することで、トークンの内側の部分マッチを避ける。
_TRIPLE_UNDERSCORE_PATTERN = re.compile(r"\b\w*[^\W_]_{3,7}(?!_)[^\W_]\w*\b")

# 3〜7 文字の連続アンダースコアを検出するパターン (ファイル名判定用)。
_SHORT_UNDERSCORE_RUN = re.compile(r"(?<!_)_{3,7}(?!_)")


def _is_claude_local_md(file_path: str) -> bool:
    """ファイルパス自体が CLAUDE.local.md かを判定する (言及チェック除外用)。"""
    if not file_path:
        return False
    # パス区切りを正規化してファイル名を取得
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return name == _CLAUDE_LOCAL_MD


def _has_triple_underscore_filename(file_path: str) -> bool:
    """ファイル名自体に 3〜7 文字の連続アンダースコアが含まれるかを判定する (言及チェック除外用)。"""
    if not file_path:
        return False
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return bool(_SHORT_UNDERSCORE_RUN.search(name))


def _personal_file_mentions_warning(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    """個人用 / ローカル専用ファイルの言及の警告メッセージを返す (該当しなければ None)。

    対象は `CLAUDE.local.md` と、ファイル名に `___` を含むトークン。
    対象ファイル自身の編集は作成・更新として警告をスキップする。
    文脈依存の判断は LLM に委ね、hook は緩い警告のみを表示してブロックはしない。
    """
    messages: list[str] = []
    if not _is_claude_local_md(file_path):
        for field, value in fields:
            if _CLAUDE_LOCAL_MD in value:
                messages.append(f"'{_CLAUDE_LOCAL_MD}' in {tool_name}.{field}")
                break
    if not _has_triple_underscore_filename(file_path):
        for field, value in fields:
            match = _TRIPLE_UNDERSCORE_PATTERN.search(value)
            if match:
                messages.append(f"'{match.group()}' (filename-like token containing '___') in {tool_name}.{field}")
                break
    if not messages:
        return None
    return (
        "detected possible mention(s) of local-only personal file(s): "
        + "; ".join(messages)
        + ". Such files are typically gitignored personal memos."
        " Referencing them from version-controlled files is often unintentional"
        " and risks leaking the filename via ignore-lists or stale references."
        " On the other hand, recommending end users to create their own local memo"
        " file (e.g., in distributed docs/skills) is legitimate."
        " Judge the context and keep the mention only if it is intentional."
    )


# --- agent-toolkit 配布物への dotfiles 固有名混入 check (block + warn) ---

# 個人プロジェクト名の固定リスト。
# ファイルシステムから機械的に取得できないため明示的に持つ。
# OSS として紹介する想定がある pyfltr / pytilpack は warn にとどめ、
# それ以外 (個人非公開・特定プラットフォーム専用) は block する。
_PERSONAL_PROJECTS_BLOCK: frozenset[str] = frozenset({"glatasks", "gv", "lc", "smpr"})
_PERSONAL_PROJECTS_WARN: frozenset[str] = frozenset({"pyfltr", "pytilpack"})


def _check_dotfiles_specific_names(
    tool_name: str, fields: list[tuple[str, str]], file_path: str
) -> tuple[str | None, str | None]:
    """agent-toolkit 配布物への dotfiles 固有名混入を検出する。

    対象範囲は `agent-toolkit/` および `.chezmoi-source/dot_claude/rules/agent-toolkit/`。
    block 対象は配布先の利用者にとって意味不明な参照となるため exit 2 で停止する。
    warn 対象 (`pyfltr` / `pytilpack`) は OSS として正規参照される場合があるため通知のみ。

    `(block_message, warn_message)` を返す。該当なしの側は None。
    """
    if not file_path:
        return None, None
    dotfiles_root = pathlib.Path(__file__).resolve().parent.parent
    if not _is_in_agent_toolkit_distribution(file_path, dotfiles_root):
        return None, None
    block_names, warn_names = _build_dotfiles_specific_names(dotfiles_root)
    block_hits = _collect_word_hits(tool_name, fields, block_names)
    warn_hits = _collect_word_hits(tool_name, fields, warn_names)
    block_msg: str | None = None
    if block_hits:
        block_msg = (
            "agent-toolkit distribution must not contain dotfiles-specific identifiers."
            f" Hits: {'; '.join(block_hits)}."
            " Replace with generalized wording (personal skill names, pytools commands, scripts,"
            " and personal project names like glatasks/gv/lc/smpr leak repository internals)."
            f" Target: {file_path}"
        )
    warn_msg: str | None = None
    if warn_hits:
        warn_msg = (
            "agent-toolkit distribution references possibly dotfiles-related projects: "
            + "; ".join(warn_hits)
            + ". These names are personal projects but commonly referenced as OSS."
            " Verify the reference is intentional and accurate."
            f" Target: {file_path}"
        )
    return block_msg, warn_msg


def _is_in_agent_toolkit_distribution(file_path: str, dotfiles_root: pathlib.Path) -> bool:
    """対象ファイルが agent-toolkit 配布範囲のいずれかに含まれるかを判定する。

    相対パスでは判定不能なためスキップする (resolve は CWD 基準で解決され誤検出になり得る)。
    """
    try:
        target = pathlib.Path(file_path).expanduser()
        if not target.is_absolute():
            return False
        target = target.resolve(strict=False)
    except (ValueError, OSError):
        return False
    for base in _agent_toolkit_distribution_roots(dotfiles_root):
        try:
            target.relative_to(base.resolve(strict=False))
            return True
        except (ValueError, OSError):
            continue
    return False


def _agent_toolkit_distribution_roots(dotfiles_root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """マーケットプレイス経由で配布されるディレクトリの一覧を返す。"""
    return (
        dotfiles_root / "agent-toolkit",
        dotfiles_root / ".chezmoi-source" / "dot_claude" / "rules" / "agent-toolkit",
    )


def _build_dotfiles_specific_names(dotfiles_root: pathlib.Path) -> tuple[frozenset[str], frozenset[str]]:
    """Dotfiles 固有名 (block 対象 / warn 対象) を返す。

    block 対象は次の 5 カテゴリの動的取得結果と固定の個人プロジェクト名の和集合。
    各カテゴリは対象ディレクトリ未存在時に空集合扱いで安全側に倒す。
    """
    block: set[str] = set()
    block |= _list_subdirs(dotfiles_root / ".chezmoi-source" / "dot_claude" / "skills")
    block |= _list_subdirs(dotfiles_root / ".claude" / "skills")
    block |= _list_pyproject_scripts(dotfiles_root / "pyproject.toml")
    block |= _list_pytools_modules(dotfiles_root / "pytools")
    block |= _list_scripts_modules(dotfiles_root / "scripts")
    block |= _PERSONAL_PROJECTS_BLOCK
    # warn 対象が誤って block に混入した場合は warn を優先する (保守的措置)
    block -= _PERSONAL_PROJECTS_WARN
    return frozenset(block), _PERSONAL_PROJECTS_WARN


def _list_subdirs(path: pathlib.Path) -> set[str]:
    """ディレクトリ直下のサブディレクトリ名を返す。未存在なら空集合。"""
    if not path.is_dir():
        return set()
    return {child.name for child in path.iterdir() if child.is_dir()}


def _list_pyproject_scripts(path: pathlib.Path) -> set[str]:
    """`pyproject.toml` の `[project.scripts]` キー名を返す。読み込み失敗時は空集合。"""
    if not path.is_file():
        return set()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return set()
    project = data.get("project")
    if not isinstance(project, dict):
        return set()
    scripts = project.get("scripts")
    if not isinstance(scripts, dict):
        return set()
    return {name for name in scripts if isinstance(name, str)}


def _list_pytools_modules(path: pathlib.Path) -> set[str]:
    """`pytools/` 直下のモジュール名 (拡張子除去) を返す。`__init__.py` と `_internal/` は除外。"""
    if not path.is_dir():
        return set()
    return {child.stem for child in path.glob("*.py") if child.name != "__init__.py"}


def _list_scripts_modules(path: pathlib.Path) -> set[str]:
    """`scripts/` 直下のスクリプト名 (拡張子除去) を返す。`*_test.py` は除外。"""
    if not path.is_dir():
        return set()
    names: set[str] = set()
    for child in list(path.glob("*.py")) + list(path.glob("*.sh")):
        if child.suffix == ".py" and child.name.endswith("_test.py"):
            continue
        names.add(child.stem)
    return names


def _collect_word_hits(tool_name: str, fields: list[tuple[str, str]], names: frozenset[str]) -> list[str]:
    """単語境界マッチで各フィールドから検出された名前を `'name' in field` 形式で列挙する。

    同名が複数フィールドで出ても 1 度だけ記録する。
    """
    hits: list[str] = []
    seen: set[str] = set()
    for field, value in fields:
        for name in sorted(names):
            if name in seen:
                continue
            if re.search(rf"\b{re.escape(name)}\b", value):
                hits.append(f"'{name}' in {tool_name}.{field}")
                seen.add(name)
    return hits


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        # 予期せぬ例外は安全側として通過させる。デバッグのためスタックトレースは stderr に表示する
        traceback.print_exc()
        sys.exit(0)
