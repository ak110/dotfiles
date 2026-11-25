import base64
import pathlib

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state
from agent_toolkit._testing.serve_support import _write_detail_entry


@pytest.mark.asyncio
async def test_serve_attachment_dispositions(tmp_path: pathlib.Path) -> None:
    _write_detail_entry(tmp_path, "---\nstate: inbox\ntype: awi\n---\n\nattachment:pixel.png attachment:log.svg\n")
    folder = tmp_path / "attachments/entry"
    folder.mkdir(parents=True)
    pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC")
    (folder / "pixel.png").write_bytes(pixel)
    (folder / "log.svg").write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    client = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path)).test_client()

    detail = await client.get("/api/entries/entry.md")
    assert (await detail.get_json())["entry"]["attachments"] == [
        {"name": "log.svg", "image": False},
        {"name": "pixel.png", "image": True},
    ]
    image = await client.get("/api/wi-attachments/entry.md/pixel.png")
    assert image.headers["Content-Disposition"] == 'inline; filename="pixel.png"'
    assert image.headers["Content-Type"] == "image/png"
    assert await image.get_data() == pixel
    log = await client.get("/api/wi-attachments/entry.md/log.svg")
    assert log.headers["X-Content-Type-Options"] == "nosniff"
    assert log.headers["Content-Disposition"] == 'attachment; filename="log.svg"'
    assert (await client.get("/api/wi-attachments/missing.md/pixel.png")).status_code == 404
