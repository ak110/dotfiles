"""push前後の実行ID差分でCI通過確認を待機する補助スクリプト。

push前に対象repository・destination ref・source refから解決したcommit SHAの実行IDをbaselineへ保存し、push後は
GitHub ActionsまたはGitLab CIの実行一覧からbaselineに存在しないIDだけを待機する。
一覧とジョブ取得の両方に明示的なrepositoryを指定し、一覧はdestination refとSHAで限定する。
境界条件（run未登録・コマンド失敗・登録遅延・cancelled後の後続run追跡・タイムアウト・シグナル）を明示的に扱う。
未報告の失敗を検出し未終了が残る場合は6を返す。同じbaselineまたは--failure-recordで再開すると、
既報の失敗では返らず残りを待つ。全体終了では全失敗を出力し、成功0・失敗1を返す。
GitLab CI Lintで対象commit・refのパイプライン非生成を確定した場合は8（CI実行対象外）を返す。
`agent-toolkit:commit`の`references/push-and-ci.md`から参照される。
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import os
import pathlib
import re
import signal
import sys
from typing import NoReturn

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wait_ci_forge import (
    _default_run_list_fn,
    _follow_shas,
    _gh_job_list,
    _gh_run_list,
    _github_ci_configured,
    _glab_job_list,
    _glab_pipeline_list,
    _is_ancestor_of_ref,
    _normalize_gitlab_pipeline,
    _resolve_forge,
    _resolve_sha,
    _run_forge_json_command,
)
from agent_toolkit._atk.wait_ci_polling import _all_cancelled, _all_success, _run_ids, _wait_for_completion, wait_for_ci
from agent_toolkit._atk.wait_ci_records import (
    _NEXT_ACTION_FORGE_ERROR,
    _NEXT_ACTION_INTERRUPTED,
    _NEXT_ACTION_SHA_RESOLUTION,
    _STDERR_FD,
    EXIT_CI_EXCLUDED,
    EXIT_CI_FAILED,
    EXIT_GH_ERROR,
    EXIT_INTERRUPTED,
    EXIT_NEW_FAILURE,
    EXIT_NO_CI_CONFIG,
    EXIT_NO_RUNS,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    CiBaseline,
    JobRecord,
    RunListError,
    RunRecord,
    _finish,
    _load_baseline,
    _validate_baseline_context,
    _write_baseline,
)
from agent_toolkit._common import next_action as _next_action

__all__ = [
    "CiBaseline",
    "EXIT_CI_EXCLUDED",
    "EXIT_CI_FAILED",
    "EXIT_GH_ERROR",
    "EXIT_INTERRUPTED",
    "EXIT_NEW_FAILURE",
    "EXIT_NO_CI_CONFIG",
    "EXIT_NO_RUNS",
    "EXIT_SUCCESS",
    "EXIT_TIMEOUT",
    "EXIT_USAGE",
    "JobRecord",
    "RunListError",
    "RunRecord",
    "_all_cancelled",
    "_all_success",
    "_default_run_list_fn",
    "_follow_shas",
    "_gh_job_list",
    "_gh_run_list",
    "_github_ci_configured",
    "_glab_job_list",
    "_glab_pipeline_list",
    "_is_ancestor_of_ref",
    "_normalize_gitlab_pipeline",
    "_run_forge_json_command",
    "_wait_for_completion",
    "main",
    "wait_for_ci",
]


def _install_signal_handlers() -> None:
    """SIGINT/SIGTERM受信時にexit codeを返す簡易ハンドラ。

    ハンドラは即座に`sys.exit`する。実行中の子`gh`プロセスは明示的に終了させず、
    `subprocess.run`側の`timeout`（`--subprocess-timeout`）到達による自然終了に委ねる。
    厳密なプロセスグループ制御は、複雑化に見合う利得が無いため実装しない。

    終了メッセージは`os.write`で書く。`print`は`sys.stderr`の
    `BufferedWriter`を経由するため、メインフローが同じバッファへ書き込んでいる最中に
    シグナルが到達すると再入となり`RuntimeError: reentrant call inside <_io.BufferedWriter>`で
    異常終了する。`os.write`はバッファを介さないため再入が成立しない。
    """

    def _handler(signum, _frame):
        os.write(
            _STDERR_FD,
            f"[wait_ci] シグナル{signum}受信で終了\n{_next_action.next_action_line(_NEXT_ACTION_INTERRUPTED)}\n".encode(),
        )
        sys.exit(EXIT_INTERRUPTED)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _positive_float(value: str) -> float:
    """0より大きい有限`float`へ変換する。不正時はargparse標準のusage表示で終了させる。"""
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError(f"0より大きい有限の値を指定してください: {value!r}")
    return parsed


def _non_negative_float(value: str) -> float:
    """0以上の有限`float`へ変換する。不正時はargparse標準のusage表示で終了させる。"""
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError(f"0以上の有限の値を指定してください: {value!r}")
    return parsed


def _full_sha(value: str) -> str:
    """Forge APIへそのまま渡せる完全長commit SHAを検証する。"""
    if re.fullmatch(r"[0-9a-fA-F]{40}", value) is None:
        raise argparse.ArgumentTypeError("40桁の完全長commit SHAを指定してください")
    return value.lower()


_NEXT_ACTION_BASELINE_MISMATCH = (
    "push前に同じ`--repo`・`--ref`・`--source-ref`で`--write-baseline`を取り直してから待機するか、"
    "baselineを使わない`--wait-sha`で対象commitの全runを待つ"
)


_NEXT_ACTION_USAGE = "`atk run-script wait-ci -- --help`で受理形式を確かめて引数を直し、起動し直す"


class _UsageErrorParser(argparse.ArgumentParser):
    """引数の誤りをtimeout（終了コード2）と区別できる終了コードで終える。

    argparseは引数の誤りを終了コード2で返し、本スクリプトの`EXIT_TIMEOUT`と重なる。
    手順書は終了コード2をtimeoutとして再呼び出しへ振り分けるため、引数の誤りは`EXIT_USAGE`で終える。
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(_finish(EXIT_USAGE, _NEXT_ACTION_USAGE))


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析し、baseline作成またはCI通過確認を実行する。"""
    _outcome.force_utf8_stdio()
    _install_signal_handlers()
    parser = _UsageErrorParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write-baseline", type=pathlib.Path, metavar="PATH", help="push前の実行IDを保存するJSONパス")
    mode.add_argument("--baseline", type=pathlib.Path, metavar="PATH", help="push前に保存したbaseline JSONパス")
    mode.add_argument("--wait-sha", type=_full_sha, help="baselineを使わず全実行を待つ完全長commit SHA")
    parser.add_argument(
        "--failure-record",
        type=pathlib.Path,
        metavar="PATH",
        help="--wait-shaの報告済み失敗を保存するJSON。同じ引数で終了コード6から待機を再開する",
    )
    parser.add_argument(
        "--repo",
        help=(
            "対象repository。--write-baselineと--wait-shaでは必須。--baselineでは省略するとbaselineの保存値を使い、"
            "指定するとbaselineの保存値と一致するかを確かめる。GitLabのホスト付き識別子とSSH指定はglabのホスト別API・認証設定を使う。"
            "明示HTTP(S) URLはscheme・authorityをAPI接続先と認証設定のホストキーに使う。"
            "ホスト省略時はGITLAB_HOST・GITLAB_URI・GL_HOSTとglabの設定を使う"
        ),
    )
    parser.add_argument(
        "--ref",
        help=(
            "対象destination ref（例: refs/heads/main）。--write-baselineと--wait-shaでは必須。"
            "--baselineでは省略するとbaselineの保存値を使う"
        ),
    )
    parser.add_argument(
        "--source-ref",
        help=(
            "push元のローカルsource ref（例: HEAD）。--write-baselineと--wait-shaでは必須。"
            "--baselineでは省略するとbaselineの保存値を使う"
        ),
    )
    parser.add_argument(
        "--sha",
        help="対象commit SHA（省略時はbaseline作成でsource ref、待機でbaseline保存SHAを使用）",
    )
    parser.add_argument(
        "--timeout", type=_positive_float, default=270.0, help="全体タイムアウト秒数。省略時は270秒で打ち切る。"
    )
    parser.add_argument(
        "--poll-interval", type=_positive_float, default=20.0, help="ポーリング間隔秒数。省略時は20秒間隔とする。"
    )
    parser.add_argument(
        "--registration-grace", type=_non_negative_float, default=60.0, help="runが登録されるまで待つ秒数。省略時は60秒とする。"
    )
    parser.add_argument(
        "--subprocess-timeout",
        type=_positive_float,
        default=60.0,
        help="個別forge CLI実行のタイムアウト秒数。省略時は60秒で打ち切る。",
    )
    parser.add_argument(
        "--forge",
        choices=("auto", "github", "gitlab"),
        default="auto",
        help=(
            "対象ホスティング種別。省略時はautoとして--repoのホストから自動判定する。"
            "ホストを含まない場合は作業ディレクトリのGit remoteから判定する。"
            "--baselineで--repoを省略した場合はbaselineの保存値を使う。"
        ),
    )
    parser.add_argument(
        "--follow-cancelled",
        action="store_true",
        help="全runがcancelledの場合に、source refの後続commitで実行されたrunの成功を追跡する",
    )
    args = parser.parse_args(argv)
    if args.wait_sha is not None and args.sha is not None:
        parser.error("--wait-shaと--shaは同時に指定できません")
    if args.failure_record is not None and args.wait_sha is None:
        parser.error("--failure-recordは--wait-shaと組み合わせて指定してください")
    baseline: CiBaseline | None = None
    if args.baseline is None:
        missing = [
            name
            for name, value in (("--repo", args.repo), ("--ref", args.ref), ("--source-ref", args.source_ref))
            if value is None
        ]
        if missing:
            parser.error(f"--write-baselineと--wait-shaでは次の引数が必須です: {', '.join(missing)}")
        forge = _resolve_forge(args.forge, args.repo)
    else:
        try:
            baseline = _load_baseline(args.baseline)
        except RunListError as exc:
            print(f"[wait_ci] baseline検証に失敗: {exc}", file=sys.stderr)
            return _finish(EXIT_GH_ERROR, _NEXT_ACTION_BASELINE_MISMATCH)
        # 省略した値はbaselineの保存値を使い、指定した値は後段でbaselineの保存値と比べる。
        explicit_repo = args.repo is not None
        args.repo = baseline.repository if args.repo is None else args.repo
        args.ref = baseline.ref if args.ref is None else args.ref
        args.source_ref = baseline.source_ref if args.source_ref is None else args.source_ref
        forge = _resolve_forge(args.forge, args.repo) if explicit_repo or args.forge != "auto" else baseline.forge
    if forge is None:
        print("[wait_ci] 対象forgeを--repoと作業ディレクトリのGit remoteから判別できない", file=sys.stderr)
        return _finish(EXIT_GH_ERROR, "`--forge github`または`--forge gitlab`を明示指定して再実行する")
    if args.write_baseline is not None:
        revision = args.sha if args.sha is not None else args.source_ref
        sha = _resolve_sha(revision, args.subprocess_timeout)
        if sha is None:
            print(f"[wait_ci] {revision}のcommit解決に失敗（git rev-parse）", file=sys.stderr)
            return _finish(EXIT_GH_ERROR, _NEXT_ACTION_SHA_RESOLUTION)
        try:
            runs = _default_run_list_fn(forge, args.repo, args.ref, args.subprocess_timeout)(sha)
            baseline = CiBaseline(
                forge=forge,
                repository=args.repo,
                ref=args.ref,
                source_ref=args.source_ref,
                sha=sha,
                run_ids=frozenset(_run_ids(runs)),
            )
            _write_baseline(args.write_baseline, baseline)
        except (OSError, RunListError) as exc:
            print(f"[wait_ci] baseline作成に失敗: {exc}", file=sys.stderr)
            next_action = (
                "`--write-baseline`の保存先ディレクトリが存在し書き込めることを確かめて再実行する"
                if isinstance(exc, OSError)
                else _NEXT_ACTION_FORGE_ERROR
            )
            return _finish(EXIT_GH_ERROR, next_action)
        print(f"[wait_ci] baseline保存: {args.write_baseline} ({len(baseline.run_ids)}件)")
        return EXIT_SUCCESS
    if args.wait_sha is not None:
        record_path = args.failure_record
        baseline = CiBaseline(forge, args.repo, args.ref, args.source_ref, args.wait_sha, frozenset())
        try:
            if record_path is not None:
                if record_path.exists():
                    baseline = _load_baseline(record_path)
                    _validate_baseline_context(baseline, forge, args.repo, args.ref, args.source_ref, args.wait_sha)
                    if baseline.run_ids:
                        raise RunListError("--wait-shaの記録にbaselineの除外runが含まれている")
                else:
                    _write_baseline(record_path, baseline)
        except (OSError, RunListError) as exc:
            print(f"[wait_ci] 待機記録の準備に失敗: {exc}", file=sys.stderr)
            return _finish(EXIT_GH_ERROR, "対象に一致する書込可能な`--failure-record`を指定して再実行する")
        return _wait_from_record(args, baseline, record_path)
    assert baseline is not None
    try:
        if args.sha is None:
            sha = baseline.sha
        else:
            sha = _resolve_sha(args.sha, args.subprocess_timeout)
            if sha is None:
                print(f"[wait_ci] {args.sha}のcommit解決に失敗（git rev-parse）", file=sys.stderr)
                return _finish(EXIT_GH_ERROR, _NEXT_ACTION_SHA_RESOLUTION)
        _validate_baseline_context(baseline, forge, args.repo, args.ref, args.source_ref, sha)
    except RunListError as exc:
        print(f"[wait_ci] baseline検証に失敗: {exc}", file=sys.stderr)
        return _finish(EXIT_GH_ERROR, _NEXT_ACTION_BASELINE_MISMATCH)
    return _wait_from_record(args, baseline, args.baseline)


def _wait_from_record(args: argparse.Namespace, baseline: CiBaseline, path: pathlib.Path | None) -> int:
    """対象と報告済み集合を待機へ渡し、部分結果の返却前に同じ記録を更新する。"""

    def save(reported: set[str]) -> None:
        if path is not None:
            _write_baseline(path, dataclasses.replace(baseline, reported_failures=frozenset(reported)))

    return wait_for_ci(
        baseline.sha,
        args.timeout,
        args.poll_interval,
        args.registration_grace,
        args.follow_cancelled,
        args.subprocess_timeout,
        repository=args.repo,
        ref=args.ref,
        source_ref=args.source_ref,
        baseline_ids=baseline.run_ids,
        forge=baseline.forge,
        reported_failures=baseline.reported_failures,
        save_failures=save if path is not None else None,
    )


if __name__ == "__main__":
    sys.exit(main())
