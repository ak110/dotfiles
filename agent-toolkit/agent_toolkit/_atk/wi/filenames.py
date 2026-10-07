"""WIのファイル名の検証、重複除去、採番と補完候補。"""

import functools
import os
import pathlib
import sys
import tempfile
from collections.abc import Callable, Iterable

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi.constants import WI_STATES
from agent_toolkit._common import private_notes as _private_notes


def normalize_md_filename(filename: str) -> str:
    """拡張子`.md`が省略されたファイル名を正規形（`.md`付き）へ補完して返す。

    ファイル名を受け取る際に、呼び出し元によらず同一の規約で正規化する。
    パス妥当性検証は行わない（呼び出し元が別途`validate_filename`等で担う）。
    """
    if not filename.endswith(".md"):
        return f"{filename}.md"
    return filename


def validate_filename(filename: str, base_dir: pathlib.Path) -> pathlib.Path:
    r"""ファイル名が基準ディレクトリ直下の単純名であることを検証して絶対パスを返す。

    `/`・`\`・`..`・絶対パス・空文字列・カレント参照は早期に拒否する。
    拡張子`.md`が省略された入力は正規形（`.md`付き）へ補完する。
    """
    parts = pathlib.Path(filename).parts
    if (
        filename in ("", ".", "..")
        or "/" in filename
        or "\\" in filename
        or ".." in parts
        or pathlib.PurePath(filename).is_absolute()
    ):
        _outcome.report_failure(
            f"不正なファイル名: {filename}", next_action="パス区切りを含まないファイル名を指定して再実行する"
        )
        sys.exit(2)
    filename = normalize_md_filename(filename)
    path = base_dir / filename
    base_resolved = base_dir.resolve()
    try:
        path.resolve().relative_to(base_resolved)
    except ValueError:
        _outcome.report_failure(
            f"ファイル名が基準ディレクトリ外を指す: {filename}",
            next_action="基準ディレクトリ内のファイル名を指定して再実行する",
        )
        sys.exit(2)
    return path


def validate_filenames_only(filenames: list[str], base_dir: pathlib.Path) -> None:
    """ファイル名群のみ検証する（pull前の早期拒否用）。"""
    for f in filenames:
        validate_filename(f, base_dir)


def existing_entry_filenames(private_notes: pathlib.Path) -> set[str]:
    """5状態フォルダに実在する`.md`ファイル名の集合を返す。"""
    return {
        path.name
        for state in WI_STATES
        if (private_notes / state).exists()
        for path in (private_notes / state).iterdir()
        if path.suffix == ".md"
    }


def is_case_sensitive(directory: pathlib.Path) -> bool:
    """指定ディレクトリのファイルシステムがファイル名の大文字小文字を区別するかを実際にファイルを作成して確認する。

    OS種別から推定すると誤る（`os.path.normcase`はPOSIX実装では恒等関数であり、
    大文字小文字を区別しないファイルシステムが標準で使われる環境でも名前を畳み込まない）ため、
    一意な名前の空ファイルを指定ディレクトリ内に作成し、名前の大文字小文字を反転させたパスが
    存在するかどうかで判定する。プローブ用ファイルは判定後に必ず削除する。
    """
    handle, created = tempfile.mkstemp(prefix=".atk-case-probe-", dir=directory)
    os.close(handle)
    probe = pathlib.Path(created)
    try:
        return not probe.with_name(probe.name.swapcase()).exists()
    finally:
        probe.unlink()


def comparison_key(name: str, *, case_sensitive: bool) -> str:
    """ファイル名の衝突判定に用いる比較キーを返す。

    大文字小文字を区別しないファイルシステムでは同一物理パスへ解決される名前を同一視するため
    小文字化したキーを返し、区別するファイルシステムでは元の名前をそのまま返す。
    保存名自体はこのキーと分離し、常に元の大文字小文字を維持する。
    """
    return name if case_sensitive else name.lower()


MISSING_DEPENDENCY_NEXT_ACTION = "登録は完了した。依存先を投入するか、`atk wi set-dependencies`で依存を直す"
"""`depends_on`の参照先が取り込み先に無いときの警告に続ける次の操作。"""


