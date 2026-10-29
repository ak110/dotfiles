"""OpenCodeのグローバル検出先へtoolkitと品質スキル、常時規範を同期する。

OpenCodeの公開仕様（https://opencode.ai/docs/skills/ と /rules/）を使う。
ユーザーの既存規範・設定キーと独自スキルを保持する。
"""

import logging
import os
import re
import shutil
from pathlib import Path

import pytilpack.jsonc
import yaml

from pytools._internal import common, log_format, post_apply_outcome, setup_opencode_cli, sync_agent_toolkit_rules

logger = logging.getLogger(__name__)

_BEGIN = '<atk-auto source="dotfiles" kind="opencode-host">'
_END = "</atk-auto><!-- opencode-host -->"
_OWNER = ".dotfiles-opencode-skill"
_PLUGINS = ("agent-toolkit", "agent-basics")


def _config_dir() -> Path:
    # OpenCodeはWindowsでもXDG形式のホーム配下を使う（公式troubleshootingのStorage/Config）。
    if custom := os.environ.get("OPENCODE_CONFIG_DIR"):
        return Path(custom).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode"


def _skill_body(path: Path, plugin: str) -> str:
    text = path.read_text(encoding="utf-8")
    matched = re.fullmatch(r"---\n(.*?)\n---(?:\n(.*))?", text, re.DOTALL)
    if matched is None:
        raise ValueError(f"skillのfrontmatterを取得できない: {path}")
    frontmatter = yaml.safe_load(matched[1])
    if not isinstance(frontmatter, dict) or frontmatter.get("name") != path.parent.name:
        raise ValueError(f"skill名がディレクトリ名と一致しない: {path}")
    name = f"{plugin}-{path.parent.name}"
    if len(name) > 64 or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
        raise ValueError(f"OpenCodeのskill名として受理できない: {name}")
    frontmatter["name"] = name
    metadata = dict(frontmatter.get("metadata") or {})
    if frontmatter.get("disable-model-invocation") is True:
        metadata["opencode/autoinvoke"] = "false"
    if frontmatter.get("user-invocable") is not False:
        metadata["opencode/slash"] = "true"
    if metadata:
        frontmatter["metadata"] = metadata
    return "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n" + (matched[2] or "")


def _rules_body(root: Path) -> str:
    toolkit = root / "agent-toolkit"
    basics = root / "agent-basics"
    lines = [
        _BEGIN,
        "# OpenCodeでのagent-toolkit",
        "",
        f"agent-toolkit plugin root: `{toolkit.as_posix()}`。agent-basics plugin root: `{basics.as_posix()}`。",
        "スキル本文の`${CLAUDE_PLUGIN_ROOT}`は、そのスキルを提供するpluginの上記rootへ読み替える。",
        "`${CLAUDE_SKILL_DIR}`は、そのplugin rootの`skills/<元のスキル名>`へ読み替える。",
        "スキル本文の相対参照も、対応するplugin rootの`skills/<元のスキル名>`を起点に解決する。",
        "`agent-toolkit:<名前>`はOpenCodeの`agent-toolkit-<名前>`、"
        "`agent-basics:<名前>`は`agent-basics-<名前>`に対応し、`skill`ツールのnameへ渡す。",
        "参照資料と実行資源は対応するplugin rootから解決する。",
        "モデルの自動選択を無効にしたスキルは、ユーザーが明示した時にだけ同じ名前で読み込む。",
        "この導入はスキルと規範を提供する。MCPサーバーとClaude Code用hookの登録は含まない。",
        "OpenCodeからagents_serverを呼ばない。ホストに無い操作が必要なら、"
        "必要な操作と未完了の成果をユーザーまたは委譲元へ返す。",
        "",
    ]
    lines.extend(sync_agent_toolkit_rules.wrapped_body(rule) for rule in sorted((toolkit / "rules").glob("*.md")))
    lines.append(_END)
    return "\n".join(lines) + "\n"


