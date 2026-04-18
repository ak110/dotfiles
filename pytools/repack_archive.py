"""アーカイブを gv 向けに前処理して無圧縮 ZIP へ再パックする。

既存の bat 前処理ワークフロー (7z で全解凍 → rmdirs → imageconverter → 7z 再圧縮)
を 1 つのコマンドに統合した Python 実装。設定は YAML ファイルで与え、ignore
パターンやリネームルール、画像変換オプションを一元管理する。

対応する機能:

- libarchive-c による ZIP / RAR / 7z / tar 等の選択的解凍 (無視エントリを
  そもそもディスクへ展開しない)。
- rgrename 互換のパターンファイル / YAML 直書きルールの混在を許可。
- 画像変換は `pytools.imageconverter.convert_directory` を関数呼び出しで実行し、
  呼び元 tqdm で進捗を表示する。
- 最終出力は無圧縮 ZIP (JPEG + 残ったテキストのみ)。単一ルートディレクトリは
  平坦化する。
- バックアップは `<parent>/bk/` に作成し、処理完了後にゴミ箱送り。
"""

import argparse
import fnmatch
import functools
import logging
import os
import pathlib
import re
import shutil
import sys
import tempfile
import typing
import zipfile

import pydantic
import send2trash
import tqdm
import yaml

from pytools import imageconverter, rename

logger = logging.getLogger(__name__)

_BACKUP_DIR_NAME = "bk"
_WORKDIR_PREFIX = "repack-archive-"
# 長さ順にソート済み (複合拡張子 .tar.gz 等を .gz より先に判定するため)
_ARCHIVE_SUFFIXES: tuple[str, ...] = (
    ".tar.bz2",
    ".tar.zst",
    ".tar.gz",
    ".tar.xz",
    ".tbz2",
    ".tgz",
    ".txz",
    ".cbz",
    ".cbr",
    ".cb7",
    ".iso",
    ".zip",
    ".rar",
    ".tar",
    ".cab",
    ".7z",
)


class _InlineRenameRule(pydantic.BaseModel):
    """YAML 直書きのリネームルール 1 件。"""

    pattern: str
    replacement: str
    target: typing.Literal["both", "file", "dir"] = "both"
    model_config = pydantic.ConfigDict(extra="forbid")


class _PatternFileRule(pydantic.BaseModel):
    """rgrename 形式の外部パターンファイル参照。"""

    pattern_file: str
    model_config = pydantic.ConfigDict(extra="forbid")


class _ImageConfig(pydantic.BaseModel):
    output_type: typing.Literal["jpeg", "png", "webp"] = "jpeg"
    max_width: int = 2048
    max_height: int = 1536
    jpeg_quality: int = 90
    repack_png: bool = False
    model_config = pydantic.ConfigDict(extra="forbid")


class RepackConfig(pydantic.BaseModel):
    """repack-archive の YAML 設定。"""

    ignore_files: list[str] = pydantic.Field(default_factory=list)
    ignore_dirs: list[str] = pydantic.Field(default_factory=list)
    rename_rules: list[_InlineRenameRule | _PatternFileRule] = pydantic.Field(default_factory=list)
    image: _ImageConfig = pydantic.Field(default_factory=_ImageConfig)
    model_config = pydantic.ConfigDict(extra="forbid")


