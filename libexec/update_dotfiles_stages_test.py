"""宣言した段へ未知の失敗を注入し、続行と中断の契約を検証する。"""

import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_stages as stages  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.mark.parametrize("name", ["update_bootstrap.sh", "update_bootstrap.ps1"])
def test_bootstrap_classification_matches_declarations(name: str) -> None:
    text = pathlib.Path(__file__).with_name(name).read_text(encoding="utf-8-sig")
    declarations = re.findall(r"(?:run_stage|Invoke-UpdateStage) '([^']+)' (abort|continue)", text)
    if name == "update_bootstrap.ps1":
        helpers = re.findall(r"Invoke-UpdateInstallStage '([^']+)'", text)
        assert helpers == ["uvの導入", "管理Pythonの導入"]
        declarations.extend((title, "abort") for title in helpers)
    expected = {item.name: item.abort for item in stages.STAGES if item.where == "bootstrap"}
    assert {title for title, _classification in declarations} == set(expected)
    for title, classification in declarations:
        assert (classification == "abort") == expected[title]


@pytest.mark.parametrize("missing_runner", [False, True])
def test_unknown_optional_stage_never_prevents_known_updates(monkeypatch: pytest.MonkeyPatch, missing_runner: bool) -> None:
    monkeypatch.setattr(update_dotfiles_runtime, "continued_failures", [])
    declared = [item.name for item in stages.STAGES if item.where == "executor"]
    visited: list[str] = []

    def run(item: stages.Stage) -> int:
        visited.append(item.name)
        if item.name == "future optional stage":
            raise RuntimeError("unknown failure")
        return 0

    runners = dict.fromkeys(declared, run)
    if missing_runner:
        monkeypatch.setattr(stages, "STAGES", (*stages.STAGES, stages.Stage("future optional stage")))
    else:
        runners["future optional stage"] = run
    result = stages.run_stages(runners)
    assert result == 0
    assert visited[: len(declared)] == declared
    assert update_dotfiles_runtime.continued_failures[-1].title == "future optional stage"
    assert update_dotfiles_runtime.finish(result) == 1


@pytest.mark.parametrize("failed", stages.STAGES, ids=lambda item: item.name)
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

    declared = [item.name for item in stages.STAGES if item.where == failed.where]
    result = stages.run_stages(dict.fromkeys(declared, run), where=failed.where, sleep=lambda _seconds: None)
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


def test_stage_exception_detail_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """段の未知例外の本文は、続行の記録と同期報告へ渡す前に共通の抜粋で伏せる。"""
    monkeypatch.setattr(update_dotfiles_runtime, "continued_failures", [])
    item = stages.Stage("future optional stage")

    def run(_item: stages.Stage) -> int:
        raise RuntimeError("password=synthetic-secret-0123456789")

    # pylint: disable-next=protected-access
    assert stages._run_stage(item, run, fatal=(), sleep=lambda _seconds: None) == 1
    assert "synthetic-secret" not in (update_dotfiles_runtime.last_stderr_tail or "")
    assert "synthetic-secret" not in (update_dotfiles_runtime.continued_failures[-1].detail or "")
