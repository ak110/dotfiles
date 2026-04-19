r"""mise 用のセットアップを post_apply から呼ぶモジュール。

本モジュールは `chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
mise (https://mise.jdx.dev/) が導入されているユーザーに対し、次の 2 つを冪等に実施する。

1. クロスプラットフォーム: `mise ls --global --json` に node が無ければ `mise use --global
   node@lts` を実行して global Node を担保する。
2. Windows のみ: ユーザー PATH (HKCU\Environment\Path) に `%LOCALAPPDATA%\mise\shims`
   を追加する。追加後は `WM_SETTINGCHANGE` をブロードキャストして他プロセスに通知する。

node 設定を先に実行するのは、初回実行時に shims ディレクトリがまだ存在しないケースへの
対処。`mise use --global node@lts` が shims ディレクトリを作成した後に PATH 追加を
実行することで、1 回の `update-dotfiles` で両方の設定が完了する。

前提条件が揃わない場合 (mise 未導入など) は何もせず `False` を返す。
本ステップは dotfiles apply 全体を止めない方針のため、subprocess / JSON パース / winreg の
失敗は内部で吸収し例外を伝播させない。
"""

import json
import logging
import os
import shutil
import subprocess
import typing
from collections.abc import Callable
from pathlib import Path

from pytools._internal import log_format, winutils
from pytools._internal.cli import setup_logging

# Pyright の to be narrowed を避けつつ Windows 判定するためのフラグ。
# `_IS_WINDOWS` を直接使うと非 Windows 環境で条件式が False に評価され、
# Windows 専用コードが unreachable として警告されてしまうため、実行時参照の `os.name`
# を使う。
_IS_WINDOWS = os.name == "nt"

logger = logging.getLogger(__name__)

# `mise` CLI のタイムアウト (秒)。`ls --global --json` は数百 ms、`use --global` は
# 新規インストールが実行されると数十秒かかるため余裕を持たせる。
_MISE_TIMEOUT = 300

# Windows 上で mise の shims ディレクトリを指し示す値。レジストリ上は
# `%LOCALAPPDATA%\mise\shims` のまま REG_EXPAND_SZ で保持する。
_WINDOWS_SHIMS_ENTRY = r"%LOCALAPPDATA%\mise\shims"

# Windows PATH のセパレータ。`os.pathsep` は実行ホストに依存するため、Windows 向け
# 処理内では明示的に `;` を使う (Linux 上のテスト実行でも一貫するように)。
_WINDOWS_PATHSEP = ";"


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    setup_logging()
    run()


def run(
    *,
    find_mise_fn: Callable[[], Path | None] | None = None,
    is_windows: bool | None = None,
    ensure_global_node_fn: Callable[[Path], bool] | None = None,
) -> bool:
    """Mise セットアップを実行する。

    Returns:
        何らかの変更を加えたら True。何もしなければ False。
    """
    mise_bin = (find_mise_fn or _find_mise_binary)()
    if mise_bin is None:
        logger.info(log_format.format_status("mise", "未検出のためスキップ"))
        return False

    win = _IS_WINDOWS if is_windows is None else is_windows
    changed = False
    changed |= (ensure_global_node_fn or _ensure_global_node)(mise_bin)
    if win:
        changed |= _ensure_windows_user_path_has_shims()
    return changed


def _find_mise_binary() -> Path | None:
    """Mise の実行ファイルを探す。

    現プロセスの PATH に無い場合 (Windows で User PATH 更新直後など) も見落とさないよう、
    既知のインストールパスも併せて確認する。
    """
    from_path = shutil.which("mise")
    if from_path is not None:
        return Path(from_path)

    candidates: list[Path] = []
    if _IS_WINDOWS:
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            candidates.append(Path(localappdata) / "mise" / "bin" / "mise.exe")
    else:
        candidates.append(Path.home() / ".local" / "bin" / "mise")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _ensure_windows_user_path_has_shims() -> bool:
    r"""HKCU\Environment\Path に mise の shims ディレクトリを追加する (Windows 専用).

    Returns:
        レジストリを更新したら True。
    """
    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        logger.info(log_format.format_status("mise", "LOCALAPPDATA 環境変数が無いため shims 追加をスキップ"))
        return False

    shims_dir = Path(localappdata) / "mise" / "shims"
    if not shims_dir.is_dir():
        logger.info(log_format.format_status("mise", f"{shims_dir} が無いため shims 追加をスキップ"))
        return False

    try:
        current_value, value_type = winutils.read_user_env_var("Path")
    except OSError as e:
        logger.warning(log_format.format_status("mise", f"ユーザー PATH の読み取りに失敗: {e}"))
        return False
    if current_value is None:
        current_value = ""

    # `%LOCALAPPDATA%` を展開させたいので、元が REG_SZ だった場合も REG_EXPAND_SZ に
    # 揃えて書き戻す (REG_SZ のままだとリテラルで扱われて shims にアクセスできない)。
    wr = winutils.import_winreg()
    if value_type != wr.REG_EXPAND_SZ:
        value_type = wr.REG_EXPAND_SZ

    already_registered = _path_contains_shims(current_value, shims_dir)
    if already_registered:
        logger.info(log_format.format_status("mise", f"ユーザー PATH に {_WINDOWS_SHIMS_ENTRY} は既に登録済み"))
    else:
        new_value = _append_entry(current_value, _WINDOWS_SHIMS_ENTRY)
        try:
            winutils.write_user_env_var("Path", new_value, value_type)
        except OSError as e:
            logger.warning(log_format.format_status("mise", f"ユーザー PATH の書き込みに失敗: {e}"))
            return False
        logger.info(log_format.format_status("mise", f"ユーザー PATH に {_WINDOWS_SHIMS_ENTRY} を追加しました"))
        winutils.broadcast_environment_change()

    # 現プロセスの PATH にも反映しておく (post_apply の後続ステップが shims 内の
    # コマンドを参照できるようにするため)。冪等性のため重複追加は避ける。
    current_process_path = os.environ.get("PATH", "")
    if str(shims_dir) not in current_process_path.split(os.pathsep):
        os.environ["PATH"] = str(shims_dir) + os.pathsep + current_process_path

    return not already_registered