def missing_dependency_warnings(
    references: Iterable[tuple[str, str]],
    *,
    resolvable: set[str],
    case_sensitive: bool,
) -> list[str]:
    """取り込み先に実在しない`depends_on`参照を警告文へ列挙する。

    `references`は`(参照元の保存ファイル名, 依存先の原文)`の列とする。
    実在判定は`comparison_key`が返す比較キーで行い、大文字小文字を区別しないファイルシステムで
    大小の綴りだけが異なる参照を不在と誤判定しない。警告文には参照の原文を用いる。
    判定と文面を`atk wi add`の通常の投入と`--batch`指定時で共有し、どちらの場合も同じ条件の参照へ同じ警告を返す。
    """
    key = functools.partial(comparison_key, case_sensitive=case_sensitive)
    resolvable_keys = {key(name) for name in resolvable}
    return [
        f"{source}のdepends_onが参照する{dependency}は取り込み先に実在しません"
        for source, dependency in references
        if key(dependency) not in resolvable_keys
    ]


def dedup_positional_filenames(filenames: list[str], subcommand: str) -> list[str]:
    """位置引数として渡された`filenames`から重複を除去し、除去件数が0より大きい場合はstderrへ警告する。

    正規化後の同一性で重複判定するため、`normalize_md_filename`で正規化した値をキーに
    順序保存する（例: `name`と`name.md`は同一項目として1件へ集約）。
    呼び出し元は`_atk_wi_show.py`の`_cmd_show`と、`_atk_wi_mutations.py`の
    `_cmd_start_processing`・`_cmd_return_to_inbox`・`_cmd_adopt`・`_cmd_reject`・`_cmd_rm`とする。
    戻り値は正規化前の原文字列のうち初出のものを保持する（正規化は判定にのみ用いる）。
    """
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for name in filenames:
        key = normalize_md_filename(name)
        if key in seen:
            duplicates.append(name)
        else:
            seen[key] = name
    if duplicates:
        unique_duplicates = list(dict.fromkeys(duplicates))
        _outcome.report_warning(
            f"{subcommand}の引数リストに重複がある（重複を除いて処理を継続する）: {', '.join(unique_duplicates)}",
            next_action="対応不要（重複を除いて処理を継続した）",
        )
    return list(seen.values())


def make_filename_completer(
    states: tuple[str, ...],
    entry_type: str | None = None,
) -> Callable[..., list[str]]:
    """argcomplete用のキュー内ファイル名補完候補生成器を返す。

    `states`が指す状態ディレクトリ配下の`.md`をprefix一致で列挙する。
    `entry_type`を指定した場合はfrontmatterの`type`が一致するものだけを返す。
    種別を限定する場合だけ本文を読むため、限定しない場合はディレクトリ走査で完結する。
    """

    def complete(prefix: str, **_: object) -> list[str]:
        private_notes = _private_notes.default_private_notes()
        candidates: list[str] = []
        for state in states:
            state_dir = private_notes / state
            if not state_dir.exists():
                continue
            for path in state_dir.iterdir():
                if path.suffix != ".md" or not path.name.startswith(prefix):
                    continue
                if entry_type is not None and _wi_entries.parse_type(path.read_text(encoding="utf-8")) != entry_type:
                    continue
                candidates.append(path.name)
        return sorted(candidates)

    return complete


def max_existing_seq(private_notes: pathlib.Path, timestamp_prefix: str) -> int:
    """同一タイムスタンププレフィックスを持つファイルの最大連番を、5状態すべてから返す。

    例えば`{prefix}-001.md`と`{prefix}-003.md`が存在する場合は3を返す。
    非連続連番でも新規生成側で既存ファイルへ衝突しないよう最大値を基準にする。
    inboxのみを走査すると、同一秒に採番したエントリが別状態へ遷移した後の再投入で
    連番が再発行され、`adopted`・`rejected`等の既存エントリと同名衝突を起こすため、
    5状態フォルダすべてを走査対象にする。
    """
    max_seq = 0
    for state in WI_STATES:
        state_dir = private_notes / state
        if not state_dir.exists():
            continue
        for p in state_dir.iterdir():
            if not p.name.startswith(f"{timestamp_prefix}-"):
                continue
            try:
                seq = int(p.stem.rsplit("-", 1)[-1])
            except ValueError:
                continue
            max_seq = max(max_seq, seq)
    return max_seq
