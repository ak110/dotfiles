"""稼働中の利用を守るために延期した更新の記録と、再試行の案内を扱う。

post-applyは延期を返した工程を延期記録へ残す。自動更新タイマーを持つホストでは
`libexec/update_dotfiles_if_upstream_changed.py`が上流変更の有無によらず延期記録を確認して`update-dotfiles`を再実行し、
延期が解消した実行でpost-applyが記録を削除する。記録の保存先とキーは同helperの未完了状態（`.pending`）と同じ規則とする。
"""

import datetime
import hashlib
import json
import pathlib

import platformdirs

from pytools._internal import common, host_roles

_SUFFIX = ".deferred"


def record_path(root: pathlib.Path) -> pathlib.Path:
    """dotfilesルートに対応する延期記録の絶対パスを返す。"""
    # 自動更新helperの`_state_paths`と同じく、ルートの絶対パスを固定長のキーへ変換する。
    key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()
    directory = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "auto-update"
    return directory / f"{key}{_SUFFIX}"


def write(steps: list[str]) -> pathlib.Path | None:
    """延期した工程があれば記録し、無ければ既存の記録を削除する。dotfilesルートを解決できなければ何もしない。

    書込・削除の失敗は`OSError`として送出する（再試行が成立しないことを呼び出し元が失敗として扱うため）。
    """
    root = common.find_dotfiles_root()
    if root is None:
        return None
    path = record_path(root)
    if not steps:
        path.unlink(missing_ok=True)
        return path
    payload = {"steps": steps, "recorded_at": datetime.datetime.now(tz=datetime.UTC).isoformat()}
    if not common.atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"):
        raise OSError(f"延期記録を保存できません: {path}")
    return path


def has_retry_timer() -> bool:
    """延期記録から再試行する自動更新タイマーを持つホストかを返す（同タイマーの導入条件と同じ）。"""
    return host_roles.is_linux_server()


def retry_guidance(manual: str) -> str:
    """延期の案内の後半を返す。タイマーを持つホストでは操作不要、それ以外は`manual`の手動操作を示す。"""
    if has_retry_timer():
        return "自動更新タイマーが再試行し、利用が終わった後に反映するため、操作は不要です。"
    return manual
