#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code plugin agent-toolkit: PermissionRequestフック。

PreToolUseの`permissionDecision: "allow"`は組み込みのaskルール
（`.claude/`配下の編集確認ダイアログ等）を上書きできないため、
確認ダイアログ表示時に発火するPermissionRequestフックで自動許可を返す。

自動許可の対象パス:

- `~/.claude/plans/`配下
- scratchpadディレクトリ配下（パス構成要素として`scratchpad`を含み、`/tmp/`または`~/`配下）
- `/tmp/`配下（一時ファイル領域として自動許可対象に含める）
- Gitワークツリー配下のコーディングエージェント向け文書（`~/.claude/`配下を除く）
  - パス構成要素に`.claude`または`.agents`を含むファイル
  - ファイル名が`AGENTS.md`のファイル

各判定ロジックの詳細は対応する関数のdocstringを参照する。
予期せぬ例外は0にフォールバックする（フックが破損して編集できなくなる事故を避けるため）。

Bashコマンド判定の設計方針:

- 字句解析は`shlex.shlex(command, posix=True, punctuation_chars=True)`を使い、
  `whitespace`から改行を除外して行う。空白の有無によらず`&&`・`||`・`;`・単独`&`・
  単独`|`を独立トークンへ分割でき、引用符内の文字列は連結されたまま扱われる
- `&&`・`||`・`;`・改行はサブコマンドの区切りとして扱い、各サブコマンドを
  個別に判定してすべてが許可条件を満たす場合にのみ全体を許可する
- 末尾区切りや連続区切りで生じる空サブコマンドは無視し、区切りだけの入力は拒否する
- 単独`&`・単独`|`・`|&`・`>&`・`&>`等の複合演算子トークンも拒否する。
  `&&`と`||`のみを許容する例外とする
- 単独`&`はバックグラウンド実行指示、単独`|`はパイプ、`|&`はstdout+stderrパイプを表す
- `>&`・`&>`はfd番号を伴わない場合、stdout+stderr結合リダイレクトの糖衣構文で同義に解釈される。
  `2>&1`のようにfd番号が前置される場合はfd複製として扱われる
- 読み取り系コマンド（`wc`等）はホワイトリスト方式で扱う。
  `_BASH_READ_OPS`に列挙したコマンドのみ対象とし、
  `_READ_OP_ALLOWED_OPTS`に列挙したbool系オプション以外の指定は拒否する
  （値がパスとなり得るオプションの誤許可を避けるため）
- `echo`は文字列出力だけのサブコマンドとして許可する。リダイレクトを伴う場合は
  リダイレクト先のパス検査を行う
"""

import json
import pathlib
import shlex
import sys
import traceback

# Git ワークツリー判定で親ディレクトリを遡る際の上限段数。
# 病的に深いパスでの暴走を防ぐガード。
_GIT_WORKTREE_LOOKUP_DEPTH = 64

_FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})

# Bash サブコマンドの区切りトークン。
_BASH_COMMAND_SEPARATORS = frozenset({"&&", "||", ";", "\n"})

# Bash 自動許可の対象コマンド（引数がすべて対象パス配下なら許可）。
# 危険性が高い `dd` / `tar` / `rsync` 等は対象外。
_BASH_FILE_OPS = frozenset({"rm", "mkdir", "mv", "cp", "touch", "ln", "chmod", "chown", "cd"})

# Bash 自動許可の対象となる文字列出力コマンド。
_BASH_ECHO_OPS = frozenset({"echo"})

# 安全と判断できないシェルメタ文字。`>` `>>` のリダイレクトと `;` のサブコマンド区切りは
# 別途トークンレベルで扱う。
# `&` は論理AND結合 `&&` を、`|` は論理OR結合 `||` を許容するため除外する。
# 単独の `&`・`|`（バックグラウンド実行・パイプ）はトークンレベルで別途拒否する。
_UNSAFE_METACHARS = frozenset("`$()<")

# Bash 自動許可の対象となる読み取り系コマンド（ホワイトリスト方式）。
# `_READ_OP_ALLOWED_OPTS` に列挙したオプション以外を指定した場合は拒否する。
_BASH_READ_OPS = frozenset({"wc"})

# 読み取り系コマンドごとに許容するオプション（bool系のみ）。
# 値がパスとなり得るオプション（`wc --files0-from=` 等）は含めない。
_READ_OP_ALLOWED_OPTS: dict[str, frozenset[str]] = {
    "wc": frozenset(
        {
            "-l",
            "-c",
            "-w",
            "-m",
            "-L",
            "--lines",
            "--words",
            "--chars",
            "--bytes",
            "--max-line-length",
        }
    ),
}

# `/tmp` 全体を自動許可対象へ含めるためのルートパス文字列。
# テストの `home`・`repo` fixture は `/tmp` 配下へ配置されるため、テスト側で本定数を
# 一時的に差し替えて `/tmp` 全許可判定を無効化する運用を想定する。
_TMP_ROOT_STR = "/tmp"

# パス構成要素として一致した場合に Git ワークツリー判定対象とするディレクトリ名。
_AGENT_META_DIRS = frozenset({".claude", ".agents"})

# ファイル名として一致した場合に Git ワークツリー判定対象とするファイル名。
_AGENT_META_FILES = frozenset({"AGENTS.md"})


def main() -> int:
    """エントリポイント。exit code 0 を返す。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name in _FILE_TOOLS:
        file_path_raw = tool_input.get("file_path")
        file_path = file_path_raw if isinstance(file_path_raw, str) else ""
        allowed = should_allow(file_path)
    elif tool_name == "Bash":
        command_raw = tool_input.get("command")
        command = command_raw if isinstance(command_raw, str) else ""
        cwd_raw = payload.get("cwd")
        cwd = cwd_raw if isinstance(cwd_raw, str) else ""
        allowed = should_allow_bash(command, cwd)
    else:
        return 0

    if not allowed:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow"},
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


