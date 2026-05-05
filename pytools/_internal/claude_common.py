"""Claude Code関連の共通定数・ユーティリティ。"""

import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

from pytools._internal import log_format

logger = logging.getLogger(__name__)

# --- 定数 ---

# Claude Code の設定ディレクトリ
CLAUDE_HOME = Path.home() / ".claude"

# Claude Code のグローバル設定ファイル
CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"

# Claude Code のユーザー設定ファイル
SETTINGS_JSON_PATH = CLAUDE_HOME / "settings.json"

# Claude Code の plan ファイル保存ディレクトリ
PLANS_DIR = CLAUDE_HOME / "plans"

# インストール済みプラグイン情報ファイル (CLI 呼び出しを回避するための直接読み取り用)
INSTALLED_PLUGINS_PATH = CLAUDE_HOME / "plugins" / "installed_plugins.json"

# marketplace.json の `name` と一致させる (.claude-plugin/marketplace.json を参照)
MARKETPLACE_NAME = "ak110-dotfiles"

# `claude plugin` コマンドのタイムアウト (秒)
# GitHub からの初回 clone や install 処理で時間が掛かる場合があるため余裕を持たせる
CLAUDE_TIMEOUT = 30


# --- 関数 ---


def find_dotfiles_root() -> Path | None:
    """Dotfiles ルートディレクトリを返す。

    dotfiles ルートは `.claude-plugin/marketplace.json` を持つ。
    `pytools/_internal/` は dotfiles/pytools/_internal/ に置かれているため
    親の親の親がルート。
    """
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / ".claude-plugin" / "marketplace.json").is_file():
        return candidate
    return None


def format_cli_error(result: subprocess.CompletedProcess[str] | None) -> str:
    """CLI 失敗時のログに付ける stderr/stdout の簡潔な連結文字列を返す。

    Claude Code CLI は本質的なエラーメッセージを stderr 側ではなく stdout に書き込むことがあり、
    stderr のみでは原因特定が難しい。双方を併記してログに載せる。
    """
    if result is None:
        return "(実行に失敗)"
    parts: list[str] = []
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    if stderr:
        parts.append(f"stderr: {stderr}")
    if stdout:
        parts.append(f"stdout: {stdout}")
    return " / ".join(parts) if parts else f"exit {result.returncode}"


def run_subprocess(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cwd: Path | None = None,
    tag: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Subprocess を UTF-8 + `errors="replace"` で実行する共通ラッパー。

    タイムアウト・OSError・SubprocessError を吸収して None を返す。非ゼロ終了は
    そのまま呼び出し元に渡す。`tag` を指定すると失敗時のログラベルに使用する。

    Windows では `text=True` のデフォルトが cp932 になり、CLI の UTF-8 出力で
    UnicodeDecodeError が発生するため、明示的に UTF-8 を指定し、不正バイトが
    混入しても例外が発生しないよう `errors="replace"` を併用する。
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as e:
        label = tag or cmd[0]
        rest = " ".join(cmd[1:])
        logger.info(log_format.format_status(label, f"`{rest}` 実行に失敗: {e}"))
        return None


def run_claude(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str] | None:
    """`claude` CLI を呼び出す共通ヘルパー。

    タイムアウト・例外・非ゼロ終了を全て吸収して呼び出し元に返す。
    `cwd` を指定すると project scope など cwd 依存のサブコマンドに対応できる。
    失敗時の原因追跡を容易にするため、実行コマンドと戻り値をログに残す。
    """
    logger.info(
        log_format.format_status(
            "claude",
            f"exec: {' '.join(args)}" + (f" (cwd={cwd})" if cwd is not None else ""),
        )
    )
    result = run_subprocess(["claude", *args], timeout=CLAUDE_TIMEOUT, cwd=cwd, tag="claude")
    if result is None:
        return None
    logger.info(log_format.format_status("claude", f"exit {result.returncode}: {' '.join(args)}"))
    return result


def load_json_dict(
    path: Path,
    default: dict[str, object] | None = None,
    *,
    tag: str | None = None,
) -> dict[str, object] | None:
    """JSON ファイルをトップレベル dict として読み込む。

    ファイルが存在しない場合は空 dict (または `default`) を返し、新規作成の足場として機能させる。
    JSON 解析失敗・非 dict・I/O エラーは ``None`` を返し、呼び出し元で書き込みを中止させる。
    ``tag`` を指定するとログメッセージのラベルに使用する。``None`` の場合はエラーログを出力しない。
    """
    if default is None:
        default = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as e:
        if tag is not None:
            logger.info(log_format.format_status(tag, f"{path.name} の読み込みに失敗: {e}"))
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        if tag is not None:
            logger.info(log_format.format_status(tag, f"{path.name} の JSON 解析に失敗: {e}"))
        return None
    if not isinstance(data, dict):
        if tag is not None:
            logger.info(log_format.format_status(tag, f"{path.name} がトップレベル dict でないためスキップ"))
        return None
    return cast("dict[str, object]", data)


def atomic_write_text(path: Path, content: str, *, mode: int | None = None, tag: str | None = None) -> bool:
    """テキストファイルを同一ディレクトリの tempfile + ``os.replace`` で原子的に保存する。

    Claude Code 起動中の排他や他プロセスとの競合による書き込み失敗を捕捉し、
    ``False`` を返して呼び出し元に委ねる (post_apply 全体を中断させない)。
    ``mode`` を指定すると書き込み後に ``chmod`` でパーミッションを設定する (Unix のみ)。
    ``tag`` を指定するとログメッセージのラベルに使用する。``None`` の場合はエラーログを出力しない。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        if tag is not None:
            logger.info(log_format.format_status(tag, f"ディレクトリ作成に失敗: {e}"))
        return False
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            prefix=f"{path.name}.",
            suffix=".tmp",
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        if mode is not None and sys.platform != "win32":
            tmp_path.chmod(mode)
        os.replace(tmp_path, path)
        return True
    except OSError as e:
        if tag is not None:
            logger.info(log_format.format_status(tag, f"{path.name} の書き込みに失敗: {e}"))
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        return False


def atomic_write_json(path: Path, data: object, *, tag: str | None = None) -> bool:
    """JSON ファイルを同一ディレクトリの tempfile + ``os.replace`` で原子的に保存する。

    Claude Code 起動中の排他や他プロセスとの競合による書き込み失敗を捕捉し、
    ``False`` を返して呼び出し元に委ねる (post_apply 全体を中断させない)。
    ``tag`` を指定するとログメッセージのラベルに使用する。``None`` の場合はエラーログを出力しない。
    """
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return atomic_write_text(path, content, tag=tag)
