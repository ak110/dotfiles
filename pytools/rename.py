"""正規表現でリネーム。

単発のパターン指定に加えて、rgrename 互換のパターンファイル (TAB 区切り UTF-8)
を `-f/--pattern-file` で読み込めるようにしている。パターンファイルローダーは
`load_pattern_file()` / `apply_rules()` としてモジュール関数で公開し、
`pytools.repack_archive` からも再利用する。
"""

import argparse
import dataclasses
import logging
import pathlib
import re
import typing

logger = logging.getLogger(__name__)

RuleTarget = typing.Literal["both", "file", "dir"]


@dataclasses.dataclass(frozen=True)
class RenameRule:
    """リネームルール 1 件。`pattern` を `replacement` に置換する。"""

    pattern: re.Pattern[str]
    replacement: str
    target: RuleTarget = "both"

    def applies_to(self, is_dir: bool) -> bool:
        """対象種別 (ファイル/ディレクトリ) に適用すべきかを判定する。"""
        if self.target == "both":
            return True
        if self.target == "file":
            return not is_dir
        return is_dir


def load_pattern_file(path: pathlib.Path, *, ignore_case: bool = False) -> list[RenameRule]:
    r"""Rgrename 互換のパターンファイルを読み込む。

    行形式 (TAB 区切り):

    - ``正規表現\\t置換文字列`` (ファイル・ディレクトリ両方)
    - ``F\\t正規表現\\t置換文字列`` (ファイルのみ)
    - ``D\\t正規表現\\t置換文字列`` (ディレクトリのみ)

    行頭 ``#`` と空行はスキップする。
    """
    flags = re.IGNORECASE if ignore_case else 0
    rules: list[RenameRule] = []
    comment_re = re.compile(r"^\s*#")
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw or comment_re.match(raw):
            continue
        parts = raw.split("\t")
        if len(parts) == 2:
            rules.append(RenameRule(pattern=re.compile(parts[0], flags=flags), replacement=parts[1]))
        elif len(parts) == 3:
            prefix, pattern_str, replacement = parts
            if prefix == "F":
                target: RuleTarget = "file"
            elif prefix == "D":
                target = "dir"
            else:
                logger.warning("%s:%d: 不正なプレフィクス '%s' (F/D のみ有効)", path, lineno, prefix)
                continue
            rules.append(RenameRule(pattern=re.compile(pattern_str, flags=flags), replacement=replacement, target=target))
        else:
            logger.warning("%s:%d: 書式が不正な行をスキップ", path, lineno)
    return rules


def apply_rules_to_name(name: str, rules: typing.Iterable[RenameRule], *, is_dir: bool) -> str:
    """対象名に全ルールを順次適用し、最終結果を返す。"""
    result = name
    for rule in rules:
        if rule.applies_to(is_dir):
            result = rule.pattern.sub(rule.replacement, result)
    return result.strip()


