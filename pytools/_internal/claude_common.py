"""Claude Code関連の共通定数・ユーティリティ。"""

import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import typing
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

import pytilpack.jsonc

from pytools._internal import log_format

logger = logging.getLogger(__name__)

CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"
SETTINGS_JSON_PATH = CLAUDE_HOME / "settings.json"
PLANS_DIR = CLAUDE_HOME / "plans"

# CLI 呼び出しを回避するための直接読み取り用
INSTALLED_PLUGINS_PATH = CLAUDE_HOME / "plugins" / "installed_plugins.json"

# marketplace.json の `name` と一致させる (.claude-plugin/marketplace.json を参照)
MARKETPLACE_NAME = "ak110-dotfiles"

# GitHub からの初回 clone や install 処理で時間がかかる場合があるため余裕を持たせる
CLAUDE_TIMEOUT = 30

# バランスモード・フィードバック蓄積等、特定ホストでのみ有効化する機能が共有する対象ホスト一覧。
TARGET_HOSTS: tuple[str, ...] = ("stheno", "circe", "circe-container", "euryale", "euryale-container")


def is_target_host(hostname: str) -> bool:
    """ホスト名が`TARGET_HOSTS`に含まれるかを判定する（大文字小文字無視・FQDN接尾辞除去）。"""
    return hostname.lower().split(".")[0] in TARGET_HOSTS


def ensure_flag_file_present(flag_path: Path, *, tag: str) -> bool:
    """フラグファイルを冪等に生成する。

    Returns:
        新規生成した場合True、既存のため生成不要の場合False。
    """
    if flag_path.exists():
        return False
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_bytes(b"")
    logger.info(log_format.format_status(tag, f"フラグファイルを生成: {flag_path}"))
    return True


def find_dotfiles_root() -> Path | None:
    """Dotfiles ルートディレクトリを返す。

    dotfiles ルートは `.claude-plugin/marketplace.json` を持つ。
    本ファイルは `dotfiles/pytools/_internal/` に置かれるため、3階層上がルートとなる。
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
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """サブプロセスをUTF-8 + `errors="replace"` で実行する共通ラッパー。

    タイムアウト・OSError・SubprocessError を吸収して None を返す。非ゼロ終了は
    そのまま呼び出し元に返す。`tag` を指定すると失敗時のログラベルに使用する。

    Windowsでは `text=True` の既定エンコーディングがcp932となり、CLIのUTF-8出力で
    UnicodeDecodeErrorが発生するため、エンコーディングをUTF-8に明示し、
    不正バイトが混入しても例外が発生しないよう `errors="replace"` を併用する。

    対話入力は想定しない。標準入力は `subprocess.DEVNULL` に固定し、
    CLIがプロンプト表示により無応答状態に陥る事象を防ぐ。

    `env_overrides` を指定したときは現プロセスの環境をベースに該当キーを
    上書きしたdictを `env` として渡す（`None` の既定では現プロセスの環境を継承する）。
    """
    env: dict[str, str] | None = None
    if env_overrides is not None:
        env = os.environ.copy()
        env.update(env_overrides)
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
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as e:
        label = tag or cmd[0]
        rest = " ".join(cmd[1:])
        logger.info(log_format.format_status(label, f"`{rest}` 実行に失敗: {e}"))
        return None


def run_claude(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str] | None:
    """`claude` CLIを呼び出す共通ヘルパー。

    タイムアウト・例外・非ゼロ終了を全て吸収して呼び出し元に返す。
    `cwd` を指定すると project scope など cwd 依存のサブコマンドに対応できる。
    原因追跡のため、実行コマンドと戻り値をログに残す。
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
    """JSONファイルをトップレベルdictとして読み込む。

    ファイルが存在しない場合は空dict（または `default`）を返す。
    JSON解析失敗・非dict・I/Oエラーは ``None`` を返し、呼び出し元で書き込みを中止させる。
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


def _atomic_write(
    path: Path, writer: Callable[[typing.IO[typing.Any]], object], *, binary: bool, mode: int | None, tag: str | None
) -> bool:
    """同一ディレクトリのtempfile + `Path.replace` で原子的に保存する共通実装。

    `atomic_write_text()`・`atomic_write_bytes()`の共通処理（ディレクトリ作成・tempfile経由の
    書き込み・パーミッション設定・失敗時の後始末）を集約する。`writer`が開いたファイルへ内容を
    書き込み、`binary`でテキスト/バイナリのopenモードを切り替える。
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
            mode="wb" if binary else "w",
            encoding=None if binary else "utf-8",
            dir=path.parent,
            delete=False,
            prefix=f"{path.name}.",
            suffix=".tmp",
        ) as tmp:
            writer(tmp)
            tmp_path = Path(tmp.name)
        if mode is not None and sys.platform != "win32":
            tmp_path.chmod(mode)
        tmp_path.replace(path)
        return True
    except OSError as e:
        if tag is not None:
            logger.info(log_format.format_status(tag, f"{path.name} の書き込みに失敗: {e}"))
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        return False


