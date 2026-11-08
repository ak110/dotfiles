"""共通ルールに役割固有の条文が含まれないことを確かめる。"""

from __future__ import annotations

import pathlib
import re

# 全主体へ常時配る規範は本テストと同じディレクトリに置く。
_COMMON_RULES_DIR = pathlib.Path(__file__).resolve().parent


def test_rules_files_have_no_role_specific_sentences() -> None:
    pattern = re.compile(r"^(?:- |\d+\. )?(?:委譲先|サブエージェント|メインエージェント)は")
    actual = {
        line
        for path in _COMMON_RULES_DIR.glob("*.md")
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
    common = "\n".join(path.read_text(encoding="utf-8") for path in _COMMON_RULES_DIR.glob("*.md"))
    assert not any(value in common for value in prohibited)
