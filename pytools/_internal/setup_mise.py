"""miseセットアップをまとめるモジュール。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれる。
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import typing
from pathlib import Path

from pytools._internal import claude_common, log_format, winutils
from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)

# `mise` CLI のタイムアウト (秒)。`ls --global --json` は数百 ms、`use --global` は
# 新規インストールが実行されると数十秒かかるため余裕を持たせる。
_MISE_TIMEOUT = 300

# `mise install` のタイムアウト (秒)。aqua/npm バックエンドの初回ダウンロードや
# 複数ツールの一括インストールで時間がかかるため、通常コマンドより長めに取る。
_MISE_INSTALL_TIMEOUT = 600

# Windows 上で mise の shims ディレクトリを指し示す値。レジストリ上は
# `%LOCALAPPDATA%\mise\shims` のまま REG_EXPAND_SZ で保持する。
_WINDOWS_SHIMS_ENTRY = r"%LOCALAPPDATA%\mise\shims"

# Windows PATH のセパレータ。`os.pathsep` は実行ホストに依存するため、Windows 向け
# 処理内では明示的に `;` を使う (Linux 上のテスト実行でも一貫するように)。
_WINDOWS_PATHSEP = ";"


def _is_windows() -> bool:
    """Windows環境かどうかを返す。テストでmonkeypatchしやすいよう関数化している。"""
    return os.name == "nt"


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    setup_logging()
    run()
    sys.exit(0)


def run() -> bool:
    """Mise セットアップを実行する。

    Returns:
        何らかの変更を加えたら True。何もしなければ False。
    """
    mise_bin = _find_mise_binary()
    if mise_bin is None:
        logger.info(log_format.format_status("mise", "未検出のためスキップ"))
        return False

    changed = False
    changed |= _ensure_working_tree_trusted(mise_bin)
    changed |= _ensure_global_node(mise_bin)
    changed |= _ensure_tools_installed(mise_bin)
    if _is_windows():
        changed |= _ensure_windows_user_path_has_shims()
    return changed


def _find_mise_binary() -> Path | None:
    """Mise の実行ファイルを探す。

    現プロセスの PATH に無い場合 (Windows で User PATH 更新直後など) も看過しないよう、
    既知のインストールパスも併せて確認する。
    """
    from_path = shutil.which("mise")
    if from_path is not None:
        return Path(from_path)

    candidates: list[Path] = []
    if _is_windows():
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            candidates.append(Path(localappdata) / "mise" / "bin" / "mise.exe")
    else:
        candidates.append(Path.home() / ".local" / "bin" / "mise")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _ensure_working_tree_trusted(mise_bin: Path) -> bool:
    """Chezmoi workingTree直下の `mise.toml` を `mise trust` 対象にする。

    未trustのままではmise CLIが毎回警告を表示して設定を無視するため、冪等にtrustする。
    workingTreeは `CHEZMOI_WORKING_TREE` 環境変数経由で受け取る（未設定ならCLI単体実行
    とみなしてスキップ）。既にtrust済みであっても `mise trust` は重複登録しない仕様のため、
    事前チェックは行わず毎回実行する。副作用がないため、成功時はchanged判定も常にTrueを
    返す（サマリで「更新」扱いになるが、ノイズよりも実行事実を確認できる方を優先する）。
    """
    working_tree = os.environ.get("CHEZMOI_WORKING_TREE")
    if not working_tree:
        logger.info(log_format.format_status("mise", "CHEZMOI_WORKING_TREE 未設定のため trust をスキップ"))
        return False

    mise_toml = Path(working_tree) / "mise.toml"
    if not mise_toml.is_file():
        logger.info(log_format.format_status("mise", f"{mise_toml} が無いため trust をスキップ"))
        return False

    result = _run_mise(mise_bin, ["trust", str(mise_toml)])
    if result is None or result.returncode != 0:
        stderr = result.stderr.strip() if result else ""
        logger.info(log_format.format_status("mise", f"`trust` に失敗: {stderr}"))
        return False

    logger.info(log_format.format_status("mise", f"{mise_toml} を trust しました"))
    return True


def _ensure_global_node(mise_bin: Path) -> bool:
    """global設定にnodeがなければ `mise use --global node@lts` を実行する。

    Returns:
        nodeを新たに設定した場合True。
    """
    result = _run_mise(mise_bin, ["ls", "--global", "--json"])
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

    install_result = _run_mise(mise_bin, ["use", "--global", "node@lts"])
    if install_result is None or install_result.returncode != 0:
        stderr = install_result.stderr.strip() if install_result else ""
        logger.info(log_format.format_status("mise", f"`use --global node@lts` に失敗: {stderr}"))
        return False

    logger.info(log_format.format_status("mise", "global に node@lts を設定しました"))
    return True


def _has_global_node(data: object) -> bool:
    """`mise ls --global --json` の戻り値にnodeエントリがあるかを判定する。

    miseの出力形式はversionにより揺れがあるため、以下の全パターンに対応する。

    - ``{"node": [{...}, ...]}``: キーがツール名
    - ``[{"name": "node", ...}, ...]``: 配列の各要素に ``name``
    - 上記のネスト（例: ``{"tools": ...}``）
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


def _ensure_tools_installed(mise_bin: Path) -> bool:
    """`mise install` で global/working-tree 設定のツールを取得する。

    終了コード非ゼロ・タイムアウト・例外はすべて吸収し、後続ステップを止めない。
    インストール差分の厳密判定は出力からは行えないため、`changed` の冪等性ではなく
    実行事実の記録を優先する設計とし、結果に関わらず常に True を返す。
    """
    result = _run_mise(mise_bin, ["install"], timeout=_MISE_INSTALL_TIMEOUT)
    if result is None:
        logger.info(log_format.format_status("mise", "`install` がタイムアウトまたは例外で中断"))
        return True
    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.info(log_format.format_status("mise", f"`install` に失敗: {stderr}"))
        return True
    logger.info(log_format.format_status("mise", "`install` を実行しました"))
    return True


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
    r"""PATH文字列にshimsディレクトリが既に含まれているかを判定する。

    レジストリに ``%LOCALAPPDATA%\mise\shims`` のまま格納されているケースと、
    既に展開済みの絶対パスが格納されているケースの両方を許容する。
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


def _run_mise(
    mise_bin: Path,
    args: list[str],
    *,
    timeout: float = _MISE_TIMEOUT,
) -> subprocess.CompletedProcess[str] | None:
    """`mise` CLIを呼び出す共通ヘルパー。

    タイムアウト・例外を吸収して `CompletedProcess` または `None` を返す。
    非ゼロ終了の扱いは呼び出し元に委ねる。

    miseの対話確認とTTY検出を抑止するため `MISE_YES=1`・`CI=1` を注入する。
    aqua/npmバックエンドの初回ダウンロード時に確認プロンプトでブロックするのを防ぐ。
    """
    return claude_common.run_subprocess(
        [str(mise_bin), *args],
        timeout=timeout,
        tag="mise",
        env_overrides={"MISE_YES": "1", "CI": "1"},
    )


if __name__ == "__main__":
    main()
