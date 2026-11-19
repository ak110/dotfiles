"""CLIが出力するJSONの項目を確かめる。"""

import json
import pathlib
import subprocess
import sys


def test_cli_output_keys() -> None:
    """出力の項目名の集合は利用側との契約であり、過不足を許さない。"""
    script = pathlib.Path(__file__).with_name("app_cli.py")
    output = subprocess.run(
        [sys.executable, str(script)],
        input="x\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    assert sorted(json.loads(output)) == ["count", "name"]