def _main() -> None:
    parser = argparse.ArgumentParser(description="アーカイブを gv 向けに前処理する")
    parser.add_argument("-c", "--config", type=pathlib.Path, help="YAML 設定ファイル")
    parser.add_argument("-b", "--backup-dir", type=pathlib.Path, help="バックアップ先 (既定: 各 TARGET の親/bk)")
    parser.add_argument("--no-trash", action="store_true", help="バックアップをゴミ箱送りしない")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("targets", nargs="+", type=pathlib.Path)
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    targets = [t.resolve() for t in args.targets]
    config_path = _resolve_config_path(args.config, targets)
    config = _load_config(config_path)
    compiled = _compile_rules(config, config_path.parent if config_path else pathlib.Path.cwd())

    _preflight_check(targets, backup_dir=args.backup_dir)

    failed_targets: list[tuple[pathlib.Path, str]] = []
    failed_entries: list[tuple[pathlib.Path, str, str]] = []
    for target in targets:
        try:
            entry_failures = _process_target(
                target,
                config=config,
                compiled=compiled,
                backup_dir_override=args.backup_dir,
                no_trash=args.no_trash,
                dry_run=args.dry_run,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("%s: 処理失敗", target)
            failed_targets.append((target, str(e)))
            continue
        for entry_path, error in entry_failures:
            failed_entries.append((target, entry_path, error))

    if failed_targets:
        logger.warning("失敗した TARGET (%d件):", len(failed_targets))
        for path, err in failed_targets:
            logger.warning("  %s: %s", path, err)
    if failed_entries:
        logger.warning("失敗したエントリ (%d件):", len(failed_entries))
        for tp, ep, err in failed_entries:
            logger.warning("  %s :: %s: %s", tp, ep, err)
    if failed_targets or failed_entries:
        sys.exit(1)


@typing.final
class _CompiledRules:
    """前処理で使う事前コンパイル済みの正規表現・ルール群。"""

    def __init__(
        self,
        ignore_file_patterns: list[str],
        ignore_dir_patterns: list[re.Pattern[str]],
        rename_rules: list[rename.RenameRule],
    ) -> None:
        self.ignore_file_patterns = ignore_file_patterns
        self.ignore_dir_patterns = ignore_dir_patterns
        self.rename_rules = rename_rules

    def should_ignore_entry(self, entry_path: str) -> bool:
        """アーカイブ内エントリパスが無視対象かを判定する (展開前フィルタ)。"""
        parts = pathlib.PurePosixPath(entry_path).parts
        # ディレクトリ名が 1 つでもマッチすれば無視
        for part in parts[:-1]:
            if any(p.search(part) for p in self.ignore_dir_patterns):
                return True
        # 終端がディレクトリそのものならディレクトリ名でもチェック
        basename = parts[-1] if parts else ""
        if entry_path.endswith("/") and any(p.search(basename) for p in self.ignore_dir_patterns):
            return True
        return bool(
            not entry_path.endswith("/") and any(fnmatch.fnmatch(basename, pattern) for pattern in self.ignore_file_patterns)
        )

    def should_ignore_path(self, path: pathlib.Path, *, root: pathlib.Path) -> bool:
        """ローカルファイルシステム上のパスが無視対象かを判定する (ディレクトリコピー時)。"""
        rel = path.relative_to(root)
        if path.is_file():
            for part in rel.parts[:-1]:
                if any(p.search(part) for p in self.ignore_dir_patterns):
                    return True
            if any(fnmatch.fnmatch(rel.name, pattern) for pattern in self.ignore_file_patterns):
                return True
        elif path.is_dir():
            if any(p.search(rel.name) for p in self.ignore_dir_patterns):
                return True
        return False


def _resolve_config_path(explicit: pathlib.Path | None, targets: list[pathlib.Path]) -> pathlib.Path | None:
    """設定ファイルパスを解決する。明示指定 → TARGET 隣接 → カレントの順。"""
    if explicit is not None:
        return explicit.resolve()
    for target in targets:
        candidate = target.parent / "repack-archive.yaml"
        if candidate.is_file():
            return candidate.resolve()
    cwd_candidate = pathlib.Path.cwd() / "repack-archive.yaml"
    if cwd_candidate.is_file():
        return cwd_candidate.resolve()
    return None


def _load_config(path: pathlib.Path | None) -> RepackConfig:
    """YAML 設定ファイルをロードする。未指定なら既定値のみ。"""
    if path is None:
        logger.info("設定ファイル未指定。既定値で実行する")
        return RepackConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return RepackConfig.model_validate(raw)


def _compile_rules(config: RepackConfig, base_dir: pathlib.Path) -> _CompiledRules:
    """設定値を高速判定用の構造に変換する。"""
    ignore_dir_patterns = [re.compile(p, re.IGNORECASE) for p in config.ignore_dirs]
    rename_rules: list[rename.RenameRule] = []
    for entry in config.rename_rules:
        if isinstance(entry, _InlineRenameRule):
            rename_rules.append(
                rename.RenameRule(
                    pattern=re.compile(entry.pattern),
                    replacement=entry.replacement,
                    target=entry.target,
                )
            )
        else:
            pf_path = (base_dir / entry.pattern_file).resolve()
            rename_rules.extend(rename.load_pattern_file(pf_path, ignore_case=True))
    return _CompiledRules(
        ignore_file_patterns=list(config.ignore_files),
        ignore_dir_patterns=ignore_dir_patterns,
        rename_rules=rename_rules,
    )


def _preflight_check(targets: list[pathlib.Path], *, backup_dir: pathlib.Path | None) -> None:
    """事前衝突チェック。1 件でも問題があれば副作用なしで終了する。"""
    seen_outputs: dict[pathlib.Path, pathlib.Path] = {}
    seen_stems: dict[pathlib.Path, pathlib.Path] = {}
    for target in targets:
        if not target.exists():
            raise FileNotFoundError(f"TARGET が存在しません: {target}")
        stem = _output_stem(target)
        output_path = target.parent / f"{stem}.zip"
        prior_out = seen_outputs.get(output_path)
        if prior_out is not None and prior_out != target:
            raise ValueError(f"出力 ZIP が衝突します: {prior_out} と {target} は同じ {output_path} を生成しようとしています")
        seen_outputs[output_path] = target

        stem_path = target.parent / stem
        prior_stem = seen_stems.get(stem_path)
        if prior_stem is not None and prior_stem != target:
            raise ValueError(f"作業ディレクトリ用の stem が衝突します: {prior_stem} と {target}")
        seen_stems[stem_path] = target

        # バックアップ先の既存チェック
        bk_root = backup_dir if backup_dir is not None else target.parent / _BACKUP_DIR_NAME
        bk_entry = bk_root / target.name
        if bk_entry.exists():
            raise FileExistsError(f"バックアップ先が既に存在します: {bk_entry}")


def _output_stem(target: pathlib.Path) -> str:
    """TARGET の出力 ZIP 名 (拡張子なし) を返す。複合拡張子 (.tar.gz 等) も除去する。"""
    name = target.name
    lowered = name.lower()
    for suffix in _ARCHIVE_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return target.stem if target.is_file() else name


def _process_target(
    target: pathlib.Path,
    *,
    config: RepackConfig,
    compiled: _CompiledRules,
    backup_dir_override: pathlib.Path | None,
    no_trash: bool,
    dry_run: bool,
) -> list[tuple[str, str]]:
    """1 つの TARGET を処理する。失敗時は作業ディレクトリを削除し原本を戻す。

    アーカイブ内の個別エントリ失敗は (エントリパス, エラー文字列) のリストとして返す。
    空でない場合、部分的に欠落した ZIP が生成された状態のためバックアップは保持する
    (``send2trash`` を呼ばない)。
    """
    logger.info("== %s ==", target)
    parent = target.parent
    stem = _output_stem(target)
    is_archive = target.is_file()

    bk_root = backup_dir_override if backup_dir_override is not None else parent / _BACKUP_DIR_NAME
    bk_entry = bk_root / target.name
    if not dry_run:
        bk_root.mkdir(parents=True, exist_ok=True)

    # 1. バックアップ作成
    if dry_run:
        logger.info("[dry-run] backup: %s -> %s", target, bk_entry)
    else:
        if is_archive:
            shutil.move(str(target), str(bk_entry))
        else:
            shutil.copytree(target, bk_entry)

    # 2. 作業ディレクトリ作成
    if dry_run:
        work_dir = parent / f"{stem}.__dry_run__"
        logger.info("[dry-run] mkworkdir: %s", work_dir)
    else:
        work_dir = pathlib.Path(tempfile.mkdtemp(prefix=_WORKDIR_PREFIX, dir=parent))

    source_for_extract = bk_entry  # アーカイブ・ディレクトリとも退避先から読み出す
    entry_failures: list[tuple[str, str]] = []
    try:
        # 3. 選択的展開 / コピー
        if is_archive:
            if dry_run:
                logger.info("[dry-run] extract %s -> %s", source_for_extract, work_dir)
            else:
                entry_failures = _extract_archive(source_for_extract, work_dir, compiled)
        else:
            if dry_run:
                logger.info("[dry-run] copy filtered %s -> %s", source_for_extract, work_dir)
            else:
                entry_failures = _copy_filtered(source_for_extract, work_dir, compiled)

        # 4. リネーム適用
        if compiled.rename_rules and not dry_run:
            rename.rename_tree(
                work_dir,
                compiled.rename_rules,
                recursive=True,
                enable_mkdir=False,
                overwrite=False,
                dry_run=False,
            )

        # 5. 画像変換
        if not dry_run:
            imageconverter.convert_directory(
                work_dir,
                output_type=config.image.output_type,
                max_width=config.image.max_width,
                max_height=config.image.max_height,
                jpeg_quality=config.image.jpeg_quality,
                repack_png=config.image.repack_png,
                remove_failed=True,
            )

        # 6. 平坦化
        if not dry_run:
            _flatten_single_root(work_dir)

        # 7. 無圧縮 ZIP 作成
        zip_path = parent / f"{stem}.zip"
        tmp_zip = parent / f"{stem}.zip.tmp"
        if dry_run:
            logger.info("[dry-run] write zip: %s", zip_path)
        else:
            _write_uncompressed_zip(work_dir, tmp_zip)
            os.replace(tmp_zip, zip_path)
            logger.info("created: %s", zip_path)
    except Exception:
        # ロールバック: 作業ディレクトリ削除 + 原本復元
        if not dry_run:
            shutil.rmtree(work_dir, ignore_errors=True)
            if is_archive and bk_entry.exists() and not target.exists():
                shutil.move(str(bk_entry), str(target))
        raise

    # 8. 作業ディレクトリ削除
    if not dry_run:
        shutil.rmtree(work_dir, ignore_errors=True)

    # 9. バックアップのゴミ箱送り (エントリ失敗があれば欠落 ZIP となるため原本を保持する)
    if not dry_run and not no_trash:
        if entry_failures:
            logger.warning(
                "エントリ失敗が %d 件あったためバックアップを保持する: %s",
                len(entry_failures),
                bk_entry,
            )
        else:
            try:
                send2trash.send2trash(str(bk_entry))
                logger.info("trash: %s", bk_entry)
            except OSError as e:
                logger.warning("ゴミ箱送り失敗: %s (%s)", bk_entry, e)

    # 10. bk/ が空なら削除
    if not dry_run and not no_trash and bk_root.exists():
        try:
            if not any(bk_root.iterdir()):
                bk_root.rmdir()
        except OSError:
            pass

    return entry_failures


@functools.cache
def _load_libarchive() -> typing.Any:
    """libarchive-c を遅延 import する (Windows での DLL 解決を含む)。

    ``import libarchive`` 自体がグローバル副作用を伴い、Windows では DLL 解決の
    過程で例外を送出する。そのため ``repack-archive --help`` や既存テストの
    ``from pytools import repack_archive`` が失敗しないよう、実際にアーカイブ展開が
    必要になる瞬間までロードを遅延させる。

    Windows では MSYS2 由来の ``libarchive-13.dll`` が
    ``~/.local/lib/libarchive/`` 配下に配置されている前提で、``LIBARCHIVE``
    環境変数と DLL 探索ディレクトリを補ってから import する。
    libarchive-c 側の ``ffi.py`` は ``LIBARCHIVE`` 環境変数を最優先で参照する。
    加えて ``libarchive-13.dll`` は複数の依存 DLL (bzip2・libxml2 等) を持つため、
    同じディレクトリを ``os.add_dll_directory`` で Windows の DLL loader に渡す。

    import が失敗した場合は復旧手順を含む RuntimeError を送出する。
    libarchive-c の未ガードな ``LoadLibrary(None)`` が ``TypeError`` を送出するケースも
    捕捉する必要があるため、ここでは ``Exception`` を広めに捕捉する。
    """
    if sys.platform == "win32":
        libarchive_dir = pathlib.Path.home() / ".local" / "lib" / "libarchive"
        libarchive_dll = libarchive_dir / "libarchive-13.dll"
        if libarchive_dll.exists():
            os.environ.setdefault("LIBARCHIVE", str(libarchive_dll))
            os.add_dll_directory(str(libarchive_dir))
    try:
        import libarchive  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "libarchive の DLL を読み込めませんでした。"
            "Windows では update-dotfiles を再実行すると "
            r"%USERPROFILE%\.local\lib\libarchive\ に libarchive-13.dll を "
            "自動ダウンロードする。再実行後は新しい端末を開き直してから "
            "repack-archive をもう一度実行すること。"
        ) from e
    return libarchive


