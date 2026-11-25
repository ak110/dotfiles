"""commitのhookで`pyfltr fast`を起動し、統合前のブランチか同じ内容で成功済みのpytestがあればpytestだけを外す。

`develop`・`master`・`main`以外のブランチ（レーンや内部群の作業ブランチ）では、pytestを外す。
これらのブランチの変更は、統合の前に利用側と影響する不変条件試験を含む変更範囲の`pyfltr run`を成功させる。
不変条件試験全量は統合後のdevelop CIが実行する。commitごとに不変条件試験を実行すると共有の検証枠を占め、検証の待ちが
巡回の所要の大半になったためである（2026年11月13日、同時数8の比較の集計）。
統合先のブランチへのcommitと、ブランチを特定できない状態（detached HEADなど）では、従来どおりpytestを含める。

`pyfltr fast`のpytestは`pytest-always-targets`（不変条件試験）を毎回流し、共有の検証枠を約5分占有する。
commitの直前に同じ内容へ`pyfltr run`を成功させている場合、この実行は同じ試験の繰り返しになる。

再利用はpyfltrの公開出力（`list-runs`・`show-run`・`--version`）とGit・ファイルの属性だけで判定し、
pyfltrの内部構造には依存しない。次をすべて満たす実行だけを再利用する。

- 作業ツリーとindexが一致する（commit対象の内容と作業ツリーが同じ）。
- 追跡ファイル・除外対象以外の現存する未追跡ファイルとGit indexの更新時刻が、その実行のpytestの開始より前である。
  追跡対象の削除・改名はindexと作業ツリーの比較およびindexの更新時刻で検出する。
  基準を実行の開始にしないのは、`pyfltr run`の整形段（`sync-generated-files`など）が
  内容を変えずにファイルを書き直し、pytestの開始の直前に更新時刻を進めるためである。
  pytestは整形段の終了後に始まる（2026年11月12日、pyfltr 3.30.1の`show-run`で、
  整形段の終了とpytestの開始の間隔が約12ミリ秒だったことを観測した）。
  このため時刻の比較に余裕を設けない。
  ただし秒未満を記録しないファイルシステム（ext3など）では、同じ秒の中の変更が秒の先頭の時刻で記録され、
  pytestの開始より前の時刻になる。秒未満が0の時刻はその秒の終わりまでに変更されたものとして扱う
  （2026年11月13日、このリポジトリの開発機のext3で更新時刻の秒未満が常に0であることを観測した）。
  pyfltr 3.32.0以降はテスターを同じrunの整形が終わった秒を過ぎてから起動し、その時刻を`launch_boundary_at`へ記録する。
  記録がある場合はpytestの開始の代わりにこの境界と比べ、同じ秒の整形を受理しつつ境界の後の変更を拒否する。
  記録の無い旧版のrunは開始時刻と比べ、同じ秒の整形は再利用を控える側へ倒す。
  統合前の指定runの確認はindexとHEADの一致を求めるため、Git indexの時刻は比べない。
  整形が時刻だけを更新したファイルがあると、次のGit操作がindexを書き直して時刻が境界より後になるためである。
- 実行のcwdがこの作業ツリーで、pyfltrの版が同じで、引数が試験の範囲を変えない形だけである。
- その実行でpytestが成功している。

`fast`のpytestは不変条件試験だけを対象とし、`run`・`ci`・`fast`のpytestは対象ファイルによらず
不変条件試験を含むため、上の条件を満たす実行の成功はhookのpytestを被覆する。
判定できない場合（照会の失敗や想定外の形式の出力を含む）は従来どおりpytestを含めて実行する。

「同じ内容」の根拠はGitの追跡ファイルと、除外指定に当たらない未追跡ファイルに限る。
除外指定に当たるファイル（`.venv`、生成物、`.env`など）、リポジトリ外の入力（ホーム配下の設定など）、
環境変数の変化は検出しない。commitの直前に同じ作業ツリーで成功した実行を前提とし、
commitまでにこれらを変える操作（`uv sync`など）を実行した場合は、commitの前に検証をやり直す。
"""

import argparse
import datetime
import io
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import typing

