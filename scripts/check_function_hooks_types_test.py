"""型検査の全件選択と失敗終了を検証する。"""

import json
import pathlib
import subprocess

import check_function_hooks_types
import pytest


@pytest.mark.parametrize("cli", [False, True])
def test_explicit_types_priority(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, cli: bool) -> None:
    """CLI、環境変数の順に採用し、自動探索を呼び出さない。"""
    environment = tmp_path / "environment.d.ts"
    argument = tmp_path / "argument.d.ts"
    for path in (environment, argument):
        path.write_text("export {};", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FUNCTION_HOOKS_TYPES", str(environment))
    monkeypatch.setattr("sys.argv", ["check", *(["--types", str(argument)] if cli else [])])

    def bundled() -> tuple[list[pathlib.Path], str]:
        pytest.fail("明示指定で自動探索が呼ばれた")

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        config = json.loads(pathlib.Path(command[6]).read_text(encoding="utf-8"))
        assert config["files"] == [str(argument if cli else environment)]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(check_function_hooks_types, "_bundled_types", bundled)
    monkeypatch.setattr(check_function_hooks_types, "run_subprocess", run)
    assert check_function_hooks_types.main() == 0


@pytest.mark.parametrize("error", [PermissionError("read denied"), UnicodeError("invalid UTF-8")])
def test_unreadable_bundled_types_have_actionable_diagnostic(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    """読取失敗はトレースバックにせず、指定方法を案内して失敗する。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FUNCTION_HOOKS_TYPES", raising=False)
    monkeypatch.setattr("sys.argv", ["check"])

    def bundled() -> tuple[list[pathlib.Path], str]:
        raise error

    monkeypatch.setattr(check_function_hooks_types, "_bundled_types", bundled)
    assert check_function_hooks_types.main() == 2
    diagnostic = capsys.readouterr().err
    assert str(error) in diagnostic
    assert "--types" in diagnostic and "FUNCTION_HOOKS_TYPES" in diagnostic


@pytest.mark.parametrize("header_version", ["2.1.312", "2.1.310"])
@pytest.mark.parametrize("host_directory", ["", "claude-23456"])
@pytest.mark.parametrize("status", [0, 1])
def test_bundled_types_match_version_and_deduplicate(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    header_version: str,
    host_directory: str,
    status: int,
) -> None:
    """公開呼出しから対象版の型を一度だけコンパイラーへ渡す。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FUNCTION_HOOKS_TYPES", raising=False)
    monkeypatch.setattr("sys.argv", ["check"])
    monkeypatch.setattr(check_function_hooks_types.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(check_function_hooks_types.shutil, "which", lambda name: name)
    source = tmp_path / "agent-toolkit/hooks/example.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export {};", encoding="utf-8")
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command == ["claude", "--version"]:
            return subprocess.CompletedProcess(command, 0, "2.1.312 (Claude Code)", "")
        assert header_version == "2.1.312"
        assert command[:5] == ["npx", "--yes", "-p", "typescript@5.9.3", "tsc"]
        config = json.loads(pathlib.Path(command[6]).read_text(encoding="utf-8"))
        definition = tmp_path / host_directory / "bundled-skills/2.1.312/first/plugin-authoring/types/claude-code.d.ts"
        assert config["files"] == [str(definition), str(source)]
        return subprocess.CompletedProcess(command, status, "compiler output", "")

    monkeypatch.setattr(check_function_hooks_types, "run_subprocess", run)
    for version in ("2.1.310", "2.1.312"):
        for session in ("first", "second"):
            path = tmp_path / host_directory / "bundled-skills" / version / session / "plugin-authoring/types/claude-code.d.ts"
            path.parent.mkdir(parents=True)
            path.write_text(f"// Written by Claude Code {header_version}.\n", encoding="utf-8")
    assert check_function_hooks_types.main() == (status if header_version == "2.1.312" else 2)
    captured = capsys.readouterr()
    assert calls[0] == ["claude", "--version"]
    if header_version == "2.1.312":
        assert len(calls) == 2
        assert captured.out == "compiler output"
    else:
        assert len(calls) == 1
        assert "対象版: 2.1.312" in captured.err
        assert "FUNCTION_HOOKS_TYPES" in captured.err


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
    monkeypatch.setattr(check_function_hooks_types, "_bundled_types", lambda: ([], "2.1.312"))
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