def rename_tree(
    root: pathlib.Path,
    rules: list[RenameRule],
    *,
    files_only: bool = False,
    dirs_only: bool = False,
    recursive: bool = False,
    enable_mkdir: bool = False,
    overwrite: bool = False,
    dry_run: bool = False,
) -> None:
    """ディレクトリツリーを走査してルールを適用する。

    ディレクトリの改名は下位ツリーのパスを無効化するため、深い階層から処理する
    (rglob の結果をパス長でソート)。
    """
    if files_only and dirs_only:
        raise ValueError("files_only と dirs_only は同時に指定できません")

    entries: list[pathlib.Path]
    entries = list(root.rglob("*")) if recursive else list(root.iterdir())
    # 改名順: まずディレクトリを深い方から、続いてファイル
    entries.sort(key=lambda p: (not p.is_dir(), -len(p.parts)))

    for path in entries:
        try:
            is_dir = path.is_dir()
            if files_only and is_dir:
                continue
            if dirs_only and not is_dir:
                continue
            new_name = apply_rules_to_name(path.name, rules, is_dir=is_dir)
            if new_name == path.name:
                continue
            if not new_name:
                logger.warning("置換後の名前が空になるためスキップ: %s", path)
                continue
            new_path = path.parent / new_name
            if enable_mkdir:
                new_path.parent.mkdir(parents=True, exist_ok=True)
            if new_path.exists() and not overwrite:
                logger.warning("既存のためスキップ: %s -> %s", path, new_path)
                continue
            print(f"{path.name} -> {new_name}")
            if not dry_run:
                path.rename(new_path)
        except OSError as e:
            logger.warning("%s: rename 失敗 (%s)", path, e)


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-d", "--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-f", "--pattern-file", type=pathlib.Path, help="rgrename 互換のパターンファイル")
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument(
        "-t",
        "--target-dir",
        type=pathlib.Path,
        default=pathlib.Path.cwd(),
        help="処理対象ディレクトリ (既定: カレント)",
    )
    parser.add_argument("-P", "--make-parents", action="store_true", help="改名先ディレクトリを作成する")
    parser.add_argument("-O", "--overwrite", action="store_true", help="既存ファイル/ディレクトリを上書き許可する")
    fd_group = parser.add_mutually_exclusive_group()
    fd_group.add_argument("-F", "--files-only", action="store_true")
    fd_group.add_argument("-D", "--dirs-only", action="store_true")
    parser.add_argument("pattern", type=str, nargs="?", help="正規表現 (pattern-file 指定時は省略可)")
    parser.add_argument("replacement", type=str, nargs="?", help="置換文字列 (pattern-file 指定時は省略可)")
    parser.add_argument("targets", nargs="*", type=pathlib.Path)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--stem", action="store_true", help="replace stem only. (default, ignored in pattern-file mode)")
    mode_group.add_argument("--name", action="store_true", help="replace name only.")
    mode_group.add_argument("--fullpath", action="store_true", help="replace fullpath.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    if args.pattern_file:
        rules = load_pattern_file(args.pattern_file, ignore_case=args.ignore_case)
        if args.pattern and args.replacement:
            flags = re.IGNORECASE if args.ignore_case else 0
            rules.append(RenameRule(pattern=re.compile(args.pattern, flags=flags), replacement=args.replacement))
        if not rules:
            parser.error("パターンファイルに有効なエントリがありません")
        rename_tree(
            args.target_dir,
            rules,
            files_only=args.files_only,
            dirs_only=args.dirs_only,
            recursive=args.recursive,
            enable_mkdir=args.make_parents,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
        return

    if not args.pattern or args.replacement is None:
        parser.error("pattern と replacement を指定するか、-f でパターンファイルを指定してください")

    targets = args.targets
    if len(targets) <= 0:
        targets = list(pathlib.Path(".").glob("*"))

    flags = 0
    if args.ignore_case:
        flags |= re.IGNORECASE
    regex = re.compile(args.pattern, flags=flags)

    for src_path in targets:
        try:
            if args.fullpath:
                dst_path = pathlib.Path(regex.sub(args.replacement, str(src_path)))
                if src_path == dst_path:
                    continue
                print(f"{src_path} -> {dst_path}")
            elif args.name:
                dst_name = regex.sub(args.replacement, src_path.name).strip()
                dst_path = src_path.parent / dst_name
                if src_path == dst_path:
                    continue
                print(f"{src_path.name} -> {dst_name}")
            else:
                dst_stem = regex.sub(args.replacement, src_path.stem).strip()
                dst_path = src_path.parent / (dst_stem + src_path.suffix)
                if src_path == dst_path:
                    continue
                print(f"{src_path.stem} -> {dst_stem}")
            if not args.dry_run:
                src_path.rename(dst_path)
        except OSError as e:
            print(f"{src_path}: rename failed ({e})")


if __name__ == "__main__":
    _main()
