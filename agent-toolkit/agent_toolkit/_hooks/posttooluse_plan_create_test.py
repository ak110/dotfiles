"""公開された計画ファイル作成入口のPostToolUse記録を検証する。"""

import pathlib

from agent_toolkit._hooks import posttooluse
from agent_toolkit._hooks.bash_command_parser import extract_execution_segments


def test_run_script_plan_create_records_current_plan_file_path(
    monkeypatch,
    tmp_path: pathlib.Path,
) -> None:
    """`atk run-script plan-create`の標準出力から現在の計画パスを記録する。"""
    plan_path = tmp_path / ".claude" / "plans" / "15-0713_process-wi_レーン01.md"
    plan_path.parent.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(posttooluse, "_record_plan_file", lambda session_id, path: recorded.append((session_id, path)))

    segments = extract_execution_segments("atk run-script plan-create -- --main-source /tmp/main.md --lane lane-01")
    posttooluse._record_created_plan_file(  # pylint: disable=protected-access
        "plan-path-run-script", segments, {"stdout": f"{plan_path}\n"}
    )

    assert recorded == [("plan-path-run-script", str(plan_path))]
