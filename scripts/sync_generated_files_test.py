"""sync_generated_filesのテスト。"""

import pathlib
import tomllib

import sync_generated_files as subject


def test_sync_targets_cover_agent_toolkit_skills_and_share() -> None:
    """skillsとshareの各実在階層を生成同期の起動対象に含める。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    targets = config["tool"]["pyfltr"]["custom-commands"]["sync-generated-files"]["targets"]
    representatives = (
        pathlib.Path("agent-toolkit/share/rules-subagent.md"),
        pathlib.Path("agent-toolkit/skills/process-wi/SKILL.md"),
        pathlib.Path("agent-toolkit/skills/plan-mode/references/plan-file-standards.md"),
    )

    assert all(any(path.match(pattern) for pattern in targets) for path in representatives)


def test_runs_all_generators_in_order(monkeypatch) -> None:
    called: list[str] = []

    def fake_run(path: str) -> int:
        called.append(path)
        return 0

    monkeypatch.setattr(subject, "run_generator", fake_run)
    assert subject.main() == 0
    assert called == list(subject.GENERATORS)


def test_aggregates_failures_without_stopping(monkeypatch, capsys) -> None:
    called: list[str] = []

    def fake_run(path: str) -> int:
        called.append(path)
        return int(path in subject.GENERATORS[::2])

    monkeypatch.setattr(subject, "run_generator", fake_run)
    assert subject.main() == 1
    assert called == list(subject.GENERATORS)
    assert subject.GENERATORS[0] in capsys.readouterr().err
