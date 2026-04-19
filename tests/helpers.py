"""テスト用の共通ヘルパー。"""

import json


class _FakeResult:
    """subprocess.CompletedProcess の軽量な代替。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _plugin_list_json(*entries: dict[str, object]) -> str:
    """テスト用の `claude plugin list --json` 出力を組み立てる。"""
    return json.dumps(list(entries))