def should_allow(file_path: str) -> bool:
    """単一ファイルパスが自動許可対象か判定する。

    対象は次のいずれか。

    - `~/.claude/plans/` 配下
    - scratchpad ディレクトリ配下（パス構成要素として `scratchpad` を含み、 `/tmp/` または `~/` 配下）
    - `/tmp/` 配下（一時ファイル領域として自動許可対象に含める）
    - Git ワークツリー配下のコーディングエージェント向け文書。
      パス構成要素に `.claude` か `.agents` を含むファイル、またはファイル名が `AGENTS.md` のファイル。
      `~/.claude/` 配下は除く
    """
    target = _normalize_path(file_path)
    if target is None:
        return False
    return _is_target_path(target)


def should_allow_bash(command: str, cwd: str) -> bool:
    """Bash コマンドの操作対象パスがすべて自動許可対象配下なら True を返す。

    安全に解析できないコマンド (危険メタ文字含む / shlex 失敗 / 対象外コマンド) は False。
    `&&` / `||` で結合された複数サブコマンドは、すべてのサブコマンドが
    個別に許可条件を満たす場合にのみ全体を許可する。
    単独 `&`（バックグラウンド実行）・単独 `|`（パイプ）は拒否する。
    """
    if not command:
        return False
    tokens = _tokenize(command)
    if not tokens:
        return False
    # 単独 `&`・単独 `|`・`|&`・`>&`・`&>` 等の複合演算子トークンも拒否する。
    # `&&` と `||` のみを許容する例外とする。
    # 各演算子の意味論はモジュール冒頭docstringの「Bashコマンド判定の設計方針」節を参照する。
    if any(token not in ("&&", "||") and ("&" in token or "|" in token) for token in tokens):
        return False

    cwd_base = _resolve_cwd(cwd)
    subcommands = _split_by_logical_ops(tokens)
    return bool(subcommands) and all(_evaluate_subcommand(subcommand, cwd_base) for subcommand in subcommands)


