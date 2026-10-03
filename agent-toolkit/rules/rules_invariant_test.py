"""共通ルールに役割固有の条文が含まれないことを確かめる。"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.repo_invariant

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_rules_files_have_no_role_specific_sentences() -> None:
    pattern = re.compile(r"^(?:- |\d+\. )?(?:委譲先|サブエージェント|メインエージェント)は")
    actual = {
        line
        for path in (_PLUGIN_ROOT / "rules").glob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if pattern.match(line)
    }
    assert not actual


def test_rules_files_have_no_main_only_capabilities() -> None:
    prohibited = (
        "`AskUserQuestion`で",
        "ユーザーへ報告",
        "UWIへ記録",
        "を起動して登録",
        "を起動してUWI",
    )
    common = "\n".join(path.read_text(encoding="utf-8") for path in (_PLUGIN_ROOT / "rules").glob("*.md"))
    assert not any(value in common for value in prohibited)
