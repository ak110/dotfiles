"""フィードバック追跡表CLIの契約を検証する。"""

import os
import pathlib
import subprocess
import sys

import _atk_mq_frontmatter as frontmatter
import _atk_mq_schedule as schedule

_SCRIPT = pathlib.Path(__file__).with_name("_atk_mq_trace_table.py")
_TARGET_REPO = "github.com/ak110/dotfiles"


def _entry_text(
    title: str,
    *,
    target_files: tuple[str, ...] = ("agent-toolkit/rules/01-agent.md",),
) -> str:
    return _entry_text_from_body(f"# {title}\n\n本文\n", target_files=target_files)


def _entry_text_from_body(
    body: str,
    *,
    target_files: tuple[str, ...] = ("agent-toolkit/rules/01-agent.md",),
) -> str:
    text = frontmatter.serialize_frontmatter(
        {"type": "feedback", "target_repo": _TARGET_REPO},
        body,
    )
    metadata = schedule.ScheduleMetadata(
        body_sha256=schedule.body_sha256(text),
        normalized_target_repo=_TARGET_REPO,
        feedback_type="normal",
        dependency=schedule.Dependency(kind="none"),
        plan_file=None,
        target_files=target_files,
        carry_count=0,
        carry_reasons=(),
    )
    return schedule.serialize_schedule_metadata(text, metadata)


