"""CLIの実HTMLを使い、検索・範囲変更・候補選択からダウンロードまで検証する。"""

import json
import os
import pathlib
import shlex
import subprocess
from collections.abc import Iterator
from typing import Any

import playwright.sync_api
import pytest
from agent_toolkit._testing.isolation import restore_host_environment

from pytools import dirsize_test

pytestmark = pytest.mark.browser


@pytest.fixture(name="browser")
def _browser(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[playwright.sync_api.Browser]:
    # ブラウザーの探索はdriver起動時に行われるため、その区間だけホスト環境へ戻す。
    with monkeypatch.context() as driver_environment:
        restore_host_environment(driver_environment)
        engine = playwright.sync_api.sync_playwright().start()
    try:
        browser = engine.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()
    finally:
        engine.stop()


def test_cli_html_candidate_selection_download_and_search(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.sync_api.Browser,
) -> None:
    target = tmp_path / "fixture"
    target.mkdir()
    root = target / "uv ' $ cache"
    root.mkdir()
    (root / "archive").mkdir()
    (root / "archive" / "wheel").write_bytes(b"1" * 41)
    long_name = "長いパス" * 18
    (target / long_name).mkdir()
    for index in range(123):
        (target / long_name / f"project-{index:03d}").write_bytes(b"1" * (index + 1))
    hostile = "<img src=x onerror=alert(1)> & '" if os.name != "nt" else "& ' $ text"
    (target / hostile).write_bytes(b"1" * 7)
    calls = dirsize_test.mock_uv(monkeypatch, root)
    output = tmp_path / "report"
    data = dirsize_test.run_report(monkeypatch, target, output)
    with browser.new_context(accept_downloads=True, viewport={"width": 1000, "height": 800}) as context:
        page = context.new_page()
        errors: list[str] = []
        requests: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto((output / "report.html").as_uri())
        expect = playwright.sync_api.expect
        expect(page.get_by_role("heading", name="容量レポート", exact=True)).to_be_visible()
        areas = page.locator(".tile").evaluate_all(
            "tiles => tiles.map(t => ({title:t.title, area:parseFloat(t.style.width)*parseFloat(t.style.height)}))"
        )
        assert len(areas) == 125
        assert sum(tile["area"] for tile in areas) == pytest.approx(10000)
        wheel = next(tile for tile in areas if tile["title"].startswith(str(root / "archive" / "wheel") + " "))
        # CSSの割合文字列はブラウザーが有効桁を丸めるため、直列化精度を許容する。
        assert wheel["area"] == pytest.approx(10000 * 41 / 7674, rel=1e-5)
        expect(page.get_by_role("status").first).to_contain_text("件")
        expect(page.get_by_role("button", name="Shellをダウンロード", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="次のページ")).to_be_enabled()
        page.get_by_role("button", name="次のページ").click()
        expect(page.locator("#page")).to_have_text("2 / 2")
        page.get_by_label("パスを検索").fill(hostile)
        expect(page.get_by_role("row")).to_have_count(2)
        expect(page.get_by_role("cell").first).to_contain_text(hostile)
        expect(page.locator("img")).to_have_count(0)
        page.get_by_label("表示範囲").select_option("directory")
        expect(page.get_by_role("status").first).to_contain_text("一致するパスなし")
        page.get_by_label("パスを検索").fill("")
        expect(page.get_by_role("status").first).to_contain_text("件")
        choice = page.get_by_role("checkbox", name="uvキャッシュルート全体を選択")
        choice.focus()
        page.keyboard.press("Space")
        expect(choice).to_be_checked()
        expect(page.locator("#candidate-path")).to_have_text(str(root))
        expect(page.get_by_label("生成コマンドのプレビュー")).to_contain_text("--cache-dir")
        for label, suffix, encoding in [
            ("Shellをダウンロード", "sh", "utf-8"),
            ("PowerShellをダウンロード", "ps1", "utf-8-sig"),
        ]:
            with page.expect_download() as download_info:
                page.get_by_role("button", name=label, exact=True).click()
            download = download_info.value
            assert download.suggested_filename == f"uv-cache-prune.{suffix}"
            destination = tmp_path / download.suggested_filename
            download.save_as(destination)
            text = destination.read_text(encoding=encoding)
            if suffix == "sh":
                assert shlex.split(text.split("set -eu\n")[1]) == [
                    "uv",
                    "cache",
                    "prune",
                    "--no-config",
                    "--offline",
                    "--cache-dir",
                    str(root),
                ]
            else:
                assert text.replace("\n", "\r\n") == data["candidate"]["powershell"]
        choice.uncheck()
        expect(page.get_by_role("button", name="Shellをダウンロード", exact=True)).to_be_disabled()
        page.set_viewport_size({"width": 320, "height": 800})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
        assert requests == [(output / "report.html").as_uri()]
    assert calls == [["uv", "cache", "dir", "--no-config"]]


def test_cli_empty_html_has_no_candidate(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.sync_api.Browser,
) -> None:
    target = tmp_path / "empty"
    target.mkdir()
    dirsize_test.mock_uv(monkeypatch, tmp_path / "outside")
    output = tmp_path / "out"
    dirsize_test.run_report(monkeypatch, target, output)
    with browser.new_page() as page:
        page.goto((output / "report.html").as_uri())
        expect = playwright.sync_api.expect
        expect(page.locator("#total")).to_have_text("0 B")
        expect(page.locator("#map-empty")).to_contain_text("通常ファイルがない")
        expect(page.locator("#candidate-reason")).to_contain_text("生成可能な項目なし")
        expect(page.get_by_role("checkbox")).to_have_count(0)
        expect(page.get_by_role("button", name="Shellをダウンロード", exact=True)).to_be_disabled()


def test_cli_uv_failure_recovery_guidance_in_html(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.sync_api.Browser,
) -> None:
    original_lstat = pathlib.Path.lstat
    scenarios = [
        "absent",
        "timeout",
        "exit",
        "relative",
        "missing",
        "incomplete",
        "outside",
        "excluded",
        "excluded-child",
        "excluded-ancestor",
    ]
    with browser.new_page() as page:
        for scenario in scenarios:
            target = tmp_path / scenario
            target.mkdir()
            (target / "retained").write_bytes(b"1234")
            root = target / "container" / "uv" if scenario == "excluded-ancestor" else target / "uv"
            root.parent.mkdir(exist_ok=True)
            if scenario != "missing":
                root.mkdir()
                (root / "data").write_bytes(b"123")
            if scenario in {"incomplete", "excluded-child"}:
                (root / "bad").write_bytes(b"123")

            def query(
                command: list[str],
                *,
                scenario: str = scenario,
                root: pathlib.Path = root,
                **kwargs: Any,
            ) -> subprocess.CompletedProcess[str]:
                del kwargs
                assert command == ["uv", "cache", "dir", "--no-config"]
                if scenario == "absent":
                    raise FileNotFoundError("uv test double: absent")
                if scenario == "timeout":
                    raise subprocess.TimeoutExpired(command, 15)
                return subprocess.CompletedProcess(
                    command,
                    1 if scenario == "exit" else 0,
                    "relative\n" if scenario == "relative" else str(tmp_path if scenario == "outside" else root) + "\n",
                    "uv diagnostic <unavailable>" if scenario == "exit" else "",
                )

            def lstat(path: pathlib.Path, *, root: pathlib.Path = root, **kwargs: Any) -> Any:
                if path == root / "bad":
                    raise PermissionError("uv fixture: unreadable")
                return original_lstat(path, **kwargs)

            dirsize_test.mock_uv_command(monkeypatch, query)
            exclusions = {"excluded": "uv", "excluded-child": "uv/bad", "excluded-ancestor": "container"}
            extra = ("--exclude", exclusions[scenario]) if scenario in exclusions else ()
            output = tmp_path / (scenario + "-report")
            with monkeypatch.context() as boundary:
                if scenario == "incomplete":
                    boundary.setattr(pathlib.Path, "lstat", lstat)
                data = dirsize_test.run_report(monkeypatch, target, output, *extra, code=int(scenario == "incomplete"))
            assert data["candidate"] is None
            assert data["status"] == ("partial" if scenario == "incomplete" else "success")
            assert data["nodes"][0]["size"] == (4 if scenario in {"missing", "excluded", "excluded-ancestor"} else 7)
            page.goto((output / "report.html").as_uri())
            reason = page.locator("#candidate-reason")
            expect = playwright.sync_api.expect
            expect(reason).to_have_text("生成可能な項目なし: " + data["candidate_reason"])
            print(json.dumps({"scenario": scenario, "html_text": reason.inner_text()}, ensure_ascii=False))
            expect(page.get_by_role("checkbox")).to_have_count(0)
            expect(page.get_by_role("button", name="Shellをダウンロード", exact=True)).to_be_disabled()
            expect(page.locator("unavailable")).to_have_count(0)
            if scenario in {"outside", "excluded", "excluded-child", "excluded-ancestor"}:
                assert "再生成" not in data["candidate_reason"]
                continue
            expect(reason).to_contain_text("dirsize")
            expect(reason).to_contain_text("再生成")
            if scenario == "incomplete":
                expect(reason).to_contain_text("除外・未取得")
                expect(reason).to_contain_text("読み取り権限")
            else:
                expect(reason).to_contain_text("uv cache dir --no-config")
            if scenario == "absent":
                expect(reason).to_contain_text("uv --version")
            if scenario == "relative":
                expect(reason).to_contain_text("UV_CACHE_DIR")
            if scenario == "missing":
                root.mkdir()
                (root / "data").write_bytes(b"123")
            # 案内に沿って照会・取得境界を正常に戻し、同じCLIでレポートを再生成する。
            dirsize_test.mock_uv(monkeypatch, root)
            recovered = dirsize_test.run_report(monkeypatch, target, output)
            assert recovered["candidate"]["path"] == str(root)
            page.goto((output / "report.html").as_uri())
            expect(page.get_by_role("checkbox", name="uvキャッシュルート全体を選択")).to_be_visible()
