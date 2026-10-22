"""WIのファイル名の検証、重複除去、採番と補完候補。"""

import functools
import pathlib
import sys
import tempfile
from collections.abc import Callable, Iterable

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi.layout import entry_state_of, iter_entry_paths
from agent_toolkit._common import private_notes as _private_notes

# 補完器を登録するだけの起動では本文を解析しないため、parse_typeを使用時に読み込む。
lazy from agent_toolkit._atk.wi.formatters import parse_type


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
    """年月配置に実在する`.md`ファイル名の集合を返す。"""
    return {path.name for path in iter_entry_paths(private_notes)}


def is_case_sensitive(directory: pathlib.Path) -> bool:
    """指定ディレクトリのファイルシステムがファイル名の大文字小文字を区別するかを実際にファイルを作成して確認する。

    OS種別から推定すると誤る（`os.path.normcase`はPOSIX実装では恒等関数であり、
    大文字小文字を区別しないファイルシステムが標準で使われる環境でも名前を畳み込まない）ため、
    一意な名前の空ファイルを指定ディレクトリ内に作成し、名前の大文字小文字を反転させたパスが
    存在するかどうかで判定する。プローブ用ファイルは判定後に必ず削除する。
    """
    with tempfile.NamedTemporaryFile(prefix=".atk-case-probe-", dir=directory) as temporary:
        probe = pathlib.Path(temporary.name)
        return not probe.with_name(probe.name.swapcase()).exists()


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
    """位置引数として渡された`filenames`の同一項目を1件へ集約する。

    正規化後の同一性で重複判定するため、`normalize_md_filename`で正規化した値をキーに
    順序保存する（例: `name`と`name.md`は同一項目として1件へ集約）。
    呼び出し元は`show.py`の`cmd_show`と、`mutations/transitions.py`の
    `cmd_start_processing`・`cmd_return_to_inbox`・`cmd_adopt`・`cmd_reject`・`cmd_rm`とする。
    戻り値は正規化前の原文字列のうち初出のものを保持する（正規化は判定にのみ用いる）。
    """
    del subcommand
    seen: dict[str, str] = {}
    for name in filenames:
        key = normalize_md_filename(name)
        if key not in seen:
            seen[key] = name
    return list(seen.values())


def make_filename_completer(
    states: tuple[str, ...],
    entry_type: str | None = None,
) -> Callable[..., list[str]]:
    """argcomplete用のキュー内ファイル名補完候補生成器を返す。

    `states`が指すfrontmatterの状態に一致する`.md`をprefix一致で列挙する。
    `entry_type`を指定した場合はfrontmatterの`type`が一致するものだけを返す。
    状態を判定するため本文を読む。種別を限定する場合は同じ本文から種別も判定する。
    """

    def complete(prefix: str, **_: object) -> list[str]:
        private_notes = _private_notes.default_private_notes()
        candidates: list[str] = []
        for path in iter_entry_paths(private_notes):
            if entry_state_of(path) not in states:
                continue
            if not path.name.startswith(prefix):
                continue
            if entry_type is not None and parse_type(path.read_text(encoding="utf-8")) != entry_type:
                continue
            candidates.append(path.name)
        return sorted(candidates)

    return complete


def max_existing_seq(private_notes: pathlib.Path, timestamp_prefix: str) -> int:
    """同一タイムスタンププレフィックスを持つファイルの最大連番を、年月配置全体から返す。

    例えば`{prefix}-001.md`と`{prefix}-003.md`が存在する場合は3を返す。
    非連続連番でも新規生成側で既存ファイルへ衝突しないよう最大値を基準にする。
    inboxだけを対象にすると、同一秒に採番したエントリが状態遷移した後の再投入で
    連番が再発行され、`adopted`・`rejected`等の既存エントリと同名衝突を起こすため、
    年月配置全体を走査対象にする。
    """
    max_seq = 0
    for p in iter_entry_paths(private_notes):
        if not p.name.startswith(f"{timestamp_prefix}-"):
            continue
        try:
            seq = int(p.stem.rsplit("-", 1)[-1])
        except ValueError:
            continue
        max_seq = max(max_seq, seq)
    return max_seq
