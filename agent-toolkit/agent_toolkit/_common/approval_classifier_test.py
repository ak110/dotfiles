"""soft拒否の一意な特定と、未知のホスト入力で許可しないことを確かめる。"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._common import approval_classifier


def _config(*, soft: list[str] | None = None, hard: list[str] | None = None) -> dict[str, list[str]]:
    return {
        "allow": [],
        "environment": [],
        "soft_deny": ["Local Cleanup: explicit permission is required"] if soft is None else soft,
        "hard_deny": ["Data Exfiltration: always blocked"] if hard is None else hard,
    }


def test_rule_condition_annotation_is_not_a_denial_label() -> None:
    config = _config(soft=["Local Cleanup [named+specifics]: requires explicit consent"])
    assert approval_classifier.classify("[Local Cleanup]", config, version="2.1.325").eligible


@pytest.mark.parametrize(
    ("reason", "config", "version"),
    [
        ("[Data Exfiltration]", _config(), "2.1.325"),
        ("[Local Cleanup]", _config(hard=["Local Cleanup: blocked even with consent"]), "2.1.325"),
        ("[Local Cleanup]", _config(soft=["Local Cleanup: first", "Local Cleanup: second"]), "2.1.325"),
        ("[Local Cleanup]", _config(hard=["Always block this action without a label"]), "2.1.325"),
        ("[Local Cleanup]", _config(hard=["$defaults"]), "2.1.325"),
        ("[Unknown]", _config(), "2.1.325"),
        ("[Local Cleanup][Data Exfiltration]", _config(), "2.1.325"),
        ("Auto mode could not evaluate this action and is blocking it for safety", _config(), "2.1.325"),
        ("Classifier unavailable", _config(), "2.1.325"),
        ("Don't retry this action: [Local Cleanup]", _config(), "2.1.325"),
        ("", _config(), "2.1.325"),
        ("[Local Cleanup]", _config(), "2.1.326"),
        ("[Local Cleanup]", {"soft_deny": ["Local Cleanup: requires consent"]}, "2.1.325"),
        ("[Local Cleanup]", {**_config(), "new_decision_field": True}, "2.1.325"),
    ],
)
def test_unclassified_or_hard_verdict_is_not_eligible(reason: str, config: object, version: str) -> None:
    assert not approval_classifier.classify(reason, config, version=version).eligible


def test_cli_defaults_do_not_hide_explicit_rules(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    executable = tmp_path / "claude-fixture"
    executable.touch()
    monkeypatch.setattr(approval_classifier.shutil, "which", lambda _name, **_kwargs: str(executable))
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "2.1.325 (Claude Code)\n", ""),
            subprocess.CompletedProcess([], 0, json.dumps(_config(soft=["Local Cleanup: modified condition"])), ""),
            subprocess.CompletedProcess([], 0, json.dumps(_config()), ""),
        ]
    )

    def run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return next(results)

    monkeypatch.setattr(approval_classifier.process_tree, "run_with_timeout", run)
    result = approval_classifier.inspect("[Local Cleanup]", cwd=str(tmp_path))
    assert result.eligible
    assert result.default_fields == ("hard_deny",)


@pytest.mark.parametrize(
    "failure", ["missing", "timeout", "exit", "json", "version", "defaults_exit", "defaults_json", "replacement"]
)
def test_snapshot_failure_cannot_authorize(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, failure: str) -> None:
    executable = tmp_path / "claude-fixture"
    executable.touch()
    monkeypatch.setattr(
        approval_classifier.shutil,
        "which",
        lambda _name, **_kwargs: None if failure == "missing" else str(executable),
    )
    calls = 0

    def run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise subprocess.TimeoutExpired("claude", 15)
        if calls == 1:
            return subprocess.CompletedProcess([], 0, "changed format" if failure == "version" else "2.1.325 (Claude Code)", "")
        if failure == "replacement":
            return subprocess.CompletedProcess(
                [], 0, json.dumps({**_config(), "environment": ["\ufffd"]}, ensure_ascii=False), ""
            )
        if failure.startswith("defaults"):
            return subprocess.CompletedProcess(
                [], int(calls == 3 and failure == "defaults_exit"), "invalid JSON" if calls == 3 else json.dumps(_config()), ""
            )
        return subprocess.CompletedProcess([], 1 if failure == "exit" else 0, "invalid JSON", "")

    monkeypatch.setattr(approval_classifier.process_tree, "run_with_timeout", run)
    assert not approval_classifier.inspect("[Local Cleanup]", cwd=str(tmp_path)).eligible


@pytest.mark.parametrize(
    "case", ["match", "flag", "missing", "changed", "defaults", "duplicate", "implicit", "partial", "unproven"]
)
def test_native_settings_must_match_public_snapshot(case: str) -> None:
    """flag、取得不能、hard/softの移動、未展開、重複を自動許可へ変換しない。"""
    config = _config()
    verdict = approval_classifier.classify("[Local Cleanup]", config, version="2.1.325")
    context: dict = {
        "flag_auto_mode_present": case == "flag",
        "auto_mode": {"soft_deny": config["soft_deny"], "hard_deny": config["hard_deny"]},
    }
    if case == "missing":
        context.pop("auto_mode")
    elif case == "changed":
        context["auto_mode"]["hard_deny"] = [*config["hard_deny"], *config["soft_deny"]]
    elif case == "defaults":
        context["auto_mode"]["hard_deny"] = ["$defaults"]
    elif case == "duplicate":
        context["auto_mode"]["soft_deny"] = config["soft_deny"] * 2
    elif case in {"implicit", "partial", "unproven"}:
        context["auto_mode"] = {} if case != "partial" else {"soft_deny": config["soft_deny"]}
        if case != "unproven":
            verdict = dataclasses.replace(verdict, default_fields=("soft_deny", "hard_deny"))
    assert approval_classifier.native_settings_match(verdict, context) is (case in {"match", "implicit", "partial"})
