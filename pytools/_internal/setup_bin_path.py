r"""ユーザーPATHへの`bin`ディレクトリ群の登録（Windowsのみ）。

dotfilesの作業ツリーの`bin`・`agent-toolkit\bin`と、`uv tool install`が実行ファイルを置く
`%USERPROFILE%\.local\bin`を登録する。
Linuxでは`~/.bashrc`が同じディレクトリをPATHへ追加する。
Windowsには対応する自動投入経路がないため、`chezmoi apply`後処理で
`HKCU\Environment`の`Path`へ冪等に追記する。
`.local\bin`は`uv tool update-shell`へ任せず本工程で登録し、登録済みの判定を`winutils.append_user_path`の1か所にそろえる。
"""

import logging
from pathlib import Path, PureWindowsPath

from pytools._internal import common, log_format, post_apply_outcome, winutils

logger = logging.getLogger(__name__)

_USERPROFILE = "%USERPROFILE%"


def _bin_entries(dotfiles_root: Path | None, home: Path) -> tuple[str, ...]:
    r"""登録するPATHの値を、Linux側`.chezmoi-source/dot_bashrc`の追加順序（dotfiles/bin → dotfiles/agent-toolkit/bin）で返す。

    作業ツリーの位置は`find_dotfiles_root()`から求める。ホーム配下の値は`%USERPROFILE%`を残した相対表記で登録する。
    REG_EXPAND_SZなら展開されてプロファイルパスの変更にも追従でき、`cleanup_user_path`がPATH整理で置換する形とも一致する。
    絶対パスへ展開して登録すると、PATH整理が`%USERPROFILE%`形式へ戻し、次の登録が再び追記する往復を起こす。
    作業ツリーを解決できない場合は、作業ツリー配下の2件を登録しない。
    """
    entries: list[str] = []
    if dotfiles_root is not None:
        try:
            base = "\\".join((_USERPROFILE, *dotfiles_root.relative_to(home).parts))
        except ValueError:
            base = str(PureWindowsPath(dotfiles_root))
        entries.extend((f"{base}\\bin", f"{base}\\agent-toolkit\\bin"))
    entries.append(f"{_USERPROFILE}\\.local\\bin")
    return tuple(entries)


def run() -> post_apply_outcome.PostApplyOutcome:
    r"""`HKCU\Environment` の `Path` に dotfiles配下のbinディレクトリを冪等に追記する。"""
    any_appended = False
    failures: list[str] = []
    for entry in _bin_entries(common.find_dotfiles_root(), Path.home()):
        try:
            appended = winutils.append_user_path(entry)
        except OSError as e:
            failures.append(f"{entry} の登録に失敗: {e}")
            continue
        if appended:
            logger.info(log_format.format_status("bin PATH", f"ユーザー PATH に追記: {entry}"))
            any_appended = True
    if any_appended:
        # 追記時のみ環境変数変更をブロードキャストし、新規プロセスで即時反映させる。
        winutils.broadcast_environment_change()
    return post_apply_outcome.PostApplyOutcome(changed=any_appended, failure=" / ".join(failures) or None)
