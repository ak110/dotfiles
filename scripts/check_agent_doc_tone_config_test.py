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
    """外部規範の表記を保持する設定でも、リンクの確認へは到達する。"""
    repository = pathlib.Path(__file__).resolve().parents[1]
    config = pyfltr.config.config.load_config(repository, global_config_path=tmp_path / "absent.toml")
    external = pathlib.Path(".chezmoi-source/dot_gemini/antigravity-cli/skills/japanese-tech-writing/SKILL.md")
    assert pyfltr.command.targets.excluded(external, config) is None
    assert pyfltr.command.targets.matches_exclude_patterns(external, config["lychee-exclude"], base_dir=repository) is None
    for command in ("textlint", "colloquial-check"):
        assert pyfltr.command.targets.matches_exclude_patterns(external, config[f"{command}-exclude"], base_dir=repository)


def test_project_checks_include_extensionless_shell_targets(tmp_path: pathlib.Path) -> None:
    """binのbashを現在と将来の名前によらず対象にし、他の言語は区別する。"""
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
    """ルールの実装が更新された場合も、現在の実装で本文を確認する設定を保つ。"""
    repository = pathlib.Path(__file__).resolve().parents[1]
    config = pyfltr.config.config.load_config(repository, global_config_path=tmp_path / "absent.toml")
    assert "--cache" not in config["textlint-args"]


@pytest.mark.parametrize("config_path", ["pyproject.toml", "agent-toolkit/pyproject.toml"])
def test_project_pytest_reports_skip_reasons(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], config_path: str
) -> None:
    """通常のpytest設定を通した未実行のテストは、要約に理由を残す。"""
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
            str(repository / config_path),
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
            '# 既定の値を使う。\nprint("照合する値")\n',
            'raise RuntimeError("既定の値を照合できない")\n',
            "",
            '{"input_path": "照合.md"}',
            False,
            1,
            ["正本", "既定", "照合", "tone-examples.md", "new_module.py", "helper.py"],
            [],
        ),
        (
            "```text\n例文: 記録した値を読む。\n```\n",
            'from pathlib import Path\npath = Path("照合.md")\nprint("是正本文")\n',
            "",
            "",
            '{"input_path": "照合.md"}',
            False,
            0,
            [],
            [],
        ),
        (
            "```text\n例文: 記録した値を読む。\n```\n",
            "# 入力の経路を記録する。\nprint(1)\n",
            "",
            "",
            '{"input_path": "照合.md"}',
            False,
            0,
            ['"kind": "diagnostic"', "new_module.py", '"line": 1', "警告: 「経路」"],
            [],
        ),
        (
            "```text\n例文: 記録した値を読む。\n```\n",
            'print("是正本文")\n',
            "",
            "APIの入口を確認しない。" * 20,
            '{"input_path": "照合.md"}',
            False,
            1,
            ["警告: 「入口」", "否定形終端率", '"line": 1'],
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
            '{"input_path": "照合.md"}',
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
            '{"input_path": "照合.md"}',
            False,
            1,
            ["否定形終端率 13.6%", "「値は書かない。」", "「当該」の出現率 9.1%"],
            [7, 9, 10, 11, 11],
        ),
    ],
    ids=["violations", "compliant", "caution", "caution-and-density", "parse-error", "read-error", "density-positions"],
)
# 子処理の時間上限（120秒）より大きいテスト単位の上限を置き、停止時は子処理の時間切れとして報告させる。
# CIで全チェックを並行して実行するジョブでは子処理に約40秒を要したため、子処理の上限は観測値の約3倍とする。
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
    """通常の登録対象を検査するチェックが、例の良い文と新しいコードの説明へ到達し、警告の語を位置付きの診断として表示する。"""
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
    density = tmp_path / "agent-toolkit" / "share" / "density.md"
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
    # 消費側が本文を解析せずに区別できるよう、注意語は`warning`、それ以外の全診断は`error`の重大度を持つ。
    messages = [message for record in records if record["kind"] == "diagnostic" for message in record["messages"]]
    for message in messages:
        expected_severity = "warning" if message["msg"].startswith("警告: ") else "error"
        assert message.get("severity") == expected_severity, message