def _path_contains_shims(current_value: str, shims_dir: Path) -> bool:
    r"""PATH 文字列に shims ディレクトリが既に含まれているかを判定する.

    レジストリに ``%LOCALAPPDATA%\mise\shims`` のまま入っているケースと、既に展開済みの
    絶対パスが入っているケースの両方を許容する。
    """
    entries = [entry for entry in current_value.split(_WINDOWS_PATHSEP) if entry]
    shims_str = str(shims_dir).lower()
    for entry in entries:
        normalized = os.path.expandvars(entry).lower()
        if normalized == shims_str:
            return True
        if entry.lower() == _WINDOWS_SHIMS_ENTRY.lower():
            return True
    return False


def _append_entry(current_value: str, new_entry: str) -> str:
    """PATH 末尾に新エントリを追加する。末尾の `;` は重複させない。"""
    if current_value == "":
        return new_entry
    separator = "" if current_value.endswith(_WINDOWS_PATHSEP) else _WINDOWS_PATHSEP
    return current_value + separator + new_entry


def _ensure_global_node(
    mise_bin: Path,
    *,
    run_mise_fn: Callable[[Path, list[str]], subprocess.CompletedProcess[str] | None] | None = None,
) -> bool:
    """Global 設定に node が無ければ `mise use --global node@lts` を実行する。

    Returns:
        node を新たに設定したら True。
    """
    runner = run_mise_fn or _run_mise
    result = runner(mise_bin, ["ls", "--global", "--json"])
    if result is None or result.returncode != 0:
        stderr = result.stderr.strip() if result else ""
        logger.info(log_format.format_status("mise", f"`ls --global --json` に失敗したため node 設定をスキップ: {stderr}"))
        return False

    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as e:
        logger.info(log_format.format_status("mise", f"`ls --global --json` の出力 JSON を解析できずスキップ: {e}"))
        return False

    if _has_global_node(data):
        logger.info(log_format.format_status("mise", "global Node は既に設定済み"))
        return False

    install_result = runner(mise_bin, ["use", "--global", "node@lts"])
    if install_result is None or install_result.returncode != 0:
        stderr = install_result.stderr.strip() if install_result else ""
        logger.info(log_format.format_status("mise", f"`use --global node@lts` に失敗: {stderr}"))
        return False

    logger.info(log_format.format_status("mise", "global に node@lts を設定しました"))
    return True


def _has_global_node(data: object) -> bool:
    """`mise ls --global --json` の戻り値に node エントリがあるかを判定する。

    mise の出力形式は version により揺れがあるため、下記の全パターンに対応する。

    - ``{"node": [{...}, ...]}``: キーがツール名
    - ``[{"name": "node", ...}, ...]``: 配列の各要素に ``name``
    - 上記のネスト (例えば ``{"tools": ...}``)
    """
    if isinstance(data, dict):
        if "node" in data:
            return True
        return any(_has_global_node(value) for value in data.values())
    if isinstance(data, list):
        return any(_list_item_is_node(item) for item in data)
    return False


def _list_item_is_node(item: object) -> bool:
    """`mise ls --global --json` の list 形式 1 エントリが node を指しているかを判定する。"""
    if not isinstance(item, dict):
        return False
    item_dict = typing.cast("dict[object, object]", item)
    name = item_dict.get("name")
    return isinstance(name, str) and name == "node"


def _run_mise(mise_bin: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """`mise` CLI を呼び出す共通ヘルパー。

    タイムアウト・例外・非ゼロ終了を全て吸収して呼び出し元に返す。
    """
    try:
        return subprocess.run(
            [str(mise_bin), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_MISE_TIMEOUT,
            # Windows で text=True のデフォルトが cp932 になり、mise CLI の
            # UTF-8 出力に日本語や非 ASCII 文字が含まれると reader thread で
            # UnicodeDecodeError が発生する。UTF-8 を明示し、不正なバイトが
            # 混入しても例外が発生しないよう errors="replace" を併用する。
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(log_format.format_status("mise", f"`{' '.join(args)}` 実行に失敗: {e}"))
        return None


if __name__ == "__main__":
    _main()