def _extract_archive(archive: pathlib.Path, dest: pathlib.Path, compiled: _CompiledRules) -> list[tuple[str, str]]:
    """libarchive-c でストリーミング展開する。無視対象エントリはディスクに書かない。

    個別エントリの ``OSError`` は捕捉してスキップし、(エントリパス, エラー文字列) の
    リストとして戻り値で返す。target 単位のロールバックは呼び出し元が担うため、
    アーカイブ全体での致命的エラー (libarchive のフォーマットエラー等) は再送出する。
    """
    libarchive = _load_libarchive()
    entry_count, pathname_map = _prepare_archive(archive)
    dest.mkdir(parents=True, exist_ok=True)
    failures: list[tuple[str, str]] = []
    with (
        tqdm.tqdm(total=entry_count, desc=f"extract {archive.name}", ascii=True, ncols=100, unit="f") as pbar,
        libarchive.file_reader(str(archive)) as reader,
    ):
        for entry in reader:
            pbar.update(1)
            entry_path = _resolve_entry_path(entry, pathname_map, libarchive)
            if not entry_path:
                continue
            if compiled.should_ignore_entry(entry_path):
                continue
            try:
                out_path = dest / entry_path
                # 親ディレクトリを作成
                if entry.isdir:
                    out_path.mkdir(parents=True, exist_ok=True)
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with out_path.open("wb") as fp:
                    for block in entry.get_blocks():
                        fp.write(block)
            except OSError as e:
                failures.append((entry_path, str(e)))
                logger.warning("%s: エントリ展開失敗: %s (%s)", archive.name, entry_path, e)
    return failures


