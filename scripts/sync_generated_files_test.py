"""sync_generated_filesのテスト。"""

import pytest
import sync_generated_files as subject


def test_runs_all_generators_in_order(monkeypatch) -> None:
    called: list[str] = []

    def fake_run(path: str) -> int:
        called.append(path)
        return 0

    monkeypatch.setattr(subject, "run_generator", fake_run)
    assert subject.main([]) == 0
    assert called == list(subject.GENERATORS)


def test_aggregates_failures_without_stopping(monkeypatch, capsys) -> None:
    called: list[str] = []

    def fake_run(path: str) -> int:
        called.append(path)
        return int(path in subject.GENERATORS[::2])

    monkeypatch.setattr(subject, "run_generator", fake_run)
    assert subject.main([]) == 1
    assert called == list(subject.GENERATORS)
    assert subject.GENERATORS[0] in capsys.readouterr().err


@pytest.mark.parametrize("arguments,code", [(["--help"], 0), (["--check"], 2), (["--unknown"], 2), (["extra"], 2)])
def test_input_is_handled_before_generation(monkeypatch, capsys, arguments, code) -> None:
    """公開入口の入力検査では生成器の子プロセスを起動しない。"""
    calls = []
    monkeypatch.setattr(subject.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(SystemExit) as error:
        subject.main(arguments)
    assert error.value.code == code
    assert not calls
    output = capsys.readouterr()
    assert output.out if code == 0 else output.err
