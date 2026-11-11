"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import pytest

from agent_toolkit._common.read_triggers import ReadTrigger
from agent_toolkit._hooks.pretooluse import dependency_usage, read_delivery
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_support import (
    _FOREIGN_LOADING_IN_COMMENTS,
    _FOREIGN_LOADING_LINES,
    _assert_acquired,
    _declared_dependencies,
    _guidance,
    _local_plugin_root,
    _memory,
    _notice_body,
)

__all__ = [
    "_FOREIGN_LOADING_IN_COMMENTS",
    "_FOREIGN_LOADING_LINES",
    "_assert_acquired",
    "_declared_dependencies",
    "_guidance",
    "_local_plugin_root",
    "_memory",
    "_notice_body",
]


@pytest.mark.parametrize(
    ("path", "content", "expected"),
    [
        ("document.md", "import sqlalchemy\n", False),
        ("module.py", "import sqlalchemy\n", True),
        ("pyproject.toml", '[project]\ndependencies = ["SQLAlchemy>=2"]\n', True),
        ("pyproject.toml", '[project]\ndescription = "sqlalchemy helpers"\n', False),
    ],
)
def test_dependency_selection_requires_code_or_dependency_definition(tmp_path, path, content, expected):
    target = tmp_path / path
    assert (
        read_delivery.matches("dependency:sqlalchemy", "Write", {"file_path": str(target), "content": content}, str(tmp_path))
        is expected
    )