def _write(path: Path, data: bytes) -> bool:
    if path.is_file() and path.read_bytes() == data:
        return False
    if path.is_symlink():
        raise ValueError(f"管理対象がシンボリックリンクであるため同期できない: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not common.atomic_write_bytes(path, data, tag="OpenCode toolkit"):
        raise OSError(f"書き込みに失敗: {path}")
    return True


def sync(root: Path, destination: Path) -> bool:
    """配布元を検証して同期し、OpenCodeのユーザー所有ファイルを保持する。"""
    expected: dict[Path, bytes] = {}
    skill_names: set[str] = set()
    for plugin in _PLUGINS:
        skills = root / plugin / "skills"
        if not skills.is_dir():
            raise ValueError(f"配布元のskillsが見つからない: {skills}")
        for skill in sorted(skills.iterdir()):
            if not (skill / "SKILL.md").is_file():
                continue
            name = f"{plugin}-{skill.name}"
            target = destination / "skills" / name
            marker = target / _OWNER
            if target.is_symlink():
                raise ValueError(f"配置先skillがシンボリックリンクであるため同期できない: {target}")
            if target.exists() and (not marker.is_file() or marker.read_text(encoding="utf-8") != plugin):
                raise ValueError(f"ユーザー所有のskillと配置先が重なる: {target}")
            skill_names.add(name)
            for source in sorted(skill.rglob("*")):
                relative = source.relative_to(skill)
                if not source.is_file() or "__pycache__" in relative.parts or source.suffix == ".pyc":
                    continue
                data = _skill_body(source, plugin).encode("utf-8") if relative == Path("SKILL.md") else source.read_bytes()
                expected[target / relative] = data
            expected[marker] = plugin.encode("utf-8")
    if not (root / "agent-toolkit" / "rules" / "01-agent.md").is_file():
        raise ValueError("agent-toolkitの配布元規範が見つからない")
    rules = destination / "agent-toolkit.md"
    if rules.is_file() and not rules.read_text(encoding="utf-8").startswith(_BEGIN + "\n"):
        raise ValueError(f"ユーザー所有の規範と配置先が重なる: {rules}")
    expected[rules] = _rules_body(root).encode("utf-8")
    config = destination / "opencode.jsonc"
    if not config.exists() and (destination / "opencode.json").exists():
        config = destination / "opencode.json"
    original = pytilpack.jsonc.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    if not isinstance(original, dict):
        raise ValueError(f"OpenCode設定がJSON objectでない: {config}")
    instructions = original.get("instructions", [])
    if not isinstance(instructions, list) or not all(isinstance(item, str) for item in instructions):
        raise ValueError(f"OpenCode設定のinstructionsが文字列配列でない: {config}")
    updated = {**original, "instructions": [*instructions, rules.as_posix()]}
    config_changed = rules.as_posix() not in instructions

    changed = False
    for path, data in expected.items():
        changed = _write(path, data) or changed
    if config_changed:
        if not common.write_settings_hybrid(config, original, updated, tag="OpenCode toolkit"):
            raise OSError(f"OpenCode設定の書き込みに失敗: {config}")
        changed = True
    # 専用markerのあるディレクトリだけを所有し、改名・廃止したスキルと付属資源を同期する。
    for marker in sorted((destination / "skills").glob(f"*/{_OWNER}")):
        plugin = marker.read_text(encoding="utf-8")
        if plugin not in _PLUGINS or not marker.parent.name.startswith(f"{plugin}-"):
            continue
        if marker.parent.name not in skill_names:
            shutil.rmtree(marker.parent)
            changed = True
            continue
        for existing in sorted(marker.parent.rglob("*"), reverse=True):
            if existing.is_file() and existing not in expected:
                existing.unlink()
                changed = True
            elif existing.is_dir() and not any(existing.iterdir()):
                existing.rmdir()
    return changed


def run() -> post_apply_outcome.PostApplyOutcome:
    """OpenCode CLIの導入後にグローバルスキルと規範を同期する。"""
    if setup_opencode_cli.find_launcher() is None:
        logger.info(log_format.format_status("OpenCode toolkit", "CLIが無いためスキップ。`update-dotfiles`で導入を再試行する"))
        return post_apply_outcome.PostApplyOutcome()
    root = common.find_dotfiles_root()
    if root is None:
        return post_apply_outcome.PostApplyOutcome(
            failure="dotfilesの配布元が見つからない", next_action="dotfilesの作業ツリーから`update-dotfiles`を再実行する"
        )
    return post_apply_outcome.PostApplyOutcome(changed=sync(root, _config_dir()))
