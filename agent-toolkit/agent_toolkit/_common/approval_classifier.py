"""観測済みホストの公開設定から、承認の適用候補となるsoft拒否だけを特定する。"""

from __future__ import annotations

import collections
import dataclasses
import json
import pathlib
import re
import shutil
import subprocess

import pydantic

from agent_toolkit._common.operation_approvals import fingerprint

# PermissionDeniedのreasonは構造化された区分を持たない。観測していない版の書式を推測して許可しない。
OBSERVED_CLAUDE_VERSION = "2.1.325"
_LABELS = re.compile(r"\[[^\[\]\r\n]+\](?:[ \t]*,?[ \t]*\[[^\[\]\r\n]+\])*")
_VERSION = re.compile(r"(\d+\.\d+\.\d+) \(Claude Code\)")


class _EffectiveConfig(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(strict=True, extra="forbid")

    allow: list[str]
    soft_deny: list[str]
    hard_deny: list[str]
    environment: list[str]


@dataclasses.dataclass(frozen=True)
class Classification:
    """実行の承認と区別し、許可候補の可否と版・ラベルの一致根拠を保持する。"""

    eligible: bool
    diagnostic: str
    labels: tuple[str, ...] = ()
    version: str | None = None
    config_hash: str | None = None
    soft_labels: tuple[str, ...] = ()
    hard_labels: tuple[str, ...] = ()
    default_fields: tuple[str, ...] = ()


def _rule_labels(rules: list[str]) -> list[str] | None:
    labels: list[str] = []
    for rule in rules:
        # 公開configの規則は「Label [条件]: 本文」の形も持つ。拒否ラベルは条件注記より前の名前を使う。
        matched = re.match(r"([^\[\]:\r\n]+)(?:\[[^\r\n]*\])?:\s*\S", rule)
        if matched is None:
            return None
        label = matched.group(1).strip()
        if not label:
            return None
        labels.append(label)
    return labels


def classify(reason: str, config: object, *, version: str) -> Classification:
    """拒否の全ラベルがsoft側へ一意に対応するかを確かめる。"""
    if version != OBSERVED_CLAUDE_VERSION:
        return Classification(False, "拒否書式を観測したClaude Codeの版と一致しない", version=version)
    if _LABELS.fullmatch(reason) is None:
        return Classification(False, "拒否理由が観測済みのラベル列の書式ではない", version=version)
    labels = tuple(re.findall(r"\[([^\[\]\r\n]+)\]", reason))
    try:
        effective = _EffectiveConfig.model_validate(config)
    except pydantic.ValidationError:
        return Classification(False, "実効設定の構造を確認できない", labels, version)
    soft = _rule_labels(effective.soft_deny)
    hard = _rule_labels(effective.hard_deny)
    if soft is None or hard is None:
        return Classification(False, "無名または未展開の拒否規則があり区分を確定できない", labels, version)
    counts = collections.Counter(soft)
    if len(set(labels)) != len(labels) or any(counts[label] != 1 or label in hard for label in labels):
        return Classification(False, "拒否ラベルがsoft側へ一意に対応しないかhard側と重なる", labels, version)
    return Classification(
        True,
        "全拒否ラベルが実効設定のsoft側だけへ一意に対応した",
        labels,
        version,
        fingerprint(effective.model_dump()),
        tuple(soft),
        tuple(hard),
    )


def native_settings_match(classified: Classification, context: dict) -> bool:
    """稼働会話のhard/soft名が公開CLIの名前と一致する場合だけ自動承認の候補にする。"""
    if not classified.eligible or context.get("flag_auto_mode_present") is not False:
        return False
    mode = context.get("auto_mode")
    if not isinstance(mode, dict) or not set(mode) <= {"soft_deny", "hard_deny"}:
        return False
    for field in ("soft_deny", "hard_deny"):
        if field not in mode:
            if field not in classified.default_fields:
                return False
            continue
        if not isinstance(mode[field], list) or not all(isinstance(rule, str) for rule in mode[field]):
            return False
        labels = _rule_labels(mode[field])
        expected = classified.soft_labels if field == "soft_deny" else classified.hard_labels
        if labels is None or sorted(labels) != sorted(expected):
            return False
    return True


def inspect(reason: str, *, cwd: str) -> Classification:
    """公開CLIで現在の版と実効設定を取得し、取得失敗を許可へ変換しない。"""
    if _LABELS.fullmatch(reason) is None:
        return Classification(False, "拒否理由が観測済みのラベル列の書式ではない")
    executable = shutil.which("claude")
    if executable is None:
        return Classification(False, "Claude Codeの公開CLIが見つからない")
    try:
        executable = str(pathlib.Path(executable).resolve(strict=True))
        version_result = subprocess.run(
            [executable, "--version"],
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        matched = _VERSION.fullmatch(version_result.stdout.strip()) if version_result.returncode == 0 else None
        if matched is None:
            return Classification(False, "Claude Codeの版を確認できない")
        version = matched.group(1)
        if version != OBSERVED_CLAUDE_VERSION:
            return Classification(False, "拒否書式を観測したClaude Codeの版と一致しない", version=version)
        result = subprocess.run(
            [executable, "auto-mode", "config"],
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return Classification(False, "公開CLIから実効設定を取得できない", version=version)
        config = json.loads(result.stdout)
        classified = classify(reason, config, version=version)
        if not classified.eligible:
            return classified
        defaults = subprocess.run(
            [executable, "auto-mode", "defaults"],
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        if defaults.returncode != 0:
            return Classification(False, "公開CLIからデフォルトの拒否規則を取得できない", version=version)
        baseline = _EffectiveConfig.model_validate(json.loads(defaults.stdout))
        # merged readは未指定の規則を展開しない。同じ実行版の公開defaultsと実効configが等しい項目だけ補える。
        fields = tuple(field for field in ("soft_deny", "hard_deny") if config[field] == getattr(baseline, field))
        return dataclasses.replace(classified, default_fields=fields)
    except OSError, UnicodeError, subprocess.TimeoutExpired, ValueError:
        return Classification(False, "公開CLIの実効設定または版の取得に失敗した")
