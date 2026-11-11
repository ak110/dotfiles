"""生成元が見つからない診断を直接スクリプトとpost-applyで検証する。"""

import runpy
import traceback
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import codex_plugin_manifests


@pytest.mark.parametrize("entry", ["script", "post-apply"])
def test_missing_root_reports_recovery(monkeypatch, capsys, caplog, entry):
    """発生源の理由と次の操作が両方の出力経路で保たれる。"""
    monkeypatch.setattr(codex_plugin_manifests.common, "find_dotfiles_root", lambda: None)
    if entry == "script":
        monkeypatch.setattr("sys.argv", ["sync_codex_plugin_manifests.py"])
        with pytest.raises(FileNotFoundError):
            try:
                script = Path(__file__).resolve().parents[2] / "scripts/sync_codex_plugin_manifests.py"
                runpy.run_path(str(script), run_name="__main__")
            except FileNotFoundError:
                traceback.print_exc()
                raise
        output = capsys.readouterr().err
    else:
        # 通常の工程一覧から実際の生成工程だけを渡し、他の導入工程は起動しない。
        steps = [step for step in post_apply.DEFAULT_STEPS if step.name == "Codex plugin snapshot の生成"]
        assert len(steps) == 1
        results, _recommendations = post_apply.run([(steps[0].name, steps[0].run)])
        assert len(results) == 1 and not results[0].ok
        output = "\n".join(caplog.messages)
    assert "dotfilesの作業ツリーを解決できない" in output
    assert "次の操作: dotfilesの作業ツリーのパスを確かめ" in output
    assert "作業ツリー内で同じコマンドを再実行する" in output
    print(output)