from agent_toolkit._common.shell_segments import extract_execution_pipelines
from agent_toolkit._common.shell_tokens import strip_redirections
from agent_toolkit._git import command as git_command
from verification_targets import consumer_tests, required_tests, uncovered_tests

from pytools._internal.common import format_cli_error, run_subprocess

_LIST_LIMIT = 50

_SHOW_LIMIT = 5
_QUERY_TIMEOUT_SECONDS = 60
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
_REUSABLE_SUBCOMMANDS = frozenset({"run", "ci", "fast"})

_INTEGRATION_BRANCHES = frozenset({"develop", "master", "main"})
# 試験の対象を変えない引数。これ以外の引数を含む実行は再利用しない。
# 例: 範囲を変える`--pytest-args`、`--only-failed`、`--disable`。
_NEUTRAL_FLAGS = frozenset(
    {
        "--no-fix",
        "--ui",
        "--no-ui",
        "--quiet",
        "--no-quiet",
        "-v",
        "--verbose",
        "--exit-zero-even-if-formatted",
        "--fail-fast",
        "--no-cache",
    }
)
# 値を取る引数のうち、試験の対象を変えないもの。`--commands`はpytestの成功を別に確かめるため受け入れる。
_NEUTRAL_VALUE_OPTIONS = frozenset({"--output-format", "--commands"})
# `--pytest-args`のうち、並列数と失敗時の打ち切り数だけを変える引数。統合時の検証値（`-n 2 --maxfail=10`）を受け入れる。
# 打ち切り数は失敗時だけ作用し、pytestの成功を要件とする再利用の判定では対象を狭めない。
_NEUTRAL_PYTEST_OPTIONS = frozenset({"-n", "--numprocesses", "--maxfail"})


def _neutral_pytest_args(value: str) -> bool:
    try:
        tokens = iter(shlex.split(value))
    except ValueError:
        return False
    for token in tokens:
        name, separator, number = token.partition("=")
        if name not in _NEUTRAL_PYTEST_OPTIONS:
            return False
        if not separator:
            number = next(tokens, "")
        if not (number.isdecimal() or (name != "--maxfail" and number == "auto")):
            return False
    return True


class Reuse(typing.NamedTuple):
    run_id: str | None
    reason: str


def _early_reader(tokens: tuple[str, ...]) -> str | None:
    if not tokens:
        return None
    name = pathlib.PurePosixPath(tokens[0]).name
    if name == "head":
        return name
    if name != "grep":
        return None
    args = iter(tokens[1:])
    for token in args:
        if token == "--":
            break
        if token in {"--quiet", "--silent", "--max-count"} or token.startswith("--max-count="):
            return name
        if token in {"--regexp", "--file", "--after-context", "--before-context", "--context", "--devices", "--directories"}:
            next(args, None)
        elif token.startswith("-") and not token.startswith("--"):
            letters = token[1:]
            for index, letter in enumerate(letters):
                if letter in "qm":
                    return name
                if letter in "efABCDd":
                    if index == len(letters) - 1:
                        next(args, None)
                    break
    return None


def _pipefail_candidate(source: str) -> str | None:
    if "pipefail" not in source:
        return None
    enabled = False
    for pipeline in extract_execution_pipelines(source, expand_shell=False):
        segments = [strip_redirections(part.tokens) for part in pipeline]
        if len(segments) == 1:
            tokens = segments[0]
            if len(tokens) == 3 and tokens[0] == "set" and tokens[1].endswith("o") and tokens[2] == "pipefail":
                enabled = tokens[1].startswith("-")
        elif enabled:
            for tokens in segments[1:]:
                if reader := _early_reader(tokens):
                    return reader
    return None


