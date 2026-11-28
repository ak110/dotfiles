import ast
import pathlib
import re

import pytest
from agent_toolkit._testing import isolation

from scripts.verification_scan_rules import scan_files


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "全製品Pythonから環境変数の定義をAST抽出する不変条件。試験専用"
        + "のコードは既に除外している。同群Linux CI所有CPU最大0."
        + "372秒。"
    ),
)
def test_session_environment_inputs_are_isolated() -> None:
    """入力名の定義と直接参照を収集し、ホスト識別子の登録漏れを検出する。

    定数経由の読取りも対象とするため、環境変数の呼出し引数だけでなく製品の文字列定義を走査する。
    テスト専用パッケージとテスト入力は製品入力の母集団から除く。
    """
    root = pathlib.Path(__file__).parent / "agent_toolkit"
    pattern = re.compile(
        r"AGENT_TOOLKIT_[A-Z_]*(?:SESSION|DELEGATED|HANDOFF)[A-Z_]*"
        r"|AGENT_TOOLKIT_(?:COMMIT_CLAUDE_ACTOR|COMMIT_HOOKS|RUN_SKILL_ID)"
        r"|CLAUDE_CODE_SESSION_ID|CLAUDE_ENV_FILE|CODEX_(?:THREAD|SESSION)_ID"
    )
    names: dict[str, list[str]] = {}
    for path in scan_files(__file__):
        source = path.read_text(encoding="utf-8")
        if not pattern.search(source):
            continue
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if pattern.fullmatch(node.value):
                names.setdefault(node.value, []).append(f"{path.relative_to(root)}:{node.lineno}")
    assert names, "会話・委譲の環境変数入力が収集されなかった"
    missing = {name: paths for name, paths in names.items() if name not in isolation.DEVELOPMENT_SESSION_ENVIRONMENT_NAMES}
    assert not missing, f"製品の会話・委譲入力を共通隔離へ登録する: {missing}"
