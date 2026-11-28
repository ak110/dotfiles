"""Pylintの抑止指定に関するリポジトリ全体の契約テスト。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.verification_scan_rules import scan_files

REPO_ROOT = Path(__file__).resolve().parent
FORBIDDEN_TOKEN = "duplicate-code"
PYLINT_DIRECTIVE = re.compile(r"#\s*pylint:\s*(?:disable|enable)\s*=\s*(?P<messages>[^#\r\n]*)")


def _forbidden_suppressions(source: str) -> list[int]:
    violations = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        for directive in PYLINT_DIRECTIVE.finditer(line):
            messages = directive.group("messages").split(",")
            if FORBIDDEN_TOKEN in (message.strip() for message in messages):
                violations.append(line_number)
    return violations


def _tracked_python_files() -> list[Path]:
    return scan_files(__file__)


def test_forbidden_suppression_is_detected_at_each_list_position() -> None:
    prefix = "# pylint: disable="
    source = "\n".join(
        (
            prefix + FORBIDDEN_TOKEN + ",unused-import",
            prefix + "unused-import, " + FORBIDDEN_TOKEN + ",wrong-import-order",
            prefix + "unused-import," + FORBIDDEN_TOKEN,
            prefix + "unused-import,wrong-import-order",
        )
    )

    assert _forbidden_suppressions(source) == [1, 2, 3]


@pytest.mark.cpu_limit(1.5, reason="全追跡Python本文のPylint抑止を走査する横断契約。Linux CIのCPU0.54秒を基準に余裕を確保")
def test_tracked_python_files_do_not_suppress_duplicate_code() -> None:
    violations = [
        f"{path.relative_to(REPO_ROOT)}:{line_number}"
        for path in _tracked_python_files()
        for line_number in _forbidden_suppressions(path.read_text(encoding="utf-8"))
    ]

    assert violations == []
