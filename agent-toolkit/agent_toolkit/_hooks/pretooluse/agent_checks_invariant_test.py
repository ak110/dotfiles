"""起動経路の回帰テストコードと実際の役割文書の対応を検証する。"""

import pathlib

from agent_toolkit._common import task_documents
from agent_toolkit._testing.pretooluse_support import _EXECUTE_REVIEW_TASK_NAMES

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[3]


class TestExecuteReviewAlternateRouteAllowed:
    def test_guarded_task_references_exist(self) -> None:
        """回帰テストが与える役割名の文書が実在することを確かめ、改名で文書が無くなりテストが何も検証しなくなる状態を検出する。"""
        for task_name in _EXECUTE_REVIEW_TASK_NAMES:
            assert task_documents.find_role_document(_PLUGIN_ROOT, task_name) is not None, task_name
