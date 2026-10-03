"""起動経路の回帰テストコードと実際の役割文書の対応を検証する。"""

import pytest

from agent_toolkit._hooks.pretooluse.test_support_test import _EXECUTE_REVIEW_TASK_NAMES, _SHARE_DIR

pytestmark = pytest.mark.repo_invariant


class TestExecuteReviewAlternateRouteAllowed:
    def test_guarded_task_references_exist(self) -> None:
        """回帰テストが与える役割名の実在を確認し、改名による空振りを検出する。"""
        for task_name in _EXECUTE_REVIEW_TASK_NAMES:
            assert (_SHARE_DIR / task_name).is_file()
