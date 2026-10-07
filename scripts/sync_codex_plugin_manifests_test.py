"""`scripts/sync_codex_plugin_manifests.py`（生成器の入口）のテスト。

生成と検査の処理は`pytools/_internal/codex_plugin_manifests_test.py`が確かめる。
本ファイルは入口が持つ引数の解釈と終了コードの契約だけを確かめる。
"""

from pathlib import Path

import pytest
import sync_codex_plugin_manifests as entry

from pytools._internal import codex_plugin_manifests


def test_check_exit_codes_and_reports_differences(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`--check`は差が無ければ0、差があれば差を標準エラーへ出力して1を返し、同期しない。"""
    diagnostics: tuple[str, ...] = ()
    monkeypatch.setattr(codex_plugin_manifests, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        codex_plugin_manifests, "check_diagnostics", lambda root: diagnostics if root == tmp_path else ("root",)
    )
    monkeypatch.setattr(codex_plugin_manifests, "sync", lambda root: pytest.fail(f"同期しない: {root}"))

    assert entry.main(["--check"]) == 0
    assert capsys.readouterr().err == ""
    diagnostics = ("agent-toolkit/.codex-plugin/plugin.json: 内容差",)
    assert entry.main(["--check"]) == 1
    assert "agent-toolkit/.codex-plugin/plugin.json: 内容差" in capsys.readouterr().err


def test_main_without_arguments_synchronizes_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """引数なしはリポジトリの派生JSONを同期して0を返す。"""
    synced: list[Path] = []

    def _record_sync(root: Path) -> bool:
        synced.append(root)
        return True

    monkeypatch.setattr(codex_plugin_manifests, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(codex_plugin_manifests, "sync", _record_sync)

    assert entry.main([]) == 0
    assert synced == [tmp_path]


def test_main_rejects_unknown_arguments() -> None:
    """未知の引数は終了コード2で拒否する。"""
    with pytest.raises(SystemExit) as error:
        entry.main(["--unknown"])
    assert error.value.code == 2
