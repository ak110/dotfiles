"""sync_generated_filesのテスト。"""

from pathlib import Path

import pytest
import sync_generated_files as subject


def test_runs_all_generators_in_order(monkeypatch) -> None:
    called: list[str] = []

    def fake_run(path: str) -> int:
        called.append(path)
        return 0

    monkeypatch.setattr(subject, "run_generator", fake_run)
    monkeypatch.setattr(subject, "sync_quality_principles", lambda: called.append("quality"))
    assert subject.main([]) == 0
    assert called == ["quality", *subject.GENERATORS]


def test_aggregates_failures_without_stopping(monkeypatch, capsys) -> None:
    called: list[str] = []

    def fake_run(path: str) -> int:
        called.append(path)
        return int(path in subject.GENERATORS[::2])

    monkeypatch.setattr(subject, "run_generator", fake_run)
    monkeypatch.setattr(subject, "sync_quality_principles", lambda: None)
    assert subject.main([]) == 1
    assert called == list(subject.GENERATORS)
    assert subject.GENERATORS[0] in capsys.readouterr().err


@pytest.mark.parametrize("arguments,code", [(["--help"], 0), (["--check"], 2), (["--unknown"], 2), (["extra"], 2)])
def test_input_is_handled_before_generation(monkeypatch, capsys, arguments, code) -> None:
    """公開された起動コマンドの入力検査では生成器の子プロセスを起動しない。"""
    calls = []
    monkeypatch.setattr(subject.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(subject, "sync_quality_principles", lambda: calls.append("quality"))
    with pytest.raises(SystemExit) as error:
        subject.main(arguments)
    assert error.value.code == code
    assert not calls
    output = capsys.readouterr()
    assert output.out if code == 0 else output.err


def test_quality_source_updates_normal_and_lightweight_delivery(tmp_path: Path) -> None:
    """原則の正本改訂が全同梱先へ届き、品質外の認可本文と軽量配送範囲を保つ。"""
    source = tmp_path / subject.QUALITY_SOURCE
    source.parent.mkdir(parents=True)
    source.write_text(
        "# 品質\n\n## 役割分担\n\n役割本文。\n\n## QCDと3段判定\n\n品質本文。\n\n"
        "### 設計の判定順\n\n順序本文。\n\n## 規定の区分と標示\n\n制約本文。\n\n"
        "## 事実と推論\n\n帰属本文。\n\n### 結果の返却\n\n返却の条件。\n\n"
        "## 要件と認可\n\n配送対象外。\n",
        encoding="utf-8",
    )
    normal = tmp_path / "agent-toolkit/rules/01-agent.md"
    lightweight = tmp_path / "agent-toolkit/share/agents-server-base.md"
    for path in (normal, lightweight):
        path.parent.mkdir(parents=True, exist_ok=True)
        keys = [key for relative, key, _, _ in subject.QUALITY_TARGETS if tmp_path / relative == path]
        blocks = "\n".join(
            f"<!-- quality-principles:{key}:start -->\n旧本文。\n<!-- quality-principles:{key}:end -->" for key in keys
        )
        path.write_text(f"# 固有規範\n\n認可本文。\n{blocks}\n末尾の規約。\n", encoding="utf-8")
    subject.sync_quality_principles(tmp_path)
    normal_text = normal.read_text(encoding="utf-8")
    lightweight_text = lightweight.read_text(encoding="utf-8")
    assert "役割本文。" in normal_text
    assert "#### 設計の判定順\n\n順序本文。" in normal_text
    assert "### 結果の返却\n\n返却の条件。" in normal_text
    assert "返却の条件。" in lightweight_text
    assert "帰属本文。" not in lightweight_text
    assert "品質本文。" not in lightweight_text
    for text in (normal_text, lightweight_text):
        assert "認可本文。" in text
        assert text.endswith("末尾の規約。\n")
        assert "配送対象外。" not in text
        assert "旧本文。" not in text
    source.write_text(source.read_text(encoding="utf-8").replace("返却の条件。", "改訂した返却の条件。"), encoding="utf-8")
    subject.sync_quality_principles(tmp_path)
    for path in (normal, lightweight):
        assert "改訂した返却の条件。" in path.read_text(encoding="utf-8")


def test_quality_sync_failure_stops_downstream_generation(monkeypatch, capsys) -> None:
    """品質核を同期できないときは、旧本文の配布物を生成する前に原因を返す。"""

    def fail() -> None:
        raise ValueError("factsの生成境界が一意でない")

    calls: list[str] = []
    monkeypatch.setattr(subject, "sync_quality_principles", fail)
    monkeypatch.setattr(subject, "run_generator", calls.append)
    assert subject.main([]) == 1
    assert not calls
    assert "factsの生成境界が一意でない" in capsys.readouterr().err
