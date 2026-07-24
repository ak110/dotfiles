"""sync_generated_filesのテスト。"""

import sync_generated_files as subject


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
