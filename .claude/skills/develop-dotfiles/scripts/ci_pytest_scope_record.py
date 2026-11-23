import json
import os
from pathlib import Path


def pytest_collection_finish(session):
    output = os.environ["CI_PYTEST_SCOPE_NODE_IDS"]
    Path(output).write_text(json.dumps([item.nodeid for item in session.items], ensure_ascii=False), encoding="utf-8")
