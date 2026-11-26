import pathlib

import playwright.async_api
import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_app,
    _browser_fixture,
    _serve,
)

pytestmark = pytest.mark.browser


@pytest.mark.asyncio(loop_scope="session")
async def test_runtime_warning_displays_versions_and_can_recheck(
    tmp_path: pathlib.Path, browser: playwright.async_api.Browser
) -> None:
    app = _browser_app(tmp_path, tmp_path)
    serve_app.register_version_routes(app, tmp_path, "a" * 40)
    mismatch = {"service": "atk serve", "status": "mismatch", "detail": "稼働版: " + "a" * 40}
    pending = {"service": "agents_server", "status": "mismatch", "detail": "公開版: 2.310.2（世代 f9785bcc94d3）"}
    warnings = [mismatch, pending]

    async with _serve(app, browser) as (_context, page, port):

        async def versions_response(route: playwright.async_api.Route) -> None:
            await route.fulfill(json=warnings)

        await page.route("**/api/runtime-versions?view=browser", versions_response)
        await page.goto(f"http://127.0.0.1:{port}/")
        notice = page.locator("#runtime-version-status")
        details = notice.locator("details")
        status = notice.get_by_role("status")
        expect = playwright.async_api.expect
        await expect(status).to_have_text("atk serve・agents_server：更新の反映待ち")
        await expect(details.get_by_text(mismatch["detail"])).to_be_hidden()
        await expect(details.get_by_text(pending["detail"])).to_be_hidden()
        await details.locator("summary").focus()
        await page.keyboard.press("Enter")
        await expect(details.get_by_text(mismatch["detail"])).to_be_visible()
        await expect(details.get_by_text(pending["detail"])).to_be_visible()

        warnings[:] = [{"service": "atk serve", "status": "unavailable", "detail": "公開版: 取得不能"}]
        await notice.get_by_role("button", name="版を再確認").click()
        await expect(status).to_have_text("公開版との一致を確認できません")

        warnings.clear()
        await notice.get_by_role("button", name="版を再確認").click()
        await expect(notice).to_be_hidden()
        await expect(page.locator("#screen-wi h1")).to_be_focused()
