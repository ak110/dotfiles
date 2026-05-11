#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code statusLine: セッション状況を1行で可視化する。

stdinから公式statusLine JSON入力を受け取り、モデル名・effort・cwd・gitブランチ・
未ステージ変更行数・コンテキスト使用率・累計コスト・経過時間・5時間使用率を
パイプ区切りで標準出力へ出力する。欠落・null要素は省略する。
"""

import json
import pathlib
import subprocess
import sys
from typing import Any

RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
GRAY = "\033[90m"


def main() -> int:
    """stdinから入力JSONを読み、ステータス行を標準出力へ出力する。"""
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    line = render(data)
    if line:
        sys.stdout.write(line + "\n")
    return 0


def render(data: dict[str, Any]) -> str:
    """入力dictを1行ステータス文字列へレンダリングする。"""
    model_name = _get_nested_str(data, "model", "display_name")
    effort_level = _get_nested_str(data, "effort", "level")
    cwd = _get_nested_str(data, "workspace", "current_dir")
    ctx_pct = _get_nested_number(data, "context_window", "used_percentage")
    total_cost = _get_nested_number(data, "cost", "total_cost_usd")
    duration_ms = _get_nested_number(data, "cost", "total_duration_ms")
    five_hour_pct = _get_nested_number(data, "rate_limits", "five_hour", "used_percentage")

    branch = _git_branch(cwd) if cwd else None
    added, deleted = _git_numstat(cwd) if (cwd and branch) else (0, 0)

    head = _build_head_segment(model_name, effort_level, cwd, branch, added, deleted)

    tail: list[str] = []
    if ctx_pct is not None:
        tail.append(_color(f"ctx {ctx_pct:.0f}%", _threshold_color(ctx_pct)))
    if total_cost is not None:
        tail.append(_color(f"${total_cost:.2f}", GRAY))
    if duration_ms is not None:
        tail.append(_color(_format_duration(duration_ms), GRAY))
    if five_hour_pct is not None:
        tail.append(_color(f"5h:{five_hour_pct:.0f}%", _threshold_color(five_hour_pct)))

    segments = ([head] if head else []) + tail
    return " | ".join(segments)


def _build_head_segment(
    model_name: str | None,
    effort_level: str | None,
    cwd: str | None,
    branch: str | None,
    added: int,
    deleted: int,
) -> str:
    """先頭の[モデル|effort] cwd branch +N -N部分を組み立てる。"""
    parts: list[str] = []
    label = _build_model_label(model_name, effort_level)
    if label:
        parts.append(_color(f"[{label}]", CYAN))
    if cwd:
        parts.append(_color(_shorten_home(cwd), BLUE))
    if branch:
        parts.append(_color(branch, GREEN))
        if added > 0 or deleted > 0:
            parts.append(f"{_color(f'+{added}', GREEN)} {_color(f'-{deleted}', RED)}")
    return " ".join(parts)


def _build_model_label(model_name: str | None, effort_level: str | None) -> str:
    """`<モデル名>|<effort>`形式の内側ラベル文字列を返す。両方Noneなら空文字。"""
    if model_name and effort_level:
        return f"{model_name}|{effort_level}"
    if model_name:
        return model_name
    if effort_level:
        return effort_level
    return ""


def _shorten_home(path: str) -> str:
    """ホームディレクトリ部分を`~`に短縮する。"""
    home = str(pathlib.Path.home())
    if path == home:
        return "~"
    for sep in ("/", "\\"):
        prefix = home + sep
        if path.startswith(prefix):
            return "~" + sep + path[len(prefix) :]
    return path


def _format_duration(ms: float) -> str:
    """ミリ秒を`分:秒`または`時:分:秒`で整形する。"""
    seconds = int(ms / 1000)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _threshold_color(percentage: float) -> str:
    """80%超で赤・50%超で黄・それ以下で緑のANSI色コードを返す。"""
    if percentage > 80:
        return RED
    if percentage > 50:
        return YELLOW
    return GREEN


def _color(text: str, code: str) -> str:
    """ANSI色付きテキストを返す。"""
    return f"{code}{text}{RESET}"


def _git_branch(cwd: str) -> str | None:
    """gitブランチ名を取得する。git未管理または取得失敗時はNoneを返す。"""
    result = _run_git(cwd, ["symbolic-ref", "--short", "HEAD"])
    if result is None or result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def _git_numstat(cwd: str) -> tuple[int, int]:
    """`git diff --numstat`の集計（未ステージの追加・削除行数）を返す。"""
    result = _run_git(cwd, ["diff", "--numstat"])
    if result is None or result.returncode != 0:
        return 0, 0
    added = 0
    deleted = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            added += int(parts[0])
            deleted += int(parts[1])
        except ValueError:
            # バイナリ差分は`-`になるためスキップ
            continue
    return added, deleted


def _run_git(cwd: str, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Git -C <cwd> ...を実行する。失敗時はNoneを返す。"""
    try:
        return subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


def _get_nested_str(data: dict[str, Any], *keys: str) -> str | None:
    """ネストしたdictから文字列値を取得する。型不一致・null・欠落でNoneを返す。"""
    value = _get_nested(data, *keys)
    return value if isinstance(value, str) and value else None


def _get_nested_number(data: dict[str, Any], *keys: str) -> float | None:
    """ネストしたdictから数値を取得する。型不一致・null・欠落でNoneを返す。"""
    value = _get_nested(data, *keys)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _get_nested(data: dict[str, Any], *keys: str) -> Any:
    """ネストしたdictから値を取得する。途中でdict以外に当たればNoneを返す。"""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


if __name__ == "__main__":
    sys.exit(main())
