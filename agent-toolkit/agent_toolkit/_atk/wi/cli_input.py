"""`atk wi`のCLIが受け取る本文と位置引数の解釈。"""

import argparse
import os
import pathlib
import subprocess
import tempfile

from agent_toolkit._atk import outcome as _outcome


def is_existing_dir(path: pathlib.Path) -> bool:
    """パスが実在ディレクトリかどうかを判定する（OSレベルの`OSError`はFalse扱い）。

    自由記述のMESSAGE文字列をパス候補として`is_dir()`へ渡す呼び出し元があり、
    長大な文字列は`OSError: File name too long`を送出しうるため、ここで吸収する。
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def copy_to_tempfile(content: bytes) -> pathlib.Path:
    """バイト列を`.md`拡張子の一時ファイルへ書き込み、そのパスを返す。

    エディターをロック外で起動する処理（`_cmd_edit`等）が、ロック保持下で取得した
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
    設定済みならそれを優先する。未設定の場合、argparseが単一のcontiguousな位置引数群として
    解決できたケース（REPO_PATHがオプションの後ろに置かれた呼び出し等）を対象に、
    messages先頭が実在ディレクトリなら追加でREPO_PATHとして抽出する。
    """
    messages = list(args_messages)
    if pre_parse_override is not None:
        return messages, pre_parse_override
    if not messages or not messages[0]:
        # 空文字列は`Path("").expanduser()`がカレントディレクトリ（常に実在）へ解決され、
        # 本文としての空メッセージ（UWIの空質問等）を誤ってREPO_PATHと誤認するため除外する。
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
    """先頭引数がディレクトリと解釈されたのに本文が続かない呼び出しをusage表示付きで拒否する。

    対象リポジトリは常にカレントディレクトリから自動判定する。ディレクトリらしき引数の後ろに
    本文が続かない呼び出しは誤指定とみなし、`subparser.error()`でargparse標準のusage行に
    続けて平易な文言を出力しexit 2する。
    """
    if repo_path_override is None or messages:
        return
    subparser.error(
        f"投入する本文の代わりにディレクトリパス（{repo_path_override}）が渡されました。"
        "対象リポジトリはカレントディレクトリから自動判定されるため、パスの指定は不要です。"
    )


def collect_message_via_editor(*, strip: bool = True) -> str | None:
    """$EDITORで一時ファイルを開き、保存内容を返す。

    指定が無ければ保存内容をstripして返す。`strip=False`は保存内容を原文のまま返し、
    空判定だけをstrip結果で行う（原文保持が必要な一括取り込みで用いる）。
    $EDITOR未設定・エディター非ゼロ終了・保存内容が空のいずれもNoneを返し、
    原因をstderrへ出力する。一時ファイルは終了時に必ず削除する。
    """
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
