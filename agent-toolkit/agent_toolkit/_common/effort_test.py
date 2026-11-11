"""実受理段に沿った相対操作と、対応不明・端の区別を検証する。"""

import subprocess

import pytest

from agent_toolkit._common import effort


@pytest.mark.parametrize(
    ("current", "direction", "expected"),
    [("low", "up", "high"), ("high", "down", "low"), ("low", "down", "low"), ("max", "up", "max")],
)
def test_relative_skips_unsupported_levels_and_preserves_boundaries(current, direction, expected):
    assert effort.relative(current, direction, ("low", "high", "max")) == expected


@pytest.mark.parametrize("levels", [(), ("high", "max")])
def test_unknown_current_is_not_a_boundary(levels):
    with pytest.raises(ValueError, match="受理段を確認できない"):
        effort.relative("medium", "up", levels)


def test_catalog_selects_exact_model_and_actual_supported_values():
    catalog = [
        {"model": "other", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
        {"model": "chosen", "supportedReasoningEfforts": [{"reasoningEffort": value} for value in ("max", "low", "high")]},
    ]
    assert effort.catalog_levels("chosen", catalog) == ("low", "high", "max")
    with pytest.raises(ValueError, match="モデルmissingが無い"):
        effort.catalog_levels("missing", catalog)


@pytest.mark.parametrize(
    ("engine", "model", "output", "expected"),
    [
        (
            "agy",
            "gemini",
            "Fetching available models...\ngemini-low\tGemini Low\ngemini-high\tGemini High\nother-medium\tOther",
            ("low", "high"),
        ),
        ("agy", "only", "only-medium\tOnly Medium", ("medium",)),
        ("opencode", "provider/model", 'provider/model\n{\n"id":"model","variants":{"high":{},"low":{}}\n}', ("low", "high")),
    ],
)
def test_cli_catalogs_keep_model_specific_variants(monkeypatch, engine, model, output, expected):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(effort.process_tree, "run_with_timeout", run)
    assert effort.cli_levels(engine, model) == expected
    command, options = calls.pop()
    assert command[:2] == [engine, "models"]
    assert options["timeout"] == 20 and options["check"] is True


def test_claude_skips_xhigh_when_it_would_fall_back_to_high():
    assert effort.relative("high", "up", effort.cli_levels("claude", "claude-opus-5-5")) == "max"
    assert effort.relative("high", "up", effort.cli_levels("claude", "claude-opus-4-7")) == "xhigh"
    with pytest.raises(ValueError, match="未定義"):
        effort.cli_levels("claude", "unmapped")
