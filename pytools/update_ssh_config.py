import argparse
import logging
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from pytools._internal import common, log_format, post_apply_outcome
from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)


_KEY_PATTERN = re.compile(r"(?:ssh-\S+|ecdsa-\S+|sk-\S+)\s+([A-Za-z0-9+/=]{32,})")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SSHのconfigとauthorized_keysを更新する。")
    parser.parse_args(argv)
    setup_logging()
    run()
    sys.exit(0)


def run() -> post_apply_outcome.PostApplyOutcome:
    """~/.ssh/config と authorized_keys を生成/更新する。書き込みの失敗は例外として送出する。"""
    ssh_dir = Path.home() / ".ssh"
    if not (ssh_dir / "conf.d").exists():
        logger.info(
            log_format.format_status(
                log_format.home_short(ssh_dir / "conf.d"), ("存在しないためスキップ。管理対象のSSH設定がないため対応不要。")
            )
        )
        return post_apply_outcome.PostApplyOutcome()
    changed_config = _generate_ssh_config(ssh_dir)
    changed_keys = _generate_authorized_keys(ssh_dir)
    return post_apply_outcome.PostApplyOutcome(changed=changed_config or changed_keys)


def _generate_ssh_config(ssh_dir: Path) -> bool:
    conf_d = ssh_dir / "conf.d"
    fragments = [conf_file.read_text(encoding="utf-8") for conf_file in sorted(conf_d.glob("*.conf"))]
    localconfig = ssh_dir / "localconfig"
    if localconfig.exists():
        fragments.append(localconfig.read_text(encoding="utf-8"))
    new_content = _build_ssh_config_content(fragments)

    config_path = ssh_dir / "config"
    short = log_format.home_short(config_path)
    old_content = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    if old_content == new_content:
        logger.info(log_format.format_status(short, "変更なし"))
        return False

    backup_path = config_path.with_suffix(".bk")
    if config_path.exists() and not backup_path.exists():
        shutil.copy2(config_path, backup_path)
        logger.info(log_format.format_status(short, f"バックアップを作成: {log_format.home_short(backup_path)}"))
    if not common.atomic_write_text(config_path, new_content, mode=0o600):
        raise OSError(f"{short} の書き込みに失敗")
    logger.info(log_format.format_status(short, "更新しました"))
    return True


def _build_ssh_config_content(fragments: Sequence[str]) -> str:
    return "".join(_ensure_trailing_newline(fragment) for fragment in fragments)


def _generate_authorized_keys(ssh_dir: Path) -> bool:
    ak_path = ssh_dir / "authorized_keys"
    existing_text = ak_path.read_text(encoding="utf-8") if ak_path.exists() else ""
    sources = (ssh_dir / "conf.d" / "authorized_keys", ssh_dir / "local_authorized_keys")
    source_texts = [source.read_text(encoding="utf-8") for source in sources if source.exists()]
    content, added = _merge_authorized_keys(existing_text, source_texts)
    short = log_format.home_short(ak_path)
    if content is None:
        logger.info(log_format.format_status(short, "変更なし (追加鍵 0 件)"))
        return False
    if not common.atomic_write_text(ak_path, content, mode=0o600):
        raise OSError(f"{short} の書き込みに失敗")
    logger.info(log_format.format_status(short, f"{added} 件の鍵を追加しました"))
    return True


def _merge_authorized_keys(existing_text: str, source_texts: Sequence[str]) -> tuple[str | None, int]:
    lines = existing_text.splitlines()
    known_keys = {key_data for line in lines if (key_data := _extract_key_data(line)) is not None}
    added = 0
    for text in source_texts:
        for line in text.splitlines():
            key_data = _extract_key_data(line)
            if key_data is not None and key_data not in known_keys:
                lines.append(line)
                known_keys.add(key_data)
                added += 1
    return ("\n".join(lines) + "\n", added) if added else (None, 0)


def _extract_key_data(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    m = _KEY_PATTERN.search(stripped)
    if m:
        return m.group(1)

    return stripped


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


if __name__ == "__main__":
    main()
