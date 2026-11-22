import functools
import pathlib
import sys
import tempfile
from collections.abc import Callable, Iterable

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi.layout import entry_state_of, iter_entry_paths
from agent_toolkit._common import private_notes as _private_notes
lazy from agent_toolkit._atk.wi.formatters import parse_type


def normalize_md_filename(filename: str) -> str:
    if not filename.endswith(".md"):
        return f"{filename}.md"
    return filename


def validate_filename(filename: str, base_dir: pathlib.Path) -> pathlib.Path:
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
    for f in filenames:
        validate_filename(f, base_dir)


def existing_entry_filenames(private_notes: pathlib.Path) -> set[str]:
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
    return name if case_sensitive else name.lower()


MISSING_DEPENDENCY_NEXT_ACTION = "登録は完了した。依存先を投入するか、`atk wi set-dependencies`で依存を直す"
"""`depends_on`の参照先が取り込み先に無いときの警告に続ける次の操作。"""


def missing_dependency_warnings(
    references: Iterable[tuple[str, str]],
    *,
    resolvable: set[str],
    case_sensitive: bool,
) -> list[str]:
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
