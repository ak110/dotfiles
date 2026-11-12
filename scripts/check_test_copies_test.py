"""独立した合成入力で候補・例外・母集団・失敗終了の公開CLI契約を検証する。"""

import json
import pathlib

import check_test_copies
import pytest
from agent_toolkit._testing.git_repository import init_repository, run_git


@pytest.mark.parametrize(
    ("filename", "source", "function_id", "kind"),
    [
        (
            "sample_test.py",
            'def test_source():\n    source = Path("app.py").read_text()\n    assert "hello" in source\n',
            "test_source",
            "text-only",
        ),
        (
            "sample_spec.py",
            'def test_registry():\n    assert module.REGISTRY == {"a", "b"}\n',
            "test_registry",
            "constant-only",
        ),
        (
            "sample_test.py",
            'def check_words(text):\n    for word in ("a", "b"):\n        assert word in text\n'
            'def test_help():\n    result = subprocess.run(["tool", "--help"])\n'
            "    assert result.returncode == 0\n    check_words(result.stdout)\n",
            "test_help",
            "text-only",
        ),
        (
            "sample_test.py",
            '@pytest.mark.parametrize("word", ["a", "b"])\ndef test_help(word):\n    assert word in module.DESCRIPTION\n',
            "test_help",
            "constant-only",
        ),
        (
            "sample.test.ts",
            'test("help", () => { const help = readFileSync("app.ts"); expect(help).toContain("hello"); });',
            "help@1",
            "text-only",
        ),
        (
            "sample.test.tsx",
            'it("registry", () => { expect(module.REGISTRY).toEqual(["a"]); });',
            "registry@1",
            "constant-only",
        ),
        ("sample.rs", '#[test]\nfn registry() { assert_eq!(module::REGISTRY, ["a"]); }', "registry@2", "constant-only"),
        (
            "sample.rs",
            '#[test]\nfn words() { let source = include_str!("app.rs"); '
            'for word in ["a", "b"] { assert!(source.contains(word)); } }',
            "words@2",
            "text-only",
        ),
        (
            "sample.test.ts",
            'function words(text: string) { for (const word of ["a", "b"]) { expect(text).toContain(word); } }\n'
            'test("helper", () => { words(readFileSync("app.ts")); });',
            "helper@2",
            "text-only",
        ),
        (
            "sample.rs",
            'fn words(text: &str) { assert!(text.contains("a")); }\n#[test]\nfn helper() { words(include_str!("app.rs")); }',
            "helper@3",
            "text-only",
        ),
        (
            "sample_test.py",
            'def test_words():\n    assert all(word in module.DESCRIPTION for word in ["a", "b"])\n',
            "test_words",
            "constant-only",
        ),
        (
            "sample_test.py",
            'class TestRegistry:\n    def test_value(self):\n        self.assertEqual(module.REGISTRY, ["a"])\n',
            "TestRegistry.test_value",
            "constant-only",
        ),
        (
            "sample.ts",
            'test("callback", function () { expect(module.REGISTRY).toEqual(["a"]); });',
            "callback@1",
            "constant-only",
        ),
        (
            "sample_test.py",
            'def source():\n    return (Path(__file__).resolve().parent / "app.py").read_text().strip()\n'
            'def test_source():\n    assert "hello" in source()\n',
            "test_source",
            "text-only",
        ),
        (
            "sample_test.py",
            'def test_subset():\n    assert module.REGISTRY.issubset({"a", "b"})\n',
            "test_subset",
            "constant-only",
        ),
        (
            "sample_test.py",
            'def test_help(capsys):\n    with pytest.raises(SystemExit) as raised:\n        main(["--help"])\n'
            '    assert raised.value.code == 0\n    assert "hello" in capsys.readouterr().out\n',
            "test_help",
            "text-only",
        ),
    ],
)
def test_candidates_from_static_inputs(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    filename: str,
    source: str,
    function_id: str,
    kind: str,
) -> None:
    path = tmp_path / filename
    if filename.endswith(".py"):
        source = "import module\n" + source
    elif filename.endswith((".ts", ".tsx")):
        source = "import * as module from 'module'; " + source
    path.write_text(source, encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", filename]) == 1
    report = json.loads(capsys.readouterr().out)
    functions = report["files"][0]["functions"]
    assert [(function["id"], function["status"]) for function in functions] == [(function_id, "candidate")]
    assert report["diagnostics"][0]["kind"] == kind


@pytest.mark.parametrize(
    "source",
    [
        "def test_transform():\n    assert transform([3, 1]) == [1, 3]\n",
        'def test_mixed():\n    assert "hello" in module.DESCRIPTION\n    assert transform([3, 1]) == [1, 3]\n',
        'def test_saved(tmp_path):\n    save(tmp_path / "state", [3])\n    assert (tmp_path / "state").read_text() == "3"\n',
        'def test_reject():\n    with pytest.raises(ValueError):\n        parse("bad")\n    assert "a" in module.DESCRIPTION\n',
        'def test_no_import():\n    raise RuntimeError("must not execute")\n',
        'def test_backend():\n    result = backend("input")\n    assert result.message == "saved"\n',
        'def test_mutation():\n    seen = ["first"]\n    seen.append(backend("input"))\n'
        '    assert seen == ["first", "saved"]\n',
        'def test_callback():\n    seen = ["first"]\n    backend(seen.append)\n    assert seen == ["first", "saved"]\n',
    ],
)
def test_behavior_and_unknown_checks_are_not_condemned(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    path = tmp_path / "sample_test.py"
    path.write_text("import module\n" + source, encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), path.name]) == 0
    assert not capsys.readouterr().out


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        (
            "behavior.ts",
            'test("mixed", () => { expect(module.REGISTRY).toEqual(["a"]); expect(transform([3])).toEqual([6]); });',
        ),
        ("behavior.ts", 'test("expected", () => { expect(module.REGISTRY).toEqual(transform([3])); });'),
        ("behavior.ts", 'test("source", () => { expect(transform(source)).toEqual("a"); });'),
        ("behavior.rs", '#[test]\nfn mixed() { assert_eq!(module::REGISTRY, ["a"]); assert_eq!(transform([3]), [6]); }'),
        ("behavior.rs", '#[test]\nfn source_result() { assert_eq!(transform(source), "a"); }'),
        (
            "behavior.rs",
            'fn borrow<\'a>(value: &\'a str) -> &\'a str { value }\n#[test]\nfn lifetimes() { assert_eq!(borrow("a"), "a"); }',
        ),
        ("behavior.ts", 'const quote = /[\'"`]/; test("regex", () => { expect(parse(quote)).toEqual(1); });'),
    ],
)
def test_foreign_independent_checks_block_partial_classification(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    filename: str,
    source: str,
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", filename]) == 0
    report = json.loads(capsys.readouterr().out)
    assert not report["diagnostics"]
    assert all(item["status"] != "candidate" for item in report["files"][0]["functions"])


@pytest.mark.parametrize("placement", ["before", "body"])
def test_local_exception_preserves_reason(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], placement: str) -> None:
    reason = "外部スキーマとの互換性を保証する"
    marker = f"# test-copy-contract: {reason}\n"
    source = (
        marker + 'def test_schema():\n    assert module.SCHEMA == "v1"\n'
        if placement == "before"
        else "def test_schema():\n    " + marker + '    assert module.SCHEMA == "v1"\n'
    )
    path = tmp_path / "sample_test.py"
    path.write_text(source, encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [(function["id"], function["status"], function["reason"]) for function in report["files"][0]["functions"]] == [
        ("test_schema", "exempt", reason),
    ]
    assert not report["diagnostics"]


@pytest.mark.parametrize(
    ("filename", "source", "kind"),
    [
        ("bad_test.py", "def test_bad(:\n", "input-error"),
        ("bad_test.py", "# test-copy-contract:\ndef test_bad():\n    assert module.X == 1\n", "invalid-exception"),
        ("bad_test.py", "# test-copy-contract: all\n\ndef test_bad():\n    pass\n", "invalid-exception"),
        ("bad.ts", 'test("bad", () => {', "input-error"),
        ("bad.rs", '#[test]\nfn bad() { assert!("unterminated); }', "input-error"),
    ],
)
def test_invalid_input_is_distinct_from_zero_candidates(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    filename: str,
    source: str,
    kind: str,
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", filename]) == 2
    assert kind in {item["kind"] for item in json.loads(capsys.readouterr().out)["diagnostics"]}


def test_unreadable_and_out_of_scope_are_separate(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("ordinary data", encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name, "missing_test.py"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert {item["path"]: item["status"] for item in report["files"]} == {
        "notes.txt": "out_of_scope",
        "missing_test.py": "error",
    }


def test_git_default_population_includes_all_tracked_tests(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    init_repository(tmp_path)
    for filename in ("one_test.py", "two_spec.py", "plain.py", "untracked_test.py"):
        (tmp_path / filename).write_text("def test_result():\n    assert transform(2) == 4\n", encoding="utf-8")
    run_git(tmp_path, "add", "--", "one_test.py", "two_spec.py", "plain.py")
    assert check_test_copies.main(["--root", str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert {item["path"] for item in report["files"]} == {"one_test.py", "two_spec.py"}
    assert all([function["id"] for function in item["functions"]] == ["test_result"] for item in report["files"])


def test_normal_diagnostic_has_location_and_failure(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "sample_test.py"
    path.write_text("import module\ndef test_value():\n    assert module.VALUE == 7\n", encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), path.name]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "sample_test.py:2:" in captured.err
    assert "test_value" in captured.err


@pytest.mark.parametrize(
    "source",
    [
        "def test_local():\n    value = 3\n    assert value == 3\n",
        "def fixed():\n    return 3\ndef test_return():\n    assert fixed() == 3\n",
        "import module\ndef test_instance():\n    value = module.create()\n    assert value.STATUS == 3\n",
        "def test_shadow(module):\n    assert module.STATUS == 3\n",
    ],
)
def test_literals_and_instance_attributes_do_not_prove_copy_origin(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    source: str,
) -> None:
    path = tmp_path / "origin_test.py"
    path.write_text(source, encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name]) == 0
    report = json.loads(capsys.readouterr().out)
    assert not report["diagnostics"]
    assert {function["status"] for function in report["files"][0]["functions"]} == {"unclassified"}


@pytest.mark.parametrize("invocation", ["from helper import source\n", "import helper\n"])
def test_imported_helper_is_parsed_without_importing_it(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    invocation: str,
) -> None:
    (tmp_path / "helper.py").write_text(
        'from pathlib import Path\nraise RuntimeError("must not execute")\n'
        'def source():\n    return Path("product.py").read_text()\n',
        encoding="utf-8",
    )
    call = "source()" if invocation.startswith("from") else "helper.source()"
    path = tmp_path / "origin_test.py"
    path.write_text(invocation + f'def test_source():\n    assert "hello" in {call}\n', encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [(item["id"], item["kinds"]) for item in report["files"][0]["functions"]] == [("test_source", ["text-only"])]


def test_global_constant_and_await_keep_their_origin(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "origin_test.py"
    path.write_text(
        "import subprocess\nEXPECTED = 3\ndef test_global():\n    assert EXPECTED == 3\n"
        'async def test_help():\n    result = await subprocess.run(["tool", "--help"])\n'
        '    assert "hello" in result.stdout\n',
        encoding="utf-8",
    )
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {item["id"]: item["kinds"] for item in report["files"][0]["functions"]} == {
        "test_global": ["constant-only"],
        "test_help": ["text-only"],
    }


@pytest.mark.parametrize(
    "body",
    [
        'notice = create_notice({"now": 60})\nassert "stage" in notice\nassert "secret" not in notice\n',
        'notice = create_notice()\nassert notice.startswith("<notice>")\nassert notice.count("stage") == 1\n',
        'notice = create_notice()\nassert re.search(r"stage: [0-9]+", notice)\n',
        'result = cli(["--help"])\nassert result.returncode == 0\nassert "usage:" in result.stdout\n',
    ],
)
def test_unknown_text_fragment_only_functions_are_candidates(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    body: str,
) -> None:
    path = tmp_path / "text_shape_test.py"
    path.write_text("import re\ndef test_notice():\n    " + body.replace("\n", "\n    "), encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [(item["id"], item["status"], item["kinds"]) for item in report["files"][0]["functions"]] == [
        ("test_notice", "candidate", ["text-only"]),
    ]


@pytest.mark.parametrize(
    "extra",
    [
        'assert result["permissionDecision"] == "deny"',
        'assert (tmp_path / "saved.md").exists()',
        "assert result.returncode == 2",
        "assert transform(result) == [3]",
        "sender.assert_called_once()",
        'assert inspect_unknown("fixed fragment")',
    ],
)
def test_result_state_denial_and_unknown_checks_prevent_shape_only_verdict(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    extra: str,
) -> None:
    path = tmp_path / "text_shape_test.py"
    path.write_text(
        'def test_result(tmp_path):\n    result = run()\n    assert "stage" in notice\n    ' + extra + "\n", encoding="utf-8"
    )
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name]) == 0
    report = json.loads(capsys.readouterr().out)
    assert not report["diagnostics"]


def test_imported_assert_helper_joins_the_whole_function_verdict(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "helper.py").write_text('def inspect(text):\n    assert "stage" in text\n', encoding="utf-8")
    path = tmp_path / "text_shape_test.py"
    path.write_text("from helper import inspect\ndef test_notice():\n    inspect(create_notice())\n", encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [(item["id"], item["kinds"]) for item in report["files"][0]["functions"]] == [("test_notice", ["text-only"])]


def test_state_preparation_and_static_source_presence_do_not_hide_text_only_checks(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "text_shape_test.py"
    path.write_text(
        "import persistence\nfrom pathlib import Path\ndef prepare():\n"
        '    def update(state):\n        state["started"] = 1\n        return state\n'
        "    assert persistence.update(update)\ndef test_notice():\n    prepare()\n"
        '    notice = run_notice()\n    assert notice.count("<notice>") == 1\n'
        '    for topic in ("stage", "remaining"):\n        assert topic in notice\n'
        '    guidance = Path(__file__).parent / "guide.md"\n    assert guidance.is_file()\n',
        encoding="utf-8",
    )
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [(item["id"], item["status"]) for item in report["files"][0]["functions"]] == [("test_notice", "candidate")]


def test_one_exception_cannot_cover_two_functions(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "sample.ts"
    path.write_text(
        "// test-copy-contract: public contract\n"
        'test("one", () => { expect(text).toContain("a"); }); '
        'test("two", () => { expect(text).toContain("b"); });\n',
        encoding="utf-8",
    )
    assert check_test_copies.main(["--root", str(tmp_path), "--json", path.name]) == 2
    report = json.loads(capsys.readouterr().out)
    assert "invalid-exception" in {item["kind"] for item in report["diagnostics"]}


@pytest.mark.parametrize(
    ("filename", "source", "status"),
    [
        ("shape.ts", 'test("notice", () => { expect(notice).toContain("stage"); });', 1),
        ("shape.rs", '#[test]\nfn notice() { assert!(notice.contains("stage")); }', 1),
        ("shape.rs", '#[test]\nfn result() { assert!(notice.contains("stage") && result.denied); }', 0),
    ],
)
def test_foreign_text_shape_and_mixed_boolean_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    filename: str,
    source: str,
    status: int,
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    assert check_test_copies.main(["--root", str(tmp_path), "--json", filename]) == status
    report = json.loads(capsys.readouterr().out)
    assert bool(report["diagnostics"]) == bool(status)
