"""セッション振り返りの証拠抽出に必要な準備項目を1行のJSONで取得する。

本スクリプトは検査スクリプトではなくデータ取得ツールであるため、
`agent-toolkit:writing-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
引数誤用と準備項目を取得できない実行前エラーを終了コード2とする区分だけを踏襲する。
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import shutil
import subprocess
import sys


def _build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数を定義する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    session = parser.add_mutually_exclusive_group(required=True)
    session.add_argument("--transcript", metavar="PATH", help="Claude Codeのtranscriptパス。")
    session.add_argument("--codex-thread-id", metavar="ID", help="Codexのthread ID。")
    parser.add_argument("--target-repo", metavar="PATH", help="振り返り対象のリポジトリ。")
    return parser


def _missing(item: str) -> int:
    """取得できなかった準備項目を報告する。"""
    print(f"不足: {item}", file=sys.stderr)
    return 2


def _run_atk(executable: str, arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    """`atk`を実行し、起動できない場合は`None`を返す。"""
    try:
        return subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None


def _managed_temp_path(result: subprocess.CompletedProcess[str] | None) -> pathlib.Path | None:
    """管理対象一時領域の作成結果から検証済みの絶対パスを返す。"""
    if result is None or result.returncode != 0:
        return None
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        return None
    path = pathlib.Path(lines[0])
    return path if path.is_absolute() and path.is_dir() else None


def _reference_document(target_repo: pathlib.Path | None, *, codex: bool) -> pathlib.Path | None:
    """Git共通dirから対象リポジトリ固有の振り返り参照文書を解決する。"""
    if target_repo is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(target_repo), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 1:
        return None
    common_dir = pathlib.Path(lines[0])
    if not common_dir.is_absolute():
        common_dir = target_repo / common_dir
    repository_name = common_dir.resolve().parent.name
    path = pathlib.Path.home() / (".codex" if codex else ".claude") / "docs" / f"session-review-{repository_name}.md"
    return path if path.is_file() else None


def main(argv: list[str] | None = None, *, now: datetime.datetime | None = None) -> int:
    """準備項目を取得して1行のJSONを出力する。"""
    args = _build_parser().parse_args(argv)
    evidence_script = pathlib.Path(__file__).resolve().with_name("session_review_evidence.py")
    if not evidence_script.is_file():
        return _missing("evidence_script")
    report_script = pathlib.Path(__file__).resolve().with_name("session_review_report.py")
    if not report_script.is_file():
        return _missing("report_script")

    transcript_path = pathlib.Path(args.transcript).expanduser().resolve() if args.transcript is not None else None
    if transcript_path is not None and not transcript_path.is_file():
        return _missing("transcript_path")

    executable = shutil.which("atk")
    if executable is None:
        return _missing("managed_temp")
    create_result = _run_atk(executable, ["managed-temp", "create", "--prefix", "session-review"])
    managed_temp = _managed_temp_path(create_result)
    if managed_temp is None:
        return _missing("managed_temp")

    current = now if now is not None else datetime.datetime.now(datetime.UTC)
    observation_boundary = current.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    target_repo = pathlib.Path(args.target_repo).expanduser().resolve() if args.target_repo is not None else None
    bundle_dir = managed_temp / "bundle"
    try:
        bundle_dir.mkdir(exist_ok=True)
    except OSError:
        return _missing("bundle_dir")
    reference_document = _reference_document(target_repo, codex=args.codex_thread_id is not None)
    record = {
        "evidence_script": str(evidence_script),
        "report_script": str(report_script),
        "plugin_root": str(pathlib.Path(__file__).resolve().parents[3]),
        "transcript_path": str(transcript_path) if transcript_path is not None else None,
        "codex_thread_id": args.codex_thread_id,
        "managed_temp": str(managed_temp),
        "bundle_dir": str(bundle_dir),
        "observation_boundary": observation_boundary,
        "target_repo": str(target_repo) if target_repo is not None else None,
        "reference_document": str(reference_document) if reference_document is not None else None,
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
