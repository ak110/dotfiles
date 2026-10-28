"""型検査の全件選択と失敗終了を検証する。"""

import json
import pathlib
import subprocess

import check_function_hooks_types
import pytest


@pytest.mark.parametrize("status", [0, 2])
@pytest.mark.parametrize("explicit", [False, True])
def test_checks_all_modules_and_propagates_status(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, status: int, explicit: bool
) -> None:
    hooks = tmp_path / "agent-toolkit/hooks"
    hooks.mkdir(parents=True)
    for name in ("old.ts", "new.tsx", "new.test.tsx"):
        (hooks / name).write_text("export {};", encoding="utf-8")
    definition = tmp_path / "types/claude-code.d.ts"
    definition.parent.mkdir()
    definition.write_text("declare module 'claude-code' {}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FUNCTION_HOOKS_TYPES", raising=False)
    monkeypatch.setattr("sys.argv", ["check", *(["--types", str(definition)] if explicit else [])])
    settings: list[pathlib.Path] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert pathlib.Path(command[0]).name in {"npx", "npx.cmd"}
        assert command[1:5] == ["--yes", "-p", "typescript@5.9.3", "tsc"]
        setting = pathlib.Path(command[6])
        settings.append(setting)
        config = json.loads(setting.read_text(encoding="utf-8"))
        assert set(config["files"]) == {str(definition), *(str(path) for path in hooks.iterdir())}
        assert config["compilerOptions"]["allowImportingTsExtensions"]
        return subprocess.CompletedProcess(command, status, "", "")

    monkeypatch.setattr(check_function_hooks_types, "run_subprocess", run)
    assert check_function_hooks_types.main() == status
    assert all(not path.exists() for path in settings)


@pytest.mark.parametrize("explicit", [False, True])
def test_missing_types_fails_with_preparation_instructions(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], explicit: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FUNCTION_HOOKS_TYPES", raising=False)
    monkeypatch.setattr("sys.argv", ["check", *(["--types", str(tmp_path / "missing.d.ts")] if explicit else [])])
    assert check_function_hooks_types.main() == 2
    error = capsys.readouterr().err
    assert "FUNCTION_HOOKS_TYPES" in error and "開発担当へ報告" in error


def test_compiler_start_failure_guides_preparation_and_report(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """コンパイラー起動失敗は準備の確認と解消不能時の報告先を示して非0で終了する。"""
    definition = tmp_path / "claude-code.d.ts"
    definition.write_text("export {};", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check", "--types", str(definition)])
    monkeypatch.setattr(check_function_hooks_types, "run_subprocess", lambda *_a, **_k: None)
    assert check_function_hooks_types.main() == 2
    error = capsys.readouterr().err
    assert "Node.js・npm" in error and "開発担当へ報告" in error