@pytest.mark.parametrize(
    "section",
    ["project", "project.optional-dependencies", "dependency-groups", "tool.uv"],
)
@pytest.mark.parametrize("change", ["settings", "version", "add", "remove"])
@pytest.mark.parametrize("tool", ["Write", "Edit", "apply_patch"])
@pytest.mark.usefixtures("memory")
def test_public_hook_pyproject_selects_only_dependency_definition_changes(tmp_path, monkeypatch, section, change, tool):
    reference = tmp_path / "playwright.md"
    reference.write_text("# 技術資料\n依存定義の確認\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("dependency:playwright",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    key = {"project": "dependencies", "tool.uv": "dev-dependencies"}.get(section, "test")
    dependency = f'[{section}]\n{key} = ["playwright>=1.61.0"]\n'
    settings = '[tool.pytest.ini_options]\ntmp_path_retention_policy = "failed"\n'
    before = dependency + settings
    after = before
    if change == "settings":
        after = before.replace('"failed"', '"all"')
    elif change == "version":
        after = before.replace("1.61.0", "1.61.1")
    elif change == "add":
        before = before.replace('"playwright>=1.61.0"', "")
    else:
        after = before.replace('"playwright>=1.61.0"', "")
    target = tmp_path / "pyproject.toml"
    target.write_text(before, encoding="utf-8")
    if tool == "Write":
        inputs = {"file_path": str(target), "content": after}
    elif tool == "Edit":
        inputs = {"file_path": str(target), "old_string": before, "new_string": after}
    else:
        removed = "".join("-" + line + "\n" for line in before.splitlines())
        added = "".join("+" + line + "\n" for line in after.splitlines())
        inputs = {"command": f"*** Begin Patch\n*** Update File: {target}\n@@\n{removed}{added}*** End Patch\n"}
    operation = {"session_id": "test", "cwd": str(tmp_path), "tool_name": tool, "tool_input": inputs}
    first = _run_hook_in_process("pretooluse", operation)
    second = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == second.returncode == 0 and not first.stderr and not second.stderr
    if change == "settings":
        assert str(reference) not in first.stdout
        assert not second.stdout
    else:
        assert str(reference) in first.stdout
        assert not second.stdout
        _assert_acquired(operation, reference, False)
        read = _run_hook_in_process(
            "posttooluse",
            {
                "session_id": "test",
                "cwd": str(tmp_path),
                "tool_name": "Read",
                "tool_input": {"file_path": str(reference)},
                "tool_response": reference.read_text(encoding="utf-8"),
            },
        )
        assert read.returncode == 0 and not read.stderr
        _assert_acquired(operation, reference, True)
        resumed = _run_hook_in_process("pretooluse", operation)
        assert resumed.returncode == 0 and not resumed.stderr and not resumed.stdout


@pytest.mark.parametrize(
    ("dependency", "filename", "usage"),
    [
        ("tailwindcss", "styles.css", '@import "tailwindcss";\n'),
        (
            "alpinejs",
            "page.html",
            '<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>\n',
        ),
        ("playwright", "test.ts", "import { test } from '@playwright/test';\n"),
        ("sqlalchemy", "module.py", "from sqlalchemy import select\n"),
        ("drizzle", "schema.ts", "import { pgTable } from 'drizzle-orm/pg-core';\n"),
        ("svelte", "Component.svelte", "<p>hello</p>\n"),
        ("react", "Component.tsx", 'import { useEffect } from "react";\n'),
    ],
)
@pytest.mark.usefixtures("memory")
def test_public_hook_dependency_rows_exclude_markdown_and_select_code(tmp_path, monkeypatch, dependency, filename, usage):
    reference = tmp_path / f"{dependency}.md"
    reference.write_text("# 技術資料\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", (f"dependency:{dependency}",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    markdown = _guidance(
        read_delivery.deliver(
            {"cwd": str(tmp_path)}, "Write", {"file_path": str(tmp_path / "note.md"), "content": usage}, "test"
        )
    )
    code = _notice_body(
        read_delivery.deliver(
            {"cwd": str(tmp_path)}, "Write", {"file_path": str(tmp_path / filename), "content": usage}, "test"
        )
    )
    assert markdown is None
    assert str(reference) in code


def test_dependency_does_not_mix_markdown_content_with_an_unrelated_code_edit(tmp_path):
    patch = "*** Begin Patch\n*** Add File: module.py\n+value = 1\n*** Add File: note.md\n+診断引用: sqlalchemy\n*** End Patch"
    assert not read_delivery.matches("dependency:sqlalchemy", "apply_patch", {"command": patch}, str(tmp_path))


@pytest.mark.usefixtures("memory")
def test_public_hook_dependency_uses_existing_code_for_a_nameless_partial_edit(tmp_path, monkeypatch):
    reference = tmp_path / "sqlalchemy.md"
    reference.write_text("# 技術資料\n", encoding="utf-8")
    code = tmp_path / "module.py"
    code.write_text("import sqlalchemy\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("dependency:sqlalchemy",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    notice = _notice_body(
        read_delivery.deliver({"cwd": str(tmp_path)}, "Write", {"file_path": str(code), "content": "value = 1\n"}, "test")
    )
    assert str(reference) in notice


@pytest.mark.parametrize(
    ("filename", "content", "dependency", "expected"),
    [
        ("module.py", 'sample = """\nimport sqlalchemy\n"""\nvalue = 1\n', "sqlalchemy", False),
        ("module.ts", "// require('playwright')\nconst value = 1;\n", "playwright", False),
        ("module.ts", "const sample = \"require('playwright')\";\n", "playwright", False),
        ("styles.css", '/*\n@import "tailwindcss";\n*/\np { color: red; }\n', "tailwindcss", False),
        (
            "page.html",
            '<!-- <script src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script> -->\n',
            "alpinejs",
            False,
        ),
        ("module.py", "lazy from sqlalchemy import select\n", "sqlalchemy", True),
        ("module.py", "from sqlalchemy import select\n", "sqlalchemy", True),
        ("module.ts", "const p = require('playwright');\n", "playwright", True),
        (
            "page.html",
            '<p>Don\'t wait</p><script src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>\n',
            "alpinejs",
            True,
        ),
        ("module.ts", "const p = `${import('playwright')}`;\n", "playwright", True),
        ("module.ts", "const pattern = /'/; const p = require('playwright');\n", "playwright", True),
        ("module.ts", "const ratio = a / b; const s = 'x';\nconst p = require('playwright');\n", "playwright", True),
        ("page.html", "<div title=\"require('playwright')\"></div>\n", "playwright", False),
        ("page.html", "<div data-example=\"import('playwright')\"></div>\n", "playwright", False),
        ("page.html", "<script>const marker = \"<!--\"; const p = require('playwright');</script>\n", "playwright", True),
        ("page.html", "<script type=\"module\">import { test } from '@playwright/test';</script>\n", "playwright", True),
        ("Component.svelte", "<script>\n// import('playwright')\nconst p = 1;\n</script>\n", "playwright", False),
    ],
    ids=[
        "py-string",
        "js-comment",
        "js-string",
        "css-comment",
        "html-comment",
        "py-lazy",
        "py-import",
        "js-require",
        "html-apostrophe",
        "js-template-expression",
        "js-regex-quote",
        "js-division",
        "html-title-attribute",
        "html-data-attribute",
        "html-script-comment-marker",
        "html-module-script",
        "svelte-script-comment",
    ],
)
@pytest.mark.usefixtures("memory")
def test_public_hook_dependency_ignores_loading_syntax_inside_comments_and_strings(
    tmp_path, monkeypatch, filename, content, dependency, expected
):
    """コメントや文字列リテラルの中に書いた読込記述は使用に数えず、実際の読込記述（Pythonの遅延読込を含む）は数える。"""
    reference = tmp_path / f"{dependency}.md"
    reference.write_text("# 技術資料\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", (f"dependency:{dependency}",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    decision = read_delivery.deliver(
        {"cwd": str(tmp_path)}, "Write", {"file_path": str(tmp_path / filename), "content": content}, "test"
    )
    assert (str(reference) in (_guidance(decision) or "")) is expected


@pytest.mark.parametrize("suffix", sorted(dependency_usage.CODE_SUFFIXES))
@pytest.mark.usefixtures("memory")
def test_public_hook_dependency_applies_only_the_language_rules_of_each_suffix(tmp_path, monkeypatch, suffix):
    """拡張子ごとの言語の規則を適用し、コメントや別の言語の書式だけでは案内と資料選択を成立させない。

    JavaScript・Python・CSS・マークアップはその言語のコメント内の読込記述を数えない。
    読込の規則を持たない言語（シェル、PowerShell、Rust、C#、バッチ）は、どの言語の書式の行も数えない。
    """
    family = {".jsx": ".js", ".mjs": ".js", ".cjs": ".js", ".ts": ".js", ".tsx": ".js", ".mts": ".js", ".cts": ".js"}
    family |= {".htm": ".html", ".svelte": ".html"}
    content = _FOREIGN_LOADING_IN_COMMENTS.get(family.get(suffix) or suffix, _FOREIGN_LOADING_LINES)
    reference = tmp_path / "playwright.md"
    reference.write_text("# 技術資料\n", encoding="utf-8")
    author = tmp_path / "agent-basics/skills/writing-standards/SKILL.md"
    author.parent.mkdir(parents=True)
    author.write_text("# 作成側\n", encoding="utf-8")
    review = ReadTrigger(
        tmp_path / "agent-basics/skills/review-standards/SKILL.md",
        1,
        "判断",
        ("tool:Write",),
        (),
        selector="artifact-standards",
    )
    declared = ReadTrigger(author, 1, "操作", ("dependency:playwright",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [review, declared])
    monkeypatch.setattr(read_delivery.read_triggers, "resolve_document", lambda *_args, **_kwargs: author)
    tool_input = {"file_path": str(tmp_path / f"example{suffix}"), "content": content}
    assert str(reference) not in (_guidance(read_delivery.deliver({"cwd": str(tmp_path)}, "Write", tool_input, "test")) or "")


@pytest.mark.parametrize("tool", ["Edit", "Bash"])
@pytest.mark.usefixtures("memory")
def test_public_hook_dependency_ignores_names_in_strings_comments_and_test_data(tmp_path, monkeypatch, tool):
    """名前が文字列・コメント・テストデータにあるだけのコードを、名前と無関係な箇所で編集しても案内しない。

    2026年10月29日に、6種類の名前をテストパラメーターに持つテストファイルの無関係な行の編集で全種類が案内された。
    """
    dependencies = _declared_dependencies()
    code = tmp_path / "module_test.py"
    code.write_text(
        "import pytest\n\n"
        "# svelte対応は未定\n"
        f"@pytest.mark.parametrize('name', {dependencies!r})\n"
        "def test_name(name):\n"
        "    value = 1\n"
        "    assert name\n",
        encoding="utf-8",
    )
    tool_input = (
        {"file_path": str(code), "old_string": "    value = 1\n", "new_string": "    value = 2\n"}
        if tool == "Edit"
        else {"command": f"sed -i 's/value = 1/value = 2/' {code}"}
    )
    rows = [
        ReadTrigger(tmp_path / "SKILL.md", index, "操作", (f"dependency:{name}",), (tmp_path / f"{name}.md",))
        for index, name in enumerate(dependencies, 1)
    ]
    for row in rows:
        row.documents[0].write_text("# 技術資料\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    assert _guidance(read_delivery.deliver({"cwd": str(tmp_path)}, tool, tool_input, "test")) is None


@pytest.mark.usefixtures("memory")
def test_public_hook_dependency_uses_existing_markup_for_a_nameless_partial_edit(tmp_path, monkeypatch):
    """本文にアポストロフィを含む既存HTMLがscript要素で依存を読み込んでいれば、名前の無い部分編集でも案内する。"""
    reference = tmp_path / "alpinejs.md"
    reference.write_text("# 技術資料\n", encoding="utf-8")
    page = tmp_path / "page.html"
    page.write_text(
        '<p>Don\'t wait</p><script src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>\n',
        encoding="utf-8",
    )
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("dependency:alpinejs",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    tool_input = {"file_path": str(page), "old_string": "Don't wait", "new_string": "Don't wait here"}
    assert str(reference) in _notice_body(read_delivery.deliver({"cwd": str(tmp_path)}, "Edit", tool_input, "test"))