def atomic_write_text(path: Path, content: str, *, mode: int | None = None, tag: str | None = None) -> bool:
    """テキストファイルを同一ディレクトリのtempfile + `Path.replace` で原子的に保存する。

    Claude Code起動中の排他や他プロセスとの競合による書き込み失敗を捕捉し、
    ``False`` を返して呼び出し元に委ねる（post_apply全体を中断させない）。
    ``mode`` を指定すると書き込み後に ``chmod`` でパーミッションを設定する（Unixのみ）。
    ``tag`` を指定するとログメッセージのラベルに使用する。``None`` の場合はエラーログを出力しない。
    """
    return _atomic_write(path, lambda tmp: tmp.write(content), binary=False, mode=mode, tag=tag)


def atomic_write_bytes(path: Path, content: bytes, *, mode: int | None = None, tag: str | None = None) -> bool:
    """バイナリファイルを同一ディレクトリのtempfile + `Path.replace` で原子的に保存する。

    `atomic_write_text()`のバイナリ版。書き込み・パーミッション設定の途中で失敗しても
    `path`の既存内容を保持する（実行ファイル配置など破損置換を避けたい用途向け）。
    """
    return _atomic_write(path, lambda tmp: tmp.write(content), binary=True, mode=mode, tag=tag)


def _collect_value_only_updates(
    original: object,
    updated: object,
    path: tuple[str | int, ...] = (),
) -> dict[Sequence[str | int], object] | None:
    """既存パスの値置換のみで済む差分を収集する。

    `pytilpack.jsonc.edit` が扱える更新（既存パスの値書き換え）のみで
    ``original`` から ``updated`` への変化を再現できる場合、
    パス→新値のマッピングを返す。
    構造変化（片方のみに存在するキー・list全体の差し替え・型変化）を
    検出した場合は ``None`` を返し、呼び出し元にフォールバック（全書き換え）を促す。
    ``list`` は要素同一（順序含む）でなければ構造変化として扱う。
    """
    if type(original) is not type(updated):
        return None
    if isinstance(original, dict):
        assert isinstance(updated, dict)
        original_dict = cast("dict[str | int, object]", original)
        updated_dict = cast("dict[str | int, object]", updated)
        if set(original_dict.keys()) != set(updated_dict.keys()):
            return None
        result: dict[Sequence[str | int], object] = {}
        for key in original_dict:
            sub = _collect_value_only_updates(original_dict[key], updated_dict[key], (*path, key))
            if sub is None:
                return None
            result.update(sub)
        return result
    if isinstance(original, list):
        if original != updated:
            return None
        return {}
    if original == updated:
        return {}
    return {path: updated}


def _atomic_edit_jsonc(
    path: Path,
    updates: Mapping[Sequence[str | int], object],
    *,
    tag: str | None = None,
) -> bool:
    """JSONCファイルの既存パスの値を書き換えてコメント・空行・インデントを維持する。

    ``pytilpack.jsonc.edit`` で書き換えた結果を ``atomic_write_text`` で保存する。
    ``updates`` が空の場合は書き込みをせず ``False`` を返す。
    書き換え結果が元テキストと一致した場合も ``False`` を返す。
    """
    if not updates:
        return False
    original = path.read_text(encoding="utf-8")
    updated = pytilpack.jsonc.edit(original, updates)
    if updated == original:
        return False
    return atomic_write_text(path, updated, tag=tag)


def write_settings_hybrid(
    path: Path,
    original: object,
    data: object,
    *,
    tag: str | None = None,
) -> bool:
    """マージ結果を対象ファイルへ書き戻すハイブリッド経路。

    既存パスの値置換のみで済む差分ならJSONCコメント・空行・インデントを維持する
    経路（``_atomic_edit_jsonc``）で書き戻す。構造変化を含む場合や当該経路が
    失敗した場合は全書き換え経路（``json.dumps`` + ``atomic_write_text``）へ
    フォールバックする。両経路とも原子的書き込みで統一しており、書き込み失敗時は
    ``False`` を返す。

    ``pytilpack.jsonc.edit`` は元テキストの再パース時に、コンカレントな他プロセス
    書き込みでパス構造が変化した場合に ``KeyError``・``IndexError``・``TypeError``・
    ``ValueError`` を送出する。これらを捕捉して全書き換え経路へ倒す。
    """
    updates: Mapping[Sequence[str | int], object] | None
    updates = _collect_value_only_updates(original, data) if path.exists() else None
    if updates:
        with contextlib.suppress(KeyError, IndexError, TypeError, ValueError):
            if _atomic_edit_jsonc(path, updates, tag=tag):
                return True
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return atomic_write_text(path, content, tag=tag)


def atomic_write_json(path: Path, data: object, *, tag: str | None = None) -> bool:
    """JSONファイルを同一ディレクトリのtempfile + `Path.replace` で原子的に保存する。

    Claude Code起動中の排他や他プロセスとの競合による書き込み失敗を捕捉し、
    ``False`` を返して呼び出し元に委ねる（post_apply全体を中断させない）。
    ``tag`` を指定するとログメッセージのラベルに使用する。``None`` の場合はエラーログを出力しない。
    """
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return atomic_write_text(path, content, tag=tag)
