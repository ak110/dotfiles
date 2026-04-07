"""SSH config and authorized_keys generator.

- ~/.ssh/config: conf.d/*.conf + localconfig を結合して上書き生成
- ~/.ssh/authorized_keys: conf.d/authorized_keys + local_authorized_keys から
  既存にない鍵のみ追加（既存の鍵は削除しない）
"""

import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# 鍵タイプ + base64データを抽出する正規表現
# options内のクォート文字列に鍵タイプが含まれる場合でも、
# base64データの長さ(32文字以上)で誤認を防ぐ
_KEY_PATTERN = re.compile(r"(?:ssh-\S+|ecdsa-\S+|sk-\S+)\s+([A-Za-z0-9+/=]{32,})")


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    logging.basicConfig(format="%(message)s", level="INFO")
    run()


def run() -> bool:
    """~/.ssh/config と authorized_keys を生成/更新する。

    Returns:
        いずれかのファイルを実際に書き換えたかどうか。
    """
    ssh_dir = Path.home() / ".ssh"
    if not (ssh_dir / "conf.d").exists():
        logger.info("%s/conf.d が存在しないためスキップ", ssh_dir)
        return False
    changed_config = _generate_ssh_config(ssh_dir)
    changed_keys = _generate_authorized_keys(ssh_dir)
    return changed_config or changed_keys


def _generate_ssh_config(ssh_dir: Path) -> bool:
    """conf.d/*.conf + localconfig -> config (上書き)

    Returns:
        ファイル内容が変化したかどうか。
    """
    conf_d = ssh_dir / "conf.d"
    parts: list[str] = []
    for conf_file in sorted(conf_d.glob("*.conf")):
        parts.append(_ensure_trailing_newline(conf_file.read_text(encoding="utf-8")))
    localconfig = ssh_dir / "localconfig"
    if localconfig.exists():
        parts.append(_ensure_trailing_newline(localconfig.read_text(encoding="utf-8")))
    new_content = "".join(parts)

    config_path = ssh_dir / "config"
    old_content = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    if old_content == new_content:
        logger.info("%s: 変更なし", config_path)
        return False

    # 初回のみバックアップを作成
    backup_path = config_path.with_suffix(".bk")
    if config_path.exists() and not backup_path.exists():
        shutil.copy2(config_path, backup_path)
        logger.info("バックアップを作成: %s", backup_path)
    _atomic_write(config_path, new_content)
    logger.info("%s を更新しました", config_path)
    return True


def _generate_authorized_keys(ssh_dir: Path) -> bool:
    """conf.d/authorized_keys + local_authorized_keys -> authorized_keys (追加のみ)

    Returns:
        ファイル内容が変化したかどうか。
    """
    ak_path = ssh_dir / "authorized_keys"
    # 既存ファイル読み込み
    existing_lines: list[str] = []
    known_keys: set[str] = set()
    if ak_path.exists():
        for line in ak_path.read_text(encoding="utf-8").splitlines():
            existing_lines.append(line)
            key_data = _extract_key_data(line)
            if key_data is not None:
                known_keys.add(key_data)
    original_lines = list(existing_lines)
    # ソースファイルから未知の鍵を追加
    sources = [ssh_dir / "conf.d" / "authorized_keys", ssh_dir / "local_authorized_keys"]
    added = 0
    for source in sources:
        if not source.exists():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            key_data = _extract_key_data(line)
            if key_data is None:
                continue  # 空行・コメント行はスキップ
            if key_data not in known_keys:
                existing_lines.append(line)
                known_keys.add(key_data)
                added += 1
    if added == 0 and existing_lines == original_lines:
        logger.info("%s: 変更なし (追加鍵 0 件)", ak_path)
        return False
    # 書き出し
    content = "\n".join(existing_lines) + "\n" if existing_lines else ""
    _atomic_write(ak_path, content)
    logger.info("%s に %d 件の鍵を追加しました", ak_path, added)
    return True


def _extract_key_data(line: str) -> str | None:
    """authorized_keys行からbase64鍵データを抽出する。空行・コメント行はNone。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    m = _KEY_PATTERN.search(stripped)
    if m:
        return m.group(1)
    # フォールバック: 行全体
    return stripped


def _atomic_write(path: Path, content: str) -> None:
    """一時ファイルに書き込んでからrenameすることで、中断時のファイル破損を防ぐ。"""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        tmp_path = Path(tmp)
        if sys.platform != "win32":
            tmp_path.chmod(0o600)
        tmp_path.replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


if __name__ == "__main__":
    _main()
