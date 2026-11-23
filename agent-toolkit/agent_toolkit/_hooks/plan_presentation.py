import hashlib
import pathlib
import re
import subprocess
import sys

from agent_toolkit._common.process_tree import run_with_timeout
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._plan.path_kinds import is_plan_main_file

_CHECKED = "presented_plan_structure"
_TARGET = re.compile(r"^- 対象リポジトリ:\s*`([^`\n]+)`", re.MULTILINE)


def check_before_presentation(session_id: str, cwd: str, presented: object = None) -> str | None:
    if not session_id:
        return None
    state = read_state(session_id)
    filename = state.get("current_plan_file_path")
    if not state.get("plan_mode_skill_invoked") or not isinstance(filename, str) or not is_plan_main_file(filename):
        return None
    path = pathlib.Path(filename)
    try:
        content = path.read_bytes()
        if isinstance(presented, str):
            saved_text = content.decode("utf-8-sig").replace("\r\n", "\n")
            if presented.replace("\r\n", "\n").removesuffix("\n") != saved_text.removesuffix("\n"):
                return "提示する本文が現在の計画ファイルと一致しない。提示する全文を保存してから提示し直す。"
        digest = hashlib.sha256(content).hexdigest()
        if state.get(_CHECKED) == [filename, digest]:
            return None
        argv = [
            sys.executable,
            "-m",
            "agent_toolkit.atk",
            "run-script",
            "plan-check",
            "--",
            "--reject-migration-warnings",
            filename,
        ]
        target = _TARGET.search(content.decode("utf-8-sig"))
        work_dir = target[1] if target is not None and pathlib.Path(target[1]).is_absolute() else cwd
        if work_dir:
            argv.extend(["--work-dir", work_dir])
        result = run_with_timeout(
            argv, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        if result.returncode:
            diagnostic = result.stderr.strip() or result.stdout.strip()
            preview = diagnostic if len(diagnostic) <= 1200 else f"先頭1200文字だけ表示: {diagnostic[:1200]}"
            return f"計画の構造検査が終了{result.returncode}: {preview}"
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return "計画が構造検査の間に変更された。"
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        return f"計画の構造検査を完了できない: {error}"
    update_state(session_id, lambda current: {**current, _CHECKED: [filename, digest]})
    return None