def _tokenize(command: str) -> list[str] | None:
    """`command` を punctuation_chars 対応の shlex でトークン化する（失敗時は None）。

    `posix=True` かつ `punctuation_chars=True` の `shlex.shlex` で、空白の有無によらず
    `&&` / `||` / `;` / 単独 `&` / 単独 `|` を独立トークンへ分割する。
    `whitespace` から改行を除外し、改行も独立トークンとして扱う。空白なしの
    リダイレクト `>` / `>>` も同様に独立トークン化される。引用符内の文字列は
    連結されたまま扱われる。
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace = lexer.whitespace.replace("\n", "")
        return list(lexer)
    except ValueError:
        return None


def _split_by_logical_ops(tokens: list[str]) -> list[list[str]]:
    """サブコマンド区切りトークンでトークン列を分割する。"""
    subcommands: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _BASH_COMMAND_SEPARATORS:
            if current:
                subcommands.append(current)
            current = []
            continue
        current.append(token)
    if current:
        subcommands.append(current)
    return subcommands


def _evaluate_subcommand(tokens: list[str], cwd_base: pathlib.Path | None) -> bool:
    """単一サブコマンドの操作対象パスがすべて自動許可対象配下か判定する。"""
    if not tokens:
        return False
    if tokens[0] not in _BASH_ECHO_OPS and any(ch in _UNSAFE_METACHARS for token in tokens for ch in token):
        return False

    redirect_targets, remaining = _split_redirects(tokens)
    if remaining is None:
        return False  # 不正なリダイレクト構文のため拒否する

    paths: list[str] = list(redirect_targets)
    cmd = ""
    if remaining:
        cmd = remaining[0]
        if cmd in _BASH_ECHO_OPS:
            if not redirect_targets:
                return True
        elif cmd == "cd":
            return _evaluate_cd_subcommand(redirect_targets, remaining[1:], cwd_base)
        elif cmd in _BASH_FILE_OPS:
            paths.extend(_extract_path_args(remaining[1:]))
        elif cmd in _BASH_READ_OPS:
            extracted = _extract_read_op_paths(cmd, remaining[1:])
            if extracted is None:
                return False  # 許容外オプション指定のため拒否する
            paths.extend(extracted)
        elif not redirect_targets:
            # ファイル操作系・読み取り系でもリダイレクトでもないため拒否する。
            return False
        # else: リダイレクトのみで判定する（コマンド本体は問わない）

    if not paths:
        return False

    for path_arg in paths:
        target = _normalize_path(path_arg, cwd_base=cwd_base)
        if target is None or not (_is_target_path(target) or (cmd == "mkdir" and _is_home_claude_plans_root(target))):
            return False
    return True


def _evaluate_cd_subcommand(redirect_targets: list[str], args: list[str], cwd_base: pathlib.Path | None) -> bool:
    """`cd` サブコマンドの移動先とリダイレクト先を判定する。"""
    for path_arg in redirect_targets:
        target = _normalize_path(path_arg, cwd_base=cwd_base)
        if target is None or not _is_target_path(target):
            return False

    cd_paths = _extract_path_args(args)
    if not cd_paths:
        return bool(redirect_targets)
    for path_arg in cd_paths:
        target = _normalize_path(path_arg, cwd_base=cwd_base)
        if target is None or not (_is_target_path(target) or _is_home_claude_plans_root(target)):
            return False
    return True


def _normalize_path(file_path: str, *, cwd_base: pathlib.Path | None = None) -> pathlib.Path | None:
    """パス文字列を正規化された絶対パスへ変換する（失敗時は None）。

    相対パスは `cwd_base` 起点で絶対パスへ解決する。`cwd_base` が None の場合は
    相対パスを拒否する。`~` は expanduser で展開する。
    """
    if not file_path:
        return None
    try:
        target = pathlib.Path(file_path).expanduser()
        if not target.is_absolute():
            if cwd_base is None:
                return None
            target = cwd_base / target
        # `.` / `..`・シンボリックリンクを解消して字句比較の迂回を防ぐ。
        # strict=False で存在しないパスでも例外を送出しない。
        return target.resolve(strict=False)
    except (ValueError, OSError):
        return None


def _resolve_cwd(cwd: str) -> pathlib.Path | None:
    """`cwd` 文字列を絶対パスへ正規化する（空 / 不正なら None）。"""
    if not cwd:
        return None
    try:
        path = pathlib.Path(cwd).expanduser()
        if not path.is_absolute():
            return None
        return path.resolve(strict=False)
    except (ValueError, OSError):
        return None


def _is_target_path(target: pathlib.Path) -> bool:
    """正規化済みパスが自動許可対象配下か判定する。"""
    try:
        home_claude = (pathlib.Path.home() / ".claude").resolve(strict=False)
        tmp_root = pathlib.Path(_TMP_ROOT_STR).resolve(strict=False)
    except (ValueError, OSError):
        return False
    if _is_under(target, home_claude / "plans"):
        return True
    if _is_scratchpad_path(target):
        return True
    if _is_under(target, tmp_root):
        return True
    return _is_repo_agent_meta_edit(target, home_claude)


def _is_home_claude_plans_root(target: pathlib.Path) -> bool:
    """`target` が `~/.claude/plans` ディレクトリそのものか判定する。"""
    try:
        return target == (pathlib.Path.home() / ".claude" / "plans").resolve(strict=False)
    except (ValueError, OSError):
        return False


def _is_under(target: pathlib.Path, base: pathlib.Path) -> bool:
    """`target` が `base` 配下（`base` 自身は含まない）か判定する。"""
    try:
        rel = target.relative_to(base)
    except ValueError:
        return False
    return bool(rel.parts)


def _is_scratchpad_path(target: pathlib.Path) -> bool:
    """パス構成要素として `scratchpad` を含み、かつ `/tmp/` または home 配下か判定する。

    「パス構成要素として含む」とは `target.parts` 内に `"scratchpad"` が要素として
    現れることを指す。ファイル名の一部（`scratchpad-notes.md` 等）は対象外とする。

    ここでの `/tmp` は scratchpad 判定の対象範囲を限定する境界条件として用いる
    リテラル指定であり、`/tmp` 全許可判定用の `_TMP_ROOT_STR`（テストで差し替え可能）
    とは別概念として意図的に分離する。
    """
    if "scratchpad" not in target.parts:
        return False
    try:
        tmp_root = pathlib.Path("/tmp").resolve(strict=False)
        home_root = pathlib.Path.home().resolve(strict=False)
    except (ValueError, OSError, RuntimeError):
        return False
    return _is_under(target, tmp_root) or _is_under(target, home_root)


def _is_repo_agent_meta_edit(target: pathlib.Path, home_claude: pathlib.Path) -> bool:
    """Git ワークツリー配下のコーディングエージェント向け文書への編集か判定する。

    以下のいずれかに該当するパスを対象とする。

    - パス構成要素に `.claude` または `.agents` を含むファイル
    - ファイル名が `AGENTS.md` のファイル

    `~/.claude/` 配下は除外する（配布先誤編集の警告経路を維持するため）。
    """
    parts = set(target.parts)
    if _AGENT_META_DIRS.isdisjoint(parts) and target.name not in _AGENT_META_FILES:
        return False
    try:
        target.relative_to(home_claude)
        return False
    except ValueError:
        pass
    return _is_inside_git_worktree(target)


def _is_inside_git_worktree(target: pathlib.Path) -> bool:
    """対象パスの親を遡って `.git` の存在を確認する。

    `.git` はディレクトリ（通常のリポジトリ）またはファイル（worktree / submodule）の
    いずれもありうるため `exists()` で判定する。subprocess は使わずファイルシステム
    存在確認のみで完結させる（PermissionRequest が編集毎に実行されるため軽量化が必要）。
    """
    current = target.parent
    for _ in range(_GIT_WORKTREE_LOOKUP_DEPTH):
        if (current / ".git").exists():
            return True
        if current.parent == current:
            return False
        current = current.parent
    return False


def _split_redirects(tokens: list[str]) -> tuple[list[str], list[str] | None]:
    """`>` / `>>` トークンを抽出してリダイレクト先パスと残りのトークンに分ける。

    リダイレクト直後にトークンがない不正な構文の場合は `([], None)` を返す。
    """
    redirects: list[str] = []
    remaining: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in (">", ">>"):
            if i + 1 >= len(tokens):
                return [], None
            redirects.append(tokens[i + 1])
            i += 2
            continue
        remaining.append(token)
        i += 1
    return redirects, remaining


def _extract_path_args(args: list[str]) -> list[str]:
    """`-` で始まらない引数のみをパスとして抽出する。

    `--` をオプション終端マーカーとして扱う（それ以降は `-` 始まりもすべてパス扱い）。
    """
    paths: list[str] = []
    after_double_dash = False
    for arg in args:
        if not after_double_dash:
            if arg == "--":
                after_double_dash = True
                continue
            if arg.startswith("-"):
                continue
        paths.append(arg)
    return paths


def _extract_read_op_paths(cmd: str, args: list[str]) -> list[str] | None:
    """読み取り系コマンド `cmd` の引数からパスのみを抽出する（許容外オプションは None）。

    `--` をオプション終端マーカーとして扱う（`_extract_path_args` と同じ挙動）。
    `-` で始まる引数は `_READ_OP_ALLOWED_OPTS[cmd]` に含まれる場合のみスキップし、
    含まれない場合は誤許可を避けるため None を返す。`--long=VALUE` 形式は `=` の前で照合する。
    """
    allowed = _READ_OP_ALLOWED_OPTS.get(cmd, frozenset())
    paths: list[str] = []
    after_double_dash = False
    for arg in args:
        if not after_double_dash:
            if arg == "--":
                after_double_dash = True
                continue
            if arg.startswith("-"):
                bare = arg.split("=", 1)[0]
                if bare in allowed:
                    continue
                return None
        paths.append(arg)
    return paths


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        traceback.print_exc()
        sys.exit(0)
