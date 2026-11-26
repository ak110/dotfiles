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
    warnings = ["atk serve: 公開版と不一致（未反映）（稼働版: abc、公開版: def）"]

    async with _serve(app, browser) as (_context, page, port):

        async def versions_response(route: playwright.async_api.Route) -> None:
            await route.fulfill(json=warnings)

        await page.route("**/api/runtime-versions", versions_response)
        await page.goto(f"http://127.0.0.1:{port}/")
        notice = page.locator("#runtime-version-status")
        await playwright.async_api.expect(notice).to_contain_text(warnings[0])

        warnings[:] = ["atk serve: 版を取得できません（稼働版: abc、公開版: 取得不能）"]
        await notice.get_by_role("button", name="版を再確認").click()
        await playwright.async_api.expect(notice).to_contain_text(warnings[0])

        warnings.clear()
        await notice.get_by_role("button", name="版を再確認").click()
        await playwright.async_api.expect(notice).to_be_hidden()
