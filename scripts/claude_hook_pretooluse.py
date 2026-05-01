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
     最終判断は LLM に委ねるため、ブロックせず警告のみ出す。
   - 警告を出さない対象:
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
     hook は緩い警告のみを出してブロックはしない
   - `file_path` 自体が対象パターンに一致するファイルの場合は、
     ファイル自身の作成・編集として警告もスキップする
4. リポジトリ配下 `.claude/` 編集の自動許可 (allow, 非ブロック)
   - 任意のリポジトリ配下の `.claude/` への編集に対し
     `permissionDecision: "allow"` を返し、Claude Code 組み込みの保護機構が
     発動する確認プロンプトを抑制する。
     ルール・スキル整備中の体感負荷を下げるための個人環境専用ガード。
   - 判定条件 (3 つすべてを満たす場合に対象):
     (a) パスのいずれかのコンポーネントが `.claude` である
     (b) `~/.claude/` 配下ではない
         (配布先誤編集の警告経路 (1) を維持するため除外する。
          ただし (1) も `permissionDecision: "allow"` を返すため
          ユーザーから見える挙動は警告メッセージ付きで確認なしのまま変わらない)
     (c) パスの親を順に遡り `.git` (ディレクトリまたはファイル) が見つかる
         (= Git ワークツリー配下である)
   - Git ワークツリー判定は subprocess を使わずファイルシステム存在確認のみで行う
     (PreToolUse は編集毎に走るため軽量化が必要)。

検査対象は「新規に書き込まれる側」 (`content` / `new_string`) のみ。
`old_string` は既存内容の修正・削除を妨げないため検査しない。

出力契約:

- block: exit 2 + stderr にブロック理由を出力
- warn / auto-allow (allow + 任意のメッセージ):
  exit 0 + stdout に JSON (`hookSpecificOutput`) を出力
  - 警告系または自動許可のいずれかが該当する場合に `permissionDecision: "allow"` を付与
  - 警告がある場合のみ `additionalContext` を付与
- 通過 (違反なし / スキップ対象ツール / 想定外入力): exit 0、出力なし

メッセージは英語で記述する (ユーザーの日本語思考コンテキストへのノイズ混入を避けるため)。
予期せぬ例外は 0 にフォールバックする (フックが破損して編集できなくなる事故を避けるため)。
"""

import json
import pathlib
import re
import sys
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

# Git ワークツリー判定で親ディレクトリを遡る際の上限段数。
# 病的に深いパスでの暴走を避けるためのガード。
_GIT_WORKTREE_LOOKUP_DEPTH = 64


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

    # --- warn 系 / 自動許可 系 check ---
    # 警告系と自動許可は判定経路を独立させるが、最終出力は同一 JSON に集約する。
    # いずれかが該当した時点で `permissionDecision: "allow"` を付与する。
    warnings: list[str] = []
    home_claude_warning = _home_claude_edit_warning(tool_name, file_path)
    if home_claude_warning is not None:
        warnings.append(home_claude_warning)
    personal_warning = _personal_file_mentions_warning(tool_name, fields, file_path)
    if personal_warning is not None:
        warnings.append(personal_warning)

    auto_allow = _is_repo_claude_edit(file_path)
    should_allow = bool(warnings) or auto_allow

    if should_allow:
        hook_specific: dict[str, object] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
        if warnings:
            hook_specific["additionalContext"] = _llm_notice(" | ".join(warnings), tag="warn")
        print(
            json.dumps(
                {"hookSpecificOutput": hook_specific},
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
# 「関数/条件ブロック内に書かれている (= スクリプト全体には効かない)」ケースをブロックする。
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
    難しく、誤判定でブロックすると作業が止まるため、警告のみ出し判断は LLM に委ねる。
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
    文脈依存の判断は LLM に委ね、hook は緩い警告のみを出してブロックはしない。
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


# --- リポジトリ配下 `.claude/` 編集の自動許可 check (allow) ---


def _is_repo_claude_edit(file_path: str) -> bool:
    """リポジトリ配下の `.claude/` 配下への編集かを判定する。

    判定条件 (3 つすべてを満たす場合に True):
    1. パスのいずれかのコンポーネントが `.claude` である
    2. `~/.claude/` 配下ではない (配布先誤編集の警告経路を維持するため除外)
    3. パスの親を順に遡って `.git` (ディレクトリまたはファイル) が見つかる
       (= Git ワークツリー配下である)
    """
    if not file_path:
        return False
    try:
        target = pathlib.Path(file_path).expanduser()
        if not target.is_absolute():
            return False
        target = target.resolve(strict=False)
        home_claude = (pathlib.Path.home() / ".claude").resolve(strict=False)
    except (ValueError, OSError):
        return False

    if ".claude" not in target.parts:
        return False

    # ~/.claude 配下は対象外 (既存の警告経路がカバー)
    try:
        target.relative_to(home_claude)
        return False
    except ValueError:
        pass

    return _is_inside_git_worktree(target)


def _is_inside_git_worktree(target: pathlib.Path) -> bool:
    """対象パスの親を遡って `.git` の存在を確認する。

    `.git` はディレクトリ (通常のリポジトリ) またはファイル (worktree / submodule) の
    いずれもありうるため `exists()` で判定する。subprocess は使わずファイルシステム
    存在確認のみで完結させる (PreToolUse が編集毎に走るため軽量化が必要)。
    """
    current = target.parent
    for _ in range(_GIT_WORKTREE_LOOKUP_DEPTH):
        if (current / ".git").exists():
            return True
        if current.parent == current:
            # filesystem root に到達
            return False
        current = current.parent
    return False


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        # 予期せぬ例外は安全側として通過させる。デバッグのためスタックトレースは stderr に出す
        traceback.print_exc()
        sys.exit(0)
