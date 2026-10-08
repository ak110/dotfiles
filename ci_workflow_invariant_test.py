"""`.github/workflows/`のpytestコマンドが指定するファイルとテスト定義の実在を検証する。"""

import ast

import pytest

from _test_helpers import (
    REPO_ROOT,
    direct_pytest_targets,
    load_workflow,
)


@pytest.fixture(scope="module", name="workflow_data")
def _workflow_fixture() -> dict[str, object]:
    return load_workflow()


def test_direct_pytest_targets_exist(workflow_data: dict[str, object]) -> None:
    """workflowのpytestコマンドが直接指定するリポジトリ内の対象は実在する。"""
    targets = direct_pytest_targets(workflow_data)
    assert targets
    for target in targets:
        # pytestのnode指定はファイルパスの後に::でクラス名やテスト名を持つ。
        filename, *selectors = target.split("::")
        path = REPO_ROOT / filename
        assert path.exists(), target
        if selectors:
            body = ast.parse(path.read_text(encoding="utf-8")).body
            for selector in selectors:
                definitions = {
                    node.name: node for node in body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                }
                name = selector.split("[", maxsplit=1)[0]
                assert name in definitions, target
                body = definitions[name].body
