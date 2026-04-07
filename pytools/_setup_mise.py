r"""mise 用のセットアップを post_apply から呼ぶモジュール。

本モジュールは `chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
mise (https://mise.jdx.dev/) が導入されているユーザーに対し、次の 2 つを冪等に実施する。

1. Windows のみ: ユーザー PATH (HKCU\Environment\Path) に `%LOCALAPPDATA%\mise\shims`
   を追加する。追加後は `WM_SETTINGCHANGE` をブロードキャストして他プロセスに通知する。
2. クロスプラットフォーム: `mise ls --global --json` に node が無ければ `mise use --global
   node@lts` を実行して global Node を担保する。

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
from pathlib import Path

# Pyright の to be narrowed を避けつつ Windows 判定するためのフラグ。
# `_IS_WINDOWS` を直接使うと非 Windows 環境で条件式が False に評価され、
# Windows 専用コードが unreachable として警告されてしまうため、実行時参照の `os.name`
# を使う。
_IS_WINDOWS = os.name == "nt"

logger = logging.getLogger(__name__)

# `mise` CLI のタイムアウト (秒)。`ls --global --json` は数百 ms、`use --global` は
# 新規インストールが実行されると数十秒かかるため余裕を持たせる。
_MISE_TIMEOUT = 120

# Windows 上で mise の shims ディレクトリを指し示す値。レジストリ上は
# `%LOCALAPPDATA%\mise\shims` のまま REG_EXPAND_SZ で保持する。
_WINDOWS_SHIMS_ENTRY = r"%LOCALAPPDATA%\mise\shims"

# Windows PATH のセパレータ。`os.pathsep` は実行ホストに依存するため、Windows 向け
# 処理内では明示的に `;` を使う (Linux 上のテスト実行でも一貫するように)。
_WINDOWS_PATHSEP = ";"


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    logging.basicConfig(format="%(message)s", level="INFO")
    run()


def run() -> bool:
    """Mise セットアップを実行する。

    Returns:
        何らかの変更を加えたら True。何もしなければ False。
    """
    mise_bin = _find_mise_binary()
    if mise_bin is None:
        logger.info("  -> mise 未検出のためスキップ")
        return False

    changed = False
    if _IS_WINDOWS:
        changed |= _ensure_windows_user_path_has_shims()
    changed |= _ensure_global_node(mise_bin)
    return changed


def _find_mise_binary() -> Path | None:
    """Mise の実行ファイルを探す。

    現プロセスの PATH に無い場合 (Windows で User PATH 更新直後など) も取りこぼさないよう、
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
        logger.info("  -> LOCALAPPDATA 環境変数が無いため shims 追加をスキップ")
        return False

    shims_dir = Path(localappdata) / "mise" / "shims"
    if not shims_dir.is_dir():
        logger.info("  -> %s が無いため shims 追加をスキップ", shims_dir)
        return False

    try:
        current_value, value_type = _read_user_path()
    except OSError as e:
        logger.warning("ユーザー PATH の読み取りに失敗: %s", e)
        return False

    already_registered = _path_contains_shims(current_value, shims_dir)
    if already_registered:
        logger.info("  -> ユーザー PATH に %s は既に登録済み", _WINDOWS_SHIMS_ENTRY)
    else:
        new_value = _append_entry(current_value, _WINDOWS_SHIMS_ENTRY)
        try:
            _write_user_path(new_value, value_type)
        except OSError as e:
            logger.warning("ユーザー PATH の書き込みに失敗: %s", e)
            return False
        logger.info("  -> ユーザー PATH に %s を追加しました", _WINDOWS_SHIMS_ENTRY)
        _broadcast_environment_change()

    # 現プロセスの PATH にも反映しておく (この直後の mise 呼び出しで shims を使える
    # ようにするため)。冪等性のため重複追加は避ける。
    current_process_path = os.environ.get("PATH", "")
    if str(shims_dir) not in current_process_path.split(os.pathsep):
        os.environ["PATH"] = str(shims_dir) + os.pathsep + current_process_path

    return not already_registered


