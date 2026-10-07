"""`mcp`の上書き指定が、リポジトリ直下・agent-toolkitの依存解決・配布先の導入で一致することを確かめる不変条件テスト。

直下の`pyproject.toml`と`agent-toolkit/pyproject.toml`の`[tool.uv] override-dependencies`は各プロジェクトの依存解決に、
`agent-toolkit/uv-overrides.txt`は`uv tool install --overrides`による配布先の導入に使われる。
1か所だけを更新すると、ホストと導入経路によって解決される`mcp`の版が異なる。
2つの領域にまたがるため、リポジトリ直下に置く。
"""

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


def _package_name(spec: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", spec.strip())
    return match.group(0).lower() if match else ""


def _mcp_overrides_from_pyproject(path: Path) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return sorted(spec for spec in data["tool"]["uv"]["override-dependencies"] if _package_name(spec) == "mcp")


def _mcp_overrides_from_requirements(path: Path) -> list[str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return sorted(line for line in lines if not line.startswith("#") and _package_name(line) == "mcp")


def test_mcp_override_is_identical_across_three_locations() -> None:
    """3か所の`mcp`の上書き指定が1件ずつあり、同じ指定である。"""
    root = _mcp_overrides_from_pyproject(_ROOT / "pyproject.toml")
    toolkit = _mcp_overrides_from_pyproject(_ROOT / "agent-toolkit" / "pyproject.toml")
    requirements = _mcp_overrides_from_requirements(_ROOT / "agent-toolkit" / "uv-overrides.txt")
    assert len(root) == 1
    assert root == toolkit == requirements
