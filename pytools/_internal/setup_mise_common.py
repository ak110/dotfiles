"""setup_miseの環境設定とCLI呼出し。"""

import logging
import ntpath
import os
import subprocess
from pathlib import Path

import platformdirs

from pytools._internal import common

logger = logging.getLogger(__name__)

# `mise` CLI のタイムアウト (秒)。`ls --global --json` は数百 ms、`use --global` は
# 新規インストールが実行されると数十秒かかるため余裕を持たせる。
MISE_TIMEOUT = 300

# `mise install` のタイムアウト (秒)。aqua/npm バックエンドの初回ダウンロードや
# 複数ツールの一括インストールで時間がかかるため、通常コマンドより長めに取る。
MISE_INSTALL_TIMEOUT = 600

# minimalは単独post-applyでも既存のfull用グローバル設定から大型ツールを導入しない。
LIGHTWEIGHT_TOOLS = ("chezmoi", "fzf", "gh", "glab", "glow", "jq", "ripgrep")

# 無人セットアップではネットワークの一時的な遅延で版一覧取得が失敗しないよう、
# miseが設定のない場合に使う値より長い上限を全mise呼び出しへ適用する。
_MISE_FETCH_REMOTE_VERSIONS_TIMEOUT = "120s"

# Windows 上で mise の shims ディレクトリを指し示す値。レジストリ上は
# `%LOCALAPPDATA%\mise\shims` のまま REG_EXPAND_SZ で保持する。
WINDOWS_SHIMS_ENTRY = r"%LOCALAPPDATA%\mise\shims"

# Windows PATH のセパレータ。`os.pathsep` は実行ホストで値が変わるため、Windows 向け
# 処理内では明示的に `;` を使う (Linux 上のテスト実行でも一貫するように)。
_WINDOWS_PATHSEP = ";"


def is_windows() -> bool:
    """Windows環境かどうかを返す。テストでmonkeypatchしやすいよう関数化している。"""
    return os.name == "nt"


def mise_env_overrides() -> dict[str, str]:
    """mise子プロセスへ渡す非対話設定とWindows固有の.NET設定を返す。"""
    overrides = {
        "MISE_YES": "1",
        "CI": "1",
        "MISE_FETCH_REMOTE_VERSIONS_TIMEOUT": _MISE_FETCH_REMOTE_VERSIONS_TIMEOUT,
    }
    if not is_windows():
        return overrides

    dotnet_root = os.environ.get("MISE_DOTNET_ROOT")
    if not dotnet_root:
        mise_data_dir = os.environ.get("MISE_DATA_DIR") or platformdirs.user_data_dir("mise", appauthor=False)
        dotnet_root = str(Path(mise_data_dir) / "dotnet-root")

    normalized_root = ntpath.normcase(ntpath.normpath(dotnet_root))
    path_entries = [entry for entry in os.environ.get("PATH", "").split(_WINDOWS_PATHSEP) if entry]
    retained_entries = [entry for entry in path_entries if ntpath.normcase(ntpath.normpath(entry)) != normalized_root]
    overrides["MISE_DOTNET_ROOT"] = dotnet_root
    overrides["PATH"] = _WINDOWS_PATHSEP.join([dotnet_root, *retained_entries])
    return overrides


class SettingError(Exception):
    """設定の書き換えの失敗。`run`が失敗と数える。"""


def run_mise(
    mise_bin: Path,
    args: list[str],
    *,
    timeout: float = MISE_TIMEOUT,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """`mise` CLIを呼び出す共通ヘルパー。

    タイムアウト・例外を吸収して `CompletedProcess` または `None` を返す。
    非ゼロ終了の扱いは呼び出し元に委ねる。

    miseの対話確認とTTY検出を抑止するため `MISE_YES=1`・`CI=1` を注入する。
    aqua/npmバックエンドの初回ダウンロード時に確認プロンプトでブロックするのを防ぐ。
    無人セットアップで版一覧取得が短時間のネットワーク遅延により失敗しないよう、
    mise内部の版一覧取得上限も延長する。
    Windowsではシステム版`dotnet.exe`が先行しても、miseのSDK検証が共有ルートを
    参照するよう子プロセスだけの`PATH`を設定する。

    失敗時の標準エラー出力は`_mise_error_lines`で失敗理由の行だけに限定する。
    呼び出し元の診断へ進捗表示が混入しないよう、整形をここへ集約する。
    """
    result = common.run_subprocess(
        [str(mise_bin), *args],
        timeout=timeout,
        cwd=cwd,
        tag="mise",
        env_overrides=mise_env_overrides() | (extra_env or {}),
    )
    if result is None or result.returncode == 0:
        return result
    return subprocess.CompletedProcess(result.args, result.returncode, result.stdout, _mise_error_lines(result.stderr))


def _mise_error_lines(stderr: str) -> str:
    """miseの失敗出力から、色指定を除いた`mise ERROR`行以降を返す。該当行が無ければ色指定だけを除く。

    miseは非TTYかつ`CI=1`でも進捗表示と`WARN`行を標準エラー出力へ書く。
    mise 2026.10.17をネットワーク遮断下で失敗させた観測では、`mise ERROR`行以降は
    `--quiet`を付けた場合の出力と一致し、ツールごとの失敗理由を含む。
    """
    lines = common.strip_ansi(stderr).splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("mise ERROR")), 0)
    return "\n".join(lines[start:]).strip()


def with_mise_output(message: str, result: subprocess.CompletedProcess[str] | None) -> str:
    """dotfilesが書く案内の後へ、miseの出力を独立した行として続けた診断を返す。

    miseの出力は複数行で句点を持たないため、案内と同じ行へ連結すると文の境界が読めなくなる。
    """
    return f"{message}\n{common.format_cli_error(result)}"