def _write_entry(private_notes: pathlib.Path, filename: str, text: str) -> pathlib.Path:
    path = private_notes / "inbox" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(
    private_notes: pathlib.Path,
    command: str,
    filenames: tuple[str, ...],
    *,
    plan_file: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    arguments = [
        sys.executable,
        str(_SCRIPT),
        command,
        "--target-repo",
        _TARGET_REPO,
    ]
    for filename in filenames:
        arguments.extend(("--filename", filename))
    if plan_file is not None:
        arguments.extend(("--plan-file", str(plan_file)))
    environment = os.environ.copy()
    environment["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(private_notes)
    return subprocess.run(arguments, capture_output=True, text=True, check=False, env=environment)


def test_generate_preserves_requested_filename_order(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    _write_entry(private_notes, "first.md", _entry_text("先頭"))
    _write_entry(private_notes, "second.md", _entry_text("末尾"))

    result = _run(private_notes, "generate", ("second.md", "first.md"))

    assert result.returncode == 0
    assert result.stdout.index("second.md") < result.stdout.index("first.md")


def test_generate_uses_queue_body_title_hash_and_classified_targets(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    text = _entry_text("原題", target_files=("one.py", "two.md"))
    _write_entry(private_notes, "feedback.md", text)

    result = _run(private_notes, "generate", ("feedback.md",))

    assert result.returncode == 0
    assert "| `feedback.md` | 原題 |" in result.stdout
    assert schedule.body_sha256(text) in result.stdout
    assert "one.py<br>two.md" in result.stdout


def test_generate_accepts_headingless_feedback(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    text = frontmatter.serialize_frontmatter(
        {"type": "feedback", "target_repo": _TARGET_REPO},
        "本文先頭行\n\n続き\n",
    )
    metadata = schedule.ScheduleMetadata(
        body_sha256=schedule.body_sha256(text),
        normalized_target_repo=_TARGET_REPO,
        feedback_type="normal",
        dependency=schedule.Dependency(kind="none"),
        plan_file=None,
        target_files=("one.py",),
        carry_count=0,
        carry_reasons=(),
    )
    _write_entry(private_notes, "feedback.md", schedule.serialize_schedule_metadata(text, metadata))

    result = _run(private_notes, "generate", ("feedback.md",))

    assert result.returncode == 0
    assert "| `feedback.md` | 本文先頭行 |" in result.stdout


def test_generate_preserves_non_heading_hash_prefixes(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    _write_entry(private_notes, "literal.md", _entry_text_from_body("#literal\n本文\n"))
    _write_entry(private_notes, "seven.md", _entry_text_from_body("####### literal\n本文\n"))

    result = _run(private_notes, "generate", ("literal.md", "seven.md"))

    assert result.returncode == 0
    assert "| `literal.md` | #literal |" in result.stdout
    assert "| `seven.md` | ####### literal |" in result.stdout


def test_generate_escapes_markdown_table_cells(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    _write_entry(private_notes, "feedback.md", _entry_text("縦棒|を含む", target_files=("one|two.py",)))

    result = _run(private_notes, "generate", ("feedback.md",))

    assert result.returncode == 0
    assert "縦棒\\|を含む" in result.stdout
    assert "one\\|two.py" in result.stdout


def test_generate_rejects_duplicate_filenames(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    _write_entry(private_notes, "feedback.md", _entry_text("原題"))

    result = _run(private_notes, "generate", ("feedback.md", "feedback.md"))

    assert result.returncode == 2
    assert "重複" in result.stderr


def test_generate_rejects_unknown_filename(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path / "private-notes", "generate", ("missing.md",))

    assert result.returncode == 2
    assert "存在しません" in result.stderr


def test_generate_rejects_broken_frontmatter(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    _write_entry(private_notes, "broken.md", "---\ninvalid: [\n---\n# 原題\n")

    result = _run(private_notes, "generate", ("broken.md",))

    assert result.returncode == 2
    assert "frontmatter" in result.stderr


def test_generate_rejects_missing_classification_metadata(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    text = frontmatter.serialize_frontmatter(
        {"type": "feedback", "target_repo": _TARGET_REPO},
        "# 原題\n",
    )
    _write_entry(private_notes, "unclassified.md", text)

    result = _run(private_notes, "generate", ("unclassified.md",))

    assert result.returncode == 2
    assert "分類メタデータ" in result.stderr


def test_check_accepts_exact_table_once_in_background(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    entry = _write_entry(private_notes, "feedback.md", _entry_text("原題"))
    generated = _run(private_notes, "generate", ("feedback.md",))
    presentations = ("`feedback.md`\n\n```text\n# 原題\n\n本文\n```",)
    for index, presentation in enumerate(presentations):
        plan = tmp_path / f"plan-{index}.md"
        plan.write_text(
            f"# 計画\n\n## 背景\n\n### 提示素材\n\n{presentation}\n\n"
            f"### フィードバック追跡表\n\n{generated.stdout}\n## 変更内容\n",
            encoding="utf-8",
        )
        before = (entry.read_bytes(), plan.read_bytes())

        result = _run(private_notes, "check", ("feedback.md",), plan_file=plan)

        assert result.returncode == 0
        assert not result.stdout
        assert not result.stderr
        assert before == (entry.read_bytes(), plan.read_bytes())


def test_check_ignores_selected_filename_heading_inside_source_body(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    _write_entry(private_notes, "first.md", _entry_text_from_body("# 第一\n\n## second.md\n原文内見出し\n"))
    _write_entry(private_notes, "second.md", _entry_text("第二"))
    generated = _run(private_notes, "generate", ("first.md", "second.md"))
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# 計画\n\n## 背景\n\n### 提示素材\n\n`first.md`\n\n"
        "```text\n# 第一\n\n## second.md\n原文内見出し\n```\n\n"
        "`second.md`\n\n```text\n# 第二\n\n本文\n```\n\n### フィードバック追跡表\n\n"
        f"{generated.stdout}\n",
        encoding="utf-8",
    )

    result = _run(private_notes, "check", ("first.md", "second.md"), plan_file=plan)

    assert result.returncode == 0
    assert not result.stderr


def test_check_rejects_modified_source_body_or_invisible_intervening_block(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    _write_entry(private_notes, "feedback.md", _entry_text("原題"))
    generated = _run(private_notes, "generate", ("feedback.md",))
    variants = (
        "`feedback.md`\n\n```text\n# 原題\n\n改変\n```",
        "`feedback.md`\n\n```text\n# 原題\n```",
        "`feedback.md`\n\n[ref]: https://example.com\n\n```text\n# 原題\n\n本文\n```",
    )
    for index, presentation in enumerate(variants):
        plan = tmp_path / f"modified-{index}.md"
        plan.write_text(
            f"# 計画\n\n## 背景\n\n### 提示素材\n\n{presentation}\n\n### フィードバック追跡表\n\n{generated.stdout}",
            encoding="utf-8",
        )

        result = _run(private_notes, "check", ("feedback.md",), plan_file=plan)

        assert result.returncode == 1


def test_check_rejects_missing_modified_or_duplicate_table(tmp_path: pathlib.Path) -> None:
    private_notes = tmp_path / "private-notes"
    entry = _write_entry(private_notes, "feedback.md", _entry_text("原題"))
    generated = _run(private_notes, "generate", ("feedback.md",))
    presentation = "### 提示素材\n\n`feedback.md`\n\n```text\n# 原題\n\n本文\n```\n\n### フィードバック追跡表\n\n"
    compact_header_table = generated.stdout.replace(
        "| ファイル名 | 原題 | 本文SHA-256 | 想定変更対象 |",
        "|ファイル名|原題|本文SHA-256|想定変更対象|",
    ).replace("| 原題 | `", "| 改変 | `")
    variants = (
        f"# 計画\n\n## 背景\n\n{presentation}表なし\n",
        f"# 計画\n\n## 背景\n\n{presentation}{generated.stdout.replace('原題', '改変')}\n",
        f"# 計画\n\n```markdown\n## 背景\n\n{presentation}{generated.stdout}\n```\n",
        f"# 計画\n\n## 背景\n\n{presentation}{generated.stdout}| `feedback.md` | 原題 | `{'0' * 64}` | one.py |\n",
        f"# 計画\n\n## 背景\n\n### 提示素材\n\n`feedback.md`\n\n```text\n# 原題\n```\n\n"
        f"`feedback.md`\n\n```text\n# 原題\n```\n\n{generated.stdout}\n",
        f"# 計画\n\n## 背景\n\n### 提示素材\n\n```text\n## feedback.md\n# 原題\n```\n\n"
        f"### フィードバック追跡表\n\n{generated.stdout}\n",
        f"# 計画\n\n## 背景\n\n### 提示素材\n\n#### `feedback.md`\n\n```text\n# 原題\n```\n\n"
        f"### フィードバック追跡表\n\n{generated.stdout}\n",
        f"# 計画\n\n## 背景\n\n### 提示素材\n\n> `feedback.md`\n>\n> ```text\n> # 原題\n> ```\n\n"
        f"### フィードバック追跡表\n\n{generated.stdout}\n",
        f"# 計画\n\n## 背景\n\n### 提示素材\n\n`feedback.md`\n\n---\n\n```text\n# 原題\n```\n\n"
        f"### フィードバック追跡表\n\n{generated.stdout}\n",
        f"# 計画\n\n## 背景\n\n### 提示素材\n\n`feedback.md`\n\n```markdown\n# 原題\n```\n\n"
        f"### フィードバック追跡表\n\n{generated.stdout}\n",
        f"# 計画\n\n## 背景\n\n### 提示素材\n\n```text\nラベル無し\n```\n\n"
        f"`feedback.md`\n\n```text\n# 原題\n```\n\n### フィードバック追跡表\n\n{generated.stdout}\n",
    )
    for index, text in enumerate(variants):
        plan = tmp_path / f"plan-{index}.md"
        plan.write_text(text, encoding="utf-8")
        before = (entry.read_bytes(), plan.read_bytes())

        result = _run(private_notes, "check", ("feedback.md",), plan_file=plan)

        assert result.returncode == 1
        assert before == (entry.read_bytes(), plan.read_bytes())

    duplicate_tables = (
        f"{generated.stdout}\n\n### 別表\n\n{generated.stdout}",
        f"{generated.stdout}\n\n### 別表\n\n{generated.stdout.replace('| 原題 | `', '| 改変 | `')}",
        f"{generated.stdout}\n\n### 別表\n\n{compact_header_table}",
    )
    for index, tables in enumerate(duplicate_tables):
        plan = tmp_path / f"duplicate-{index}.md"
        plan.write_text(f"# 計画\n\n## 背景\n\n{presentation}{tables}\n", encoding="utf-8")

        result = _run(private_notes, "check", ("feedback.md",), plan_file=plan)

        assert result.returncode == 1
        assert result.stderr.strip() == "背景節のフィードバック追跡表が1件ではありません: 実際=2件"
