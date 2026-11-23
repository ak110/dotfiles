import hashlib
import os
import re
from pathlib import Path
from typing import Any

_HASH = r"sha256:[0-9a-f]{64}"
_HOOKS = Path("hooks", "hooks.codex.json")


def bundle_hash(source: Path) -> str | None:
    # Codex 0.171のplugin_fingerprint.rsと同じ形式で計算する。  # pyfltr: allow-comment
    if not (source / _HOOKS).is_file():
        return None
    digest = hashlib.sha256(b"codex-plugin-bundle-v1")
    pending = [source]
    try:
        while pending:
            path = pending.pop()
            if path.name in {"__pycache__", ".venv"} or path.name.endswith((".pyc", ".egg-info")):
                continue
            relative = os.fsencode(str(path.relative_to(source))) if path != source else b""
            digest.update(len(relative).to_bytes(8, "little"))
            digest.update(relative)
            if path.is_symlink() or path.is_junction():
                return None
            if path.is_dir():
                digest.update(b"directory")
                pending.extend(sorted(path.iterdir(), key=lambda child: os.fsencode(child.name), reverse=True))
            elif path.is_file():
                length = path.stat().st_size
                digest.update(b"file")
                digest.update(length.to_bytes(8, "little"))
                with path.open("rb") as stream:
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(remaining, 65536))
                        if not chunk:
                            return None
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        return None
            else:
                return None
    except OSError:
        return None
    return "sha256:" + digest.hexdigest()


def approval_updates(
    result: dict[str, Any] | None, plugin_id: str, cache: Path, expected_bundle: str
) -> dict[str, dict[str, str]] | None:
    data = result.get("data") if result is not None else None
    if not isinstance(data, list):
        return None
    expected_path = (cache / _HOOKS).resolve()
    updates: dict[str, dict[str, str]] = {}
    for group in data:
        if not isinstance(group, dict) or not isinstance(hooks := group.get("hooks"), list):
            continue
        for hook in hooks:
            if (
                not isinstance(hook, dict)
                or hook.get("pluginId") != plugin_id
                or hook.get("source") != "plugin"
                or hook.get("enabled") is not True
                or hook.get("isManaged") is not False
                or hook.get("trustStatus") not in ("untrusted", "modified")
            ):
                continue
            key, source, combined = (hook.get(name) for name in ("key", "sourcePath", "currentHash"))
            if not isinstance(key, str) or not isinstance(source, str) or not isinstance(combined, str):
                return None
            if not key.startswith(f"{plugin_id}:hooks/hooks.codex.json:") or Path(source).resolve() != expected_path:
                return None
            declaration, _, content = combined.partition("/")
            if not re.fullmatch(_HASH, declaration) or content != expected_bundle:
                return None
            approval = {"trusted_hash": declaration, "trusted_content_hash": content}
            if key in updates and updates[key] != approval:
                return None
            updates[key] = approval
    return updates