def _prepare_archive(archive: pathlib.Path) -> tuple[int, dict[bytes, str] | None]:
    """アーカイブ展開前にエントリ数をカウントし、ZIP ならパスマッピングを併せて構築する。

    戻り値のマッピングは libarchive の生バイトパス (``ffi.entry_pathname``) を
    キーに、``zipfile`` 経由で復元したパスを値に持つ。ZIP 以外 (RAR / 7z / tar 等)
    では ``None`` を返し、呼び出し元は libarchive の ``entry.pathname`` を使う。
    """
    libarchive = _load_libarchive()
    pathname_map = _build_zip_pathname_map(archive)
    count = 0
    with libarchive.file_reader(str(archive)) as reader:
        for _ in reader:
            count += 1
    return count, pathname_map


def _build_zip_pathname_map(archive: pathlib.Path) -> dict[bytes, str] | None:
    """ZIP の中央ディレクトリからエントリごとにパスを復元し、生バイト→復号パスのマッピングを返す。

    General purpose bit 11 (Unicode flag) をエントリ単位で参照する。bit 11 が立つ
    エントリは UTF-8 のまま採用し、立たないエントリは一括して CP932 strict デコードを
    試す。全て成功かつ制御文字が混入していなければ CP932 を採用し、満たさない場合は
    ``zipfile`` 既定の CP437 デコード結果をそのまま使う (ZIP 仕様の既定挙動)。

    ZIP でないなら ``None`` を返す。
    """
    if not zipfile.is_zipfile(archive):
        return None

    mapping: dict[bytes, str] = {}
    # bit 11 未設定エントリは (生バイト, CP437 デコード結果) を貯めて後段で一括判定する。
    # 単一エントリ判定では ASCII 名が常に成功扱いになり全体判定を歪めるため、
    # アーカイブ全体の非 UTF-8 エントリをまとめて判定する必要がある。
    non_utf8_entries: list[tuple[bytes, str]] = []
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError):
        # 中央ディレクトリが破損している ZIP は libarchive 側の復旧挙動に委ねる。
        return None
    for info in infos:
        if info.flag_bits & 0x800:
            mapping[info.filename.encode("utf-8")] = info.filename
        else:
            non_utf8_entries.append((info.filename.encode("cp437"), info.filename))

    cp932_decoded: list[str] = []
    cp932_ok = True
    for raw, _fallback in non_utf8_entries:
        try:
            decoded = raw.decode("cp932")
        except UnicodeDecodeError:
            cp932_ok = False
            break
        if _has_control_chars(decoded):
            cp932_ok = False
            break
        cp932_decoded.append(decoded)

    if cp932_ok:
        for (raw, _fallback), decoded in zip(non_utf8_entries, cp932_decoded, strict=True):
            mapping[raw] = decoded
    else:
        for raw, fallback in non_utf8_entries:
            mapping[raw] = fallback
    return mapping


