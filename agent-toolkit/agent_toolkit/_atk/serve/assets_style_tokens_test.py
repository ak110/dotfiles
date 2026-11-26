import re

import pytest

from agent_toolkit._atk.serve import assets

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECLARATION = re.compile(
    r"(?:^|;)\s*((?:padding|margin|border-radius)(?:-[\w-]+)?|(?:row-|column-)?gap|"
    r"color|background(?:-color)?|font-size|line-height|box-shadow)\s*:\s*([^;]+)"
)
_LITERAL = re.compile(r"#[0-9a-f]{3,8}\b|\b(?:rgb|hsl)a?\(|(?<![\w.-])-?(?:[1-9]\d*(?:\.\d+)?|0?\.\d+)(?:rem|px|em)\b")
_GEOMETRY = {
    (".visually-hidden", "margin"),
    ("#screen-wi .app-header", "padding-inline"),
    ("#agents-app .agent-identity", "padding-inline-start"),
}


def _unshared_declarations(css: str) -> list[tuple[str, str, str]]:
    violations = []
    for block in _BLOCK.finditer(_COMMENT.sub("", css)):
        selector, body = block.groups()
        selector = selector.strip()
        if selector == ":root" or selector.startswith(".markdown-body") or selector.startswith("#screen-plans .diagram"):
            continue
        for prop, value in _DECLARATION.findall(body):
            value = value.strip()
            if (selector, prop) in _GEOMETRY:
                continue
            if _LITERAL.search(value) or (prop in {"line-height", "font-size"} and re.fullmatch(r"\d+(?:\.\d+)?", value)):
                violations.append((selector, prop, value))
    return violations


@pytest.mark.parametrize("name", [name for name in assets.STYLES if name != "app_markdown.css"])
def test_operation_style_uses_shared_tokens(name: str) -> None:
    assert not _unshared_declarations(assets.STYLES[name])


def test_style_guard_reports_reintroduced_values_inside_a_media_query() -> None:
    assert _unshared_declarations(
        "@media (pointer: coarse) { .detail-toolbar { gap: 7px; font-size: 0.9rem; color: #ddd; } }"
    ) == [
        (".detail-toolbar", "gap", "7px"),
        (".detail-toolbar", "font-size", "0.9rem"),
        (".detail-toolbar", "color", "#ddd"),
    ]
