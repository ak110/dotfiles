"""同じ生成元の派生JSONが、別プロセスでも同じバイト列になることを確認する。"""

import json
import os
import pathlib
import sys

from agent_toolkit._common import process_tree


def test_generated_manifests_are_identical_across_hash_seeds(host_environ):
    """集合を含む設定情報から生成しても、全派生JSONの順序を確定させる。"""
    root = pathlib.Path(__file__).resolve().parents[2]
    code = (
        "import json,pathlib; from pytools._internal import codex_plugin_manifests as c; "
        "print(json.dumps({str(path): body for path, body in c._outputs(pathlib.Path.cwd()).items()}, sort_keys=True))"
    )
    outputs = []
    for seed in ("1", "2"):
        environment = host_environ()
        environment.update(
            {
                "PYTHONHASHSEED": seed,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(root) + os.pathsep + str(root / "agent-toolkit"),
            }
        )
        result = process_tree.run_with_timeout(
            [sys.executable, "-c", code],
            cwd=root,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        assert isinstance(result.stdout, str)
        generated = json.loads(result.stdout)
        assert "agent-toolkit/.mcp.codex.json" in generated
        outputs.append(generated)
    assert outputs[0] == outputs[1]
