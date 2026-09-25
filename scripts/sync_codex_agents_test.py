"""sync_codex_agentsのテスト。"""

import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import sync_codex_agents as subject

_TWO_LAYER_WAIT_HEADING = "## agents_serverの二層待機"


def _root(
    tmp_path: Path,
    *,
    project: str = "project\n",
    max_bytes: int = 128 * 1024,
    warn_ratio: float = 0.8,
) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "agent-toolkit/rules").mkdir(parents=True)
    (tmp_path / "agent-toolkit/share").mkdir(parents=True)
    (tmp_path / ".chezmoi-source/dot_codex").mkdir(parents=True)
    (tmp_path / "agent-toolkit/share/rules-main.codex.md").write_text("base\n", encoding="utf-8")
    (tmp_path / ".chezmoi-source/dot_claude/rules").mkdir(parents=True)
    (tmp_path / subject.PERSONAL_SOURCE).write_text("personal\n", encoding="utf-8")
    (tmp_path / subject.CODEX_CONFIG).write_text(
        f"project_doc_max_bytes = {max_bytes}\nproject_doc_warn_ratio = {warn_ratio}\ntool_output_token_limit = 20000\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text(project, encoding="utf-8")
    return tmp_path


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^{re.escape(heading)}\n(?P<body>.*?)(?=^#{{1,3}} |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match is not None
    return match.group("body")


def test_render_preserves_rules_in_sorted_order(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/02-b.md").write_text("second\n\n", encoding="utf-8")
    (root / "agent-toolkit/rules/01-a.md").write_text("first\n", encoding="utf-8")

    content = subject.render(root)

    assert content.startswith(subject.GENERATED_MARKER + "\n\nbase\n")
    assert content.index('path="agent-toolkit/rules/01-a.md"') < content.index('path="agent-toolkit/rules/02-b.md"')
    assert f'path="agent-toolkit/rules/01-a.md">\nfirst\n</{subject.NORMATIVE_ELEMENT}>' in content
    assert content.endswith(f"</{subject.NORMATIVE_ELEMENT}>\n")


def test_render_includes_all_common_rules(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/01-agent.md").write_text("first\n", encoding="utf-8")
    (root / "agent-toolkit/rules/02-agent-operations.md").write_text("second\n", encoding="utf-8")

    content = subject.render(root)

    assert 'path="agent-toolkit/rules/01-agent.md"' in content
    assert 'path="agent-toolkit/rules/02-agent-operations.md"' in content


def test_render_embeds_personal_project_rule(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/01-a.md").write_text("rule\n", encoding="utf-8")

    content = subject.render(root)

    marker = subject.PERSONAL_SOURCE.as_posix()
    assert f'path="{marker}">\npersonal\n</{subject.NORMATIVE_ELEMENT}>' in content
    assert content.index(marker) < content.index('path="agent-toolkit/rules/01-a.md"')


def test_sync_is_idempotent(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/01-a.md").write_text("rule\n", encoding="utf-8")
    assert subject.sync(root) is True
    mtime = (root / subject.TARGET).stat().st_mtime_ns
    assert subject.sync(root) is False
    assert (root / subject.TARGET).stat().st_mtime_ns == mtime


def test_two_layer_wait_contract_is_owned_by_delegation_skill() -> None:
    source = (subject.REPO_ROOT / "agent-toolkit/share/rules-main.codex.md").read_text(encoding="utf-8")
    reference = (subject.REPO_ROOT / "agent-toolkit/skills/delegation/references/codex-runtime.md").read_text(encoding="utf-8")
    generated = (subject.REPO_ROOT / subject.TARGET).read_text(encoding="utf-8")
    assert generated == subject.render()

    assert _TWO_LAYER_WAIT_HEADING not in source
    assert _TWO_LAYER_WAIT_HEADING not in generated
    section = _section(reference, _TWO_LAYER_WAIT_HEADING)
    assert {"`functions.exec`", "`atk agents wait`", "`cell_id`", "`functions.wait`"} <= set(re.findall(r"`[^`]+`", section))
    assert "タスク固有timeoutを渡さず" in section
    assert "対象が未終端なら" in section
    assert "前景のCLIが本文を返した後の逐次待機は新しいrunへ進む" in section
    assert "先行CLIが稼働中にlock競合した後発待機だけが、先行runの本文を1回回収する" in section


def test_main_help_does_not_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--help`は終了コード0で生成処理へ到達しない。"""
    monkeypatch.setattr(subject, "sync", lambda: pytest.fail("syncを呼び出した"))

    with pytest.raises(SystemExit) as exc_info:
        subject.main(["--help"])

    assert exc_info.value.code == 0


def test_size_failure_does_not_replace_output(tmp_path: Path) -> None:
    root = _root(tmp_path, project="project\n", max_bytes=1)
    target = root / subject.TARGET
    target.write_text("old\n", encoding="utf-8")
    (root / "agent-toolkit/rules/01-a.md").write_text("large\n", encoding="utf-8")
    with pytest.raises(ValueError, match="超える"):
        subject.sync(root)
    assert target.read_text(encoding="utf-8") == "old\n"


def test_sync_warns_at_threshold_and_writes_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _root(tmp_path, max_bytes=1_000, warn_ratio=0.1)
    (root / "agent-toolkit/rules/01-a.md").write_text("rule\n", encoding="utf-8")

    assert subject.sync(root) is True
    assert "警告: Codex instruction chainが" in capsys.readouterr().err
    assert (root / subject.TARGET).is_file()


def test_sync_does_not_warn_below_threshold(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _root(tmp_path, max_bytes=10_000, warn_ratio=0.8)
    assert subject.sync(root) is True
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("warn_ratio", [0, -0.1, 1.1])
def test_sync_rejects_out_of_range_warn_ratio(tmp_path: Path, warn_ratio: float) -> None:
    root = _root(tmp_path, warn_ratio=warn_ratio)
    with pytest.raises(ValueError, match="0より大きく1以下"):
        subject.sync(root)


def test_config_template_applies_shared_limits_and_preserves_existing_values() -> None:
    """chezmoiの実行結果へ共有設定を反映し、無関係な利用者設定を保持する。"""
    template = subject.REPO_ROOT / ".chezmoi-source/dot_codex/modify_private_config.toml"
    result = subprocess.run(
        [
            "chezmoi",
            "execute-template",
            "--file",
            str(template),
            "--with-stdin",
            "--working-tree",
            str(subject.REPO_ROOT),
        ],
        input=('model = "gpt-test"\nproject_doc_max_bytes = 1\ntool_output_token_limit = 2\n[features]\nuser_feature = true\n'),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = tomllib.loads(result.stdout)
    assert rendered["project_doc_max_bytes"] == 262144
    assert rendered["tool_output_token_limit"] == 20000
    assert rendered["suppress_unstable_features_warning"] is True
    assert rendered["model"] == "gpt-test"
    assert rendered["features"]["reasoning_effort_override"] is True
    assert rendered["features"]["user_feature"] is True


def test_shared_rule_references_resolve_from_codex_and_claude_distribution() -> None:
    """共有ルールの参照資料が両配布経路のplugin rootから解決できることを固定する。"""
    skill_pattern = re.compile(r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`")
    reference_pattern = re.compile(r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`の`(?P<reference>references/[A-Za-z0-9._/-]+)`")
    rule_paths = sorted((subject.REPO_ROOT / "agent-toolkit/rules").glob("*.md"))
    source_rules = "\n".join(path.read_text(encoding="utf-8") for path in rule_paths)
    generated = subject.render()

    for distribution_text in (source_rules, generated):
        assert "../skills/" not in distribution_text
        for skill_name in set(skill_pattern.findall(distribution_text)):
            assert (subject.REPO_ROOT / "agent-toolkit/skills" / skill_name / "SKILL.md").is_file(), skill_name
        matches = reference_pattern.findall(distribution_text)
        assert matches
        for skill_name, reference in matches:
            skill_root = subject.REPO_ROOT / "agent-toolkit/skills" / skill_name
            assert (skill_root / "SKILL.md").is_file(), skill_name
            assert (skill_root / reference).is_file(), reference


@pytest.mark.parametrize(
    ("body", "wrapped"),
    [
        ('<agent-toolkit-auto-inserted source="x" kind="y" path="z">\nbody\n</agent-toolkit-auto-inserted>', False),
        ("<agent-toolkit-auto-inserted>\nbody\n</agent-toolkit-auto-inserted>", False),
        ('<agent-toolkit-auto-inserted-extra source="x">\nbody\n</agent-toolkit-auto-inserted-extra>', True),
        ("plain body", True),
    ],
)
def test_embedded_section_detects_boundary_by_exact_element_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, wrapped: bool
) -> None:
    """要素名が完全一致する本文だけを境界付きとみなし、別要素の本文は境界で囲む。"""
    root = _root(tmp_path)
    relative = Path("agent-toolkit/rules/00-sample.md")
    (root / relative).write_text(body + "\n", encoding="utf-8")

    original_sync = subject.sync
    monkeypatch.setattr(subject, "sync", lambda: original_sync(root))
    assert subject.main([]) == 0
    rendered = (root / subject.TARGET).read_text(encoding="utf-8")
    sample = rendered.split('path="agent-toolkit/rules/00-sample.md">', 1)[-1]

    if wrapped:
        assert sample.startswith(f"\n{body}\n</{subject.NORMATIVE_ELEMENT}>")
    else:
        assert f"\n{body}\n" in rendered
        assert 'path="agent-toolkit/rules/00-sample.md">' not in rendered