def _import_winreg() -> typing.Any:
    """Winreg モジュールを Any 型で読み込む。

    winreg は Windows 専用の標準モジュールで、Linux 上で pyright を実行すると
    全属性アクセスが `reportAttributeAccessIssue` として検出されてしまう。
    本モジュールは Windows でのみ winreg 依存関数を呼ぶ設計のため、型情報を失っても
    害は無く、`Any` 経由でアクセスするのが最も簡潔。
    """
    import importlib  # noqa: PLC0415

    return importlib.import_module("winreg")


def _read_user_path() -> tuple[str, int]:
    r"""HKCU\Environment\Path の現在値と値型を返す (空文字 + REG_EXPAND_SZ なら未設定)."""
    wr = _import_winreg()
    with wr.OpenKey(wr.HKEY_CURRENT_USER, "Environment", 0, wr.KEY_READ) as key:
        try:
            value, value_type = wr.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return "", wr.REG_EXPAND_SZ
    if not isinstance(value, str):
        return "", wr.REG_EXPAND_SZ
    return value, value_type


def _write_user_path(value: str, value_type: int) -> None:
    r"""HKCU\Environment\Path を書き換える (元の値型 REG_SZ / REG_EXPAND_SZ を維持)."""
    wr = _import_winreg()
    # `%LOCALAPPDATA%` を展開させたいので、元が REG_SZ だった場合も REG_EXPAND_SZ に
    # 揃えて書き戻す (REG_SZ のままだとリテラルで扱われて shims にアクセスできない)。
    if value_type != wr.REG_EXPAND_SZ:
        value_type = wr.REG_EXPAND_SZ

    with wr.OpenKey(wr.HKEY_CURRENT_USER, "Environment", 0, wr.KEY_SET_VALUE) as key:
        wr.SetValueEx(key, "Path", 0, value_type, value)


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


def _broadcast_environment_change() -> None:
    """環境変数変更を他プロセスへ通知する (失敗しても致命ではない)。

    `ctypes.windll` は Windows でしか存在しない属性で、型チェッカや pylint は
    非 Windows 環境では未解決属性として検出してしまう。実行は Windows に限られる
    ため `typing.Any` 経由で呼び出してチェック対象外にする。
    """
    try:
        import ctypes  # noqa: PLC0415

        hwnd_broadcast = 0xFFFF
        wm_settingchange = 0x001A
        smto_abortifhung = 0x0002
        result = ctypes.c_long(0)
        # `ctypes.windll` は Windows 専用で、Linux で pyright/ty などの型チェッカに
        # かけると `ctypes has no member windll` と誤検出される。getattr 経由で
        # 取得して型チェック対象から外す (実行は Windows でのみ)。
        windll = getattr(ctypes, "windll")  # noqa: B009
        windll.user32.SendMessageTimeoutW(
            hwnd_broadcast,
            wm_settingchange,
            0,
            "Environment",
            smto_abortifhung,
            5000,
            ctypes.byref(result),
        )
    except Exception as e:  # noqa: BLE001 -- 通知失敗は致命ではない
        logger.info("  -> 環境変数変更のブロードキャストに失敗: %s", e)


def _ensure_global_node(mise_bin: Path) -> bool:
    """Global 設定に node が無ければ `mise use --global node@lts` を実行する。

    Returns:
        node を新たに設定したら True。
    """
    result = _run_mise(mise_bin, ["ls", "--global", "--json"])
    if result is None or result.returncode != 0:
        stderr = result.stderr.strip() if result else ""
        logger.info("  -> `mise ls --global --json` に失敗したため node 設定をスキップ: %s", stderr)
        return False

    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as e:
        logger.info("  -> `mise ls --global --json` の出力 JSON を解析できずスキップ: %s", e)
        return False

    if _has_global_node(data):
        logger.info("  -> global Node は既に設定済み")
        return False

    install_result = _run_mise(mise_bin, ["use", "--global", "node@lts"])
    if install_result is None or install_result.returncode != 0:
        stderr = install_result.stderr.strip() if install_result else ""
        logger.info("  -> `mise use --global node@lts` に失敗: %s", stderr)
        return False

    logger.info("  -> global に node@lts を設定しました")
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
            # UTF-8 出力に日本語や非 ASCII 文字が含まれると reader thread が
            # UnicodeDecodeError を出す。UTF-8 を明示し、errors="replace" で保険。
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.info("  -> `mise %s` 実行に失敗: %s", " ".join(args), e)
        return None


if __name__ == "__main__":
    _main()
