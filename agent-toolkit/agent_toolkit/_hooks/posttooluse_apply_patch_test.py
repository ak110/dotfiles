"""agent-toolkit/agent_toolkit/_hooks/posttooluse.pyがCodexの`apply_patch`を計画ファイルの編集として扱う挙動のテスト。"""

# 独立したフックシナリオ間で状態ディレクトリ初期化を同形に保つ。

import json
import os
import pathlib
import subprocess

import pytest

from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import _read_state

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"


def _run(payload: dict, *, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("posttooluse",),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )


def _patch(*sections: str) -> str:
    """Codexの`apply_patch`入力本文を組み立てる。"""
    return "*** Begin Patch\n" + "".join(sections) + "*** End Patch\n"


def _codex_payload(patch_text: str, cwd: pathlib.Path, session_id: str) -> dict:
    """成功した`apply_patch`のPostToolUse payloadを組み立てる。"""
    return {
        "session_id": session_id,
        "tool_name": "apply_patch",
        "tool_input": {"command": patch_text},
        "tool_response": "applied",
        "cwd": str(cwd),
        "turn_id": "turn-1",
    }


class TestCodexApplyPatchPlanFile:
    """成功したCodex `apply_patch`の計画ファイルを記録し、追加時だけ検査案内を返す。"""

    def test_added_plan_file_returns_check_guidance(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """追加した計画ファイルだけが検査案内を返し、更新は返さない。"""
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        added = plans / "added.md"
        updated = plans / "updated.md"
        updated.write_text("# 旧\n", encoding="utf-8")
        env_state = {"plan_mode_skill_invoked": True}
        for session_id, patch_text, expected in (
            ("codex-plan-add", _patch(f"*** Add File: {added}\n+# 計画\n"), True),
            ("codex-plan-update", _patch(f"*** Update File: {updated}\n@@\n-# 旧\n+# 新\n"), False),
        ):
            state_path = tmp_path / f"claude-agent-toolkit-{session_id}.json"
            state_path.write_text(json.dumps(env_state), encoding="utf-8")
            result = _run(_codex_payload(patch_text, tmp_path, session_id), state_dir=tmp_path)
            assert ("書き込み後の検査を実行する" in result.stdout) is expected
            assert _read_state(tmp_path, session_id).get("current_plan_file_path") is not None
