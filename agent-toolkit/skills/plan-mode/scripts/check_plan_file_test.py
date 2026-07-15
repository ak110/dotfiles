"""`check_plan_file.py`の単体テスト。"""

# pylint: disable=protected-access
# テストは対象モジュールの`_`接頭辞関数を意図的に検査対象とするため、pylintの警告を抑止する。
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import check_plan_file
import pytest


def _write_plan(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "plan.md"
    path.write_text(
        "# タイトル\n\n## 背景\n\n### 計画メタ情報\n\n"
        "- 起動経路: process-feedbacks経由\n- 対象リポジトリ: `~/dotfiles`\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(name="stub_check_one")
def _stub_check_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_check_one`が呼ぶ全下位検査を無違反へスタブする共通fixture。個別テストで差分箇所のみ上書きする。"""
    monkeypatch.setattr(check_plan_file.check_wc_projection, "_check_wc", lambda _p: 0)
    monkeypatch.setattr(
        check_plan_file.check_plan_diff_gates,
        "_extract_diff_blocks",
        lambda _p: ([], ([], {})),
    )
    monkeypatch.setattr(check_plan_file.check_plan_diff_gates, "_check_extracted_paths", lambda _paths: [])
    monkeypatch.setattr(check_plan_file.check_deprecated_identifier_coverage, "_check_plan", lambda _p, _r: 0)
    monkeypatch.setattr(check_plan_file.check_line_ref, "_check_file", lambda _p, _t: [])
    monkeypatch.setattr(check_plan_file.check_line_ref, "_check_content_level_violations", lambda _p, _t, _r: [])
    monkeypatch.setattr(check_plan_file.check_self_ref, "_check_file", lambda _p, _t: [])
    monkeypatch.setattr(check_plan_file.check_plan_meta, "_check_file", lambda _p, _t: [])
    monkeypatch.setattr(
        check_plan_file.check_plan_diff_gates,
        "_check_transcription_declaration_consistency",
        lambda _p, _t, _r: [],
    )
    monkeypatch.setattr(check_plan_file, "_run_subprocess_check", lambda _cmd, _label: 0)
    monkeypatch.setattr(check_plan_file, "_run_pyfltr_jsonl", lambda _p: 0)


@pytest.mark.usefixtures("stub_check_one")
class TestCheckOne:
    """`_check_one`の集約ロジックを検証する。"""

    def test_no_violations_returns_zero(self, tmp_path: pathlib.Path) -> None:
        plan_path = _write_plan(tmp_path)
        assert check_plan_file._check_one(plan_path, tmp_path) == 0

    def test_wc_projection_violation_counted(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        plan_path = _write_plan(tmp_path)
        monkeypatch.setattr(check_plan_file.check_wc_projection, "_check_wc", lambda _p: 2)
        assert check_plan_file._check_one(plan_path, tmp_path) == 2

    def test_diff_gates_violation_counted(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        plan_path = _write_plan(tmp_path)
        monkeypatch.setattr(
            check_plan_file.check_plan_diff_gates,
            "_extract_diff_blocks",
            lambda _p: (["msg1", "msg2"], ([], {})),
        )
        assert check_plan_file._check_one(plan_path, tmp_path) == 2

    def test_transcription_declaration_consistency_called_with_no_warnings(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """正常系: 警告なし応答時は違反件数へ影響しない。"""
        plan_path = _write_plan(tmp_path)
        received: list[tuple[pathlib.Path, str, pathlib.Path]] = []

        def _fake_check(p: pathlib.Path, t: str, r: pathlib.Path) -> list[str]:
            received.append((p, t, r))
            return []

        monkeypatch.setattr(
            check_plan_file.check_plan_diff_gates,
            "_check_transcription_declaration_consistency",
            _fake_check,
        )
        assert check_plan_file._check_one(plan_path, tmp_path) == 0
        assert received == [(plan_path, plan_path.read_text(encoding="utf-8"), tmp_path)]

    def test_transcription_declaration_consistency_warning_printed_without_counting(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """異常系: 警告応答時はstderrへ出力されるが違反件数（exit code相当）には計上しない。"""
        plan_path = _write_plan(tmp_path)
        monkeypatch.setattr(
            check_plan_file.check_plan_diff_gates,
            "_check_transcription_declaration_consistency",
            lambda _p, _t, _r: ["plan.md:1: [warn] 責務差分の可能性"],
        )
        assert check_plan_file._check_one(plan_path, tmp_path) == 0
        assert "責務差分の可能性" in capsys.readouterr().err

    def test_extracted_paths_violation_counted(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_extract_diff_blocks`が返す一時ファイルパス・位置マップを`_check_extracted_paths`へ渡し、違反を集計する。"""
        plan_path = _write_plan(tmp_path)
        prose_file = tmp_path / "prose.md"
        prose_file.write_text("散文ブロック", encoding="utf-8")
        location_map = {str(prose_file): "plan.md: H3=`foo.md` L10"}
        monkeypatch.setattr(
            check_plan_file.check_plan_diff_gates,
            "_extract_diff_blocks",
            lambda _p: ([], ([prose_file], location_map)),
        )
        received: list[tuple[list[pathlib.Path], dict[str, str]]] = []

        def _fake_check_extracted_paths(
            paths: tuple[list[pathlib.Path], dict[str, str]],
        ) -> list[str]:
            received.append(paths)
            return ["textlint違反\ndetail"]

        monkeypatch.setattr(
            check_plan_file.check_plan_diff_gates,
            "_check_extracted_paths",
            _fake_check_extracted_paths,
        )
        assert check_plan_file._check_one(plan_path, tmp_path) == 1
        assert received == [([prose_file], location_map)]


class TestCaptureAndRelay:
    """`_capture_and_relay`のstderr捕捉・再出力判定を検証する。"""

    def test_zero_violations_no_warn_suppresses_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        def _check() -> int:
            print("廃止・改名対象なし", file=sys.stderr)
            return 0

        assert check_plan_file._capture_and_relay(_check) == 0
        assert capsys.readouterr().err == ""

    def test_violations_relays_captured_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        def _check() -> int:
            print("違反1件", file=sys.stderr)
            return 1

        assert check_plan_file._capture_and_relay(_check) == 1
        assert "違反1件" in capsys.readouterr().err

    def test_zero_violations_with_warn_relays_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        def _check() -> int:
            print("[warn] bump step欠落", file=sys.stderr)
            return 0

        assert check_plan_file._capture_and_relay(_check) == 0
        assert "[warn]" in capsys.readouterr().err


class TestRunSubprocessCheck:
    """`_run_subprocess_check`の成否判定を検証する。"""

    def test_success_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        assert check_plan_file._run_subprocess_check(["true"], "label") == 0

    def test_failure_returns_one_and_prints(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_a, **_k: subprocess.CompletedProcess([], 1, stdout="out", stderr="err"),
        )
        assert check_plan_file._run_subprocess_check(["false"], "label") == 1
        assert "label" in capsys.readouterr().err


class TestRunPyfltrJsonl:
    """`_run_pyfltr_jsonl`のJSONL要約ロジックを検証する。"""

    def test_diagnostic_records_counted(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lines = [
            json.dumps({"kind": "header"}),
            json.dumps({"kind": "diagnostic", "path": "x.md", "line": 3, "message": "違反"}),
            json.dumps({"kind": "command", "status": "succeeded", "diagnostics": 0}),
        ]
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_a, **_k: subprocess.CompletedProcess([], 1, stdout="\n".join(lines), stderr=""),
        )
        plan_path = _write_plan(tmp_path)
        assert check_plan_file._run_pyfltr_jsonl(plan_path) == 1
        assert "x.md:3" in capsys.readouterr().err

    def test_no_diagnostics_returns_zero(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lines = [json.dumps({"kind": "header"}), json.dumps({"kind": "command", "status": "succeeded"})]
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout="\n".join(lines), stderr=""),
        )
        plan_path = _write_plan(tmp_path)
        assert check_plan_file._run_pyfltr_jsonl(plan_path) == 0
        assert capsys.readouterr().err == ""

    def test_failed_command_status_counted(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lines = [
            json.dumps({"kind": "header"}),
            json.dumps({"kind": "command", "command": "textlint", "status": "failed", "message": "設定エラー"}),
            json.dumps(
                {"kind": "command", "command": "markdownlint", "status": "resolution_failed", "message": "依存解決失敗"}
            ),
        ]
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_a, **_k: subprocess.CompletedProcess([], 1, stdout="\n".join(lines), stderr=""),
        )
        plan_path = _write_plan(tmp_path)
        assert check_plan_file._run_pyfltr_jsonl(plan_path) == 2
        err = capsys.readouterr().err
        assert "[pyfltr] textlint: failed" in err
        assert "[pyfltr] markdownlint: resolution_failed" in err
