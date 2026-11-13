"""commitのhookで`pyfltr fast`を起動し、統合前のブランチか同じ内容で成功済みのpytestがあればpytestだけを外す。

`develop`・`master`・`main`以外のブランチ（レーンや内部群の作業ブランチ）では、pytestを外す。
これらのブランチの変更は、統合の前に利用側と不変条件試験を含む変更範囲の`pyfltr run`を成功させ、統合する側が
その成功を確かめてから取り込む。commitごとに不変条件試験を実行すると共有の検証枠を占め、検証の待ちが
巡回の所要の大半になったためである（2026年11月13日、同時数8の比較の集計）。
統合先のブランチへのcommitと、ブランチを特定できない状態（detached HEADなど）では、従来どおりpytestを含める。

`pyfltr fast`のpytestは`pytest-always-targets`（不変条件試験）を毎回流し、共有の検証枠を約5分占有する。
commitの直前に同じ内容へ`pyfltr run`を成功させている場合、この実行は同じ試験の繰り返しになる。

再利用はpyfltrの公開出力（`list-runs`・`show-run`・`--version`）とGit・ファイルの属性だけで判定し、
pyfltrの内部構造には依存しない。次をすべて満たす実行だけを再利用する。

- 作業ツリーとindexが一致する（commit対象の内容と作業ツリーが同じ）。
- 追跡ファイル・未追跡ファイルとその親ディレクトリの更新時刻が、その実行のpytestの開始より前である
  （ファイルの追加・削除・改名は親ディレクトリの更新時刻に現れる）。
  基準を実行の開始にしないのは、`pyfltr run`の整形段（`sync-generated-files`など）が
  内容を変えずにファイルを書き直し、pytestの開始の直前に更新時刻を進めるためである。
  pytestは整形段の終了後に始まる（2026年11月12日、pyfltr 3.30.1の`show-run`で、
  整形段の終了とpytestの開始の間隔が約12ミリ秒だったことを観測した）。
  このため時刻の比較に余裕を設けない。
  ただし秒未満を記録しないファイルシステム（ext3など）では、同じ秒の中の変更が秒の先頭の時刻で記録され、
  pytestの開始より前の時刻になる。秒未満が0の時刻はその秒の終わりまでに変更されたものとして扱う
  （2026年11月13日、このリポジトリの開発機のext3で更新時刻の秒未満が常に0であることを観測した）。
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
import shutil
import subprocess
import sys
import typing

from agent_toolkit._git import command as git_command
from verification_targets import consumer_tests, uncovered_tests

from pytools._internal.common import format_cli_error, run_subprocess

# 他の作業ツリーの実行も同じ一覧に並ぶため、直近の実行から候補を探す件数。
_LIST_LIMIT = 50
# `show-run`は1件ごとに起動するため、照会する候補の上限を設けてhookの所要を抑える。
_SHOW_LIMIT = 5
_QUERY_TIMEOUT_SECONDS = 60
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
_REUSABLE_SUBCOMMANDS = frozenset({"run", "ci", "fast"})
# 統合先のブランチ。これらへのcommitでは従来どおり不変条件試験を含めて実行する。
_INTEGRATION_BRANCHES = frozenset({"develop", "master", "main"})
# 試験の対象を変えない引数。これ以外の引数（`--pytest-args`、`--only-failed`、`--disable`など）を含む実行は再利用しない。
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


class Reuse(typing.NamedTuple):
    """再利用の判定結果。"""

    run_id: str | None
    reason: str


def main(argv: list[str] | None = None) -> int:
    """prekが渡すファイルを`pyfltr fast`へ渡し、その終了コードを返す。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--consumer-tests", action="store_true", help="固定文字列参照と命名による利用試験候補をJSONで返す。")
    parser.add_argument(
        "--verify-run",
        metavar="RUN_ID",
        help="commitせずに、指定した実行が現在のHEADの内容で不変条件試験を含むpytestに成功したかを確かめる。"
        "統合する側が、統合入力の作業ツリーでff統合の前に使う。成立すれば終了0、しなければ終了1。",
    )
    args = parser.parse_args(argv)
    if args.consumer_tests and args.verify_run is not None:
        parser.error("--consumer-testsと--verify-runは併用できない")
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
            verified = find_reusable_run(pathlib.Path.cwd(), pyfltr, run_id=args.verify_run)
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
    branch = current_branch(pathlib.Path.cwd())
    if branch is not None and branch not in _INTEGRATION_BRANCHES:
        print(
            f"pyfltr-precommit: 統合前のブランチ{branch}のためpytestを外して実行する。"
            "統合の前に利用側と不変条件試験を含む変更範囲の`pyfltr run`を成功させる。",
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
            # `--short`は同名のタグがあると`heads/<名前>`を返すため、完全な参照名から接頭辞を除く。
            ["-C", str(directory), "symbolic-ref", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_QUERY_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    name = result.stdout.strip().removeprefix("refs/heads/")
    return name if result.returncode == 0 and name and not name.startswith("refs/") else None


def find_reusable_run(directory: pathlib.Path, pyfltr: str, *, run_id: str | None = None) -> Reuse:
    """`directory`の作業ツリーの現在の内容でpytestが成功した実行を探す。

    `run_id`を指定した場合は、その実行だけを調べる。加えてindexとHEADの一致も求め、
    HEADの内容で成功したことを確かめる（統合の前の確認に使う）。
    """
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
    latest = _latest_change_ns(root)
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
        # 空のパスは現在のディレクトリへ解決され、別の作業ツリーの実行と区別できないため再利用しない。
        if (
            isinstance(cwd, str)
            and cwd
            and pathlib.Path(cwd).resolve() == root
            and f"pyfltr {meta.get('version')}" == version.stdout.strip()
            and _neutral_argv(meta.get("argv", []))
            and any(
                command.get("command") == "pytest"
                and command.get("status") == "succeeded"
                and _to_ns(command.get("started_at")) > latest
                for command in shown.get("commands", [])
            )
        ):
            if run_id is not None:
                targets = _argv_targets(meta["argv"])
                sources = [target for target in targets if target.endswith(".py")]
                missing = uncovered_tests(root, consumer_tests(root, sources), targets)
                if missing:
                    return Reuse(None, f"利用試験が不足: {', '.join(missing)}。これらを加えてpyfltr runを再実行する")
            return Reuse(run["run_id"], "")
    return Reuse(None, "現在の内容でpytestが成功した実行が無い")


def _latest_change_ns(root: pathlib.Path) -> int | str:
    """対象ファイルと親ディレクトリの更新時刻の最大値を返す。判定できない場合は理由を返す。"""
    listed = git_command.run(
        ["-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if listed.returncode:
        return f"ファイル一覧を取得できない: exit {listed.returncode}"
    paths: set[pathlib.Path] = {root}
    for name in listed.stdout.split(b"\0"):
        if not name:
            continue
        path = root / os.fsdecode(name)
        paths.add(path)
        paths.update(parent for parent in path.parents if parent.is_relative_to(root))
    latest = 0
    for path in paths:
        try:
            stat = path.lstat()
        except FileNotFoundError:
            return f"indexにあるファイルが作業ツリーに無い: {path.relative_to(root).as_posix()}"
        # ctimeは改名や属性の変更でも進むため、mtimeを保つ移動も検出できる。
        latest = max(latest, _upper_bound_ns(stat.st_mtime_ns), _upper_bound_ns(stat.st_ctime_ns))
    return latest


def _upper_bound_ns(timestamp_ns: int) -> int:
    """記録された時刻から、実際に変更された可能性がある最も遅い時刻を返す。

    秒未満が0の時刻は、秒単位で切り捨てて記録するファイルシステムの値とみなし、その秒の終わりを返す。
    秒未満を記録するファイルシステムで偶然0になった時刻も同じ扱いになるが、再利用を控える側へ倒れるだけである。
    """
    if timestamp_ns % 1_000_000_000:
        return timestamp_ns
    return timestamp_ns + 999_999_999


def _query_json(pyfltr: str, root: pathlib.Path, arguments: list[str]) -> dict[str, typing.Any] | str:
    """pyfltrの照会コマンドのJSON出力を返す。取得できない場合は理由を返す。"""
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


def _neutral_argv(argv: list[str]) -> bool:
    """実行の引数が、試験の対象を変えない形だけかを返す。"""
    if not argv or argv[0] not in _REUSABLE_SUBCOMMANDS:
        return False
    tokens = iter(argv[1:])
    for token in tokens:
        if token in _NEUTRAL_FLAGS:
            continue
        if token in _NEUTRAL_VALUE_OPTIONS:
            if next(tokens, None) is None:
                return False
            continue
        if token.split("=", 1)[0] in _NEUTRAL_VALUE_OPTIONS and "=" in token:
            continue
        if token.startswith("-"):
            return False
    return True


def _argv_targets(argv: list[str]) -> list[str]:
    """中立性を確認済みのargvから、値付きオプションの値を除いて対象を返す。"""
    targets: list[str] = []
    tokens = iter(argv[1:])
    for token in tokens:
        if token in _NEUTRAL_VALUE_OPTIONS:
            next(tokens)
        elif not token.startswith("-"):
            targets.append(token)
    return targets


def _to_ns(value: typing.Any) -> int:
    """ISO 8601の時刻をエポックからのナノ秒へ変換する。読めない値は最小値とし、候補から外す。"""
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
