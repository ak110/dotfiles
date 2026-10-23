"""公開された起動コマンドの失敗時に復旧操作と元の終了・保持契約を検収する。"""

import io
import pathlib
import sys
from typing import Any

import pytest

from pytools import (
    claude_hook,
    clonedir,
    date_relocator,
    delete_homonym,
    ecoutilities,
    imageconverter,
    randfile,
    reg_export,
    rename,
    repack_archive,
    run_as_admin,
)
from pytools.markdown_viewer import _cli as markdown_cli
from pytools.media_remote import _app as media_app
from pytools.media_remote import _cli as media_cli


@pytest.mark.parametrize(
    "module,arguments,exception,operation",
    [
        (clonedir, ["missing", "destination"], FileNotFoundError, "ディレクトリをsrcに指定"),
        (randfile, ["existing", "10"], FileExistsError, "存在しない出力先"),
        (repack_archive, ["missing"], FileNotFoundError, "アーカイブ・PDF・ディレクトリを指定"),
    ],
)
def test_invalid_path_preserves_input_and_explains_accepted_value(
    module: Any,
    arguments: list[str],
    exception: type[Exception],
    operation: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """誤入力による中断前に元ファイルを保持し、有効な入力を案内する。"""
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "existing"
    existing.write_bytes(b"keep")
    monkeypatch.setattr(sys, "argv", ["command", *arguments])
    with pytest.raises(exception, match=operation):
        module.main()
    assert existing.read_bytes() == b"keep"
    assert not (tmp_path / "destination").exists()


@pytest.mark.parametrize("opened", [False, "error"])
def test_markdown_browser_failure_keeps_html_for_manual_open(
    opened: bool | str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ブラウザ故障後にも生成済みHTMLを手動で開ける。"""
    source = tmp_path / "source.md"
    source.write_text("# recoverable", encoding="utf-8")
    monkeypatch.setattr(markdown_cli.tempfile, "gettempdir", lambda: str(tmp_path))

    def open_browser(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        if opened == "error":
            raise markdown_cli.webbrowser.Error("browser unavailable")
        return False

    monkeypatch.setattr(markdown_cli.webbrowser, "open", open_browser)
    assert markdown_cli.main([str(source)]) == 1
    output = capsys.readouterr().err
    html_files = list((tmp_path / "markdown-viewer").glob("*.html"))
    assert len(html_files) == 1
    assert str(html_files[0]) in output
    assert "ブラウザで手動で開いて" in output
    assert "recoverable" in html_files[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("remove_failed", [False, True])
def test_image_failure_reports_actual_removal_and_safe_retry(
    remove_failed: bool,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """読込故障時の原本の処置と次回の保持オプションをCLIから観測する。"""
    source = tmp_path / "image.png"
    source.write_bytes(b"input")

    def fail_read(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise OSError("read failed")

    monkeypatch.setattr(imageconverter, "open_image_with_exif", fail_read)
    arguments = ["py-imageconverter", str(source)]
    if not remove_failed:
        arguments.append("--no-remove-failed")
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(SystemExit) as exit_info:
        imageconverter.main()
    assert exit_info.value.code == 1
    assert source.exists() is not remove_failed
    output = capsys.readouterr().out
    assert ("原本を削除" if remove_failed else "原本を保持") in output
    assert "--no-remove-failed" in output


@pytest.mark.parametrize("failure", ["decode", "import", "execution"])
def test_hook_failure_passes_and_reports_recovery(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """入力・依存・実行の失敗でも通過と復旧先を返す。"""
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff" if failure == "decode" else b"{}")))

    class FailingHook:
        def main(self, *args: Any, **kwargs: Any) -> int:
            del args, kwargs
            raise RuntimeError("hook failed")

    def import_hook(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        if failure == "import":
            raise ImportError("dependency missing")
        return FailingHook()

    monkeypatch.setattr(claude_hook.importlib, "import_module", import_hook)
    assert claude_hook.main(["pretooluse"]) == 0
    output = capsys.readouterr().err
    assert "通過" in output
    assert ("UTF-8にそろえて" if failure == "decode" else "dotfilesのIssue") in output
    if failure == "import":
        assert "make sync" in output


@pytest.mark.parametrize("command", ["serve", "doctor"])
def test_media_windows_only_rejection_does_not_create_token(
    command: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """OS拒否は終了1でWindows側の同じ起動コマンドを案内する。"""
    monkeypatch.setattr(media_cli.sys, "platform", "linux")
    token = tmp_path / "token"
    assert media_cli.main([command, "--token-file", str(token)]) == 1
    assert f"Windowsでdotfiles-media-remote {command}" in caplog.text
    assert not token.exists()


def test_rename_empty_rules_rejects_without_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """空の設定は改名前に拒否し、受理するTAB書式を返す。"""
    rules = tmp_path / "rules"
    rules.write_text("# empty\n", encoding="utf-8")
    target = tmp_path / "original"
    target.write_bytes(b"keep")
    monkeypatch.setattr(sys, "argv", ["py-rename", "-f", str(rules), "-t", str(tmp_path)])
    with pytest.raises(SystemExit) as exit_info:
        rename.main()
    assert exit_info.value.code == 2
    assert "正規表現<TAB>置換文字列" in capsys.readouterr().err
    assert target.read_bytes() == b"keep"


@pytest.mark.parametrize("module", [reg_export, run_as_admin])
def test_windows_cli_rejects_before_operating(
    module: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """OS外での実行は終了1を保ちWindowsでの実行へ案内する。"""
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit) as exit_info:
        module.main()
    assert exit_info.value.code == 1
    assert "Windowsで" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_media_authentication_failure_provides_new_url_without_sending_key() -> None:
    """無効な認証値から有効URLの取得へ案内し、キーを送出しない。"""
    sent: list[str] = []
    app = media_app.create_app("expected", send_key=sent.append)
    response = await app.test_client().post("/api/key/play_pause?t=invalid")
    assert response.status_code == 401
    assert "dotfiles-media-remote url" in await response.get_data(as_text=True)
    assert not sent


@pytest.mark.parametrize("pid_present", [False, True])
def test_media_doctor_explains_missing_process_even_when_listener_exists(
    pid_present: bool,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """別listenerが起動勧告を削除してもprocess欄に復旧操作が残る。"""
    monkeypatch.setattr(media_cli.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    pid = media_cli.default_pid_path()
    if pid_present:
        pid.parent.mkdir(parents=True)
        pid.write_text("4242", encoding="utf-8")

    def powershell(script: str, **kwargs: Any) -> tuple[str, int]:
        del kwargs
        if "Get-NetTCPConnection" in script:
            return "127.0.0.1:29123 (PID=1)", 0
        if "Get-NetConnectionProfile" in script:
            return "", 1
        return "NONE", 0

    monkeypatch.setattr(media_cli, "_ps", powershell)
    assert media_cli.main(["doctor", "--host", "127.0.0.1", "--token-file", str(tmp_path / "token")]) == 0
    output = capsys.readouterr().out
    assert "dotfiles-media-remote serve" in output
    assert "Get-NetConnectionProfile" in output


def test_date_relocator_skips_invalid_target_and_continues(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """誤入力の後の有効ディレクトリを処理し、誤入力だけの再指定へ案内する。"""
    source = tmp_path / "directory"
    source.mkdir()
    (source / "item").write_bytes(b"keep")
    monkeypatch.setattr(sys, "argv", ["date-relocator", str(tmp_path / "missing"), str(source)])
    with pytest.raises(SystemExit) as exit_info:
        date_relocator.main()
    assert exit_info.value.code == 0
    assert "存在するディレクトリを指定" in caplog.text
    assert not (source / "item").exists()
    assert len(list(tmp_path.glob("directory-*/item"))) == 1


def test_homonym_collision_keeps_both_files_and_explains_comparison(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """サイズ不一致のハッシュ名は両方保持し、手動比較を案内する。"""
    files = [tmp_path / name / ("a" * 32 + ".bin") for name in ("first", "second")]
    for index, file in enumerate(files):
        file.parent.mkdir()
        file.write_bytes(b"x" * (index + 1))
    monkeypatch.setattr(sys, "argv", ["delete-homonym", "*.bin", *(str(file.parent) for file in files)])
    with pytest.raises(SystemExit) as exit_info:
        delete_homonym.main()
    assert exit_info.value.code == 0
    assert all(file.is_file() for file in files)
    assert "両ファイルを開いて内容を比較" in caplog.text


def test_ecoutilities_writes_editable_sample_before_retry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """未作成の設定は生成後に編集対象と同じ起動コマンドを表示する。"""
    config = tmp_path / "sites.xml"
    monkeypatch.setattr(sys, "argv", ["EcoUtilities", "DownloadCustom", str(config)])
    with pytest.raises(SystemExit) as exit_info:
        ecoutilities.main()
    assert exit_info.value.code == 0
    assert config.is_file()
    output = capsys.readouterr().out
    assert str(config) in output
    assert "編集" in output and "EcoUtilities DownloadCustom" in output
