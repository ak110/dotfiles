"""文体検査の主題別契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tomllib
from collections.abc import Callable

import pyfltr.command.targets
import pyfltr.config.config
import pytest
from _scripts_test_helpers import _DENSITY_BODY, _write


def test_project_checks_keep_external_norm_links(tmp_path: pathlib.Path) -> None:
    repository = pathlib.Path(__file__).resolve().parents[1]
    config = pyfltr.config.config.load_config(repository, global_config_path=tmp_path / "absent.toml")
    external = pathlib.Path(".chezmoi-source/dot_gemini/antigravity-cli/skills/japanese-tech-writing/SKILL.md")
    assert pyfltr.command.targets.excluded(external, config) is None
    assert pyfltr.command.targets.matches_exclude_patterns(external, config["lychee-exclude"], base_dir=repository) is None
    for command in ("textlint", "colloquial-check"):
        assert pyfltr.command.targets.matches_exclude_patterns(external, config[f"{command}-exclude"], base_dir=repository)


def test_project_checks_include_extensionless_shell_targets(tmp_path: pathlib.Path) -> None:
    repository = pathlib.Path(__file__).resolve().parents[1]
    config = pyfltr.config.config.load_config(repository, global_config_path=tmp_path / "absent.toml")
    bash_paths = [path.relative_to(repository) for path in (repository / "bin").iterdir() if not path.suffix]
    assert bash_paths
    for command in ("shellcheck", "shfmt"):
        candidates = [*bash_paths, pathlib.Path("bin/new-cli"), pathlib.Path("bin/script.py"), pathlib.Path("bin/cli.cmd")]
        globs = [config[f"{command}-targets"], *config[f"{command}-extend-targets"]]
        selected = pyfltr.command.targets.filter_by_globs(candidates, globs)
        selected = [
            path
            for path in selected
            if pyfltr.command.targets.matches_exclude_patterns(path, config[f"{command}-exclude"], base_dir=repository) is None
        ]
        assert set(selected) == {*bash_paths, pathlib.Path("bin/new-cli")}


def test_project_textlint_runs_current_rules(tmp_path: pathlib.Path) -> None:
    repository = pathlib.Path(__file__).resolve().parents[1]
    config = pyfltr.config.config.load_config(repository, global_config_path=tmp_path / "absent.toml")
    assert "--cache" not in config["textlint-args"]


def test_project_pytest_reports_skip_reasons(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    repository = pathlib.Path(__file__).resolve().parents[1]
    sample = _write(
        tmp_path,
        'import pytest\n\ndef test_sample():\n    pytest.skip("G6 skip evidence")\n',
        name="sample_test.py",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            str(repository / "pyproject.toml"),
            "--noconftest",
            "-n",
            "0",
            str(sample),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=host_environ(),
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIPPED [1]" in result.stdout
    assert "G6 skip evidence" in result.stdout


@pytest.mark.parametrize(
    (
        "example_body",
        "application_body",
        "helper_tail",
        "density_body",
        "config_body",
        "invalid_application_encoding",
        "expected_returncode",
        "expected_output",
        "expected_density_lines",
    ),
    [
        (
            "```text\n例文: 正本を読む。\n```\n",
            '# 失効が効く。\nprint("足すものがない")\n',
            'raise RuntimeError("正本を読めない")\n',
            "",
            '{"input_path": "正本.md"}',
            False,
            1,
            ["正本", "失効が効く", "足すものがない", "tone-examples.md", "new_module.py", "helper.py"],
            [],
        ),
        (
            "```text\n例文: 記録した値を読む。\n```\n",
            'from pathlib import Path\npath = Path("正本.md")\nprint("是正本文")\n',
            "",
            "",
            '{"input_path": "正本.md"}',
            False,
            0,
            [],
            [],
        ),
        (
            "```text\n例文: 記録した値を読む。\n```\n",
            "# 入力の受け口を記録する。\nprint(1)\n",
            "",
            "",
            '{"input_path": "正本.md"}',
            False,
            0,
            ['"kind": "diagnostic"', "new_module.py", '"line": 1', "警告: 「受け口」"],
            [],
        ),
        (
            "```text\n例文: 記録した値を読む。\n```\n",
            'print("是正本文")\n',
            "",
            "APIの受け口を確認しない。" * 20,
            '{"input_path": "正本.md"}',
            False,
            0,
            ["警告: 「受け口」", "警告: 否定形終端率", '"line": 1'],
            [1] * 20,
        ),
        (
            "```text\n例文: 記録した値を読む。\n```\n",
            'print("是正本文")\n',
            "",
            "",
            "{\n",
            False,
            1,
            ["入力の構文を読み取れない", "config.json", '"line": 2', '"rc": 2'],
            [],
        ),
        (
            "```text\n例文: 記録した値を読む。\n```\n",
            'print("是正本文")\n',
            "",
            "",
            '{"input_path": "正本.md"}',
            True,
            1,
            ["入力を読み取れない", "new_module.py", '"line": 1', '"rc": 2'],
            [],
        ),
        (
            "```text\n例文: 記録した値を読む。\n```\n",
            'print("是正本文")\n',
            "",
            _DENSITY_BODY,
            '{"input_path": "正本.md"}',
            False,
            1,
            ["警告: 否定形終端率 26.1%", "「値は書かない。」", "「当該」の出現率 8.7%"],
            [7, 7, 7, 9, 10, 10, 11, 11],
        ),
    ],
    ids=["violations", "compliant", "caution", "caution-and-density", "parse-error", "read-error", "density-positions"],
)
@pytest.mark.timeout(180)
def test_registered_pyfltr_check_reaches_examples_and_code(
    tmp_path: pathlib.Path,
    example_body: str,
    application_body: str,
    helper_tail: str,
    density_body: str,
    config_body: str,
    invalid_application_encoding: bool,
    expected_returncode: int,
    expected_output: list[str],
    expected_density_lines: list[int],
) -> None:
    repository = pathlib.Path(__file__).resolve().parents[1]
    settings = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pyfltr"]
    command = settings["custom-commands"]["agent-doc-tone"]
    arguments_for_check = [
        str(repository / "scripts/check_agent_doc_tone.py"),
        "--term-root",
        str(tmp_path / "agent-toolkit"),
        "--term-root",
        str(tmp_path / "agent-basics"),
    ]
    config = (
        "[tool.pyfltr]\nagent-doc-tone-subproject-aware = false\n"
        f"extend-exclude = {json.dumps(settings['extend-exclude'])}\n"
        "[tool.pyfltr.custom-commands.agent-doc-tone]\ntype = 'linter'\n"
        f"path = {json.dumps(sys.executable)}\n"
        f"args = {json.dumps(arguments_for_check)}\n"
        f"targets = {json.dumps(command['targets'])}\n"
        f"error-pattern = {json.dumps(command['error-pattern'])}\n"
    )
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    example = tmp_path / "agent-basics/skills/writing-standards/references/tone-examples.md"
    application = tmp_path / "agent-toolkit" / "agent_toolkit" / "new_module.py"
    helper = application.parent / "_testing" / "helper.py"
    path_config = tmp_path / "agent-toolkit" / "config.json"
    density = tmp_path / "agent-toolkit" / "skills" / "atk-mode" / "roles" / "density.md"
    example.parent.mkdir(parents=True)
    application.parent.mkdir(parents=True)
    helper.parent.mkdir(parents=True)
    density.parent.mkdir(parents=True)
    example.write_text(example_body, encoding="utf-8")
    application.write_text(application_body, encoding="utf-8")
    fixture_data = '# agent-doc-tone: test-data\nsample = "既定の値を照合する。"\n'
    helper.write_text(fixture_data + helper_tail, encoding="utf-8")
    path_config.write_text(config_body, encoding="utf-8")
    density.write_text(density_body, encoding="utf-8")
    if invalid_application_encoding:
        application.write_bytes(b"\xff")
    arguments = [
        sys.executable,
        "-m",
        "pyfltr",
        "run",
        "--commands=agent-doc-tone",
        "--no-fix",
        "--no-cache",
        "--output-format=jsonl",
        "--work-dir",
        str(tmp_path),
        str(example),
        str(application),
        str(helper),
        str(path_config),
        str(density),
    ]

    result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)

    assert result.returncode == expected_returncode, result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    decoded = json.dumps(records, ensure_ascii=False)
    for expected in expected_output:
        assert expected in decoded
    density_messages = [
        message
        for record in records
        if record["kind"] == "diagnostic" and record["file"].endswith("density.md")
        for message in record["messages"]
    ]
    element_lines = [
        message["line"]
        for message in density_messages
        if "否定形で終わる文: " in message["msg"] or "数えた「当該」: " in message["msg"]
    ]
    assert element_lines == expected_density_lines, decoded

    messages = [message for record in records if record["kind"] == "diagnostic" for message in record["messages"]]
    for message in messages:
        expected_severity = "warning" if message["msg"].startswith("警告: ") else "error"
        assert message.get("severity") == expected_severity, message
