import argparse
import os
import pathlib
import subprocess
import tempfile

from agent_toolkit._atk import outcome as _outcome


def is_existing_dir(path: pathlib.Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def copy_to_tempfile(content: bytes) -> pathlib.Path:
    """バイト列を`.md`拡張子の一時ファイルへ書き込み、そのパスを返す。

    エディターをロック外で起動する処理（`cmd_edit`等）が、ロック保持下で取得した
    対象ファイルのスナップショットを一時ファイルへ複製する用途に用いる。
    """
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".md", delete=False) as f:
        f.write(content)
        return pathlib.Path(f.name)


def resolve_repo_path_override(
    args_messages: list[str],
    pre_parse_override: str | None,
) -> tuple[list[str], str | None]:
    """旧REPO_PATH位置引数形式の呼び出しを解決する（`atk.py`の`_extract_legacy_repo_path`の後段）。

    `pre_parse_override`（サブコマンド名直後のトークンをargparse解析前に抽出した結果）が
    設定済みならそれを優先する。未設定の場合、argparseが位置引数を1つの連続した並びとして
    解決できた呼び出し（REPO_PATHがオプションの後ろに置かれた呼び出し等）を対象に、
    messages先頭が実在ディレクトリなら追加でREPO_PATHとして抽出する。
    """
    messages = list(args_messages)
    if pre_parse_override is not None:
        return messages, pre_parse_override
    if not messages or not messages[0]:
        # 空文字列は`Path("").expanduser()`がカレントディレクトリ（常に実在）へ解決され、
        # 本文としての空メッセージ（UWIの空質問等）をREPO_PATHと誤認するため除外する。
        return messages, None
    candidate = pathlib.Path(messages[0]).expanduser()
    if not is_existing_dir(candidate):
        return messages, None
    return messages[1:], str(candidate)


def reject_bare_repo_path_override(
    repo_path_override: str | None,
    messages: list[str],
    subparser: argparse.ArgumentParser,
) -> None:
    if repo_path_override is None or messages:
        return
    subparser.error(
        f"投入する本文の代わりにディレクトリパス（{repo_path_override}）が渡されました。"
        "対象リポジトリはカレントディレクトリから自動判定されるため、パスの指定は不要です。"
    )


def collect_message_via_editor(*, strip: bool = True) -> str | None:
    editor = os.environ.get("EDITOR")
    if not editor:
        _outcome.report_failure(
            "$EDITORが未設定のためエディターで本文を入力できない",
            next_action="$EDITORを設定するか--body-fileを指定して再実行する",
        )
        return None
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as f:
        tmp_path = pathlib.Path(f.name)
    try:
        # 時間上限なし: 対話型のエディターであり、ユーザーが終了させる。共有ロックは保持していない。
        result = subprocess.run([editor, str(tmp_path)], check=False)
        if result.returncode != 0:
            _outcome.report_failure(
                f"エディターが終了コード{result.returncode}で終了した",
                next_action="本文は保存していない。本文をファイルへ書き、--body-fileで指定して再実行する",
            )
            return None
        saved = tmp_path.read_text(encoding="utf-8")
        if not saved.strip():
            _outcome.report_failure("本文が空のため投入を中止した", next_action="本文を書いてから再実行する")
            return None
        return saved.strip() if strip else saved
    finally:
        tmp_path.unlink(missing_ok=True)