def _observe_pipefail(files: list[str]) -> None:
    root = pathlib.Path.cwd().resolve()
    for name in files:
        if not name.endswith((".sh", ".bash", ".sh.tmpl")):
            continue
        try:
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                continue
            reader = _pipefail_candidate(path.read_text(encoding="utf-8"))
        except OSError, UnicodeError:
            continue
        if reader is not None:
            print(
                f"{name}: pipefailと後段{reader}の早期終了が重なる候補。"
                "前段の結果を確認し、必要なら全入力を読むか生成と検索を分ける。",
                file=sys.stderr,
            )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*")
    parser.add_argument(
        "--consumer-tests", action="store_true", help="HEAD・本文ハッシュと、直接import/意味判定要の根拠付き候補をJSONで返す。"
    )
    parser.add_argument("--consumer-review", type=pathlib.Path, help="現在の候補JSONと根拠付きdecisionsを保持した検証証拠。")
    parser.add_argument("--check-argv", help="予定pyfltr argvのJSON配列を読み取り専用で確認する。run成功の証明ではない。")
    parser.add_argument(
        "--verify-run",
        metavar="RUN_ID",
        help="commitせずに、指定した実行が現在のHEADの内容で必要なpytestに成功したかを確かめる。"
        "統合する側が、統合入力の作業ツリーでff統合の前に使う。成立すれば終了0、しなければ終了1。",
    )
    args = parser.parse_args(argv)
    if sum((args.consumer_tests, args.verify_run is not None, args.check_argv is not None)) > 1:
        parser.error("--consumer-tests、--verify-run、--check-argvは併用できない")
    try:
        review = json.loads(args.consumer_review.read_text(encoding="utf-8")) if args.consumer_review is not None else None
    except (OSError, ValueError) as exc:
        parser.error(
            f"意味判定を取得できない: {exc}\n"
            "次の操作: --consumer-reviewのパス・読取権限・UTF-8 JSONを確認する。"
            "修正できない場合は--consumer-tests <変更ファイル...>で候補を再取得し、"
            "根拠付きdecisionsを記録したJSONのパスを--consumer-reviewへ渡して再実行する。"
        )
    if args.check_argv is not None:
        return _check_planned_argv(args.check_argv, review)
    if args.consumer_tests:
        try:
            print(json.dumps(consumer_tests(pathlib.Path.cwd(), args.files), ensure_ascii=False))
        except (ValueError, OSError, SyntaxError, subprocess.TimeoutExpired) as exc:
            print(f"候補列挙を確認できない: {exc}", file=sys.stderr)
            print(
                "次の操作: 入力パスがリポジトリ内の実在ファイルか確認せよ。"
                "取得や読取に失敗した場合は、診断に示したGit状態とファイルの読取権限を確認せよ。"
                "問題を直し、同じ引数で--consumer-testsを再実行せよ。",
                file=sys.stderr,
            )
            return 1
        return 0
    pyfltr = shutil.which("pyfltr")
    if pyfltr is None:
        print("pyfltr-precommit: pyfltrが見つからない。`uv sync --frozen`で開発環境を準備する。", file=sys.stderr)
        return 2
    if args.verify_run is not None:
        try:
            options = {"review": review} if args.consumer_review is not None else {}
            verified = find_reusable_run(pathlib.Path.cwd(), pyfltr, run_id=args.verify_run, **options)
        except (KeyError, TypeError, AttributeError, ValueError, OSError, SyntaxError, subprocess.TimeoutExpired) as exc:
            verified = Reuse(None, f"判定できない: {type(exc).__name__}: {exc}")
        if verified.run_id is None:
            print(
                f"pyfltr-precommit: run {args.verify_run}は現在のHEADの内容の成功と確かめられない（{verified.reason}）。",
                file=sys.stderr,
            )
            print(
                "次の操作: 診断の原因と対象HEADを確認し、必要な利用側試験を含むpyfltr runを成功させよ。"
                "その実行IDを--verify-runへ渡せ。",
                file=sys.stderr,
            )
            return 1
        print(f"pyfltr-precommit: run {args.verify_run}は現在のHEADの内容でpytestに成功している。")
        return 0
    _observe_pipefail(args.files)
    branch = current_branch(pathlib.Path.cwd())
    if branch is not None and branch not in _INTEGRATION_BRANCHES:
        print(
            f"pyfltr-precommit: 統合前のブランチ{branch}のためpytestを外して実行する。"
            "統合の前に変更箇所と利用側を含む変更範囲の`pyfltr run`を成功させる。",
            file=sys.stderr,
        )
        return subprocess.run([pyfltr, "fast", "--disable", "pytest", *args.files], check=False).returncode
    try:
        reuse = find_reusable_run(pathlib.Path.cwd(), pyfltr)
    except (KeyError, TypeError, AttributeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        # 照会出力の形式の変化やGitの時間切れで判定できない場合も、pytestを外さない側へ倒す。
        reuse = Reuse(None, f"再利用を判定できない: {type(exc).__name__}: {exc}")
    extra: list[str] = []
    if reuse.run_id is None:
        print(f"pyfltr-precommit: pytestを含めて実行する（{reuse.reason}）。", file=sys.stderr)
    else:
        extra = ["--disable", "pytest"]
        print(
            f"pyfltr-precommit: 同じ内容で成功したrun {reuse.run_id}のpytestを再利用し、pytestを外して実行する。",
            file=sys.stderr,
        )
    return subprocess.run([pyfltr, "fast", *extra, *args.files], check=False).returncode


def current_branch(directory: pathlib.Path) -> str | None:
    """現在のブランチ名を返す。detached HEADや取得の失敗ではNoneを返し、pytestを含める側へ倒す。"""
    try:
        result = git_command.run(
            ["-C", str(directory), "symbolic-ref", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_QUERY_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    name = result.stdout.strip().removeprefix("refs/heads/")
    return name if result.returncode == 0 and name and not name.startswith("refs/") else None


def find_reusable_run(directory: pathlib.Path, pyfltr: str, *, run_id: str | None = None, review: object = None) -> Reuse:
    toplevel = git_command.run(
        ["-C", str(directory), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=_QUERY_TIMEOUT_SECONDS
    )
    if toplevel.returncode:
        return Reuse(None, f"作業ツリーを特定できない: {format_cli_error(toplevel)}")
    root = pathlib.Path(toplevel.stdout.rstrip("\n")).resolve()
    # `GIT_INDEX_FILE`を含む環境を引き継ぐため、`git commit <path>`の一時indexとも比べる。
    diff = git_command.run(["-C", str(root), "diff", "--quiet"], capture_output=True, text=True, timeout=_QUERY_TIMEOUT_SECONDS)
    if diff.returncode == 1:
        return Reuse(None, "作業ツリーにindexと異なる変更がある")
    if diff.returncode:
        return Reuse(None, f"作業ツリーとindexを比較できない: {format_cli_error(diff)}")
    if run_id is not None:
        cached = git_command.run(
            ["-C", str(root), "diff", "--cached", "--quiet"], capture_output=True, text=True, timeout=_QUERY_TIMEOUT_SECONDS
        )
        if cached.returncode:
            return Reuse(None, "indexにHEADと異なる変更がある")

    latest = _latest_change_ns(root, include_index=run_id is None)
    if isinstance(latest, str):
        return Reuse(None, latest)

    version = run_subprocess([pyfltr, "--version"], timeout=_QUERY_TIMEOUT_SECONDS, cwd=root, tag="pyfltr")
    if version is None or version.returncode:
        return Reuse(None, f"pyfltrの版を取得できない: {format_cli_error(version)}")
    if run_id is not None:
        specified: dict[str, typing.Any] = {"run_id": run_id, "finished_at": None, "commands": ["pytest"]}
        listed: dict[str, typing.Any] | str = {"runs": [specified]}
    else:
        listed = _query_json(pyfltr, root, ["list-runs", "--limit", str(_LIST_LIMIT), "--output-format", "json"])
    if isinstance(listed, str):
        return Reuse(None, listed)
    candidates = (
        list(listed["runs"])
        if run_id is not None
        else sorted(
            (
                run
                for run in listed.get("runs", [])
                if _to_ns(run.get("finished_at")) > latest and "pytest" in run.get("commands", [])
            ),
            key=lambda run: _to_ns(run.get("finished_at")),
            reverse=True,
        )
    )
    for run in candidates[:_SHOW_LIMIT]:
        shown = _query_json(pyfltr, root, ["show-run", run["run_id"], "--output-format", "json"])
        if isinstance(shown, str):
            return Reuse(None, shown)
        meta = shown.get("meta", {})
        cwd = meta.get("cwd")
        argv_error = argv_problem(meta.get("argv", []), allow_no_always=run_id is not None)
        if run_id is not None:
            if not isinstance(cwd, str) or not cwd or pathlib.Path(cwd).resolve() != root:
                return Reuse(None, "cwdが現在の作業ツリーと一致しない")
            if f"pyfltr {meta.get('version')}" != version.stdout.strip():
                return Reuse(None, "pyfltrの版が現在と一致しない")
            if argv_error:
                return Reuse(None, argv_error)
            if not any(
                command.get("command") == "pytest" and command.get("status") == "succeeded"
                for command in shown.get("commands", [])
            ):
                return Reuse(None, "指定runでpytestの成功を確認できない")

        if (
            isinstance(cwd, str)
            and cwd
            and pathlib.Path(cwd).resolve() == root
            and f"pyfltr {meta.get('version')}" == version.stdout.strip()
            and not argv_error
            and any(
                command.get("command") == "pytest"
                and command.get("status") == "succeeded"
                and _to_ns(command.get("launch_boundary_at") or command.get("started_at")) > latest
                for command in shown.get("commands", [])
            )
        ):
            if run_id is not None and (problem := _coverage_problem(root, meta["argv"], review)):
                return Reuse(None, problem)
            return Reuse(run["run_id"], "")
    return Reuse(None, "現在の内容でpytestが成功した実行が無い")


def _latest_change_ns(root: pathlib.Path, *, include_index: bool = True) -> int | str:
    listed = git_command.run(
        ["-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if listed.returncode:
        return f"ファイル一覧を取得できない: exit {listed.returncode}"
    index = git_command.run(
        ["-C", str(root), "rev-parse", "--path-format=absolute", "--git-path", "index"],
        capture_output=True,
        text=True,
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if index.returncode or not index.stdout.strip():
        return (
            f"Git indexのパスを取得できない: {format_cli_error(index)}。"
            "対象worktreeで`git rev-parse --git-path index`を実行し、Gitの診断とindexの所在を確認する"
        )
    paths: set[pathlib.Path] = {pathlib.Path(index.stdout.strip())} if include_index else set()
    for name in listed.stdout.split(b"\0"):
        if not name:
            continue
        path = root / os.fsdecode(name)
        paths.add(path)
    latest = 0
    for path in paths:
        try:
            stat = path.lstat()
        except FileNotFoundError:
            return f"indexにあるファイルが作業ツリーに無い: {path.relative_to(root).as_posix()}"

        latest = max(latest, _upper_bound_ns(stat.st_mtime_ns), _upper_bound_ns(stat.st_ctime_ns))
    return latest


def _upper_bound_ns(timestamp_ns: int) -> int:
    if timestamp_ns % 1_000_000_000:
        return timestamp_ns
    return timestamp_ns + 999_999_999


def _query_json(pyfltr: str, root: pathlib.Path, arguments: list[str]) -> dict[str, typing.Any] | str:
    result = run_subprocess([pyfltr, *arguments], timeout=_QUERY_TIMEOUT_SECONDS, cwd=root, tag="pyfltr")
    if result is None or result.returncode:
        return f"`pyfltr {arguments[0]}`が失敗した: {format_cli_error(result)}"
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return f"`pyfltr {arguments[0]}`の出力をJSONとして読めない: {exc}"
    if not isinstance(parsed, dict):
        return f"`pyfltr {arguments[0]}`の出力がJSONオブジェクトでない"
    return parsed


def argv_problem(argv: object, *, allow_no_always: bool = False) -> str:
    if not isinstance(argv, list) or any(not isinstance(token, str) for token in argv):
        return "argvが文字列配列でない"
    if not argv or argv[0] not in _REUSABLE_SUBCOMMANDS:
        return "argvの先頭はrun、ci、fastのいずれかにする"
    tokens = iter(argv[1:])
    for token in tokens:
        if token in _NEUTRAL_FLAGS or (allow_no_always and token == "--no-always-targets"):
            continue
        if token in _NEUTRAL_VALUE_OPTIONS:
            if next(tokens, None) is None:
                return f"argv不適合: {token}の値が無い"
            continue
        if token.split("=", 1)[0] in _NEUTRAL_VALUE_OPTIONS and "=" in token:
            continue
        if token == "--pytest-args":
            value = next(tokens, None)
            if value is None or not _neutral_pytest_args(value):
                return "argv不適合: --pytest-argsの値を試験範囲を変えない引数と確認できない"
            continue
        if token.startswith("--pytest-args=") and _neutral_pytest_args(token.split("=", 1)[1]):
            continue
        if token.startswith("-"):
            return f"argv不適合: {token}は試験範囲を変えない引数と確認できない"
    return ""


def _coverage_problem(root: pathlib.Path, argv: list[str], review: object) -> str:
    if argv[0] == "fast":
        return "実行範囲不足: fastのpytestは不変条件試験だけなので、最終検証にはrunまたはciを使う"
    tokens = iter(argv[1:])
    for token in tokens:
        commands = (
            next(tokens, "") if token == "--commands" else token.partition("=")[2] if token.startswith("--commands=") else None
        )
        if commands is not None and "pytest" not in commands.split(","):
            return "argv不適合: --commandsにpytestが含まれていない"
    targets = _argv_targets(argv)
    sources = [target for target in targets if target.endswith(".py") or (root / target).is_file()]
    try:
        snapshot = consumer_tests(root, sources)
        required = required_tests(snapshot, review)
    except ValueError as exc:
        return str(exc)
    missing = uncovered_tests(root, required, targets)
    return f"利用試験が不足: {', '.join(missing)}。予定argvへ追加する" if missing else ""


def _check_planned_argv(value: str, review: object) -> int:
    try:
        planned = json.loads(value)
        if not isinstance(planned, list) or any(not isinstance(token, str) for token in planned):
            raise ValueError("argvは文字列のJSON配列で指定する")
        reason = argv_problem(planned, allow_no_always=True)
        if not reason:
            reason = _coverage_problem(pathlib.Path.cwd(), planned, review)
    except (ValueError, OSError, SyntaxError, subprocess.TimeoutExpired) as exc:
        print(f"事前確認を確定できない: {exc}", file=sys.stderr)
        print(
            "次の操作: `uv run --frozen python scripts/pyfltr_precommit.py --help`で公開入力を確認し、"
            "予定argvのJSONと対象パスを修正して再実行する。",
            file=sys.stderr,
        )
        return 2
    if reason:
        print(reason, file=sys.stderr)
        if "判定" in reason or "曖昧候補" in reason:
            action = (
                "`uv run --frozen python scripts/pyfltr_precommit.py --consumer-tests <変更ファイル...>`で候補を再取得し、"
                "現行HEADの全曖昧候補へ根拠付きdecisionsを記録して--consumer-reviewへ渡す。"
            )
        elif reason.startswith("argv不適合:"):
            action = "表示した未確認の追加オプションを予定argvから外し、pytestを含むrun/ciの予定argvを--check-argvへ再度渡す。"
        else:
            action = "表示した必要試験と対象パスを予定argvへ加え、run/ciを使って--check-argvを再実行する。"
        print(f"次の操作: {action}", file=sys.stderr)
        return 1
    print("予定argvと必須試験範囲は適合。実runの成功・版・cwd・HEAD/index・実行後不変は未確認。verify-runで確認する。")
    return 0


def _argv_targets(argv: list[str]) -> list[str]:
    targets: list[str] = []
    tokens = iter(argv[1:])
    for token in tokens:
        if token in _NEUTRAL_VALUE_OPTIONS or token == "--pytest-args":
            next(tokens)
        elif not token.startswith("-"):
            targets.append(token)
    return targets


def _to_ns(value: typing.Any) -> int:
    if not isinstance(value, str):
        return -1
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return -1
    if parsed.tzinfo is None:
        return -1
    return (parsed - _EPOCH) // datetime.timedelta(microseconds=1) * 1_000


if __name__ == "__main__":
    raise SystemExit(main())
