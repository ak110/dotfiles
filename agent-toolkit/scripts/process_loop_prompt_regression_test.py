"""process-loopがCodexへ渡す継続条件を検証する。"""

import pathlib

import _atk_mq_process_loop as subject


def test_codex_prompt_preserves_goal_across_infrastructure_recovery() -> None:
    """実行基盤の修正又は再起動後も同じgoalの処理へ戻す。"""
    prompt = subject._build_process_loop_prompt(  # pylint: disable=protected-access
        pathlib.Path("/repo"),
        "github.com/example/repo",
        "codex",
    )

    assert "実行基盤の不具合" in prompt
    assert "未完了のフィードバック処理へ戻って" in prompt
    assert "再起動後も同じgoalを再開" in prompt
    assert prompt.count("/goal ") == 1
