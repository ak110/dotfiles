import re

import pytest

from agent_toolkit._atk.serve import assets

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECLARATION = re.compile(r"(?:^|;)\s*([\w-]+)\s*:\s*([^;]+)")
_DIAGRAM_CONTENT = {
    "#screen-plans .diagram",
    "#screen-plans .diagram-output",
    "#screen-plans .diagram-source",
    "#screen-plans .diagram-source pre",
    "#screen-plans .diagram-error",
}
_GEOMETRY = {
    (".visually-hidden", "margin"),
    ("::highlight(session-search)", "background-color"),
    ("::highlight(session-search)", "color"),
    ("#agents-app .agent-identity", "padding-inline-start"),
}
_CONTROL_PROPERTY = re.compile(
    r"(?:padding|margin|gap|row-gap|column-gap|font|border-radius)(?:-[\w-]+)?|"
    r"line-height|color|background(?:-color)?|box-shadow|"
    r"border(?:-(?:block|inline|start|end|left|right|top|bottom|color))+|border"
)
_TOKEN = re.compile(r"var\(\s*--[\w-]+\s*\)", re.IGNORECASE)
_KEYWORD = re.compile(r"[a-z][\w-]*", re.IGNORECASE)
_DIMENSION = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[a-z]+|%)", re.IGNORECASE)
_KEYWORDS = {
    "auto",
    "calc",
    "center",
    "currentcolor",
    "dashed",
    "dotted",
    "inherit",
    "initial",
    "none",
    "normal",
    "tabular-nums",
    "revert",
    "revert-layer",
    "solid",
    "transparent",
    "unset",
    "var",
}
_ROLE_SIZE_OWNERS = {
    "var(--font-size-page)": {".page-heading"},
    "var(--font-size-value)": {".metric-value"},
}
_ROLE_SIZE_SELECTORS = re.compile(r"\.(?:page-heading|metric-value)(?![\w-])")


def _uses_shared_values(prop: str, value: str) -> bool:
    if prop == "border-radius" and value == "50%":
        return True
    if value.lower() == "inherit":
        return True
    if prop == "font":
        return bool(_TOKEN.fullmatch(value))
    value = _TOKEN.sub("", value.lower())
    if prop.startswith("border") and not prop.endswith(("color", "radius")):
        value = re.sub(r"\b[12]px(?=\s+(?:solid|dashed|dotted)\b)", "", value)
    if "#" in value or _DIMENSION.search(value):
        return False
    if prop.startswith(("font", "line-height")) and re.search(r"\d", value):
        return False
    return all(word in _KEYWORDS for word in _KEYWORD.findall(value))


def _unshared_declarations(css: str) -> list[tuple[str, str, str]]:
    violations = []
    for block in _BLOCK.finditer(_COMMENT.sub("", css)):
        selector, body = block.groups()
        selector = selector.strip()
        if selector == ":root" or selector.startswith(".markdown-body") or selector in _DIAGRAM_CONTENT:
            continue
        for prop, value in _DECLARATION.findall(body):
            prop, value = prop.lower(), value.strip()
            if (selector, prop) not in _GEOMETRY and _CONTROL_PROPERTY.fullmatch(prop) and not _uses_shared_values(prop, value):
                violations.append((selector, prop, value))
    return violations


@pytest.mark.parametrize("name", [name for name in assets.STYLES if name != "app_markdown.css"])
def test_operation_style_uses_shared_tokens(name: str) -> None:
    assert not _unshared_declarations(assets.STYLES[name])


@pytest.mark.parametrize("name", assets.STYLES)
def test_page_title_and_quota_value_sizes_belong_to_shared_roles(name: str) -> None:
    rules = _BLOCK.findall(_COMMENT.sub("", assets.STYLES[name]))
    for selectors, body in rules:
        for prop, value in _DECLARATION.findall(body):
            if prop != "font-size":
                continue
            tokens = _ROLE_SIZE_OWNERS.get(value.strip(), set())
            if tokens or _ROLE_SIZE_SELECTORS.search(selectors):
                assert name == "app.css"
                assert {selector.strip() for selector in selectors.split(",")} == tokens


@pytest.mark.parametrize(
    ("prop", "value"),
    [
        ("gap", "7px"),
        ("font-size", "0.9rem"),
        ("color", "red"),
        ("border-color", "#ddd"),
        ("border", "2px solid #ddd"),
        ("font", "13px system-ui"),
        ("font-weight", "300"),
        ("padding", "1vw"),
        ("background", "rebeccapurple"),
        ("padding-inline", "var(--space-2, 3vw)"),
    ],
)
def test_style_guard_reports_direct_values_inside_media_query(prop: str, value: str) -> None:
    rule = f"@media (pointer: coarse) {{ .detail-toolbar {{ {prop}: {value}; }} }}"
    assert _unshared_declarations(rule) == [(".detail-toolbar", prop, value)]


@pytest.mark.parametrize(("prop", "value"), [("font-size", "13px"), ("color", "#ddd")])
def test_style_guard_checks_diagram_source_control(prop: str, value: str) -> None:
    selector = "#screen-plans .diagram-source summary"
    assert _unshared_declarations(f"{selector} {{ {prop}: {value}; }}") == [(selector, prop, value)]


def test_style_guard_accepts_shared_values_and_content_geometry() -> None:
    assert not _unshared_declarations(
        ".detail-toolbar { border: 1px solid var(--color-border); font: inherit; padding: var(--space-2); }"
        "#screen-plans .diagram-source summary { font-size: var(--font-size-base); color: var(--color-secondary-text); }"
        "#screen-plans .diagram-source pre { font-size: 0.875rem; }"
        ".markdown-body h2 { font-size: 1.5em; }"
        "#screen-plans .diagram-output { margin: 1rem; }"
        "#agents-app .agent-identity { padding-inline-start: min(calc(var(--agent-depth) * 0.875rem), 20%); }"
    )
