"""agent-toolkit/agent_toolkit/_hooks/pretooluse/notices.py のテスト。

資料配送を対象外の境界へ差し替え、公開hookのexit code・stderr・stdoutを検証する。
"""

import pathlib

import pytest

from agent_toolkit._hooks import work_contract
from agent_toolkit._testing.pretooluse_support import (
    _additional_context,
    _codex_payload,
    _patch,
    _plan_file_state_env,
    _write_session_state,
)
from agent_toolkit._testing.pretooluse_support import (
    run_without_skill_notices as _run,
)


@pytest.fixture(autouse=True)
def _existing_work_contract(tmp_path, monkeypatch):
    """編集固有の通知を、作業契約を作成済みの状態で検証する。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: 編集通知の検証\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args, **_kwargs: path)


class TestCodexApplyPatchEditChecks:
    """Codexの`apply_patch`入力に対するホスト共通の編集内容の判定。"""

    @staticmethod
    def _ready_env(tmp_path: pathlib.Path) -> dict[str, str]:
        _write_session_state(tmp_path, "codex-edit", {})
        return _plan_file_state_env(tmp_path)

    def test_multiple_warnings_are_merged_into_single_json(self, tmp_path: pathlib.Path) -> None:
        patch_text = _patch(
            "*** Add File: one/uv.lock\n+version = 1\n",
            "*** Add File: two/uv.lock\n+version = 1\n",
        )
        result = _run(_codex_payload(patch_text, tmp_path), env_overrides=self._ready_env(tmp_path))

        assert result.returncode == 0
        assert len(result.stdout.strip().splitlines()) == 1
        context = _additional_context(result)
        assert context.count("uv add") == 1
        assert "one/uv.lock" in context and "two/uv.lock" in context
        assert "この通知は同一セッションで2件目である" not in context
        assert result.stderr == ""

    def test_mojibake_in_patch_warns(self, tmp_path: pathlib.Path) -> None:
        patch_text = _patch("*** Add File: docs/a.md\n+hello � world\n")
        result = _run(_codex_payload(patch_text, tmp_path), env_overrides=self._ready_env(tmp_path))

        assert result.returncode == 0
        assert "U+FFFD" in _additional_context(result)

    def test_unparsable_patch_passes_through(self, tmp_path: pathlib.Path) -> None:
        result = _run(_codex_payload("not a patch at all\n", tmp_path))

        assert result.returncode == 0
        assert result.stdout == ""

    def test_lockfile_path_in_patch_warns(self, tmp_path: pathlib.Path) -> None:
        patch_text = _patch("*** Update File: uv.lock\n@@\n-old\n+new\n")
        result = _run(_codex_payload(patch_text, tmp_path), env_overrides=self._ready_env(tmp_path))

        assert result.returncode == 0
        assert "uv.lock" in _additional_context(result)

    def test_delete_of_unprotected_file_passes(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "docs" / "old.md"
        target.parent.mkdir(parents=True)
        target.write_text("本文\n", encoding="utf-8")
        patch_text = _patch("*** Delete File: docs/old.md\n")
        result = _run(_codex_payload(patch_text, tmp_path))

        assert result.returncode == 0