def _has_control_chars(value: str) -> bool:
    """文字列に制御文字 (U+0000–U+001F および U+007F–U+009F、ただしタブ・改行を除く) が含まれるかを判定する。

    libarchive が ZIP のファイル名をワイド文字として誤解釈した場合、C1 制御文字 (U+0080–U+009F)
    が混入した str が返る。CP932 デコードに成功しても制御文字が残るパターンは、実ファイル名ではなく
    バイト列の偶然の一致である可能性が高いため、CP932 判定から除外する判断材料として使う。
    """
    for ch in value:
        code = ord(ch)
        if code in (0x09, 0x0A, 0x0D):
            continue
        if code <= 0x1F or 0x7F <= code <= 0x9F:
            return True
    return False


def _resolve_entry_path(entry: typing.Any, pathname_map: dict[bytes, str] | None, libarchive: typing.Any) -> str:
    """Libarchive エントリから使用すべきパス文字列を返す。

    ZIP 用マッピングがあれば生バイトをキーに引き、見つからない場合のみ libarchive の
    ``entry.pathname`` にフォールバックする。libarchive の ``entry.pathname`` は
    bit 11 未設定 ZIP の非 ASCII 名を誤デコードして壊れた str を返すことがあり、
    Windows の ``OSError: [WinError 123]`` の原因になるため可能な限り回避する。
    """
    if pathname_map is not None:
        raw = libarchive.ffi.entry_pathname(entry._entry_p)  # pylint: disable=protected-access
        if raw is not None:
            resolved = pathname_map.get(raw)
            if resolved is not None:
                return resolved
    return entry.pathname or ""


