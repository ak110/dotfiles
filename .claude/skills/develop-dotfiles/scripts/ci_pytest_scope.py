import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath

import yaml


def _step(job):
    steps = [step["run"] for step in job["steps"] if step.get("id") == "pytest" and isinstance(step.get("run"), str)]
    if len(steps) != 1:
        raise ValueError("id=pytest の実行stepが一つ必要")
    return steps[0]


def _command(body, prefix):
    lines = [line.strip() for line in body.replace("\\\n", " ").splitlines() if line.strip().startswith(prefix)]
    if len(lines) != 1:
        raise ValueError(f"実行行を一つに特定できない: {prefix}")
    return shlex.split(lines[0])


def _root_scope(body):
    command = _command(body, "git ls-files ")
    if command[:4] != ["git", "ls-files", "-z", "--"] or command[-2:] != [">", "$RUNNER_TEMP/pytest-root-targets"]:
        raise ValueError("直下の git ls-files の形式が未対応")
    pathspecs = command[4:-2]
    if not pathspecs or not all(re.fullmatch(r"(?::!:)?:?[\w./*?-]+", item) for item in pathspecs):
        raise ValueError("直下のテスト対象の指定が未対応")
    command = _command(body, "uv run --frozen pytest ")
    if command[-1] != "${targets[@]}" or command.count("--junitxml=$junit") != 1:
        raise ValueError("直下の全体 pytest の形式が未対応")
    options = [item for item in command[4:-1] if item != "--junitxml=$junit"]
    if not all(re.fullmatch(r"-[\w=-]+", item) for item in options):
        raise ValueError("直下の pytest 引数が未対応")
    return {"pathspecs": pathspecs, "pytest_args": options}


def _toolkit_scope(body, job):
    command = _command(body, "pyfltr ci ")
    if len(command) != 6 or command[:4] != ["pyfltr", "ci", "--no-always-targets", "--commands=pytest"]:
        raise ValueError("toolkit の pyfltr の形式が未対応")
    target = PurePosixPath(command[5])
    if (
        not command[4].startswith("--pytest-args=")
        or not re.fullmatch(r"[\w./-]+", command[5])
        or command[5].startswith("-")
        or target.is_absolute()
        or ".." in target.parts
    ):
        raise ValueError("toolkit の検査対象または引数が未対応")
    options = shlex.split(command[4].split("=", 1)[1])
    if options.count("--test-shard=$TEST_SHARD") != 1 or options.count("--junitxml=$junit") != 1:
        raise ValueError("toolkit の分割または出力指定が未対応")
    fixed = [item for item in options if item not in {"--test-shard=$TEST_SHARD", "--junitxml=$junit"}]
    if not all(re.fullmatch(r"-[\w.=-]+", item) for item in fixed):
        raise ValueError("toolkit の固定 pytest 引数が未対応")
    matrix = job["strategy"]["matrix"]
    shards = matrix.get("shard")
    if (
        set(matrix) != {"shard"}
        or not isinstance(shards, list)
        or not shards
        or not all(isinstance(item, str) for item in shards)
        or sorted(shards) != sorted(f"{i}/{len(shards)}" for i in range(1, len(shards) + 1))
    ):
        raise ValueError("toolkit の shard が全分割と一致しない")
    return {"path": command[5], "pytest_args": fixed, "shards": shards}


def load_scope(workflow, scope):
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    job = jobs[f"pytest-{scope}"]
    body = _step(job)
    return _root_scope(body) if scope == "root" else _toolkit_scope(body, job)


def run_scope(scope, selected, root, *, collect_only):
    env = {**os.environ, "PYTHONDEVMODE": "1", "UV_NO_SYNC": "true"}
    for key in ("PYFLTR_GLOBAL_CONFIG", "PYTEST_ADDOPTS", "TESTSCOPE"):
        env.pop(key, None)
    extra = ["--collect-only", "-q"] if collect_only else []
    if scope == "root":
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", *selected["pathspecs"]], cwd=root, capture_output=True, check=True, timeout=30
        )
        paths = [os.fsdecode(path) for path in listed.stdout.split(b"\0") if path]
        if not paths:
            raise ValueError("直下の検査対象が空")
        return subprocess.call(
            [sys.executable, "-m", "pytest", *selected["pytest_args"], *extra, "--", *paths], cwd=root, env=env
        )

    result = 0
    for shard in selected["shards"]:
        print(f"toolkit shard {shard}", file=sys.stderr, flush=True)
        args = [*selected["pytest_args"], f"--test-shard={shard}", *extra]
        code = subprocess.call(
            [
                sys.executable,
                "-m",
                "pyfltr",
                "ci",
                "--no-always-targets",
                "--commands=pytest",
                f"--pytest-args={shlex.join(args)}",
                selected["path"],
            ],
            cwd=root,
            env=env,
        )
        result = result or code
    return result


def matching_old_assertions(diff: str, tests: dict[str, str]) -> list[str]:
    removed = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("--- "))
    candidates = []
    for name, source in tests.items():
        try:
            parsed = ast.parse(source)
        except SyntaxError:
            continue
        if any(
            isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) >= 8 and node.value in removed
            for assertion in ast.walk(parsed)
            if isinstance(assertion, ast.Assert)
            for node in ast.walk(assertion.test)
        ):
            candidates.append(name)
    return sorted(candidates)


def _old_text_candidates(root: Path, base: str) -> dict[str, object]:
    if not re.fullmatch(r"[a-f0-9]{40}", base):
        raise ValueError("旧文面を探す比較元には完全OIDを指定する")
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-color", "--unified=0", base, "--", "*.py", "*.md", "*.ts", "*.tsx"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout.decode("utf-8", "replace")
    paths = subprocess.run(
        ["git", "ls-files", "-z", "--", "*_test.py", "test_*.py"], cwd=root, capture_output=True, check=True, timeout=30
    ).stdout.split(b"\0")
    tests = {
        name: (root / name).read_text(encoding="utf-8", errors="replace")
        for path in paths
        if path and (root / (name := os.fsdecode(path))).is_file()
    }
    return {"test_files": matching_old_assertions(diff, tests), "requires_job_scope": True}


def main():
    parser = argparse.ArgumentParser(description="CIのpytestの対象を表示または検証する")
    parser.add_argument("scope", choices=("root", "toolkit"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run", action="store_true")
    group.add_argument("--collect-only", action="store_true")
    group.add_argument("--old-text-tests", metavar="BASE_OID")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    try:
        selected = load_scope(root / ".github/workflows/ci.yaml", args.scope)
        if args.old_text_tests is not None:
            print(json.dumps(_old_text_candidates(root, args.old_text_tests), ensure_ascii=False))
            return 0
        if args.run or args.collect_only:
            return run_scope(args.scope, selected, root, collect_only=args.collect_only)
        print(json.dumps(selected, ensure_ascii=False))
        return 0
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError, subprocess.SubprocessError) as error:
        parser.exit(2, f"CIのpytestの対象を確定できない: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
