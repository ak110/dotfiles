"""`check_plan_file.py`の単体テスト。"""

# pylint: disable=protected-access
# テストは対象モジュールの`_`接頭辞関数を意図的に検査対象とするため、pylintの警告を抑止する。
from __future__ import annotations

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
    monkeypatch.setattr(check_plan_file, "_run_subprocess_check", lambda _cmd, _label, **_kwargs: 0)


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

    def test_extracted_paths_message_printed_but_not_counted(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`_extract_diff_blocks`が返す一時ファイルパス・位置マップを`_check_extracted_paths`へ渡すが、
        textlint違反は警告出力のみで`violations`へは加算しない。
        """
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
        assert check_plan_file._check_one(plan_path, tmp_path) == 0
        assert received == [([prose_file], location_map)]
        assert "textlint違反" in capsys.readouterr().err


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

    def test_failure_with_blocking_false_returns_zero_and_prints(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`blocking=False`は違反があっても0を返し、警告ラベル付きで出力する。"""
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_a, **_k: subprocess.CompletedProcess([], 1, stdout="out", stderr="err"),
        )
        assert check_plan_file._run_subprocess_check(["false"], "label", blocking=False) == 0
        err = capsys.readouterr().err
        assert "警告" in err
        assert "label" in err


class TestDocumentSizeUpperLimit:
    """`_check_document_size_upper_limit`の検査を検証する。"""

    def test_no_over_threshold_files_prints_nothing(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        text = "# t\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行100行, 見込み150行）\n"
        check_plan_file._check_document_size_upper_limit(tmp_path / "plan.md", text)
        assert capsys.readouterr().err == ""

    def test_over_threshold_without_reduction_prints_warning(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = "# t\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行200行, 見込み230行）\n"
        check_plan_file._check_document_size_upper_limit(tmp_path / "plan.md", text)
        captured = capsys.readouterr().err
        assert "[warn]" in captured
        assert "文書サイズ上限" in captured

    def test_over_threshold_with_reduction_heading_prints_nothing(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = (
            "# t\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行200行, 見込み230行）\n\n"
            "#### 縮減対象（foo.md）\n\n本文縮減方針を記す。\n"
        )
        check_plan_file._check_document_size_upper_limit(tmp_path / "plan.md", text)
        assert capsys.readouterr().err == ""


class TestVersionBumpMatrix:
    """`_check_version_bump_matrix`の検査を検証する。"""

    def test_no_agent_toolkit_md_returns_zero(self, tmp_path: pathlib.Path) -> None:
        text = "# t\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `pytools/foo.py`（現行10行, 見込み15行）\n"
        assert check_plan_file._check_version_bump_matrix(tmp_path / "plan.md", text) == 0

    def test_agent_toolkit_md_without_matrix_reports_violation(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = (
            "# t\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `agent-toolkit/skills/foo/SKILL.md`（現行10行, 見込み15行）\n"
        )
        assert check_plan_file._check_version_bump_matrix(tmp_path / "plan.md", text) == 1
        assert "版更新マトリクス" in capsys.readouterr().err

    def test_agent_toolkit_md_with_matrix_returns_zero(self, tmp_path: pathlib.Path) -> None:
        text = (
            "# t\n\n## 対応方針\n\n"
            "| ファイル | 改訂節数 | 節名 | 判定 | 該当基準 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| foo | 1 | bar | PATCH | ok |\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `agent-toolkit/skills/foo/SKILL.md`（現行10行, 見込み15行）\n"
        )
        assert check_plan_file._check_version_bump_matrix(tmp_path / "plan.md", text) == 0

    def test_agent_toolkit_md_with_bump_script_returns_zero(self, tmp_path: pathlib.Path) -> None:
        text = (
            "# t\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `agent-toolkit/skills/foo/SKILL.md`（現行10行, 見込み15行）\n\n"
            "## 実行方法\n\n`scripts/agent_toolkit_bump.py minor`で更新する。\n"
        )
        assert check_plan_file._check_version_bump_matrix(tmp_path / "plan.md", text) == 0


class TestRunMethodScriptPaths:
    """`_check_run_method_script_paths`の検査を検証する。"""

    _REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

    def test_existing_script_passes(self, tmp_path: pathlib.Path) -> None:
        text = "# t\n\n## 実行方法\n\n`scripts/agent_toolkit_bump.py minor`で更新する。\n"
        assert not check_plan_file._check_run_method_script_paths(tmp_path / "plan.md", text, self._REPO_ROOT)

    def test_missing_script_blocks(self, tmp_path: pathlib.Path) -> None:
        text = "# t\n\n## 実行方法\n\n`agent-toolkit/scripts/agent_toolkit_bump.py`を実行する。\n"
        issues = check_plan_file._check_run_method_script_paths(tmp_path / "plan.md", text, self._REPO_ROOT)
        assert len(issues) == 1
        assert "agent-toolkit/scripts/agent_toolkit_bump.py" in issues[0]

    def test_command_with_arguments_extracts_only_script(self, tmp_path: pathlib.Path) -> None:
        text = "# t\n\n## 実行方法\n\n`python scripts/agent_toolkit_bump.py minor`で更新する。\n"
        assert not check_plan_file._check_run_method_script_paths(tmp_path / "plan.md", text, self._REPO_ROOT)

    def test_non_run_method_section_ignored(self, tmp_path: pathlib.Path) -> None:
        text = "# t\n\n## 変更内容\n\n`agent-toolkit/scripts/nonexistent.py`に言及する。\n"
        assert not check_plan_file._check_run_method_script_paths(tmp_path / "plan.md", text, self._REPO_ROOT)

    def test_non_script_token_ignored(self, tmp_path: pathlib.Path) -> None:
        text = "# t\n\n## 実行方法\n\n`agent-toolkit:commit`を呼び出す。\n"
        assert not check_plan_file._check_run_method_script_paths(tmp_path / "plan.md", text, self._REPO_ROOT)

    def test_flag_or_keyvalue_ignored(self, tmp_path: pathlib.Path) -> None:
        text = "# t\n\n## 実行方法\n\n`--frozen scripts/agent_toolkit_bump.py --script=scripts/nonexistent.py`で更新する。\n"
        assert not check_plan_file._check_run_method_script_paths(tmp_path / "plan.md", text, self._REPO_ROOT)


def _write_repo_file(repo_root: pathlib.Path, rel_path: str) -> None:
    path = repo_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


class TestCheckTestFilePairing:
    """`_check_test_file_pairing`のテスト。"""

    def test_pair_missing_from_target_list(self, tmp_path: pathlib.Path) -> None:
        """`.py`実装と`_test.py`がリポジトリに存在し対象一覧に実装のみの場合にwarnが返る。"""
        _write_repo_file(tmp_path, "pkg/mod.py")
        _write_repo_file(tmp_path, "pkg/mod_test.py")
        text = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `pkg/mod.py`\n"
        warnings = check_plan_file._check_test_file_pairing(tmp_path / "plan.md", text, tmp_path)
        assert len(warnings) == 1
        assert "mod_test.py" in warnings[0]

    def test_pair_both_listed(self, tmp_path: pathlib.Path) -> None:
        """実装と`_test.py`両方が対象一覧に含まれる場合はwarn無し。"""
        _write_repo_file(tmp_path, "pkg/mod.py")
        _write_repo_file(tmp_path, "pkg/mod_test.py")
        text = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `pkg/mod.py`\n- [ ] `pkg/mod_test.py`\n"
        assert not check_plan_file._check_test_file_pairing(tmp_path / "plan.md", text, tmp_path)

    def test_no_test_file_exists(self, tmp_path: pathlib.Path) -> None:
        """リポジトリに`_test.py`が存在しない場合はwarn無し。"""
        _write_repo_file(tmp_path, "pkg/mod.py")
        text = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `pkg/mod.py`\n"
        assert not check_plan_file._check_test_file_pairing(tmp_path / "plan.md", text, tmp_path)

    def test_excludes_init_and_test_helpers(self, tmp_path: pathlib.Path) -> None:
        """`__init__.py`と`_test_helpers.py`は検査対象外。"""
        _write_repo_file(tmp_path, "pkg/__init__.py")
        _write_repo_file(tmp_path, "pkg/__init___test.py")
        _write_repo_file(tmp_path, "pkg/_test_helpers.py")
        text = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `pkg/__init__.py`\n- [ ] `pkg/_test_helpers.py`\n"
        assert not check_plan_file._check_test_file_pairing(tmp_path / "plan.md", text, tmp_path)

    def test_excludes_test_file_itself(self, tmp_path: pathlib.Path) -> None:
        """末尾`_test.py`のファイル自身は検査対象外。"""
        _write_repo_file(tmp_path, "pkg/mod_test.py")
        text = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `pkg/mod_test.py`\n"
        assert not check_plan_file._check_test_file_pairing(tmp_path / "plan.md", text, tmp_path)

    def test_excludes_non_python(self, tmp_path: pathlib.Path) -> None:
        """`.md`等の非Python拡張子は検査対象外。"""
        text = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `docs/guide.md`\n"
        assert not check_plan_file._check_test_file_pairing(tmp_path / "plan.md", text, tmp_path)