def _copy_filtered(src: pathlib.Path, dest: pathlib.Path, compiled: _CompiledRules) -> list[tuple[str, str]]:
    """ディレクトリを再帰コピーしつつ、無視対象を省く。

    個別ファイルの ``OSError`` は捕捉して (相対パス, エラー文字列) のリストとして返す。
    """
    dest.mkdir(parents=True, exist_ok=True)
    failures: list[tuple[str, str]] = []
    for item in src.rglob("*"):
        if compiled.should_ignore_path(item, root=src):
            continue
        rel = item.relative_to(src)
        try:
            target = dest / rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
        except OSError as e:
            failures.append((rel.as_posix(), str(e)))
            logger.warning("%s: エントリコピー失敗: %s (%s)", src.name, rel, e)
    return failures


def _flatten_single_root(work_dir: pathlib.Path) -> None:
    """作業ディレクトリ配下の冗長な階層を除去する。

    無視パターン適用後に残った空ディレクトリを先に畳んだうえで、直下が単一
    ディレクトリである状態が続く限り階層を除去しきる。これにより
    ``Series/Vol01/001.txt`` と空の ``Series/Vol02/`` が共存するような
    フィルタ後構造でも、最終 ZIP に余計な階層を残さない。
    """
    _prune_empty_dirs(work_dir)
    while True:
        entries = list(work_dir.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            return
        inner = entries[0]
        # ``inner`` 直下に ``inner`` と同名の子 (``foo/foo``) があると
        # ``shutil.move`` が移動先ディレクトリ内へ挿入する挙動になり
        # ``rmdir`` 前に空にできない。退避名へリネームして衝突を避ける。
        staging = _reserve_flatten_staging(inner)
        inner.rename(staging)
        for child in list(staging.iterdir()):
            shutil.move(str(child), str(work_dir / child.name))
        staging.rmdir()


def _prune_empty_dirs(root: pathlib.Path) -> None:
    """Root 配下の空ディレクトリを深い順に除去する (root 自体は残す)。"""
    dirs = [p for p in root.rglob("*") if p.is_dir()]
    dirs.sort(key=lambda p: len(p.parts), reverse=True)
    for path in dirs:
        if not any(path.iterdir()):
            path.rmdir()


def _reserve_flatten_staging(inner: pathlib.Path) -> pathlib.Path:
    """``inner`` の兄弟として衝突しない退避名を返す。"""
    parent = inner.parent
    base = f"{inner.name}.__flatten_tmp__"
    candidate = parent / base
    index = 0
    while candidate.exists():
        index += 1
        candidate = parent / f"{base}{index}"
    return candidate


def _write_uncompressed_zip(work_dir: pathlib.Path, zip_path: pathlib.Path) -> None:
    """作業ディレクトリ配下を無圧縮 ZIP へ書き出す。"""
    files = sorted(p for p in work_dir.rglob("*") if p.is_file())
    with (
        tqdm.tqdm(total=len(files), desc=f"zip {zip_path.name}", ascii=True, ncols=100, unit="f") as pbar,
        zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf,
    ):
        for file in files:
            rel = file.relative_to(work_dir).as_posix()
            zf.write(file, arcname=rel)
            pbar.update(1)


if __name__ == "__main__":
    _main()
