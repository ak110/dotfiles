"""Antigravity向け配布物の生成・検査とローカルプラグイン登録を行う。"""

import json
import os
import shlex
import stat
import subprocess
from pathlib import Path

from pytools._internal import common, post_apply_outcome

PLUGIN_DIRECTORY = "agent-toolkit-agy"
PLUGIN_NAMES = ("agent-toolkit", "agent-basics")
RULE_FILE_BYTES = 24_000
RULE_BUDGET_TOKENS = 20_000
EVENTS = ("SessionStart", "PreInvocation", "PreToolUse", "PostToolUse", "PostInvocation", "Stop")
_ALWAYS_ON = "---\ntrigger: always_on\n---\n\n"
_SOURCE_DIRECTORIES = ("skills", "agents", "share", "agent_toolkit", "scripts")
_EXCLUDED = frozenset({"__pycache__", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _command(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def _project_paths(value: object, target: Path) -> object:
    if isinstance(value, str):
        return value.replace("${CLAUDE_PLUGIN_ROOT}", str(target))
    if isinstance(value, list):
        return [_project_paths(item, target) for item in value]
    if isinstance(value, dict):
        return {key: _project_paths(item, target) for key, item in value.items()}
    return value


def _rules(source: Path) -> dict[Path, bytes]:
    """各ファイルを行境界で分割し、同じ本文を常時ロードへ投影する。"""
    chunks: list[str] = []
    current = ""
    body_limit = RULE_FILE_BYTES - len(_ALWAYS_ON.encode("utf-8"))
    for path in sorted((source / "rules").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        for line in text.splitlines(keepends=True):
            if len(line.encode("utf-8")) > body_limit:
                raise ValueError(f"Antigravity規範の1行がファイル上限を超えています: {path}")
            if len((current + line).encode("utf-8")) > body_limit:
                chunks.append(current)
                current = ""
            current += line
    if current:
        chunks.append(current)
    # agyの規範予算はモデル共通。日本語を含む本文へUTF-8の2 bytes/token換算を
    # 採り、英語中心の4 bytes/token換算より余裕を持って生成時に検査する。
    estimated_tokens = sum(
        (len(chunk.encode("utf-8")) + (len(_ALWAYS_ON.encode("utf-8")) if index else 0) + 1) // 2
        for index, chunk in enumerate(chunks)
    )
    if estimated_tokens > RULE_BUDGET_TOKENS:
        raise ValueError(
            f"Antigravity常時規範が予算を超えています: 推定{estimated_tokens} tokens。"
            "常時規範と条件付きスキルの配置を見直してください"
        )
    return {
        Path("rules") / ("AGENTS.md" if index == 0 else f"AGENTS-{index + 1:02d}.md"): (
            chunk if index == 0 else _ALWAYS_ON + chunk
        ).encode("utf-8")
        for index, chunk in enumerate(chunks)
    }


def plugin_directory(plugin_name: str) -> str:
    """元の名前空間を保つプラグインのagy生成先を返す。"""
    if plugin_name not in PLUGIN_NAMES:
        raise ValueError(f"未対応のAntigravity配布物: {plugin_name}")
    return f"{plugin_name}-agy"


def _split_skills(result: dict[Path, tuple[bytes, int]], target: Path, logical_name: str = "agent-toolkit") -> None:
    """名前空間ごとのrootで、長文スキルの入口と全文資料を共通生成する。"""
    # SKILL.md本体にも同じ読込上限がある。frontmatterを維持した入口から本文全文を
    # 明示的に読むことで、既存スキルの契約を途中で切り捨てずに届ける。
    for relative, (data, mode) in list(result.items()):
        if relative.name != "SKILL.md" or len(data) <= RULE_FILE_BYTES:
            continue
        text = data.decode("utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError(f"スキルのfrontmatterが不正です: {relative}")
        metadata, body = text[4:].split("\n---\n", 1)
        full_path = relative.parent / "references/antigravity-full-instructions.md"
        instruction = (
            f"---\n{metadata}\n---\n\n"
            f"<!-- read-source: {logical_name}:/{full_path.as_posix()} -->\n\n"
            f"このスキルを起動したら、`{target / full_path}`を全文読み、その指示に従う。\n"
            f"本文の相対パスはスキルディレクトリ`{target / relative.parent}`を基準に解決する。\n"
        )
        result[relative] = (instruction.encode("utf-8"), mode)
        result[full_path] = (body.encode("utf-8"), 0o644)


def outputs(root: Path, plugin_name: str = "agent-toolkit") -> dict[Path, tuple[bytes, int]]:
    """対象作業ツリー用の配布ファイル集合を、書き込み前に組み立てる。"""
    source = root / plugin_name
    target = (root / plugin_directory(plugin_name)).resolve()
    manifest = json.loads((source / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    result: dict[Path, tuple[bytes, int]] = {}
    directories = _SOURCE_DIRECTORIES if plugin_name == "agent-toolkit" else ("skills",)
    for directory in directories:
        for path in sorted((source / directory).rglob("*")):
            relative = path.relative_to(source)
            if not path.is_file() or _EXCLUDED.intersection(relative.parts) or path.name.endswith("_test.py"):
                continue
            data = path.read_bytes()
            if path.suffix == ".md":
                data = data.decode("utf-8").replace("\r\n", "\n").replace("${CLAUDE_PLUGIN_ROOT}", str(target)).encode("utf-8")
            result[relative] = (data, stat.S_IMODE(path.stat().st_mode))
    for name in ("pyproject.toml", "uv.lock") if plugin_name == "agent-toolkit" else ():
        result[Path(name)] = ((source / name).read_bytes(), 0o644)
    _split_skills(result, target, manifest["name"])
    result[Path("plugin.json")] = (_json({key: manifest[key] for key in ("name", "version", "description")}), 0o644)
    result[Path(".claude-plugin/plugin.json")] = ((source / ".claude-plugin/plugin.json").read_bytes(), 0o644)
    if plugin_name == "agent-basics":
        return result
    for role in sorted((source / "share").glob("*.subagent.md")):
        name = role.name.removesuffix(".subagent.md")
        definition = (
            f"---\nname: {name}\ndescription: agent-toolkitの{name}担当を実行する\n---\n\n"
            f"最初に`{target / 'share' / role.name}`を全文読み、担当の入力契約と手順に従う。\n"
            "必要な入力を委譲元から受け取ってから担当作業を実行する。\n"
        )
        result[Path("agents") / f"{name}.md"] = (definition.encode("utf-8"), 0o644)
    handlers = {}
    for event in EVENTS:
        handler = {
            "type": "command",
            "command": _command(
                [
                    "uv",
                    "run",
                    "--no-config",
                    "--no-project",
                    "--python",
                    "3.15",
                    "python",
                    str(target / "scripts/atk-hook"),
                    "antigravity",
                    event,
                ]
            ),
            "timeout": 30,
        }
        handlers[event] = [{"matcher": "*", "hooks": [handler]}] if event.endswith("ToolUse") else [handler]
    result[Path("hooks.json")] = (_json({"agent-toolkit": handlers}), 0o644)
    mcp = json.loads((source / ".mcp.json").read_text(encoding="utf-8"))
    servers = {name: value for name, value in mcp["mcpServers"].items() if name in {"agents_server", "pyfltr"}}
    # MCPの引数配列ではシェルを通さず、展開後の絶対パスをそのまま渡す。
    servers = _project_paths(servers, target)
    result[Path("mcp_config.json")] = (_json({"mcpServers": servers}), 0o644)
    result.update({path: (data, 0o644) for path, data in _rules(source).items()})
    return result


def check_diagnostics(root: Path, plugin_name: str = "agent-toolkit") -> tuple[str, ...]:
    """生成物を変更せず、欠落・余剰・内容やmodeの差を返す。"""
    expected = outputs(root, plugin_name)
    directory = plugin_directory(plugin_name)
    target = root / directory
    existing = {path.relative_to(target) for path in target.rglob("*") if path.is_file() or path.is_symlink()}
    diagnostics = []
    for relative in sorted(existing | set(expected)):
        path = target / relative
        if relative not in expected:
            diagnostics.append(f"{directory}/{relative}: 余剰")
        elif relative not in existing:
            diagnostics.append(f"{directory}/{relative}: 欠落")
        elif path.is_symlink() or (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) != expected[relative]:
            diagnostics.append(f"{directory}/{relative}: 内容またはmodeの差")
    return tuple(diagnostics)


def sync(root: Path, plugin_name: str = "agent-toolkit") -> bool:
    """生成済み配布rootを原本へ同期し、不要になったファイルを除く。"""
    expected = outputs(root, plugin_name)
    directory = plugin_directory(plugin_name)
    target = root / directory
    changed = False
    for path in target.rglob("*"):
        if (path.is_file() or path.is_symlink()) and path.relative_to(target) not in expected:
            path.unlink()
            changed = True
    for relative, (data, mode) in expected.items():
        path = target / relative
        if path.is_file() and not path.is_symlink() and path.read_bytes() == data and stat.S_IMODE(path.stat().st_mode) == mode:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            path.unlink()
        if not common.atomic_write_bytes(path, data, mode=mode, tag="Antigravity plugin"):
            raise OSError(f"Antigravity pluginの書き込みに失敗: {path}")
        changed = True
    return changed


def run(plugins: tuple[str, ...] = PLUGIN_NAMES) -> post_apply_outcome.PostApplyOutcome:
    """配布rootを更新し、他の登録を保ってplugins.jsonへ追加する。"""
    if common.resolve_executable("agy") is None:
        return post_apply_outcome.PostApplyOutcome()
    root = common.find_dotfiles_root()
    if root is None:
        raise FileNotFoundError("Antigravity pluginの生成元を解決できません")
    changed = False
    for plugin_name in plugins:
        if sync(root, plugin_name):
            changed = True
    registry = Path.home() / ".gemini/config/plugins.json"
    value = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {}
    if not isinstance(value, dict) or not isinstance(value.get("entries", []), list):
        raise ValueError(f"Antigravity plugin登録の構造が不正です: {registry}")
    entries = value.setdefault("entries", [])
    selected = [plugin_directory(name) for name in plugins]
    managed = {plugin_directory(name) for name in PLUGIN_NAMES}
    selected_path = str(root.resolve())
    previous = next(
        (
            item
            for item in entries
            if isinstance(item, dict)
            and set(item) == {"path", "include_only"}
            and item["path"] == selected_path
            and isinstance(item["include_only"], list)
            and all(isinstance(name, str) and name in managed for name in item["include_only"])
        ),
        None,
    )
    names = set(selected) | (set(previous["include_only"]) if previous is not None else set())
    entry = {
        "path": selected_path,
        "include_only": [plugin_directory(name) for name in PLUGIN_NAMES if plugin_directory(name) in names],
    }
    if previous != entry:
        if previous is None:
            entries.append(entry)
        else:
            entries[entries.index(previous)] = entry
        registry.parent.mkdir(parents=True, exist_ok=True)
        if not common.atomic_write_text(registry, _json(value).decode("utf-8"), tag="Antigravity plugins"):
            raise OSError(f"Antigravity plugin登録の書き込みに失敗: {registry}")
        changed = True
    notices = (
        (
            post_apply_outcome.PostApplyNotice(
                message="Antigravityのプラグインを更新しました。新しいagyセッションで反映を確認してください。"
            ),
        )
        if changed
        else ()
    )
    return post_apply_outcome.PostApplyOutcome(changed=changed, notices=notices)
