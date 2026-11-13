"""宣言した段へ未知の失敗を注入し、続行と中断の契約を検証する。"""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_stages as stages  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.mark.parametrize("failed", [item for item in stages.STAGES if item.where == "executor"], ids=lambda item: item.name)
@pytest.mark.parametrize("error", [RuntimeError("injected"), subprocess.TimeoutExpired("injected", 1)])
def test_injected_failure_preserves_progress(failed: stages.Stage, error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    """新しい段も、宣言に従って再試行または最後まで続行する。"""
    monkeypatch.setattr(update_dotfiles_runtime, "continued_failures", [])
    visited: list[str] = []

    def run(item: stages.Stage) -> int:
        visited.append(item.name)
        if item.name == failed.name:
            raise error
        return 0

    declared = [item.name for item in stages.STAGES if item.where == "executor"]
    result = stages.run_stages(dict.fromkeys(declared, run), sleep=lambda _seconds: None)
    assert {
        "git timeout設定の検証",
        "更新ロックの取得",
        "dotfiles自身のGit更新",
        "chezmoi init (テンプレート再展開)",
        "chezmoi apply (post-apply実行)",
        "uvの導入",
        "管理Pythonの導入",
    } == stages.ABORT_STAGES
    if failed.abort:
        assert result == 1
        assert visited.count(failed.name) == failed.retries + 1
        assert visited[-1] == failed.name
    else:
        assert result == 0
        assert visited == declared
        assert len(update_dotfiles_runtime.continued_failures) == 1
        assert update_dotfiles_runtime.finish(result) == 1
